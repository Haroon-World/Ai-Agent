# 21 — Design Decisions

This document explains the key architectural and implementation decisions made in this project, and the reasoning behind them.

---

## 1. Prompt Grounding vs. Model Fine-Tuning

Decision: Use pre-trained foundation models with dynamic runtime prompt injection. Do not fine-tune.

Reasoning:
- Fine-tuning requires significant training data per clinic, compute resources, and retraining on every data change
- Prompt grounding allows ANY clinic to be onboarded by simply entering their data in the database
- Business data changes (new doctor, price change) take effect immediately without model retraining
- Foundation models (Gemini, Groq-hosted LLaMA) already have strong medical/scheduling reasoning capabilities

Tradeoffs:
- Context window limits how much information can be injected (mitigated by caching and selective inclusion)
- LLM reasoning quality depends on the provider model

---

## 2. Tool Calling for All Business Operations

Decision: The LLM can only affect business data through controlled tool calls. Direct database manipulation by the LLM is architecturally impossible.

Reasoning:
- Prevents hallucinated bookings - the LLM cannot invent appointment records
- All inputs to database operations are validated by service functions before writes
- Makes the agent behavior auditable (all tool calls are logged as Messages)
- Enables deterministic business rules (slot calculation, double-booking prevention) regardless of LLM provider

Tradeoffs:
- More complex orchestration loop vs. a simple chat completion
- Tool definitions must be maintained in sync across providers

---

## 3. Shared Multi-Tenant Architecture vs. One App Per Clinic

Decision: One shared application with business_id isolation, not separate deployments per clinic.

Reasoning:
- Dramatically lowers operational cost (single server, single database, single deployment)
- New clinics are onboarded through data entry, not code deployment
- Shared codebase means all clinics get bug fixes and improvements simultaneously
- Horizontal scaling (when needed) applies to the entire platform

Tradeoffs:
- Any bug in tenant isolation logic could affect multiple clinics (mitigated by extensive tests)
- A single-point-of-failure for all tenants
- Noisy neighbor risk (one active clinic's load affects others)

---

## 4. SQLite for Development, PostgreSQL-Ready for Production

Decision: Default to SQLite for zero-configuration local development, with PostgreSQL support via DATABASE_URL.

Reasoning:
- SQLite requires zero setup - developers can run the app with python app.py with no database server
- SQLAlchemy abstraction makes the switch transparent to application code
- WAL mode makes SQLite viable for low-to-medium concurrent access

Tradeoffs:
- SQLite partial indexes behave slightly differently from PostgreSQL
- WAL mode does not help with true multi-process write concurrency (needed for Gunicorn multi-worker)
- Migration from SQLite to PostgreSQL requires a data migration step

---

## 5. Custom Auto-Migration vs. Alembic

Decision: Build a custom additive auto_migrate_db() instead of using Alembic/Flask-Migrate.

Reasoning:
- Eliminates the need for developers to run migration commands after git pull
- Early-stage project with frequent schema changes - Alembic would require a migration file per change
- Auto-migration happens on startup automatically

Tradeoffs:
- Does not handle column removal, type changes, or index changes (additive only)
- Will need to be replaced with a proper migration tool before production at scale
- Not appropriate for production databases with real data without careful review

---

## 6. WhatsApp Cloud API vs. WhatsApp Web Automation

Decision: Use Meta WhatsApp Cloud API exclusively. No browser automation (Baileys, WWebJS, etc.).

Reasoning:
- Browser automation tools violate Meta's Terms of Service and risk permanent WhatsApp ban
- Cloud API is officially supported, reliable, and scalable
- Cloud API supports multiple tenants via multiple phone_number_ids under one application
- Cloud API provides webhook signatures for security verification

Tradeoffs:
- Requires a registered WhatsApp Business Account and Meta Developer setup
- Requires a public HTTPS endpoint (complicates development without a tunnel)
- Template messages required for initiating conversations outside 24-hour window

---

## 7. Request-Scoped Caching (RequestCache)

Decision: Cache business info, doctors, and services within a single request using Flask's g object, falling back to thread-local storage outside requests.

Reasoning:
- A single agent turn may call get_clinic_info, get_doctors, and get_services multiple times (once per LLM turn and once when building the system prompt)
- Without caching, the same database queries run 3-5 times per request
- Flask g is naturally request-scoped and garbage-collected after each request

Tradeoffs:
- Not distributed (does not work across multiple processes/Gunicorn workers)
- Cache is never stale within a single request, but may be stale across requests (acceptable for this data)

---

## 8. Conversation State Persisted in Database

Decision: All conversation state (workflow_state, selected_doctor_id, requested_date, etc.) is persisted to the database after each message.

Reasoning:
- Allows the conversation to survive server restarts and process crashes
- Multi-process deployment (Gunicorn) would share state correctly via database
- Enables admin dashboard to show current conversation context
- Chat history replay is straightforward - just load from DB

Tradeoffs:
- Database write on every message turn adds latency
- More complex than in-memory state for single-server deployment

---

## 9. Visitor ID for Web Chat Identity

Decision: Web chat visitors are identified by a UUID visitor_id stored in the Flask session cookie.

Reasoning:
- Allows persistent conversation threads without requiring login
- Prevents IDOR: a visitor cannot access another visitor's conversation by guessing an ID
- When patient books, Customer record is created and linked to the conversation

Tradeoffs:
- If the patient clears cookies, their conversation thread is lost
- Different browsers or devices for the same person create separate threads

---
