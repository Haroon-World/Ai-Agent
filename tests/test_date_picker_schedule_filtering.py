import unittest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Conversation, Message, Customer, Appointment, DoctorSchedule, DoctorLeave
from ai.agent import Agent, _build_ui_action
from services.booking_service import BookingService


class TestDatePickerConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-secret-datepicker"
    LLM_PROVIDER = "mock"


class TestDatePickerScheduleFiltering(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestDatePickerConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()

        # Update Dr. Ahmed Khan: Works ONLY Monday, Friday, Saturday (Tue, Wed, Thu, Sun Closed)
        dr_ahmed = db.session.get(Doctor, 1)
        if dr_ahmed:
            dr_ahmed.working_days = "Monday,Friday,Saturday"
            DoctorSchedule.query.filter_by(doctor_id=1).delete()
            ahmed_schedules = [
                DoctorSchedule(doctor_id=1, day_of_week="Monday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=1, day_of_week="Tuesday", is_available=False, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=1, day_of_week="Wednesday", is_available=False, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=1, day_of_week="Thursday", is_available=False, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=1, day_of_week="Friday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=1, day_of_week="Saturday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=1, day_of_week="Sunday", is_available=False, start_time="09:00", end_time="17:00"),
            ]
            db.session.add_all(ahmed_schedules)

        # Update Dr. Sara Malik: Works Monday-Saturday (Sun Closed)
        dr_sara = db.session.get(Doctor, 2)
        if dr_sara:
            dr_sara.working_days = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
            DoctorSchedule.query.filter_by(doctor_id=2).delete()
            sara_schedules = [
                DoctorSchedule(doctor_id=2, day_of_week="Monday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=2, day_of_week="Tuesday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=2, day_of_week="Wednesday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=2, day_of_week="Thursday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=2, day_of_week="Friday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=2, day_of_week="Saturday", is_available=True, start_time="09:00", end_time="17:00"),
                DoctorSchedule(doctor_id=2, day_of_week="Sunday", is_available=False, start_time="09:00", end_time="17:00"),
            ]
            db.session.add_all(sara_schedules)

        db.session.commit()
        from services.booking_service import RequestCache
        RequestCache.clear()
        self.agent = Agent(business_id=1, llm_provider="mock")

    def tearDown(self):
        from services.booking_service import RequestCache
        RequestCache.clear()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_1_dr_ahmed_actual_schedule_date_picker(self):
        """
        Regression Test 1: Dr Ahmed with his actual schedule (Monday, Friday, Saturday).
        Date picker options MUST only contain Mondays, Fridays, and Saturdays.
        MUST NOT contain Tuesday, Wednesday, Thursday, or Sunday.
        """
        conv = Conversation(
            business_id=1,
            status="AI",
            intent="BOOK_APPOINTMENT",
            workflow_state="COLLECTING_INFO",
            selected_service_id=1,
            selected_doctor_id=1,
            awaiting_input="date_choice"
        )
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action["type"], "date_selection")

        options = ui_action["options"]
        self.assertEqual(len(options), 5)

        for opt in options:
            day_name = opt["day"]
            self.assertIn(day_name, ["Monday", "Friday", "Saturday"], f"Invalid day {day_name} shown for Dr Ahmed!")
            self.assertNotIn(day_name, ["Tuesday", "Wednesday", "Thursday", "Sunday"])

    def test_2_dr_sara_actual_schedule_date_picker(self):
        """
        Regression Test 2: Dr Sara with her actual schedule (Monday-Saturday).
        Date picker options MUST contain Monday through Saturday, but MUST NOT contain Sunday.
        """
        conv = Conversation(
            business_id=1,
            status="AI",
            intent="BOOK_APPOINTMENT",
            workflow_state="COLLECTING_INFO",
            selected_service_id=1,
            selected_doctor_id=2,
            awaiting_input="date_choice"
        )
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action["type"], "date_selection")

        options = ui_action["options"]
        self.assertEqual(len(options), 5)

        for opt in options:
            day_name = opt["day"]
            self.assertIn(day_name, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"])
            self.assertNotEqual(day_name, "Sunday", "Sunday must not be shown for Dr Sara!")

    def test_3_sunday_off_day_rejected_by_backend(self):
        """
        Regression Test 3: Backend availability and booking validation correctly rejects Sunday.
        """
        tz = ZoneInfo("Asia/Karachi")
        today = datetime.now(tz).date()
        days_ahead = (6 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_sunday = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        # Dr. Ahmed on Sunday -> 0 slots, closed
        avail_ahmed = BookingService.check_availability(business_id=1, doctor_id=1, date_str=next_sunday)
        self.assertEqual(len(avail_ahmed["available_slots"]), 0)
        self.assertTrue(avail_ahmed["is_closed"])

        # Dr. Sara on Sunday -> 0 slots, closed
        avail_sara = BookingService.check_availability(business_id=1, doctor_id=2, date_str=next_sunday)
        self.assertEqual(len(avail_sara["available_slots"]), 0)
        self.assertTrue(avail_sara["is_closed"])

        # Booking on Sunday must be rejected
        book_res = BookingService.book_appointment(
            business_id=1,
            customer_name="Ali",
            customer_phone="03001234567",
            doctor_id=1,
            service_id=1,
            appointment_date=next_sunday,
            appointment_time="10:00"
        )
        self.assertFalse(book_res["success"])
        self.assertIn("closed", book_res["error"].lower())

    def test_4_working_day_with_no_available_slots(self):
        """
        Regression Test 4: Working day with no available slots (all-day leave or fully booked).
        Date picker shows the working day, but check_availability reports no available slots.
        """
        tz = ZoneInfo("Asia/Karachi")
        today = datetime.now(tz).date()
        # Find next Monday (Dr. Ahmed's working day)
        days_ahead = (0 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_monday = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        # Put Dr Ahmed on all-day leave on that Monday
        leave = DoctorLeave(
            doctor_id=1,
            leave_date=next_monday,
            is_all_day=True,
            reason="Medical Conference"
        )
        db.session.add(leave)
        db.session.commit()

        # check_availability returns 0 slots
        avail = BookingService.check_availability(business_id=1, doctor_id=1, date_str=next_monday)
        self.assertEqual(len(avail["available_slots"]), 0)

        # booking attempt is rejected
        book_res = BookingService.book_appointment(
            business_id=1,
            customer_name="Ali",
            customer_phone="03001234567",
            doctor_id=1,
            service_id=1,
            appointment_date=next_monday,
            appointment_time="10:00"
        )
        self.assertFalse(book_res["success"])
        self.assertIn("leave", book_res["error"].lower())

    def test_5_working_day_with_available_slots(self):
        """
        Regression Test 5: Working day with available slots returns valid slot list and succeeds booking.
        """
        tz = ZoneInfo("Asia/Karachi")
        today = datetime.now(tz).date()
        # Find next Friday (Dr. Ahmed's working day)
        days_ahead = (4 - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        next_friday = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        avail = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=next_friday)
        self.assertTrue(avail["success"])
        self.assertGreater(len(avail["available_slots"]), 0)
        self.assertIn("09:00", avail["available_slots"])
        self.assertIn("10:00", avail["available_slots"])

        book_res = BookingService.book_appointment(
            business_id=1,
            customer_name="Ali",
            customer_phone="03001234567",
            doctor_id=1,
            service_id=1,
            appointment_date=next_friday,
            appointment_time="10:00"
        )
        self.assertTrue(book_res["success"])
        self.assertEqual(book_res["appointment"]["status"], "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
