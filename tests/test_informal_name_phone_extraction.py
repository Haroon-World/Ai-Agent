import unittest
from datetime import datetime, timedelta
from app import create_app
from config.config import Config
from models import db, Conversation, Appointment, Customer
from ai.agent import Agent
from ai.llm_client import _extract_name, _extract_phone_number
from seed import seed_database
from services.booking_service import RequestCache


class TestInformalNamePhoneExtraction(unittest.TestCase):
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

        # Find next valid weekday (e.g. Monday-Thursday)
        today = datetime.now().date()
        days_ahead = 1
        while (today + timedelta(days=days_ahead)).weekday() in [4, 6]:  # Skip Friday/Sunday
            days_ahead += 1
        self.valid_date = (today + timedelta(days=days_ahead)).isoformat()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_reproduction_space_separated_name_and_phone(self):
        """
        Reproduction Step 2:
        'Hassan 03001234567' must extract BOTH name and phone in one pass,
        create a confirmed appointment, and return a real confirmation message.
        """
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()
        agent = Agent(business_id=1, llm_provider="mock")

        # 1. Reach the point where AI asks for name and phone
        agent.process_message(conv.id, f"I want an appointment with Dr. Ahmed Khan on {self.valid_date}")
        agent.process_message(conv.id, "10:00")

        # 2. Test message: "Hassan 03001234567"
        appts_before = Appointment.query.count()
        r = agent.process_message(conv.id, "Hassan 03001234567")

        # Confirms: Appointment created in database
        self.assertEqual(Appointment.query.count(), appts_before + 1)
        appt = Appointment.query.order_by(Appointment.id.desc()).first()
        self.assertEqual(appt.customer.name, "Hassan")
        self.assertEqual(appt.customer.phone, "03001234567")
        self.assertEqual(appt.status, "CONFIRMED")

        # Confirms: Reply text is an authentic confirmation matching DB state
        content = r.get("content", "")
        self.assertTrue(
            "confirmed" in content.lower() or "booked" in content.lower(),
            f"Expected confirmation in reply, got: {content}"
        )
        self.assertNotIn("Please provide your full name", content)
        self.assertIn("Hassan", content)

    def test_reproduction_comma_separated_name_and_phone(self):
        """
        Reproduction Step 3:
        'Hassan, 03001234567' must extract BOTH name and phone in one pass,
        create a confirmed appointment, and return an accurate confirmation message
        rather than an unrelated slot list or asking again for name.
        """
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()
        agent = Agent(business_id=1, llm_provider="mock")

        # 1. Reach the point where AI asks for name and phone
        agent.process_message(conv.id, f"I want an appointment with Dr. Ahmed Khan on {self.valid_date}")
        agent.process_message(conv.id, "10:00")

        # 2. Test message: "Hassan, 03001234567"
        appts_before = Appointment.query.count()
        r = agent.process_message(conv.id, "Hassan, 03001234567")

        # Confirms: Appointment created in database
        self.assertEqual(Appointment.query.count(), appts_before + 1)
        appt = Appointment.query.order_by(Appointment.id.desc()).first()
        self.assertEqual(appt.customer.name, "Hassan")
        self.assertEqual(appt.customer.phone, "03001234567")
        self.assertEqual(appt.status, "CONFIRMED")

        # Confirms: Reply text is an authentic confirmation matching DB state
        content = r.get("content", "")
        self.assertTrue(
            "confirmed" in content.lower() or "booked" in content.lower(),
            f"Expected confirmation in reply, got: {content}"
        )
        self.assertNotIn("Please provide your full name", content)
        self.assertNotIn("available slots", content.lower())
        self.assertIn("Hassan", content)

    def test_explicit_phrasing_still_works_unchanged(self):
        """
        Requirement 3: Explicit phrasing ('my name is X, my phone is Y') must continue to work.
        """
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()
        agent = Agent(business_id=1, llm_provider="mock")

        agent.process_message(conv.id, f"I want an appointment with Dr. Ahmed Khan on {self.valid_date}")
        agent.process_message(conv.id, "10:00")

        r = agent.process_message(conv.id, "My name is Ali, my phone is 03001234567")
        appt = Appointment.query.order_by(Appointment.id.desc()).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.customer.name, "Ali")
        self.assertEqual(appt.customer.phone, "03001234567")
        self.assertIn("confirmed", r["content"].lower())

    def test_natural_variations_generalization(self):
        """
        Requirement 3: Natural variations ('It's Hassan, my number is 03001234567', 'Hassan - 03001234567', etc.)
        """
        variations = [
            ("It's Hassan, my number is 03001234567", "Hassan", "03001234567", "11:00"),
            ("Hassan - 03001234567", "Hassan", "03001234567", "11:30"),
            ("03001234567, Hassan", "Hassan", "03001234567", "12:00"),
            ("Hassan Raza 03001234567", "Hassan Raza", "03001234567", "12:30"),
        ]

        for msg, exp_name, exp_phone, slot_time in variations:
            RequestCache.clear()
            conv = Conversation(business_id=1, status="AI")
            db.session.add(conv)
            db.session.commit()
            agent = Agent(business_id=1, llm_provider="mock")

            agent.process_message(conv.id, f"I want to see Dr. Ahmed Khan on {self.valid_date}")
            agent.process_message(conv.id, slot_time)
            r = agent.process_message(conv.id, msg)

            appt = Appointment.query.order_by(Appointment.id.desc()).first()
            self.assertIsNotNone(appt, f"Failed for variation: {msg}")
            self.assertEqual(appt.customer.name, exp_name, f"Name mismatch for: {msg}")
            self.assertEqual(appt.customer.phone, exp_phone, f"Phone mismatch for: {msg}")
            self.assertTrue(
                "confirmed" in r["content"].lower() or "booked" in r["content"].lower(),
                f"Confirmation missing in reply for: {msg}"
            )

    def test_unit_extraction_functions(self):
        """
        Unit level validation of _extract_name and _extract_phone_number across edge cases.
        """
        roster = ["Dr. Ahmed Khan", "Dr. Sara Malik", "Dental Consultation", "Teeth Whitening"]

        # 1. Informal compound extraction
        self.assertEqual(_extract_name("Hassan 03001234567", roster), "Hassan")
        self.assertEqual(_extract_phone_number("Hassan 03001234567"), "03001234567")

        self.assertEqual(_extract_name("Hassan, 03001234567", roster), "Hassan")
        self.assertEqual(_extract_phone_number("Hassan, 03001234567"), "03001234567")

        self.assertEqual(_extract_name("Hassan - 03001234567", roster), "Hassan")
        self.assertEqual(_extract_name("It's Hassan, my number is 03001234567", roster), "Hassan")
        self.assertEqual(_extract_name("03001234567 Hassan", roster), "Hassan")
        self.assertEqual(_extract_name("03001234567, Hassan", roster), "Hassan")
        self.assertEqual(_extract_name("Hassan Raza 03001234567", roster), "Hassan Raza")

        # 2. Exclusions must be respected
        self.assertIsNone(_extract_name("sara", roster))
        self.assertIsNone(_extract_name("Dr. Ahmed", roster))
        self.assertIsNone(_extract_name("tomorrow at 10am", roster))
        self.assertIsNone(_extract_name("tomorrow 10:00 03001234567", roster))
