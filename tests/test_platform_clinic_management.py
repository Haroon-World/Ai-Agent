import os
import unittest
from datetime import datetime, timezone, timedelta
from app import create_app
from config.config import Config
from models import (
    db, Business, User, Doctor, Service, Appointment,
    Conversation, Message, Reminder
)
from seed import seed_database


class TestPlatformClinicManagement(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-platform-secret"
            WTF_CSRF_ENABLED = False
            PLATFORM_ADMIN_USERNAME = "superadmin"
            PLATFORM_ADMIN_PASSWORD = "SecretAdminPass123"

        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        seed_database(self.app)

        self.client = self.app.test_client()

        # Find seeded superadmin and set known password
        self.superadmin = User.query.filter_by(is_platform_admin=True).first()
        if not self.superadmin:
            self.superadmin = User(
                username="superadmin_test",
                email="superadmin_test@platform.test",
                is_platform_admin=True
            )
            db.session.add(self.superadmin)
        self.superadmin.set_password("SecretAdminPass123")
        db.session.commit()

        # Create a dedicated clinic for test isolation
        self.clinic = Business(
            name="Apex Dental Care",
            business_type="dental_clinic",
            address="123 Blue Ridge Ave",
            phone="+923001234567",
            email="contact@apexdental.test",
            subscription_status="trial",
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=25)
        )
        db.session.add(self.clinic)
        db.session.commit()

        # Create clinic admin user
        self.clinic_admin = User(
            username="apex_admin_user",
            email="admin_user@apexdental.test",
            business_id=self.clinic.id,
            is_platform_admin=False
        )
        self.clinic_admin.set_password("ApexSecretPass123")
        db.session.add(self.clinic_admin)

        # Create Doctor & Service
        self.doctor = Doctor(
            business_id=self.clinic.id,
            name="Zain Malik",
            specialization="Orthodontist"
        )
        db.session.add(self.doctor)
        db.session.commit()

        self.service = Service(
            business_id=self.clinic.id,
            doctor_id=self.doctor.id,
            name="Teeth Whitening",
            duration=45,
            price=5000.0
        )
        db.session.add(self.service)
        db.session.commit()

        # Create Conversation & Message
        self.conv = Conversation(
            business_id=self.clinic.id,
            status="AI",
            channel="web_chat",
            pending_customer_name="Ali Khan"
        )
        db.session.add(self.conv)
        db.session.commit()

        self.msg = Message(
            conversation_id=self.conv.id,
            role="user",
            content="Hi, I need an appointment"
        )
        db.session.add(self.msg)

        # Create Customer
        from models.customer import Customer
        self.customer = Customer(
            business_id=self.clinic.id,
            name="Ali Khan",
            phone="+923331112233"
        )
        db.session.add(self.customer)
        db.session.commit()

        # Create Appointment
        self.appt = Appointment(
            business_id=self.clinic.id,
            customer_id=self.customer.id,
            doctor_id=self.doctor.id,
            service_id=self.service.id,
            appointment_date="2026-09-20",
            appointment_time="14:00",
            status="CONFIRMED",
            conversation_id=self.conv.id
        )
        db.session.add(self.appt)
        db.session.commit()

        self.clinic_id = self.clinic.id
        self.superadmin_id = self.superadmin.id
        self.clinic_admin_id = self.clinic_admin.id
        self.doctor_id = self.doctor.id
        self.service_id = self.service.id
        self.appt_id = self.appt.id
        self.conv_id = self.conv.id
        self.msg_id = self.msg.id

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_as_platform_admin(self):
        with self.client.session_transaction() as sess:
            sess["platform_admin_id"] = self.superadmin_id
            sess["platform_admin_user"] = self.superadmin.username
            sess["is_platform_admin"] = True
            sess["user_id"] = self.superadmin_id
            sess["admin_user"] = self.superadmin.username

    def test_unauthenticated_blocked_from_platform_dashboard_and_detail(self):
        res_dash = self.client.get("/platform/dashboard", follow_redirects=False)
        self.assertEqual(res_dash.status_code, 302)
        self.assertIn("/platform/login", res_dash.headers["Location"])

        res_detail = self.client.get(f"/platform/clinic/{self.clinic_id}", follow_redirects=False)
        self.assertEqual(res_detail.status_code, 302)
        self.assertIn("/platform/login", res_detail.headers["Location"])

    def test_simplified_dashboard_table_renders_correctly(self):
        self._login_as_platform_admin()
        res = self.client.get("/platform/dashboard")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Main headers must be simplified
        self.assertIn("Clinic Name", html)
        self.assertIn("Status", html)
        self.assertIn("Access Portal", html)
        self.assertIn("Action", html)

        # Clinic row details
        self.assertIn("Apex Dental Care", html)
        self.assertIn(f"/platform/clinic/{self.clinic_id}", html)
        self.assertIn("Client Login ↗", html)
        self.assertIn("Manage Clinic →", html)

    def test_clinic_detail_view_renders_all_sections(self):
        self._login_as_platform_admin()
        res = self.client.get(f"/platform/clinic/{self.clinic_id}")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Header banner
        self.assertIn("Apex Dental Care", html)
        self.assertIn("Back to Clinics Directory", html)
        self.assertIn("Client Login Portal ↗", html)

        # Section 1: Subscription Status & Controls
        self.assertIn("Subscription Status &amp; Controls", html)
        self.assertIn("+30 Days", html)
        self.assertIn("Set Date Range", html)
        self.assertIn("Cancel Subscription", html)

        # Section 2: Admin Account
        self.assertIn("Admin Account", html)
        self.assertIn(self.clinic_admin.username, html)
        self.assertIn("Reset Password", html)
        self.assertIn("Edit Email", html)

        # Section 3: Capacity
        self.assertIn("Clinic Capacity &amp; Resource Records", html)
        self.assertIn("Dr. Zain Malik", html)
        self.assertIn("Teeth Whitening", html)

        # Section 4: Danger Zone
        self.assertIn("Danger Zone: Permanent Clinic Deletion", html)
        self.assertIn("Delete Clinic Permanently", html)

    def test_clinic_detail_extend_subscription(self):
        self._login_as_platform_admin()
        res = self.client.post(
            f"/platform/clinic/{self.clinic_id}/subscription/extend",
            data={"days": "30", "next": f"/platform/clinic/{self.clinic_id}"},
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("subscription extended by 30 days", html)

    def test_clinic_detail_update_email_and_reset_password(self):
        self._login_as_platform_admin()

        # Update email
        res_email = self.client.post(
            f"/platform/clinic/{self.clinic_id}/update-email",
            data={"new_email": "updated_apex_email@clinic.test", "next": f"/platform/clinic/{self.clinic_id}"},
            follow_redirects=True
        )
        self.assertEqual(res_email.status_code, 200)
        self.assertIn("successfully updated", res_email.get_data(as_text=True))

        updated_user = db.session.get(User, self.clinic_admin.id)
        self.assertEqual(updated_user.email, "updated_apex_email@clinic.test")

        # Reset password
        res_pass = self.client.post(
            f"/platform/clinic/{self.clinic_id}/reset-password",
            data={"new_password": "BrandNewApexPass999", "next": f"/platform/clinic/{self.clinic_id}"},
            follow_redirects=True
        )
        self.assertEqual(res_pass.status_code, 200)
        self.assertIn("updated successfully", res_pass.get_data(as_text=True))

        updated_user = db.session.get(User, self.clinic_admin.id)
        self.assertTrue(updated_user.check_password("BrandNewApexPass999"))

    def test_delete_clinic_fails_with_wrong_password(self):
        self._login_as_platform_admin()
        res = self.client.post(
            f"/platform/clinic/{self.clinic_id}/delete",
            data={"admin_password": "WrongPassword999"},
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("Authentication failed: Incorrect platform admin password", html)

        # Ensure clinic still exists
        clinic_check = db.session.get(Business, self.clinic_id)
        self.assertIsNotNone(clinic_check)
        self.assertEqual(clinic_check.name, "Apex Dental Care")

    def test_delete_clinic_succeeds_with_correct_password(self):
        self._login_as_platform_admin()
        res = self.client.post(
            f"/platform/clinic/{self.clinic_id}/delete",
            data={"admin_password": "SecretAdminPass123"},
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("permanently deleted", html)

        # Verify clinic and all child entities were deleted
        self.assertIsNone(db.session.get(Business, self.clinic_id))
        self.assertIsNone(db.session.get(User, self.clinic_admin_id))
        self.assertIsNone(db.session.get(Doctor, self.doctor_id))
        self.assertIsNone(db.session.get(Service, self.service_id))
        self.assertIsNone(db.session.get(Appointment, self.appt_id))
        self.assertIsNone(db.session.get(Conversation, self.conv_id))
        self.assertIsNone(db.session.get(Message, self.msg_id))


if __name__ == "__main__":
    unittest.main()
