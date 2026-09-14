# 05 — System Architecture

## High-Level Architecture

```
Communication Channels
  Browser / Web Chat          WhatsApp (Meta Cloud API)
  /chat  /chat/<clinic_id>    /api/whatsapp/webhook
          |                          |
          v                          v
         Flask Backend
    chat_bp (routes/chat.py)   whatsapp_bp (routes/whatsapp.py)
    - /api/chat/send           - Webhook verification
    - /api/chat/send-voice     - HMAC-SHA256 signature check
    - /api/chat/synthesize     - Tenant resolution
    - /api/chat/init           - Message deduplication
          |
          v
    Tenant Identification
    Web Chat: clinic_id from URL, query param, or session
    WhatsApp: phone_number_id -> ClinicWhatsAppAccount -> business_id
          |
          v
    Conversation / Customer Context
    - Look up or create Customer record
    - Look up or create Conversation record
    - Load message history from database
    - Check conversation status (AI / HUMAN / CLOSED)
          |
          v
    AI Agent (ai/agent.py)
    1. Build dynamic system prompt (ai/prompts.py)
    2. Assemble conversation history
    3. Call LLMClient (ai/llm_client.py)
    4. Parse tool call decisions
    5. Execute tools via ToolDispatcher (ai/tools.py)
    6. Feed tool results back to LLM
    7. Generate final natural language response
    8. Persist messages to database
    9. Update conversation state
          |
          v
    Tool Dispatcher (ai/tools.py)
    get_clinic_info      -> BookingService
    get_doctors          -> BookingService
    get_services         -> BookingService
    check_availability   -> BookingService
    book_appointment     -> BookingService
    get_appointment_details -> BookingService
    cancel_appointment   -> BookingService
    reschedule_appointment -> BookingService
    update_customer_details -> BookingService
    human_handoff        -> HandoffService
          |
          v
    Business Services (services/)
    BookingService     - slot calculation, DB writes
    HandoffService     - conversation takeover lifecycle
    ReminderService    - reminder record creation
    WhatsAppService    - Meta API HTTP calls
    SubscriptionService - plan lifecycle management
    EmailService       - SMTP dispatch or dev mock
          |
          v
    Database
    SQLite (dev) / PostgreSQL (prod)
    Business, Doctor, Service, DoctorSchedule, DoctorLeave
    Customer, Appointment, Conversation, Message, Reminder
    User, SubscriptionRequest, ClinicWhatsAppAccount, ClinicInvitation
          |
          v
    Response
    Web Chat: JSON -> chat.js renders message
    WhatsApp: WhatsAppService.send_text_message()
```

---

## Blueprint Structure

| Blueprint | URL Prefix | Purpose |
|---|---|---|
| chat_bp | root | Customer chat API and voice endpoints |
| appointments_bp | root | Appointment-related API endpoints |
| admin_bp | /admin | Clinic admin portal |
| platform_bp | /platform | Platform owner master console |
| whatsapp_bp | /api/whatsapp | WhatsApp Cloud API webhook and test-send |

---

## Key Routes

| Route | Method | Purpose |
|---|---|---|
| / | GET | Landing page |
| /chat, /chat/<clinic_id> | GET | Customer chat UI |
| /api/chat/init | POST | Initialize chat session |
| /api/chat/send | POST | Send text message through AI agent |
| /api/chat/send-voice | POST | Upload audio, transcribe, process through agent |
| /api/chat/synthesize | POST | Convert text to audio (TTS) |
| /api/chat/history/<id> | GET | Get conversation message history |
| /admin/login | GET/POST | Clinic admin login |
| /admin/dashboard | GET | Admin dashboard |
| /admin/conversations | GET | Conversation management |
| /admin/appointments | GET | Appointment management |
| /admin/doctors | GET/POST | Doctor management |
| /admin/services | GET/POST | Service management |
| /admin/reminders | GET | Reminder queue |
| /admin/subscription | GET | Subscription status |
| /platform/login | GET/POST | Platform owner login |
| /platform/dashboard | GET | Master console |
| /api/whatsapp/webhook | GET | Meta webhook verification challenge |
| /api/whatsapp/webhook | POST | Meta inbound message processing |
| /api/whatsapp/test-send | POST | Admin-scoped test message dispatch |

---

## Separation of Concerns

| Layer | Responsibility |
|---|---|
| LLM | Language understanding, intent reasoning, response generation |
| Agent | Orchestration - calls LLM, interprets tool decisions, persists state |
| Tools | Canonical action definitions - delegates to services |
| Services | Business logic, validation, DB writes |
| Database | Source of truth |
| Routes | HTTP boundary - session, auth, channel-specific setup |

---
