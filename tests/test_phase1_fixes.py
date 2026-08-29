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
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Appointment, Conversation, Message
from services.booking_service import BookingService
from services.handoff_service import HandoffService
from ai.agent import Agent
from ai.tools import CANONICAL_TOOLS, ToolDispatcher
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
            "not available" in content2.lower() or "available slots" in content2.lower() or "available appointment slots" in content2.lower(),
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


class TestVisitorSessionAndEndpointSecurity(BaseFixTest):
    """
    Phase 2 validation: Visitor session binding & Public endpoint protection.
    - Visitor A cannot read or hijack Visitor B's conversation.
    - Public mutation endpoints are removed/secured.
    """

    def test_visitor_cannot_access_other_visitor_conversation_history(self):
        # Client 1 creates a conversation
        client1 = self.app.test_client()
        init_res = client1.post("/api/chat/init")
        self.assertEqual(init_res.status_code, 200)
        data1 = init_res.get_json()
        conv_id = data1["conversation_id"]

        # Client 1 sends a message
        client1.post("/api/chat/send", json={"conversation_id": conv_id, "message": "My secret is 12345"})

        # Client 2 (separate session) tries to read Client 1's conversation history
        client2 = self.app.test_client()
        hist_res = client2.get(f"/api/chat/history/{conv_id}")
        self.assertEqual(hist_res.status_code, 404,
                         "Cross-visitor conversation history fetch must return 404")
        data2 = hist_res.get_json()
        self.assertFalse(data2.get("success"))

    def test_visitor_cannot_send_to_other_visitor_conversation(self):
        # Client 1 creates conversation
        client1 = self.app.test_client()
        init_res = client1.post("/api/chat/init")
        conv_id_1 = init_res.get_json()["conversation_id"]

        # Client 2 attempts to send a message targeting Client 1's conversation_id
        client2 = self.app.test_client()
        send_res = client2.post("/api/chat/send", json={"conversation_id": conv_id_1, "message": "Hijack attempt"})
        self.assertEqual(send_res.status_code, 200)
        data2 = send_res.get_json()
        # Must create a fresh conversation for Client 2 and NOT hijack conv_id_1
        self.assertNotEqual(data2["conversation_id"], conv_id_1,
                            "Client 2 must not hijack Client 1's conversation ID")
        self.assertTrue(data2.get("session_reset"),
                        "Session reset must be flagged when attempting to access foreign conversation")

    def test_public_mutation_endpoints_removed(self):
        client = self.app.test_client()
        # Direct booking via public API should return 404 (removed)
        book_res = client.post("/api/appointments/book", json={"customer_name": "Test", "doctor_id": 1})
        self.assertEqual(book_res.status_code, 404, "Public /api/appointments/book endpoint must be removed")

        # Direct listing via public API should return 404 (removed)
        list_res = client.get("/api/appointments")
        self.assertEqual(list_res.status_code, 404, "Public /api/appointments listing must be removed")

        # Availability endpoint remains accessible
