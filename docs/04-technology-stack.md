# 04 — Technology Stack

This document lists every technology actually present in the codebase, identified directly from source code, dependency definitions, and configuration files.

---

## Backend & Frameworks

### Python 3.10+
- **What:** Modern, high-level programming language.
- **Role:** Core runtime language for the entire backend application, AI orchestration, tools, and background helper scripts.
- **Why Chosen:** Extensive AI/ML ecosystem, first-class SDKs for Google Gemini (`google-genai`) and Groq (`groq`), native audio byte processing, and clean cross-platform execution.

### Flask >= 3.1.0
- **What:** Lightweight, extensible Python WSGI web application framework.
- **Role:** HTTP routing, blueprint architecture, session lifecycle management, request context handling, and template rendering.
- **Architectural Pattern:** Uses the Application Factory pattern (`create_app()` in `app.py`) with modular blueprints (`chat_bp`, `appointments_bp`, `admin_bp`, `platform_bp`, `whatsapp_bp`).
- **Why Chosen:** Minimal overhead, un-opinionated structure allowing custom LLM loop integration without framework impedance, and native blueprint-based route modularization.

### Flask-SQLAlchemy >= 3.1.0 & SQLAlchemy >= 2.0.0
- **What:** Python SQL Toolkit and Object-Relational Mapper (ORM).
- **Role:** Database modeling, relational query construction, migration pragmas, session transaction boundaries, and multi-tenant scoping.
- **Why Chosen:** Abstracted database layer enabling seamless switching between local development (SQLite with WAL) and enterprise production (PostgreSQL) via connection string configuration.

### python-dotenv >= 1.0.0
- **What:** Environment variable configuration loader.
- **Role:** Reads `.env` files into `os.environ` on startup (`config/config.py`).
- **Why Chosen:** Strict adherence to 12-factor app methodology, keeping API keys, database URLs, and secrets isolated from version control.

### Werkzeug
- **What:** WSGI utility library bundled with Flask.
- **Role:** Cryptographic password hashing (`generate_password_hash`, `check_password_hash`) using salted PBKDF2+SHA256, HTTP exception handling, and development server utilities.

### psycopg2-binary >= 2.9.9
- **What:** PostgreSQL driver for Python.
- **Role:** Active when `DATABASE_URL` targets a PostgreSQL cluster. Supports connection pooling, SSL modes, and native PostgreSQL types.

---

## Database Layer

### SQLite (Development Default)
- **File Location:** `instance/ai_business_agent.db`
- **Driver / Engine:** Built-in Python `sqlite3` through SQLAlchemy.
- **Optimizations Configured:**
  - `PRAGMA journal_mode=WAL;` — Write-Ahead Logging enables non-blocking concurrent readers during database writes.
  - `PRAGMA busy_timeout=30000;` — Threads wait up to 30 seconds before throwing lock errors.
  - `PRAGMA synchronous=NORMAL;` — Balances durability with write throughput.
- **Limitations:** Multi-worker WSGI concurrency requires centralized database locking; unsuitable for horizontal multi-server scaling.

### PostgreSQL (Production Target)
- **Status:** Supported via connection string normalization (`postgres://` -> `postgresql://` in `config/config.py`).
- **Role:** Production target for high-concurrency multi-tenant operations, connection pooling, and multi-worker WSGI deployment.

### Dynamic Auto-Migration Engine (`auto_migrate_db`)
- **File:** `models/__init__.py`
- **Mechanic:** Inspects SQLite `PRAGMA table_info()` or PostgreSQL column schemas against SQLAlchemy model column metadata at application boot.
- **Action:** Dynamically issues `ALTER TABLE {table} ADD COLUMN {column} {type}` for any missing attributes.
- **Classification:** Additive schema sync; non-destructive. Does not alter types or drop columns.

---

## AI & LLM Engine

### Google Gemini API (`google-genai >= 2.0.0`)
- **Primary Model:** `gemini-3.5-flash-lite` (configurable via `GEMINI_MODEL`).
- **TTS Model:** `gemini-2.5-flash-preview-tts`.
- **Role:** Primary cloud LLM for prompt-grounded intent extraction, canonical tool calling, multilingual Urdu/Roman Urdu dialogue generation, and speech generation.
- **SDK Integration:** Modern `google.genai` client, converting canonical tools into `genai_types.FunctionDeclaration` and `genai_types.Tool`.

### Groq Cloud API (`groq >= 1.0.0`)
- **Primary LLM:** `openai/gpt-oss-120b` or `llama3-70b-8192` (via LPU hardware).
- **STT Model:** `whisper-large-v3`.
- **TTS Model:** `playai-tts` (Voice: `Fritz-PlayAI`).
- **Role:** High-speed, low-latency fallback/alternative LLM provider; primary high-accuracy Whisper speech-to-text transcriber.

