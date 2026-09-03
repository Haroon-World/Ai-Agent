import unittest
import json
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Conversation, Message, Customer, Appointment
from ai.agent import Agent

class MasterWorkflowTestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret-master-audit"
    LLM_PROVIDER = "mock"


class TestMasterWorkflowStabilityAudit(unittest.TestCase):
    def setUp(self):
        self.app = create_app(MasterWorkflowTestConfig)
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

        self.svc_consult = Service.query.filter(Service.business_id == self.biz.id, Service.name.ilike("%consultation%")).first()
        if not self.svc_consult:
            self.svc_consult = Service(
                business_id=self.biz.id,
                doctor_id=self.doc1.id,
                name="Dental Checkup & Consultation",
                duration=30,
                price=2000.0,
                is_active=True
            )
            db.session.add(self.svc_consult)

        self.svc_clean = Service.query.filter(Service.business_id == self.biz.id, Service.name.ilike("%cleaning%")).first()
        if not self.svc_clean:
            self.svc_clean = Service(
                business_id=self.biz.id,
                doctor_id=self.doc2.id,
                name="Dental Cleaning & Scaling",
                duration=45,
                price=4000.0,
                is_active=True
            )
            db.session.add(self.svc_clean)
        db.session.commit()

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

    def _init_conv(self):
        res = self.client.post("/api/chat/init")
        return res.get_json()["conversation_id"]

    def _send(self, conv_id, text):
        res = self.client.post("/api/chat/send", json={"conversation_id": conv_id, "message": text})
        return res.get_json()

    # 1. Name only
    def test_01_name_only(self):
        c_id = self._init_conv()
        d = self._send(c_id, "My name is Ali.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.pending_customer_name, "Ali")

    # 2. Name + appointment request
    def test_02_name_and_appointment_request(self):
        c_id = self._init_conv()
        d = self._send(c_id, "My name is Ali and I want an appointment.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(d.get("ui_action", {}).get("type"), "doctor_selection")

    # 3. Name + consultation
    def test_03_name_and_consultation(self):
        c_id = self._init_conv()
        d = self._send(c_id, "My name is Ali and I need a dental consultation.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_service_id, self.svc_consult.id)
        self.assertEqual(conv.selected_doctor_id, self.svc_consult.doctor_id)

    # 4. Name + doctor
    def test_04_name_and_doctor(self):
        c_id = self._init_conv()
        d = self._send(c_id, "My name is Ali. Book me with Dr Sara.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)

    # 5. Name + doctor + date
    def test_05_name_doctor_date(self):
        c_id = self._init_conv()
        d = self._send(c_id, "My name is Ali. I want Dr Ahmed tomorrow.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_doctor_id, self.doc1.id)
        self.assertEqual(conv.requested_date, self.tomorrow_str)
        self.assertEqual(d.get("ui_action", {}).get("type"), "time_slot_selection")

    # 6. Name + doctor + date + time
    def test_06_name_doctor_date_time(self):
        c_id = self._init_conv()
        d = self._send(c_id, "My name is Ali. I want Dr Sara tomorrow at 2 PM.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.requested_date, self.tomorrow_str)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.awaiting_input, "phone")
        self.assertIsNone(d.get("ui_action"))

    # 7. Name + doctor + date + time + symptom
    def test_07_name_doctor_date_time_symptom(self):
        c_id = self._init_conv()
        d = self._send(c_id, "My name is Ali. I need Dr Sara tomorrow at 2 PM because my tooth hurts and I don't know what treatment I need.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.requested_date, self.tomorrow_str)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.awaiting_input, "phone")
        self.assertIsNone(d.get("ui_action"))

    # 8. Unknown treatment -> consultation fallback
    def test_08_unknown_treatment_fallback(self):
        c_id = self._init_conv()
        d = self._send(c_id, "I don't know what treatment I need.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_service_id, self.svc_consult.id)
        self.assertEqual(d.get("ui_action", {}).get("type"), "date_selection")

    # 9. Doctor selected through UI
    def test_09_doctor_selected_ui(self):
        c_id = self._init_conv()
        self._send(c_id, "I need a consultation")
        d = self._send(c_id, "Dr. Ahmed Khan")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_doctor_id, self.doc1.id)
        self.assertEqual(d.get("ui_action", {}).get("type"), "date_selection")

    # 10. Service selected through UI
    def test_10_service_selected_ui(self):
        c_id = self._init_conv()
        self._send(c_id, "I want an appointment")
        d = self._send(c_id, "Dental Cleaning & Scaling")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_service_id, self.svc_clean.id)
        self.assertEqual(conv.selected_doctor_id, self.svc_clean.doctor_id)

    # 11. Date selected through UI
    def test_11_date_selected_ui(self):
        c_id = self._init_conv()
        self._send(c_id, "I want Dental Cleaning with Dr Ahmed")
        d = self._send(c_id, self.tomorrow_str)
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_date, self.tomorrow_str)
        self.assertEqual(d.get("ui_action", {}).get("type"), "time_slot_selection")

    # 12. Time selected through UI
    def test_12_time_selected_ui(self):
        c_id = self._init_conv()
        self._send(c_id, f"Dental Cleaning with Dr Ahmed on {self.tomorrow_str}")
        d = self._send(c_id, "10:00 AM")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_time, "10:00")
        self.assertIsNone(d.get("ui_action"))

    # 13. Exact time in natural language
    def test_13_exact_time_natural_language(self):
        c_id = self._init_conv()
        d = self._send(c_id, f"Book Dr Sara on {self.tomorrow_str} at 2 PM")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIsNone(d.get("ui_action"))

    # 14. Exact time unavailable
    def test_14_exact_time_unavailable(self):
        # Book 14:00 slot to make it unavailable
        cust = Customer(business_id=self.biz.id, name="Booked Patient", phone="03009999999")
        db.session.add(cust)
        db.session.commit()
        appt = Appointment(
            business_id=self.biz.id,
            customer_id=cust.id,
            doctor_id=self.doc2.id,
            service_id=self.svc_consult.id,
            appointment_date=self.tomorrow_str,
            appointment_time="14:00",
            status="CONFIRMED"
        )
        db.session.add(appt)
        db.session.commit()

        c_id = self._init_conv()
        d = self._send(c_id, f"Book Dr Sara on {self.tomorrow_str} at 2 PM")
        conv = db.session.get(Conversation, c_id)
        self.assertIsNone(conv.requested_time)
        self.assertEqual(d.get("ui_action", {}).get("type"), "time_slot_selection")

    # 15. Urdu time
    def test_15_urdu_time(self):
        c_id = self._init_conv()
        d = self._send(c_id, f"ڈاکٹر احمد کے ساتھ کل دن دو بجے کی اپوائنٹمنٹ چاہیے")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIsNone(d.get("ui_action"))

    # 16. Roman Urdu time
    def test_16_roman_urdu_time(self):
        c_id = self._init_conv()
        d = self._send(c_id, f"Dr Sara ke sath kal do baje appointment chahiye")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIsNone(d.get("ui_action"))

    # 17. STT time variation (e.g. gyara baje / گیارہ بجے / گہرہ بجے)
    def test_17_stt_time_variation(self):
        c_id = self._init_conv()
        d = self._send(c_id, f"Dr Ahmed tomorrow at gyara baje")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_time, "11:00")
        self.assertIsNone(d.get("ui_action"))

    # 18. Repeated doctor
    def test_18_repeated_doctor(self):
        c_id = self._init_conv()
        self._send(c_id, f"I want Dr Sara on {self.tomorrow_str} at 2 PM")
        d = self._send(c_id, "Dr Sara")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIsNone(d.get("ui_action"))

    # 19. Repeated service
    def test_19_repeated_service(self):
        c_id = self._init_conv()
        self._send(c_id, f"Dental Cleaning with Dr Sara on {self.tomorrow_str} at 2 PM")
        d = self._send(c_id, "Dental Cleaning")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_service_id, self.svc_clean.id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIsNone(d.get("ui_action"))

    # 20. Repeated date
    def test_20_repeated_date(self):
        c_id = self._init_conv()
        self._send(c_id, f"Dr Sara on {self.tomorrow_str} at 2 PM")
        d = self._send(c_id, "tomorrow")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_date, self.tomorrow_str)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIsNone(d.get("ui_action"))

    # 21. Repeated time
    def test_21_repeated_time(self):
        c_id = self._init_conv()
        self._send(c_id, f"Dr Sara on {self.tomorrow_str} at 2 PM")
        d = self._send(c_id, "2 PM")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertIsNone(d.get("ui_action"))

    # 22. Explicit doctor change
    def test_22_explicit_doctor_change(self):
        c_id = self._init_conv()
        self._send(c_id, f"I want Dr Sara on {self.tomorrow_str}")
        d = self._send(c_id, "Actually I want Dr Ahmed instead.")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_doctor_id, self.doc1.id)
        self.assertEqual(conv.requested_date, self.tomorrow_str)

    # 23. Explicit date change
    def test_23_explicit_date_change(self):
        c_id = self._init_conv()
        self._send(c_id, f"I want Dr Sara on {self.tomorrow_str} at 2 PM")
        # Change date to next open weekday
        from tests.test_date_helpers import get_next_open_weekday
        target_open_date = get_next_open_weekday(self.biz.id, from_date=self.tomorrow_str, doctor_id=self.doc2.id)
        d = self._send(c_id, f"Actually I want to change date to {target_open_date}")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_date, target_open_date)

    # 24. Explicit time change
    def test_24_explicit_time_change(self):
        c_id = self._init_conv()
        self._send(c_id, f"Dental Cleaning with Dr Sara on {self.tomorrow_str} at 2 PM")
        d = self._send(c_id, "Change time to 3 PM")
        db.session.expire_all()
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.requested_time, "15:00")

    # 25. Stale selector click (e.g. clicking old doctor card when awaiting phone)
    def test_25_stale_selector_click(self):
        c_id = self._init_conv()
        self._send(c_id, f"My name is Ali. Dr Sara on {self.tomorrow_str} at 2 PM")
        # Stale click on Dr. Sara Malik
        d = self._send(c_id, "Dr. Sara Malik")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_doctor_id, self.doc2.id)
        self.assertEqual(conv.requested_time, "14:00")
        self.assertEqual(conv.awaiting_input, "phone")
        self.assertIsNone(d.get("ui_action"))

    # 26. Page reload (loading history)
    def test_26_page_reload_history(self):
        c_id = self._init_conv()
        self._send(c_id, f"Dr Sara on {self.tomorrow_str} at 2 PM")
        hist = self.client.get(f"/api/chat/history/{c_id}").get_json()
        self.assertTrue(hist["success"])
        last_asst = [m for m in hist["messages"] if m["role"] == "assistant"][-1]
        self.assertIsNone(last_asst.get("interactive_data"))

    # 27. History polling
    def test_27_history_polling(self):
        c_id = self._init_conv()
        self._send(c_id, "I need a consultation")
        hist = self.client.get(f"/api/chat/history/{c_id}").get_json()
        self.assertTrue(hist["success"])
        last_asst = [m for m in hist["messages"] if m["role"] == "assistant"][-1]
        self.assertEqual(last_asst.get("interactive_data", {}).get("type"), "date_selection")

    # 28. Booking completion
    def test_28_booking_completion(self):
        c_id = self._init_conv()
        self._send(c_id, f"My name is Ali. Dr Sara on {self.tomorrow_str} at 2 PM")
        self._send(c_id, "03001234567")
        d = self._send(c_id, "Confirm Appointment")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.workflow_state, "BOOKED")
        self.assertIsNone(d.get("ui_action"))

    # 29. Post-booking message
    def test_29_post_booking_message(self):
        c_id = self._init_conv()
        self._send(c_id, f"My name is Ali. Dr Sara on {self.tomorrow_str} at 2 PM")
        self._send(c_id, "03001234567")
        self._send(c_id, "Confirm Appointment")
        d = self._send(c_id, "Thank you so much!")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.workflow_state, "BOOKED")
        self.assertIsNone(d.get("ui_action"))

    # 30. Voice transcript following same workflow
    def test_30_voice_transcript_flow(self):
        c_id = self._init_conv()
        d = self._send(c_id, f"Book appointment with Dr Ahmed tomorrow at 10 AM my name is Bilal")
        conv = db.session.get(Conversation, c_id)
        self.assertEqual(conv.selected_doctor_id, self.doc1.id)
        self.assertEqual(conv.requested_date, self.tomorrow_str)
        self.assertEqual(conv.requested_time, "10:00")
        self.assertEqual(conv.pending_customer_name, "Bilal")
        self.assertEqual(conv.awaiting_input, "phone")
        self.assertIsNone(d.get("ui_action"))


if __name__ == "__main__":
    unittest.main()