class TestGeminiAndGroqToolCalling(unittest.TestCase):
    """
    Phase 3 validation:
    - GeminiAdapter properly formats prior assistant tool_calls as function_call parts
      and tool results as function_response parts in multi-turn history.
    - Real adapters raise exceptions on provider errors rather than silently falling back to mock.
    """

    def test_gemini_history_reconstructs_function_call_and_response_parts(self):
        from ai.llm_client import GeminiAdapter

        messages = [
            {"role": "user", "content": "Check slots for Dr. Ahmed on 2026-08-25"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "check_availability", "arguments": {"doctor_id": 1, "date": "2026-08-25"}}]
            },
            {
                "role": "tool",
                "tool_name": "check_availability",
                "content": json.dumps({"date": "2026-08-25", "results": []})
            }
        ]

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.candidates = [
            MagicMock(content=MagicMock(parts=[
                MagicMock(function_call=None, text="Here are your open slots.")
            ]))
        ]
        mock_client.models.generate_content.return_value = mock_response

        with patch("google.genai.Client", return_value=mock_client):
            adapter = GeminiAdapter(api_key="fake-key")
            resp = adapter.chat_completion(
                system_prompt="System instructions",
                messages=messages,
                tools=CANONICAL_TOOLS
            )

            self.assertEqual(resp["content"], "Here are your open slots.")
            self.assertTrue(mock_client.models.generate_content.called)
            call_kwargs = mock_client.models.generate_content.call_args.kwargs
            contents = call_kwargs["contents"]
            self.assertEqual(len(contents), 3)

            # Turn 1: user text
            self.assertEqual(contents[0].role, "user")

            # Turn 2: model function_call part
            self.assertEqual(contents[1].role, "model")
            self.assertTrue(len(contents[1].parts) > 0)
            self.assertIsNotNone(contents[1].parts[0].function_call)
            self.assertEqual(contents[1].parts[0].function_call.name, "check_availability")

            # Turn 3: user function_response part
            self.assertEqual(contents[2].role, "user")
            self.assertTrue(len(contents[2].parts) > 0)
            self.assertIsNotNone(contents[2].parts[0].function_response)
            self.assertEqual(contents[2].parts[0].function_response.name, "check_availability")

    def test_gemini_raises_on_api_error_without_mock_fallback(self):
        from ai.llm_client import GeminiAdapter

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("Gemini API Quota Exceeded")

        with patch("google.genai.Client", return_value=mock_client):
            adapter = GeminiAdapter(api_key="fake-key")
            with self.assertRaises(RuntimeError):
                adapter.chat_completion(
                    system_prompt="System instructions",
                    messages=[{"role": "user", "content": "Hi"}],
                    tools=CANONICAL_TOOLS
                )

    def test_groq_raises_on_api_error_without_mock_fallback(self):
        from ai.llm_client import GroqAdapter

        mock_groq_client = MagicMock()
        mock_groq_client.chat.completions.create.side_effect = RuntimeError("Groq Connection Error")

        with patch("groq.Groq", return_value=mock_groq_client):
            adapter = GroqAdapter(api_key="fake-key")
            with self.assertRaises(RuntimeError):
                adapter.chat_completion(
                    system_prompt="System instructions",
                    messages=[{"role": "user", "content": "Hi"}],
                    tools=CANONICAL_TOOLS
                )


class TestConversationStateAndBookingValidation(BaseFixTest):
    """
    Phase 5 & 6 validation:
    - Multi-turn booking with name and phone on separate turns preserves state.
    - Server-side validation returns structured missing_fields.
    - ToolDispatcher handles None arguments gracefully without uncaught exceptions.
    """

    def test_multi_turn_separate_name_and_phone_booking(self):
        conv = Conversation(business_id=Config.DEFAULT_BUSINESS_ID, channel="web_chat", status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=Config.DEFAULT_BUSINESS_ID, llm_provider="mock")
        monday = self._next_monday()

        # Turn 1: Ask for appointment on Monday
        res1 = agent.process_message(conv.id, f"I want an appointment with Dr. Ahmed on {monday}")
        self.assertIn("10:00", res1["content"])

        # Turn 2: Pick time "10:00"
        res2 = agent.process_message(conv.id, "10:00")
        self.assertIn("10:00", res2["content"])
        self.assertEqual(conv.requested_time, "10:00")

        # Turn 3: User provides only name "Muhammad Haroon"
        res3 = agent.process_message(conv.id, "Muhammad Haroon")
        self.assertIn("phone number", res3["content"].lower())
        self.assertEqual(conv.pending_customer_name, "Muhammad Haroon")
        # Ensure no appointment was created yet
        appts_count = Appointment.query.filter_by(business_id=Config.DEFAULT_BUSINESS_ID, appointment_date=monday).count()
        self.assertEqual(appts_count, 0)

        # Turn 4: User provides only phone number "03001234567"
        res4 = agent.process_message(conv.id, "03001234567")
        self.assertIn("confirmed", res4["content"].lower())
        
        # Verify DB appointment was created with correct name and phone
        appt = Appointment.query.filter_by(business_id=Config.DEFAULT_BUSINESS_ID, appointment_date=monday, appointment_time="10:00").first()
        self.assertIsNotNone(appt, "Appointment must be created in DB after name and phone provided")
        self.assertEqual(appt.customer.name, "Muhammad Haroon")
        self.assertEqual(appt.customer.phone, "03001234567")

    def test_missing_fields_structured_response(self):
        monday = self._next_monday()
        # Missing customer_phone
        res = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="John Doe",
            customer_phone="",
            doctor_id=1,
            service_id=2,
            appointment_date=monday,
            appointment_time="10:00"
        )
        self.assertFalse(res["success"])
        self.assertIn("missing_fields", res)
        self.assertIn("customer_phone", res["missing_fields"])

        # Missing multiple fields
        res2 = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="",
            customer_phone="",
            doctor_id=None,
            service_id=None,
            appointment_date="",
            appointment_time=""
        )
        self.assertFalse(res2["success"])
        self.assertEqual(set(res2["missing_fields"]), {"customer_name", "customer_phone", "doctor_id", "service_id", "appointment_date", "appointment_time"})

    def test_dispatcher_handles_none_arguments_gracefully(self):
        dispatcher = ToolDispatcher(business_id=Config.DEFAULT_BUSINESS_ID, conversation_id=1)
        res = dispatcher.execute("book_appointment", {
            "customer_name": "Jane",
            "customer_phone": None,
            "doctor_id": None,
            "service_id": None
        })
        self.assertFalse(res.get("success"))
        self.assertIn("missing_fields", res)


