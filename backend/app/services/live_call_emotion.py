"""Conservative conversational tone selection for ephemeral Live Call turns."""

from enum import Enum
import re
import unicodedata

from app.services.ai.provider import AIMessage


class LiveCallTone(str, Enum):
    NEUTRAL = "neutral"
    WARM = "warm"
    HAPPY = "happy"
    EXCITED = "excited"
    GENTLE = "gentle"
    COMFORTING = "comforting"
    NOSTALGIC = "nostalgic"
    SERIOUS = "serious"


TONE_GENERATION_GUIDANCE = {
    LiveCallTone.NEUTRAL: "Keep the response direct, relaxed, and conversational.",
    LiveCallTone.WARM: "Respond with restrained warmth and familiar conversational phrasing.",
    LiveCallTone.HAPPY: "Share the user's positive energy warmly without exaggeration.",
    LiveCallTone.EXCITED: "Respond with lively but controlled energy; avoid theatrical enthusiasm.",
    LiveCallTone.GENTLE: "Use calm, gentle phrasing and invite the user to continue if appropriate.",
    LiveCallTone.COMFORTING: "Be quietly supportive and present without diagnosing feelings or using stock sympathy.",
    LiveCallTone.NOSTALGIC: "Use a reflective present-day tone, but never invent a past feeling, event, or memory.",
    LiveCallTone.SERIOUS: "Use calm, measured, attentive language without sounding formal or alarmist.",
}


class LiveCallToneResolver:
    """Interpret response style, never a psychological state or diagnosis."""

    _SIGNALS = (
        (LiveCallTone.COMFORTING, (
            "terrible day", "really bad day", "awful day", "manager shouted",
            "i miss you", "feel lonely", "it hurts", "मला तुझी आठवण येते",
            "खूप वाईट दिवस", "मुझे तुम्हारी याद आती है", "बहुत बुरा दिन",
            "mala tujhi athavan yete", "bahut bura din",
        )),
        (LiveCallTone.EXCITED, (
            "i got the job", "i got promoted", "i passed", "can't wait",
            "cannot wait", "so exciting", "मला नोकरी मिळाली", "मी पास झाले",
            "मी पास झालो", "मुझे नौकरी मिल गई", "मैं पास हो गया", "मैं पास हो गई",
            "mala job milali", "mujhe job mil gayi",
        )),
        (LiveCallTone.HAPPY, (
            "so happy", "wonderful news", "good news", "खूप आनंद", "आनंदाची बातमी",
            "बहुत खुश", "खुशी की खबर", "khup anand", "bahut khush",
        )),
        (LiveCallTone.NOSTALGIC, (
            "do you remember", "remember our", "those days", "old house", "old memories",
            "तुला आठवतं", "आपलं जुनं", "ते दिवस", "तुम्हें याद है", "पुराने दिन",
            "tula athavta", "tumhe yaad hai",
        )),
        (LiveCallTone.SERIOUS, (
            "this is important", "need to decide", "serious matter", "हे महत्त्वाचं आहे",
            "निर्णय घ्यायचा", "यह महत्वपूर्ण है", "फैसला करना", "important aahe",
        )),
        (LiveCallTone.WARM, (
            "good to talk", "lovely to hear", "dear", "तुझ्याशी बोलून", "तुमसे बात करके",
        )),
    )
    _CONTINUING = frozenset({
        LiveCallTone.GENTLE, LiveCallTone.COMFORTING,
        LiveCallTone.NOSTALGIC, LiveCallTone.SERIOUS,
    })

    def resolve(self, text: str, history: tuple[AIMessage, ...]) -> LiveCallTone:
        normalized = self._normalize(text)
        for tone, signals in self._SIGNALS:
            if any(self._contains(normalized, signal) for signal in signals):
                return tone
        # A single neutral follow-up may retain a nearby reflective/supportive tone.
        previous_users = [item.content for item in history if item.role == "user"]
        if previous_users:
            previous = self.resolve(previous_users[-1], ())
            if previous in self._CONTINUING:
                return previous
        return LiveCallTone.NEUTRAL

    @staticmethod
    def _normalize(text: str) -> str:
        return " ".join(unicodedata.normalize("NFC", text).casefold().split())

    @staticmethod
    def _contains(text: str, signal: str) -> bool:
        normalized_signal = unicodedata.normalize("NFC", signal).casefold()
        return bool(re.search(rf"(?<!\w){re.escape(normalized_signal)}(?!\w)", text))