### Mock LLM / STT / TTS Provider
- **File:** `ai/llm_client.py`, `ai/speech_client.py`
- **Role:** Fully deterministic in-process stub layer returning realistic responses and synthetic audio bytes without network calls.
- **Importance:** Enables complete offline testing, CI test isolation, and local development without active API credits.

### Unified LLM Abstraction Layer (`ai/llm_client.py`)
- **Size & Scope:** ~3,238 lines of provider adapter logic and multilingual token extraction.
- **Capabilities:**
  - Bidirectional tool declaration translation between provider-agnostic schemas, Gemini types, and OpenAI/Groq function calling protocols.
  - Multilingual fuzzy roster matching for doctor names, Pakistani phone extraction, relative date calculation in Asia/Karachi timezone, and Urdu calendar parsing.

---

## Speech & Audio Processing

### Speech-to-Text (STT)
| Provider | Underlying Engine | Formats Accepted |
|---|---|---|
| **Groq Whisper** | `whisper-large-v3` | WebM, WAV, MP3, OGG, OPUS, M4A |
| **Google Gemini** | Multimodal Audio Understanding | WebM, WAV, MP3, OGG |
| **Mock STT** | Deterministic Scripted Stub | Any audio payload |

- **Resilience:** `STTClient` automatically falls back to secondary provider if primary encounters a network or rate limit failure.
- **Domain Tuning:** Groq Whisper adapter injects medical clinic terminology and Pakistani Urdu time tokens into the transcription context.

### Text-to-Speech (TTS)
| Provider | Model / Endpoint | Audio Output |
|---|---|---|
| **Google Gemini** | `gemini-2.5-flash-preview-tts` | Linear PCM / WAV |
| **Groq PlayAI** | `playai-tts` (`Fritz-PlayAI`) | 24kHz Mono WAV |
| **Mock TTS** | In-Memory Silent WAV Header | 44-byte 8kHz Mono WAV |

---

## Messaging & Channels

### Meta WhatsApp Cloud API (Graph API v19.0)
- **Integration Type:** Official Direct Webhook & Outbound Graph REST API (no unofficial scraping or browser automation).
- **Inbound:** Handled at `/api/whatsapp/webhook` via POST.
- **Verification:** GET endpoint responds to Meta's `hub.challenge` using `WHATSAPP_WEBHOOK_VERIFY_TOKEN`.
- **Signature Security:** Cryptographic `X-Hub-Signature-256` HMAC validation using clinic or global `WHATSAPP_APP_SECRET`.
- **Deduplication:** Tracks Meta `wamid` on `Message.external_message_id` with a database unique index.
- **Voice Notes:** Inbound WhatsApp audio (OGG/OPUS) is fetched via Graph API media endpoints and transcribed through the STT pipeline.

### Web Chat Channel
- **Endpoint:** Server-rendered Jinja2 template at `/chat` and `/chat/<clinic_id>`.
- **Client Implementation:** Vanilla ES6 JavaScript (`static/js/chat.js`), MediaRecorder API for mic capture, and HTML5 Audio API for synthesized speech playback.
- **Session Security:** Cryptographically signed UUID4 `visitor_id` stored in session cookies to prevent IDOR access to conversation histories.

---

## Administration, Auth & Email

### Authentication & Session Management
- **Type:** Server-signed HTTP cookie sessions with configurable timeout (`SESSION_TIMEOUT_MINUTES`, default 60 mins).
- **Separation:** Strict segregation between Clinic Admins (`is_platform_admin=False`) and Platform Master Owners (`is_platform_admin=True`).
- **Password Reset:** 256-bit cryptographically secure tokens (`secrets.token_urlsafe(32)`) expiring in 1 hour.

### Email Delivery (`services/email_service.py`)
- **Transport:** Standard SMTP over STARTTLS (port 587) or SSL (port 465).
- **MIME Support:** Dual-part multipart (`text/plain` and `text/html`).
- **Development Fallback:** Automatically logs formatted emails to stdout when SMTP environment variables are unconfigured.

---

## Testing & Quality Assurance

### Python `unittest` Suite
- **Structure:** 48 comprehensive test modules inside `tests/`.
- **Isolation:** Spin up transient in-memory SQLite instances per test class with fresh schema generation and mock LLM dispatch.
- **Test Categories:** End-to-end multi-turn conversation tests, scheduling invariants, polyclinic doctor routing, WhatsApp webhook payloads, double-booking race conditions, and tenant isolation guards.

---

## Local Development & Tunneling Utilities

| Tool | Purpose |
|---|---|
| `run.bat` | Dual-process batch script: boots Flask server and starts ngrok tunnel on port 5001. |
| `cloudflared.exe` | Pre-bundled Cloudflare Tunnel binary for zero-config public HTTPS tunneling. |
| `pyngrok` | Python wrapper for ngrok used by startup automation. |
| `seed.py` | Automated database seed populating default tenants, doctors, weekly schedules, and service catalog. |

---

*Next: [05-system-architecture.md](./05-system-architecture.md)*
