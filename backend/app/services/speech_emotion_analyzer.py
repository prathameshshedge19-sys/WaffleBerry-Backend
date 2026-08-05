"""Deterministic multilingual emotion signals for speech delivery only."""

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata

from app.services.speech_language_analyzer import SpeechLanguageMode


class SpeechEmotion(str, Enum):
    NEUTRAL = "neutral"
    WARM = "warm"
    REASSURING = "reassuring"
    PEACEFUL = "peaceful"
    NOSTALGIC = "nostalgic"
    SAD = "sad"
    JOYFUL = "joyful"
    EXCITED = "excited"
    SERIOUS = "serious"
    ANGRY = "angry"


class EmotionConfidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class SpeechEmotionAnalysis:
    emotion: SpeechEmotion
    confidence: EmotionConfidence


class SpeechEmotionAnalyzer:
    """Classify explicit wording conservatively without another AI request."""

    _SIGNALS = {
        SpeechEmotion.ANGRY: (
            "i am angry", "i'm angry", "this is unacceptable", "how dare",
            "this is wrong", "मी रागावलो", "मी रागावले", "हे चुकीचे आहे",
            "हे योग्य नाही", "मुझे गुस्सा है", "यह गलत है", "यह अस्वीकार्य है",
        ),
        SpeechEmotion.REASSURING: (
            "i am here", "i'm here", "it will be okay", "don't worry",
            "do not worry", "you are not alone", "take your time",
            "everything will be okay", "मी तुझ्यासोबत आहे", "काळजी करू नको",
            "सगळं ठीक होईल", "तू एकटा नाहीस", "तू एकटी नाहीस",
            "मैं तुम्हारे साथ हूँ", "चिंता मत करो", "सब ठीक हो जाएगा",
            "तुम अकेले नहीं हो", "तुम अकेली नहीं हो",
        ),
        SpeechEmotion.SAD: (
            "i miss you", "i'm sorry", "i am sorry", "it hurts",
            "feeling lonely", "deep loss", "मला तुझी आठवण येते",
            "मला वाईट वाटतं", "खूप दुःख झालं", "एकटं वाटतं",
            "मुझे तुम्हारी याद आती है", "मुझे दुख है", "बहुत दुख हुआ",
            "अकेला महसूस", "अकेली महसूस",
        ),
        SpeechEmotion.EXCITED: (
            "can't wait", "cannot wait", "so exciting", "how exciting",
            "absolutely incredible", "खूप उत्सुक आहे", "वाट पाहवत नाही",
            "किती रोमांचक", "बहुत उत्साहित हूँ", "इंतज़ार नहीं हो रहा",
            "कितना रोमांचक",
        ),
        SpeechEmotion.JOYFUL: (
            "so happy", "proud of you", "congratulations", "wonderful news",
            "beautiful news", "मला तुझा अभिमान आहे", "खूप आनंद झाला",
            "आनंदाची बातमी", "अभिनंदन", "मुझे तुम पर गर्व है",
            "बहुत खुशी हुई", "खुशी की खबर", "बधाई",
        ),
        SpeechEmotion.NOSTALGIC: (
            "i remember", "those days", "back then", "old memories",
            "we used to", "मला आठवतं", "मला अजूनही आठवतं", "ते दिवस",
            "जुन्या आठवणी", "आम्ही नेहमी", "मुझे याद है", "वे दिन",
            "पुरानी यादें", "हम अक्सर", "mala ajunhi athavta",
        ),
        SpeechEmotion.PEACEFUL: (
            "breathe slowly", "breathe calmly", "peaceful and quiet",
            "rest quietly", "शांतपणे श्वास", "निवांत बस", "शांत आणि निवांत",
            "धीरे से साँस", "शांति से बैठो", "शांत और सुकून",
        ),
        SpeechEmotion.WARM: (
            "dear friend", "lovely to see you", "good to see you",
            "welcome back", "तुला पाहून छान वाटलं", "मनापासून स्वागत",
            "तुम्हें देखकर अच्छा लगा", "दिल से स्वागत",
        ),
        SpeechEmotion.SERIOUS: (
            "this is important", "we need to decide", "must take this seriously",
            "हे महत्त्वाचे आहे", "निर्णय घ्यावा लागेल", "गंभीरपणे विचार",
            "यह महत्वपूर्ण है", "निर्णय लेना होगा", "गंभीरता से सोच",
        ),
    }

    _SUPPORTING_WORDS = {
        SpeechEmotion.ANGRY: {"angry", "unacceptable", "wrong", "enough", "राग", "चुकीचे", "गलत", "गुस्सा"},
        SpeechEmotion.SAD: {"sad", "hurt", "loss", "lonely", "दुःख", "वाईट", "एकटं", "दुख", "अकेला"},
        SpeechEmotion.REASSURING: {"okay", "safe", "together", "here", "ठीक", "सोबत", "साथ"},
        SpeechEmotion.JOYFUL: {"happy", "wonderful", "proud", "beautiful", "आनंद", "अभिमान", "खुशी", "गर्व"},
        SpeechEmotion.EXCITED: {"amazing", "incredible", "excited", "उत्सुक", "रोमांचक", "उत्साहित"},
        SpeechEmotion.NOSTALGIC: {"remember", "memories", "आठवण", "आठवतं", "याद", "यादें", "athavta"},
        SpeechEmotion.PEACEFUL: {"breathe", "peaceful", "calmly", "quiet", "शांत", "निवांत", "सुकून"},
        SpeechEmotion.WARM: {"welcome", "dear", "lovely", "स्वागत", "प्रिय"},
        SpeechEmotion.SERIOUS: {"important", "serious", "decision", "महत्त्वाचे", "गंभीर", "महत्वपूर्ण", "निर्णय"},
    }

    _PRECEDENCE = (
        SpeechEmotion.ANGRY,
        SpeechEmotion.REASSURING,
        SpeechEmotion.SAD,
        SpeechEmotion.EXCITED,
        SpeechEmotion.JOYFUL,
        SpeechEmotion.NOSTALGIC,
        SpeechEmotion.PEACEFUL,
        SpeechEmotion.WARM,
        SpeechEmotion.SERIOUS,
    )

    def analyze(
        self,
        text: str,
        *,
        language_mode: SpeechLanguageMode,
    ) -> SpeechEmotionAnalysis:
        del language_mode  # Signals are multilingual; mode remains audit context.
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Speech emotion input must not be blank.")
        normalized = unicodedata.normalize("NFC", text).casefold()
        if not any(character.isalnum() for character in normalized):
            return SpeechEmotionAnalysis(SpeechEmotion.NEUTRAL, EmotionConfidence.LOW)
        scores = {}
        phrase_hits = {}
        for emotion, phrases in self._SIGNALS.items():
            hits = sum(self._contains(normalized, phrase) for phrase in phrases)
            words = sum(
                self._contains(normalized, word)
                for word in self._SUPPORTING_WORDS[emotion]
            )
            phrase_hits[emotion] = hits
            scores[emotion] = hits * 2 + words

        # Comforting language governs mixed sadness-plus-support responses.
        if (
            not phrase_hits[SpeechEmotion.ANGRY]
            and phrase_hits[SpeechEmotion.REASSURING]
            and scores[SpeechEmotion.REASSURING] >= 2
        ):
            return SpeechEmotionAnalysis(
                SpeechEmotion.REASSURING,
                EmotionConfidence.HIGH if scores[SpeechEmotion.REASSURING] >= 4 else EmotionConfidence.MEDIUM,
            )
        for emotion in self._PRECEDENCE:
            score = scores[emotion]
            if phrase_hits[emotion] >= 1 or score >= 2:
                confidence = EmotionConfidence.HIGH if score >= 4 else EmotionConfidence.MEDIUM
                return SpeechEmotionAnalysis(emotion, confidence)
        return SpeechEmotionAnalysis(SpeechEmotion.NEUTRAL, EmotionConfidence.LOW)

    @staticmethod
    def _contains(text: str, signal: str) -> bool:
        return bool(re.search(rf"(?<!\w){re.escape(signal)}(?!\w)", text))
