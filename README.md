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

---

## 🚀 Quickstart Guide

### 1. Prerequisites
- Python 3.10+
- (Optional) Gemini API Key or Groq API Key

### 2. Setup Environment
```bash
# Clone the repository
git clone https://github.com/Haroon-World/Ai-Agent.git
cd Ai-Agent

# Install dependencies
pip install -r requirements.txt

# Create environment file
cp .env.example .env
```

### 3. Configure `.env`
Edit `.env` to choose your provider:
```ini
# Choose provider: 'mock', 'gemini', or 'groq'
LLM_PROVIDER=mock

# If using Gemini:
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash

# If using Groq:
GROQ_API_KEY=your_key_here
GROQ_MODEL=llama3-70b-8192

# Database & Admin Defaults
DATABASE_URL=sqlite:///ai_business_agent.db
DEFAULT_BUSINESS_ID=1
BUSINESS_TIMEZONE=Asia/Karachi
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
```

### 4. Run the Application
```bash
python app.py
```
Open your browser at:
- **Landing / Prototype Overview:** `http://127.0.0.1:5000/`
- **Customer Web Chat (WhatsApp style):** `http://127.0.0.1:5000/chat`
- **Clinic Admin Dashboard:** `http://127.0.0.1:5000/admin` *(Login: admin / admin123)*
- **Live Conversations & Human Takeover:** `http://127.0.0.1:5000/admin/conversations`
- **Appointments Management:** `http://127.0.0.1:5000/admin/appointments`
- **Scheduled Reminders Queue:** `http://127.0.0.1:5000/admin/reminders`

---

## 🧪 Running Automated Tests

Run the complete end-to-end test suite:
```bash
python -m unittest discover tests
```

---

## 📁 Project Structure

```text
├── app.py                      # Flask Application entry point & factory
├── requirements.txt            # Python dependencies
├── .env.example                # Configuration template
├── seed.py                     # Database seeder with sample clinic & doctors
│
├── config/
│   └── config.py               # Application configuration
│
├── models/
│   ├── business.py             # Tenant / Business entity
│   ├── doctor.py               # Doctors & specialist schedules
│   ├── service.py              # Dental treatments, duration, pricing
│   ├── customer.py             # Customer/Patient records
│   ├── appointment.py          # Booked appointments with status tracking
│   ├── conversation.py         # Chat sessions & handoff status (AI vs HUMAN)
│   ├── message.py              # Multi-role message history
│   └── reminder.py             # Scheduled proactive reminder logs
│
├── ai/
│   ├── agent.py                # Core Agent runner & tool orchestration loop
│   ├── prompts.py              # Dynamic clinic system prompt builder
│   ├── tools.py                # Controlled tool definitions & execution dispatcher
│   └── llm_client.py           # Unified client supporting Gemini, Groq, and Mock
│
├── services/
│   ├── booking_service.py      # Slot availability calculation & double-booking prevention
│   ├── reminder_service.py     # Automated reminder scheduling & status tracking
│   └── handoff_service.py      # Human takeover & release lifecycle management
│
├── routes/
│   ├── chat.py                 # Customer chat API & channel adapter endpoints
│   ├── appointments.py         # Appointment booking & calendar endpoints
│   └── admin.py                # Admin dashboard analytics & conversation management
│
├── templates/                  # Frontend HTML templates (chat, dashboard, etc.)
└── static/                     # CSS stylesheets and interactive JavaScript
```
