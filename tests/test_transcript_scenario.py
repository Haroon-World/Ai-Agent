import unittest
from datetime import date, timedelta
import freezegun
from app import create_app
from models import db, Business, Doctor, Service, Conversation, Appointment
from ai.agent import Agent

class TestTranscriptScenarioFixes(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"

        self.app_context = self.app.app_context()
        self.app_context.push()

        db.create_all()
        self._seed_data()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _seed_data(self):
        clinic = db.session.get(Business, 1)
        if not clinic:
            clinic = Business(id=1, name="SmileCare Dental Clinic", business_type="dental_clinic", address="Plot 42-B, Main Boulevard", phone="+92 42 35789000", timezone="Asia/Karachi", opening_hours="09:00 AM - 05:00 PM")
            db.session.add(clinic)

        doc = db.session.get(Doctor, 1)
        if doc:
            doc.working_days = "Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday"
            doc.start_time = "09:00"
            doc.end_time = "17:00"
            doc.slot_interval = 30
            doc.is_active = True
            for s in doc.schedules:
                s.is_available = True
                s.start_time = "09:00"
                s.end_time = "17:00"
        else:
            doc = Doctor(id=1, business_id=1, name="Dr. Ahmed Khan", specialization="General Dentistry", working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday", start_time="09:00", end_time="17:00", slot_interval=30, is_active=True)
            db.session.add(doc)
            db.session.flush()
            from models import DoctorSchedule, DAYS_OF_WEEK
            for day in DAYS_OF_WEEK:
                db.session.add(DoctorSchedule(doctor_id=doc.id, day_of_week=day, is_available=True, start_time="09:00", end_time="17:00"))

        svc = db.session.get(Service, 2)
        if not svc:
            svc = Service(id=2, business_id=1, name="Dental Cleaning & Scaling", description="Cleaning", duration=45, price=4000.0)
            db.session.add(svc)

        db.session.commit()

    def test_full_user_transcript_flow(self):
        """Recreate the exact 4-turn user conversation transcript and verify proper handling at each turn."""
        with freezegun.freeze_time("2026-08-31 08:00:00+05:00"):
            conv = Conversation(business_id=1, status="AI")
            db.session.add(conv)
            db.session.commit()

            agent = Agent(business_id=1, llm_provider="mock")

            # Turn 1: Initial request
            res1 = agent.process_message(conv.id, "Hi, I need to book a dental cleaning appointment for today.")
            self.assertEqual(res1["status"], "AI")
            self.assertIn("09:00", res1["content"])

            # Turn 2: Question about slots after 12
            res2 = agent.process_message(conv.id, "is this slot available, if we want appointment after 12")
            self.assertEqual(res2["status"], "AI")
            # Must explicitly acknowledge the 12:00 request or list slots
            self.assertTrue("12:00" in res2["content"] or "no available slots" in res2["content"].lower() or "09:00" in res2["content"])
            self.assertNotIn("Sorry, I didn't quite catch that", res2["content"])

            # Turn 3: Name input
            res3 = agent.process_message(conv.id, "Name is Haroon")
            self.assertEqual(res3["status"], "AI")
            self.assertIn("Haroon", res3["content"])
            self.assertTrue("phone" in res3["content"].lower() or "contact" in res3["content"].lower())

            # Turn 4: Phone input -> MUST COMPLETE BOOKING SUCCESSFULLY!
            res4 = agent.process_message(conv.id, "03197155071")
            self.assertEqual(res4["status"], "AI")
            self.assertNotIn("Sorry, I didn't quite catch that", res4["content"])
            self.assertTrue("confirmed" in res4["content"].lower() or "appointment id" in res4["content"].lower())

            # Verify appointment saved in database!
            appts = Appointment.query.filter_by(business_id=1).order_by(Appointment.id.desc()).all()
            self.assertGreaterEqual(len(appts), 1)
            self.assertEqual(appts[0].customer.name, "Haroon")
            self.assertEqual(appts[0].customer.phone, "03197155071")

if __name__ == "__main__":
    unittest.main()
