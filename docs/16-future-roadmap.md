# 16 — Future Roadmap

This document outlines the evolutionary trajectory of ClinicConnect AI. Items are categorized by time horizon and architectural priority, clearly separating verified existing capabilities from planned features.

---

## Short-Term Priorities (Next Release Cycle)

### 1. Automated Reminder Dispatch Engine
- **Current State:** `Reminder` database records are generated upon booking confirmation (`scheduled_for = appointment_datetime - 24 hours`), but no active worker polls or delivers them.
- **Planned Work:**
  - Implement a lightweight background worker using APScheduler or Celery + Redis.
  - Periodic polling query: `Reminder.query.filter(Reminder.status == "SCHEDULED", Reminder.scheduled_for <= now_utc)`.
  - Dispatches WhatsApp notification templates via `WhatsAppService.send_template` or SMS/Email.
  - Automatically transitions status from `SCHEDULED` to `SENT` or `FAILED`.

### 2. Production Security Hardening
- **Current State:** Application enforces tenant isolation and password hashing, but lacks web application firewall protections.
- **Planned Work:**
  - Integrate `Flask-Limiter` with Redis backend for IP and session-based rate limiting on `/api/chat/send`, `/api/whatsapp/webhook`, and `/admin/login`.
  - Enforce `WHATSAPP_APP_SECRET` verification in all environments, removing the development fail-open bypass.
  - Integrate `Flask-WTF` for CSRF token validation on server-rendered admin and onboarding forms.
  - Set hardened session cookie parameters (`SESSION_COOKIE_SECURE = True`, `SESSION_COOKIE_HTTPONLY = True`, `SESSION_COOKIE_SAMESITE = 'Lax'`).

### 3. PostgreSQL Staging & Production Deployment
- **Current State:** Database configuration and driver (`psycopg2-binary`) support PostgreSQL, but tests and development utilize SQLite.
- **Planned Work:**
  - Establish automated Docker-based PostgreSQL migration and staging validation.
  - Replace additive `auto_migrate_db` with official Alembic / Flask-Migrate revision trees for zero-downtime migrations.

---

## Medium-Term Roadmap (SaaS Scale & Expansion)

### 1. Automated Tenant Onboarding & Payment Gateway
- **Current State:** Subscription requests are manually approved or rejected by the platform admin in the platform dashboard.
- **Planned Work:**
  - Integrate Stripe / LemonSqueezy / local payment gateways (JazzCash, EasyPaisa, PayFast for Pakistan).
  - Webhook-driven subscription activation: payment confirmation automatically extends `subscription_expires_at` and sets `subscription_status = "active"`.
  - Self-service invoice and receipt generation.

### 2. Meta WhatsApp Embedded Signup
- **Current State:** Setting up a WhatsApp number requires manual entry of `phone_number_id`, `waba_id`, and `access_token` in the database.
- **Planned Work:**
  - Integrate Meta's official Embedded Signup Flow (FBE - Facebook Business Extension).
  - Allows clinic administrators to click "Connect WhatsApp", log into their Meta Business Manager, and grant permissions in an OAuth popup.
  - Automatic token exchange and webhook subscription via Meta Graph API without developer console intervention.

### 3. Real-Time Admin Console via WebSockets
- **Current State:** Admin conversation dashboard requires manual page refresh to see new patient messages or handoff requests.
- **Planned Work:**
  - Introduce Flask-SocketIO or SSE (Server-Sent Events) to stream inbound messages and conversation state transitions directly to the browser.
  - Desktop audio chime and push notification alerts when human handoff is requested.

### 4. Advanced Analytics & Operational Reporting
- **Current State:** Basic statistics (total appointments, today's count) shown on the admin dashboard.
- **Planned Work:**
  - Visual charts showing booking conversion funnel: Visitor -> Inquiry -> Availability Check -> Confirmation.
  - AI containment rate metrics (percentage of conversations completed without human handoff).
  - Peak inquiry hours and doctor slot occupancy heatmaps.

### 5. WhatsApp Interactive Messages (Buttons & Lists)
- **Current State:** AI formats available slots as text lists; frontend web chat renders interactive chips, but WhatsApp relies on natural language text responses.
- **Planned Work:**
  - Transform `ui_action` payloads into Meta Graph API interactive button and radio list messages (`type: "interactive"`).
  - Patients on WhatsApp tap structured buttons for doctor, date, and slot selection.

---

## Long-Term Vision (Enterprise Multi-Channel AI Platform)

### 1. Omni-Channel Expansion
- **Current State:** Web chat and WhatsApp Cloud API channels supported.
- **Planned Work:**
  - **Instagram Direct Messages:** Leverage Meta Graph API Instagram Messaging endpoints using the existing agent pipeline.
  - **Two-Way SMS:** Twilio / MessageBird adapter for patients without smartphones or data access.
  - **Inbound Phone Voice Agent:** Direct telephony integration (Twilio Voice / LiveKit / Daily) using real-time streaming STT and conversational TTS.

### 2. Multi-Branch & Departmental Hierarchy
- **Current State:** Each tenant is a single clinic with multiple doctors and services.
- **Planned Work:**
  - Hierarchical tenant model: Enterprise -> Region -> Branch Location -> Department -> Doctor.
  - Cross-branch availability lookup and centralized billing.

### 3. Dynamic Knowledge Base & Retrieval-Augmented Generation (RAG)
- **Current State:** Clinic policies and FAQs are injected directly into the LLM system prompt from `Business.policies`.
- **Planned Work:**
  - Chunked document ingestion for clinic PDFs (insurance coverage guides, treatment preparation guidelines).
  - Vector similarity search (e.g. pgvector or ChromaDB) to retrieve exact policy excerpts before prompt assembly.

---

## Explicitly Out of Scope

To maintain product focus, the following areas are deliberately excluded from the roadmap:

| Domain | Reason for Exclusion |
|---|---|
| Full Electronic Medical Records (EMR/EHR) | Significant HIPAA/GDPR regulatory burden, clinical charting complexity. System integrates with EMRs, but will not replace them. |
| Diagnostic AI / Symptom Treatment Prescription | Strict medical liability and regulatory constraints. System functions strictly as a scheduling receptionist, never diagnosing conditions. |
| In-App Telemedicine Video Calls | High bandwidth overhead; better served by specialized third-party providers (Zoom, Doxy.me). |
| Custom Model Fine-Tuning | Runtime prompt grounding provides better agility and zero retraining latency compared to fine-tuning foundation models. |

---

*Next: [17-testing-and-quality.md](./17-testing-and-quality.md)*
