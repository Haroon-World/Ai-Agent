import unittest
from datetime import date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Conversation, Doctor, Service, Appointment, Customer
from ai.agent import Agent
from seed import seed_database

class TestHybridUIAndStateHardening(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_single_turn_multi_parameter_extraction(self):
        """
        User provides Doctor, Service, Date, and Time in a single Roman Urdu turn.
        System must extract all 4, NOT ask for doctor/service/date/time, and ask ONLY for customer full name.
        """
        from tests.test_date_helpers import patch_open_date

        conv = Conversation(business_id=1, status="AI", intent="BOOK_APPOINTMENT", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Multi-parameter input
        msg = "Mujhe Dr Sara ke sath kal cleaning ki appointment chahiye, 10 baje."
        with patch_open_date(1, doctor_id=2) as expected_date:
            res = agent.process_message(conv.id, msg)

        # Verify state parameters resolved from DB
        db.session.refresh(conv)
        self.assertEqual(conv.selected_doctor_id, 2)  # Dr. Sara Malik
        self.assertEqual(conv.selected_service_id, 2) # Dental Cleaning & Scaling
        self.assertEqual(conv.requested_time, "10:00")
        self.assertEqual(conv.requested_date, expected_date)

    def test_multi_turn_flow_with_name_and_phone(self):
        """
        Verify complete multi-turn flow through name and phone to appointment creation.
        """
        from tests.test_date_helpers import get_next_open_weekday
        conv = Conversation(business_id=1, status="AI", intent="BOOK_APPOINTMENT", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: Specify booking details on working day
        target_date = get_next_open_weekday(1, doctor_id=2)
        msg1 = f"Mujhe Dr Sara ke sath {target_date} ko cleaning ki appointment chahiye, 10 baje."
        r1 = agent.process_message(conv.id, msg1)
        self.assertEqual(r1["status"], "AI")

        # Turn 2: Provide Name
        msg2 = "Muhammad Haroon"
        r2 = agent.process_message(conv.id, msg2)
        db.session.refresh(conv)
        self.assertEqual(conv.pending_customer_name, "Muhammad Haroon")

        # Turn 3: Provide Phone
        msg3 = "03001234567"
        r3 = agent.process_message(conv.id, msg3)
        db.session.refresh(conv)
        self.assertEqual(conv.pending_customer_phone, "03001234567")

        # Verify appointment created in DB
        cust = Customer.query.filter_by(business_id=1, phone="03001234567").first()
        self.assertIsNotNone(cust)
        appt = Appointment.query.filter_by(business_id=1, customer_id=cust.id).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.doctor_id, 2)
        self.assertEqual(appt.service_id, 2)
        self.assertEqual(appt.appointment_time, "10:00")
        self.assertEqual(appt.status, "CONFIRMED")

    def test_ui_action_generation(self):
        """
        Verify that Agent attaches dynamic ui_action payloads for doctor and service selection.
        """
        conv = Conversation(business_id=1, status="AI", intent="BOOK_APPOINTMENT", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "I want to book an appointment.")

        self.assertIn("ui_action", res)
        ui_act = res["ui_action"]
        self.assertIsNotNone(ui_act)
        self.assertIn("type", ui_act)
        self.assertIn(ui_act["type"], ["doctor_selection", "service_selection", "date_selection"])
        self.assertGreater(len(ui_act["options"]), 0)

    def test_admin_dashboard_redirect_unauthenticated(self):
        """
        Verify that accessing /admin without authentication redirects to /admin/login.
        """
        client = self.app.test_client()
        response = client.get("/admin", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login", response.location)

if __name__ == "__main__":
    unittest.main()
