# 02 — Product Goals and Scope

## Primary Goal

Build a reusable, multi-tenant AI receptionist SaaS platform that reduces the operational burden of appointment management for small-to-medium service businesses without requiring each business to commission a custom AI system.

---

## Product Scope

### A. Implemented

| Capability | Description |
|---|---|
| Customer web chat | Browser-based chat UI at /chat and /chat/<clinic_id> |
| WhatsApp inbound messaging | Receives patient messages via Meta Cloud API webhook |
| WhatsApp outbound messaging | Sends AI-generated replies back to patients via Meta API |
| AI receptionist | LLM-powered agent handling common patient queries |
| Doctor listing | Lists all doctors with specializations and schedules |
| Service listing | Lists services offered per-doctor with pricing |
| Clinic information | Answers questions about address, hours, policies |
| Real-time availability | check_availability tool queries actual doctor schedules |
| Appointment booking | End-to-end booking with customer name, phone, doctor, service, slot |
| Appointment cancellation | Cancels CONFIRMED appointments |
| Appointment rescheduling | Changes date/time/doctor/service |
| Appointment status lookup | get_appointment_details checks live database records |
| Customer record management | update_customer_details corrects name/phone without rebooking |
| Human handoff | Transfers conversation to staff; AI stops responding |
| Staff reply | Admin can send messages directly into any conversation |
| AI release | Admin releases conversation back to AI from admin panel |
| Business admin dashboard | Appointments, conversations, reminders, doctors, services |
| Platform master console | SaaS owner manages all tenants, subscriptions, onboarding |
| Subscription lifecycle | Trial to Pending to Approved to Active to Expired states |
| Clinic invitation flow | Platform owner sends email invite; clinic admin sets own password |
| Password reset | Secure email-based token reset with 1-hour expiry |
| Voice STT web | Browser records audio, Groq Whisper / Gemini transcription |
| Voice TTS web | AI text reply to audio via Groq PlayAI / Gemini TTS |
| Voice STT WhatsApp | Voice notes downloaded and transcribed before agent processing |
| Multi-tenant isolation | All data scoped to business_id; enforced at DB and service layer |
| Per-doctor schedules | Each doctor has per-day availability, break times, slot intervals |
| Doctor leave management | DoctorLeave model tracks date-specific unavailability |
| Double-booking prevention | Unique partial index on business_id, doctor_id, date, time, CONFIRMED |
| Idempotency keys | Prevents duplicate bookings on message retries |
| WhatsApp deduplication | Stores Meta wamid to detect and skip duplicate webhook deliveries |
| Proactive reminder records | Reminder records created 24h before each appointment at booking |
| HMAC-SHA256 webhook verification | Validates Meta webhook signatures |
| Request-scoped caching | RequestCache caches business info, doctors, services per request |

### B. Partially Implemented

| Capability | What Is Done | What Is Missing |
|---|---|---|
| Reminder delivery | Records created in DB | No background job scheduler to dispatch reminders |
| Email service | SMTP integration complete | Not triggered for all events such as booking confirmation |
| WhatsApp onboarding | Per-tenant ClinicWhatsAppAccount model exists | No UI wizard; platform owner must insert records manually |
| Admin UI refresh | Dashboard displays data | No real-time push; requires manual page refresh |

### C. Planned (FUTURE - Not Implemented)

| Capability | Notes |
|---|---|
| Reminder dispatch | Needs Celery, APScheduler, or similar background job runner |
| Payment billing integration | Stripe or similar for automated subscription billing |
| Meta Embedded Signup | Automated WhatsApp Business number setup for new tenants |
| WebSocket real-time updates | Live conversation updates in admin UI |
| Analytics and reporting | Appointment trends, volume, AI vs human resolution rates |
| Knowledge base FAQ | Structured FAQ management beyond system prompt |
| Additional channels | Instagram DM, SMS, Telegram - architecture is channel-agnostic |
| Production WSGI | Gunicorn and Nginx configuration |
| Docker containerization | Dockerfile and docker-compose |
| CI CD pipeline | GitHub Actions or similar |

### D. Out of Scope (Current Phase)

| Capability | Reason |
|---|---|
| Electronic Medical Records (EMR) | Beyond receptionist scope |
| Telemedicine video calls | Separate product category |
| Insurance processing | Complex regulatory domain |
| Custom LLM fine-tuning | Uses pre-trained foundation models |

---

## Design Constraints

1. No direct database access by the LLM - The language model never executes SQL.
2. Business data is authoritative - The database is the single source of truth.
3. Tenant isolation is mandatory - Every database query is scoped to business_id.
4. Deterministic booking - Slot availability is calculated by deterministic Python code, not by the LLM.

---
