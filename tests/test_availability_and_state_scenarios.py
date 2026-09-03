import unittest
import os
import sys
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Appointment, DoctorSchedule, DoctorLeave, Conversation
from services.booking_service import BookingService
from ai.agent import Agent, resolve_date_string
from seed import seed_database


class TestAvailabilityAndStateScenarios(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.app.config["LLM_PROVIDER"] = "mock"
        self.ctx = self.app.app_context()
        self.ctx.push()

        db.create_all()
        seed_database(self.app)

        self.biz_id = Config.DEFAULT_BUSINESS_ID
        self.agent = Agent(business_id=self.biz_id, llm_provider="mock")

        # Set Dr. Sara (ID 2): Mon-Sat 09:00-17:00, Sunday CLOSED
        dr_sara = db.session.get(Doctor, 2)
        if dr_sara:
            dr_sara.working_days = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
            dr_sara.start_time = "09:00"
            dr_sara.end_time = "17:00"
            for sched in dr_sara.schedules:
                if sched.day_of_week == "Sunday":
                    sched.is_available = False
                else:
                    sched.is_available = True
                    sched.start_time = "09:00"
                    sched.end_time = "17:00"
            db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_1_doctors_availability_inquiry(self):
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "Doctors availability")
        self.assertTrue(res.get("content"))
        self.assertNotIn("error", res)

    def test_2_doctor_selection(self):
        conv = Conversation(business_id=self.biz_id, status="AI", intent="BOOK_APPOINTMENT")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "Dr Sara")
        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.selected_doctor_id, 2, "Dr Sara (ID 2) must be selected")

    def test_3_show_her_available_slots_without_date(self):
        conv = Conversation(business_id=self.biz_id, status="AI", intent="BOOK_APPOINTMENT", selected_doctor_id=2)
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "first tell me slot")
        self.assertTrue(res.get("content"))
        self.assertNotIn("error", res)

    def test_4_tomorrow_date_resolution_and_closed_day_explanation(self):
        conv = Conversation(business_id=self.biz_id, status="AI", intent="BOOK_APPOINTMENT", selected_doctor_id=2)
        db.session.add(conv)
        db.session.commit()

        # Resolve date "tomorrow"
        parsed = resolve_date_string("tomorrow", self.biz_id)
        self.assertIsNotNone(parsed)

        # Check availability for a Sunday
        today = datetime.now(ZoneInfo("Asia/Karachi")).date()
        days_to_sunday = (6 - today.weekday()) % 7
        if days_to_sunday == 0:
            days_to_sunday = 7
        sunday_date = (today + timedelta(days=days_to_sunday)).strftime("%Y-%m-%d")

        avail = BookingService.check_availability(self.biz_id, doctor_id=2, date_str=sunday_date)
        self.assertTrue(avail["success"])
        self.assertTrue(avail["is_closed"])
        self.assertEqual(avail["available_slots"], [])
        self.assertIsNotNone(avail["next_available_date"])

    def test_5_monday_overwrites_requested_date_to_aug_24(self):
        conv = Conversation(business_id=self.biz_id, status="AI", intent="BOOK_APPOINTMENT", selected_doctor_id=2, requested_date="2026-08-23")
        db.session.add(conv)
        db.session.commit()

        # User says "Monday"
        res = self.agent.process_message(conv.id, "Monday")
        conv_db = db.session.get(Conversation, conv.id)

        parsed_mon = resolve_date_string("Monday", self.biz_id)
        self.assertEqual(conv_db.requested_date, parsed_mon, f"requested_date must update to {parsed_mon}")
        self.assertNotEqual(conv_db.requested_date, "2026-08-23", "Must overwrite previous Sunday date")

    def test_6_bare_time_slot_selection(self):
        from tests.test_date_helpers import get_next_open_weekday
        future_working_date = get_next_open_weekday(self.biz_id, doctor_id=2)
        conv = Conversation(
            business_id=self.biz_id,
            status="AI",
            intent="BOOK_APPOINTMENT",
            selected_doctor_id=2,
            selected_service_id=2,
            requested_date=future_working_date,
            awaiting_input="time_choice"
        )
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "9:30")
        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.requested_time, "09:30", "Bare '9:30' must update requested_time to 09:30")

    def test_7_compound_intent_service_doctor_date(self):
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "I need cleaning with Sara on Monday.")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_doctor_id, 2, "Dr. Sara (ID 2) must be extracted")
        self.assertIsNotNone(conv_db.selected_service_id, "Dental cleaning service must be extracted")
        parsed_mon = resolve_date_string("Monday", self.biz_id)
        self.assertEqual(conv_db.requested_date, parsed_mon, f"Monday date must resolve to {parsed_mon}")

    def test_8_admin_schedule_change_reflects_immediately(self):
        # Update Dr Sara Monday hours to 11:00-15:00
        sched = DoctorSchedule.query.filter_by(doctor_id=2, day_of_week="Monday").first()
        if sched:
            sched.start_time = "11:00"
            sched.end_time = "15:00"
            sched.is_available = True
            db.session.commit()

        # Find next Monday
        today = datetime.now(ZoneInfo("Asia/Karachi")).date()
        days_to_mon = (0 - today.weekday()) % 7
        if days_to_mon == 0:
            days_to_mon = 7
        mon_date = (today + timedelta(days=days_to_mon)).strftime("%Y-%m-%d")

        avail = BookingService.check_availability(self.biz_id, doctor_id=2, date_str=mon_date)
        self.assertTrue(avail["success"])
        slots = avail["available_slots"]
        self.assertTrue(len(slots) > 0)
        self.assertEqual(slots[0], "11:00", f"First slot must start at 11:00, got {slots[0]}")
        self.assertTrue(all("11:00" <= s < "15:00" for s in slots), f"All slots must fall within 11:00-15:00: {slots}")

    def test_9_doctor_leave_date_blocks_slots(self):
        # Find next Monday for doctor leave
        today = datetime.now(ZoneInfo("Asia/Karachi")).date()
        days_to_mon = (0 - today.weekday()) % 7
        if days_to_mon == 0:
            days_to_mon = 7
        leave_date = (today + timedelta(days=days_to_mon)).strftime("%Y-%m-%d")

        leave = DoctorLeave(doctor_id=2, leave_date=leave_date, is_all_day=True, reason="Vacation")
        db.session.add(leave)
        db.session.commit()

        avail = BookingService.check_availability(self.biz_id, doctor_id=2, date_str=leave_date)
        self.assertTrue(avail["success"])
        self.assertEqual(avail["available_slots"], [], "Leave date must have 0 slots")
        self.assertTrue(avail["is_closed"])

    def test_10_existing_appointment_blocks_slot(self):
        # Find next Tuesday for appointment blocking test
        today = datetime.now(ZoneInfo("Asia/Karachi")).date()
        days_to_tue = (1 - today.weekday()) % 7
        if days_to_tue == 0:
            days_to_tue = 7
        target_date = (today + timedelta(days=days_to_tue)).strftime("%Y-%m-%d")

        # Create customer & appointment at 10:00
        cust = Customer(business_id=self.biz_id, name="Test Patient", phone="03001112233")
        db.session.add(cust)
        db.session.flush()

        appt = Appointment(
            business_id=self.biz_id,
            customer_id=cust.id,
            doctor_id=2,
            service_id=1,
            appointment_date=target_date,
            appointment_time="10:00",
            status="CONFIRMED"
        )
        db.session.add(appt)
        db.session.commit()

        avail = BookingService.check_availability(self.biz_id, doctor_id=2, date_str=target_date)
        self.assertTrue(avail["success"])
        self.assertNotIn("10:00", avail["available_slots"], "10:00 slot must be removed because of existing appointment")


if __name__ == "__main__":
    unittest.main()
