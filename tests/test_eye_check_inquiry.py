import unittest
from app import create_app
from models import db, Conversation
from ai.agent import Agent

class TestEyeCheckInquiry(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_eye_check_inquiry_returns_active_dental_response(self):
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "I want to check my eyes")

        # Must not return generic fallback
        self.assertNotIn("Sorry, I didn't quite catch that", res["content"])
        # Must explicitly clarify dental clinic scope and offer active dental assistance
        self.assertIn("dental", res["content"].lower())
        self.assertTrue("eye" in res["content"].lower() or "medical" in res["content"].lower())
        self.assertTrue("teeth" in res["content"].lower() or "checkup" in res["content"].lower())

    def test_skin_inquiry_returns_active_dental_response(self):
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "Do you have a skin doctor?")

        self.assertNotIn("Sorry, I didn't quite catch that", res["content"])
        self.assertIn("dental", res["content"].lower())

if __name__ == "__main__":
    unittest.main()