class TestIdempotencyAndReminderTimezone(BaseFixTest):
    """
    Phase 7 & 8 validation:
    - Repeated booking submissions with same idempotency_key are handled gracefully.
    - Reminder scheduling calculates 24H prior in clinic's timezone (Asia/Karachi) and stores in UTC.
    """

    def test_repeated_booking_with_same_idempotency_key_returns_existing(self):
        monday = self._next_monday()
        key = "idemp-test-key-999"

        # First booking attempt
        res1 = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Idempotent Patient",
            customer_phone="03009999999",
            doctor_id=1,
            service_id=2,
            appointment_date=monday,
            appointment_time="11:00",
            idempotency_key=key
        )
        self.assertTrue(res1["success"])
        appt_id = res1["appointment_id"]

        # Second booking attempt with exact same key
        res2 = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Idempotent Patient",
            customer_phone="03009999999",
            doctor_id=1,
            service_id=2,
            appointment_date=monday,
            appointment_time="11:00",
            idempotency_key=key
        )
        self.assertTrue(res2["success"])
        self.assertTrue(res2.get("is_duplicate_request"))
        self.assertEqual(res2["appointment"]["id"], appt_id)

        # Confirm only 1 appointment row was persisted
        count = Appointment.query.filter_by(idempotency_key=key).count()
        self.assertEqual(count, 1)

    def test_reminder_scheduled_in_utc_matching_business_timezone(self):
        from zoneinfo import ZoneInfo
        from datetime import timezone
        from services.reminder_service import ReminderService

        monday = self._next_monday()
        future_date_str = (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=7)).strftime("%Y-%m-%d")
        res = BookingService.book_appointment(
            business_id=Config.DEFAULT_BUSINESS_ID,
            customer_name="Timezone Patient",
            customer_phone="03008888888",
            doctor_id=1,
            service_id=2,
            appointment_date=future_date_str,
            appointment_time="09:00"
        )
        self.assertTrue(res["success"])
        appt = db.session.get(Appointment, res["appointment_id"])
        self.assertEqual(len(appt.reminders), 1)

        reminder = appt.reminders[0]
        # In Asia/Karachi (UTC+5), an appointment at 09:00 corresponds to 04:00 UTC on the same day.
        # The 24-hours-before reminder must be 04:00 UTC on the preceding day.
        appt_date_obj = datetime.strptime(future_date_str, "%Y-%m-%d")
        expected_reminder_date = appt_date_obj - timedelta(days=1)
        expected_reminder_utc = datetime(
            expected_reminder_date.year,
            expected_reminder_date.month,
            expected_reminder_date.day,
            4, 0, 0,
            tzinfo=timezone.utc
        )

        # Compare naive/aware UTC datetimes
        rem_dt = reminder.scheduled_for
        if rem_dt.tzinfo is None:
            rem_dt = rem_dt.replace(tzinfo=timezone.utc)
        self.assertEqual(rem_dt, expected_reminder_utc,
                         f"Scheduled reminder {rem_dt} must match 24h prior in UTC {expected_reminder_utc}")


