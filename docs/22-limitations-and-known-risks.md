# 22 — Limitations and Known Risks

This document lists current technical limitations, architecture gaps, and known risks that should be understood before production deployment.

---

## Production Readiness

| Item | Risk Level | Notes |
|---|---|---|
| No production WSGI server configured | HIGH | Flask development server is single-threaded and not for production |
| No HTTPS configuration | HIGH | Required by Meta for WhatsApp webhooks |
| Default credentials in config | HIGH | ADMIN_PASSWORD=admin123 and SECRET_KEY must be changed |
| WHATSAPP_APP_SECRET not enforced in dev | HIGH | Webhook signature verification bypassed without it |
| No CSRF protection | MEDIUM | Forms are vulnerable to CSRF attacks |
| No rate limiting | MEDIUM | Login brute force and LLM cost abuse possible |

---

## Architecture Limitations

### No Background Job Scheduler

The Reminder system creates database records at booking time but has no mechanism to send them. There is no Celery, APScheduler, Redis, or cron job. This means:
- Appointment reminders are never actually sent to patients
- Implementing this requires adding a background task infrastructure

### Single-Process SQLite in Production

SQLite WAL mode supports concurrent reads but serializes writes. Under Gunicorn with multiple workers:
- Multiple workers will compete for write locks
- busy_timeout=30000 will cause requests to wait up to 30 seconds
- PostgreSQL is required for true multi-process production deployment

### In-Process Request Cache

RequestCache uses Flask g (within requests) and thread-local storage (outside requests). In multi-process deployments:
- Each worker has an independent cache
- Cache invalidation is per-process, not distributed
- For most read-heavy clinic data this is acceptable

### No Real-Time Admin UI

The admin conversation page has no WebSocket or polling mechanism. Admins must manually refresh to see new messages. This makes the human handoff flow less usable in practice.

### Custom Migration System

auto_migrate_db() is additive only. Before production:
- Column type changes cannot be applied automatically
- Column removals require manual SQL
- Index changes require manual SQL
- A real migration tool (Alembic) should be adopted for production

---

## LLM Behavior Risks

| Risk | Notes |
|---|---|
| Hallucination of information | The system prompt grounds the LLM with real data, but the LLM may occasionally generate plausible-but-wrong responses for edge cases |
| Tool argument hallucination | LLM may pass incorrect doctor_id or service_id; service functions validate these but may return errors |
| Language detection errors | Roman Urdu vs. English detection is heuristic-based; may fail for ambiguous inputs |
| STT transcription errors | Voice notes with heavy background noise or strong accents may be misrecognized |
| LLM provider outage | If the primary provider is down, the agent fails; no automatic provider failover in the LLM layer (unlike STT) |
| Context window limits | Very long conversations (100+ messages) may exceed LLM context; older messages may be dropped |

---

## WhatsApp Integration Risks

| Risk | Notes |
|---|---|
| 24-hour messaging window | WhatsApp restricts businesses from sending messages outside a 24-hour window after the last patient message. AI replies to old conversations will fail |
| Access token expiry | Meta system user tokens expire. Expired tokens will cause all outbound messages to fail silently |
| Rate limits | Meta enforces per-number message rate limits; bulk usage could trigger throttling |
| Template approval | Template messages require Meta pre-approval; unavailable templates will error |

---

## Known Technical Debt

| Item | Location | Notes |
|---|---|---|
| Legacy flat schedule columns | Doctor.working_days, start_time, end_time | Superseded by DoctorSchedule per-day records; legacy columns are stale |
| Hardcoded Urdu vocabulary | ai/agent.py, ai/llm_client.py | Large hardcoded dicts; would benefit from a localization file |
| seed.py runs on every startup | models/__init__.py, app.py | Checks if records exist before inserting, but still queries DB on every start |
| ai/llm_client.py is very large | ai/llm_client.py (3238 lines) | Mixes provider adapters, NLP helpers, date parsing; could be split into modules |
| ai/agent.py is very large | ai/agent.py (1665 lines) | Core orchestration + helper functions; could be split |
| No logging configuration | All modules | Uses Python logging but no centralized log configuration or file handler |

---
