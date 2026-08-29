import unittest
from datetime import date, timedelta
from app import create_app
from models import db, Business, Doctor, Service, Appointment, Conversation
from services.booking_service import BookingService
from ai.agent import Agent

from config.config import Config

class TestDoctorSchedulesAndTypoIntent(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()

        self.app_context = self.app.app_context()
        self.app_context.push()

        db.create_all()
        self._seed_test_data()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _seed_test_data(self):
        clinic = db.session.get(Business, 1)
        if not clinic:
            clinic = Business(
                id=1,
                name="Test Dental Clinic",
                business_type="dental_clinic",
                address="123 Test St",
                phone="+92 42 12345678",
                timezone="Asia/Karachi",
                opening_hours="09:00 AM - 05:00 PM"
            )
            db.session.add(clinic)
            db.session.flush()

        doc = db.session.get(Doctor, 1)
        if doc:
            doc.working_days = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday"
            doc.start_time = "09:00"
            doc.end_time = "17:00"
            doc.slot_interval = 30
            doc.break_start_time = "13:00"
            doc.break_end_time = "14:00"
            doc.is_active = True
            for s in doc.schedules:
                s.is_available = True
                s.start_time = "09:00"
                s.end_time = "17:00"
        else:
            doc = Doctor(
                id=1,
                business_id=1,
                name="Dr. Test Dentist",
                specialization="General Dentistry",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
                start_time="09:00",
                end_time="17:00",
                slot_interval=30,
                break_start_time="13:00",
                break_end_time="14:00",
                is_active=True
            )
            db.session.add(doc)
            db.session.flush()
            from models import DoctorSchedule, DAYS_OF_WEEK
            for day in DAYS_OF_WEEK:
                db.session.add(DoctorSchedule(doctor_id=doc.id, day_of_week=day, is_available=True, start_time="09:00", end_time="17:00"))

        svc = db.session.get(Service, 1)
        if not svc:
            svc = Service(
                id=1,
                business_id=1,
                name="Checkup",
                description="Regular Checkup",
                duration=30,
                price=2000.0
            )
            db.session.add(svc)

        db.session.commit()

    def test_typo_intent_matching(self):
        """Test that user queries with typos like 'i want an appoinment, tell me schdeule' trigger check_availability."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "i want an appoinment for tomorrow, tell me schdeule")

        self.assertEqual(res["status"], "AI")
        # Check that check_availability tool was executed
        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertIn("check_availability", executed_tool_names)
        self.assertNotIn("Sorry, I didn't quite catch that", res["content"])

    def test_doctor_break_and_slot_gaps(self):
        """Test that availability accounts for doctor slot_interval (30m) and excludes break times (13:00-14:00)."""
        from tests.test_date_helpers import get_next_open_weekday
        target_date = get_next_open_weekday(1, doctor_id=1)
        avail = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=target_date)

        self.assertIn("results", avail)
        doc_res = avail["results"][0]
        slots = doc_res["available_slots"]

        # Ensure break time slots (13:00, 13:30) are excluded
        self.assertNotIn("13:00", slots)
        self.assertNotIn("13:30", slots)
        self.assertIn("09:00", slots)
        self.assertIn("09:30", slots)
        self.assertIn("14:00", slots)

    def test_booking_and_cancellation_slot_recovery(self):
        """Test that booking reserves a slot, and cancelling immediately frees up the slot for other clients."""
        from tests.test_date_helpers import get_next_open_weekday
        target_date = get_next_open_weekday(1, doctor_id=1)
        chosen_time = "10:00"

        # 1. Book appointment
        book_res = BookingService.book_appointment(
            business_id=1,
            customer_name="Ali Khan",
            customer_phone="03001234567",
            doctor_id=1,
            service_id=1,
            appointment_date=target_date,
            appointment_time=chosen_time
        )
        self.assertTrue(book_res["success"])
        appt_id = book_res["appointment_id"]

        # 2. Check availability -> 10:00 must be blocked
        avail_after_book = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=target_date)
        slots_after_book = avail_after_book["results"][0]["available_slots"]
        self.assertNotIn(chosen_time, slots_after_book)

        # 3. Cancel appointment
        cancel_res = BookingService.cancel_appointment(business_id=1, appointment_id=appt_id, reason="Client change of plans")
        self.assertTrue(cancel_res["success"])

        # 4. Check availability -> 10:00 must now be FREED and available again!
        avail_after_cancel = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=target_date)
        slots_after_cancel = avail_after_cancel["results"][0]["available_slots"]
        self.assertIn(chosen_time, slots_after_cancel)

    def test_admin_doctor_management_routes(self):
        """Test Admin routes for editing doctor schedules and toggling status."""
        # Login admin
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_user"] = "admin"

        # Edit Dr. Test Dentist schedule
        res = self.client.post("/admin/doctors/edit/1", data={
            "name": "Dr. Test Dentist Updated",
            "specialization": "Orthodontics",
            "working_days": ["Monday", "Tuesday", "Wednesday"],
            "start_time": "10:00",
            "end_time": "16:00",
            "slot_interval": "45",
            "break_start_time": "12:00",
            "break_end_time": "13:00",
            "is_active": "on"
        }, follow_redirects=True)

        self.assertEqual(res.status_code, 200)

        # Verify DB updated
        doc = db.session.get(Doctor, 1)
        self.assertEqual(doc.name, "Dr. Test Dentist Updated")
        self.assertEqual(doc.specialization, "Orthodontics")
        self.assertEqual(doc.start_time, "10:00")
        self.assertEqual(doc.slot_interval, 45)
        self.assertEqual(doc.break_start_time, "12:00")

if __name__ == "__main__":
    unittest.main()
