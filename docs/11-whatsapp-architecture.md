# 11 — WhatsApp Architecture

## Overview

ClinicConnect AI integrates with WhatsApp via the Meta WhatsApp Cloud API (Graph API). This is the official, policy-compliant integration method. The system supports receiving messages, transcribing voice notes, processing them through the AI agent, and sending replies back to patients.

---

## Meta WhatsApp Terminology

| Term | Meaning |
|---|---|
| WhatsApp Business Account (WABA) | The Meta business entity that owns WhatsApp phone numbers |
| Phone Number ID | Meta-assigned unique identifier for a specific WhatsApp phone number |
| Webhook | An HTTP endpoint that Meta calls when messages arrive |
| Access Token | OAuth token authorizing API calls on behalf of the WABA |
| wamid | Meta-issued unique ID for each WhatsApp message |

---

## Architecture

```
Patient's WhatsApp
        |
        v
Meta Cloud API
        |
        v
POST /api/whatsapp/webhook
        |
        v
routes/whatsapp.py
  1. HMAC-SHA256 signature verification
  2. Deduplication check (wamid)
  3. phone_number_id lookup in whatsapp_accounts table
        |
        v
  ClinicWhatsAppAccount.phone_number_id -> business_id
        |
        v
  AI Agent processing (same agent as web chat)
        |
        v
  WhatsAppService.send_text_message(reply, to_phone)
        |
        v
Meta Cloud API
        |
        v
Patient's WhatsApp
```

---

## Multi-Tenant WhatsApp Routing

Each clinic that uses WhatsApp has exactly one ClinicWhatsAppAccount record:

```
Clinic A: phone_number_id = 111, business_id = 1
Clinic B: phone_number_id = 222, business_id = 2
```

When Meta sends a webhook:
- The payload contains: entry[0].changes[0].value.metadata.phone_number_id
- The system queries ClinicWhatsAppAccount by this phone_number_id
- The matched business_id scopes all downstream processing

If no matching account is found: falls back to Config.DEFAULT_BUSINESS_ID.

---

## Webhook Verification (GET)

Meta sends a GET request to verify the webhook URL before allowing webhook subscriptions:

```
GET /api/whatsapp/webhook
  ?hub.mode=subscribe
  &hub.verify_token=<token>
  &hub.challenge=<random_string>
```

The server checks the verify_token against:
1. Config.WHATSAPP_WEBHOOK_VERIFY_TOKEN (global)
2. Any per-clinic webhook_verify_token in ClinicWhatsAppAccount

If it matches, returns the challenge string as plain text with HTTP 200.

---

## Inbound Message Security (POST)

Every inbound webhook POST is verified before any processing:

```python
X-Hub-Signature-256: sha256=<hmac>

Verification:
expected = sha256_hmac(WHATSAPP_APP_SECRET, raw_body)
if not hmac.compare_digest(expected, signature_header):
    return 403  # Reject
```

DEVELOPMENT NOTE: If WHATSAPP_APP_SECRET is not set, signature verification is bypassed with a warning. This must be configured before production deployment.

---

## Message Deduplication

Meta may deliver the same message multiple times (retry on timeout, network issues). Deduplication prevents processing the same message twice:

```python
# Each inbound message has a unique wamid
meta_message_id = parsed.get(message_id)

# Check if already processed
existing = Message.query.filter_by(external_message_id=meta_message_id).first()
if existing:
    return 200  # Already handled - skip

# After processing, stamp the wamid onto the stored Message record
user_msg.external_message_id = meta_message_id
```

---

## Voice Note Handling

WhatsApp voice notes arrive as audio messages:

```
Webhook payload: { msg_type: audio, media_id: XYZ, mime_type: audio/ogg; codecs=opus }
        |
        v
WhatsAppService.download_media(media_id, access_token)
  -> GET https://graph.facebook.com/v19.0/<media_id>
        |
        v
STTClient.transcribe(audio_bytes, mime_type=audio/ogg; codecs=opus)
        |
        v
Transcribed text processed through AI agent
        |
        v
Text reply sent back to patient
```

---

## Outbound Messaging

All AI replies are sent as text messages via Meta Cloud API:

```python
POST https://graph.facebook.com/v19.0/{phone_number_id}/messages
{
  messaging_product: whatsapp,
  recipient_type: individual,
  to: 923001234567,
  type: text,
  text: { body: AI_reply_text }
}
```

Template messages are also supported (for initiating conversations outside the 24-hour window):

```python
WhatsAppService.send_template(
    to_phone, template_name=hello_world, language_code=en_US
)
```

---

## Human Staff Reply via WhatsApp

When an admin sends a reply through the admin dashboard to a WhatsApp conversation:

```python
HandoffService.admin_reply(conversation_id, message)
  -> Stores message in DB
  -> If conv.channel == whatsapp:
     WhatsAppService.send_text_message(
         to_phone=patient_phone,
         text=message,
         phone_number_id=clinic_wa_phone_number_id
     )
```

This means staff can reply to WhatsApp patients directly from the admin panel.

---

## Current Implementation Status

| Feature | Status | Notes |
|---|---|---|
| Inbound text messages | IMPLEMENTED | Full |
| Inbound voice notes (STT) | IMPLEMENTED | Full |
| Outbound text messages | IMPLEMENTED | Full |
| Outbound template messages | IMPLEMENTED | Basic |
| Multi-tenant routing via phone_number_id | IMPLEMENTED | Full |
| HMAC-SHA256 signature verification | IMPLEMENTED | Full |
| Message deduplication | IMPLEMENTED | Full |
| Human staff reply via admin panel | IMPLEMENTED | Full |
| Per-clinic access tokens in DB | IMPLEMENTED | Full |
| WhatsApp TTS (voice replies) | NOT IMPLEMENTED | PLANNED |
| Automated tenant WhatsApp onboarding | NOT IMPLEMENTED | PLANNED (Meta Embedded Signup) |
| WhatsApp interactive messages (buttons) | NOT IMPLEMENTED | PLANNED |

---

## Development Setup

1. Set WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_ACCESS_TOKEN, WHATSAPP_WEBHOOK_VERIFY_TOKEN, WHATSAPP_APP_SECRET in .env
2. Run Flask server: python app.py or run.bat
3. Expose with ngrok or cloudflared to get a public HTTPS URL
4. In Meta Developer Portal, set webhook URL to: https://<public-url>/api/whatsapp/webhook
5. Subscribe to messages webhook event

---
