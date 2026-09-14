# 14 — Security and Tenant Isolation

## Overview

This document describes the security controls that have been implemented, identifies areas that need hardening before production, and lists known risks.

---

## Authentication

### Password Storage
Status: IMPLEMENTED
- Werkzeug PBKDF2+SHA256 with salt via generate_password_hash / check_password_hash
- Plain text passwords are never stored or logged

### Session Management
Status: IMPLEMENTED
- Flask signed cookie sessions
- Session timeout: 60 minutes (configurable via SESSION_TIMEOUT_MINUTES)
- Session permanently set for platform admins

### Password Reset
Status: IMPLEMENTED
- secrets.token_urlsafe(32) - 256-bit cryptographically secure random token
- 1-hour expiry enforced
- Token cleared after use via clear_reset_token()

### Login Portal Separation
Status: IMPLEMENTED
- Clinic admin login (/admin/login) blocks platform admin accounts
- Platform login (/platform/login) blocks clinic admin accounts
- Prevents privilege escalation via wrong portal

---

## Authorization

### login_required Decorator
Status: IMPLEMENTED
- Applied to all admin routes
- Checks: session[user_id] exists and session[business_id] is set
- Enforces subscription validity (redirects to expired page if lapsed)

### platform_admin_required Decorator
Status: IMPLEMENTED
- Checks session[is_platform_admin] and verifies against DB
- Returns 403 for authenticated but non-platform-admin users

### Tenant Isolation at Service Layer
Status: IMPLEMENTED
- Every service function receives and validates business_id
- HandoffService verifies: conv.business_id == business_id before any operation
- BookingService verifies: appointment.business_id == business_id on every access
- Returns 403 JSON error on mismatch

### IDOR Prevention in Web Chat
Status: IMPLEMENTED
- Conversation access requires matching visitor_id (for anonymous users)
- Admins can only access conversations belonging to session[business_id]
- get_or_create_conversation() enforces visitor_id ownership

---

## WhatsApp Security

### HMAC-SHA256 Webhook Signature Verification
Status: IMPLEMENTED (fail-open in dev)
- Meta includes X-Hub-Signature-256 header on every webhook POST
- Signature verified using WHATSAPP_APP_SECRET before any processing
- Constant-time comparison via hmac.compare_digest to prevent timing attacks
- WARNING: If WHATSAPP_APP_SECRET is not set, verification is bypassed with a warning log. This MUST be fixed before production.

### Message Deduplication
Status: IMPLEMENTED
- Meta wamid stored in Message.external_message_id (UNIQUE index)
- Duplicate delivery detected before any processing
- Returns 200 immediately to prevent Meta retries

---

## Invitation Security

### Clinic Onboarding Invitations
Status: IMPLEMENTED
- ClinicInvitation uses secrets.token_urlsafe(32) - 256-bit token
- Default 7-day expiry
- is_valid() checks: not used, not expired
- mark_used() called immediately after successful setup

---

## Input Handling

### SQL Injection
Status: PROTECTED
- All database queries go through SQLAlchemy ORM
- No raw SQL string construction in application code
- Parameterized queries used throughout

### LLM Output Safety
Status: PARTIALLY IMPLEMENTED
- LLM cannot directly execute database operations
- Tool calls are gated through controlled ToolDispatcher
- Tool arguments are type-coerced and validated before service calls
- LLM cannot invent new tools not defined in CANONICAL_TOOLS

---

## Known Security Risks and Hardening Needed

| Risk | Severity | Status | Notes |
|---|---|---|---|
| Default ADMIN_PASSWORD=admin123 | HIGH | NEEDS FIXING | Must be changed before production |
| Default PLATFORM_ADMIN_PASSWORD in config | HIGH | NEEDS FIXING | Must be changed before production |
| SECRET_KEY default value in config | HIGH | NEEDS FIXING | Use strong random key in production |
| WHATSAPP_APP_SECRET not enforced in dev | MEDIUM | By design for dev | Must be set before production |
| No CSRF protection on forms | MEDIUM | NOT IMPLEMENTED | Flask does not include CSRF by default |
| No rate limiting on login endpoints | MEDIUM | NOT IMPLEMENTED | Brute force possible |
| No rate limiting on chat API | MEDIUM | NOT IMPLEMENTED | Could be abused for LLM cost attacks |
| Tokens stored unencrypted in ClinicWhatsAppAccount.access_token | MEDIUM | NOT IMPLEMENTED | Should encrypt at rest |
| Session cookie not configured with Secure/HttpOnly/SameSite | MEDIUM | NOT IMPLEMENTED | Should be set in production config |
| No request logging or audit trail | LOW | NOT IMPLEMENTED | No production observability |
| Auto-migration runs ALTER TABLE in production | LOW | ACCEPTABLE | Additive only; low risk |

---

## Recommendations Before Production

1. Change all default credentials (ADMIN_PASSWORD, PLATFORM_ADMIN_PASSWORD, SECRET_KEY)
2. Set WHATSAPP_APP_SECRET to enforce webhook signature verification
3. Enable CSRF protection (Flask-WTF or similar)
4. Add rate limiting (Flask-Limiter)
5. Configure session cookie security flags
6. Encrypt WhatsApp access tokens at rest
7. Set up request logging and alerting
8. Run behind HTTPS (required by Meta for webhooks)

---
