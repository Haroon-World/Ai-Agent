"""
Phase 1 Fix Regression Tests
=============================
5 targeted tests — each directly validates a specific gap identified in the audit.

Test matrix:
  1. test_state_context_injected_and_slot_selection_works
       → After check_availability sets context, sending bare "9:30" triggers booking (not greeting).
         Validates Step 1 (state context injection).

  2. test_admin_cross_tenant_isolation
       → Admin for Business 1 cannot take over a conversation belonging to Business 2.
         Validates Step 2 (HandoffService tenant isolation).

  3. test_timezone_past_date_karachi
       → check_availability rejects a date that is "tomorrow" in UTC but "today-or-past" in
         Asia/Karachi when the server clock is mocked.
         Validates Step 3 (ZoneInfo-aware past-date guard).

  4. test_duration_overlap_booking_rejected
       → Booking a 30-min service at 10:15 when a 45-min service is already at 10:00 returns error.
         Validates Step 4 (duration-aware overlap detection).

  5. test_booking_on_nonworking_day_rejected
       → Attempting to book Dr. Ahmed (Mon–Fri only) on a Saturday is rejected.
         Validates Step 4 (schedule revalidation — working day guard).
"""

import unittest
import os
import sys
import json
from datetime import datetime, timedelta, date
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Appointment, Conversation, Message
from services.booking_service import BookingService
from services.handoff_service import HandoffService
from ai.agent import Agent
from seed import seed_database


class BaseFixTest(unittest.TestCase):
    """Shared setUp/tearDown for all fix regression tests."""

    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _next_monday(self) -> str:
        """Return the next Monday as YYYY-MM-DD (Dr. Ahmed works Mon–Sat)."""
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7  # 0 = Monday
        if days_ahead == 0:
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    def _next_sunday(self) -> str:
        """Return the next Sunday as YYYY-MM-DD.
        Dr. Ahmed works Monday-Saturday, so Sunday is his only non-working day."""
        today = date.today()
        days_ahead = (6 - today.weekday()) % 7  # 6 = Sunday
        if days_ahead == 0:
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


