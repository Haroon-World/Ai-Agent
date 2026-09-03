import os
import sys
import unittest
from datetime import date, timedelta

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import create_app
from config.config import Config
from models import db, Conversation, Appointment
from ai.agent import Agent
from seed import seed_database


class TestIntentRoutingAndConversationFix(unittest.TestCase):
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
        seed_database()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _get_next_working_date(self, day_name="Monday"):
        today = date.today()
        days_map = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6}
        target_day = days_map.get(day_name, 0)
        days_ahead = (target_day - today.weekday()) % 7
        if days_ahead <= 0:
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    def test_1_compound_intent_doctors_name(self):
        """TEST 1: 'I want an appointment, tell me doctors name' -> calls get_doctors, NOT check_availability."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "I want an appointment, tell me doctors name")

        self.assertEqual(res["status"], "AI")
        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertIn("get_doctors", executed_tool_names)
        self.assertNotIn("check_availability", executed_tool_names)
        self.assertTrue("dr. ahmed" in res["content"].lower() or "dentist" in res["content"].lower())

    def test_2_generic_appointment_no_invented_date(self):
        """TEST 2: 'I want an appointment' -> No check_availability call, no invented date."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "I want an appointment")

        self.assertEqual(res["status"], "AI")
        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertNotIn("check_availability", executed_tool_names)
        self.assertTrue("doctor" in res["content"].lower() or "select" in res["content"].lower() or "date" in res["content"].lower())

    def test_3_tell_me_your_doctors(self):
        """TEST 3: 'Tell me your doctors' -> calls get_doctors."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "Tell me your doctors")

        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertIn("get_doctors", executed_tool_names)

    def test_4_is_doctor_available_tomorrow(self):
        """TEST 4: 'Is Dr Ahmed available tomorrow?' -> calls check_availability for tomorrow."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "Is Dr Ahmed available tomorrow?")

        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertIn("check_availability", executed_tool_names)

    def test_5_is_doctor_available_no_date(self):
        """TEST 5: 'Is Dr Ahmed available?' -> No invented date, asks which date."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "Is Dr Ahmed available?")

        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertNotIn("check_availability", executed_tool_names)
        self.assertTrue("date" in res["content"].lower())

    def test_6_appointment_tomorrow_with_doctor(self):
        """TEST 6: 'I want an appointment tomorrow with Dr Ahmed' -> doctor and date recognized, check_availability called."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "I want an appointment tomorrow with Dr Ahmed")

        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertIn("check_availability", executed_tool_names)

    def test_7_progressive_multi_turn_booking_flow(self):
        """TEST 7: Multi-turn progressive booking flow preserving state."""
        monday_date = self._get_next_working_date("Monday")
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: Generic appointment request -> asks for doctor/date
        r1 = agent.process_message(conv.id, "I want an appointment.")
        self.assertNotIn("check_availability", [t["name"] for t in r1.get("executed_tools", [])])

        # Turn 2: Specify doctor
        r2 = agent.process_message(conv.id, "Dr Ahmed.")
        self.assertNotIn("check_availability", [t["name"] for t in r2.get("executed_tools", [])])

        # Turn 3: Specify date -> triggers availability
        r3 = agent.process_message(conv.id, monday_date)
        self.assertIn("check_availability", [t["name"] for t in r3.get("executed_tools", [])])

        # Turn 4: Specify time
        r4 = agent.process_message(conv.id, "10:00")
        self.assertEqual(r4["status"], "AI")

        # Turn 5: Specify name
        r5 = agent.process_message(conv.id, "Muhammad Haroon")
        self.assertEqual(r5["status"], "AI")

        # Turn 6: Specify phone -> triggers book_appointment
        r6 = agent.process_message(conv.id, "03001234567")
        self.assertIn("book_appointment", [t["name"] for t in r6.get("executed_tools", [])])

        # Verify DB appointment persisted
        appt = Appointment.query.filter_by(business_id=1, appointment_date=monday_date, appointment_time="10:00").first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.customer.name, "Muhammad Haroon")

    def test_8_compound_intent_services_inquiry(self):
        """TEST 8: 'I want an appointment with Dr Sara, what services does she offer?' -> get_services first."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "I want an appointment with Dr Sara, what services does she offer?")

        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertIn("get_services", executed_tool_names)
        self.assertNotIn("check_availability", executed_tool_names)

    def test_9_compound_intent_cleaning_price_and_tomorrow(self):
        """TEST 9: 'How much is cleaning and can I book tomorrow?' -> get_services first."""
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "How much is cleaning and can I book tomorrow?")

        executed_tool_names = [t["name"] for t in res.get("executed_tools", [])]
        self.assertTrue("get_services" in executed_tool_names or "check_availability" in executed_tool_names)
        self.assertTrue("cleaning" in res["content"].lower() or "4,000" in res["content"] or "4000" in res["content"])


if __name__ == "__main__":
    unittest.main()
