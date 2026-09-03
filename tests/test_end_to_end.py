import unittest
import os
import sys
import json
from datetime import datetime, timezone, timedelta, date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Appointment, Conversation, Message, Reminder
from services.booking_service import BookingService
from services.handoff_service import HandoffService
from ai.tools import ToolDispatcher
from ai.agent import Agent
from seed import seed_database

class TestEndToEndSuite(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Explicitly seed the in-memory database
        seed_database(self.app)
        from models import DoctorSchedule
        scheds = DoctorSchedule.query.filter_by(doctor_id=1).all()
        for s in scheds:
            s.is_available = True
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_booking_service_and_double_booking_prevention(self):
        """Test booking transaction, reminders, and double booking rejection."""
        from tests.test_date_helpers import get_next_open_weekday
        biz_id = Config.DEFAULT_BUSINESS_ID
        tomorrow = get_next_open_weekday(biz_id)

        # Check availability
        avail = BookingService.check_availability(business_id=biz_id, doctor_id=1, date_str=tomorrow)
        self.assertIn("results", avail)
        self.assertTrue(len(avail["results"][0]["available_slots"]) > 0)
        chosen_slot = avail["results"][0]["available_slots"][0]

        # First booking -> should succeed
        res1 = BookingService.book_appointment(
            business_id=biz_id,
            customer_name="Ali Hassan",
            customer_phone="+923001234567",
            doctor_id=1,
            service_id=1,
            appointment_date=tomorrow,
            appointment_time=chosen_slot,
            notes="First visit"
        )
        self.assertTrue(res1["success"])
        appt_id = res1["appointment_id"]

        # Verify reminder was scheduled automatically
        reminders = Reminder.query.filter_by(appointment_id=appt_id).all()
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0].status, "SCHEDULED")

        # Second booking at same slot -> must fail gracefully without crash
        res2 = BookingService.book_appointment(
            business_id=biz_id,
            customer_name="Sara Khan",
            customer_phone="+923009876543",
            doctor_id=1,
            service_id=1,
            appointment_date=tomorrow,
            appointment_time=chosen_slot
        )
        self.assertFalse(res2["success"])
        error_lower = res2["error"].lower()
        self.assertTrue(
            "overlap" in error_lower or "booked" in error_lower or "already" in error_lower,
            f"Expected overlap/conflict error message, got: {res2['error']}"
        )

    def test_idempotency_duplicate_protection(self):
        """Test that identical idempotency_key returns existing appointment."""
        biz_id = Config.DEFAULT_BUSINESS_ID
        # Use next Monday — Dr. Ahmed works Mon-Sat, Sunday is the only day off
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7  # Monday = 0
        if days_ahead == 0:
            days_ahead = 7
        target_date = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        key = "req-unique-id-998877"
        res1 = BookingService.book_appointment(
            business_id=biz_id,
            customer_name="Zubair",
            customer_phone="+923005555555",
            doctor_id=1,
            service_id=1,
            appointment_date=target_date,
            appointment_time="11:00",
            idempotency_key=key
        )
        self.assertTrue(res1["success"])

        # Repeat identical request
        res2 = BookingService.book_appointment(
            business_id=biz_id,
            customer_name="Zubair",
            customer_phone="+923005555555",
            doctor_id=1,
            service_id=1,
            appointment_date=target_date,
            appointment_time="11:00",
            idempotency_key=key
        )
        self.assertTrue(res2["success"])
        self.assertTrue(res2.get("is_duplicate_request"))
        self.assertEqual(res1["appointment_id"], res2["appointment"]["id"])

    def test_reschedule_and_cancellation(self):
        """Test appointment rescheduling and cancellation workflows."""
        from tests.test_date_helpers import get_next_open_weekday
        biz_id = Config.DEFAULT_BUSINESS_ID
        target_date = get_next_open_weekday(biz_id, doctor_id=2)

        book_res = BookingService.book_appointment(
            business_id=biz_id,
            customer_name="Hamza",
            customer_phone="+923004444444",
            doctor_id=2, # Dr. Sara
            service_id=3, # Whitening
            appointment_date=target_date,
            appointment_time="14:00"
        )
        self.assertTrue(book_res["success"])
        appt_id = book_res["appointment_id"]

        # Reschedule
        new_date = get_next_open_weekday(biz_id, from_date=target_date, doctor_id=2)
        reschedule_res = BookingService.reschedule_appointment(
            business_id=biz_id,
            appointment_id=appt_id,
            new_date=new_date,
            new_time="15:00"
        )
        self.assertTrue(reschedule_res["success"])
        self.assertEqual(reschedule_res["appointment"]["appointment_date"], new_date)

        # Cancel
        cancel_res = BookingService.cancel_appointment(
            business_id=biz_id,
            appointment_id=appt_id,
            reason="Patient requested"
        )
        self.assertTrue(cancel_res["success"])
        appt = db.session.get(Appointment, appt_id)
        self.assertEqual(appt.status, "CANCELLED")

    def test_agent_workflow_and_tool_dispatcher(self):
        """Test full Agent conversation loop with tool dispatching."""
        from tests.test_date_helpers import get_next_open_weekday
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        # 1. Start conversation
        conv = Conversation(business_id=biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # 2. Inquire services
        resp1 = agent.process_message(conv.id, "What dental services do you provide?")
        self.assertEqual(resp1["status"], "AI")
        self.assertIn("dental services", resp1["content"].lower())

        # 3. Book cleaning
        tomorrow = get_next_open_weekday(biz_id)
        resp2 = agent.process_message(conv.id, f"Please book a cleaning for Tariq at 10:00 on {tomorrow}, phone 03001234567")
        self.assertTrue(any(t["name"] == "book_appointment" for t in resp2.get("executed_tools", [])))
        self.assertIn("confirmed", resp2["content"].lower())

        # Verify structured state persisted in conversation record
        conv_updated = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_updated.workflow_state, "BOOKED")
        self.assertEqual(conv_updated.intent, "BOOK_APPOINTMENT")

    def test_human_handoff_and_release_lifecycle(self):
        """Test bidirectional human handoff (AI -> HUMAN -> Staff reply -> Release -> AI)."""
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Customer triggers handoff
        resp = agent.process_message(conv.id, "I want to speak with a human receptionist right now.")
        self.assertEqual(resp["status"], "HUMAN")

        # Verify conversation status in DB
        conv_check = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_check.status, "HUMAN")

        # Subsequent customer message when in HUMAN mode must NOT be auto-answered by AI
        subsequent_resp = agent.process_message(conv.id, "Hello? Anyone there?")
        self.assertEqual(subsequent_resp["status"], "HUMAN")
        self.assertEqual(len(subsequent_resp.get("executed_tools", [])), 0)
        self.assertIn("human staff", subsequent_resp["content"].lower())

        # Customer message must still be saved to the database
        saved_customer_msg = Message.query.filter_by(conversation_id=conv.id, content="Hello? Anyone there?").first()
        self.assertIsNotNone(saved_customer_msg, "Customer message sent during HUMAN status must be persisted in DB")
        self.assertEqual(saved_customer_msg.role, "user")

        # Staff replies
        staff_reply = HandoffService.admin_reply(conv.id, "Hello! I am Dr. Ahmed's assistant. How can I help you?")
        self.assertTrue(staff_reply["success"])

        # Admin releases conversation back to AI
        release_res = HandoffService.release_to_ai(conv.id)
        self.assertTrue(release_res["success"])
        self.assertEqual(release_res["status"], "AI")

        # Subsequent customer message now handled by AI again
        resume_resp = agent.process_message(conv.id, "Where is your clinic located?")
        self.assertEqual(resume_resp["status"], "AI")
        self.assertIn("smilecare", resume_resp["content"].lower())

    def test_customer_message_persisted_during_active_human_handoff(self):
        """
        Regression test: When conversation status is HUMAN, customer messages
        must be persisted as Message(role='user') in the database and update conv.updated_at
        so staff can see them in admin conversations view.
        """
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Trigger handoff
        agent.process_message(conv.id, "I want to talk to a human receptionist")
        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.status, "HUMAN")

        # Step 2: Admin replies once
        staff_res = HandoffService.admin_reply(conv.id, "Hello! I am here to help. What is your issue?")
        self.assertTrue(staff_res["success"])

        old_updated_at = conv_db.updated_at

        # Step 3: Customer sends a second message while in HUMAN mode
        msg_text = "My tooth is aching very badly since yesterday."
        resp = agent.process_message(conv.id, msg_text)
        self.assertEqual(resp["status"], "HUMAN")

        # Step 4: Verify message exists in database with role='user'
        saved_msg = Message.query.filter_by(conversation_id=conv.id, content=msg_text).first()
        self.assertIsNotNone(saved_msg, "Customer message sent during HUMAN status must be saved to DB")
        self.assertEqual(saved_msg.role, "user")
        self.assertEqual(saved_msg.conversation_id, conv.id)

        # Step 5: Verify conv.updated_at was updated
        db.session.refresh(conv_db)
        self.assertGreaterEqual(conv_db.updated_at, old_updated_at)

    def test_admin_auth_and_protected_routes(self):
        """Test admin authentication session protection."""
        # Unauthenticated access should redirect to login
        res = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login", res.headers["Location"])

        # Authenticate
        login_res = self.client.post("/admin/login", data={"username": "admin", "password": "admin123"}, follow_redirects=True)
        self.assertEqual(login_res.status_code, 200)

        # Access dashboard
        dash_res = self.client.get("/admin")
        self.assertEqual(dash_res.status_code, 200)
        self.assertIn(b"Clinic Operations Dashboard", dash_res.data)

if __name__ == "__main__":
    unittest.main()
