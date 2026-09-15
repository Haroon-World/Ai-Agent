import unittest
from datetime import datetime, date, timedelta
from app import create_app
from models import db, Business, Doctor, Service, Conversation, Message, Appointment, DoctorSchedule
from config.config import Config
from seed import seed_database
from services.booking_service import BookingService, _get_business_tz, RequestCache
from ai.agent import Agent
from ai.response_generator import (
    DISTINCT_ROMAN_URDU_WORDS,
    detect_language,
    _format_doctor_schedule_lines,
)
from ai.prompts import build_system_prompt



class TestScheduleConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret-schedule"
    LLM_PROVIDER = "mock"


class TestWeeklyScheduleAndLanguageParity(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestScheduleConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        seed_database(self.app)
        RequestCache.clear()

        self.agent = Agent(business_id=1, llm_provider="mock")
        self.tz = _get_business_tz(1)
        self.today = datetime.now(self.tz).date()

    def tearDown(self):
        RequestCache.clear()
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_case_1_roman_urdu_service_and_doctor_booking(self):
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "mere dr sara se apointment fix kro, check up kalye")
        content = res["content"]

        self.assertIn("Dr. Sara", content)
        self.assertTrue("schedule" in content.lower() or "date" in content.lower() or "appointment" in content.lower())
        self.assertFalse(any(ord(c) >= 0x0600 and ord(c) <= 0x06FF for c in content[:20]))

    def test_case_2_followup_weekly_schedule_request(self):
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        self.agent.process_message(conv.id, "mere dr sara se apointment fix kro, check up kalye")
        res2 = self.agent.process_message(conv.id, "pehly mujhy in ka weekly schedule btao")
        content2 = res2["content"]

        self.assertIn("Dr. Sara Malik", content2)
        self.assertIn("weekly schedule", content2.lower())
        self.assertIn("• Monday:", content2)
        self.assertIn("• Tuesday:", content2)
        self.assertIn("• Sunday: Closed", content2)
        self.assertNotIn("check_availability", [t["name"] for t in res2.get("executed_tools", [])])

    def test_case_3_direct_weekly_schedule_query(self):
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "dr sara ka weekly schedule kya hai")
        content = res["content"]

        self.assertIn("Dr. Sara Malik", content)
        self.assertIn("• Monday:", content)
        self.assertIn("• Friday:", content)
        self.assertIn("• Saturday:", content)
        self.assertIn("• Sunday: Closed", content)
        self.assertNotIn("check_availability", [t["name"] for t in res.get("executed_tools", [])])

    def test_case_4_tomorrow_availability_query(self):
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "Dr Sara kal available hain?")
        content = res["content"]

        tomorrow = self.today + timedelta(days=1)
        expected_day = tomorrow.strftime("%A")

        self.assertTrue(expected_day in content or "available" in content.lower())
        self.assertIn("check_availability", [t["name"] for t in res.get("executed_tools", [])])

    def test_case_5_weekday_specific_schedule_query(self):
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "dr sara ka Monday ka time kya hai")
        content = res["content"]

        self.assertIn("Dr. Sara Malik", content)
        self.assertIn("Monday", content)
        self.assertIn("• Monday: 09:00 AM – 05:00 PM", content)
        self.assertNotIn("check_availability", [t["name"] for t in res.get("executed_tools", [])])

    def test_case_6_tomorrow_slots_query(self):
        from tests.test_date_helpers import patch_open_date
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        with patch_open_date(1, doctor_id=2):
            res = self.agent.process_message(conv.id, "dr sara ke kal ke slots kya hain")
        content = res["content"]

        self.assertIn("•", content)
        self.assertIn("check_availability", [t["name"] for t in res.get("executed_tools", [])])

    def test_case_7_english_weekly_schedule_query(self):
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "What is Dr. Sara's weekly schedule?")
        content = res["content"]

        self.assertIn("Here is Dr. Sara Malik's weekly schedule:", content)
        self.assertIn("• Monday: 09:00 AM – 05:00 PM", content)
        self.assertIn("• Sunday: Closed", content)

    def test_case_8_urdu_script_weekly_schedule_query(self):
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "ڈاکٹر سارہ کا ہفتہ وار شیڈول کیا ہے؟")
        content = res["content"]

        self.assertIn("Dr. Sara Malik", content)
        self.assertIn("شیڈول", content)
        self.assertIn("• Monday: 09:00 AM – 05:00 PM", content)
        self.assertIn("• Sunday: Closed", content)

    def test_admin_custom_schedule_sync(self):
        doc = db.session.get(Doctor, 2)
        wed_sched = DoctorSchedule.query.filter_by(doctor_id=doc.id, day_of_week="Wednesday").first()
        if wed_sched:
            orig_avail = wed_sched.is_available
            try:
                wed_sched.is_available = False
                db.session.commit()

                conv = Conversation(business_id=1, status="AI", workflow_state="START")
                db.session.add(conv)
                db.session.commit()

                res = self.agent.process_message(conv.id, "Dr Sara ka weekly schedule kya hai")
                self.assertIn("• Wednesday: Closed", res["content"])
            finally:
                wed_sched.is_available = orig_avail
                db.session.commit()

    def test_case_9_checkup_does_not_trigger_roman_urdu(self):
        self.assertNotIn("checkup", DISTINCT_ROMAN_URDU_WORDS)
        self.assertEqual(detect_language("I need a checkup"), "english")
        self.assertEqual(detect_language("General Checkup"), "english")
        self.assertEqual(detect_language("Checkup please"), "english")

        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "I need a checkup with Dr. Sara")
        content = res["content"]
        self.assertNotIn("Ji ", content)
        self.assertNotIn("karein", content.lower())
        self.assertNotIn("shukriya", content.lower())
        self.assertNotIn("theek", content.lower())

    def test_case_10_multi_shift_doctor_schedule_presentation(self):
        multi_shift_doc = {
            "name": "Dr. Multi Shift",
            "weekly_schedule": [
                {
                    "day_of_week": "Monday",
                    "is_available": True,
                    "start_time": "08:00",
                    "end_time": "10:00",
                    "shift_2_start_time": "17:00",
                    "shift_2_end_time": "21:00"
                },
                {
                    "day_of_week": "Tuesday",
                    "is_available": True,
                    "start_time": "09:00",
                    "end_time": "17:00"
                }
            ]
        }
        lines = _format_doctor_schedule_lines(multi_shift_doc, target_day="Monday")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0], "• Monday: 08:00 AM – 10:00 AM (Morning) & 05:00 PM – 09:00 PM (Evening)")

    def test_case_11_system_prompt_mandates_and_multi_shift(self):
        prompt = build_system_prompt(1)
        self.assertIn("STRICT LANGUAGE LOCK MANDATE", prompt)
        self.assertIn("STRICT PROHIBITION: The AI MUST NEVER switch to Roman Urdu", prompt)
        self.assertIn("MULTI-SHIFT WORKING HOURS", prompt)
        self.assertNotIn("• Dr. Bilal Tariq", prompt)

if __name__ == "__main__":
    unittest.main()
