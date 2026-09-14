# ClinicConnect AI — AI Receptionist SaaS Platform

A multi-tenant SaaS platform that deploys an AI-powered receptionist for appointment-based businesses (clinics, dental practices, laboratories, etc.) across web chat and WhatsApp channels.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
copy .env.example .env
# Edit .env (minimum: set LLM_PROVIDER=mock for zero-config start)

# Start the development server
python app.py

# Or on Windows, start Flask + ngrok tunnel together:
run.bat
```

Access points:
- Customer chat: http://127.0.0.1:5001/chat
- Admin portal:  http://127.0.0.1:5001/admin
- Platform console: http://127.0.0.1:5001/platform/login

Default admin credentials (development only):
- Clinic admin: `admin` / `admin123`
- Platform owner: `clinicconnectaipro` / `@Clinic2026`

> ⚠️ Change all default credentials before any public deployment.

---

## Documentation

All technical and product documentation is in the [`docs/`](./docs/) directory.

| Document | Description |
|---|---|
| [01 — Project Overview](./docs/01-project-overview.md) | What this is, why it exists, what's built and what isn't |
| [02 — Product Goals and Scope](./docs/02-product-goals-and-scope.md) | Implemented, partial, planned, and out-of-scope capabilities |
| [03 — Target Users and Use Cases](./docs/03-target-users-and-use-cases.md) | Patients, clinic admins, platform owner |
| [04 — Technology Stack](./docs/04-technology-stack.md) | Every technology used and why |
| [05 — System Architecture](./docs/05-system-architecture.md) | Architecture diagram, blueprints, routes, separation of concerns |
| [06 — AI Agent Architecture](./docs/06-ai-agent-architecture.md) | How the agent works, prompting, tool loop, state machine |
| [07 — Data and Database Architecture](./docs/07-data-and-database-architecture.md) | All 14 database models documented |
| [08 — Request and Message Flows](./docs/08-request-and-message-flows.md) | End-to-end flow for booking, WhatsApp, handoff, and more |
| [09 — Multi-Tenant Architecture](./docs/09-multi-tenant-architecture.md) | Tenant isolation, routing, enforcement |
| [10 — Website Chat](./docs/10-website-chat.md) | Web chat channel, voice input, TTS, UI actions |
| [11 — WhatsApp Architecture](./docs/11-whatsapp-architecture.md) | Meta Cloud API integration, webhook security, routing |
| [12 — Voice AI](./docs/12-voice-ai.md) | STT/TTS pipeline, providers, fallback, multilingual |
| [13 — Admin and Platform Architecture](./docs/13-admin-and-platform-architecture.md) | Admin portal, platform console, onboarding, subscription lifecycle |
| [14 — Security and Tenant Isolation](./docs/14-security-and-tenant-isolation.md) | Security controls, known risks, hardening checklist |
| [15 — Current Implementation Status](./docs/15-current-implementation-status.md) | Component-by-component status based on code review |
| [16 — Future Roadmap](./docs/16-future-roadmap.md) | Planned improvements by timeline |
| [17 — Testing and Quality](./docs/17-testing-and-quality.md) | 48 test files, coverage areas, how to run tests |
| [18 — Deployment and Environments](./docs/18-deployment-and-environments.md) | Dev, test, and production environment setup |
| [19 — Configuration and Environment Variables](./docs/19-configuration-and-environment-variables.md) | Every .env variable documented |
| [20 — Development Guide](./docs/20-development-guide.md) | Project structure, common tasks, how to extend |
| [21 — Design Decisions](./docs/21-design-decisions.md) | Why key architecture decisions were made |
| [22 — Limitations and Known Risks](./docs/22-limitations-and-known-risks.md) | Technical debt, production risks, LLM behavior risks |
| [23 — Glossary](./docs/23-glossary.md) | Definitions of terms used throughout the docs |

---

## What This Platform Does

- Deploys one shared AI agent for multiple business tenants
- Patients interact via website chat or WhatsApp
- AI handles: clinic info, doctor listing, service pricing, appointment booking, cancellation, rescheduling
- Staff can take over any conversation and reply directly (including WhatsApp)
- Platform owner manages all tenants via a separate master console
- Subscription lifecycle: 30-day trial → paid plan (manually approved)

## What Channels Are Supported

| Channel | Status |
|---|---|
| Web chat (browser) | ✅ Implemented |
| WhatsApp | ✅ Implemented |
| Voice input (web + WhatsApp) | ✅ Implemented (STT) |
| Voice output (web only) | ✅ Implemented (TTS) |

## Technology

Python · Flask · SQLAlchemy · Google Gemini · Groq · Meta WhatsApp Cloud API

---

## Running Tests

```bash
python -m unittest discover tests
```

No API keys needed — tests use the mock LLM provider.
