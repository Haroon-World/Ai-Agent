import unittest
import json
from datetime import datetime, date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Appointment, Conversation, Message
from ai.agent import Agent
from ai.llm_client import resolve_date_string

class TestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret"
    LLM_PROVIDER = "mock"


class TestStrictStateAwareWorkflow(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Seed business, doctors, and services
        self.biz = Business(
            name="SmileCare Dental Clinic",
            phone="+92 42 35789012",
            address="Plot 42-B, Main Boulevard, Gulberg III, Lahore",
            timezone="Asia/Karachi",
            opening_hours="09:00 AM - 05:00 PM",
            consultation_fee=2000.0,
            policies="Please arrive 10 minutes prior to your scheduled slot."
        )
        db.session.add(self.biz)
        db.session.commit()

        self.doc1 = Doctor(
            business_id=self.biz.id,
            name="Dr. Ahmed Khan",
            specialization="Orthodontics & Implants",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        self.doc2 = Doctor(
            business_id=self.biz.id,
            name="Dr. Sara Malik",
            specialization="Cosmetic Dentistry & Checkup",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        db.session.add_all([self.doc1, self.doc2])
        db.session.commit()

        self.svc1 = Service(
            business_id=self.biz.id,
            name="Dental Checkup & Consultation",
            duration=30,
            price=2000.0,
            is_active=True
        )
        self.svc2 = Service(
            business_id=self.biz.id,
            name="Dental Cleaning & Scaling",
            duration=45,
            price=3500.0,
            is_active=True
        )
        db.session.add_all([self.svc1, self.svc2])
        db.session.commit()

        self.agent = Agent(business_id=self.biz.id, llm_provider="mock")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_scenario_1_name_and_generic_appointment(self):
        """
        TEST 1: 'My name is Ali and I want an appointment.'
        Expected:
        - Ask for service/consultation preference.
        - Do not ask for name again.
        - UI action allows service selection.
        """
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "My name is Ali and I want an appointment.")
        conv = db.session.get(Conversation, conv.id)

        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.awaiting_input, "service_choice")
        self.assertIsNotNone(res["ui_action"])
        self.assertEqual(res["ui_action"]["type"], "service_selection")
        self.assertIn("service", res["content"].lower())

    def test_scenario_2_name_doctor_dont_know_treatment(self):
        """
        TEST 2: 'My name is Ali. I want Dr Sara. I don't know what treatment I need.'
        Expected:
        - Doctor = Dr. Sara Malik
        - Service = Consultation
        - Name = Ali
        - Ask ONLY for date.
        - Do not show service or doctor selector.
        """
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "My name is Ali. I want Dr Sara. I don't know what treatment I need.")
        conv = db.session.get(Conversation, conv.id)

        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.selected_service_id, self.svc1.id)
        self.assertEqual(conv.awaiting_input, "date_choice")
        self.assertIn("date", res["content"].lower())
        self.assertIsNotNone(res["ui_action"])
        self.assertEqual(res["ui_action"]["type"], "date_selection")
        self.assertNotIn("service_selection", res["ui_action"]["type"])
        self.assertNotIn("doctor_selection", res["ui_action"]["type"])

    def test_scenario_3_multi_param_tooth_pain(self):
        """
        TEST 3: 'My name is Ali, I want Dr Sara, I have tooth pain, Friday at 2 PM.'
        Expected:
        - Extract all possible info (Name, Doctor, Consultation from pain, Friday, 14:00).
        - Do not restart workflow.
        - Ask ONLY for missing required info (phone number).
        - No service/doctor/date/time selector.
        """
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "My name is Ali, I want Dr Sara, I have tooth pain, Friday at 2 PM.")
        conv = db.session.get(Conversation, conv.id)

        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.selected_service_id, self.svc1.id)
        self.assertIsNotNone(conv.requested_date)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.awaiting_input, "phone")
        self.assertIn("phone", res["content"].lower())
        self.assertIsNone(res["ui_action"])

    def test_scenario_4_all_in_one_sentence_instant_booking(self):
        """
        TEST 4: 'Book me with Dr Sara tomorrow at 10 AM. My name is Ali and my number is 03123456789.'
        Expected:
        - Extract all information.
        - Check real availability and book directly.
        - No service/doctor/date/time selector.
        """
        from tests.test_date_helpers import patch_open_date
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        with patch_open_date(self.biz.id, doctor_id=self.doc2.id):
            res = self.agent.process_message(conv.id, "Book me with Dr Sara tomorrow at 10 AM. My name is Ali and my number is 03123456789.")
        conv = db.session.get(Conversation, conv.id)

        self.assertEqual(conv.workflow_state, "BOOKED")
        self.assertIn("confirmed", res["content"].lower())
        self.assertIsNone(res["ui_action"])

        # Check appointment created in database
        appt = Appointment.query.filter_by(business_id=self.biz.id).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.customer.name, "Ali")
        self.assertEqual(appt.customer.phone, "03123456789")
        self.assertEqual(appt.doctor_id, self.doc2.id)

    def test_scenario_5_user_selects_doctor_from_ui(self):
        """
        TEST 5: User selects Dr Sara from UI.
        Expected:
        - Doctor state is saved.
        - Doctor selector disappears / transitions to next missing field.
        """
        conv = Conversation(business_id=self.biz.id, status="AI", selected_service_id=self.svc1.id, awaiting_input="doctor_choice")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "Dr. Sara Malik")
        conv = db.session.get(Conversation, conv.id)

        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.awaiting_input, "date_choice")
        self.assertIsNotNone(res["ui_action"])
        self.assertEqual(res["ui_action"]["type"], "date_selection")

    def test_scenario_6_user_selects_consultation(self):
        """
        TEST 6: User selects 'I don't know / I need a consultation'.
        Expected:
        - Service becomes Dental Checkup & Consultation.
        - Price is admin-configured consultation price (PKR 2,000).
        - Service selector disappears / transitions to doctor choice.
        """
        conv = Conversation(business_id=self.biz.id, status="AI", awaiting_input="service_choice")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "I don't know, I need a consultation")
        conv = db.session.get(Conversation, conv.id)

        self.assertEqual(conv.selected_service_id, self.svc1.id)
        self.assertEqual(conv.awaiting_input, "doctor_choice")
        self.assertIsNotNone(res["ui_action"])
        self.assertEqual(res["ui_action"]["type"], "doctor_selection")

    def test_scenario_7_user_provides_friday_at_2pm(self):
        """
        TEST 7: User says 'Friday at 2 PM.'
        Expected:
        - Resolve date + time.
        - Check actual availability.
        - Do not ask for date again.
        """
        conv = Conversation(
            business_id=self.biz.id,
            status="AI",
            selected_service_id=self.svc1.id,
            selected_doctor_id=self.doc2.id,
            pending_customer_name="Ali",
            awaiting_input="date_choice"
        )
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "Friday at 2 PM.")
        conv = db.session.get(Conversation, conv.id)

        self.assertIsNotNone(conv.requested_date)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.awaiting_input, "phone")
        self.assertIn("phone", res["content"].lower())
        self.assertNotIn("which date", res["content"].lower())

    def test_scenario_8_final_confirmation_and_no_selectors(self):
        """
        TEST 8: After successful booking:
        Expected:
        - Exactly ONE final confirmation containing: customer name, doctor, service, date, time, price, appointment ID.
        - No additional service/doctor/date/time selector appears after successful booking.
        """
        from tests.test_date_helpers import get_next_open_weekday
        target_date = get_next_open_weekday(self.biz.id, doctor_id=self.doc2.id)

        conv = Conversation(
            business_id=self.biz.id,
            status="AI",
            selected_service_id=self.svc1.id,
            selected_doctor_id=self.doc2.id,
            pending_customer_name="Ali",
            requested_date=target_date,
            requested_time="14:00",
            awaiting_input="phone"
        )
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "03123456789")
        conv = db.session.get(Conversation, conv.id)

        self.assertEqual(conv.workflow_state, "BOOKED")
        content = res["content"].lower()
        self.assertIn("confirmed", content)
        self.assertIn("ali", content)
        self.assertIn("sara", content)
        self.assertIn(target_date.lower(), content)
        self.assertIn("02:00 pm", content)
        self.assertIn("appointment id", content)
        self.assertIsNone(res["ui_action"])


if __name__ == "__main__":
    unittest.main()
