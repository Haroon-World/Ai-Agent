# 03 — Target Users and Use Cases

## User Categories

ClinicConnect AI has three distinct user categories.

## 1. End Users (Patients / Customers)

These are the people who interact with the AI receptionist directly.

### Who They Are
- Patients of a clinic or dental practice
- Customers of a lab or diagnostic center
- Family members booking on behalf of a patient

### Primary Use Cases

| Use Case | Channel |
|---|---|
| Ask about doctors and specializations | Web / WhatsApp |
| Ask about services and pricing | Web / WhatsApp |
| Check clinic hours and address | Web / WhatsApp |
| Check available appointment slots | Web / WhatsApp |
| Book an appointment | Web / WhatsApp |
| Cancel an appointment | Web / WhatsApp |
| Reschedule an appointment | Web / WhatsApp |
| Check appointment status | Web / WhatsApp |
| Request a human receptionist | Web / WhatsApp |
| Speak instead of type (voice input) | Web only |
| Send voice note | WhatsApp |

## 2. Business Administrators (Clinic Admins)

These are clinic managers or designated receptionists who manage their clinic setup.

### Access
- Login at /admin/login
- Each admin account is tied to exactly one business_id

### What They Can Do

| Action | Location |
|---|---|
| View dashboard | /admin/dashboard |
| Manage appointments | /admin/appointments |
| View live conversations | /admin/conversations |
| Take over a conversation (human handoff) | /admin/conversations |
| Send a direct reply to a patient | /admin/conversations |
| Release conversation back to AI | /admin/conversations |
| Manage doctors | /admin/doctors |
| Manage services | /admin/services |
| View reminders | /admin/reminders |
| Manage subscription | /admin/subscription |

## 3. Platform Owner (SaaS Master Administrator)

The operator of the ClinicConnect AI SaaS business.

### Access
- Login at /platform/login with is_platform_admin=True account
- Explicitly blocked from clinic admin portals

### What They Can Do

| Action | Location |
|---|---|
| View all tenants | /platform/dashboard |
| Create new tenant | Platform dashboard |
| Send clinic onboarding invitation | Platform dashboard |
| Approve/reject subscription requests | Platform dashboard |
| Cancel/suspend tenants | Platform dashboard |
| Configure WhatsApp accounts per tenant | Platform dashboard |

---

## Primary Target Business Types

| Business Type | Fit |
|---|---|
| Multi-specialty polyclinics | Excellent - designed around per-doctor service routing |
| Dental clinics | Excellent - original use case |
| General practice clinics | Excellent |
| Diagnostic laboratories | Good - test booking works with appointment model |
| Dermatology clinics | Good |
| Physiotherapy centers | Good |
| Eye clinics | Good |
| Large hospitals | Partial - may need departmental structure |
| Non-appointment businesses | Poor fit - platform is appointment-centric |

---

## Why Multiple Business Types Are Supported

The AI agent contains NO hardcoded knowledge about dental procedures, lab tests, or any specific specialty. Instead:

1. Doctors are defined by the admin with any specialization string
2. Services are defined by the admin with any name, description, duration, and price
3. The system prompt reads these from the database dynamically
4. The LLM reasons from this runtime context

Adding a new clinic type requires only entering doctors and services in the admin panel - no code changes.

---
