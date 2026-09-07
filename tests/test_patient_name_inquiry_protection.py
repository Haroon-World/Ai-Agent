import unittest
from datetime import datetime, timedelta
from app import create_app
from config.config import Config
from models import db, Conversation, Appointment, Customer
from ai.agent import Agent
from ai.llm_client import _extract_name, _extract_phone_number, _is_valid_name_token, _is_question_query
from services.booking_service import BookingService, RequestCache, _is_valid_human_name
from seed import seed_database


class TestPatientNameInquiryProtection(unittest.TestCase):
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

        # Find next valid weekday (Monday-Thursday)
        today = datetime.now().date()
        days_ahead = 1
        while (today + timedelta(days=days_ahead)).weekday() in [4, 6]:
            days_ahead += 1
        self.valid_date = (today + timedelta(days=days_ahead)).isoformat()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_unit_extract_name_rejects_inquiries_and_questions(self):
        """Verify that inquiries, fee questions, and casual chatter are never extracted as names."""
        roster = ["Dr. Ahmed Khan", "Dr. Sara Malik", "Dental Consultation", "Dental Checkup & Consultation"]

        # Exact user message that triggered the bug
        self.assertIsNone(_extract_name("fee chages kia hin", roster))
        self.assertIsNone(_extract_name("fee charges kia hain", roster))
        self.assertIsNone(_extract_name("fee chages kia hin", roster, is_awaiting_name=True))

        # Other inquiries and Roman Urdu casual phrases
        self.assertIsNone(_extract_name("koi discount", roster))
        self.assertIsNone(_extract_name("kitni fees hai", roster))
        self.assertIsNone(_extract_name("yar bhai ni ho", roster))
        self.assertIsNone(_extract_name("ap kis time available hon gy", roster))
        self.assertIsNone(_extract_name("koi chaye nasta mily ga kia akhir 2000 dyna ha", roster))
        self.assertIsNone(_extract_name("acha ma ny ni ana plan cancel", roster))
        self.assertIsNone(_extract_name("discount do to ma ata hu", roster))

        # Question query detection
        self.assertTrue(_is_question_query("fee chages kia hin"))
        self.assertTrue(_is_question_query("koi discount"))
        self.assertTrue(_is_question_query("ap kis time available hon gy"))
        self.assertTrue(_is_question_query("kia charges hain"))

    def test_unit_extract_name_accepts_valid_names_appropriately(self):
        """Verify valid names are extracted when explicit, compound with phone, or when awaiting name."""
        roster = ["Dr. Ahmed Khan", "Dr. Sara Malik"]

        # 1. Compound name + phone
        self.assertEqual(_extract_name("Hassan 03001234567", roster), "Hassan")
        self.assertEqual(_extract_name("Ali Hassan 03001234567", roster), "Ali Hassan")
        self.assertEqual(_extract_name("03001234567, Tariq", roster), "Tariq")

        # 2. Explicit patterns
        self.assertEqual(_extract_name("My name is Ali Hassan", roster), "Ali Hassan")
        self.assertEqual(_extract_name("Mera naam Haroon hai", roster), "Haroon")
        self.assertEqual(_extract_name("I'm Usman Tariq", roster), "Usman Tariq")

        # 3. Standalone bare name when awaiting name
        self.assertEqual(_extract_name("Ali Hassan", roster, is_awaiting_name=True), "Ali Hassan")
        self.assertEqual(_extract_name("Haroon", roster, is_awaiting_name=True), "Haroon")

        # 4. Standalone bare name when NOT awaiting name must be None to prevent leakage
        self.assertIsNone(_extract_name("Ali Hassan", roster, is_awaiting_name=False))
        self.assertIsNone(_extract_name("Haroon", roster, is_awaiting_name=False))

    def test_unit_booking_service_rejects_inquiries_as_names(self):
        """Verify BookingService rejects non-human names and inquiries."""
        self.assertFalse(_is_valid_human_name("Fee Charges Kia Hain"))
        self.assertFalse(_is_valid_human_name("Fee Chages Kia Hin"))
        self.assertFalse(_is_valid_human_name("koi discount"))
        self.assertFalse(_is_valid_human_name("valued patient"))
        self.assertFalse(_is_valid_human_name("test"))
        self.assertFalse(_is_valid_human_name("12345"))
        self.assertFalse(_is_valid_human_name("a"))

        self.assertTrue(_is_valid_human_name("Ali Hassan"))
        self.assertTrue(_is_valid_human_name("Haroon Khan"))
        self.assertTrue(_is_valid_human_name("Sara Ahmed"))

        # Direct call to book_appointment with invalid name
        res = BookingService.book_appointment(
            business_id=1,
            customer_name="Fee Charges Kia Hain",
            customer_phone="03001234567",
            doctor_id=1,
            service_id=1,
            appointment_date=self.valid_date,
            appointment_time="10:00"
        )
        self.assertFalse(res["success"])
        self.assertIn("customer_name", res.get("missing_fields", []))

    def test_user_dialogue_reproduction_and_fix(self):
        """
        Reproduce the user's test scenario:
        1. User asks: 'fee chages kia hin' -> must NOT become patient name.
        2. User selects date and time.
        3. User provides phone number without name -> AI must ask for patient name, NOT confirm booking.
        4. User provides patient name -> AI confirms booking with correct patient name.
        """
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()
        agent = Agent(business_id=1, llm_provider="mock")

        # Step 1: User asks fee question
        r1 = agent.process_message(conv.id, "fee chages kia hin")
        conv = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv.pending_customer_name, "Inquiry 'fee chages kia hin' must not set pending_customer_name")

        # Step 2: User asks discount
        r2 = agent.process_message(conv.id, "koi discount")
        conv = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv.pending_customer_name, "Inquiry 'koi discount' must not set pending_customer_name")

        # Step 3: User picks doctor and date
        r3 = agent.process_message(conv.id, f"Dr. Ahmed Khan {self.valid_date}")
        conv = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv.pending_customer_name)

        # Step 4: User picks time
        r4 = agent.process_message(conv.id, "10:00")
        conv = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv.pending_customer_name)
        # Should be awaiting name
        self.assertEqual(conv.awaiting_input, "name")

        # Step 5: User gives phone number ONLY
        appts_before = Appointment.query.count()
        r5 = agent.process_message(conv.id, "03317117957")
        conv = db.session.get(Conversation, conv.id)

        # MUST NOT book yet because name is missing!
        self.assertEqual(Appointment.query.count(), appts_before, "Must NOT create appointment without patient name")
        self.assertEqual(conv.pending_customer_phone, "03317117957")
        self.assertIsNone(conv.pending_customer_name)
        self.assertEqual(conv.awaiting_input, "name")

        # AI must ask for patient's name
        content5 = r5.get("content", "")
        self.assertTrue(
            any(w in content5.lower() for w in ["naam", "name"]),
            f"Expected AI to ask for patient name, got: {content5}"
        )

        # Step 6: User provides patient name
        r6 = agent.process_message(conv.id, "Tariq Ahmed")
        self.assertEqual(Appointment.query.count(), appts_before + 1, "Appointment must be booked once name is provided")

        appt = Appointment.query.order_by(Appointment.id.desc()).first()
        self.assertEqual(appt.customer.name, "Tariq Ahmed")
        self.assertEqual(appt.customer.phone, "03317117957")
        self.assertEqual(appt.status, "CONFIRMED")
        self.assertIn("Tariq Ahmed", r6.get("content", ""))
        self.assertNotIn("Fee", r6.get("content", ""))

    def test_inquiry_while_awaiting_name_does_not_set_inquiry_as_name(self):
        """If AI asks for name and user asks another question, it answers without using it as name."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()
        agent = Agent(business_id=1, llm_provider="mock")

        # Setup: Doctor, Date, Time, Phone
        agent.process_message(conv.id, f"I want an appointment with Dr. Ahmed Khan on {self.valid_date}")
        agent.process_message(conv.id, "10:00")
        agent.process_message(conv.id, "03001234567")

        conv = db.session.get(Conversation, conv.id)
        self.assertEqual(conv.awaiting_input, "name")

        # User replies with a question instead of a name
        r = agent.process_message(conv.id, "kitna discount mil sakta hai")
        conv = db.session.get(Conversation, conv.id)

        self.assertIsNone(conv.pending_customer_name)
        self.assertEqual(conv.awaiting_input, "name")
        self.assertEqual(Appointment.query.count(), 0)
