# AI Business Agent SaaS (Phase 1 Prototype)

Autonomous AI Business Agent platform prototype tailored for a **Dental Clinic (SmileCare Dental Clinic)**.

---

## 🌟 Architecture Overview

```text
Customer (Web Chat) ────┐
WhatsApp (Future)  ─────┤
Instagram (Future) ─────┤
                        ↓
                 SAME AI AGENT (LLM Client & Dynamic Prompts)
                        ↓
                 CONTROLLED TOOLS & DISPATCHER
                        ↓
                 BUSINESS SERVICES (Booking, Reminder, Handoff)
                        ↓
                 DATABASE & TRANSACTION PROTECTION
```

### Core Architecture Principles
1. **Separation of Concerns:** The LLM never executes raw SQL or modifies critical database records directly. It requests controlled tools that pass through a validated backend dispatcher.
2. **Provider Abstraction:** Unified `LLMClient` supporting `Gemini`, `Groq`, and `Mock` providers with canonical tool translations.
3. **Database-Level Conflict Prevention:** Atomic booking transactions and database uniqueness constraints prevent double booking and race conditions.
4. **Automated Reminders:** Proactive reminder records automatically scheduled 24 hours prior to confirmed appointments.
5. **Bidirectional Human Handoff:** Seamless takeover for clinic staff from the admin dashboard with automated AI pause and release capabilities.
6. **Multi-Tenant Foundation:** All database entities enforce tenant isolation with `business_id` from day one.
