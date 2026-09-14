# Documentation Audit Report

**Generated:** 2026-09-13  
**Audit Scope:** Complete codebase review for documentation accuracy  
**Method:** Direct source code inspection of all major files

---

## Files Created / Modified

### New Files Created

| File | Size | Description |
|---|---|---|
| docs/01-project-overview.md | ~4KB | Project overview, what is/isn't built |
| docs/02-product-goals-and-scope.md | ~5KB | Capability matrix (implemented/partial/planned) |
| docs/03-target-users-and-use-cases.md | ~3KB | User categories and use cases |
| docs/04-technology-stack.md | ~5KB | Every technology, its role, and why chosen |
| docs/05-system-architecture.md | ~5KB | Architecture diagram, blueprints, routes |
| docs/06-ai-agent-architecture.md | ~6KB | Agent loop, tools, state machine, multilingual |
| docs/07-data-and-database-architecture.md | ~8KB | All 14 database models documented |
| docs/08-request-and-message-flows.md | ~7KB | 7 end-to-end flow diagrams |
| docs/09-multi-tenant-architecture.md | ~5KB | Tenant isolation design and enforcement |
| docs/10-website-chat.md | ~4KB | Web chat channel documentation |
| docs/11-whatsapp-architecture.md | ~6KB | WhatsApp integration documentation |
| docs/12-voice-ai.md | ~4KB | STT/TTS pipeline documentation |
| docs/13-admin-and-platform-architecture.md | ~6KB | Admin portals, onboarding, subscriptions |
| docs/14-security-and-tenant-isolation.md | ~5KB | Security controls and risk registry |
| docs/15-current-implementation-status.md | ~8KB | Component-by-component status table |
| docs/16-future-roadmap.md | ~4KB | Short/medium/long-term plans |
| docs/17-testing-and-quality.md | ~6KB | All 48 test files catalogued |
| docs/18-deployment-and-environments.md | ~4KB | Dev, test, production environment guide |
| docs/19-configuration-and-environment-variables.md | ~5KB | Every .env variable documented |
| docs/20-development-guide.md | ~5KB | Project structure and development tasks |
| docs/21-design-decisions.md | ~6KB | 9 key architectural decisions with reasoning |
| docs/22-limitations-and-known-risks.md | ~5KB | Technical debt and risk registry |
| docs/23-glossary.md | ~5KB | All platform-specific terms defined |

### Modified Files

| File | Change |
|---|---|
| README.md | Complete replacement with navigation index pointing to docs/ |

---

## Architecture Observations and Ambiguities

### Confirmed from Code

1. **ai/llm_client.py (3238 lines) is the largest file** — It mixes LLM provider adapters, extensive multilingual NLP helpers (name extraction, phone extraction, fuzzy doctor matching, date parsing, intent classification), and response formatting. It is effectively three separate concerns in one file.

2. **ai/agent.py (1665 lines) is the second largest** — Contains the core orchestration loop and also houses Urdu number vocabulary dicts and time token extraction helpers.

3. **DoctorSchedule supersedes Doctor flat schedule columns** — `Doctor.working_days`, `start_time`, `end_time` are legacy columns. Per-day `DoctorSchedule` records are the authoritative schedule. The `to_dict()` method omits the flat columns when per-day schedules exist.

4. **Reminder dispatch is NOT implemented** — Reminder records are created (`ReminderService.schedule_reminder()`) but no background job scheduler exists to send them. The `reminders` table will have SCHEDULED records that are never dispatched.

5. **RequestCache is in-process only** — Uses Flask `g` object (request-scoped) and thread-local storage (outside requests). Not distributed across Gunicorn workers. This is a production concern.

6. **seed.py runs on every app startup** — It checks for existing records before inserting, but still queries the database on every start. Minor overhead but correct behavior.

7. **PostgreSQL support is code-complete but untested** — The connection string normalization and psycopg2 dependency are present, but no PostgreSQL testing has been done.

8. **WhatsApp webhook security fails open in development** — When `WHATSAPP_APP_SECRET` is not set, HMAC signature verification is skipped with a warning. This must be enforced before production.

---

## Features Confirmed Implemented That Are Not Obvious

1. **STT fallback** — If primary STT provider fails, `STTClient` automatically tries the other provider (Groq→Gemini or Gemini→Groq). This is resilience behavior not documented in the original README.

2. **Idempotency keys on bookings** — Each booking attempt generates a UUID4 idempotency key. Duplicate submissions (common with slow networks) return the existing appointment rather than creating a duplicate.

3. **Admin reply to WhatsApp via admin panel** — `HandoffService.admin_reply()` delivers messages to WhatsApp patients in real-time from the admin dashboard, not just stores them in the database.

4. **Whisper prompt tuning** — The Groq Whisper adapter includes a domain-specific prompt listing doctor names, clinic terms, and Urdu time vocabulary to bias transcription toward correct recognition.

5. **Template messages on WhatsApp** — `WhatsAppService.send_template()` is implemented for initiating conversations outside the 24-hour Meta messaging window. Not documented in the original README.

6. **Username uniqueness** — `User.username` has a UNIQUE constraint globally (not just per business). A user cannot register the same username at two different clinics.

7. **Partial subscription rejection clearing** — If a clinic has both a rejected and an approved subscription request, and the approval is newer, the rejection notification is cleared from the UI. This is subtle UX logic in `SubscriptionService`.

---

## Unverifiable Features (Cannot Confirm from Code Alone)

| Feature | Why Unverifiable |
|---|---|
| Actual WhatsApp message delivery | Requires a live Meta API account and registered number |
| Gemini TTS audio quality | Requires live API call |
| Groq LLM response quality in production | Requires live API call |
| Email delivery via SMTP | Requires live SMTP credentials |
| PostgreSQL behavior in production | No PostgreSQL test environment |
| Performance under concurrent load | No load testing performed |

---

## Contradictions Found Between Code and Previous README

The original README.md described the project as a "dental clinic" assistant. The current codebase is generic and supports multiple business types. The seed data contains both dental clinic and polyclinic examples. The documentation has been updated to reflect the actual multi-tenant, multi-business-type nature of the platform.

---

## Known Technical Debt (Documented in 22-limitations-and-known-risks.md)

1. **ai/llm_client.py** — 3238 lines mixing concerns; should be split into provider adapters + NLP helpers
2. **ai/agent.py** — 1665 lines; orchestration + helpers should be separated
3. **auto_migrate_db()** — additive only; needs replacement with Alembic before production
4. **Legacy Doctor flat columns** — `working_days`, `start_time`, `end_time` are stale but still in schema
5. **Hardcoded Urdu vocabulary** — large in-code dicts; would benefit from localization files
6. **No centralized logging configuration** — modules use Python logging but no file handler or log level config

---

## Security Issues Found (All Documented in 14-security-and-tenant-isolation.md)

| Issue | Severity |
|---|---|
| Default ADMIN_PASSWORD=admin123 | HIGH |
| Default SECRET_KEY | HIGH |
| WHATSAPP_APP_SECRET not enforced in dev | HIGH (before production) |
| No CSRF protection | MEDIUM |
| No rate limiting | MEDIUM |
| WhatsApp access tokens stored unencrypted in DB | MEDIUM |
| Session cookie flags not set | MEDIUM |

---

*This audit was generated by direct inspection of all source files in the repository.*
