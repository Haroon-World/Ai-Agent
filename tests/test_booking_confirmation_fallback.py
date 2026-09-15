import unittest
from datetime import datetime, timezone, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Conversation, Appointment
from services.booking_service import BookingService
from ai.tools import ToolDispatcher
from ai.agent import Agent


class TestBookingConfirmationFallback(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            WTF_CSRF_ENABLED = False

        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Create Clinic
        self.clinic = Business(
            name="Care PolyClinic",
            business_type="polyclinic",
            address="45-A Main St",
            phone="+924235789000",
            subscription_status="active",
            subscription_expires_at=datetime.now(timezone.utc) + timedelta(days=60)
        )
        db.session.add(self.clinic)
        db.session.commit()

        # Create Doctor
        self.doctor = Doctor(
            business_id=self.clinic.id,
            name="Soha Fatima",
            specialization="Cardiologist",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30
        )
        db.session.add(self.doctor)
        db.session.commit()

        # Create Service
        self.service = Service(
            business_id=self.clinic.id,
            doctor_id=self.doctor.id,
            name="Consultation & Checkup",
            duration=30,
            price=2000.0
        )
        db.session.add(self.service)
        db.session.commit()

        # Create Conversation with pre-collected details
        self.conv = Conversation(
            business_id=self.clinic.id,
            status="AI",
            channel="web_chat",
            selected_doctor_id=self.doctor.id,
            selected_service_id=self.service.id,
            requested_date="2026-09-16",
            requested_time="11:30",
            pending_customer_name="Ali Haider",
            pending_customer_phone="03197155071",
            awaiting_input="confirmation"
        )
        db.session.add(self.conv)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_booking_service_falls_back_to_conversation_phone_and_name(self):
        """Verify BookingService directly uses conversation state when customer_phone/name are omitted."""
        result = BookingService.book_appointment(
            business_id=self.clinic.id,
            customer_name="",     # Omitted by caller
            customer_phone="",    # Omitted by caller
            doctor_id=self.doctor.id,
            service_id=self.service.id,
            appointment_date="2026-09-16",
            appointment_time="11:30",
            conversation_id=self.conv.id
        )

        self.assertTrue(result.get("success"), f"Expected success but got: {result}")
        appt = result["appointment"]
        self.assertEqual(appt["customer_name"], "Ali Haider")
        self.assertEqual(appt["customer_phone"], "03197155071")

    def test_tool_dispatcher_falls_back_when_llm_omits_phone(self):
        """Verify ToolDispatcher handles missing customer_phone by pulling from conversation."""
        dispatcher = ToolDispatcher(business_id=self.clinic.id, conversation_id=self.conv.id)
        # LLM only passed doctor, service, date, time and name, omitting customer_phone
        result = dispatcher.execute("book_appointment", {
            "doctor_id": self.doctor.id,
            "service_id": self.service.id,
            "appointment_date": "2026-09-16",
            "appointment_time": "11:30",
            "customer_name": "Ali Haider"
        })

        self.assertTrue(result.get("success"), f"Expected success but got: {result}")
        appt = result["appointment"]
        self.assertEqual(appt["customer_phone"], "03197155071")

    def test_tool_dispatcher_resolves_argument_aliases(self):
        """Verify ToolDispatcher maps aliases like 'phone' and 'patient_name' correctly."""
        dispatcher = ToolDispatcher(business_id=self.clinic.id, conversation_id=self.conv.id)
        result = dispatcher.execute("book_appointment", {
            "doctor_id": self.doctor.id,
            "service_id": self.service.id,
            "date": "2026-09-16",
            "time": "11:30",
            "patient_name": "Ali Haider",
            "phone": "03197155071"
        })

        self.assertTrue(result.get("success"), f"Expected success but got: {result}")
        appt = result["appointment"]
        self.assertEqual(appt["customer_name"], "Ali Haider")
        self.assertEqual(appt["customer_phone"], "03197155071")

    def test_agent_auto_triggers_booking_on_confirm_appointment(self):
        """Verify Agent books the appointment when patient replies 'Confirm Appointment'."""
        agent = Agent(business_id=self.clinic.id)
        result = agent.process_message(self.conv.id, "Confirm Appointment")

        # Must not have the error message
        self.assertNotIn("Missing required booking fields", result["content"])
        # Must confirm the booking
        self.assertEqual(result["workflow_state"], "BOOKED")
        # Ensure appointment was created in DB
        appt = Appointment.query.filter_by(business_id=self.clinic.id, conversation_id=self.conv.id).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.customer.name, "Ali Haider")
        self.assertEqual(appt.customer.phone, "03197155071")


if __name__ == "__main__":
    unittest.main()
