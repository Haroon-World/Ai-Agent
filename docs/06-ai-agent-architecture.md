# 06 — AI Agent Architecture

## Overview

The AI agent is the core intelligence of ClinicConnect AI. It orchestrates the entire flow from receiving a patient message to delivering a final response. The agent is implemented in ai/agent.py as the Agent class.

---

## Conceptual Model

```
LLM       = Reasoning / language layer
            (Gemini or Groq - understands language, decides what to do)

Agent     = Orchestration layer
            (runs the LLM loop, calls tools, persists state)

Tools     = Controlled actions the LLM can request
            (defined in ai/tools.py as CANONICAL_TOOLS)

Services  = Business logic
            (BookingService, HandoffService - deterministic Python)

Database  = Source of truth
            (real doctor schedules, confirmed appointments, actual prices)
```

The LLM never directly accesses the database. It can only request tools, and tools call validated service functions.

---

## How Business Knowledge Reaches the LLM (Prompting vs Training)

This project does NOT fine-tune or train a proprietary language model. It uses pre-trained foundation models (Gemini, Groq-hosted LLMs) and injects business-specific knowledge at runtime.

```
Database (Business, Doctor, Service)
        |
        v
build_system_prompt(business_id)   <- ai/prompts.py
        |
        | Injects:
        | - Clinic name, address, phone, hours, policies
        | - Current date and time (clinic timezone)
        | - All doctors with specializations and per-day schedules
        | - All services grouped by doctor with prices
        | - Behavioral rules and workflow instructions
        v
System Prompt (sent to LLM as first message)
        |
        v
LLM reasons with this context plus patient conversation history
        |
        v
Business-specific, accurate response
```

This is called prompt grounding or context injection. The LLM behavior is shaped by real data, not by training.

---

## Agent Processing Loop

For each incoming patient message, Agent.process_message() executes:

```
1. LOAD CONVERSATION STATE
   - Fetch Conversation record from DB
   - Check conversation.status
     - If HUMAN: skip AI processing entirely
     - If CLOSED: return early
     - If AI: continue

2. PRE-PROCESSING
   - Detect language (English / Urdu / Roman Urdu)
   - Extract date tokens (resolve_date_string)
   - Extract time tokens (_extract_time_token)
   - Extract doctor name mentions (_extract_doctor_mention)
   - Extract phone numbers (_extract_phone_number)
   - Update conversation state fields

3. BUILD PROMPT CONTEXT
   - build_system_prompt(business_id) -> system message
   - Load message history from DB -> user/assistant/tool messages

4. LLM CALL (ai/llm_client.py -> LLMClient)
   - Send: system prompt + message history + new user message
   - Include: CANONICAL_TOOLS definitions
   - Receive: LLM response (text or tool call requests)

5. TOOL EXECUTION LOOP
   - If LLM returns tool call request:
     a. Parse tool name and arguments
     b. Dispatch to ToolDispatcher.execute()
     c. ToolDispatcher calls BookingService or HandoffService
     d. Tool result returned as JSON
     e. Append tool result message to conversation
     f. Send updated context back to LLM
     g. Repeat until LLM returns text response

6. RESPONSE GENERATION (ai/response_generator.py)
   - Post-process raw LLM text
   - Generate ui_action hints (slot_picker, doctor_selection, etc.)

7. STATE PERSISTENCE
   - Persist user message to DB
   - Persist assistant message to DB
   - Update Conversation workflow_state, intent, selected_doctor_id,
     selected_service_id, requested_date, requested_time,
     pending_customer_name, pending_customer_phone

8. RETURN RESULT
   - content: AI reply text
   - status: conversation status
   - workflow_state: current state
   - executed_tools: list of tools called
   - ui_action: optional structured UI hint
   - metrics: timing information
```

---

## Tool Definitions (CANONICAL_TOOLS)

The 10 tools available to the LLM are defined in ai/tools.py:

| Tool Name | Purpose |
|---|---|
| get_clinic_info | Returns address, phone, hours, policies |
| get_doctors | Returns all doctors with specializations and schedules |
| get_services | Returns services for a specific doctor |
| check_availability | Returns open slots for date + optional doctor/service |
| book_appointment | Books a confirmed appointment |
| get_appointment_details | Looks up live appointment status |
| cancel_appointment | Cancels a CONFIRMED appointment |
| reschedule_appointment | Changes date/time/doctor/service |
| update_customer_details | Updates patient name/phone without touching appointments |
| human_handoff | Transfers conversation to human staff |

---

## LLM Provider Abstraction

The LLMClient in ai/llm_client.py provides a unified interface:

```
CANONICAL_TOOLS (provider-neutral definitions)
        |
        v
LLMClient.chat(messages, tools)
        |
        +-- Gemini adapter:
        |   Translates tools to genai FunctionDeclarations
        |   Sends via google.genai SDK
        |   Parses FunctionCall from response
        |
        +-- Groq adapter:
        |   Translates tools to OpenAI-protocol JSON schema
        |   Sends via groq SDK
        |   Parses tool_calls from response
        |
        +-- Mock adapter:
            Returns deterministic scripted responses
```

---

## Conversation State Machine

| workflow_state | Meaning |
|---|---|
| START | New conversation, no context collected |
| COLLECTING_INFO | Doctor / service / date being gathered |
| CHECKING_AVAILABILITY | Slots being retrieved or displayed |
| AWAITING_CONFIRMATION | Summary shown, awaiting patient confirmation |
| BOOKED | Appointment successfully created |

| status | Meaning |
|---|---|
| AI | AI agent is handling responses |
| HUMAN | Human staff has taken over |
| CLOSED | Conversation is closed |

---

## Multilingual Support

The agent supports three modes simultaneously:
- English
- Urdu script (native Urdu)
- Roman Urdu (Urdu written in Latin characters like kal, subah, do baje)

Language detection is performed by detect_language() in ai/response_generator.py. The LLM is instructed to mirror the patient language.

The agent also has a comprehensive Urdu/Roman Urdu number parser for time expressions (do baje = 2 oclock) and date expressions (kal = tomorrow, parso = day after tomorrow).

---
