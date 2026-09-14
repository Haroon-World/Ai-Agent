# 13 — Admin and Platform Architecture

## Two Separate Admin Systems

ClinicConnect AI has two completely separate administrative portals:

| Portal | URL | Who | Scope |
|---|---|---|---|
| Clinic Admin Portal | /admin | Clinic staff | Their own clinic only |
| Platform Master Console | /platform | SaaS platform owner | All tenants |

These portals have separate login pages, separate session handling, and separate access controls. A clinic admin cannot access the platform console, and a platform admin cannot log in through the clinic portal.

---

## Clinic Admin Portal (/admin)

### Authentication

- URL: /admin/login
- Credentials: username/email + password
- Stored as: User record with business_id (non-null) and is_platform_admin=False
- Session: Sets session[user_id] and session[business_id]
- Platform admin accounts are explicitly rejected at this login

### Access Control

```python
@login_required  # Checks session[user_id] and session[business_id]
def dashboard():
    # All queries scoped to session[business_id]
```

Subscription enforcement: If the clinic subscription is expired, the admin is redirected to /admin/subscription-expired and cannot access other pages.

### Dashboard Features

| Page | URL | Content |
|---|---|---|
| Dashboard | /admin/dashboard | Today's appointments, stats, recent conversations |
| Appointments | /admin/appointments | Full appointment list with status management |
| Conversations | /admin/conversations | Live AI conversations with handoff controls |
| Doctors | /admin/doctors | Doctor management, schedules, services |
| Services | /admin/services | Per-doctor service management |
| Slots | /admin/slots | Schedule/slot configuration |
| Reminders | /admin/reminders | Scheduled reminder queue |
| Subscription | /admin/subscription | Current plan, renewal requests |

### Human Handoff Controls

From the Conversations page, admins can:
- Click Take Over to set conversation.status = HUMAN
- Send direct messages to WhatsApp or web chat patients
- Click Release to set conversation.status = AI (resumes AI)

---

## Platform Master Console (/platform)

### Authentication

- URL: /platform/login
- Credentials: username + password
- Stored as: User record with business_id=NULL and is_platform_admin=True
- Session: Sets session[platform_admin_id] and session[is_platform_admin]
- Clinic admin accounts are explicitly rejected at this login

### Access Control

```python
@platform_admin_required  # Checks is_platform_admin in session and DB
def dashboard():
    # Can view all businesses
    businesses = Business.query.all()
```

### Platform Dashboard Features

| Feature | Description |
|---|---|
| Tenant list | All registered businesses with subscription badges |
| Create tenant | Add a new Business record |
| Edit tenant | Update business details |
| Send invitation | Email invitation link for clinic admin account setup |
| Subscription management | Approve/reject renewal requests, set custom dates |
| Cancel tenant | Set subscription_status=cancelled |
| Reactivate tenant | Restore a cancelled subscription |
| WhatsApp configuration | Link a WhatsApp phone number to a tenant |
| Onboarding panel | templates/platform/onboard.html |

### Tenant Onboarding Flow

```
Platform owner creates Business record in DB
        |
        v
Platform owner sends ClinicInvitation email to clinic admin's email
  POST /platform/clinic/<id>/invite
    -> ClinicInvitation.create_invitation(business_id, email)
    -> EmailService.send_email(token_link)
        |
        v
Clinic admin receives email with link:
  https://<domain>/admin/setup/<token>
        |
        v
/admin/setup/<token>  GET: shows setup form
        |
        v
Clinic admin submits: username + password
  POST /admin/setup/<token>
    -> Validates token: not expired, not used
    -> Creates User(business_id, username, password_hash)
    -> Marks invitation as used
    -> Redirects to /admin/login
        |
        v
Clinic admin can now log in and manage their clinic
```

---

## Subscription Lifecycle

```
Business created
        |
        v
trial (30 days) <- subscription_status=trial, trial_ends_at=created_at+30d
        |
        v (trial expires)
clinic admin submits SubscriptionRequest (plan: 1_month, 3_months, 6_months, 1_year)
        |
        v
pending <- status=pending in subscription_requests
        |
        +-- Platform owner approves:
        |   subscription_status=active
        |   subscription_expires_at=now+duration_days
        |   active_plan_name=plan_display_name
        |
        +-- Platform owner rejects:
            status=rejected, notes=reason

active subscription expires:
  is_subscription_valid returns False
  Clinic admin redirected to /admin/subscription-expired

Platform owner can also cancel:
  subscription_status=cancelled -> clinic loses access immediately
```

Note: There is NO automated payment processing. All subscription approvals are done manually by the platform owner through the master console.

---
