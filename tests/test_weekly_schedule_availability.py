import os
import unittest
from datetime import date, timedelta
import freezegun
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
            svc1 = Service(id=1, business_id=1, doctor_id=doc1.id, name="Dental Checkup", description="Exam", duration=30, price=2000.0)
            db.session.add(svc1)

        svc2 = db.session.get(Service, 2)
        if not svc2:
            svc2 = Service(id=2, business_id=1, doctor_id=doc1.id, name="Dental Cleaning", description="Cleaning", duration=45, price=4000.0)
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
        # 45 min service (Tooth Extraction, service_id=4 for doctor_id=1)
        res_45 = BookingService.check_availability(business_id=1, doctor_id=1, service_id=4, date_str=thurs_date)
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
        res = BookingService.check_availability(business_id=1, doctor_id=1, service_id=4, date_str=monday_date)
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

    def test_16_same_day_past_time_slots_filtered_with_lead_time_buffer(self):
        """16. When checking availability for today, already-passed time slots are filtered out."""
        # Dr. Ahmed is open on Monday 09:00 - 17:00. Freeze at 15:10 PKT on Monday 2026-08-31.
        with freezegun.freeze_time("2026-08-31 15:10:00+05:00"):
            res = BookingService.check_availability(1, doctor_id=1, date_str="2026-08-31")
            self.assertTrue(res["success"])
            # Cutoff with 15-min lead time is 15:25. Only slots >= 15:30 remain (15:30, 16:00, 16:30).
            self.assertEqual(res["available_slots"], ["15:30", "16:00", "16:30"])
            self.assertNotIn("09:00", res["available_slots"])
            self.assertNotIn("14:30", res["available_slots"])
            self.assertNotIn("15:00", res["available_slots"])

    def test_17_same_day_after_closing_returns_zero_slots_and_next_available_date(self):
        """17. When checking availability for today after closing hours, returns 0 slots with next available day."""
        # Freeze at 18:00 PKT on Monday (after Dr. Ahmed's 17:00 closing)
        with freezegun.freeze_time("2026-08-31 18:00:00+05:00"):
            res = BookingService.check_availability(1, doctor_id=1, date_str="2026-08-31")
            self.assertTrue(res["success"])
            self.assertEqual(res["available_slots"], [])
            self.assertTrue(res["is_closed"])
            self.assertEqual(res["next_available_date"], "2026-09-01")
            self.assertEqual(res["next_available_day"], "Tuesday")
            self.assertIn("today", res["results"][0].get("message", "").lower())

    def test_18_future_date_availability_unaffected_by_current_time(self):
        """18. Checking availability for a future date is unaffected by current time of day."""
        # Freeze at 18:00 PKT on Monday, but query Tuesday (tomorrow)
        with freezegun.freeze_time("2026-08-31 18:00:00+05:00"):
            res = BookingService.check_availability(1, doctor_id=1, date_str="2026-09-01")
            self.assertTrue(res["success"])
            self.assertIn("09:00", res["available_slots"])
            self.assertIn("10:00", res["available_slots"])
            self.assertIn("16:30", res["available_slots"])
            self.assertFalse(res["is_closed"])

    def test_19_doctor_to_dict_omits_stale_flat_hours_when_per_day_schedules_exist(self):
        """
        19. Regression: Doctor.to_dict() must NOT expose the legacy 09:00–17:00 flat
        columns as the current schedule when per-day DoctorSchedule entries exist with
        different hours (e.g. 17:00–21:30).

        Verifies:
        1. Doctor.to_dict() returns start_time=None, end_time=None.
        2. weekly_schedule contains the correct per-day Monday 17:00–21:30 entry.
        3. BookingService.get_doctors() surfaces the same correct weekly_schedule.
        4. The doctor-facing response does NOT contain the stale '09:00' / '17:00' hours.
        5. Availability calculation still uses the real DoctorSchedule values.
        6. A doctor WITHOUT per-day schedules still falls back to legacy start/end fields.
        """
        from services.booking_service import RequestCache
        from ai.response_generator import _format_doctors

        biz_id = 1

        # -- Create isolated test doctor with intentionally stale legacy flat hours --
        evening_doc = Doctor(
            business_id=biz_id,
            name="Dr. Evening Specialist",
            specialization="Orthodontics",
            working_days="Monday,Tuesday",  # legacy column — intentionally stale
            start_time="09:00",             # legacy column — intentionally stale
            end_time="17:00",               # legacy column — intentionally stale
            slot_interval=30,
            is_active=True,
        )
        db.session.add(evening_doc)
        db.session.flush()

        # Per-day schedules with evening hours (different from legacy 09:00–17:00)
        sched_mon = DoctorSchedule(
            doctor_id=evening_doc.id,
            day_of_week="Monday",
            is_available=True,
            start_time="17:00",
            end_time="21:30",
        )
        sched_tue = DoctorSchedule(
            doctor_id=evening_doc.id,
            day_of_week="Tuesday",
            is_available=True,
            start_time="17:00",
            end_time="21:30",
        )
        db.session.add_all([sched_mon, sched_tue])
        db.session.commit()
        RequestCache.clear()

        # 1. Doctor.to_dict() must return None for flat start_time / end_time
        doc_dict = evening_doc.to_dict()
        self.assertIsNone(
            doc_dict["start_time"],
            "start_time must be None when per-day DoctorSchedule entries exist"
        )
        self.assertIsNone(
            doc_dict["end_time"],
            "end_time must be None when per-day DoctorSchedule entries exist"
        )

        # 2. weekly_schedule must carry the correct 17:00–21:30 per-day hours
        self.assertTrue(len(doc_dict["weekly_schedule"]) > 0)
        mon_sched = next(
            (s for s in doc_dict["weekly_schedule"] if s["day_of_week"] == "Monday"), None
        )
        self.assertIsNotNone(mon_sched, "weekly_schedule must include Monday")
        self.assertEqual(mon_sched["start_time"], "17:00")
        self.assertEqual(mon_sched["end_time"], "21:30")
        self.assertTrue(mon_sched["is_available"])

        # 3. BookingService.get_doctors() must return correct per-day schedule & None flat hours
        doctors_list = BookingService.get_doctors(biz_id)
        doc_entry = next((d for d in doctors_list if d["id"] == evening_doc.id), None)
        self.assertIsNotNone(doc_entry, "Evening doctor must appear in get_doctors()")
        self.assertIsNone(doc_entry["start_time"])
        self.assertIsNone(doc_entry["end_time"])
        mon_entry = next(
            (s for s in doc_entry["weekly_schedule"] if s["day_of_week"] == "Monday"), None
        )
        self.assertIsNotNone(mon_entry)
        self.assertEqual(mon_entry["start_time"], "17:00")
        self.assertEqual(mon_entry["end_time"], "21:30")

        # 4. Doctor-facing response must NOT surface the stale 09:00–17:00 hours
        formatted = _format_doctors(
            {"doctors": [doc_entry]},
            lang="english",
            user_text_lower="what is dr evening specialist schedule on monday",
            conv_state={"selected_doctor_id": evening_doc.id},
        )
        self.assertNotIn(
            "09:00 AM – 05:00 PM", formatted,
            "Response must not show stale legacy 09:00–17:00 hours"
        )
        self.assertNotIn(
            "09:00 AM", formatted,
            "Response must not show stale legacy start time"
        )
        # Per-day schedule output should show 05:00 PM – 09:30 PM
        self.assertIn("05:00 PM – 09:30 PM", formatted)

        # 5. Availability calculation uses the real DoctorSchedule (17:00–21:30 on Monday)
        monday_date = self._get_next_date_for_day("Monday")
        res = BookingService.check_availability(
            business_id=biz_id,
            doctor_id=evening_doc.id,
            date_str=monday_date,
        )
        self.assertTrue(res["success"])
        self.assertIn("17:00", res["available_slots"], "Slots must start at 17:00")
        self.assertNotIn("09:00", res["available_slots"], "09:00 must NOT appear in slots")

        # 6. A doctor WITHOUT per-day schedules still uses legacy flat fields
        legacy_doc = Doctor(
            business_id=biz_id,
            name="Dr. Legacy Only",
            specialization="General Dentistry",
            working_days="Monday,Tuesday",
            start_time="08:00",
            end_time="14:00",
            slot_interval=30,
            is_active=True,
        )
        db.session.add(legacy_doc)
        db.session.commit()
        RequestCache.clear()

        legacy_dict = legacy_doc.to_dict()
        self.assertEqual(legacy_dict["start_time"], "08:00", "Legacy doctor must return its start_time")
        self.assertEqual(legacy_dict["end_time"], "14:00", "Legacy doctor must return its end_time")
        self.assertEqual(legacy_dict["weekly_schedule"], [])

    def test_20_today_availability_with_evening_schedule_returns_remaining_slots(self):
        """
        20. Regression: When a doctor works until late evening (e.g. 11:30 PM) on a working day
        and it is currently mid-evening (e.g. 6:30 PM), checking availability for today
        must NOT return zero slots. It must return all valid remaining slots after 6:30 PM (+ 15-min lead buffer),
        while excluding already-passed slots (09:00-18:30).
        """
        from services.booking_service import RequestCache

        biz_id = 1
        # Create doctor with Saturday schedule 09:00 - 23:30 (11:30 PM)
        evening_doc = Doctor(
            business_id=biz_id,
            name="Dr. Evening Surgeon",
            specialization="Oral Surgery",
            working_days="Saturday",
            start_time="09:00",
            end_time="23:30",
            slot_interval=30,
            is_active=True
        )
        db.session.add(evening_doc)
        db.session.flush()

        sched_sat = DoctorSchedule(
            doctor_id=evening_doc.id,
            day_of_week="Saturday",
            is_available=True,
            start_time="09:00",
            end_time="23:30"
        )
        db.session.add(sched_sat)
        db.session.commit()

        # Freeze clock to Saturday August 29, 2026 at 18:30 PKT (6:30 PM)
        with freezegun.freeze_time("2026-08-29 18:30:00+05:00"):
            RequestCache.clear()

            # 1. Direct check with explicit date
            res = BookingService.check_availability(biz_id, doctor_id=evening_doc.id, date_str="2026-08-29")
            self.assertTrue(res["success"])
            self.assertFalse(res["is_closed"])
            self.assertIsNone(res["next_available_date"])

            # Valid remaining slots: 19:00 to 23:00 (cutoff is 18:45)
            self.assertIn("19:00", res["available_slots"])
            self.assertIn("20:00", res["available_slots"])
            self.assertIn("23:00", res["available_slots"])

            # Past slots must be excluded
            self.assertNotIn("09:00", res["available_slots"])
            self.assertNotIn("12:00", res["available_slots"])
            self.assertNotIn("18:00", res["available_slots"])
            self.assertNotIn("18:30", res["available_slots"])

            # 2. Check with relative date_str="today"
            res_today = BookingService.check_availability(biz_id, doctor_id=evening_doc.id, date_str="today")
            self.assertTrue(res_today["success"])
            self.assertFalse(res_today["is_closed"])
            self.assertEqual(res_today["available_slots"], res["available_slots"])

    def test_21_today_availability_with_12hour_ampm_schedule_format(self):
        """
        21. Regression: When DoctorSchedule has start/end times in 12-hour format (e.g. '09:00 AM' - '11:30 PM'),
        check_availability and book_appointment must parse them correctly without silently defaulting to 17:00.
        """
        from services.booking_service import RequestCache

        biz_id = 1
        ampm_doc = Doctor(
            business_id=biz_id,
            name="Dr. AM-PM Specialist",
            specialization="General Dentistry",
            working_days="Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        db.session.add(ampm_doc)
        db.session.flush()

        sched_sat = DoctorSchedule(
            doctor_id=ampm_doc.id,
            day_of_week="Saturday",
            is_available=True,
            start_time="09:00 AM",
            end_time="11:30 PM"
        )
        db.session.add(sched_sat)
        db.session.commit()

        with freezegun.freeze_time("2026-08-29 18:30:00+05:00"):
            RequestCache.clear()
            res = BookingService.check_availability(biz_id, doctor_id=ampm_doc.id, date_str="2026-08-29")
            self.assertTrue(res["success"])
            self.assertFalse(res["is_closed"])
            self.assertIn("19:00", res["available_slots"])
            self.assertIn("23:00", res["available_slots"])
            self.assertNotIn("09:00", res["available_slots"])


if __name__ == "__main__":
    unittest.main()
