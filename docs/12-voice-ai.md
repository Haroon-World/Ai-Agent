# 12 — Voice AI

## Overview

ClinicConnect AI supports voice input on both the web chat channel and WhatsApp, and voice output (TTS) on the web chat channel. Voice processing is handled by the STTClient and TTSClient abstraction classes in ai/speech_client.py.

---

## Voice Architecture

```
Voice Input Pipeline:

User speaks
    |
    v
Web: Browser MediaRecorder API records WebM audio
WhatsApp: Patient sends voice note (OGG/OPUS from WhatsApp)
    |
    v
STTClient.transcribe(audio_bytes, mime_type)
    |
    +-- Groq Whisper Adapter (primary option)
    |   -> POST to Groq API /audio/transcriptions
    |   -> Model: whisper-large-v3
    |   -> Handles: WebM, WAV, MP3, OGG, M4A
    |
    +-- Gemini STT Adapter (alternative option)
    |   -> generate_content with audio bytes
    |   -> Instruction: Transcribe verbatim in original language
    |
    +-- Mock Adapter (development)
        -> Returns preset fixed transcript string
    |
    v
Transcribed text (English, Urdu, Roman Urdu, or mix)
    |
    v
AI Agent processes same as text input
    |
    v
AI reply (text)


Voice Output Pipeline (Web Chat Only):

AI reply text
    |
    v
POST /api/chat/synthesize { text: reply_text }
    |
    v
TTSClient.synthesize(text)
    |
    +-- Gemini TTS Adapter
    |   -> generate_content with AUDIO modality
    |   -> Model: gemini-2.5-flash-preview-tts
    |
    +-- Groq TTS Adapter
    |   -> audio.speech.create
    |   -> Model: playai-tts, Voice: Fritz-PlayAI
    |
    +-- Mock Adapter (development)
        -> Returns minimal silent WAV file bytes
    |
    v
WAV audio bytes returned as audio/wav response
    |
    v
chat.js plays audio in browser
```

---

## Provider Selection

| Setting | Options |
|---|---|
| STT_PROVIDER | groq | gemini | mock |
| TTS_PROVIDER | groq | gemini | mock |

Configure in .env:

```
STT_PROVIDER=groq
TTS_PROVIDER=gemini
GROQ_STT_MODEL=whisper-large-v3
GROQ_TTS_MODEL=playai-tts
GROQ_TTS_VOICE=Fritz-PlayAI
```

---

## STT Fallback Behavior

If the primary STT provider fails, STTClient automatically tries the other:
- If Groq fails and GEMINI_API_KEY is set: attempts Gemini
- If Gemini fails and GROQ_API_KEY is set: attempts Groq
- If both fail: raises the original exception

---

## Whisper Prompt Tuning

The GroqWhisperAdapter includes a domain-specific prompt to improve transcription accuracy for medical/clinic vocabulary:

```
whisper_prompt = Dr. Sara Malik, Dr. Ahmed Khan, SmileCare, appointment, checkup,
                 consultation, cleaning, scaling, whitening, root canal, daant, dard,
                 kal, aaj, parso, subah, dopahar, sham, baje
```

This prompt biases Whisper toward correct recognition of doctor names, clinic terms, and Urdu time/date words.

---

## Multilingual STT

Both Groq Whisper and Gemini support multilingual transcription:
- English
- Urdu (Urdu script)
- Roman Urdu (Latin-script Urdu)
- Mixed English/Urdu

The Gemini STT instruction explicitly preserves the original language without translation.

---

## Known Limitations

| Limitation | Notes |
|---|---|
| WhatsApp voice replies not implemented | WhatsApp channel receives text-only replies |
| TTS language support | Groq PlayAI Fritz voice is English-focused; Urdu TTS quality may vary |
| Audio format compatibility | WhatsApp sends OGG/OPUS; browser sends WebM; both are handled |
| STT accuracy for heavy Roman Urdu | Whisper may mishear some regional expressions |
| No audio streaming | Full audio file must be uploaded before transcription starts |
| TTS text limit | Text is truncated to 2000 characters before synthesis |

---
