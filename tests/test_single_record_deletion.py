import unittest
from datetime import datetime
from app import create_app
from config.config import Config
from models import db, Business, Appointment, Customer, Doctor, Service, Conversation, Message, Reminder
from seed import seed_database


class TestSingleRecordDeletion(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        seed_database(self.app)

        self.client = self.app.test_client()
        biz = Business.query.first()
        self.business_id = biz.id if biz else 1
        self.doc = Doctor.query.filter_by(business_id=self.business_id).first()
        self.svc = Service.query.filter_by(business_id=self.business_id).first()

        # Create test customer
        self.cust = Customer(
            business_id=self.business_id,
            name="Hamza Tariq",
            phone="+923009998877"
        )
        db.session.add(self.cust)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["business_id"] = self.business_id
            sess["is_platform_admin"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_delete_single_appointment_success(self):
        # Create an appointment with a reminder
        appt = Appointment(
            business_id=self.business_id,
            customer_id=self.cust.id,
            doctor_id=self.doc.id,
            service_id=self.svc.id,
            appointment_date="2026-09-20",
            appointment_time="11:00 AM",
            status="CONFIRMED"
        )
        db.session.add(appt)
        db.session.flush()

        rem = Reminder(
            business_id=self.business_id,
            appointment_id=appt.id,
            scheduled_for=datetime.now(),
            reminder_type="24h",
            status="SCHEDULED"
        )
        db.session.add(rem)
        db.session.commit()

        appt_id = appt.id
        rem_id = rem.id

        # Delete single appointment
        res = self.client.post(
            "/api/admin/appointments/delete-single",
            json={"appointment_id": appt_id}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))

        # Verify appointment and cascaded reminder are gone
        self.assertIsNone(Appointment.query.get(appt_id))
        self.assertIsNone(Reminder.query.get(rem_id))

    def test_delete_single_appointment_invalid_id(self):
        res = self.client.post(
            "/api/admin/appointments/delete-single",
            json={"appointment_id": 999999}
        )
        self.assertEqual(res.status_code, 404)
        data = res.get_json()
        self.assertFalse(data.get("success"))

    def test_delete_single_conversation_with_linked_appointment(self):
        # Create conversation
        conv = Conversation(
            business_id=self.business_id,
            customer_id=self.cust.id,
            channel="web",
            status="AI",
            workflow_state="START"
        )
        db.session.add(conv)
        db.session.flush()

        # Add messages
        m1 = Message(conversation_id=conv.id, role="user", content="Hello clinic")
        m2 = Message(conversation_id=conv.id, role="assistant", content="Hello! How can I help you?")
        db.session.add_all([m1, m2])

        # Add appointment linked to this conversation
        appt = Appointment(
            business_id=self.business_id,
            customer_id=self.cust.id,
            doctor_id=self.doc.id,
            service_id=self.svc.id,
            appointment_date="2026-09-22",
            appointment_time="03:00 PM",
            status="CONFIRMED",
            conversation_id=conv.id
        )
        db.session.add(appt)
        db.session.commit()

        conv_id = conv.id
        appt_id = appt.id

        # Delete single conversation
        res = self.client.post(
            "/api/admin/conversations/delete-single",
            json={"conversation_id": conv_id}
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("success"))

        # Verify conversation and messages are gone
        self.assertIsNone(Conversation.query.get(conv_id))
        self.assertEqual(Message.query.filter_by(conversation_id=conv_id).count(), 0)

        # Verify appointment still exists, and its conversation_id is cleanly unlinked to None
        updated_appt = Appointment.query.get(appt_id)
        self.assertIsNotNone(updated_appt)
        self.assertIsNone(updated_appt.conversation_id)

    def test_delete_single_conversation_invalid_id(self):
        res = self.client.post(
            "/api/admin/conversations/delete-single",
            json={"conversation_id": 999999}
        )
        self.assertEqual(res.status_code, 404)
        data = res.get_json()
        self.assertFalse(data.get("success"))

    def test_unauthenticated_request_blocked(self):
        unauth_client = self.app.test_client()
        res = unauth_client.post(
            "/api/admin/appointments/delete-single",
            json={"appointment_id": 1}
        )
        # Should redirect to login or return 302/401
        self.assertIn(res.status_code, [302, 401])

    def test_ui_templates_render_3dots_action_menu(self):
        # Create an appointment
        appt = Appointment(
            business_id=self.business_id,
            customer_id=self.cust.id,
            doctor_id=self.doc.id,
            service_id=self.svc.id,
            appointment_date="2026-09-25",
            appointment_time="10:00 AM",
            status="CONFIRMED"
        )
        db.session.add(appt)

        conv = Conversation(
            business_id=self.business_id,
            customer_id=self.cust.id,
            channel="web",
            status="AI",
            workflow_state="START"
        )
        db.session.add(conv)
        db.session.commit()

        # Test appointments page HTML
        res_appt = self.client.get("/admin/appointments")
        self.assertEqual(res_appt.status_code, 200)
        html_appt = res_appt.get_data(as_text=True)
        self.assertIn("action-menu-wrap", html_appt)
        self.assertIn("action-menu-btn", html_appt)
        self.assertIn("deleteSingleAppointment", html_appt)
        self.assertIn("Delete Permanently", html_appt)

        # Test conversations page HTML
        res_conv = self.client.get("/admin/conversations")
        self.assertEqual(res_conv.status_code, 200)
        html_conv = res_conv.get_data(as_text=True)
        self.assertIn("action-menu-wrap", html_conv)
        self.assertIn("action-menu-btn", html_conv)
        self.assertIn("deleteSingleConversation", html_conv)
        self.assertIn("Delete Chat", html_conv)
        self.assertIn("copyConvLink", html_conv)
        # Verify standardized filters
        self.assertIn("convFilterToggleBtn", html_conv)
        self.assertIn("convFilterPanel", html_conv)
        self.assertIn("convNameFilter", html_conv)
        self.assertIn("convIdFilter", html_conv)
        self.assertIn("convDatePicker", html_conv)
        self.assertIn("convCounterText", html_conv)
        self.assertIn("convResetAllBtn", html_conv)
        self.assertIn("data-date", html_conv)


if __name__ == "__main__":
    unittest.main()