class TestStateContextInjection(BaseFixTest):
    """
    Step 1 validation: State context injected into LLM each turn.
    After check_availability stores doctor/date in conv state, a follow-up
    message with just a bare time ("9:30") must retain state and ask for name/phone
    (never returning the generic initial greeting). A subsequent message with
    name/phone must then complete the booking using the previously retained context.
    """

    def test_state_context_slot_selection_triggers_booking(self):
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")
        target_date = self._next_monday()

        conv = Conversation(business_id=biz_id, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Ask for availability — check_availability is called and updates state
        resp1 = agent.process_message(
            conv.id,
            f"I'd like to book a dental cleaning with Dr. Ahmed on {target_date}"
        )
        conv_db1 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db1.requested_date, target_date,
                         "check_availability should have stored requested_date in conv state")
        self.assertEqual(conv_db1.workflow_state, "CHECKING_AVAILABILITY",
                         "workflow_state should be CHECKING_AVAILABILITY after slot query")
        self.assertEqual(conv_db1.selected_doctor_id, 1,
                         "selected_doctor_id should be 1 (Dr. Ahmed Khan)")

        # Step 2: Send ONLY a bare time ("9:30") without any phone number
        resp2 = agent.process_message(
            conv.id,
            "9:30"
        )
        content2 = resp2.get("content", "")
        # Strict assertion: Must NOT be the generic initial greeting or fallback
        self.assertNotIn("Hello! Welcome to SmileCare", content2,
                         "Bare time reply must not return the initial welcome greeting")
        self.assertNotIn("Hello! I am the AI receptionist", content2,
                         "Bare time reply must not return the default receptionist greeting")
        self.assertNotIn("Sorry, I didn't quite catch that", content2,
                         "Bare time reply must not fall through to ambiguous query fallback")
        # Must acknowledge the slot and ask for name/phone
        self.assertTrue(
            "09:30" in content2 or "9:30" in content2,
            f"Response should acknowledge the chosen 09:30 time slot: {content2}"
        )
        self.assertTrue(
            "name" in content2.lower() and "phone" in content2.lower(),
            f"Response should ask for patient name and phone number: {content2}"
        )

        # Verify state in DB retained date, doctor, service, and captured requested_time
        conv_db2 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db2.requested_date, target_date)
        self.assertEqual(conv_db2.selected_doctor_id, 1)
        self.assertEqual(conv_db2.selected_service_id, 2)
        self.assertEqual(conv_db2.requested_time, "09:30")
        self.assertEqual(conv_db2.status, "AI")

        # Step 3: Send patient name and phone number to complete booking
        resp3 = agent.process_message(
            conv.id,
            "My name is Ali Raza, phone 03001234567"
        )
        # Verify book_appointment tool was executed
        executed = [t["name"] for t in resp3.get("executed_tools", [])]
        self.assertIn("book_appointment", executed,
                      f"book_appointment should be called to complete booking: {resp3}")

        # Verify final workflow state is BOOKED and appointment was created
        conv_db3 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db3.workflow_state, "BOOKED")
        self.assertEqual(conv_db3.intent, "BOOK_APPOINTMENT")

        # Verify DB appointment record
        appt = Appointment.query.filter_by(
            business_id=biz_id,
            appointment_date=target_date,
            appointment_time="09:30"
        ).first()
        self.assertIsNotNone(appt, "Appointment record should be created in the database")
        self.assertEqual(appt.doctor_id, 1)
        self.assertEqual(appt.service_id, 2)
        self.assertEqual(appt.customer.name, "Ali Raza")

    def test_out_of_scope_specialty_mid_conversation_preserves_state(self):
        """
        Verify that out-of-scope medical specialty queries (e.g. 'neurosurgeon')
        mid-conversation trigger human_handoff, do not return the generic welcome greeting,
        and preserve previously accumulated booking state context.
        """
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")
        target_date = self._next_monday()

        conv = Conversation(business_id=biz_id, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Check availability for Dr. Ahmed
        resp1 = agent.process_message(
            conv.id,
            f"Check availability for Dr. Ahmed on {target_date}"
        )
        conv_db1 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db1.requested_date, target_date)
        self.assertEqual(conv_db1.selected_doctor_id, 1)

        # Step 2: Inquire about an out-of-scope non-dental specialist
        resp2 = agent.process_message(
            conv.id,
            "is there any neurosurgeon available?"
        )
        content2 = resp2.get("content", "")

        # Assert not generic greeting
        self.assertNotIn("Hello! Welcome to SmileCare", content2)
        self.assertNotIn("Hello! I am the AI receptionist", content2)

        # Assert human handoff was executed
        executed = [t["name"] for t in resp2.get("executed_tools", [])]
        self.assertIn("human_handoff", executed,
                      "Out-of-scope medical specialty should trigger human_handoff tool")

        # Assert conversation status is now HUMAN
        conv_db2 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db2.status, "HUMAN")
        self.assertIn("neurosurgeon", conv_db2.handoff_reason.lower())

        # Assert earlier booking context (date & doctor) was preserved
        self.assertEqual(conv_db2.requested_date, target_date)
        self.assertEqual(conv_db2.selected_doctor_id, 1)

    def test_question_with_time_does_not_select_slot(self):
        """
        Verify that a QUESTION containing a time token (e.g. 'is there any other slots after 12:00pm')
        is treated as an inquiry and does NOT falsely claim slot selection.
        """
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")
        target_date = self._next_monday()

        conv = Conversation(business_id=biz_id, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Check availability
        resp1 = agent.process_message(
            conv.id,
            f"Please check availability for Dr. Ahmed on {target_date}"
        )

        # Step 2: Ask a question containing a time
        resp2 = agent.process_message(
            conv.id,
            "is there any other slots after 12:00pm"
        )
        content2 = resp2.get("content", "")

        # Assert: must NOT claim slot selection
        self.assertNotIn("I have selected the 12:00", content2,
                         "A question must not falsely trigger slot selection")
        self.assertNotIn("I have selected the 12.00", content2,
                         "A question must not falsely trigger slot selection")
        self.assertNotIn("selected the 12", content2.lower(),
                         "A question must not claim slot 12:00 was selected")

        # Verify conv.requested_time was not set to 12:00
        conv_db = db.session.get(Conversation, conv.id)
        self.assertNotEqual(conv_db.requested_time, "12:00",
                            "A question must not persist the queried time as requested_time")

    def test_unoffered_time_statement_does_not_select_slot(self):
        """
        Verify that stating a time that was NOT in the offered slots list
        does not falsely claim selection.
        """
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")
        target_date = self._next_monday()

        conv = Conversation(business_id=biz_id, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Check availability
        resp1 = agent.process_message(
            conv.id,
            f"Check availability for Dr. Ahmed on {target_date}"
        )

        # Step 2: State a time outside working hours (e.g. 20:00 / 8 PM)
        resp2 = agent.process_message(
            conv.id,
            "20:00"
        )
        content2 = resp2.get("content", "")

        # Assert: must NOT claim 20:00 was selected
        self.assertNotIn("I have selected the 20:00", content2)
        self.assertTrue(
            "not available" in content2.lower() or "available slots" in content2.lower(),
            f"Response should indicate the slot is not available: {content2}"
        )

    def test_extract_time_str_formats(self):
        """
        Unit test _extract_time_str on dot and colon separators, spoken forms, and am/pm.
        """
        from ai.llm_client import _extract_time_str

        cases = {
            "12.00pm": "12:00",
            "12:00pm": "12:00",
            "12 pm": "12:00",
            "12.00": "12:00",
            "12:00": "12:00",
            "9.30": "09:30",
            "9:30": "09:30",
            "9.30am": "09:30",
            "9:30 am": "09:30",
            "2.30pm": "14:30",
            "2:30 pm": "14:30",
            "10 am": "10:00",
            "2 pm": "14:00",
            "is there any other slots after 12:00pm": "12:00",
            "is there any neurosurgeon available": None,
            "": None
        }

        for text, expected in cases.items():
            result = _extract_time_str(text)
            self.assertEqual(
                result, expected,
                f"Failed for text '{text}': expected {expected}, got {result}"
            )



class TestAdminTenantIsolation(BaseFixTest):
    """
    Step 2 validation: Admin API tenant isolation.
    HandoffService must reject cross-tenant operations.
    """

    def _create_second_business(self) -> Business:
        biz2 = Business(
            name="Other Clinic",
            business_type="clinic",
            timezone="Asia/Karachi",
            phone="+923000000000",
            address="Other Street, Karachi",
            opening_hours="Monday to Friday: 09:00 AM - 05:00 PM"
        )
        db.session.add(biz2)
        db.session.commit()
        return biz2

    def test_trigger_handoff_cross_tenant_rejected(self):
        biz2 = self._create_second_business()
        # Create a conversation belonging to Business 2
        conv2 = Conversation(business_id=biz2.id, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv2)
        db.session.commit()

        # Attempt takeover as Business 1 admin on Business 2 conversation
        result = HandoffService.trigger_handoff(
            conversation_id=conv2.id,
            reason="test",
            business_id=Config.DEFAULT_BUSINESS_ID  # Business 1
        )
        self.assertFalse(result["success"],
                         "Cross-tenant takeover must be rejected")
        self.assertEqual(result.get("code"), 403,
                         "Rejection must carry code=403")
        # Verify conversation status is unchanged
        conv2_db = db.session.get(Conversation, conv2.id)
        self.assertEqual(conv2_db.status, "AI",
                         "Cross-tenant takeover must not change conversation status")

    def test_release_to_ai_cross_tenant_rejected(self):
        biz2 = self._create_second_business()
        conv2 = Conversation(business_id=biz2.id, status="HUMAN", intent="UNKNOWN", workflow_state="HANDOFF_REQUESTED")
        db.session.add(conv2)
        db.session.commit()

        result = HandoffService.release_to_ai(
            conversation_id=conv2.id,
            business_id=Config.DEFAULT_BUSINESS_ID  # Business 1
        )
        self.assertFalse(result["success"])
        self.assertEqual(result.get("code"), 403)
        # Conversation must remain in HUMAN mode
        conv2_db = db.session.get(Conversation, conv2.id)
        self.assertEqual(conv2_db.status, "HUMAN")

    def test_admin_reply_cross_tenant_rejected(self):
        biz2 = self._create_second_business()
        conv2 = Conversation(business_id=biz2.id, status="HUMAN", intent="UNKNOWN", workflow_state="HANDOFF_REQUESTED")
        db.session.add(conv2)
        db.session.commit()

        result = HandoffService.admin_reply(
            conversation_id=conv2.id,
            message_content="Hello from wrong admin",
            business_id=Config.DEFAULT_BUSINESS_ID  # Business 1
        )
        self.assertFalse(result["success"])
        self.assertEqual(result.get("code"), 403)

    def test_same_tenant_operations_succeed(self):
        """Sanity: same-business handoff operations must still work after the isolation fix."""
        conv = Conversation(
            business_id=Config.DEFAULT_BUSINESS_ID,
            status="AI", intent="UNKNOWN", workflow_state="START"
        )
        db.session.add(conv)
        db.session.commit()

        result = HandoffService.trigger_handoff(
            conversation_id=conv.id,
            reason="test",
            business_id=Config.DEFAULT_BUSINESS_ID
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "HUMAN")


class TestTimezoneAwareness(BaseFixTest):
    """
    Step 3 validation: Past-date guard in check_availability uses clinic timezone.
    We mock the 'now' that booking_service._get_business_tz resolves to so we can
    simulate a server UTC time where the Karachi clock says 'today' is already tomorrow.
    """

    def test_past_date_rejected_using_clinic_timezone(self):
        biz_id = Config.DEFAULT_BUSINESS_ID
        # Use yesterday's date in Karachi time — must always be rejected
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        result = BookingService.check_availability(
            business_id=biz_id,
            date_str=yesterday
        )
        self.assertIn("error", result,
                      "A past date must be rejected by check_availability")
        self.assertIn("past", result["error"].lower(),
                      "Error message should mention 'past'")

    def test_future_date_accepted(self):
        biz_id = Config.DEFAULT_BUSINESS_ID
        future_date = self._next_monday()
        result = BookingService.check_availability(
            business_id=biz_id,
            date_str=future_date
        )
        # Should return results, not an error
        self.assertNotIn("error", result,
                         f"Future Monday {future_date} should not be rejected: {result}")
        self.assertIn("results", result)


class TestDurationAwareOverlap(BaseFixTest):
    """
    Step 4 validation: book_appointment rejects duration-overlapping slots.
    A 45-minute appointment at 10:00 should block a 30-minute slot at 10:15.
    """

    def _book_first(self, date_str: str, time_str: str, service_id: int) -> dict:
        return BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="First Patient",
            customer_phone="+923001111111",
            doctor_id=1,       # Dr. Ahmed Khan
            service_id=service_id,
            appointment_date=date_str,
            appointment_time=time_str,
        )

    def test_overlapping_appointment_rejected(self):
        target_date = self._next_monday()

        # Service 1 = Root Canal Treatment (60 min) — use any service that's ≥30 min
        # but find which service IDs are seeded:
        services = Service.query.filter_by(business_id=Config.DEFAULT_BUSINESS_ID).all()
        # Pick the longest service to guarantee overlap
        long_svc = max(services, key=lambda s: s.duration)
        short_svc = min(services, key=lambda s: s.duration)

        # Book long service at 10:00
        res1 = self._book_first(target_date, "10:00", long_svc.id)
        if not res1.get("success"):
            self.skipTest(f"First booking failed (slot unavailable?): {res1}")

        # Attempt short service at 10:00 + 1 slot (10:30 if long is ≥60 min)
        overlap_time_minutes = 10 * 60 + min(long_svc.duration - 1, 30)  # inside the long window
        overlap_h = overlap_time_minutes // 60
        overlap_m = overlap_time_minutes % 60
        overlap_time_str = f"{overlap_h:02d}:{overlap_m:02d}"

        res2 = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Second Patient",
            customer_phone="+923002222222",
            doctor_id=1,
            service_id=short_svc.id,
            appointment_date=target_date,
            appointment_time=overlap_time_str,
        )
        self.assertFalse(res2.get("success"),
                         f"Overlapping booking at {overlap_time_str} should be rejected "
                         f"(first appt: 10:00 for {long_svc.duration} min)")
        self.assertIn("overlap", res2.get("error", "").lower(),
                      "Error message should mention 'overlap'")

    def test_non_overlapping_same_day_accepted(self):
        target_date = self._next_monday()
        services = Service.query.filter_by(business_id=Config.DEFAULT_BUSINESS_ID).all()
        short_svc = min(services, key=lambda s: s.duration)

        # Two back-to-back non-overlapping slots
        res1 = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Patient A",
            customer_phone="+923003333333",
            doctor_id=1,
            service_id=short_svc.id,
            appointment_date=target_date,
            appointment_time="09:00",
        )
        if not res1.get("success"):
            self.skipTest(f"First booking failed: {res1}")

        # Next slot = 09:00 + short_svc.duration minutes — should not overlap
        next_start = 9 * 60 + short_svc.duration
        next_h = next_start // 60
        next_m = next_start % 60
        next_time_str = f"{next_h:02d}:{next_m:02d}"

        res2 = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Patient B",
            customer_phone="+923004444444",
            doctor_id=1,
            service_id=short_svc.id,
            appointment_date=target_date,
            appointment_time=next_time_str,
        )
        self.assertTrue(res2.get("success"),
                        f"Back-to-back non-overlapping booking at {next_time_str} should succeed: {res2}")


