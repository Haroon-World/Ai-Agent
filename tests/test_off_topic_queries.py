import unittest
from app import create_app
from models import db, Conversation
from ai.agent import Agent

class TestOffTopicQueries(unittest.TestCase):
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

    def test_chit_chat_how_are_you(self):
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "how are you?")

        self.assertNotIn("Sorry, I didn't quite catch that", res["content"])
        self.assertIn("dental", res["content"].lower())

    def test_off_topic_weather_query(self):
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "what is the weather today?")

        self.assertNotIn("Sorry, I didn't quite catch that", res["content"])
        self.assertIn("dental", res["content"].lower())

    def test_gratitude_thank_you(self):
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")
        res = agent.process_message(conv.id, "thank you very much!")

        self.assertNotIn("Sorry, I didn't quite catch that", res["content"])
        self.assertIn("welcome", res["content"].lower())

if __name__ == "__main__":
    unittest.main()
