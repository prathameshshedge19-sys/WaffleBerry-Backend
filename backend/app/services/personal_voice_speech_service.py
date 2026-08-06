"""Route explicit user-selected voices without exposing providers publicly."""

from app.services.ai.provider import SpeechResult
from app.services.ai.sarvam_speech_service import SarvamSpeechService
from app.services.ai.speech_service import SpeechService
from app.services.voice_catalogue import VoiceDefinition, VoiceGender, VoiceProvider
from app.services.voice_profile_resolver import StandardVoiceProfile


class PersonalVoiceSpeechService:
    def __init__(
        self,
        *,
        sarvam_service: SarvamSpeechService,
        openai_service: SpeechService,
    ) -> None:
        self._sarvam_service = sarvam_service
        self._openai_service = openai_service

    async def synthesize(
        self,
        *,
        text: str,
        voice: VoiceDefinition,
        response_format: str | None,
    ) -> SpeechResult:
        if voice.provider == VoiceProvider.SARVAM:
            profile = (
                StandardVoiceProfile.MALE
                if voice.gender == VoiceGender.MALE
                else StandardVoiceProfile.FEMALE
            )
            return await self._sarvam_service.synthesize(
                text=text,
                standard_voice_profile=profile,
                selected_voice=voice.provider_voice,
                response_format=response_format,
                preserve_text=True,
            )
        return await self._openai_service.synthesize(
            text=text,
            voice=voice.provider_voice,
            response_format=response_format,
            preserve_text=True,
        )
