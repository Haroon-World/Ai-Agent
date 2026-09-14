# 17 — Testing and Quality

This document outlines the testing strategy, test suite organization, coverage domains, and verification procedures for ClinicConnect AI.

---

## Testing Framework & Philosophy

- **Framework:** Standard Python `unittest` framework combined with Flask's built-in `test_client()`.
- **Zero External Service Dependencies:** The entire test suite runs offline without requiring API keys for Google Gemini, Groq, or Meta WhatsApp.
- **Hermetic Test Isolation:** Each test case initializes a fresh, in-memory SQLite database instance (`sqlite:///:memory:`), executes schema migration pragmas, creates mock seed data, and tears down the environment upon completion.
- **Deterministic Mocking:** Uses `MockLLMClient`, `MockSTTAdapter`, and `MockTTSAdapter` to test agent orchestration, conversation state transitions, and tool dispatching deterministically across all days of the week.

---

## Test Directory Structure & Inventory

The test suite consists of **48 automated test files** located in `tests/`:

```
tests/
├── test_end_to_end.py
├── test_real_ai_pipeline.py
├── test_real_provider_e2e.py
├── test_intent_routing_and_state_isolation.py
├── test_intent_routing_regression.py
├── test_off_topic_queries.py
├── test_eye_check_inquiry.py
├── test_transcript_scenario.py
├── test_controlled_pricing_and_workflow.py
├── test_availability_and_state_scenarios.py
├── test_strict_state_aware_workflow.py
├── test_forward_workflow_regression.py
├── test_state_stability_and_forward_workflow.py
├── test_reappointment_consultation_flow.py
├── test_reschedule_doctor_service_change.py
├── test_appointment_status_and_ui_guard.py
├── test_doctor_id_state_bug_regression.py
├── test_subscription_and_sunday_slots.py
├── test_doctor_schedules.py
├── test_date_picker_schedule_filtering.py
├── test_weekly_schedule_availability.py
├── test_weekly_schedule_and_language_parity.py
├── test_date_helpers.py
├── test_authoritative_slot_and_voice_pipeline.py
├── test_time_selection_ui_regression.py
├── test_multitenant_auth.py
├── test_production_hardening_and_security.py
├── test_saas_hardening_and_multi_doctor_guard.py
├── test_enterprise_subscription_and_doctor_guard.py
├── test_whatsapp_webhook.py
├── test_whatsapp_interaction_architecture.py
├── test_voice_route.py
├── test_roman_english_multilingual_flow.py
├── test_user_exact_urdu_flow.py
├── test_informal_name_phone_extraction.py
├── test_polyclinic_flow_and_admin_fixes.py
├── test_polyclinic_per_doctor_services.py
├── test_polyclinic_specialization_discovery.py
├── test_clinic_invitation_and_setup.py
├── test_latency_safety.py
├── test_frontend_scroll_behavior.py
├── test_hybrid_ui_and_state_hardening.py
├── test_phase1_fixes.py
├── test_phase1a.py
├── test_master_workflow_stability_audit.py
└── test_patient_name_inquiry_protection.py
```

---

## Detailed Coverage Domains

### 1. Multi-Tenant Isolation & Authentication
- **Files:** `test_multitenant_auth.py`, `test_production_hardening_and_security.py`, `test_enterprise_subscription_and_doctor_guard.py`
- **Key Assertions:**
  - Clinic Admin A authenticated to `business_id=1` is rejected with 403 or redirect when querying appointments, doctors, or conversations of `business_id=2`.
  - Platform master administrator cannot log into clinic admin portals and vice-versa.
  - Subscription validity checks intercept un-subscribed or expired tenants and redirect to the subscription barrier page.
  - IDOR protection verifies that anonymous web visitors cannot read conversations without matching `visitor_id`.

