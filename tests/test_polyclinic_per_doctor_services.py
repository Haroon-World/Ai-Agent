import unittest
from datetime import date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Appointment, Conversation
from seed import seed_database
from ai.agent import Agent
from services.booking_service import BookingService, RequestCache

class TestPolyclinicPerDoctorServices(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.ctx = self.app.app_context()
        self.ctx.push()
        RequestCache.clear()
        seed_database(self.app)

        self.biz = Business.query.first()
        self.biz_id = self.biz.id
        self.dr_ahmed = Doctor.query.filter_by(business_id=self.biz_id, name="Dr. Ahmed Khan").first()
        self.dr_sara = Doctor.query.filter_by(business_id=self.biz_id, name="Dr. Sara Malik").first()
        self.agent = Agent(business_id=self.biz_id)

    def tearDown(self):
        RequestCache.clear()
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_schema_service_belongs_to_doctor(self):
        """Test that every service belongs to a specific doctor."""
        services = Service.query.filter_by(business_id=self.biz_id).all()
        self.assertGreater(len(services), 0)
        for s in services:
            self.assertIsNotNone(s.doctor_id)
            self.assertIsNotNone(s.doctor)

        ahmed_services = Service.query.filter_by(doctor_id=self.dr_ahmed.id).all()
        sara_services = Service.query.filter_by(doctor_id=self.dr_sara.id).all()
        self.assertGreaterEqual(len(ahmed_services), 2)
        self.assertGreaterEqual(len(sara_services), 2)

    def test_services_inquiry_with_no_doctor_selected(self):
        """Test asking 'what services do you offer' with NO doctor selected yet."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "what services do you offer?")
        content = res.get("content", "")
        
        # Confirm it asks which doctor or presents doctors, not a flat list
        self.assertTrue(
            "which doctor" in content.lower() or "dr. ahmed" in content.lower() or "dr. sara" in content.lower(),
            f"Expected prompt to ask for doctor, got: {content}"
        )
        self.assertNotIn("Here is our complete list of dental services", content)

    def test_services_inquiry_for_dr_ahmed(self):
        """Test selecting Dr. Ahmed then asking about services - only HIS services appear."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Select Dr. Ahmed first
        self.agent.process_message(conv.id, "I want an appointment with Dr. Ahmed")
        self.assertEqual(conv.selected_doctor_id, self.dr_ahmed.id)

        # Ask for services
        res = self.agent.process_message(conv.id, "what services do you offer?")
        content = res.get("content", "")

        ahmed_svc_names = [s.name for s in Service.query.filter_by(doctor_id=self.dr_ahmed.id).all()]
        sara_only_svc_names = [s.name for s in Service.query.filter_by(doctor_id=self.dr_sara.id).all() if s.name not in ahmed_svc_names]

        # Confirm Ahmed's service is present
        self.assertTrue(any(name.lower() in content.lower() for name in ahmed_svc_names), f"Ahmed services should appear in: {content}")
        # Confirm Sara-only service (like Teeth Whitening) is NOT present in Dr. Ahmed's list
        for sara_svc in sara_only_svc_names:
            self.assertNotIn(sara_svc.lower(), content.lower())

    def test_services_inquiry_for_dr_sara(self):
        """Test selecting Dr. Sara then asking about services - only HER services appear."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Select Dr. Sara first
        self.agent.process_message(conv.id, "I want an appointment with Dr. Sara")
        self.assertEqual(conv.selected_doctor_id, self.dr_sara.id)

        # Ask for services
        res = self.agent.process_message(conv.id, "what services do you offer?")
        content = res.get("content", "")

        sara_svc_names = [s.name for s in Service.query.filter_by(doctor_id=self.dr_sara.id).all()]

        self.assertTrue(any(name.lower() in content.lower() for name in sara_svc_names), f"Sara services should appear in: {content}")
        self.assertNotIn("Root Canal Treatment", content)

    def test_booking_validation_rejects_mismatched_service_and_doctor(self):
        """Test attempting to book a service with a doctor who doesn't offer it."""
        root_canal = Service.query.filter(Service.name.ilike("%root canal%")).first()
        self.assertIsNotNone(root_canal)
        self.assertEqual(root_canal.doctor_id, self.dr_ahmed.id)

        # Attempt to book Root Canal with Dr. Sara Malik (who does NOT offer Root Canal)
        res = BookingService.book_appointment(
            business_id=self.biz_id,
            customer_name="Test Patient",
            customer_phone="03001234567",
            doctor_id=self.dr_sara.id,
            service_id=root_canal.id,
            appointment_date=(date.today() + timedelta(days=2)).strftime("%Y-%m-%d"),
            appointment_time="10:00"
        )

        self.assertFalse(res["success"])
        self.assertIn("does not offer", res["error"])
        self.assertIn("Dr. Sara Malik", res["error"])

    def test_mid_conversation_doctor_switch_clears_invalid_service(self):
        """
        Test mid-conversation doctor switching:
        Select Dr. Ahmed -> select Root Canal Treatment -> switch to Dr. Sara ->
        verify conv.selected_service_id is cleared and time reset.
        """
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Select Dr. Ahmed
        self.agent.process_message(conv.id, "I want to see Dr. Ahmed")
        self.assertEqual(conv.selected_doctor_id, self.dr_ahmed.id)

        # Step 2: Select Root Canal Treatment (only Ahmed offers)
        self.agent.process_message(conv.id, "I need a Root Canal Treatment")
        root_canal = Service.query.filter(Service.name.ilike("%root canal%")).first()
        self.assertEqual(conv.selected_service_id, root_canal.id)

        # Step 3: Switch to Dr. Sara mid-conversation
        res_switch = self.agent.process_message(conv.id, "Actually, I want Dr. Sara instead")
        self.assertEqual(conv.selected_doctor_id, self.dr_sara.id)

        # Verify selected_service_id was cleared because Dr. Sara does NOT offer Root Canal
        self.assertIsNone(conv.selected_service_id, "Incompatible service must be cleared when switching doctors")

    def test_full_booking_flow_dr_ahmed(self):
        """Test full natural booking flow for Dr. Ahmed end-to-end."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Select Dr. Ahmed
        self.agent.process_message(conv.id, "I'd like to book an appointment with Dr. Ahmed Khan")
        self.assertEqual(conv.selected_doctor_id, self.dr_ahmed.id)

        # Step 2: Select Root Canal
        self.agent.process_message(conv.id, "Root Canal Treatment")
        root_canal = Service.query.filter(Service.name.ilike("%root canal%")).first()
        self.assertEqual(conv.selected_service_id, root_canal.id)

        # Step 3: Date
        target_date = date.today() + timedelta(days=3)
        while target_date.strftime("%A") == "Sunday":
            target_date += timedelta(days=1)
        date_str = target_date.strftime("%Y-%m-%d")

        self.agent.process_message(conv.id, f"On {date_str}")
        self.assertEqual(conv.requested_date, date_str)

        # Step 4: Time
        self.agent.process_message(conv.id, "11:00")
        self.assertEqual(conv.requested_time, "11:00")

        # Step 5 & 6: Name and Phone
        self.agent.process_message(conv.id, "My name is Usman Ali")
        r_phone = self.agent.process_message(conv.id, "My phone is 03009876543")

        # Confirm booking created
        appt = Appointment.query.filter_by(
            business_id=self.biz_id,
            doctor_id=self.dr_ahmed.id,
            appointment_date=date_str,
            appointment_time="11:00"
        ).first()

        self.assertIsNotNone(appt)
        self.assertEqual(appt.service_id, root_canal.id)

    def test_full_booking_flow_dr_sara(self):
        """Test full natural booking flow for Dr. Sara end-to-end."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        # Step 1: Select Dr. Sara
        self.agent.process_message(conv.id, "I want to see Dr. Sara Malik")
        self.assertEqual(conv.selected_doctor_id, self.dr_sara.id)

        # Step 2: Select Dental Cleaning
        self.agent.process_message(conv.id, "Dental Cleaning & Scaling")
        cleaning = Service.query.filter(Service.name.ilike("%cleaning%")).first()
        self.assertEqual(conv.selected_service_id, cleaning.id)

        # Step 3: Date
        target_date = date.today() + timedelta(days=4)
        while target_date.strftime("%A") == "Sunday":
            target_date += timedelta(days=1)
        date_str = target_date.strftime("%Y-%m-%d")

        self.agent.process_message(conv.id, f"On {date_str}")
        self.assertEqual(conv.requested_date, date_str)

        # Step 4: Time
        self.agent.process_message(conv.id, "14:00")
        self.assertEqual(conv.requested_time, "14:00")

        # Step 5 & 6: Name and Phone
        self.agent.process_message(conv.id, "My name is Hira Khan")
        self.agent.process_message(conv.id, "My phone is 03215554433")

        # Confirm booking created
        appt = Appointment.query.filter_by(
            business_id=self.biz_id,
            doctor_id=self.dr_sara.id,
            appointment_date=date_str,
            appointment_time="14:00"
        ).first()

        self.assertIsNotNone(appt)
        self.assertEqual(appt.service_id, cleaning.id)

    def test_booking_request_without_doctor_prompts_doctor_choice(self):
        """Test that 'I want to book an appointment tomorrow' does not silently inject Dr. Ahmed."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "I want to book an appointment tomorrow")
        # Ensure check_availability was NOT called with doctor_id=1
        for tool in res.get("executed_tools", []):
            self.assertNotEqual(tool.get("args", {}).get("doctor_id"), self.dr_ahmed.id)

        # Ensure doctor selection is prompted
        self.assertIn("Dr. Ahmed Khan", res["content"])
        self.assertIn("Dr. Sara Malik", res["content"])
        self.assertIsNone(conv.selected_doctor_id)

    def test_multi_field_request_without_doctor_prompts_doctor_choice(self):
        """Test that full details without a doctor prompt for doctor choice instead of auto-booking Dr. Ahmed."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "My name is Ali, phone 03001234567, book tomorrow at 2pm")
        # Ensure no appointment was booked
        self.assertEqual(res.get("executed_tools", []), [])
        self.assertIn("Dr. Ahmed Khan", res["content"])
        self.assertIn("Dr. Sara Malik", res["content"])
        self.assertEqual(res.get("ui_action", {}).get("type"), "doctor_selection")
        self.assertIsNone(conv.selected_doctor_id)

    def test_reschedule_request_without_doctor_prompts_doctor_choice(self):
        """Test that reschedule request without doctor prompts for doctor choice."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "reschedule to 3pm")
        self.assertEqual(res.get("executed_tools", []), [])
        self.assertIn("Dr. Ahmed Khan", res["content"])
        self.assertIn("Dr. Sara Malik", res["content"])

    def test_service_request_before_doctor_resolves_correct_doctor(self):
        """Test that requesting Teeth Whitening resolves Dr. Sara rather than defaulting to Dr. Ahmed."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "is teeth whitening available tomorrow?")
        tools = res.get("executed_tools", [])
        self.assertGreater(len(tools), 0)
        check_tool = tools[0]
        self.assertEqual(check_tool["name"], "check_availability")
        self.assertEqual(check_tool["args"]["doctor_id"], self.dr_sara.id)

    def test_doctor_explicitly_specified_checks_that_doctor(self):
        """Test that explicitly specifying Dr. Sara checks only Dr. Sara."""
        conv = Conversation(business_id=self.biz_id, status="AI")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "Can I see Dr. Sara tomorrow?")
        tools = res.get("executed_tools", [])
        self.assertGreater(len(tools), 0)
        check_tool = tools[0]
        self.assertEqual(check_tool["name"], "check_availability")
        self.assertEqual(check_tool["args"]["doctor_id"], self.dr_sara.id)

if __name__ == "__main__":
    unittest.main()
