import io
import os
import logging
from abc import ABC, abstractmethod
from typing import Optional
from config.config import Config

logger = logging.getLogger(__name__)

class BaseSTTAdapter(ABC):
    @abstractmethod
    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        """Transcribe raw audio bytes into text string."""
        pass


class MockSTTAdapter(BaseSTTAdapter):
    """Deterministic Speech-to-Text adapter for local development and testing."""
    def __init__(self, preset_transcript: Optional[str] = None):
        self.preset_transcript = preset_transcript

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        if not audio_bytes or len(audio_bytes) == 0:
            raise ValueError("Empty audio payload received")
        if self.preset_transcript:
            return self.preset_transcript
        return "I want an appointment with Dr Sara tomorrow at 10:00"


class GroqWhisperAdapter(BaseSTTAdapter):
    """Speech-to-Text adapter using Groq API Whisper endpoint."""
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or Config.GROQ_API_KEY
        self.model = model or getattr(Config, "GROQ_STT_MODEL", "whisper-large-v3")

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        if not audio_bytes or len(audio_bytes) == 0:
            raise ValueError("Empty audio payload received")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY is not configured for GroqWhisperAdapter")

        try:
            from groq import Groq
            client = Groq(api_key=self.api_key)

            # Map mime type to file extension for Groq API payload tuple
            ext = "webm"
            if "wav" in mime_type:
                ext = "wav"
            elif "mp3" in mime_type or "mpeg" in mime_type:
                ext = "mp3"
            elif "ogg" in mime_type:
                ext = "ogg"
            elif "m4a" in mime_type or "mp4" in mime_type:
                ext = "m4a"

            filename = f"speech_input.{ext}"
            file_payload = (filename, audio_bytes, mime_type)

            transcription = client.audio.transcriptions.create(
                file=file_payload,
                model=self.model,
                response_format="json"
            )

            if hasattr(transcription, "text"):
                return transcription.text.strip()
            elif isinstance(transcription, dict) and "text" in transcription:
                return transcription["text"].strip()
            else:
                return str(transcription).strip()

        except Exception as e:
            logger.error(f"Groq Whisper STT Error: {str(e)}")
            raise RuntimeError(f"Groq Whisper STT failed: {str(e)}")


class STTClient:
    """Factory client for Speech-to-Text provider selection."""
    def __init__(self, stt_provider: Optional[str] = None, preset_transcript: Optional[str] = None):
        self.stt_provider = (stt_provider or Config.STT_PROVIDER or "mock").lower()
        if self.stt_provider == "groq":
            self.adapter = GroqWhisperAdapter()
        else:
            self.adapter = MockSTTAdapter(preset_transcript=preset_transcript)

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        return self.adapter.transcribe(audio_bytes, mime_type=mime_type)