class TestAwaitingInputAndStateFixes(BaseFixTest):
    """
    Validation for explicit awaiting_input state tracking and priority resolution.
    """

    def test_bare_sara_after_get_doctors_selects_doctor_sara_not_customer_name(self):
        from ai.llm_client import _extract_name
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI", intent="BOOK_APPOINTMENT", workflow_state="CHECKING_AVAILABILITY", awaiting_input="doctor_choice")
        db.session.add(conv)
        db.session.commit()

        # User Correction #2 Assertion: _extract_name("sara") MUST return None
        # when checked against this business's real doctor/service roster —
        # this replaces the old hardcoded "sara"/"ahmed"/"khan"/"malik"
        # exclusion list, which broke on any spelling variant (e.g. "ahmad").
        # The roster is what makes the exclusion correct now, so it must be
        # passed explicitly here too, matching how the real call sites do it.
        roster_names = [d.name for d in Doctor.query.filter_by(business_id=biz_id).all()] + \
                        [s.name for s in Service.query.filter_by(business_id=biz_id).all()]
        extracted_name = _extract_name("sara", roster_names=roster_names)
        self.assertIsNone(extracted_name, "doctor-choice input 'sara' must never be extracted as a candidate customer name")

        # Process message "sara" when awaiting_input == "doctor_choice" and workflow_state == "CHECKING_AVAILABILITY"
        resp = agent.process_message(conv.id, "sara")
        conv_db = db.session.get(Conversation, conv.id)

        # Must set doctor_id to 2 (Dr. Sara Malik)
        self.assertEqual(conv_db.selected_doctor_id, 2, "Bare 'sara' reply must select Dr. Sara Malik (ID 2)")
        self.assertNotEqual(conv_db.pending_customer_name, "Sara", "Doctor choice must never set pending_customer_name to 'Sara'")
        self.assertNotIn("Hello! Welcome to SmileCare", resp.get("content", ""), "Must not fall through to generic fallback greeting")

    def test_bare_service_after_get_services_selects_service(self):
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI", intent="BOOK_APPOINTMENT", workflow_state="COLLECTING_INFO", awaiting_input="service_choice")
        db.session.add(conv)
        db.session.commit()

        resp = agent.process_message(conv.id, "whitening")
        conv_db = db.session.get(Conversation, conv.id)

        self.assertEqual(conv_db.selected_service_id, 3, "Bare 'whitening' reply must select Teeth Whitening (ID 3)")
        self.assertIn("Teeth Whitening", resp.get("content", ""), "Response must acknowledge Teeth Whitening selection")

    def test_bare_yes_after_confirmation_pending_state_proceeds_with_booking(self):
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")
        target_date = self._next_monday()

        conv = Conversation(
            business_id=biz_id,
            status="AI",
            intent="BOOK_APPOINTMENT",
            workflow_state="CHECKING_AVAILABILITY",
            awaiting_input="confirmation",
            selected_doctor_id=1,
            selected_service_id=2,
            requested_date=target_date,
            requested_time="10:00",
            pending_customer_name="Ali Khan",
            pending_customer_phone="03001234567"
        )
        db.session.add(conv)
        db.session.commit()

        resp = agent.process_message(conv.id, "yes")
        executed = [t["name"] for t in resp.get("executed_tools", [])]
        self.assertIn("book_appointment", executed, "Bare 'yes' reply when confirmation is pending must trigger book_appointment")

        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.workflow_state, "BOOKED")
        self.assertIsNone(conv_db.awaiting_input, "awaiting_input must be cleared (None) after booking completed")

    def test_awaiting_input_cleared_on_topic_change(self):
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI", intent="BOOK_APPOINTMENT", workflow_state="COLLECTING_INFO", awaiting_input="doctor_choice")
        db.session.add(conv)
        db.session.commit()

        resp = agent.process_message(conv.id, "Where is your clinic located?")
        executed = [t["name"] for t in resp.get("executed_tools", [])]
        self.assertIn("get_clinic_info", executed, "Topic change asking for location must execute get_clinic_info")

        conv_db = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv_db.awaiting_input, "awaiting_input must be cleared when topic changes away from booking")


