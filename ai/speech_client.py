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

            whisper_prompt = (
                "Dr. Sara Malik, Dr. Ahmed Khan, SmileCare, appointment, checkup, consultation, cleaning, scaling, "
                "whitening, root canal, daant, dard, kal, aaj, parso, subah, dopahar, sham, baje, بجے, "
                "ڈاکٹر سارا, ڈاکٹر احمد, اپائنٹمنٹ, مشورہ, چیک اپ, دانت, درد, کل, آج"
            )

            transcription = client.audio.transcriptions.create(
                file=file_payload,
                model=self.model,
                prompt=whisper_prompt,
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


class GeminiSTTAdapter(BaseSTTAdapter):
    """Speech-to-Text adapter using Gemini API audio understanding."""
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or Config.GEMINI_API_KEY
        self.model = model or getattr(Config, "GEMINI_MODEL", "gemini-3.6-flash")

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        if not audio_bytes or len(audio_bytes) == 0:
            raise ValueError("Empty audio payload received")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured for GeminiSTTAdapter")

        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=self.api_key)

            response = client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    "Transcribe this audio verbatim. If the speaker speaks Urdu script, Roman Urdu, English, or a mix, transcribe exactly what is spoken in that same format without reordering, translating, or adding quotes."
                ]
            )
            if response.text:
                return response.text.strip().strip('"').strip("'")
            raise RuntimeError("No transcription text returned by Gemini")
        except Exception as e:
            logger.error(f"Gemini STT Error: {str(e)}")
            raise RuntimeError(f"Gemini STT failed: {str(e)}")


class STTClient:
    """Factory client for Speech-to-Text provider selection."""
    def __init__(self, stt_provider: Optional[str] = None, preset_transcript: Optional[str] = None):
        self.stt_provider = (stt_provider or Config.STT_PROVIDER or "mock").lower()
        if self.stt_provider == "gemini":
            self.adapter = GeminiSTTAdapter()
        elif self.stt_provider == "groq":
            self.adapter = GroqWhisperAdapter()
        else:
            self.adapter = MockSTTAdapter(preset_transcript=preset_transcript)

    def transcribe(self, audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
        try:
            return self.adapter.transcribe(audio_bytes, mime_type=mime_type)
        except Exception as primary_err:
            logger.warning(f"[STTClient Warning]: Primary STT provider '{self.stt_provider}' failed: {primary_err}")
            # Resilient fallback: If Groq failed, try Gemini
            if self.stt_provider == "groq" and Config.GEMINI_API_KEY:
                try:
                    logger.info("Attempting STT fallback using GeminiSTTAdapter...")
                    return GeminiSTTAdapter().transcribe(audio_bytes, mime_type=mime_type)
                except Exception as fallback_err:
                    logger.warning(f"Gemini STT fallback also failed: {fallback_err}")
            # If Gemini failed, try Groq
            elif self.stt_provider == "gemini" and Config.GROQ_API_KEY:
                try:
                    logger.info("Attempting STT fallback using GroqWhisperAdapter...")
                    return GroqWhisperAdapter().transcribe(audio_bytes, mime_type=mime_type)
                except Exception as fallback_err:
                    logger.warning(f"Groq Whisper fallback also failed: {fallback_err}")
            raise primary_err


class BaseTTSAdapter(ABC):
    @abstractmethod
    def synthesize(self, text: str) -> bytes:
        """Convert text into raw audio bytes (a playable audio file)."""
        pass


class MockTTSAdapter(BaseTTSAdapter):
    """Deterministic Text-to-Speech adapter for local development and
    testing without an API key. Returns a tiny valid (silent) WAV file so
    the response shape and frontend playback path can be exercised without
    ever making a real network call."""

    # A minimal valid WAV header for a near-empty silent clip (44 bytes,
    # 8kHz mono, no samples) — enough for any audio player to accept it
    # as a real file rather than producing a decode error.
    _SILENT_WAV = (
        b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
        + b"fmt " + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")      # PCM
        + (1).to_bytes(2, "little")      # mono
        + (8000).to_bytes(4, "little")   # sample rate
        + (8000).to_bytes(4, "little")   # byte rate
        + (1).to_bytes(2, "little")      # block align
        + (8).to_bytes(2, "little")      # bits per sample
        + b"data" + (0).to_bytes(4, "little")
    )

    def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            raise ValueError("Text is required for speech synthesis")
        return self._SILENT_WAV


class GroqTTSAdapter(BaseTTSAdapter):
    """Text-to-Speech adapter using Groq's PlayAI TTS endpoint."""
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, voice: Optional[str] = None):
        self.api_key = api_key or Config.GROQ_API_KEY
        self.model = model or getattr(Config, "GROQ_TTS_MODEL", "playai-tts")
        self.voice = voice or getattr(Config, "GROQ_TTS_VOICE", "Fritz-PlayAI")

    def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            raise ValueError("Text is required for speech synthesis")
        if not self.api_key:
            raise ValueError("GROQ_API_KEY is not configured for GroqTTSAdapter")

        clean_text = text.strip()[:2000]

        try:
            from groq import Groq
            client = Groq(api_key=self.api_key)

            response = client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=clean_text,
                response_format="wav"
            )

            if hasattr(response, "read"):
                return response.read()
            if hasattr(response, "content"):
                return response.content
            return bytes(response)

        except Exception as e:
            logger.error(f"Groq TTS Error: {str(e)}")
            raise RuntimeError(f"Groq TTS failed: {str(e)}")


class GeminiTTSAdapter(BaseTTSAdapter):
    """Text-to-Speech adapter using Gemini API audio generation endpoint."""
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or Config.GEMINI_API_KEY
        self.model = model or "gemini-2.5-flash-preview-tts"

    def synthesize(self, text: str) -> bytes:
        if not text or not text.strip():
            raise ValueError("Text is required for speech synthesis")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not configured for GeminiTTSAdapter")

        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=self.api_key)

            clean_text = text.strip()[:2000]
            response = client.models.generate_content(
                model=self.model,
                contents=clean_text,
                config=types.GenerateContentConfig(response_modalities=["AUDIO"])
            )

            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if getattr(part, "inline_data", None) and part.inline_data.data:
                        return part.inline_data.data

            raise RuntimeError("No audio data returned in Gemini TTS response")
        except Exception as e:
            logger.error(f"Gemini TTS Error: {str(e)}")
            raise RuntimeError(f"Gemini TTS failed: {str(e)}")


class TTSClient:
    """Factory client for Text-to-Speech provider selection."""
    def __init__(self, tts_provider: Optional[str] = None):
        self.tts_provider = (tts_provider or getattr(Config, "TTS_PROVIDER", "gemini") or "gemini").lower()
        if self.tts_provider == "gemini":
            self.adapter = GeminiTTSAdapter()
        elif self.tts_provider == "groq":
            self.adapter = GroqTTSAdapter()
        else:
            self.adapter = MockTTSAdapter()

    def synthesize(self, text: str) -> bytes:
        return self.adapter.synthesize(text)
