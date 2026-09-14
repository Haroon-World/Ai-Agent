# 09 — Multi-Tenant Architecture

## Overview

ClinicConnect AI is a multi-tenant SaaS platform. Every database record belongs to exactly one business (tenant), identified by a business_id integer foreign key. There is no shared customer data, shared conversation history, or shared appointment records between tenants.

---

## Tenant Identity

Each tenant is a row in the businesses table:

```
businesses table:
  id=1  name="Arfa Polyclinic"      (Tenant A)
  id=2  name="SmileCare Dental"     (Tenant B)
  id=3  name="HealthPlus Lab"       (Tenant C)
```

All other tables carry a business_id foreign key that ties them to exactly one tenant.

---

## Tenant Isolation in the Database

Every major entity is scoped to a tenant:

```
businesses (id=1)              businesses (id=2)
    |
    +-- doctors (business_id=1)     doctors (business_id=2)
    +-- services (business_id=1)    services (business_id=2)
    +-- customers (business_id=1)   customers (business_id=2)
    +-- appointments (business_id=1) appointments (business_id=2)
    +-- conversations (business_id=1) conversations (business_id=2)
    +-- messages (via conv)         messages (via conv)
```

No query in the application fetches records without a business_id filter.

---

## How Tenant Context Is Determined

### Web Chat Channel

```python
def _resolve_chat_business_id(clinic_id=None):
    # Priority order:
    # 1. Explicit route argument: /chat/<clinic_id>
    # 2. Query parameter: ?clinic=N or ?business_id=N
    # 3. JSON body: { business_id: N }
    # 4. Logged-in clinic admin session: session.get(business_id)
    # 5. Fallback: Config.DEFAULT_BUSINESS_ID
```

### WhatsApp Channel

```python
# Meta sends phone_number_id in each webhook payload
wa_account = ClinicWhatsAppAccount.query.filter_by(
    phone_number_id=phone_number_id,
    is_active=True
).first()
business_id = wa_account.business_id
```

Each clinic registers their WhatsApp phone number in ClinicWhatsAppAccount with a unique phone_number_id. Meta includes this in every webhook, so tenant lookup is automatic.

---

## Tenant Isolation in Service Layer

Every service function accepts and enforces business_id:

```python
# BookingService - always filters by business_id
BookingService.check_availability(business_id=3, doctor_id=7, date=...)

# HandoffService - validates conversation belongs to business
HandoffService.trigger_handoff(conversation_id=99, business_id=3)
  # Checks: conv.business_id == business_id
  # Returns 403 if mismatch

# ToolDispatcher - initialized with business_id from verified request
ToolDispatcher(business_id=3, conversation_id=99)
```

---

## Concrete Example: Two Clinics

```
Clinic A: business_id = 1
  Doctor: Dr. Sara (doctor.business_id = 1)
  Patient: Ali (customer.business_id = 1)
  Conversation: conv.business_id = 1

Clinic B: business_id = 2
  Doctor: Dr. Ahmed (doctor.business_id = 2)
  Patient: Sara (customer.business_id = 2)
  Conversation: conv.business_id = 2
```

When Ali from Clinic A sends a message:
  1. business_id=1 is resolved from his web session
  2. Agent(business_id=1) is created
  3. System prompt is built from Clinic A doctors and services only
  4. All tool calls are scoped: BookingService.check_availability(business_id=1, ...)
  5. Ali cannot see, interact with, or affect any Clinic B data

---

## Admin Authentication and Tenant Binding

```
User.business_id = 1    -> Clinic A admin
User.business_id = 2    -> Clinic B admin
User.business_id = NULL, is_platform_admin = True -> Platform owner
```

When a clinic admin logs in:
  - session[business_id] = user.business_id
  - All admin route queries use session[business_id]
  - _current_business_id() helper enforces this

The login page explicitly blocks platform admin accounts:
  if matched_user.is_platform_admin: flash(Unauthorized) and return

The platform login page explicitly blocks clinic admin accounts:
  if not is_platform_admin: flash(Unauthorized) and return

---

## Remaining Tenant Isolation Risks

| Risk | Status | Notes |
|---|---|---|
| IDOR on conversation history | Mitigated | visitor_id checked for web chat; business_id checked for admin |
| IDOR via appointment_id in tools | Mitigated | BookingService verifies business_id on every appointment access |
| Cross-tenant tool execution | Mitigated | ToolDispatcher initialized with verified business_id from route |
| WhatsApp message to wrong tenant | Mitigated | phone_number_id is unique per tenant; fallback to DEFAULT_BUSINESS_ID |
| Session fixation | Partial | Sessions tied to user_id; no cross-tenant session sharing |
| Platform admin impersonating clinic | By design | Platform admin can view tenant info but cannot access patient data |
| Production credential exposure | Needs hardening | Default ADMIN_PASSWORD=admin123 must be changed before production |

---
