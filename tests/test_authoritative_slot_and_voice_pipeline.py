import io
import unittest
from datetime import datetime, date, timedelta
from app import create_app
from config.config import Config
from models import db, Conversation, Message, Doctor, Service, Appointment, DoctorSchedule
from seed import seed_database
from ai.agent import Agent
from ai.speech_client import STTClient, MockSTTAdapter, GroqWhisperAdapter, GeminiSTTAdapter
from services.booking_service import BookingService

class TestAuthoritativeSlotAndVoicePipeline(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"
            STT_PROVIDER = "mock"
            TTS_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)
        from tests.test_date_helpers import get_next_open_weekday, get_next_closed_day
        self.doc_sara = Doctor.query.filter(Doctor.name.ilike("%Sara%")).first()
        self.doc_ahmed = Doctor.query.filter(Doctor.name.ilike("%Ahmed%")).first()
        self.target_date = get_next_open_weekday(1, doctor_id=self.doc_sara.id)
        self.closed_date = get_next_closed_day(1)
        self.service = Service.query.first()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_case_1_available_slot_accepted(self):
        """Case 1: User requests an available slot ('4pm') -> Accepted and continues workflow."""
        conv = Conversation(business_id=1, channel="web_chat", selected_doctor_id=self.doc_sara.id, requested_date=self.target_date)
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1)
        res = agent.process_message(conv.id, "4pm")

        conv_reloaded = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_reloaded.requested_time, "16:00")
        self.assertIn(conv_reloaded.awaiting_input, ["name", "phone", "confirmation"])
        self.assertIn("04:00 PM", res["content"])

    def test_case_2_unavailable_slot_rejected(self):
        """Case 2: User requests unavailable slot ('4:30pm') -> Rejected, not reserved, state not advanced."""
        from services.booking_service import RequestCache
        RequestCache.clear()
        target_day_name = datetime.strptime(self.target_date, "%Y-%m-%d").strftime("%A")
        sched = DoctorSchedule.query.filter_by(doctor_id=self.doc_sara.id, day_of_week=target_day_name).first()
        if sched:
            sched.start_time = "09:00"
            sched.end_time = "16:30" # 16:00 is last available slot
            db.session.commit()

        conv = Conversation(business_id=1, channel="web_chat", selected_doctor_id=self.doc_sara.id, requested_date=self.target_date)
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1)
        res = agent.process_message(conv.id, "4:30pm")

        conv_reloaded = db.session.get(Conversation, conv.id)
        # CRITICAL ASSERTIONS:
        self.assertIsNone(conv_reloaded.requested_time, "Unavailable slot must NOT be stored as requested_time")
        self.assertEqual(conv_reloaded.awaiting_input, "time_choice", "State must remain awaiting valid time_choice")
        self.assertIn("is not available", res["content"])
        self.assertNotIn("reserved the 04:30 PM slot", res["content"])
        self.assertNotIn("reserved the 4:30 PM slot", res["content"])

    def test_case_3_already_booked_slot_rejected_at_booking_gate(self):
        """Case 3: Already booked slot is rejected during booking."""
        res1 = BookingService.book_appointment(
            business_id=1,
            doctor_id=self.doc_ahmed.id,
            service_id=self.service.id,
            appointment_date=self.target_date,
            appointment_time="10:00",
            customer_name="First Patient",
            customer_phone="03001234567"
        )
        self.assertTrue(res1["success"])

        res2 = BookingService.book_appointment(
            business_id=1,
            doctor_id=self.doc_ahmed.id,
            service_id=self.service.id,
            appointment_date=self.target_date,
            appointment_time="10:00",
            customer_name="Second Patient",
            customer_phone="03007654321"
        )
        self.assertFalse(res2["success"])
        self.assertTrue("overlap" in res2["error"].lower() or "not available" in res2["error"].lower())

    def test_case_4_outside_working_hours_rejected(self):
        """Case 4: User requests a time outside working hours ('7pm') -> Rejected."""
        conv = Conversation(business_id=1, channel="web_chat", selected_doctor_id=self.doc_sara.id, requested_date=self.target_date)
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1)
        res = agent.process_message(conv.id, "7pm")

        conv_reloaded = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv_reloaded.requested_time)
        self.assertEqual(conv_reloaded.awaiting_input, "time_choice")
        self.assertIn("is not available", res["content"])

    def test_case_5_closed_day_rejected(self):
        """Case 5: Closed day (e.g. Sunday 2026-08-30) -> Informs clinic closed on Sundays."""
        conv = Conversation(business_id=1, channel="web_chat", selected_doctor_id=self.doc_sara.id)
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1)
        res = agent.process_message(conv.id, f"{self.closed_date} ko appointment chahiye")

        content = res["content"].lower()
        self.assertTrue("closed on sundays" in content or "band" in content or "off" in content or "closed" in content)

    def test_case_6_invalid_time_handled_gracefully(self):
        """Case 6: Invalid time format like '25pm' is rejected gracefully without crashing."""
        conv = Conversation(business_id=1, channel="web_chat", selected_doctor_id=self.doc_sara.id, requested_date=self.target_date)
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1)
        res = agent.process_message(conv.id, "25pm")

        conv_reloaded = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv_reloaded.requested_time)
        self.assertIn("content", res)

    def test_case_7_retry_after_invalid_selection(self):
        """Case 7: User tries unavailable 4:30pm (rejected), then corrects to available 4:00pm (accepted)."""
        from services.booking_service import RequestCache
        RequestCache.clear()
        target_day_name = datetime.strptime(self.target_date, "%Y-%m-%d").strftime("%A")
        sched = DoctorSchedule.query.filter_by(doctor_id=self.doc_sara.id, day_of_week=target_day_name).first()
        if sched:
            sched.start_time = "09:00"
            sched.end_time = "16:30" # 16:00 is last available slot
            db.session.commit()

        conv = Conversation(business_id=1, channel="web_chat", selected_doctor_id=self.doc_sara.id, requested_date=self.target_date)
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1)
        # Turn 1: Invalid slot
        res1 = agent.process_message(conv.id, "4:30pm")
        conv_turn1 = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv_turn1.requested_time)
        self.assertEqual(conv_turn1.awaiting_input, "time_choice")

        # Turn 2: Valid slot retry
        res2 = agent.process_message(conv.id, "4pm")
        conv_turn2 = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_turn2.requested_time, "16:00")
        self.assertIn("04:00 PM", res2["content"])

    def test_voice_pipeline_transcripts_preserve_word_ordering(self):
        """
        Voice Pipeline Test:
        Verify the 4 test phrases through STT pipeline into Agent.process_message():
        1. Urdu: 'مجھے ڈاکٹر سارا سے اپائنٹمنٹ چاہیے'
        2. Roman Urdu: 'mujhe doctor sara se appointment chahiye'
        3. Mixed: 'Dr Sara se kal appointment fix karni hai'
        4. Mixed with time: 'Dr Sara se kal 5 baje appointment chahiye'
        """
        test_phrases = [
            "مجھے ڈاکٹر سارا سے اپائنٹمنٹ چاہیے",
            "mujhe doctor sara se appointment chahiye",
            "Dr Sara se kal appointment fix karni hai",
            "Dr Sara se kal 5 baje appointment chahiye"
        ]

        for phrase in test_phrases:
            client = STTClient(stt_provider="mock", preset_transcript=phrase)
            raw_transcript = client.transcribe(b"dummy_audio_bytes")
            self.assertEqual(raw_transcript, phrase, f"Raw STT transcript did not match expected: {phrase}")

            conv = Conversation(business_id=1, channel="web_chat")
            db.session.add(conv)
            db.session.commit()

            agent = Agent(business_id=1)
            res = agent.process_message(conv.id, phrase)
            self.assertIsNotNone(res)
            self.assertIn("content", res)


if __name__ == "__main__":
    unittest.main()
