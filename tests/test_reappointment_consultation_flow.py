import unittest
from app import create_app, db
from config.config import Config
from models import Business, Doctor, Service, Customer, Appointment, Conversation
from ai.agent import Agent
from seed import seed_database


class TestReappointmentConsultationFlow(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)

        # Add a 3rd doctor (NeuroSurgeon Dr Haroon) as in polyclinic setup
        self.dr_haroon = Doctor(
            business_id=1,
            name="Dr Haroon",
            specialization="NeuroSurgeon",
            working_days="Monday,Wednesday,Friday,Sunday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        db.session.add(self.dr_haroon)
        db.session.commit()

        self.agent = Agent(business_id=1, llm_provider="mock")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_reappointment_after_cancellation_does_not_default_to_ahmed(self):
        """
        Verifies that after an appointment cancellation, selecting 'I don't know / I need a consultation'
        does NOT default to Dr. Ahmed Khan or show date selection for Dr. Ahmed Khan.
        Instead, it must present doctor selection options for the customer to choose.
        """
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        cust = Customer(business_id=1, name="Usman", phone="03001234567")
        db.session.add(cust)
        db.session.commit()
        conv.customer_id = cust.id

        appt = Appointment(
            business_id=1,
            customer_id=cust.id,
            doctor_id=1,
            service_id=1,
            appointment_date="2026-09-10",
            appointment_time="10:00",
            status="CONFIRMED"
        )
        db.session.add(appt)
        db.session.commit()

        # Turn 1: cancel my appointment
        r1 = self.agent.process_message(conv.id, "cancel my appointment")
        self.assertIn("cancelled", r1["content"].lower())
        self.assertEqual(conv.workflow_state, "COMPLETED")
        self.assertIsNone(conv.selected_doctor_id)

        # Turn 2: I don't know / I need a consultation
        r2 = self.agent.process_message(conv.id, "I don't know / I need a consultation")
        # Must not default to Dr. Ahmed Khan
        self.assertIsNone(conv.selected_doctor_id)
        self.assertEqual(conv.awaiting_input, "doctor_choice")
        self.assertIsNotNone(r2.get("ui_action"))
        self.assertEqual(r2["ui_action"]["type"], "doctor_selection")
        self.assertEqual(r2["ui_action"]["title"], "Choose Your Doctor")

        # Verify all doctors are available in options
        doctor_names = [opt["name"] for opt in r2["ui_action"]["options"]]
        self.assertIn("Dr. Ahmed Khan", doctor_names)
        self.assertIn("Dr. Sara Malik", doctor_names)
        self.assertIn("Dr Haroon", doctor_names)

        # Turn 3: User picks Dr Haroon
        r3 = self.agent.process_message(conv.id, "Dr Haroon")
        self.assertEqual(conv.selected_doctor_id, self.dr_haroon.id)
        self.assertEqual(conv.awaiting_input, "date_choice")
        self.assertEqual(r3["ui_action"]["type"], "date_selection")
        self.assertIn("Dr Haroon", r3["ui_action"]["title"])

    def test_fresh_conversation_consultation_prompts_doctor_choice(self):
        """
        Verifies that in a fresh conversation, saying 'I don't know / I need a consultation'
        prompts doctor choice without defaulting to doctor 1.
        """
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        r = self.agent.process_message(conv.id, "I don't know / I need a consultation")
        self.assertIsNone(conv.selected_doctor_id)
        self.assertEqual(conv.awaiting_input, "doctor_choice")
        self.assertEqual(r["ui_action"]["type"], "doctor_selection")


if __name__ == "__main__":
    unittest.main()