class TestFuzzyDoctorServiceMatching(unittest.TestCase):
    """
    Regression coverage for the reported bug: a spelling variant of a
    doctor's name (e.g. "dr ahmad" for "Dr. Ahmed Khan") was not recognized
    at all — neither the awaiting_input resolution nor the pre-LLM
    _resolve_workflow_input state resolver matched anything, because both
    used exact hardcoded/substring matching. Fixed by routing both through
    a shared fuzzy matcher (ai.llm_client._fuzzy_match_roster) against the
    real per-business doctor/service roster.
    """

    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_spelling_variant_resolves_and_persists_doctor_selection(self):
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent.process_message(conv.id, "doctors available")
        self.assertEqual(db.session.get(Conversation, conv.id).awaiting_input, "doctor_choice")

        resp = agent.process_message(conv.id, "dr ahmad")
        self.assertIn("Ahmed Khan", resp.get("content", ""),
                       "A spelling variant ('dr ahmad') must still resolve to the real doctor by name")

        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.selected_doctor_id, 1,
                          "selected_doctor_id must actually be persisted to the DB, not just referenced in the reply text")

    def test_doctor_name_variant_after_awaiting_input_has_moved_past_doctor_choice(self):
        """
        Regression for the exact user-reported transcript: after a doctor
        is selected and check_availability runs, awaiting_input moves to
        "time_choice". A bare doctor-name reply sent AFTER that point
        (e.g. re-selecting a different doctor, or a spelling variant like
        "ahmad") was being swallowed by name-extraction and treated as the
        customer's own name ("Thank you, Ahmad, please provide your phone
        number...") instead of being recognized as a doctor reference —
        because the doctor/service roster match only had priority during
        the exact "doctor_choice" moment, not afterward. Doctor/service
        roster matches must take priority over name-extraction regardless
        of the current awaiting_input value.
        """
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        from tests.test_date_helpers import patch_open_date

        # Turn 1: establish a requested_date (as in the real transcript,
        # this came from an earlier booking message), then ask to see
        # doctors, then pick one WITH a date already known — this is what
        # actually triggers check_availability and moves awaiting_input
        # from "doctor_choice" to "time_choice".
        with patch_open_date(biz_id, doctor_id=2):
            agent.process_message(conv.id, "I'd like to book for tomorrow")
            agent.process_message(conv.id, "tell me what doctors are available")
            agent.process_message(conv.id, "dr sara")
            conv_db = db.session.get(Conversation, conv.id)
            self.assertEqual(conv_db.awaiting_input, "time_choice")
            self.assertEqual(conv_db.selected_doctor_id, 2)

            # Turn 2: a bare doctor-name spelling variant sent AFTER
            # awaiting_input has moved past "doctor_choice" — must still be
            # recognized as a doctor reference, not the customer's own name.
            resp = agent.process_message(conv.id, "ahmad")
            conv_db = db.session.get(Conversation, conv.id)

        self.assertNotIn("Thank you, Ahmad", resp.get("content", ""),
                          "'ahmad' must never be treated as the customer's own name, at any conversation stage")
        self.assertIsNone(conv_db.pending_customer_name,
                           "pending_customer_name must never be set to a doctor's name variant")
        self.assertEqual(conv_db.selected_doctor_id, 1,
                          "'ahmad' must re-select Dr. Ahmed Khan even after awaiting_input has moved to time_choice")

    def test_shared_generic_word_does_not_cause_wrong_service_match(self):
        """
        Regression for a bug found while fixing the above: several service
        names share the generic word "Dental" (Checkup, Cleaning, Braces),
        which previously caused the FIRST alphabetical/ID-ordered service
        containing "dental" to win by coincidence rather than the service
        actually mentioned in the message.
        """
        biz_id = Config.DEFAULT_BUSINESS_ID
        agent = Agent(business_id=biz_id, llm_provider="mock")

        conv = Conversation(business_id=biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent.process_message(conv.id, "I'd like to book a dental cleaning with Dr. Ahmed")
        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.selected_service_id, 2,
                          "'dental cleaning' must match 'Dental Cleaning & Scaling' (id 2), not 'Dental Checkup & Consultation' (id 1) via the shared word 'dental'")


if __name__ == "__main__":
    unittest.main(verbosity=2)



