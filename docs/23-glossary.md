# 23 — Glossary

This glossary defines terms used throughout the documentation. Terms are listed alphabetically.

---

| Term | Definition |
|---|---|
| Agent | The AI orchestration layer in ai/agent.py. Manages the LLM loop, tool calls, and conversation state. Not to be confused with the LLM itself. |
| AI Mode | Conversation status=AI, meaning the AI agent is actively responding to patient messages. Opposite of HUMAN mode. |
| auto_migrate_db | A custom startup function that adds missing columns to database tables without a formal migration tool. |
| awaiting_input | A Conversation field recording what information the agent is currently waiting to collect from the patient (e.g. doctor_choice, name, phone). |
| BookingService | The primary service class in services/booking_service.py. Contains all slot calculation, availability checking, and appointment CRUD logic. |
| business_id | Integer foreign key on all tenant-scoped database tables. The primary tenant identifier throughout the system. |
| CANONICAL_TOOLS | The authoritative list of 10 tool definitions in ai/tools.py. Used as the single source of truth; translated to provider-specific formats by LLMClient adapters. |
| channel | The communication medium a conversation uses. Currently: web_chat or whatsapp. |
| ClinicConnect AI | The name of this SaaS platform. |
| ClinicInvitation | Database model for secure, time-limited onboarding invitation emails sent to clinic admins. |
| ClinicWhatsAppAccount | Database model mapping a Meta phone_number_id to a business_id. Enables multi-tenant WhatsApp routing. |
| Cloud API | Meta WhatsApp Cloud API - the official Meta API for programmatic WhatsApp messaging. Also called Graph API. |
| Conversation | A persistent thread of messages between a patient and the AI (or human staff). One per patient per channel session. |
| DEFAULT_BUSINESS_ID | Config fallback tenant ID used when tenant cannot be resolved from URL or WhatsApp routing. |
| DoctorLeave | Database model for date-specific doctor unavailability (holidays, sick days). Overrides the weekly schedule. |
| DoctorSchedule | Database model for per-day-of-week working hours for a doctor. The authoritative schedule source. |
| double-booking prevention | A partial unique database index on (business_id, doctor_id, date, time) WHERE status=CONFIRMED that prevents two CONFIRMED appointments at the same slot. |
| external_message_id | The Meta wamid stored on Message records to detect and skip duplicate webhook deliveries. |
| Graph API | Meta's primary REST API. WhatsApp Cloud API is part of the Graph API family. |
| Groq | A company providing ultra-fast LLM inference via LPU hardware. Also provides Whisper STT and PlayAI TTS APIs. |
| HandoffService | Service class managing conversation control transfer between AI and human staff. |
| HMAC-SHA256 | Hash-based Message Authentication Code using SHA-256. Used to verify that WhatsApp webhook payloads genuinely came from Meta. |
| human handoff | The event when conversation.status changes from AI to HUMAN. The AI stops responding; a human staff member takes over. |
| HUMAN Mode | Conversation status=HUMAN, meaning a human staff member has taken over the conversation. AI does not respond. |
| idempotency_key | A unique UUID stored on Appointment records to prevent duplicate bookings if the same booking request is submitted multiple times. |
| intent | A Conversation field classifying what the patient is trying to do (BOOK_APPOINTMENT, GET_CLINIC_INFO, etc.). |
| LLMClient | The provider abstraction class in ai/llm_client.py. Translates CANONICAL_TOOLS to provider-specific formats and handles API calls. |
| LLM | Large Language Model. The pre-trained neural network that handles language understanding and generation (Gemini or Groq-hosted model). |
| Multilingual | Support for English, Urdu script, and Roman Urdu within the same agent and conversation. |
| phone_number_id | A Meta-assigned unique identifier for a WhatsApp Business phone number. Used as the multi-tenant routing key for WhatsApp. |
| Platform Admin | A User with is_platform_admin=True. Manages the SaaS platform - creates tenants, approves subscriptions. |
| Platform Console | The separate admin interface at /platform for the platform owner. |
| prompt grounding | Technique of injecting live database content into the LLM system prompt at runtime to make a generic model behave as if it knows specific business information. No model training required. |
| RequestCache | A utility class using Flask g for request-scoped caching of frequently queried data (business info, doctors, services). |
| Roman Urdu | Urdu language written using Latin (English) characters. Common in Pakistani text messaging (e.g. kal=tomorrow, subah=morning, do baje=2 oclock). |
| seed.py | A script that creates demo business, doctor, service, and user records in the database on startup. |
| slot_interval | Minutes between available appointment slots for a doctor (e.g. 30 = appointments at :00, :30, etc.). |
| STTClient | The Speech-to-Text factory class in ai/speech_client.py. Selects the STT adapter (Groq Whisper, Gemini, or Mock). |
| subscription_status | The Business field tracking plan state: trial, active, expired, or cancelled. |
| SubscriptionRequest | Database model for subscription renewal requests submitted by clinic admins and awaiting platform owner approval. |
| tenant | A business registered on the ClinicConnect AI platform. Each tenant is one row in the businesses table. |
| ToolDispatcher | The controlled execution gateway in ai/tools.py. All LLM tool call requests pass through it before reaching service functions. |
| TTSClient | The Text-to-Speech factory class in ai/speech_client.py. Selects the TTS adapter (Gemini, Groq, or Mock). |
| ui_action | A structured JSON object returned alongside an AI reply to tell the frontend to render an interactive widget (slot picker, doctor selector, etc.). |
| visitor_id | A UUID stored in the browser session cookie identifying an anonymous web chat visitor. Used for conversation ownership verification. |
| wamid | WhatsApp message ID - the unique identifier Meta assigns to every WhatsApp message. Stored as external_message_id for deduplication. |
| WABA | WhatsApp Business Account. The Meta entity that owns WhatsApp Business phone numbers. |
| WAL mode | Write-Ahead Logging - a SQLite journal mode that allows concurrent reads while writes are in progress. |
| workflow_state | A Conversation field tracking the booking workflow stage: START, COLLECTING_INFO, CHECKING_AVAILABILITY, AWAITING_CONFIRMATION, or BOOKED. |

---
