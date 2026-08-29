import unittest
from app import create_app
from models import db, Conversation, Appointment
from ai.agent import Agent
from ai.response_generator import detect_language

class TestRomanEnglishMultilingualFlow(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        from tests.test_date_helpers import get_next_open_weekday
        self.target_date = get_next_open_weekday(1, doctor_id=2)
        Appointment.query.filter_by(business_id=1, appointment_date=self.target_date).delete()
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_language_detection(self):
        # Roman Urdu / Roman English
        self.assertEqual(detect_language("dr sara k sath appointment fix kr do"), "roman_urdu")
        self.assertEqual(detect_language("mera naam ahmed hai"), "roman_urdu")
        self.assertEqual(detect_language("kal 11:30 baje book kardo"), "roman_urdu")
        self.assertEqual(detect_language("daant me dard ho rha hai"), "roman_urdu")
        self.assertEqual(detect_language("theek hai confirm kr dein"), "roman_urdu")
        self.assertEqual(detect_language("kia hal chal ha"), "roman_urdu")
        self.assertEqual(detect_language("shukriya"), "roman_urdu")

        # Urdu Script
        self.assertEqual(detect_language("کیا تم میری ڈاکٹر سارا سے appointment fix کرو گے؟"), "urdu")
        self.assertEqual(detect_language("میرا نام احمد ہے"), "urdu")

        # English
        self.assertEqual(detect_language("I want an appointment with Dr. Sara"), "english")
        self.assertEqual(detect_language("Can I see the dentist tomorrow?"), "english")

        # History continuity on language-neutral input (e.g. date/time/phone)
        hist_roman = [{"role": "user", "content": "dr sara k sath appointment book kr do, mera name ahmed hai"}]
        self.assertEqual(detect_language("2026-08-31", hist_roman), "roman_urdu")
        self.assertEqual(detect_language("11:30", hist_roman), "roman_urdu")
        self.assertEqual(detect_language("03187538771", hist_roman), "roman_urdu")

        hist_urdu = [{"role": "user", "content": "کیا تم میری ڈاکٹر سارا سے appointment fix کرو گے؟"}]
        self.assertEqual(detect_language("2026-08-31", hist_urdu), "urdu")

    def test_full_roman_english_booking_workflow(self):
        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: Roman English / Roman Urdu
        t1 = agent.process_message(conv.id, "Dr Sara k sath appointment book kr do, mera naam Ahmed hai")
        self.assertEqual(conv.selected_doctor_id, 2)
        self.assertIn("Ji", t1["content"])
        self.assertIn("Dr. Sara Malik", t1["content"])
        self.assertTrue("date" in t1["content"].lower() or "tareekh" in t1["content"].lower())
        print("\n--- ROMAN URDU TURN 1 ---")
        print(t1["content"])

        # Turn 2: Date
        t2 = agent.process_message(conv.id, self.target_date)
        self.assertEqual(conv.requested_date, self.target_date)
        self.assertIn("available appointment slots yeh hain", t1["content"] + t2["content"])
        self.assertIn("Dr. Sara Malik", t2["content"])
        print("\n--- ROMAN URDU TURN 2 ---")
        print(t2["content"])

        # Turn 3: Time Slot
        t3 = agent.process_message(conv.id, "11:30")
        self.assertEqual(conv.requested_time, "11:30")
        self.assertIn("mehfooz", t3["content"].lower())
        self.assertTrue("phone number" in t3["content"].lower() or "contact number" in t3["content"].lower())
        print("\n--- ROMAN URDU TURN 3 ---")
        print(t3["content"])

        # Turn 4: Phone Number -> Booking Complete
        t4 = agent.process_message(conv.id, "03187538771")
        self.assertEqual(conv.workflow_state, "BOOKED")
        self.assertIn("confirm ho gayi hai", t4["content"])
        self.assertIn("Ahmed", t4["content"])
        print("\n--- ROMAN URDU TURN 4 ---")
        print(t4["content"])

        # Turn 5: Confirm Acknowledgement
        t5 = agent.process_message(conv.id, "Theek hai shukriya")
        self.assertIn("already confirmed hai", t5["content"])
        print("\n--- ROMAN URDU TURN 5 ---")
        print(t5["content"])

if __name__ == "__main__":
    unittest.main()
