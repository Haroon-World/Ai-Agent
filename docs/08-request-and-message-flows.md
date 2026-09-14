# 08 — Request and Message Flows

## A. Normal Customer Question (Web Chat)

```
Customer types a question
    |
    v
chat.js -> POST /api/chat/send
  { message: ..., conversation_id: N, business_id: M }
    |
    v
routes/chat.py: send_message()
  1. Resolve business_id from session/params
  2. Verify conversation belongs to this visitor (IDOR prevention)
  3. Create Agent(business_id, llm_provider)
  4. Call agent.process_message(conversation_id, user_text)
    |
    v
ai/agent.py: process_message()
  1. Load conversation from DB
  2. Check status = AI (proceed)
  3. Build system prompt from DB
  4. Load message history
  5. Call LLMClient.chat(messages, tools)
    |
    v
LLM decides: call get_clinic_info tool
    |
    v
ToolDispatcher.execute(get_clinic_info, {})
  -> BookingService.get_clinic_info(business_id)
  -> Returns: { name, address, phone, opening_hours, ... }
    |
    v
LLM receives tool result -> generates natural language response
    |
    v
Agent persists user message, tool message, assistant message to DB
    |
    v
Route returns JSON: { reply: ..., status: AI, workflow_state: START, ... }
    |
    v
chat.js renders message bubble
```

---

## B. Availability Inquiry

```
Customer: What slots are available tomorrow with Dr. Ahmed?
    |
    v
Agent pre-processes:
  - resolve_date_string(tomorrow) -> 2026-09-14
  - _extract_doctor_mention(Dr. Ahmed) -> doctor_id=2
    |
    v
LLM calls check_availability: { date: 2026-09-14, doctor_id: 2 }
    |
    v
BookingService.check_availability(business_id, doctor_id=2, date=2026-09-14)
  1. Fetch Doctor; verify belongs to this business
  2. Look up DoctorSchedule for the day of week
  3. Check DoctorLeave for this date
  4. Get all CONFIRMED appointments for doctor + date
  5. Generate all slots from start_time to end_time by slot_interval
  6. Subtract break slots
  7. Subtract already-booked slots
  8. For today: filter out past slots (must be >= now + 5 min)
  9. Return available slots list
    |
    v
LLM formats response with bullet-point slots
    |
    v
Response generator produces ui_action: { type: slot_picker, slots: [...] }
  -> Frontend renders clickable slot chips
```

---

## C. Appointment Booking (Full Workflow)

```
Step 1 - Doctor selection:
  Customer: I want to see Dr. Sara
  Agent: confirms doctor, asks for service type

Step 2 - Service selection:
  Customer: tooth cleaning
  Agent: retrieves Dr. Sara services, confirms Dental Cleaning (45 mins) - PKR 3,000

Step 3 - Date:
  Customer: Friday
  Agent: resolves to YYYY-MM-DD for next Friday

Step 4 - Availability:
  Agent calls check_availability(date=friday, doctor_id=sara_id)
  Agent shows available slots

Step 5 - Time selection:
  Customer selects slot

Step 6 - Patient details:
  Agent asks: patient name and whether same or different phone number
  Customer: My name is Ali Hassan, same number

Step 7 - Booking:
  Agent calls book_appointment:
    customer_name=Ali Hassan
    customer_phone=<from conversation context>
    doctor_id=<sara_id>
    service_id=<cleaning_id>
    appointment_date=2026-09-18
    appointment_time=10:00
    idempotency_key=<uuid4>
        |
        v
  BookingService.book_appointment():
    1. Validate business_id matches doctor and service
    2. Find or create Customer record
    3. Check idempotency_key for duplicate
    4. Re-check slot availability (race condition protection)
    5. INSERT Appointment with status=CONFIRMED
    6. Handle IntegrityError (unique index violation = double-booking)
    7. Call ReminderService.schedule_reminder(appointment)
    8. Return { success: True, appointment_id: N }
        |
        v
  Agent updates conversation:
    workflow_state = BOOKED
  Agent confirms booking to patient
```

---

## D. Appointment Cancellation

```
Customer: I want to cancel appointment #42
    |
    v
LLM calls cancel_appointment({ appointment_id: 42 })
    |
    v
BookingService.cancel_appointment(business_id, appointment_id=42)
  1. Fetch appointment; verify business_id matches (tenant isolation)
  2. Verify status is CONFIRMED
  3. UPDATE status = CANCELLED
  4. Cancel associated SCHEDULED reminder records
  5. Return { success: True }
    |
    v
Agent confirms cancellation to patient
```

---

## E. Human Handoff

```
Customer: Can I speak to a real person?
    |
    v
LLM calls human_handoff({ reason: Customer requested human receptionist })
    |
    v
HandoffService.trigger_handoff(conversation_id, reason, business_id)
  1. Verify conversation belongs to business_id
  2. UPDATE conversation.status = HUMAN
  3. INSERT system Message: Human handoff initiated
    |
    v
Agent: Transferring you to our receptionist now.
    |
    v
Next patient message:
  Agent checks status = HUMAN -> returns without calling LLM
  (AI is paused for this conversation)
    |
    v
Admin dashboard shows conversation flagged as HUMAN
Admin sends reply via POST /admin/api/conversations/<id>/reply
```

---

## F. WhatsApp Inbound Message

```
Patient sends WhatsApp message to clinic number
    |
    v
Meta Cloud API sends POST to /api/whatsapp/webhook
    |
    v
routes/whatsapp.py: handle_webhook()
  1. Read raw_body
  2. HMAC-SHA256 signature verification with WHATSAPP_APP_SECRET
     -> Return 403 if mismatch
  3. WhatsAppService.parse_incoming_payload(data)
     -> Extract: phone_number_id, from_phone, patient_name, user_text, msg_type, media_id, message_id
  4. Deduplication: query Message.external_message_id = wamid
     -> If found: return 200 (duplicate, skip)
  5. Multi-tenant resolution:
     ClinicWhatsAppAccount.query.filter_by(phone_number_id=X, is_active=True).first()
     -> business_id from wa_account or Config.DEFAULT_BUSINESS_ID
  6. If msg_type == audio:
     -> Download media via WhatsAppService.download_media()
     -> STTClient.transcribe(audio_bytes)
  7. Customer lookup/creation by phone
  8. Conversation lookup/creation (channel=whatsapp)
  9. Agent.process_message(conversation_id, user_text)
 10. Stamp wamid onto user Message record
 11. WhatsAppService.send_text_message(reply, to_phone=from_phone)
 12. Return 200 to Meta (always, to prevent Meta retries)
```

---

## G. Multi-Tenant Request Routing

```
Web Chat:
  URL: /chat/3  or  ?clinic=3  or  session.business_id=3
      |
      v
  _resolve_chat_business_id() -> business_id = 3
  All queries scoped: .filter_by(business_id=3)

WhatsApp:
  Webhook payload: phone_number_id=1234567890
      |
      v
  ClinicWhatsAppAccount.query.filter_by(
      phone_number_id=1234567890, is_active=True
  ).first()
      |
      v
  wa_account.business_id = 3
  All queries scoped: .filter_by(business_id=3)
```

---
