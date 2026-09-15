import unittest
from datetime import datetime, date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, DoctorSchedule, Appointment, Customer, Conversation
from services.booking_service import BookingService, get_available_slots, validate_slot, RequestCache
from ai.agent import Agent
from ai.response_generator import detect_language, DISTINCT_ROMAN_URDU_WORDS, _format_doctor_schedule_lines
from ai.prompts import build_system_prompt


class TestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret-multi-shift"
    LLM_PROVIDER = "mock"


class TestMultiShiftAndLanguageLock(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        RequestCache.clear()

        self.biz = Business(
            name="Apex Specialty Clinic",
            address="123 Health Ave, Lahore",
            phone="03001234567",
            timezone="Asia/Karachi"
        )
        db.session.add(self.biz)
        db.session.commit()

        self.doc = Doctor(
            business_id=self.biz.id,
            name="Dr. Soha Fatima",
            specialization="Cardiologist",
            slot_interval=30,
            start_time="08:00",
            end_time="10:00",
            shift_2_start_time="17:00",
            shift_2_end_time="21:00"
        )
        db.session.add(self.doc)
        db.session.commit()

        self.sched_mon = DoctorSchedule(
            doctor_id=self.doc.id,
            day_of_week="Monday",
            is_available=True,
            start_time="08:00",
            end_time="10:00",
            shift_2_start_time="17:00",
            shift_2_end_time="21:00"
        )
        db.session.add(self.sched_mon)

        self.service = Service(
            business_id=self.biz.id,
            doctor_id=self.doc.id,
            name="Consultation & Checkup",
            duration=30,
            price=2000.0
        )
        db.session.add(self.service)
        db.session.commit()

    def tearDown(self):
        RequestCache.clear()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_multi_shift_slot_generation(self):
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7 or 7
        target_monday = today + timedelta(days=days_ahead)
        date_str = target_monday.strftime("%Y-%m-%d")

        slots, err = get_available_slots(self.doc.id, date_str, duration=30, business_id=self.biz.id)
        self.assertEqual(err, "")

        expected_morning = ["08:00", "08:30", "09:00", "09:30"]
        for s in expected_morning:
            self.assertIn(s, slots, f"Expected morning slot {s} to be available")

        expected_evening = ["17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30"]
        for s in expected_evening:
            self.assertIn(s, slots, f"Expected evening slot {s} to be available")

        excluded_times = ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00", "16:30"]
        for s in excluded_times:
            self.assertNotIn(s, slots, f"Afternoon gap time {s} must not be available")

    def test_multi_shift_booking_and_validation(self):
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7 or 7
        target_monday = today + timedelta(days=days_ahead)
        date_str = target_monday.strftime("%Y-%m-%d")

        res_m = BookingService.book_appointment(
            business_id=self.biz.id,
            doctor_id=self.doc.id,
            service_id=self.service.id,
            appointment_date=date_str,
            appointment_time="08:30",
            customer_name="Ali Haider",
            customer_phone="03197155071"
        )
        self.assertTrue(res_m["success"], f"Morning booking failed: {res_m.get('error')}")

        res_e = BookingService.book_appointment(
            business_id=self.biz.id,
            doctor_id=self.doc.id,
            service_id=self.service.id,
            appointment_date=date_str,
            appointment_time="18:00",
            customer_name="Sara Khan",
            customer_phone="03009876543"
        )
        self.assertTrue(res_e["success"], f"Evening booking failed: {res_e.get('error')}")

        res_gap = BookingService.book_appointment(
            business_id=self.biz.id,
            doctor_id=self.doc.id,
            service_id=self.service.id,
            appointment_date=date_str,
            appointment_time="14:00",
            customer_name="Kamran Ahmed",
            customer_phone="03215554433"
        )
        self.assertFalse(res_gap["success"])
        self.assertIn("outside", res_gap["error"].lower())

    def test_language_detection_no_false_positive_on_checkup(self):
        self.assertNotIn("checkup", DISTINCT_ROMAN_URDU_WORDS)

        self.assertEqual(detect_language("I need a general checkup"), "english")
        self.assertEqual(detect_language("Can I get a dental checkup tomorrow?"), "english")
        self.assertEqual(detect_language("ALI Haider 03197155071"), "english")
        self.assertEqual(detect_language("Confirm Appointment"), "english")
        self.assertEqual(detect_language("What are Dr. Soha's working hours?"), "english")
        self.assertEqual(detect_language("What is the consultation fee for checkup?"), "english")

        self.assertEqual(detect_language("mera naam ali haider hai"), "roman_urdu")
        self.assertEqual(detect_language("dr soha k sath appointment fix kr do"), "roman_urdu")

        self.assertEqual(detect_language("میرا نام علی حیدر ہے"), "urdu")

    def test_schedule_formatting_presents_both_shifts(self):
        lines = _format_doctor_schedule_lines(self.doc.to_dict(), target_day="Monday")
        self.assertEqual(len(lines), 1)
        schedule_text = lines[0]
        self.assertIn("08:00 AM", schedule_text)
        self.assertIn("10:00 AM", schedule_text)
        self.assertIn("05:00 PM", schedule_text)
        self.assertIn("09:00 PM", schedule_text)

    def test_system_prompt_language_lock_mandate(self):
        conv = Conversation(business_id=self.biz.id, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        # Add an English user message
        from models import Message
        msg = Message(conversation_id=conv.id, role="user", content="I want an appointment for checkup")
        db.session.add(msg)
        db.session.commit()

        from ai.agent import _build_state_context
        base_prompt = build_system_prompt(self.biz.id)
        state_context = _build_state_context(conv)
        full_prompt = f"{base_prompt}\n\n{state_context}"

        self.assertIn("LANGUAGE MANDATE: The customer is communicating in English", full_prompt)
        self.assertIn("NEVER switch to Roman Urdu", full_prompt)
        self.assertIn("multi-shift", full_prompt.lower())


if __name__ == "__main__":
    unittest.main()
