# 01 — Project Overview

## What Is This Project?

**ClinicConnect AI** is a multi-tenant SaaS platform that provides AI-powered receptionist capabilities to appointment-based service businesses — primarily medical clinics, dental clinics, polyclinics, and laboratories.

The platform deploys a single, reusable AI agent that is configured at runtime to represent any registered business ("tenant"). Businesses access the system through a web chat widget on their website and through WhatsApp. They also receive a private admin dashboard to manage appointments, doctors, services, and conversations.

---

## The Core Problem Being Solved

Small and medium-sized clinics and service businesses share a common operational challenge:

> **Receptionists spend most of their working hours answering the same repetitive questions over and over again — clinic hours, available doctors, service prices, appointment availability — and manually booking, rescheduling, and cancelling appointments.**

This creates several problems:

| Problem | Impact |
|---|---|
| Staff time wasted on repetitive tasks | High cost, low efficiency |
| No 24/7 availability | Patients cannot book outside working hours |
| WhatsApp/phone queues | Patients are left waiting or ignored |
| Manual errors in scheduling | Double-bookings, missed appointments |
| No persistent conversation record | No patient history or analytics |
| Building a custom AI per clinic | Prohibitively expensive and slow |

---

## The Solution

ClinicConnect AI solves this by providing:

1. **A reusable AI receptionist** — one platform, many businesses
2. **24/7 automated handling** of the most common patient interactions
3. **Real-time appointment booking** that checks actual doctor schedules
4. **Multi-channel availability** — web chat and WhatsApp (both live)
5. **Human handoff** — staff can take over any conversation instantly
6. **A business admin dashboard** — appointments, conversations, staff management

---

## ONE Platform vs. SEPARATE Systems

A critical architectural principle of this project is the **reusability of the AI agent** across multiple business tenants.

### Separate system approach (NOT what this project does):

```
Clinic A → Build AI Agent A → Custom code → Custom database → Custom prompts
Clinic B → Build AI Agent B → Custom code → Custom database → Custom prompts
Clinic C → Build AI Agent C → Custom code → Custom database → Custom prompts
```

This is expensive, slow, and cannot be scaled.

### What ClinicConnect AI does instead:

```
Clinic A ──────────────────────────────────────┐
Clinic B ──── Single Shared AI Agent Platform ──┤
Clinic C ──────────────────────────────────────┘
                        │
                        ▼
           Business-specific runtime context:
           - Database records for THIS clinic only
           - Doctors registered for THIS clinic
           - Services and prices for THIS clinic
           - Policies and opening hours for THIS clinic
           - Separate patient conversation threads
```

The AI is **not retrained or modified per clinic**. Its knowledge is **dynamically injected at runtime** from the database for each specific business.

---

## Project Name and Branding

The platform is internally called **ClinicConnect AI**. The current seed data creates demo tenants (e.g. polyclinic setup). These are demonstration tenants only — the platform supports any appointment-based business type.

---

## What Has Been Built (Implemented)

- Core Flask backend with blueprint-based routing
- SQLite database with multi-tenant schema (PostgreSQL-ready)
- AI agent with tool calling (Gemini, Groq, Mock LLM providers)
- Dynamic system prompt generation from live database
- Appointment booking, cancellation, rescheduling workflows
- Doctor schedule management (per-day granularity with breaks)
- Customer/patient tracking across conversations
- Web chat channel (browser-based, WhatsApp-style UI)
- WhatsApp channel via Meta WhatsApp Cloud API (webhook, inbound, outbound)
- Voice input (STT: Groq Whisper + Gemini) on web chat and WhatsApp
- Voice output (TTS: Groq PlayAI + Gemini) on web chat
- Business admin dashboard with conversations, appointments, reminders
- Platform owner (SaaS master) console
- Subscription lifecycle management (trial → paid → expired → cancelled)
- Clinic onboarding via email invitation links
- Human handoff and staff direct-reply capability
- Proactive reminder records created at booking time
- Email service (password reset, subscription alerts, invitations)
- 48 automated test files covering agent, booking, multi-tenancy, security

---

## What Is NOT Yet Built (PLANNED / FUTURE)

- **Automated reminder delivery** — Reminder records are created but there is no background job scheduler to dispatch them
- **Payment/billing integration** — Subscriptions are managed manually by the platform owner
- **Meta Embedded Signup** — Automated WhatsApp number provisioning for new clinics
- **Real-time admin UI updates** — No WebSockets; admin must refresh to see new conversations
- **Analytics and reporting** — No usage charts or appointment analytics
- **Knowledge base / FAQ management** — Beyond what is injected in the system prompt
- **Production WSGI server configuration** — No Gunicorn/Nginx setup files
- **Docker / containerization** — No Dockerfile
- **CI/CD pipeline** — No GitHub Actions or similar

---

## Source of Truth Hierarchy

When documentation and code conflict, the following priority applies:

1. Current source code
2. Database models / schema
3. Automated tests
4. Configuration files
5. Git history
6. Existing documentation
7. Planned roadmap

---

*Next: [02-product-goals-and-scope.md](./02-product-goals-and-scope.md)*
