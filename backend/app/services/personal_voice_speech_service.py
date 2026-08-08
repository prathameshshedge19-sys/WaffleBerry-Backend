"""Route explicit user-selected voices without exposing providers publicly."""

from collections.abc import AsyncIterator

from app.services.ai.provider import SpeechChunk, SpeechResult
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
        conversational_tone: str | None = None,
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
                conversational_tone=conversational_tone,
            )
        return await self._openai_service.synthesize(
            text=text,
            voice=voice.provider_voice,
            response_format=response_format,
            preserve_text=True,
            conversational_tone=conversational_tone,
        )

    def supports_streaming(self, voice: VoiceDefinition) -> bool:
        return voice.provider == VoiceProvider.OPENAI and self._openai_service.supports_streaming

    async def stream(
        self, *, text: str, voice: VoiceDefinition,
        conversational_tone: str | None = None,
    ) -> AsyncIterator[SpeechChunk]:
        if not self.supports_streaming(voice):
            raise NotImplementedError
        async for chunk in self._openai_service.stream(
            text=text, voice=voice.provider_voice,
            conversational_tone=conversational_tone,
        ):
            yield chunk
