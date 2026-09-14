# 20 — Development Guide

## Prerequisites

- Python 3.10 or higher
- pip
- Git
- (Optional) Google AI Studio account for Gemini API key
- (Optional) Groq account for Groq API key
- (Optional) Meta Developer account for WhatsApp testing
- (Optional) ngrok or cloudflared for WhatsApp webhook development

---

## Initial Setup

```bash
# Clone the repository
git clone <repo-url>
cd AI-Agent

# Install dependencies
pip install -r requirements.txt

# Create environment file
copy .env.example .env
# Then edit .env with your settings

# Start the development server
python app.py
```

On Windows, you can use run.bat for a one-click start that also opens the ngrok tunnel.

---

## Project Structure

```
AI Agent/
+-- app.py                    Flask application factory
+-- requirements.txt          Python dependencies
+-- .env.example              Environment variable template
+-- seed.py                   Demo data seeder
+-- run.bat                   Windows one-click startup
+-- cloudflared.exe           Cloudflare tunnel binary
|
+-- config/
|   +-- config.py             Configuration class
|
+-- models/
|   +-- __init__.py           DB init, auto-migrate, model imports
|   +-- business.py           Business/Tenant model
|   +-- user.py               User (admin/platform) model
|   +-- doctor.py             Doctor model
|   +-- doctor_schedule.py    Per-day schedule model
|   +-- doctor_leave.py       Doctor leave/absence model
|   +-- service.py            Service/treatment model
|   +-- customer.py           Patient/customer model
|   +-- appointment.py        Appointment model
|   +-- conversation.py       Conversation (thread) model
|   +-- message.py            Individual message model
|   +-- reminder.py           Appointment reminder model
|   +-- subscription_request.py Subscription request model
|   +-- whatsapp_account.py   ClinicWhatsAppAccount model
|   +-- clinic_invitation.py  Clinic onboarding invitation
|
+-- ai/
|   +-- agent.py              AI Agent orchestration (1665 lines)
|   +-- llm_client.py         LLM provider abstraction (3238 lines)
|   +-- prompts.py            System prompt builder
|   +-- tools.py              CANONICAL_TOOLS + ToolDispatcher
|   +-- response_generator.py Post-processing and ui_action
|   +-- speech_client.py      STT and TTS adapters
|
+-- routes/
|   +-- chat.py               Web chat API routes
|   +-- whatsapp.py           WhatsApp webhook routes
|   +-- admin.py              Clinic admin portal routes
|   +-- platform.py           Platform master console routes
|   +-- appointments.py       Appointment API routes
|
+-- services/
|   +-- booking_service.py    Core booking logic (1211 lines)
|   +-- handoff_service.py    Human handoff lifecycle
|   +-- reminder_service.py   Reminder record creation
|   +-- whatsapp_service.py   Meta API client
|   +-- subscription_service.py Subscription lifecycle
|   +-- email_service.py      SMTP email delivery
|
+-- templates/
|   +-- chat.html             Customer chat UI
|   +-- admin/                Clinic admin templates
|   +-- platform/             Platform console templates
|       +-- login.html
|       +-- dashboard.html
|       +-- onboard.html
|
+-- static/
|   +-- css/                  Stylesheets
|   +-- js/
|       +-- chat.js           Customer chat JavaScript
|       +-- admin.js          Admin dashboard JavaScript
|
+-- tests/
|   +-- (48 test files)
|
+-- docs/
    +-- (this documentation)
```

---

## Running Tests

```bash
# Run all tests (no API keys needed - uses mock provider)
python -m unittest discover tests

# Run a specific test file
python -m unittest tests.test_multitenant_auth

# Run with verbose output
python -m unittest discover tests -v
```

---

## Adding a New Business Type

The platform is generic. To add support for a new business type:

1. Log in to the admin portal as the clinic admin
2. Add doctors with appropriate specializations
3. Add services per doctor with appropriate names, descriptions, duration, price
4. Update the clinic policies and opening hours

No code changes are required. The AI agent reads all this from the database at runtime.

---

## Adding a New AI Tool

1. Define the tool in ai/tools.py following the CANONICAL_TOOLS format
2. Add the tool handler function in ToolDispatcher.execute()
3. Implement the business logic in the appropriate service class
4. The tool is automatically available to all LLM providers via the adapter

---

## Adding a New Channel

The AI agent is channel-agnostic. To add a new channel (e.g. Telegram):

1. Create a new blueprint in routes/telegram.py
2. Implement webhook handling specific to Telegram
3. Resolve business_id from Telegram bot routing
4. Call Agent.process_message() with the text
5. Send reply back via Telegram API
6. Register the blueprint in app.py

The Agent class does not know or care what channel the message came from.

---

## Common Development Tasks

### Reset the database

```bash
# Delete the database file
del instance\ai_business_agent.db
# Restart the server - it will create fresh DB and seed data
python app.py
```

### View seed data

Check seed.py to see what demo businesses, doctors, and services are created at startup.

### Test WhatsApp locally

```bash
# Start server
python app.py

# In another terminal, start tunnel
# Using cloudflared:
.\cloudflared.exe tunnel --url http://127.0.0.1:5001

# Or using run.bat (starts both server and ngrok tunnel)
run.bat

# Set webhook URL in Meta Developer Portal:
# https://<tunnel-url>/api/whatsapp/webhook
```

---