### 2. Double-Booking & Scheduling Invariants
- **Files:** `test_availability_and_state_scenarios.py`, `test_subscription_and_sunday_slots.py`, `test_weekly_schedule_availability.py`
- **Key Assertions:**
  - Simultaneous booking attempts for the same doctor, date, and slot trigger database partial unique index constraints (`uq_confirmed_doctor_appointment_slot`), raising an `IntegrityError` that gracefully resolves to a user-friendly retry message.
  - Doctor leaves and break times (e.g., lunch 13:00–14:00) cleanly excise slots from generated availability windows.
  - Same-day slot generation filters out time intervals that have already passed in the clinic's local timezone.

### 3. State Machine & Booking Workflow Progression
- **Files:** `test_strict_state_aware_workflow.py`, `test_forward_workflow_regression.py`, `test_controlled_pricing_and_workflow.py`
- **Key Assertions:**
  - Enforces linear progression: `START` -> `COLLECTING_INFO` -> `CHECKING_AVAILABILITY` -> `AWAITING_CONFIRMATION` -> `BOOKED`.
  - Prohibits regression loops where an already-selected doctor or date is re-prompted due to minor conversational filler.
  - Verifies that `update_customer_details` successfully updates patient metadata without resetting or corrupting in-flight booking state.

### 4. Meta WhatsApp Webhook & Deduplication
- **Files:** `test_whatsapp_webhook.py`, `test_whatsapp_interaction_architecture.py`
- **Key Assertions:**
  - Webhook verification GET requests respond with the exact `hub.challenge` token when given valid `hub.verify_token`.
  - POST requests verify HMAC-SHA256 signatures in `X-Hub-Signature-256`.
  - Inbound duplicate messages carrying an already processed `wamid` are immediately acknowledged with HTTP 200 without re-invoking the AI agent.
  - Tenant resolution dynamically routes incoming messages to the correct `business_id` based on Meta's `phone_number_id`.

### 5. Multilingual & Script Parity
- **Files:** `test_roman_english_multilingual_flow.py`, `test_user_exact_urdu_flow.py`, `test_weekly_schedule_and_language_parity.py`
- **Key Assertions:**
  - Validates that queries submitted in pure Urdu script (e.g. `مجھے ڈاکٹر احمد سے ملنا ہے`) receive responses in Urdu script.
  - Confirms that Roman Urdu queries (e.g. `kal subah ka slot milega?`) accurately resolve relative dates (`kal` -> tomorrow) and extract intent.
  - Verifies Urdu number token parsing (`do baje`, `teen`, etc.) for schedule mapping.

---

## How to Run Tests

### Running the Entire Suite
```bash
python -m unittest discover tests
```

### Running Specific Test Modules
```bash
# Multi-tenant security tests
python -m unittest tests.test_multitenant_auth

# WhatsApp webhook tests
python -m unittest tests.test_whatsapp_webhook

# Booking workflow tests
python -m unittest tests.test_strict_state_aware_workflow
```

### Verbose Mode with Timing
```bash
python -m unittest discover tests -v
```

---

## Current Testing Gaps & Limitations

| Testing Area | Status | Mitigation / Recommended Improvement |
|---|---|---|
| **Live Provider E2E** | Manual / Opt-in | `test_real_ai_pipeline.py` requires live API keys; needs scheduled nightly CI job with secrets. |
| **PostgreSQL Compatibility** | Not Automated | Tests run against SQLite; need a GitHub Actions matrix testing against PostgreSQL 15. |
| **Browser E2E / UI Tests** | Not Automated | No Playwright/Selenium suite verifying CSS, MediaRecorder audio capture, or mobile touch controls. |
| **Load & Concurrency Testing** | Not Automated | No Locust or k6 performance tests measuring high-volume concurrent webhook bursts. |
| **CI/CD Integration** | Missing | No GitHub Actions workflow file (`.github/workflows/test.yml`) committing tests to pull-request gating. |

---

*Next: [18-deployment-and-environments.md](./18-deployment-and-environments.md)*
