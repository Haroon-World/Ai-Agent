import os
import io
import sys
import unittest
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()

# Force real AI providers for test execution
os.environ["LLM_PROVIDER"] = "gemini"
os.environ["GEMINI_MODEL"] = "gemini-3.5-flash-lite"
os.environ["STT_PROVIDER"] = "groq"
os.environ["GROQ_STT_MODEL"] = "whisper-large-v3"
os.environ["TTS_PROVIDER"] = "gemini"

from app import create_app
from config.config import Config
from models import db, Business, Conversation, Doctor, Service, Appointment, Customer, Message
from ai.agent import Agent
from ai.speech_client import STTClient, TTSClient
from seed import seed_database

class TestRealAIPipeline(unittest.TestCase):
    """
    Live verification suite testing real Gemini 3.5 Flash Lite & Groq Whisper STT.
    """

    def _next_monday(self) -> str:
        today = date.today()
        days_ahead = 7 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "gemini"
            GEMINI_MODEL = "gemini-3.5-flash-lite"
            STT_PROVIDER = "groq"
            GROQ_STT_MODEL = "whisper-large-v3"
            TTS_PROVIDER = "gemini"

        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)

        self.biz_id = Config.DEFAULT_BUSINESS_ID
        self.agent = Agent(business_id=self.biz_id, llm_provider="gemini")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_a_normal_text_conversation(self):
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "Hello, who are you?")
        content = resp.get("content", "")
        print("\n--- Test A (Normal text) ---")
        print("Bot Response:", content)
        self.assertTrue("SmileCare" in content or "receptionist" in content.lower() or "dental" in content.lower())

    def test_b_doctor_information(self):
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "Tell me about your doctors")
        executed = [t["name"] for t in resp.get("executed_tools", [])]
        content = resp.get("content", "")
        print("\n--- Test B (Doctor Info) ---")
        print("Executed Tools:", executed)
        print("Bot Response:", content)
        self.assertIn("get_doctors", executed)

    def test_c_service_pricing_information(self):
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "What dental services does Dr. Sara offer and what are the prices?")
        executed = [t["name"] for t in resp.get("executed_tools", [])]
        content = resp.get("content", "")
        print("\n--- Test C (Service Pricing) ---")
        print("Executed Tools:", executed)
        print("Bot Response:", content)
        self.assertIn("Dental Cleaning", content)

    def test_d_to_h_multi_turn_booking_flow(self):
        target_date = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        print("\n--- Test D to H (Multi-Turn Booking) ---")
        # Step 1: Request appointment with Dr. Sara
        r1 = self.agent.process_message(conv.id, f"I want an appointment with Dr. Sara on {target_date}")
        print("Turn 1 (Request):", r1.get("content"))
        
        # Step 2: Time selection ("9:30 works for me")
        r2 = self.agent.process_message(conv.id, "9:30 works for me")
        print("Turn 2 (Time 9:30):", r2.get("content"))

        # Step 3: Change time ("Actually, make it 10:00")
        r3 = self.agent.process_message(conv.id, "Actually, make it 10:00")
        print("Turn 3 (Change to 10:00):", r3.get("content"))

        # Step 4: Service selection ("Dental Cleaning")
        r4_svc = self.agent.process_message(conv.id, "Dental Cleaning")
        print("Turn 4 (Service):", r4_svc.get("content"))

        # Step 5: Name
        r4 = self.agent.process_message(conv.id, "My name is Tariq Mahmood")
        print("Turn 5 (Name):", r4.get("content"))

        # Step 6: Phone -> Completes booking or asks for confirmation
        r5 = self.agent.process_message(conv.id, "My phone number is 03001234567")
        print("Turn 6 (Phone & Book):", r5.get("content"))

        executed_all = [t["name"] for t in r4.get("executed_tools", [])] + [t["name"] for t in r5.get("executed_tools", [])]
        if "book_appointment" not in executed_all:
            r6 = self.agent.process_message(conv.id, "Yes, please confirm and book my appointment.")
            executed_all += [t["name"] for t in r6.get("executed_tools", [])]

        self.assertIn("book_appointment", executed_all)

        # Assert DB appointment created
        appt = Appointment.query.filter_by(business_id=self.biz_id, appointment_time="10:00").first()
        self.assertIsNotNone(appt, "Real DB appointment record must be created")
        self.assertEqual(appt.customer.name, "Tariq Mahmood")
        self.assertEqual(appt.customer.phone, "03001234567")

    def test_i_human_handoff(self):
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "Can I speak to a human receptionist?")
        executed = [t["name"] for t in resp.get("executed_tools", [])]
        print("\n--- Test I (Human Handoff) ---")
        print("Executed Tools:", executed)
        self.assertIn("human_handoff", executed)
        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.status, "HUMAN")

    def test_l_out_of_scope_question(self):
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        resp = self.agent.process_message(conv.id, "Is there any neurosurgeon working at your clinic?")
        content = resp.get("content", "")
        print("\n--- Test L (Out of Scope) ---")
        print("Bot Response:", content)
        self.assertTrue("dental" in content.lower() or "human" in content.lower() or "receptionist" in content.lower())

if __name__ == "__main__":
    unittest.main(verbosity=2)
