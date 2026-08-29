import unittest
from datetime import datetime, date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Appointment, Conversation, Message, Customer
from ai.agent import Agent

class TestStateStabilityConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret-stability"
    LLM_PROVIDER = "mock"

class TestStateStabilityAndForwardWorkflow(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestStateStabilityConfig)
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
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        self.doc2 = Doctor(
            business_id=self.biz.id,
            name="Dr. Sara Malik",
            specialization="Pediatric & Cosmetic Dentistry",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        db.session.add_all([self.doc1, self.doc2])

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
            price=4000.0,
            is_active=True
        )
        db.session.add_all([self.svc1, self.svc2])
        db.session.commit()

        self.agent = Agent(business_id=self.biz.id, llm_provider="mock")
        from tests.test_date_helpers import get_next_open_weekday, make_open_date_resolver
        from unittest.mock import patch
        self.open_date = get_next_open_weekday(self.biz.id, doctor_id=self.doc2.id)
        resolver = make_open_date_resolver(self.open_date)
        self._p_agent = patch("ai.agent.resolve_date_string", side_effect=resolver)
        self._p_llm = patch("ai.llm_client.resolve_date_string", side_effect=resolver)
        self._p_agent.start()
        self._p_llm.start()

    def tearDown(self):
        self._p_agent.stop()
        self._p_llm.stop()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_scenario_1_name_and_appointment_intent(self):
        """TEST 1: 'My name is Ali and I want an appointment.'"""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "My name is Ali and I want an appointment.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.pending_customer_name, "Ali")
        self.assertEqual(conv_db.intent, "BOOK_APPOINTMENT")
        self.assertEqual(conv_db.workflow_state, "COLLECTING_INFO")
        self.assertEqual(resp.get("ui_action", {}).get("type"), "service_selection")
        self.assertNotIn("Ali", resp.get("ui_action", {}).get("title", ""))

    def test_scenario_2_consultation_and_doctor(self):
        """TEST 2: 'I don't know what treatment I need. Book me with Dr Sara.'"""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "I don't know what treatment I need. Book me with Dr Sara.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_service_id, self.svc1.id)
        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_db.awaiting_input, "date_choice")
        self.assertEqual(resp.get("ui_action", {}).get("type"), "date_selection")

    def test_scenario_3_doctor_date_and_time(self):
        """TEST 3: 'Book me with Dr Sara tomorrow at 2 PM.'"""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "Book me with Dr Sara tomorrow at 2 PM.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)
        self.assertIsNotNone(conv_db.requested_date)
        self.assertEqual(conv_db.requested_time, "14:00")
        self.assertIn("phone", resp.get("content", "").lower() + resp.get("content", ""))

    def test_scenario_4_name_doctor_date_and_time(self):
        """TEST 4: 'My name is Ali, I want Dr Sara tomorrow at 2 PM.'"""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "My name is Ali, I want Dr Sara tomorrow at 2 PM.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.pending_customer_name, "Ali")
        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)
        self.assertIsNotNone(conv_db.requested_date)
        self.assertEqual(conv_db.requested_time, "14:00")
        self.assertIn("phone", resp.get("content", "").lower())
        self.assertNotIn("doctor", resp.get("content", "").lower().split("please")[0] if "please" in resp.get("content", "").lower() else "")

    def test_scenario_5_mention_doctor_again_no_regression(self):
        """TEST 5: After doctor is selected, user mentions doctor again."""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Step 1: select doctor and consultation
        self.agent.process_message(conv.id, "I need a checkup with Dr Sara.")
        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_db.awaiting_input, "date_choice")

        # Step 2: user mentions doctor again
        resp2 = self.agent.process_message(conv.id, "Dr Sara ke sath checkup karwana hai")
        conv_db2 = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db2.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_db2.selected_service_id, self.svc1.id)
        self.assertEqual(conv_db2.awaiting_input, "date_choice")
        self.assertEqual(resp2.get("ui_action", {}).get("type"), "date_selection")

    def test_scenario_6_service_and_doctor_then_friday(self):
        """TEST 6: After service + doctor selected, user says: 'I want the appointment on Friday.'"""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        self.agent.process_message(conv.id, "I need a cleaning with Dr Sara.")
        resp2 = self.agent.process_message(conv.id, "I want the appointment on Friday.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_db.selected_service_id, self.svc2.id)
        self.assertIsNotNone(conv_db.requested_date)
        # Should not show service or doctor selector
        self.assertNotEqual(resp2.get("ui_action", {}).get("type"), "service_selection")
        self.assertNotEqual(resp2.get("ui_action", {}).get("type"), "doctor_selection")

    def test_scenario_7_repeat_service_after_date(self):
        """TEST 7: After date is selected, user sends the service name again."""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        self.agent.process_message(conv.id, "Dr Sara tomorrow at 2 PM.")
        conv_before = db.session.get(Conversation, conv.id)
        date_before = conv_before.requested_date

        resp2 = self.agent.process_message(conv.id, "Dental Checkup & Consultation")
        conv_after = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_after.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_after.requested_date, date_before)

    def test_scenario_8_explicit_switch_doctor(self):
        """TEST 8: User explicitly says: 'Actually I want Dr Ahmed instead.'"""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        self.agent.process_message(conv.id, "My name is Ali, phone 03123456789. Book with Dr Sara.")
        conv_before = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_before.selected_doctor_id, self.doc2.id)

        resp2 = self.agent.process_message(conv.id, "Actually I want Dr Ahmed instead.")
        conv_after = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_after.selected_doctor_id, self.doc1.id)
        self.assertEqual(conv_after.pending_customer_name, "Ali")
        self.assertEqual(conv_after.pending_customer_phone, "03123456789")

    def test_scenario_9_and_10_history_and_polling(self):
        """TEST 9 & 10: Load history and verify messages representation."""
        client = self.app.test_client()
        init_res = client.post("/api/chat/init")
        init_data = init_res.get_json()
        conv_id = init_data["conversation_id"]

        self.agent.process_message(conv_id, "I want an appointment.")
        self.agent.process_message(conv_id, "Dental Cleaning & Scaling")

        res = client.get(f"/api/chat/history/{conv_id}")
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["workflow_state"], "COLLECTING_INFO")
        self.assertGreaterEqual(len(data["messages"]), 3)

    def test_scenario_11_stale_selector_click(self):
        """TEST 11: Click an old/stale selector."""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Step 1: User says Dr Sara and date
        self.agent.process_message(conv.id, "Dr Sara tomorrow at 2 PM. Name is Ali.")
        conv_before = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_before.selected_doctor_id, self.doc2.id)

        # Stale click sends "I don't know, I need a consultation"
        self.agent.process_message(conv.id, "I don't know, I need a consultation")
        conv_after = db.session.get(Conversation, conv.id)

        # State should NOT move backwards to service_choice or doctor_choice
        self.assertEqual(conv_after.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv_after.pending_customer_name, "Ali")
        self.assertIsNotNone(conv_after.requested_date)

    def test_scenario_12_complete_real_appointment(self):
        """TEST 12: Complete a real appointment."""
        conv = Conversation(business_id=self.biz.id, status="AI")
        db.session.add(conv)
        db.session.commit()

        tomorrow = self.open_date

        resp = self.agent.process_message(conv.id, f"Book me with Dr Sara on {tomorrow} at 10 AM. My name is Ali and my phone is 03123456789.")
        conv_after = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_after.workflow_state, "BOOKED")
        self.assertIsNone(resp.get("ui_action"))

        appts = Appointment.query.filter_by(business_id=self.biz.id).all()
        self.assertEqual(len(appts), 1)
        self.assertEqual(appts[0].customer.name, "Ali")
        self.assertEqual(appts[0].customer.phone, "03123456789")

if __name__ == "__main__":
    unittest.main()
