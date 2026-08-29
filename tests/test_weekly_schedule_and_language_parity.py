import unittest
from datetime import datetime, date, timedelta
from app import create_app
from models import db, Business, Doctor, Service, Conversation, Message, Appointment, DoctorSchedule
from config.config import Config
from seed import seed_database
from services.booking_service import BookingService, _get_business_tz, RequestCache
from ai.agent import Agent



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

if __name__ == "__main__":
    unittest.main()
