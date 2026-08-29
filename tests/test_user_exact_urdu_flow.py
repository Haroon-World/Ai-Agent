import unittest
import json
from datetime import datetime
from app import create_app
from models import db, Business, Doctor, Service, Conversation, Message, Appointment, Customer
from ai.agent import Agent

class TestUserExactUrduFlow(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        from tests.test_date_helpers import get_next_open_weekday, get_next_closed_day
        self.target_date = get_next_open_weekday(1, doctor_id=2)
        self.closed_date = get_next_closed_day(1)
        Appointment.query.filter_by(business_id=1, appointment_date=self.target_date).delete()
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def test_5_turn_exact_urdu_conversation(self):
        # Create fresh conversation
        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: Doctor + Name in Urdu
        t1 = agent.process_message(conv.id, "کیا تم میری ڈاکٹر سارا سے appointment fix کرو گے؟ میرا نام احمد ہے۔")
        self.assertEqual(conv.selected_doctor_id, 2)
        self.assertIn(conv.pending_customer_name, ["احمد", "Ahmed"])
        self.assertEqual(conv.awaiting_input, "date_choice")
        self.assertEqual(t1["status"], "AI")
        self.assertTrue("Ahmed" in t1["content"] or "احمد" in t1["content"])
        self.assertTrue("Sara" in t1["content"] or "سارا" in t1["content"])
        print("\n--- TURN 1 ---")
        print("Reply:", t1["content"])
        print("Metrics:", t1["metrics"])

        # Turn 2: Date
        t2 = agent.process_message(conv.id, self.target_date)
        self.assertEqual(conv.requested_date, self.target_date)
        self.assertEqual(conv.selected_doctor_id, 2)
        self.assertIn(conv.pending_customer_name, ["احمد", "Ahmed"])
        self.assertEqual(len(t2["executed_tools"]), 1)
        self.assertEqual(t2["executed_tools"][0]["name"], "check_availability")
        self.assertEqual(t2["metrics"]["llm_call_2_ms"], 0.0) # LLM Call #2 bypassed!
        print("\n--- TURN 2 ---")
        print("Reply:", t2["content"])
        print("Metrics:", t2["metrics"])

        # Turn 3: Time Slot
        t3 = agent.process_message(conv.id, "11:30")
        self.assertEqual(conv.requested_time, "11:30")
        self.assertEqual(conv.awaiting_input, "phone")
        print("\n--- TURN 3 ---")
        print("Reply:", t3["content"])
        print("Metrics:", t3["metrics"])

        # Turn 4: Phone Number -> Booking is executed!
        t4 = agent.process_message(conv.id, "03187538771")
        self.assertEqual(conv.pending_customer_phone, "03187538771")
        self.assertEqual(conv.workflow_state, "BOOKED")
        self.assertEqual(len(t4["executed_tools"]), 1)
        self.assertEqual(t4["executed_tools"][0]["name"], "book_appointment")
        self.assertIn("کامیابی سے بک", t4["content"])
        print("\n--- TURN 4 ---")
        print("Reply:", t4["content"])
        print("Metrics:", t4["metrics"])

        # Turn 5: User: 'Confirm Appointment' -> Confirms existing booking
        t5 = agent.process_message(conv.id, "Confirm Appointment")
        self.assertEqual(conv.workflow_state, "BOOKED")
        self.assertTrue("تصدیق شدہ" in t5["content"] or "confirmed" in t5["content"].lower())
        print("\n--- TURN 5 ---")
        print("Reply:", t5["content"])
        print("Metrics:", t5["metrics"])

        # Verify DB Appointment
        appt = Appointment.query.filter_by(business_id=1, appointment_date=self.target_date, appointment_time="11:30").first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.doctor_id, 2)
        self.assertIn(appt.customer.name, ["احمد", "Ahmed"])
        self.assertEqual(appt.customer.phone, "03187538771")
        self.assertEqual(appt.status, "CONFIRMED")
        print("\n=== BOOKING VERIFIED IN DATABASE ===")
        print(f"Appointment ID: #{appt.id}, Patient: {appt.customer.name}, Doctor ID: {appt.doctor_id}, Date: {appt.appointment_date} {appt.appointment_time}")

    def test_sunday_off_day_urdu_flow(self):
        """Test Sunday closed day handling, off-day notification, and valid reselection in Urdu."""
        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: Doctor + Name in Urdu
        t1 = agent.process_message(conv.id, "میرا نام حارون ہے اور میری ڈاکٹر سارا سے appointment fix کر دیں for normal checkup.")
        self.assertEqual(conv.selected_doctor_id, 2)
        self.assertEqual(conv.selected_service_id, 1)
        self.assertTrue("Haroon" in t1["content"] or "حارون" in t1["content"])
        self.assertIn("Dr. Sara Malik", t1["content"])

        # Turn 2: Closed Day
        t2 = agent.process_message(conv.id, self.closed_date)
        self.assertTrue("اتوار" in t2["content"] or "بند" in t2["content"] or "off" in t2["content"].lower())
        self.assertIsNone(conv.requested_date) # Reset so user can provide valid date

        # Turn 3: User sends time before selecting valid date
        t3 = agent.process_message(conv.id, "13:30")
        self.assertIn("تاریخ کو تشریف لانا چاہیں گے", t3["content"])

        # Turn 4: Valid open day
        t4 = agent.process_message(conv.id, self.target_date)
        day_str = str(int(self.target_date.split("-")[2]))
        self.assertTrue(day_str in t4["content"] or "09:00 AM" in t4["content"], f"Date/slots not found in: {t4['content'][:100]}")
        self.assertIn("09:00 AM", t4["content"])

        # Turn 5: User chooses time
        t5 = agent.process_message(conv.id, "11:30")
        self.assertIn("فون نمبر", t5["content"])

        # Turn 6: User provides phone -> Booking completes
        t6 = agent.process_message(conv.id, "03001234567")
        self.assertEqual(conv.workflow_state, "BOOKED")
        self.assertIn("کامیابی سے بک", t6["content"])

    def test_asghar_dr_sare_urdu_flow(self):
        """Test Asghar with 'ڈاکٹر سارے' spelling variation and warm reservation."""
        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider="mock")

        # Turn 1: Asghar + Dr. Sare
        t1 = agent.process_message(conv.id, "میرا نام اسگر ہے اور میری ڈاکٹر سارے سے appointment fix کروں گا۔")
        self.assertEqual(conv.selected_doctor_id, 2)
        self.assertIn(conv.pending_customer_name, ["Asghar", "اسگر"])
        self.assertIn("Dr. Sara Malik", t1["content"])
        self.assertEqual(conv.awaiting_input, "date_choice")

        # Turn 2: Date
        t2 = agent.process_message(conv.id, self.target_date)
        self.assertEqual(conv.requested_date, self.target_date)

        # Turn 3: Time Slot
        t3 = agent.process_message(conv.id, "11:30")
        self.assertIn("محفوظ کر لیا ہے", t3["content"])
        self.assertIn("فون نمبر", t3["content"])


if __name__ == "__main__":
    unittest.main()
