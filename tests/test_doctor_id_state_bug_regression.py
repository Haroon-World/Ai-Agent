"""
Regression test for doctor_id state divergence bug.

BUG SUMMARY
-----------
Two-part bug in the AI agent's conversation state management:

  Part 1 - Silent default injection:
    When a user says "I'd like to book for tomorrow" (no doctor mentioned),
    the LLM generated a check_availability call with doctor_id=1 as a
    fallback default.  _update_conversation_state then blindly wrote that
    default to conv.selected_doctor_id even though the customer never
    selected any doctor.

  Part 2 - Explicit selection silently dropped:
    When the user then said "dr sara", _resolve_workflow_input found the
    correct match (Dr. Sara Malik, id=2) but skipped the DB write because
    the guard condition evaluated to False -- the slot was already 1
    (from Part 1), and the message had no "change"/"switch" keyword.

FIX APPLIED (ai/agent.py)
  1. _update_conversation_state: Removed the block that wrote doctor_id
     from check_availability args into conv.selected_doctor_id.  The LLM
     routinely fills this with a fallback default; only _resolve_workflow_input
     and book_appointment should set it.

  2. _resolve_workflow_input: Removed the guard so that any fuzzy-roster
     match is always persisted -- the match itself is the explicit selection.
"""

import unittest
import os
import sys
from datetime import date, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from config.config import Config
from models import db, Conversation
from ai.agent import Agent
from seed import seed_database


class TestDoctorIdStateBugRegression(unittest.TestCase):
    """
    Verifies that:
      - Processing a booking message that mentions a date but NO doctor does NOT
        set conv.selected_doctor_id to any default.
      - A subsequent message containing an explicit doctor name correctly updates
        conv.selected_doctor_id to that doctor real ID in the database.
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
        db.create_all()
        seed_database(self.app)

        self.biz_id = Config.DEFAULT_BUSINESS_ID
        # Dr. Ahmed Khan = id 1, Dr. Sara Malik = id 2 (from seed.py)
        self.dr_ahmed_id = 1
        self.dr_sara_id = 2

        self.agent = Agent(business_id=self.biz_id, llm_provider="mock")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _next_sunday_str(self):
        """Return the next Sunday as YYYY-MM-DD (clinic is closed on Sundays)."""
        today = date.today()
        days_ahead = (6 - today.weekday()) % 7  # 6 = Sunday
        if days_ahead == 0:
            days_ahead = 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    def test_doctor_id_not_set_by_date_only_message_and_then_correctly_set_by_explicit_selection(self):
        """
        Exact scenario from the bug report:

          Message 1: "I'd like to book for tomorrow"
            -> If tomorrow is a closed day, the reply correctly says clinic is
               closed.  conv.selected_doctor_id MUST remain None (not set to any
               default like 1).

          Message 2: "dr sara"
            -> Reply text confirms Dr. Sara Malik was selected.
               conv.selected_doctor_id in the DB MUST be 2 (Dr. Sara real ID),
               NOT 1 (Dr. Ahmed), and NOT None.

        This test forces "tomorrow" to resolve to a Sunday (closed day) by
        patching resolve_date_string so the scenario is deterministic regardless
        of when the test runs.
        """
        sunday_str = self._next_sunday_str()

        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()
        conv_id = conv.id

        # Message 1: booking intent with a date, no doctor mentioned.
        # Patch resolve_date_string so "tomorrow" always resolves to a Sunday
        # (guaranteed closed day) regardless of when the test runs.
        with patch("ai.agent.resolve_date_string", return_value=sunday_str):
            self.agent.process_message(conv_id, "I'd like to book for tomorrow")

        conv_after_msg1 = db.session.get(Conversation, conv_id)

        # ASSERTION 1: No doctor should have been silently defaulted.
        self.assertIsNone(
            conv_after_msg1.selected_doctor_id,
            "After a message with a date but NO doctor selection, "
            "conv.selected_doctor_id must remain None -- never silently "
            "defaulted (got %s)." % conv_after_msg1.selected_doctor_id
        )

        # Message 2: explicit doctor selection -- "dr sara".
        self.agent.process_message(conv_id, "dr sara")

        conv_after_msg2 = db.session.get(Conversation, conv_id)

        # ASSERTION 2: Dr. Sara real ID must be in the DB.
        self.assertEqual(
            conv_after_msg2.selected_doctor_id,
            self.dr_sara_id,
            "After saying 'dr sara', conv.selected_doctor_id must be "
            "%d (Dr. Sara Malik), but got %s." % (
                self.dr_sara_id, conv_after_msg2.selected_doctor_id
            )
        )

    def test_plain_doctor_name_statement_rewrites_selection_after_awaiting_input_moves_past_doctor_choice(self):
        """
        Scenario C regression: once a doctor is selected and check_availability
        runs, awaiting_input moves to 'time_choice' (past 'doctor_choice').
        A bare doctor-name statement sent at that point (e.g. "ahmad") must
        still be recognised as a re-selection and update selected_doctor_id.

        Previously, the _resolve_workflow_input guard
            if not conv.selected_doctor_id or is_explicit_change or awaiting == "doctor_choice"
        was too narrow: once awaiting_input moved past "doctor_choice" and a
        doctor was already set, plain name statements were silently dropped.

        The fix adds condition (d): allow overwrite when the message is NOT
        phrased as a question, so bare name statements always re-select while
        information questions ("does dr ahmed have experience...") do not.
        """
        from datetime import date, timedelta
        from unittest.mock import patch

        # Use a known clinic-open weekday so check_availability returns slots
        # and moves awaiting_input to time_choice / date_choice.
        today = date.today()
        days_ahead = (0 - today.weekday()) % 7  # next Monday
        if days_ahead == 0:
            days_ahead = 7
        weekday_str = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()
        conv_id = conv.id

        # Step 1: establish a date, see doctors, pick Sara — this triggers
        # check_availability and moves awaiting_input past "doctor_choice".
        with patch("ai.agent.resolve_date_string", return_value=weekday_str):
            self.agent.process_message(conv_id, "I'd like to book for tomorrow")
        self.agent.process_message(conv_id, "tell me what doctors are available")
        self.agent.process_message(conv_id, "dr sara")

        conv_mid = db.session.get(Conversation, conv_id)
        self.assertEqual(
            conv_mid.selected_doctor_id, self.dr_sara_id,
            "Dr. Sara should be selected at this point."
        )
        self.assertNotEqual(
            conv_mid.awaiting_input, "doctor_choice",
            "awaiting_input must have moved past doctor_choice before the key assertion."
        )

        # Step 2: send a bare doctor-name statement — must re-select Ahmed.
        resp = self.agent.process_message(conv_id, "ahmad")
        conv_after = db.session.get(Conversation, conv_id)

        self.assertEqual(
            conv_after.selected_doctor_id,
            self.dr_ahmed_id,
            "Bare name 'ahmad' must re-select Dr. Ahmed Khan (id %d) even after "
            "awaiting_input has moved past doctor_choice. Got: %s" % (
                self.dr_ahmed_id, conv_after.selected_doctor_id
            )
        )
        self.assertIsNone(
            conv_after.pending_customer_name,
            "'ahmad' must never be captured as the customer's own name."
        )
        self.assertNotIn(
            "Thank you, Ahmad", resp.get("content", ""),
            "'ahmad' must not be treated as the patient's name at any stage."
        )


if __name__ == "__main__":
    unittest.main()
