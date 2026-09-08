import unittest
from datetime import datetime, timezone, timedelta
from app import create_app
from config.config import Config
from models import db, Business, User, ClinicInvitation


class TestClinicInvitationAndSetup(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            WTF_CSRF_ENABLED = False
            SECRET_KEY = "test-key-invitation"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()

        db.session.rollback()
        db.session.remove()
        db.drop_all()
        db.create_all()

        # Seed Platform Owner
        platform_owner = User(
            username="platform_admin",
            email="platform@clinicconnectai.com",
            is_platform_admin=True
        )
        platform_owner.set_password("platform_pass_123")
        db.session.add(platform_owner)

        # Seed an existing Clinic and User
        existing_biz = Business(
            name="Existing Dental Care",
            business_type="dental_clinic",
            address="123 Health Ave",
            phone="+92 300 1112222",
            email="existing@clinic.com",
            subscription_status="active"
        )
        db.session.add(existing_biz)
        db.session.flush()

        existing_user = User(
            business_id=existing_biz.id,
            username="existing_admin",
            email="existing@clinic.com",
            is_platform_admin=False
        )
        existing_user.set_password("clinic_pass_123")
        db.session.add(existing_user)
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_platform_admin(self):
        return self.client.post("/platform/login", data={
            "username": "platform_admin",
            "password": "platform_pass_123"
        }, follow_redirects=True)

    # ----------------------------------------------------------------------
    # 1. Live Username Availability API Tests
    # ----------------------------------------------------------------------

    def test_check_username_api_available(self):
        """Available username returns available: True."""
        res = self.client.get("/api/check-username?username=brand_new_user")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("available"))
        self.assertTrue(data.get("valid"))

    def test_check_username_api_taken_with_suggestions(self):
        """Taken username returns available: False and provides available suggestions."""
        res = self.client.get("/api/check-username?username=existing_admin")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data.get("available"))
        self.assertTrue(data.get("valid"))
        self.assertIn("already taken", data.get("message", ""))
        self.assertIsInstance(data.get("suggestions"), list)
        self.assertGreater(len(data.get("suggestions")), 0)

    def test_check_username_api_invalid_characters_and_length(self):
        """Invalid characters or short length return valid: False."""
        res1 = self.client.get("/api/check-username?username=ab")
        self.assertFalse(res1.get_json().get("valid"))

        res2 = self.client.get("/api/check-username?username=bad username with spaces!")
        self.assertFalse(res2.get_json().get("valid"))

    # ----------------------------------------------------------------------
    # 2. Email Invitation Flow (Option 2)
    # ----------------------------------------------------------------------

    def test_platform_admin_sends_onboarding_invite(self):
        """Platform admin submits invite form; Business & ClinicInvitation are created and email dispatched."""
        self._login_platform_admin()

        invite_data = {
            "onboard_mode": "invite",
            "clinic_name": "Horizon Eye Hospital",
            "business_type": "eye_clinic",
            "address": "45 Mall Road, Lahore",
            "phone": "+92 42 37890000",
            "timezone": "Asia/Karachi",
            "admin_email": "dr.horizon@eyecare.com",
            "plan_type": "trial_30"
        }
        res = self.client.post("/platform/onboard-clinic", data=invite_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Invitation successfully dispatched", res.data)

        with self.app.app_context():
            biz = Business.query.filter_by(name="Horizon Eye Hospital").first()
            self.assertIsNotNone(biz)
            self.assertEqual(biz.business_type, "eye_clinic")
            self.assertEqual(biz.subscription_status, "trial")

            invitation = ClinicInvitation.query.filter_by(business_id=biz.id).first()
            self.assertIsNotNone(invitation)
            self.assertEqual(invitation.email, "dr.horizon@eyecare.com")
            self.assertFalse(invitation.is_used)
            self.assertTrue(invitation.is_valid())

    def test_client_accesses_setup_page_with_valid_token(self):
        """Client opens /setup-clinic/<token> and sees personalized setup form."""
        with self.app.app_context():
            biz = Business.query.filter_by(name="Existing Dental Care").first()
            inv = ClinicInvitation.create_invitation(biz.id, "dr.partner@clinic.com")
            db.session.commit()
            token = inv.token

        res = self.client.get(f"/setup-clinic/{token}")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Activate Clinic Portal", res.data)
        self.assertIn(b"Existing Dental Care", res.data)
        self.assertIn(b"dr.partner@clinic.com", res.data)

    def test_client_completes_setup_choosing_own_credentials_and_auto_logs_in(self):
        """Client submits setup form choosing custom username & password; User is created, invitation marked used, and auto-logged in."""
        with self.app.app_context():
            biz = Business(name="Skyline Pediatric Clinic", address="Park Towers", phone="+92 321 9998888")
            db.session.add(biz)
            db.session.flush()
            inv = ClinicInvitation.create_invitation(biz.id, "dr.skyline@clinic.com")
            db.session.commit()
            token = inv.token
            biz_id = biz.id

        post_data = {
            "token": token,
            "username": "dr_skyline_custom",
            "password": "secure_clinic_pass_2026",
            "confirm_password": "secure_clinic_pass_2026"
        }
        res = self.client.post(f"/setup-clinic/{token}", data=post_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Welcome to ClinicConnectAI", res.data)

        with self.app.app_context():
            # Verify User created
            u = User.query.filter_by(username="dr_skyline_custom").first()
            self.assertIsNotNone(u)
            self.assertEqual(u.business_id, biz_id)
            self.assertEqual(u.email, "dr.skyline@clinic.com")
            self.assertTrue(u.check_password("secure_clinic_pass_2026"))

            # Verify invitation is marked used
            inv_db = ClinicInvitation.query.filter_by(token=token).first()
            self.assertTrue(inv_db.is_used)
            self.assertFalse(inv_db.is_valid())

        # Verify session was established
        with self.client.session_transaction() as sess:
            self.assertIsNotNone(sess.get("user_id"))
            self.assertEqual(sess.get("business_id"), biz_id)

    def test_client_setup_rejects_duplicate_username(self):
        """Client setup rejects a username that is already taken."""
        with self.app.app_context():
            biz = Business(name="Alpha Clinic", address="Alpha St", phone="+92 300 0000000")
            db.session.add(biz)
            db.session.flush()
            inv = ClinicInvitation.create_invitation(biz.id, "alpha@clinic.com")
            db.session.commit()
            token = inv.token

        # Try to take "existing_admin"
        post_data = {
            "token": token,
            "username": "existing_admin",
            "password": "some_password_123",
            "confirm_password": "some_password_123"
        }
        res = self.client.post(f"/setup-clinic/{token}", data=post_data, follow_redirects=True)
        self.assertIn(b"already taken across the platform", res.data)

        # Confirm no duplicate user was created
        with self.app.app_context():
            self.assertEqual(User.query.filter_by(username="existing_admin").count(), 1)

    def test_reusing_consumed_invitation_token_is_blocked(self):
        """Once an invitation has been used, accessing it again redirects with an error."""
        with self.app.app_context():
            biz = Business.query.filter_by(name="Existing Dental Care").first()
            inv = ClinicInvitation.create_invitation(biz.id, "used@clinic.com")
            inv.mark_used()
            db.session.commit()
            token = inv.token

        res = self.client.get(f"/setup-clinic/{token}", follow_redirects=True)
        self.assertIn(b"invitation link is invalid, expired, or has already been used", res.data)

    # ----------------------------------------------------------------------
    # 3. Manual Direct Setup Flow (Preserved 100%)
    # ----------------------------------------------------------------------

    def test_manual_mode_onboarding_still_works_identically(self):
        """Platform admin uses manual mode to provision clinic and user instantly."""
        self._login_platform_admin()

        manual_data = {
            "onboard_mode": "manual",
            "clinic_name": "Apex Prime Care",
            "business_type": "polyclinic",
            "address": "88 Boulevard",
            "phone": "+92 300 9991111",
            "timezone": "Asia/Karachi",
            "admin_username": "apex_prime_admin",
            "admin_email": "owner@apexprime.com",
            "admin_password": "prime_password_2026",
            "plan_type": "trial_30"
        }
        res = self.client.post("/platform/onboard-clinic", data=manual_data, follow_redirects=True)
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"onboarded successfully with 1-Month Free Trial", res.data)

        with self.app.app_context():
            biz = Business.query.filter_by(name="Apex Prime Care").first()
            self.assertIsNotNone(biz)
            user = User.query.filter_by(username="apex_prime_admin").first()
            self.assertIsNotNone(user)
            self.assertEqual(user.business_id, biz.id)
            self.assertTrue(user.check_password("prime_password_2026"))


if __name__ == "__main__":
    unittest.main()
