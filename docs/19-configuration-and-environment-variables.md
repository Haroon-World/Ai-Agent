# 19 — Configuration and Environment Variables

## Overview

All configuration is managed through environment variables loaded from .env at startup via python-dotenv. A .env.example template is provided with every variable documented.

---

## Configuration File

- Location: config/config.py
- Class: Config
- Loading: python-dotenv loads .env on startup
- Access: from config.config import Config

---

## All Environment Variables

### Flask Core

| Variable | Default | Description |
|---|---|---|
| SECRET_KEY | dev-secret-key-change-this | Flask session signing key. MUST be changed in production |
| DEBUG | False | Enable Flask debug mode. Never True in production |
| PORT | 5001 | Port to run Flask server on |
| DEFAULT_BUSINESS_ID | 1 | Fallback business_id when tenant cannot be resolved |

### Database

| Variable | Default | Description |
|---|---|---|
| DATABASE_URL | (empty = SQLite) | Database URL. Empty uses instance/ai_business_agent.db. Set to postgresql://... for PostgreSQL |

### LLM Provider

| Variable | Default | Description |
|---|---|---|
| LLM_PROVIDER | gemini | LLM provider: gemini, groq, or mock |
| GEMINI_API_KEY | (required if gemini) | Google AI Studio API key |
| GEMINI_MODEL | gemini-3.5-flash-lite | Gemini model name |
| GROQ_API_KEY | (required if groq) | Groq API key |
| GROQ_MODEL | openai/gpt-oss-120b | Groq model name |

### Speech (STT)

| Variable | Default | Description |
|---|---|---|
| STT_PROVIDER | groq | STT provider: groq, gemini, or mock |
| GROQ_STT_MODEL | whisper-large-v3 | Groq Whisper model |

### Speech (TTS)

| Variable | Default | Description |
|---|---|---|
| TTS_PROVIDER | gemini | TTS provider: gemini, groq, or mock |
| GROQ_TTS_MODEL | playai-tts | Groq TTS model |
| GROQ_TTS_VOICE | Fritz-PlayAI | Groq TTS voice |

### WhatsApp

| Variable | Default | Description |
|---|---|---|
| WHATSAPP_PHONE_NUMBER_ID | (required) | Meta phone number ID for default WhatsApp account |
| WHATSAPP_ACCESS_TOKEN | (required) | Meta system user access token |
| WHATSAPP_WEBHOOK_VERIFY_TOKEN | (required) | Token for Meta webhook verification |
| WHATSAPP_APP_SECRET | (required in production) | App secret for HMAC-SHA256 signature verification |
| WHATSAPP_API_VERSION | v19.0 | Meta Graph API version |
| WHATSAPP_WABA_ID | (optional) | WhatsApp Business Account ID |

### Admin Credentials

| Variable | Default | Description |
|---|---|---|
| ADMIN_USERNAME | admin | Default clinic admin username |
| ADMIN_PASSWORD | admin123 | Default clinic admin password. MUST be changed in production |

### Platform Admin Credentials

| Variable | Default | Description |
|---|---|---|
| PLATFORM_ADMIN_USERNAME | clinicconnectaipro | Platform master admin username |
| PLATFORM_ADMIN_PASSWORD | @Clinic2026 | Platform master admin password. MUST be changed in production |

### Email (SMTP)

| Variable | Default | Description |
|---|---|---|
| SMTP_HOST | (empty) | SMTP server hostname e.g. smtp.gmail.com |
| SMTP_PORT | 587 | SMTP port |
| SMTP_USER | (empty) | SMTP username/email address |
| SMTP_PASSWORD | (empty) | SMTP password |
| SMTP_USE_TLS | true | Use STARTTLS |
| MAIL_DEFAULT_SENDER | noreply@clinicconnectai.com | From address for emails |

### Session

| Variable | Default | Description |
|---|---|---|
| SESSION_TIMEOUT_MINUTES | 60 | Admin session timeout in minutes |

---

## Minimum Required Configuration

### For development with mock LLM (no API keys needed):

```
SECRET_KEY=any-local-dev-key
LLM_PROVIDER=mock
STT_PROVIDER=mock
TTS_PROVIDER=mock
```

### For development with Gemini:

```
SECRET_KEY=any-local-dev-key
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-google-ai-studio-key
STT_PROVIDER=groq
GROQ_API_KEY=your-groq-api-key
TTS_PROVIDER=gemini
```

### For WhatsApp integration:

```
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_ACCESS_TOKEN=your-access-token
WHATSAPP_WEBHOOK_VERIFY_TOKEN=your-verify-token
WHATSAPP_APP_SECRET=your-app-secret
```

---

## Configuration Class Properties

The Config class in config/config.py exposes these as class attributes:

```python
class Config:
    SECRET_KEY = os.getenv(SECRET_KEY, dev-secret-key-change-this)
    SQLALCHEMY_DATABASE_URI = ...  # normalized DATABASE_URL
    LLM_PROVIDER = os.getenv(LLM_PROVIDER, gemini)
    GEMINI_API_KEY = os.getenv(GEMINI_API_KEY)
    GEMINI_MODEL = os.getenv(GEMINI_MODEL, gemini-3.5-flash-lite)
    GROQ_API_KEY = os.getenv(GROQ_API_KEY)
    GROQ_MODEL = os.getenv(GROQ_MODEL, openai/gpt-oss-120b)
    STT_PROVIDER = os.getenv(STT_PROVIDER, groq)
    TTS_PROVIDER = os.getenv(TTS_PROVIDER, gemini)
    WHATSAPP_PHONE_NUMBER_ID = os.getenv(WHATSAPP_PHONE_NUMBER_ID)
    WHATSAPP_ACCESS_TOKEN = os.getenv(WHATSAPP_ACCESS_TOKEN)
    WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv(WHATSAPP_WEBHOOK_VERIFY_TOKEN)
    WHATSAPP_APP_SECRET = os.getenv(WHATSAPP_APP_SECRET)
    WHATSAPP_API_VERSION = os.getenv(WHATSAPP_API_VERSION, v19.0)
    DEFAULT_BUSINESS_ID = int(os.getenv(DEFAULT_BUSINESS_ID, 1))
    ADMIN_USERNAME = os.getenv(ADMIN_USERNAME, admin)
    ADMIN_PASSWORD = os.getenv(ADMIN_PASSWORD, admin123)
    PLATFORM_ADMIN_USERNAME = os.getenv(PLATFORM_ADMIN_USERNAME, clinicconnectaipro)
    PLATFORM_ADMIN_PASSWORD = os.getenv(PLATFORM_ADMIN_PASSWORD, @Clinic2026)
    SESSION_TIMEOUT_MINUTES = int(os.getenv(SESSION_TIMEOUT_MINUTES, 60))
```

---
