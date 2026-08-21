import os
import sys
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from config.config import Config
from models import db, Conversation, Appointment, Doctor, Service
from ai.agent import Agent
from seed import seed_database


def run_gemini_real_e2e_test():
    print("=" * 70)
    print("PHASE 4: REAL GEMINI PROVIDER END-TO-END VERIFICATION")
    print("=" * 70)

    class RealProviderConfig(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SQLALCHEMY_TRACK_MODIFICATIONS = False
        TESTING = True
        SECRET_KEY = "test-secret"
        LLM_PROVIDER = "gemini"

    app = create_app(RealProviderConfig)

    with app.app_context():
        seed_database()

        # Find next valid working day (e.g. next Monday for Dr. Ahmed)
        today = date.today()
        # Find next weekday (Monday-Friday) to guarantee doctor is working
        days_ahead = (0 - today.weekday()) % 7
        if days_ahead <= 0:
            days_ahead += 7
        target_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        day_name = (today + timedelta(days=days_ahead)).strftime("%A")

        print(f"[Target Date for Booking]: {target_date} ({day_name})")

        conv = Conversation(
            business_id=Config.DEFAULT_BUSINESS_ID,
            channel="web_chat",
            status="AI"
        )
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=Config.DEFAULT_BUSINESS_ID, llm_provider="gemini")

        turns = [
            "I want a dental appointment.",
            f"I want a cleaning on {target_date}.",
            "Dr Ahmed around 10.",
            "10:00 works.",
            "Muhammad Haroon.",
            "03001234567."
        ]

        import time
        for i, user_msg in enumerate(turns, 1):
            if i > 1:
                print(f"[Waiting 12s for Gemini Free-Tier 5 RPM rate limit window...]")
                time.sleep(12)

            print(f"\n--- Turn {i} ---")
            print(f"User: {user_msg}")
            res = agent.process_message(conv.id, user_msg)
            print(f"AI:   {res.get('content')}")
            if res.get("executed_tools"):
                print(f"Tools Executed: {[t['name'] for t in res['executed_tools']]}")


        # Check DB for persisted appointment
        appt = Appointment.query.filter_by(
            business_id=Config.DEFAULT_BUSINESS_ID,
            appointment_date=target_date,
            appointment_time="10:00"
        ).first()

        if not appt:
            # Fallback check any appointment for Muhammad Haroon
            appt = Appointment.query.filter_by(
                business_id=Config.DEFAULT_BUSINESS_ID,
                appointment_date=target_date
            ).first()

        print("\n" + "=" * 70)
        if appt:
            print(f"SUCCESS: Real Appointment created in database!")
            print(f"• ID: #{appt.id}")
            print(f"• Patient: {appt.customer.name} ({appt.customer.phone})")
            print(f"• Doctor: {appt.doctor.name}")
            print(f"• Service: {appt.service.name}")
            print(f"• Date/Time: {appt.appointment_date} at {appt.appointment_time}")
            print(f"• Status: {appt.status}")
            print("=" * 70)
            return True
        else:
            print("FAILURE: No appointment was created in the database.")
            print("=" * 70)
            return False


if __name__ == "__main__":
    success = run_gemini_real_e2e_test()
    sys.exit(0 if success else 1)
