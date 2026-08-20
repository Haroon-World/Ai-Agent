import unittest
import os
import sys
from datetime import datetime, timezone, timedelta

# Add workspace to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flask import Flask
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Appointment, Conversation, Message, Reminder
from sqlalchemy.exc import IntegrityError

class TestPhase1ADatabase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        self.app.config["TESTING"] = True
        db.init_app(self.app)

        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_business_and_relationships_creation(self):
        with self.app.app_context():
            # 1. Create Business
            biz = Business(
                name="Test Dental Clinic",
                business_type="dental_clinic",
                address="123 Dental St, Lahore",
                phone="+92 42 11122233",
                timezone="Asia/Karachi",
                opening_hours="Mon-Sat: 09:00 - 17:00",
                policies="Test policies"
            )
            db.session.add(biz)
            db.session.commit()
            self.assertIsNotNone(biz.id)

            # 2. Add Doctor and Service
            doc = Doctor(
                business_id=biz.id,
                name="Dr. Test",
                specialization="General Dentistry",
                working_days="Monday,Tuesday",
                start_time="09:00",
                end_time="17:00"
            )
            svc = Service(
                business_id=biz.id,
                name="Cleaning",
                description="Teeth scaling",
                duration=30,
                price=3000.0
            )
            db.session.add_all([doc, svc])
            db.session.commit()

            # 3. Add Customer
            cust = Customer(
                business_id=biz.id,
                name="John Doe",
                phone="+923001234567"
            )
            db.session.add(cust)
            db.session.commit()

            # 4. Add Appointment
            appt = Appointment(
                business_id=biz.id,
                customer_id=cust.id,
                doctor_id=doc.id,
                service_id=svc.id,
                appointment_date="2026-08-25",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt)
            db.session.commit()

            # 5. Add Reminder
            rem = Reminder(
                business_id=biz.id,
                appointment_id=appt.id,
                scheduled_for=datetime.now(timezone.utc) + timedelta(days=1),
                status="SCHEDULED",
                reminder_type="24H_BEFORE"
            )
            db.session.add(rem)
            db.session.commit()

            # 6. Add Conversation & Messages
            conv = Conversation(
                business_id=biz.id,
                customer_id=cust.id,
                channel="web_chat",
                status="AI",
                intent="BOOK_APPOINTMENT",
                workflow_state="COLLECTING_INFO"
            )
            db.session.add(conv)
            db.session.commit()

            msg1 = Message(conversation_id=conv.id, role="user", content="Hi, I need an appointment")
            msg2 = Message(conversation_id=conv.id, role="assistant", content="Sure! Which service?")
            db.session.add_all([msg1, msg2])
            db.session.commit()

            # Verify cascading & relationships
            self.assertEqual(len(biz.doctors), 1)
            self.assertEqual(len(biz.services), 1)
            self.assertEqual(len(biz.appointments), 1)
            self.assertEqual(len(biz.conversations), 1)
            self.assertEqual(len(conv.messages), 2)
            self.assertEqual(appt.reminders[0].status, "SCHEDULED")

    def test_double_booking_database_constraint(self):
        with self.app.app_context():
            biz = Business(
                name="Clinic A",
                business_type="dental_clinic",
                address="Address A",
                phone="123",
                opening_hours="Mon-Fri 9-5"
            )
            db.session.add(biz)
            db.session.commit()

            doc = Doctor(business_id=biz.id, name="Dr. A", specialization="Dentist")
            svc = Service(business_id=biz.id, name="Checkup", duration=30, price=1000)
            c1 = Customer(business_id=biz.id, name="Cust 1", phone="111")
            c2 = Customer(business_id=biz.id, name="Cust 2", phone="222")
            db.session.add_all([doc, svc, c1, c2])
            db.session.commit()

            # First booking
            appt1 = Appointment(
                business_id=biz.id,
                customer_id=c1.id,
                doctor_id=doc.id,
                service_id=svc.id,
                appointment_date="2026-08-25",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt1)
            db.session.commit()

            # Second booking at exact same date & time for same doctor must violate UniqueConstraint
            appt2 = Appointment(
                business_id=biz.id,
                customer_id=c2.id,
                doctor_id=doc.id,
                service_id=svc.id,
                appointment_date="2026-08-25",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt2)
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

if __name__ == "__main__":
    unittest.main()
