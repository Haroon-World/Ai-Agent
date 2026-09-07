import unittest
from datetime import datetime, timedelta
from app import create_app
from config.config import Config
from models import db, Conversation, Appointment, Customer, Doctor, Service
from ai.agent import Agent, _build_ui_action
from ai.llm_client import _is_appointment_status_inquiry, _classify_intent
from services.booking_service import BookingService, RequestCache
from seed import seed_database


class TestAppointmentStatusAndUIGuard(unittest.TestCase):
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
        seed_database(self.app)
        RequestCache.clear()

        # Find next valid weekday for Dr. Ahmed (Monday, Friday, Saturday)
        today = datetime.now().date()
        days_ahead = 1
        while (today + timedelta(days=days_ahead)).weekday() not in [0, 4, 5]:
            days_ahead += 1
        self.valid_date = (today + timedelta(days=days_ahead)).isoformat()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_unit_is_appointment_status_inquiry(self):
        """Test unit detection of status inquiry phrases."""
        # Queries asking if cancelled or details
        self.assertTrue(_is_appointment_status_inquiry("can you tell my booking details, was that cancelled or not"))
        self.assertTrue(_is_appointment_status_inquiry("check again, staff has cancelled that and give me another appoinment"))
        self.assertTrue(_is_appointment_status_inquiry("mera appointment cancel hua ya nahi"))
        self.assertTrue(_is_appointment_status_inquiry("kya meri appointment cancel ho chuki"))
        self.assertTrue(_is_appointment_status_inquiry("show my booking details"))
        self.assertTrue(_is_appointment_status_inquiry("booking status kya hai"))
        self.assertTrue(_is_appointment_status_inquiry("check again"))
        self.assertTrue(_is_appointment_status_inquiry("kya status hai"))

        # Pure cancel requests should NOT be status inquiries
        self.assertFalse(_is_appointment_status_inquiry("cancel my appointment"))
        self.assertFalse(_is_appointment_status_inquiry("booking cancel kar do"))
        self.assertFalse(_is_appointment_status_inquiry("please cancel"))

        # General availability or booking queries should NOT be status inquiries
        self.assertFalse(_is_appointment_status_inquiry("Can I check Dr Ahmed available slots for tomorrow?"))
        self.assertFalse(_is_appointment_status_inquiry("mujhe Dr Sara se appointment fix karni hai"))
        self.assertFalse(_is_appointment_status_inquiry("dr ahmed ki timing kya hai"))

    def test_unit_classify_intent_status_inquiry(self):
        """Verify _classify_intent flags APPOINTMENT_STATUS_INQUIRY."""
        self.assertEqual(
            _classify_intent("can you tell my booking details, was that cancelled or not"),
            "APPOINTMENT_STATUS_INQUIRY"
        )
        self.assertEqual(
            _classify_intent("check again, staff has cancelled that and give me another appoinment"),
            "APPOINTMENT_STATUS_INQUIRY"
        )

    def test_unit_ui_action_date_selection_guard(self):
        """
        Verify _build_ui_action NEVER displays date_selection if awaiting_input is not date_choice/date,
        even if selected_doctor_id is present or intent is APPOINTMENT_STATUS_INQUIRY.
        """
        conv = Conversation(
            business_id=1,
            status="AI",
            intent="APPOINTMENT_STATUS_INQUIRY",
            workflow_state="BOOKED",
            selected_doctor_id=1,
            selected_service_id=1,
            awaiting_input=None
        )
        db.session.add(conv)
        db.session.commit()

        ui = _build_ui_action(conv)
        self.assertIsNone(ui, "UI action should be None for status inquiry with no awaiting_input")

        # When awaiting_input is date_choice during booking, date_selection MUST appear
        conv.intent = "BOOK_APPOINTMENT"
        conv.workflow_state = "COLLECTING_INFO"
        conv.awaiting_input = "date_choice"
        conv.requested_date = None
        db.session.commit()

        ui = _build_ui_action(conv)
        self.assertIsNotNone(ui)
        self.assertEqual(ui.get("type"), "date_selection")

    def test_user_dialogue_cancelled_then_manually_booked(self):
        """
        End-to-End reproduction of user's issue:
        1. User booked Appointment #1 (status CONFIRMED).
        2. Dashboard staff cancelled Appointment #1 and created Appointment #2 manually.
        3. User asks: 'can you tell my booking details, was that cancelled or not'.
        4. AI accurately detects #1 is cancelled and #2 is active confirmed.
        5. User asks: 'check again, staff has cancelled that and give me another appoinment'.
        6. AI maintains accurate details and does NOT show unexpected date selection UI.
        """
        cust = Customer(business_id=1, name="Abdul Hadi", phone="+923001234567")
        db.session.add(cust)
        db.session.commit()

        # Create Appointment #1 (cancelled)
        appt1 = Appointment(
            business_id=1,
            customer_id=cust.id,
            doctor_id=1,
            service_id=1,
            appointment_date=self.valid_date,
            appointment_time="16:30",
            status="CANCELLED",
            notes="Staff cancelled in dashboard upon customer request"
        )
        db.session.add(appt1)
        db.session.commit()

        # Create Appointment #2 (active confirmed, manually created by clinic staff)
        appt2 = Appointment(
            business_id=1,
            customer_id=cust.id,
            doctor_id=1,
            service_id=1,
            appointment_date=self.valid_date,
            appointment_time="09:00",
            status="CONFIRMED",
            notes="Booked manually by staff"
        )
        db.session.add(appt2)
        db.session.commit()

        conv = Conversation(
            business_id=1,
            customer_id=cust.id,
            status="AI",
            pending_customer_name="Abdul Hadi",
            pending_customer_phone="+923001234567",
            selected_doctor_id=1,
            selected_service_id=1,
            requested_date=self.valid_date,
            requested_time="16:30",
            workflow_state="BOOKED"
        )
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: Customer asks about booking details / if cancelled
        res1 = agent.process_message(conv.id, "can you tell my booking details, was that cancelled or not")
        content1 = res1.get("content", "").lower()

        # Assert that the AI identifies cancelled appt #1 and active confirmed appt #2
        self.assertTrue(
            "cancelled" in content1 or "cancel" in content1 or "#1" in content1,
            f"Expected mention of cancellation in: {content1}"
        )
        self.assertTrue(
            "09:00" in content1 or f"#{appt2.id}" in content1 or "confirmed" in content1,
            f"Expected mention of active confirmed appointment in: {content1}"
        )
        # UI Action must NOT be date_selection
        ui1 = res1.get("ui_action")
        self.assertTrue(ui1 is None or ui1.get("type") != "date_selection", f"Unexpected UI action: {ui1}")

        # Turn 2: Customer asks to check again
        res2 = agent.process_message(conv.id, "check again, staff has cancelled that and give me another appoinment")
        content2 = res2.get("content", "").lower()
        ui2 = res2.get("ui_action")

        # Must not cancel appt #2!
        appt2_check = db.session.get(Appointment, appt2.id)
        self.assertEqual(appt2_check.status, "CONFIRMED", "Appt #2 must remain CONFIRMED and not be inadvertently cancelled!")
        self.assertTrue(ui2 is None or ui2.get("type") != "date_selection", f"Unexpected date selection UI in turn 2: {ui2}")

        # Turn 3: Customer replies 'yes'
        res3 = agent.process_message(conv.id, "yes")
        ui3 = res3.get("ui_action")
        self.assertTrue(ui3 is None or ui3.get("type") != "date_selection", f"Unexpected date selection UI on 'yes': {ui3}")

    def test_cancelled_only_appointment_status_inquiry(self):
        """
        Verify that when an appointment is cancelled and no active appointment exists,
        the agent clearly informs the user of cancellation and does not present date buttons.
        """
        cust = Customer(business_id=1, name="Ali Raza", phone="+923009876543")
        db.session.add(cust)
        db.session.commit()

        appt = Appointment(
            business_id=1,
            customer_id=cust.id,
            doctor_id=1,
            service_id=1,
            appointment_date=self.valid_date,
            appointment_time="11:00",
            status="CANCELLED",
            notes="Doctor emergency leave"
        )
        db.session.add(appt)
        db.session.commit()

        conv = Conversation(
            business_id=1,
            customer_id=cust.id,
            status="AI",
            pending_customer_name="Ali Raza",
            pending_customer_phone="+923009876543",
            workflow_state="COMPLETED"
        )
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "was my appointment cancelled?")
        content = res.get("content", "").lower()
        ui = res.get("ui_action")

        self.assertTrue("cancelled" in content or "cancel" in content, f"Expected cancellation statement in: {content}")
        self.assertTrue(ui is None or ui.get("type") != "date_selection", f"Expected no date picker UI: {ui}")

    def test_roman_urdu_status_inquiry(self):
        """
        Verify that status inquiries in Roman Urdu accurately identify appointment status.
        """
        cust = Customer(business_id=1, name="Hamza", phone="+923214567890")
        db.session.add(cust)
        db.session.commit()

        appt = Appointment(
            business_id=1,
            customer_id=cust.id,
            doctor_id=1,
            service_id=1,
            appointment_date=self.valid_date,
            appointment_time="14:00",
            status="CANCELLED",
            notes="Patient requested cancellation"
        )
        db.session.add(appt)
        db.session.commit()

        conv = Conversation(
            business_id=1,
            customer_id=cust.id,
            status="AI",
            pending_customer_name="Hamza",
            pending_customer_phone="+923214567890",
            workflow_state="COMPLETED"
        )
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "mera appointment cancel hua ya nahi check karein")
        content = res.get("content", "").lower()
        ui = res.get("ui_action")

        self.assertTrue("cancel" in content, f"Expected cancel confirmation in: {content}")
        self.assertTrue(ui is None or ui.get("type") != "date_selection", f"Expected no date picker UI: {ui}")

    def test_slot_selection_does_not_show_doctor_selection_button(self):
        """
        Reproduction of User Issue:
        1. User asks for appointment slots for tomorrow ('6 appointments for tomorrow.').
        2. AI presents available slots.
        3. User selects a slot: '4pm will be okay for me'.
        4. AI reserves the 04:00 PM slot and asks for customer's full name and phone number.
        5. UI Action MUST NOT show doctor selection buttons (e.g. 'Dr. Ahmed Khan')!
           Because the AI is awaiting patient name/phone, not doctor selection.
        """
        import freezegun
        with freezegun.freeze_time("2026-09-06 10:00:00+05:00"):
            conv = Conversation(business_id=1, status="AI", workflow_state="START")
            db.session.add(conv)
            db.session.commit()

            agent = Agent(business_id=1, llm_provider="mock")

            # Turn 1: User asks for slots tomorrow
            res1 = agent.process_message(conv.id, "appointments for tomorrow.")
            self.assertIn("04:00 PM", res1.get("content", ""))

            # Turn 2: User selects 4pm
            res2 = agent.process_message(conv.id, "4pm will be okay for me")
            content2 = res2.get("content", "")
            ui2 = res2.get("ui_action")

            # AI must ask for name and contact phone number
            self.assertTrue(
                "name" in content2.lower(),
                f"Expected name prompt in: {content2}"
            )
            # UI Action must NOT be doctor_selection!
            self.assertTrue(
                ui2 is None or ui2.get("type") != "doctor_selection",
                f"Unexpected doctor_selection UI action when awaiting name: {ui2}"
            )


if __name__ == "__main__":
    unittest.main()
