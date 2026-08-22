import os
import unittest
from datetime import date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Appointment, DoctorSchedule, DoctorLeave, Conversation
from services.booking_service import BookingService
from ai.agent import Agent

class TestWeeklyScheduleAndAvailabilityEngine(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
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
                name="SmileCare Dental Clinic",
                business_type="dental_clinic",
                address="Plot 42-B, Main Boulevard",
                phone="+92 42 35789000",
                timezone="Asia/Karachi",
                opening_hours="09:00 AM - 05:00 PM"
            )
            db.session.add(clinic)

        doc1 = db.session.get(Doctor, 1)
        if doc1:
            doc1.working_days = "Monday,Tuesday,Thursday,Friday,Saturday"
            doc1.start_time = "09:00"
            doc1.end_time = "17:00"
            doc1.slot_interval = 30
            doc1.is_active = True
        else:
            doc1 = Doctor(
                id=1,
                business_id=1,
                name="Dr. Ahmed Khan",
                specialization="General Dentistry",
                working_days="Monday,Tuesday,Thursday,Friday,Saturday",
                start_time="09:00",
                end_time="17:00",
                slot_interval=30,
                is_active=True
            )
            db.session.add(doc1)
            db.session.flush()

        # Delete old schedules and add explicit DoctorSchedule entries for Dr. Ahmed Khan
        DoctorSchedule.query.filter_by(doctor_id=doc1.id).delete()
        schedules = [
            DoctorSchedule(doctor_id=doc1.id, day_of_week="Monday", is_available=True, start_time="09:00", end_time="17:00"),
            DoctorSchedule(doctor_id=doc1.id, day_of_week="Tuesday", is_available=True, start_time="09:00", end_time="17:00"),
            DoctorSchedule(doctor_id=doc1.id, day_of_week="Wednesday", is_available=False, start_time="09:00", end_time="17:00"),
            DoctorSchedule(doctor_id=doc1.id, day_of_week="Thursday", is_available=True, start_time="10:00", end_time="16:00"),
            DoctorSchedule(doctor_id=doc1.id, day_of_week="Friday", is_available=True, start_time="09:00", end_time="17:00"),
            DoctorSchedule(doctor_id=doc1.id, day_of_week="Saturday", is_available=True, start_time="09:00", end_time="13:00"),
            DoctorSchedule(doctor_id=doc1.id, day_of_week="Sunday", is_available=False, start_time="09:00", end_time="17:00"),
        ]
        for s in schedules:
            db.session.add(s)

        svc1 = db.session.get(Service, 1)
        if not svc1:
            svc1 = Service(id=1, business_id=1, name="Dental Checkup", description="Exam", duration=30, price=2000.0)
            db.session.add(svc1)

        svc2 = db.session.get(Service, 2)
        if not svc2:
            svc2 = Service(id=2, business_id=1, name="Dental Cleaning", description="Cleaning", duration=45, price=4000.0)
            db.session.add(svc2)

        db.session.commit()

    def _get_next_date_for_day(self, day_name: str) -> str:
        """Find the next future date string for a given day of the week."""
        d = date.today() + timedelta(days=1)
        while d.strftime("%A") != day_name:
            d += timedelta(days=1)
        return d.strftime("%Y-%m-%d")

    def test_1_doctor_available_monday_schedule(self):
        """1. Doctor available Monday 09:00-17:00 returns valid slots."""
        monday_date = self._get_next_date_for_day("Monday")
        res = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=monday_date)
        self.assertTrue(res["success"])
        self.assertIn("09:00", res["available_slots"])
        self.assertIn("16:30", res["available_slots"])

    def test_2_doctor_unavailable_wednesday(self):
        """2. Doctor unavailable Wednesday returns empty slots and closed message."""
        wed_date = self._get_next_date_for_day("Wednesday")
        res = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=wed_date)
        self.assertTrue(res["success"])
        self.assertEqual(len(res["available_slots"]), 0)
        self.assertIn("closed", res["results"][0]["message"].lower())

    def test_3_service_duration_affects_slot_generation(self):
        """3. Service duration (45m vs 30m) correctly affects slot end calculation."""
        thurs_date = self._get_next_date_for_day("Thursday") # Thursday works 10:00 to 16:00
        # 30 min service
        res_30 = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=thurs_date)
        # 45 min service
        res_45 = BookingService.check_availability(business_id=1, doctor_id=1, service_id=2, date_str=thurs_date)
        self.assertEqual(res_30["duration_minutes"], 30)
        self.assertEqual(res_45["duration_minutes"], 45)
        # For 45 min service starting 10:00, slots: 10:00, 10:30... 15:00 (ends 15:45 <= 16:00)
        self.assertIn("10:00", res_45["available_slots"])
        self.assertIn("15:00", res_45["available_slots"])

    def test_4_existing_appointment_removes_corresponding_slot(self):
        """4. Existing appointment removes corresponding slot from availability."""
        monday_date = self._get_next_date_for_day("Monday")
        # Book 10:00 appointment
        book_res = BookingService.book_appointment(
            business_id=1,
            customer_name="Ali",
            customer_phone="03001112233",
            doctor_id=1,
            service_id=1, # 30 mins
            appointment_date=monday_date,
            appointment_time="10:00"
        )
        self.assertTrue(book_res["success"])

        # Check availability again
        res = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=monday_date)
        self.assertNotIn("10:00", res["available_slots"])
        self.assertIn("09:30", res["available_slots"])

    def test_5_cancelled_appointment_makes_slot_available_again(self):
        """5. Cancelled appointment makes the slot available again instantly."""
        monday_date = self._get_next_date_for_day("Monday")
        book_res = BookingService.book_appointment(
            business_id=1, customer_name="Ali", customer_phone="03001112233",
            doctor_id=1, service_id=1, appointment_date=monday_date, appointment_time="10:00"
        )
        appt_id = book_res["appointment_id"]

        # Cancel appointment
        cancel_res = BookingService.cancel_appointment(business_id=1, appointment_id=appt_id)
        self.assertTrue(cancel_res["success"])

        # Check availability -> 10:00 slot is back!
        res = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=monday_date)
        self.assertIn("10:00", res["available_slots"])

    def test_6_doctor_leave_removes_slots(self):
        """6. Doctor leave / blocked period removes slots."""
        monday_date = self._get_next_date_for_day("Monday")
        leave = DoctorLeave(doctor_id=1, leave_date=monday_date, is_all_day=True, reason="Vacation")
        db.session.add(leave)
        db.session.commit()

        res = BookingService.check_availability(business_id=1, doctor_id=1, service_id=1, date_str=monday_date)
        self.assertEqual(len(res["available_slots"]), 0)

    def test_7_slot_outside_working_hours_cannot_be_booked(self):
        """7. Slot outside doctor's working hours is rejected during booking validation."""
        thurs_date = self._get_next_date_for_day("Thursday") # Thursday works 10:00-16:00
        res = BookingService.book_appointment(
            business_id=1, customer_name="Ali", customer_phone="03001112233",
            doctor_id=1, service_id=1, appointment_date=thurs_date, appointment_time="09:00" # Outside working hours!
        )
        self.assertFalse(res["success"])
        self.assertIn("outside", res["error"].lower())

    def test_8_two_customers_cannot_book_same_slot(self):
        """8. Two customers cannot book the same slot (second booking rejected)."""
        monday_date = self._get_next_date_for_day("Monday")
        res1 = BookingService.book_appointment(
            business_id=1, customer_name="User 1", customer_phone="03001111111",
            doctor_id=1, service_id=1, appointment_date=monday_date, appointment_time="11:00"
        )
        self.assertTrue(res1["success"])

        res2 = BookingService.book_appointment(
            business_id=1, customer_name="User 2", customer_phone="03002222222",
            doctor_id=1, service_id=1, appointment_date=monday_date, appointment_time="11:00"
        )
        self.assertFalse(res2["success"])
        self.assertTrue("already booked" in res2["error"].lower() or "overlap" in res2["error"].lower())

    def test_9_check_availability_returns_db_generated_slots(self):
        """9. check_availability returns actual DB-generated slots matching schema."""
        monday_date = self._get_next_date_for_day("Monday")
        res = BookingService.check_availability(business_id=1, doctor_id=1, service_id=2, date_str=monday_date)
        self.assertTrue(res["success"])
        self.assertEqual(res["doctor"], "Dr. Ahmed Khan")
        self.assertEqual(res["duration_minutes"], 45)
        self.assertIsInstance(res["available_slots"], list)

    def test_10_book_appointment_validates_availability_again(self):
        """10. book_appointment validates availability independently before saving."""
        monday_date = self._get_next_date_for_day("Monday")
        # Block doctor with all-day leave
        leave = DoctorLeave(doctor_id=1, leave_date=monday_date, is_all_day=True, reason="Conference")
        db.session.add(leave)
        db.session.commit()

        # Attempt to book
        res = BookingService.book_appointment(
            business_id=1, customer_name="Tariq", customer_phone="03003334444",
            doctor_id=1, service_id=1, appointment_date=monday_date, appointment_time="10:00"
        )
        self.assertFalse(res["success"])
        self.assertIn("leave", res["error"].lower())

    def test_11_ai_does_not_claim_booking_succeeded_when_backend_rejects(self):
        """11. Agent response handling reflects backend error when booking fails."""
        monday_date = self._get_next_date_for_day("Monday")
        # Pre-book 10:00 slot
        BookingService.book_appointment(
            business_id=1, customer_name="User 1", customer_phone="03001111111",
            doctor_id=1, service_id=1, appointment_date=monday_date, appointment_time="10:00"
        )

        # Try booking same slot again via agent
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        # Attempt booking pre-booked 10:00 slot
        res = agent.process_message(conv.id, f"Book appointment with Dr. Ahmed on {monday_date} at 10:00 for Dental Checkup, name Tariq, phone 03009998877")
        self.assertNotIn("🎉 **Your appointment is confirmed!**", res["content"])

    def test_12_multi_turn_booking_flow(self):
        """12. Multi-turn booking flow works naturally step by step."""
        monday_date = self._get_next_date_for_day("Monday")
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        r1 = agent.process_message(conv.id, f"I want to see Dr. Ahmed on {monday_date} at 10:00")
        self.assertEqual(r1["status"], "AI")

        r2 = agent.process_message(conv.id, "Cleaning")
        self.assertEqual(r2["status"], "AI")

        r3 = agent.process_message(conv.id, "Name is Tariq")
        self.assertEqual(r3["status"], "AI")

        r4 = agent.process_message(conv.id, "03001234567")
        self.assertEqual(r4["status"], "AI")
        self.assertTrue("confirmed" in r4["content"].lower() or "booking" in r4["content"].lower() or "appointment" in r4["content"].lower())

    def test_13_customer_name_and_phone_collected_separately_preserved(self):
        """13. Customer name and phone collected on separate turns are preserved."""
        monday_date = self._get_next_date_for_day("Monday")
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        agent.process_message(conv.id, f"Check availability for Dr. Ahmed on {monday_date}")
        agent.process_message(conv.id, "Cleaning")
        agent.process_message(conv.id, "Name is Haroon")

        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.pending_customer_name, "Haroon")

        agent.process_message(conv.id, "03197155071")
        conv_db_final = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db_final.pending_customer_phone, "03197155071")

    def test_14_real_provider_credentials_check(self):
        """14. Check if real provider credentials (Gemini / Groq) are available."""
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        has_credentials = bool(gemini_key or groq_key)
        # Informational check for test suite
        if not has_credentials:
            print("\n[INFO] REAL PROVIDER E2E TEST BLOCKED — API credentials unavailable.")

    def test_15_mock_adapter_deterministic_fallback(self):
        """15. MockAdapter operates as a deterministic testing fallback."""
        agent = Agent(business_id=1, llm_provider="mock")
        self.assertEqual(agent.llm_client.provider, "mock")

if __name__ == "__main__":
    unittest.main()
