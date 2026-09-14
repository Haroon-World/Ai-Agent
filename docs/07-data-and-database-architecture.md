# 07 — Data and Database Architecture

## Overview

The database is the single source of truth for all business data. The LLM has no direct database access. All data access flows through controlled service functions.

Default: SQLite (instance/ai_business_agent.db) in development. PostgreSQL in production (planned).

---

## Entity Relationships

```
businesses
    |
    +-- users (clinic admin accounts)
    +-- doctors
    |       +-- doctor_schedules (per-day hours)
    |       +-- doctor_leaves (date-specific unavailability)
    |       +-- services (treatments with pricing)
    +-- customers
    |       +-- appointments
    |               +-- reminders
    +-- conversations
    |       +-- messages
    +-- subscription_requests
    +-- whatsapp_accounts (ClinicWhatsAppAccount)
    +-- clinic_invitations
```

---

## Models

### Business (businesses table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| name | String(150) | Clinic name |
| business_type | String(100) | e.g. dental_clinic, polyclinic |
| address | String(255) | |
| phone | String(50) | |
| email | String(120) | Optional |
| timezone | String(50) | e.g. Asia/Karachi |
| opening_hours | Text | Human-readable hours string |
| policies | Text | Clinic policies |
| consultation_fee | Float | Default consultation fee |
| subscription_status | String(30) | trial | active | expired | cancelled |
| trial_ends_at | DateTime | Auto-set to created_at + 30 days |
| subscription_expires_at | DateTime | |
| active_plan_name | String(100) | |

Properties: is_subscription_valid, days_remaining, subscription_badge, effective_expiry_date

### User (users table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id (NULL for platform admins) |
| username | String(80) | UNIQUE globally |
| email | String(120) | UNIQUE globally, used for password reset |
| password_hash | String(256) | PBKDF2+SHA256 |
| is_platform_admin | Boolean | TRUE = SaaS platform owner account |
| reset_token | String(128) | Secure random token for password reset |
| reset_token_expires_at | DateTime | 1-hour expiry |

### Doctor (doctors table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id (tenant isolation) |
| name | String(150) | |
| specialization | String(150) | |
| slot_interval | Integer | Minutes per appointment slot (default 30) |
| break_start_time | String(10) | Lunch/break start e.g. 13:00 |
| break_end_time | String(10) | Lunch/break end e.g. 14:00 |
| is_active | Boolean | |

### DoctorSchedule (doctor_schedules table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| doctor_id | Integer | FK doctors.id |
| day_of_week | String(20) | Monday..Sunday |
| is_available | Boolean | False = doctor closed this day |
| start_time | String(10) | 09:00 format |
| end_time | String(10) | 17:00 format |

One row per day per doctor. This is the authoritative schedule.

### DoctorLeave (doctor_leaves table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| doctor_id | Integer | FK doctors.id |
| leave_date | String(20) | YYYY-MM-DD |
| reason | String(255) | |

Date-specific exceptions. If a doctor has a leave record for a date, they are unavailable regardless of weekly schedule.

### Service (services table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id |
| doctor_id | Integer | FK doctors.id (per-doctor services) |
| name | String(150) | |
| description | Text | |
| duration | Integer | Minutes |
| price | Float | PKR |
| is_active | Boolean | |

Services are scoped to a specific doctor. A service does not exist without a doctor.

### Customer (customers table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id |
| name | String(150) | |
| phone | String(50) | Used as identifier for WhatsApp patients |

### Appointment (appointments table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id |
| customer_id | Integer | FK customers.id |
| doctor_id | Integer | FK doctors.id |
| service_id | Integer | FK services.id |
| appointment_date | String(20) | YYYY-MM-DD |
| appointment_time | String(10) | HH:MM |
| status | String(30) | CONFIRMED | CANCELLED | COMPLETED | PENDING |
| idempotency_key | String(100) | UNIQUE - prevents duplicate bookings on retries |

Double-booking prevention: A partial unique index ensures no two CONFIRMED appointments share (business_id, doctor_id, appointment_date, appointment_time). Enforced at database level, not just application code.

### Conversation (conversations table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id |
| customer_id | Integer | FK customers.id (may be NULL for web visitors) |
| visitor_id | String(100) | UUID for web visitors; wa_<phone> for WhatsApp |
| channel | String(50) | web_chat | whatsapp |
| status | String(30) | AI | HUMAN | CLOSED |
| workflow_state | String(50) | START | COLLECTING_INFO | CHECKING_AVAILABILITY | AWAITING_CONFIRMATION | BOOKED |
| awaiting_input | String(50) | Current input being collected: doctor_choice, service_choice, time_choice, confirmation, name, phone |
| selected_service_id | Integer | In-progress booking context |
| selected_doctor_id | Integer | In-progress booking context |
| requested_date | String(20) | In-progress booking context |
| requested_time | String(10) | In-progress booking context |
| pending_customer_name | String(100) | Not-yet-confirmed patient name |
| pending_customer_phone | String(50) | Not-yet-confirmed patient phone |
| handoff_reason | Text | Set when status = HUMAN |

### Message (messages table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| conversation_id | Integer | FK conversations.id |
| role | String(20) | user | assistant | system | tool |
| content | Text | |
| tool_name | String(100) | Set for role=tool messages |
| tool_call_id | String(100) | Echo of LLM tool_call_id for Groq protocol |
| input_mode | String(20) | text | voice |
| external_message_id | String(255) | UNIQUE - Meta wamid for deduplication |

### Reminder (reminders table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id |
| appointment_id | Integer | FK appointments.id |
| scheduled_for | DateTime | appointment_datetime minus 24 hours |
| status | String(30) | SCHEDULED | SENT | CANCELLED |
| reminder_type | String(50) | 24H_BEFORE |

NOTE: Records are created at booking time. Actual dispatch requires a background job scheduler - NOT YET IMPLEMENTED.

### SubscriptionRequest (subscription_requests table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id |
| plan_name | String(50) | 1_month | 3_months | 6_months | 1_year | custom |
| duration_days | Integer | |
| status | String(20) | pending | approved | rejected | cancelled |
| notes | Text | Platform admin review notes |
| reviewed_at | DateTime | |
| reviewed_by | String(80) | Platform admin username |

### ClinicWhatsAppAccount (whatsapp_accounts table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id - UNIQUE (one WA account per clinic) |
| phone_number_id | String(64) | UNIQUE - Meta Graph API Phone Number ID |
| waba_id | String(64) | WhatsApp Business Account ID |
| display_phone_number | String(32) | e.g. +923001234567 |
| access_token | String(512) | Meta system user access token |
| webhook_verify_token | String(128) | Token for Meta webhook verification |
| app_secret | String(128) | For HMAC-SHA256 signature verification |
| is_active | Boolean | |

### ClinicInvitation (clinic_invitations table)

| Field | Type | Notes |
|---|---|---|
| id | Integer | Primary key |
| business_id | Integer | FK businesses.id |
| email | String(120) | Invitee email |
| token | String(64) | UNIQUE - cryptographically secure random token |
| expires_at | DateTime | Default 7 days from creation |
| is_used | Boolean | Marked True once admin completes setup |

---

## SQLite Performance Configuration

Applied via SQLAlchemy engine event listeners:

- PRAGMA journal_mode=WAL - Write-Ahead Logging for concurrent readers
- PRAGMA busy_timeout=30000 - Wait up to 30s before giving up on locked DB
- PRAGMA synchronous=NORMAL - Balance between durability and performance

---
