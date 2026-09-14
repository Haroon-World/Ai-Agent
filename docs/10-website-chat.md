# 10 — Website Chat Channel

## Overview

The web chat channel is a browser-based customer interface rendered at /chat or /chat/<clinic_id>. It provides a WhatsApp-style messaging UI that connects directly to the AI agent backend.

---

## Entry Points

| URL | Description |
|---|---|
| /chat | Default clinic chat (uses DEFAULT_BUSINESS_ID) |
| /chat/<clinic_id> | Specific clinic chat by ID |
| /chat?clinic=N | Specific clinic chat by query param |
| /chat?business_id=N | Alternative query param |

---

## Session and Identity

- Each browser session receives a UUID visitor_id stored in the Flask session cookie
- This visitor_id is tied to the Conversation record to prevent IDOR attacks
- Anonymous visitors (not logged in) are identified by visitor_id only
- When a patient books an appointment, a Customer record is created and linked

---

## API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| /api/chat/init | POST | Initialize session; get conversation_id and message history |
| /api/chat/send | POST | Send text message; receive AI reply |
| /api/chat/send-voice | POST | Upload audio file; transcribe via STT; process through agent |
| /api/chat/synthesize | POST | Convert text to audio (TTS) |
| /api/chat/history/<id> | GET | Retrieve full conversation message history |
| /api/chat/reset | POST | Reset chat session (start new conversation) |

---

## Text Message Flow

```
User types message -> chat.js
  POST /api/chat/send { message, conversation_id, business_id }
        |
        v
  Agent.process_message() -> AI reply
        |
        v
  Response: { reply, status, workflow_state, executed_tools, ui_action, metrics }
        |
        v
  chat.js renders message bubble
  If ui_action present: renders interactive widget (slot chips, doctor list, etc.)
```

---

## Voice Input Flow

```
User clicks mic button -> chat.js
  MediaRecorder API records audio (WebM format)
  User releases mic
  POST /api/chat/send-voice { audio file, conversation_id }
        |
        v
  routes/chat.py: send_voice()
    audio_bytes = audio_file.read()
    STTClient.transcribe(audio_bytes, mime_type=audio/webm)
        |
        v
  Transcribed text processed through Agent same as text message
        |
        v
  Response: { transcript, reply, input_mode: voice, ... }
        |
        v
  chat.js shows transcript, then AI reply
  User can optionally request TTS playback of the reply
```

---

## Voice Output (TTS) Flow

```
User clicks speaker icon on AI message
  POST /api/chat/synthesize { text: AI_reply_text }
        |
        v
  TTSClient.synthesize(text)
        |
        v
  Returns audio/wav bytes
        |
        v
  chat.js creates Audio object and plays it
```

---

## UI Actions

The agent can return a ui_action object in the response to trigger interactive widgets:

| ui_action.type | Frontend Behavior |
|---|---|
| slot_picker | Render clickable time slot chips |
| doctor_selection | Render doctor choice buttons |
| service_selection | Render service choice buttons |
| booking_confirmation | Render a booking summary card |

When a patient clicks a chip or button, chat.js sends it as a regular text message, which the agent processes normally.

---

## Frontend Files

| File | Purpose |
|---|---|
| templates/chat.html | Chat UI HTML template |
| static/js/chat.js | All chat client-side logic (~26KB) |
| templates/index.html | Landing page with business context |

---

## Limitations

- No real-time push (WebSocket) - chat works via polling/request-response only
- TTS is on-demand only - no automatic audio playback of AI replies
- Voice recording requires browser microphone permission
- Voice input quality depends on STT provider and network conditions

---
