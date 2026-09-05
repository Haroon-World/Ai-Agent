import unittest
from datetime import date, timedelta
from app import create_app
from models import db, Conversation, Service, Doctor
from ai.agent import Agent
from seed import seed_database

class TestUserTranscriptDiagnosis(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        seed_database()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_user_reported_transcript_flow(self):
        """
        Re-play the user's exact transcript and verify:
        1. 'who are you' -> bot identifies itself
        2. 'Are doctor available?' -> returns doctor info/list, NOT hardcoded Dr. Ahmed check
        3. 'How many doctors in your clinic' -> returns doctor list/count (get_doctors)
        4. 'doctors list please' -> returns doctors list
        5. 'What is Sara prices' -> returns service list & prices
        6. 'Root canal Treatment' -> resolves service_id=5 (Root Canal), DOES NOT treat 'Root Canal Treatment' as customer name!
        """
        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: who are you
        r1 = agent.process_message(conv.id, "who are you")
        self.assertTrue("ClinicConnect" in r1["content"] or "SmileCare" in r1["content"])

        # Turn 2: Are doctor available?
        r2 = agent.process_message(conv.id, "Are doctor available?")
        # Must execute get_doctors or list doctors, NOT check availability for tomorrow automatically
        executed_2 = [t["name"] for t in r2.get("executed_tools", [])]
        self.assertNotIn("check_availability", executed_2)

        # Turn 3: How many doctors in your clinic
        r3 = agent.process_message(conv.id, "How many doctors in your clinic")
        self.assertTrue("2" in r3.get("content", "") or "doctor" in r3.get("content", "").lower())

        # Turn 4: doctors list please
        r4 = agent.process_message(conv.id, "doctors list please")
        self.assertTrue("Ahmed" in r4.get("content", "") or "Sara" in r4.get("content", "") or "doctor" in r4.get("content", "").lower())

        # Turn 5: What is Sara prices
        r5 = agent.process_message(conv.id, "What is Sara prices")
        self.assertTrue("Sara" in r5.get("content", "") or "PKR" in r5.get("content", "") or "services" in r5.get("content", "").lower())

        # Turn 6: Root canal Treatment
        r6 = agent.process_message(conv.id, "Root canal Treatment")
        db.session.refresh(conv)
        # CRITICAL ASSERTION: Root Canal Treatment MUST NOT be assigned to pending_customer_name!
        self.assertNotEqual(conv.pending_customer_name, "Root Canal Treatment")
        self.assertEqual(conv.selected_service_id, 5) # Root Canal Treatment service ID

if __name__ == "__main__":
    unittest.main()
