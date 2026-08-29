import unittest
import json
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Conversation, Message, Customer, Appointment
from ai.agent import Agent
from ai.llm_client import resolve_date_string


class TestTimeRegressionConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret-time-regression"
    LLM_PROVIDER = "mock"


class TestTimeSelectionUIRegression(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestTimeRegressionConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

        self.biz = db.session.get(Business, Config.DEFAULT_BUSINESS_ID)
        if not self.biz:
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

        self.doc1 = Doctor.query.filter_by(business_id=self.biz.id, name="Dr. Ahmed Khan").first()
        if not self.doc1:
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
            db.session.add(self.doc1)

        self.doc2 = Doctor.query.filter_by(business_id=self.biz.id, name="Dr. Sara Malik").first()
        if not self.doc2:
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
            db.session.add(self.doc2)

        self.svc1 = Service.query.filter(Service.business_id == self.biz.id, Service.name.ilike("%consultation%")).first()
        if not self.svc1:
            self.svc1 = Service(
                business_id=self.biz.id,
                name="Dental Checkup & Consultation",
                duration=30,
                price=2000.0,
                is_active=True
            )
            db.session.add(self.svc1)

        self.svc2 = Service.query.filter(Service.business_id == self.biz.id, Service.name.ilike("%cleaning%")).first()
        if not self.svc2:
            self.svc2 = Service(
                business_id=self.biz.id,
                name="Dental Cleaning & Scaling",
                duration=45,
                price=4000.0,
                is_active=True
            )
            db.session.add(self.svc2)
        self.agent = Agent(business_id=self.biz.id, llm_provider="mock")
        self.tz = ZoneInfo("Asia/Karachi")
        from tests.test_date_helpers import get_next_open_weekday, make_open_date_resolver
        from unittest.mock import patch
        self.tomorrow_str = get_next_open_weekday(self.biz.id)
        resolver = make_open_date_resolver(self.tomorrow_str)
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

    def test_1_ali_wants_dr_ahmed_tomorrow_at_2pm_dont_know_treatment(self):
        """
        TEST 1:
        'Ali wants Dr Ahmed tomorrow at 2 PM and does not know the treatment.'
        Expected:
        - service = consultation
        - doctor = Dr. Ahmed Khan
        - date = tomorrow
        - time = 14:00
        - no time selector
        - asks only for missing contact information (phone).
        """
        tomorrow_str = self.tomorrow_str

        init_res = self.client.post("/api/chat/init")
        conv_id = init_res.get_json()["conversation_id"]

        msg = "My name is Ali. I want Dr Ahmed tomorrow at 2 PM. I don't know what treatment I need."
        res = self.client.post("/api/chat/send", json={
            "conversation_id": conv_id,
            "message": msg
        })
        data = res.get_json()
        self.assertTrue(data["success"])

        # State check
        conv = db.session.get(Conversation, conv_id)
        consult_svc = Service.query.filter(Service.business_id == self.biz.id, Service.name.ilike("%consultation%")).first()
        self.assertEqual(conv.selected_service_id, consult_svc.id)
        self.assertEqual(conv.selected_doctor_id, self.doc1.id)
        self.assertEqual(conv.requested_date, tomorrow_str)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.awaiting_input, "phone")

        # No time selector
        self.assertIsNone(data.get("ui_action"))

    def test_2_dr_sara_tomorrow_shows_time_selector(self):
        """
        TEST 2:
        'Dr Sara tomorrow'
        Expected:
        - doctor = Dr. Sara Malik
        - date = tomorrow
        - time = missing
        - time-slot selector is shown.
        """
        tomorrow_str = self.tomorrow_str

        init_res = self.client.post("/api/chat/init")
        conv_id = init_res.get_json()["conversation_id"]

        res = self.client.post("/api/chat/send", json={
            "conversation_id": conv_id,
            "message": "I want an appointment with Dr Sara tomorrow."
        })
        data = res.get_json()
        self.assertTrue(data["success"])

        conv = db.session.get(Conversation, conv_id)
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.requested_date, tomorrow_str)
        self.assertIsNone(conv.requested_time)
        self.assertEqual(conv.awaiting_input, "time_choice")

        self.assertIsNotNone(data.get("ui_action"))
        self.assertEqual(data["ui_action"]["type"], "time_slot_selection")

    def test_3_dr_sara_tomorrow_at_2pm_available_no_time_selector(self):
        """
        TEST 3:
        'Dr Sara tomorrow at 2 PM' where 2 PM is available
        Expected:
        - time = 14:00
        - no time selector.
        """
        tomorrow_str = self.tomorrow_str

        init_res = self.client.post("/api/chat/init")
        conv_id = init_res.get_json()["conversation_id"]

        res = self.client.post("/api/chat/send", json={
            "conversation_id": conv_id,
            "message": "I want Dr Sara tomorrow at 2 PM."
        })
        data = res.get_json()
        self.assertTrue(data["success"])

        conv = db.session.get(Conversation, conv_id)
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.requested_date, tomorrow_str)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIn(conv.awaiting_input, ["phone", "name"])

        self.assertIsNone(data.get("ui_action"))

    def test_4_dr_sara_tomorrow_at_2pm_unavailable_shows_alternative_slots(self):
        """
        TEST 4:
        'Dr Sara tomorrow at 2 PM' where 2 PM is unavailable
        Expected:
        - time is NOT incorrectly finalized
        - user is informed that 2 PM is unavailable
        - alternative slots are shown.
        """
        tomorrow_str = self.tomorrow_str

        # Book 14:00 slot to make it unavailable
        cust = Customer(business_id=self.biz.id, name="Booked Patient", phone="03009999999")
        db.session.add(cust)
        db.session.commit()

        appt = Appointment(
            business_id=self.biz.id,
            customer_id=cust.id,
            doctor_id=self.doc2.id,
            service_id=self.svc1.id,
            appointment_date=tomorrow_str,
            appointment_time="14:00",
            status="CONFIRMED"
        )
        db.session.add(appt)
        db.session.commit()

        init_res = self.client.post("/api/chat/init")
        conv_id = init_res.get_json()["conversation_id"]

        res = self.client.post("/api/chat/send", json={
            "conversation_id": conv_id,
            "message": "I want Dr Sara tomorrow at 2 PM."
        })
        data = res.get_json()
        self.assertTrue(data["success"])

        conv = db.session.get(Conversation, conv_id)
        self.assertIsNone(conv.requested_time)
        self.assertEqual(conv.awaiting_input, "time_choice")

        self.assertIsNotNone(data.get("ui_action"))
        self.assertEqual(data["ui_action"]["type"], "time_slot_selection")
        offered_values = [opt["value"] for opt in data["ui_action"]["options"]]
        self.assertNotIn("14:00", offered_values)

    def test_5_history_polling_does_not_rerender_time_selector(self):
        """
        TEST 5:
        After explicit 2 PM selection, history polling must not re-render the time selector.
        """
        tomorrow_str = self.tomorrow_str

        init_res = self.client.post("/api/chat/init")
        conv_id = init_res.get_json()["conversation_id"]

        self.client.post("/api/chat/send", json={
            "conversation_id": conv_id,
            "message": "I want Dr Sara tomorrow at 2 PM."
        })

        # History fetch (simulating background polling)
        hist_res = self.client.get(f"/api/chat/history/{conv_id}")
        hist_data = hist_res.get_json()
        self.assertTrue(hist_data["success"])
        messages = hist_data["messages"]
        last_asst = [m for m in messages if m["role"] == "assistant"][-1]
        self.assertIsNone(last_asst.get("interactive_data"))
        self.assertIsNone(last_asst.get("ui_action"))

    def test_6_phone_submission_completes_booking_after_2pm_selection(self):
        """
        TEST 6:
        After selecting 2 PM, sending the phone number must complete the normal booking flow
        without asking for time again.
        """
        tomorrow_str = self.tomorrow_str

        init_res = self.client.post("/api/chat/init")
        conv_id = init_res.get_json()["conversation_id"]

        # Step 1: Provide details with time
        r1 = self.client.post("/api/chat/send", json={
            "conversation_id": conv_id,
            "message": "My name is Tariq. I need a dental consultation with Dr Ahmed tomorrow at 2 PM."
        })
        d1 = r1.get_json()
        self.assertTrue(d1["success"])
        self.assertIsNone(d1.get("ui_action"))

        # Step 2: Provide phone number
        r2 = self.client.post("/api/chat/send", json={
            "conversation_id": conv_id,
            "message": "03001234567"
        })
        d2 = r2.get_json()
        self.assertTrue(d2["success"])

        # Time must still be 14:00
        conv = db.session.get(Conversation, conv_id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.pending_customer_name, "Tariq")
        self.assertEqual(conv.pending_customer_phone, "03001234567")

    def test_7_urdu_2_turn_exact_scenario(self):
        """
        TEST 7:
        Turn 1: 'میرا نام علی ہے اور مجھے کل کی ڈاکٹر احمد سے اپوائنٹمنٹ چاہیے مجھے نہیں پتا کہ میرا مسئلہ کیا ہے اس لئے میری اپوائنٹمنٹ فکس کر دو' -> shows time slots.
        Turn 2: 'کل کی دن دو بجی کی اپائٹمنٹ فکس کر دو' -> time=14:00, no time selector, asks for phone.
        """
        init_res = self.client.post("/api/chat/init")
        conv_id = init_res.get_json()["conversation_id"]

        # Turn 1
        t1 = "میرا نام علی ہے اور مجھے کل کی ڈاکٹر احمد سے اپوائنٹمنٹ چاہیے مجھے نہیں پتا کہ میرا مسئلہ کیا ہے اس لئے میری اپوائنٹمنٹ فکس کر دو"
        r1 = self.client.post("/api/chat/send", json={"conversation_id": conv_id, "message": t1})
        d1 = r1.get_json()
        self.assertTrue(d1["success"])
        self.assertEqual(d1.get("ui_action", {}).get("type"), "time_slot_selection")

        # Turn 2
        t2 = "کل کی دن دو بجی کی اپائٹمنٹ فکس کر دو"
        r2 = self.client.post("/api/chat/send", json={"conversation_id": conv_id, "message": t2})
        d2 = r2.get_json()
        self.assertTrue(d2["success"])
        self.assertIsNone(d2.get("ui_action"))

        conv = db.session.get(Conversation, conv_id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.awaiting_input, "phone")


if __name__ == "__main__":
    unittest.main()
