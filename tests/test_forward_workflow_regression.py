import unittest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Conversation, Message, Customer, Appointment
from ai.agent import Agent
from ai.llm_client import resolve_date_string


class TestRegressionConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret-regression"
    LLM_PROVIDER = "mock"


class TestForwardWorkflowRegression(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestRegressionConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.biz = Business(
            name="SmileCare Dental Clinic",
            phone="+92420000000",
            address="Plot 42-B, Main Boulevard, Gulberg III, Lahore",
            timezone="Asia/Karachi",
            opening_hours="09:00 AM - 05:00 PM",
            consultation_fee=2000.0
        )
        db.session.add(self.biz)
        db.session.commit()

        self.doc1 = Doctor(
            business_id=self.biz.id,
            name="Dr. Ahmed Khan",
            specialization="General Dentistry & Orthodontics",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        self.doc2 = Doctor(
            business_id=self.biz.id,
            name="Dr. Sara Malik",
            specialization="Pediatric & Cosmetic Dentistry",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        db.session.add_all([self.doc1, self.doc2])
        db.session.commit()

        self.svc1 = Service(
            business_id=self.biz.id,
            doctor_id=self.doc1.id,
            name="Dental Checkup & Consultation",
            duration=30,
            price=2000.0,
            is_active=True
        )
        self.svc2 = Service(
            business_id=self.biz.id,
            doctor_id=self.doc2.id,
            name="Dental Cleaning & Scaling",
            duration=45,
            price=4000.0,
            is_active=True
        )
        db.session.add_all([self.svc1, self.svc2])
        db.session.commit()

        self.agent = Agent(business_id=self.biz.id, llm_provider="mock")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_scenario_a_and_b_forward_progression(self):
        """
        TEST A & TEST B:
        Turn 1: 'My name is Ali and I need an appointment tomorrow.' -> name=Ali, date=tomorrow, doctor missing -> doctor_selection
        Turn 2: 'I don't know what problem I have. I just need a dental checkup.' -> service=Consultation, doctor auto-assigned -> date_choice / date_selection
        """
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Turn 1
        res1 = self.agent.process_message(conv.id, "My name is Ali and I need an appointment tomorrow.")
        conv_1 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_1.pending_customer_name, "Ali")
        tz = ZoneInfo("Asia/Karachi")
        expected_tomorrow = (datetime.now(tz).date() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(conv_1.requested_date, expected_tomorrow)
        self.assertIsNone(conv_1.selected_service_id)
        self.assertIsNone(conv_1.selected_doctor_id)
        self.assertEqual(conv_1.awaiting_input, "doctor_choice")
        self.assertEqual(res1.get("ui_action", {}).get("type"), "doctor_selection")

        # Turn 2
        res2 = self.agent.process_message(conv.id, "I don't know what problem I have. I just need a dental checkup.")
        conv_2 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_2.selected_service_id, self.svc1.id)  # Dental Checkup & Consultation
        self.assertEqual(conv_2.selected_doctor_id, self.svc1.doctor_id)  # Auto-assign service's doctor
        self.assertEqual(conv_2.requested_date, expected_tomorrow)  # Date preserved
        self.assertEqual(conv_2.awaiting_input, "time_choice")

    def test_scenario_urdu_script_conversation(self):
        """
        Test Urdu script conversation:
        Turn 1: 'اسلام علیکم میرا نام علی ہے اور مجھے کل کے لیے ایک اپائنٹمنٹ چاہیے' -> Name=Ali, Date=Tomorrow -> doctor_selection
        Turn 2: 'مجھے پروبلم کا نہیں پتا لیکن میں فیلال دانت کی چیک اپ کے لیے آ رہا ہوں' -> Service=Consultation -> auto-assign doctor
        """
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Turn 1
        res1 = self.agent.process_message(conv.id, "اسلام علیکم میرا نام علی ہے اور مجھے کل کے لیے ایک اپائنٹمنٹ چاہیے")
        conv_1 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_1.pending_customer_name, "Ali")
        tz = ZoneInfo("Asia/Karachi")
        expected_tomorrow = (datetime.now(tz).date() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertEqual(conv_1.requested_date, expected_tomorrow)
        self.assertIsNone(conv_1.selected_service_id)
        self.assertEqual(res1.get("ui_action", {}).get("type"), "doctor_selection")

        # Turn 2
        res2 = self.agent.process_message(conv.id, "مجھے پروبلم کا نہیں پتا لیکن میں فیلال دانت کی چیک اپ کے لیے آ رہا ہوں")
        conv_2 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_2.selected_service_id, self.svc1.id)
        self.assertEqual(conv_2.selected_doctor_id, self.svc1.doctor_id)

    def test_scenario_c_consultation_with_doctor(self):
        """
        TEST C:
        Input: 'I don't know what treatment I need. Book me with Dr Sara.'
        Expected: doctor = Dr Sara, ui_action = date_selection
        """
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "I don't know what treatment I need. Book me with Dr Sara.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)  # Dr. Sara Malik
        self.assertIsNone(conv_db.requested_date)
        self.assertEqual(conv_db.awaiting_input, "date_choice")
        self.assertEqual(res.get("ui_action", {}).get("type"), "date_selection")

    def test_scenario_d_compound_intent_with_symptom(self):
        """
        TEST D:
        Input: 'My name is Ali and I want Dr Sara tomorrow at 2 PM because I have tooth pain.'
        Expected: name = Ali, doctor = Dr Sara, date = tomorrow, time = 14:00, missing = phone
        """
        from tests.test_date_helpers import get_next_open_weekday
        from unittest.mock import patch
        expected_date = get_next_open_weekday(self.biz.id, doctor_id=self.doc2.id)

        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        with patch("ai.agent.resolve_date_string", return_value=expected_date):
            res = self.agent.process_message(conv.id, "My name is Ali and I want Dr Sara tomorrow at 2 PM because I have tooth pain.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.pending_customer_name, "Ali")
        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_db.requested_date, expected_date)
        self.assertEqual(conv_db.requested_time, "14:00")
        self.assertEqual(conv_db.awaiting_input, "phone")
        self.assertIsNone(res.get("ui_action"))

    def test_scenario_e_timezone_relative_dates(self):
        """
        TEST E:
        Verify 'tomorrow', 'today', 'kal', 'aaj', 'کل', 'آج' always use Asia/Karachi and never stale dates.
        """
        tz = ZoneInfo("Asia/Karachi")
        today_expected = datetime.now(tz).date().strftime("%Y-%m-%d")
        tomorrow_expected = (datetime.now(tz).date() + timedelta(days=1)).strftime("%Y-%m-%d")
        day_after_expected = (datetime.now(tz).date() + timedelta(days=2)).strftime("%Y-%m-%d")

        self.assertEqual(resolve_date_string("today", business_id=self.biz.id), today_expected)
        self.assertEqual(resolve_date_string("aaj", business_id=self.biz.id), today_expected)
        self.assertEqual(resolve_date_string("آج", business_id=self.biz.id), today_expected)

        self.assertEqual(resolve_date_string("tomorrow", business_id=self.biz.id), tomorrow_expected)
        self.assertEqual(resolve_date_string("kal", business_id=self.biz.id), tomorrow_expected)
        self.assertEqual(resolve_date_string("کل", business_id=self.biz.id), tomorrow_expected)

        self.assertEqual(resolve_date_string("day after tomorrow", business_id=self.biz.id), day_after_expected)
        self.assertEqual(resolve_date_string("parson", business_id=self.biz.id), day_after_expected)
        self.assertEqual(resolve_date_string("پرسوں", business_id=self.biz.id), day_after_expected)

    def test_scenario_f_repeating_service_text_preserves_state(self):
        """
        TEST F:
        After service is selected, repeating service-related text must NOT cause:
        service_selection or rewind awaiting_input to service.
        """
        conv = Conversation(
            business_id=self.biz.id,
            status="AI",
            selected_doctor_id=self.doc2.id,
            selected_service_id=self.svc2.id,  # Dental Cleaning & Scaling
            awaiting_input="date_choice",
            intent="BOOK_APPOINTMENT",
            workflow_state="COLLECTING_INFO"
        )
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "I need dental cleaning scaling please")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_service_id, self.svc2.id)
        self.assertEqual(conv_db.awaiting_input, "date_choice")
        self.assertEqual(res.get("ui_action", {}).get("type"), "date_selection")

    def test_scenario_g_repeating_doctor_text_preserves_state(self):
        """
        TEST G:
        After doctor is selected, repeating doctor name must NOT cause:
        doctor_selection or rewind awaiting_input to doctor.
        """
        conv = Conversation(
            business_id=self.biz.id,
            status="AI",
            selected_service_id=self.svc2.id,
            selected_doctor_id=self.doc2.id,  # Dr. Sara Malik
            awaiting_input="date_choice",
            intent="BOOK_APPOINTMENT",
            workflow_state="COLLECTING_INFO"
        )
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "Dr Sara Malik please")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_db.awaiting_input, "date_choice")
        self.assertEqual(res.get("ui_action", {}).get("type"), "date_selection")
        self.assertNotEqual(res.get("ui_action", {}).get("type"), "doctor_selection")


if __name__ == "__main__":
    unittest.main()