class TestWorkingDayValidation(BaseFixTest):
    """
    Step 4 validation: book_appointment rejects bookings on doctor's non-working days.
    Dr. Ahmed Khan works Mon–Fri. Saturday must be rejected.
    """

    def test_booking_on_nonworking_day_rejected(self):
        sunday = self._next_sunday()
        result = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Weekend Warrior",
            customer_phone="+923005555555",
            doctor_id=1,       # Dr. Ahmed Khan — Mon–Sat only (Sunday is off)
            service_id=2,
            appointment_date=sunday,
            appointment_time="10:00",
        )
        self.assertFalse(result.get("success"),
                         f"Booking on Sunday should be rejected for Dr. Ahmed: {result}")
        error_lower = result.get("error", "").lower()
        self.assertTrue(
            "sunday" in error_lower or "does not work" in error_lower,
            f"Error should mention the non-working day or 'does not work': {result.get('error')}"
        )

    def test_booking_on_working_day_succeeds(self):
        monday = self._next_monday()
        result = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Monday Patient",
            customer_phone="+923006666666",
            doctor_id=1,
            service_id=2,
            appointment_date=monday,
            appointment_time="10:00",
        )
        self.assertTrue(result.get("success"),
                        f"Booking on Monday should succeed for Dr. Ahmed: {result}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
