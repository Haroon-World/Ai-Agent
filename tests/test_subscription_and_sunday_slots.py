import unittest
from datetime import datetime, timedelta, timezone
from app import create_app
from models import db, Business, Doctor, DoctorSchedule, User
from services.subscription_service import SubscriptionService
from services.booking_service import BookingService
from config.config import Config


class TestSubscriptionAndSundaySlots(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

            # Create test business
            self.biz = Business(
                name="Apex Polyclinic",
                business_type="polyclinic",
                address="Suite 101, Test Blvd",
                phone="+92 300 0000000",
                timezone="Asia/Karachi",
                opening_hours="Monday to Sunday: 09:00 AM - 05:00 PM",
                subscription_status="trial",
                trial_ends_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
            db.session.add(self.biz)
            db.session.flush()

            # Create clinic admin user
            self.admin_user = User(
                business_id=self.biz.id,
                username="apex_admin",
                is_platform_admin=False,
            )
            self.admin_user.set_password("admin_pass_123")
            db.session.add(self.admin_user)

            # Create platform admin user
            self.platform_user = User(
                business_id=None,
                username="super_platform",
                is_platform_admin=True,
            )
            self.platform_user.set_password("super_secret_platform")
            db.session.add(self.platform_user)

            # Create a doctor with Sunday open
            self.doctor = Doctor(
                business_id=self.biz.id,
                name="Dr. Zafar Iqbal",
                specialization="Internal Medicine",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday",
                start_time="09:00",
                end_time="17:00",
                slot_interval=30,
                is_active=True
            )
            db.session.add(self.doctor)
            db.session.flush()

            # Add DoctorSchedule for Sunday (available)
            sched_sun = DoctorSchedule(
                doctor_id=self.doctor.id,
                day_of_week="Sunday",
                is_available=True,
                start_time="09:00",
                end_time="17:00"
            )
            db.session.add(sched_sun)

            # Add consultation service for doctor
            from models import Service
            svc = Service(
                business_id=self.biz.id,
                doctor_id=self.doctor.id,
                name="General Medical Consultation",
                duration=30,
                price=2500.0,
                is_active=True
            )
            db.session.add(svc)

            db.session.commit()
            self.biz_id = self.biz.id
            self.doctor_id = self.doctor.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # ------------------------------------------------------------------
    # 1. Subscription Lifecycle & Expiration Enforcement
    # ------------------------------------------------------------------

    def test_trial_period_active_access(self):
        """Active 30-day trial permits access to clinic portal."""
        with self.app.app_context():
            self.assertTrue(SubscriptionService.check_access(self.biz_id))
            info = SubscriptionService.get_subscription_info(self.biz_id)
            self.assertEqual(info["status"], "trial")
            self.assertTrue(info["is_valid"])
            self.assertGreaterEqual(info["days_remaining"], 29)

    def test_expired_subscription_blocks_portal_access(self):
        """Expired trial or subscription blocks admin portal access and redirects to expired notice."""
        with self.app.app_context():
            # Set trial to the past
            b = db.session.get(Business, self.biz_id)
            b.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=2)
            db.session.commit()

            self.assertFalse(SubscriptionService.check_access(self.biz_id))

        # Attempt to access dashboard as clinic admin with expired subscription
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["business_id"] = self.biz_id
            sess["admin_user"] = "apex_admin"
            sess["is_platform_admin"] = False

        res = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/subscription-expired", res.headers["Location"])

    def test_subscription_extension_restores_access(self):
        """Renewing / extending subscription reactivates clinic access."""
        with self.app.app_context():
            b = db.session.get(Business, self.biz_id)
            b.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=5)
            db.session.commit()
            self.assertFalse(SubscriptionService.check_access(self.biz_id))

            # Extend subscription by 90 days
            ext = SubscriptionService.extend_subscription(self.biz_id, days=90)
            self.assertTrue(ext["success"])
            self.assertEqual(ext["status"], "active")
            self.assertGreaterEqual(ext["days_remaining"], 89)

            self.assertTrue(SubscriptionService.check_access(self.biz_id))

    # ------------------------------------------------------------------
    # 2. Platform Authentication Boundary & Dedicated Onboarding
    # ------------------------------------------------------------------

    def test_platform_credentials_rejected_at_client_login(self):
        """Platform owner credentials entered at /admin/login must be rejected with instruction."""
        res = self.client.post(
            "/admin/login",
            data={"username": "super_platform", "password": "super_secret_platform"},
            follow_redirects=True
        )
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Platform Owner accounts cannot sign in through the Clinic Client Portal", res.data)

    def test_manage_clinic_redirects_to_login(self):
        """Platform owner manage-clinic does not bypass credentials, redirects to login."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["is_platform_admin"] = True
            sess["admin_user"] = "super_platform"

        res = self.client.get(f"/platform/manage-clinic/{self.biz_id}", follow_redirects=False)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin", res.headers["Location"])

    def test_dedicated_onboarding_page_provisions_client_with_trial(self):
        """Dedicated onboarding page creates clinic with 1-month trial and credentials."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = 2
            sess["is_platform_admin"] = True
            sess["admin_user"] = "super_platform"

        # GET dedicated onboard page
        get_res = self.client.get("/platform/onboard-clinic")
        self.assertEqual(get_res.status_code, 200)
        self.assertIn(b"Onboard New Clinic Tenant", get_res.data)

        # POST onboard new clinic
        post_data = {
            "clinic_name": "Sunrise Dental",
            "address": "45-A Model Town, Lahore",
            "phone": "+92 42 35112233",
            "business_type": "dental_clinic",
            "timezone": "Asia/Karachi",
            "opening_hours": "09:00 AM - 05:00 PM",
            "admin_username": "sunrise_admin",
            "admin_password": "secure_password_99",
            "plan_type": "trial_30"
        }
        res = self.client.post("/platform/onboard-clinic", data=post_data, follow_redirects=True)
        self.assertIn(b"Clinic Client Successfully Onboarded!", res.data)
        # Security boundary: Must NOT contain chat or appointment booking links
        self.assertNotIn(b"/chat/", res.data)
        self.assertNotIn(b"Patient AI Chat URL", res.data)
        # Must contain clean structured details and Client Login Portal link
        self.assertIn(b"Open Client Login Portal", res.data)
        self.assertIn(b"Strict Authentication Enforced", res.data)
        self.assertIn(b"sunrise_admin", res.data)
        self.assertIn(b"Software License", res.data)
        self.assertIn(b"Clinic Profile", res.data)

        with self.app.app_context():
            new_biz = Business.query.filter_by(name="Sunrise Dental").first()
            self.assertIsNotNone(new_biz)
            self.assertEqual(new_biz.subscription_status, "trial")
            self.assertTrue(new_biz.is_subscription_valid)
            self.assertGreaterEqual(new_biz.days_remaining, 29)

            new_user = User.query.filter_by(username="sunrise_admin").first()
            self.assertIsNotNone(new_user)
            self.assertTrue(new_user.check_password("secure_password_99"))

    # ------------------------------------------------------------------
    # 3. Password Reset Workflow
    # ------------------------------------------------------------------

    def test_forgot_and_reset_password_workflow(self):
        """Clinic admin requests reset token and sets new password."""
        # Step 1: Request reset token
        res = self.client.post("/admin/forgot-password", data={"username": "apex_admin"}, follow_redirects=True)
        self.assertIn(b"If an account exists for that username, password reset instructions have been dispatched.", res.data)
        self.assertNotIn(b"Reset Token Generated", res.data)

        with self.app.app_context():
            user = User.query.filter_by(username="apex_admin").first()
            self.assertIsNotNone(user.reset_token)
            token = user.reset_token
            self.assertTrue(user.verify_reset_token(token))

        # Step 2: Use token to reset password
        reset_res = self.client.post(
            f"/admin/reset-password/{token}",
            data={"password": "brand_new_password_2026", "confirm_password": "brand_new_password_2026"},
            follow_redirects=True
        )
        self.assertEqual(reset_res.status_code, 200)
        self.assertIn(b"Your password has been successfully reset", reset_res.data)

        # Step 3: Verify new password works
        with self.app.app_context():
            user = User.query.filter_by(username="apex_admin").first()
            self.assertIsNone(user.reset_token)
            self.assertTrue(user.check_password("brand_new_password_2026"))
            self.assertFalse(user.check_password("admin_pass_123"))

        # Step 4: Verify query parameter format works as well (?token=...)
        with self.app.app_context():
            user = User.query.filter_by(username="apex_admin").first()
            t2 = user.generate_reset_token()
            db.session.commit()

        # GET with ?token=
        get_res = self.client.get(f"/admin/reset-password?token={t2}")
        self.assertEqual(get_res.status_code, 200)
        self.assertIn(b"Set New Password", get_res.data)

        # POST with ?token=
        post_res = self.client.post(
            f"/admin/reset-password?token={t2}",
            data={"password": "final_password_9999", "confirm_password": "final_password_9999"},
            follow_redirects=True
        )
        self.assertEqual(post_res.status_code, 200)
        self.assertIn(b"Your password has been successfully reset", post_res.data)

    # ------------------------------------------------------------------
    # 4. Sunday Availability & Time-Slot Fix
    # ------------------------------------------------------------------

    def test_sunday_open_slots_generation(self):
        """Doctor with Sunday enabled generates open available slots on Sunday."""
        with self.app.app_context():
            # Find next upcoming Sunday
            today = datetime.now().date()
            days_ahead = 6 - today.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_sunday = today + timedelta(days=days_ahead)
            sunday_str = next_sunday.strftime("%Y-%m-%d")

            avail = BookingService.check_availability(
                business_id=self.biz_id,
                doctor_id=self.doctor_id,
                date_str=sunday_str
            )
            self.assertTrue(avail.get("success"))
            self.assertFalse(avail.get("is_closed"))
            self.assertGreater(len(avail.get("available_slots", [])), 0)
            self.assertIn("09:00", avail["available_slots"])
            self.assertIn("16:30", avail["available_slots"])

    def test_response_generator_does_not_claim_sunday_closed_when_open(self):
        """AI response generator acknowledges available slots on Sunday when open."""
        from ai.response_generator import generate_tool_response
        tool_data = {
            "success": True,
            "doctor": "Dr. Zafar Iqbal",
            "doctor_id": self.doctor_id,
            "date": "2026-09-13",
            "day": "Sunday",
            "available_slots": ["09:00", "09:30", "10:00", "10:30"],
            "results": [{
                "doctor_id": self.doctor_id,
                "doctor_name": "Dr. Zafar Iqbal",
                "available_slots": ["09:00", "09:30", "10:00", "10:30"],
                "total_slots": 4
            }]
        }
        conv_state = {"workflow_state": "SELECT_SLOT"}
        resp = generate_tool_response("check_availability", tool_data, conv_state, "roman_urdu", "")
        self.assertNotIn("Sunday ko clinic off hota hai", resp)
        self.assertNotIn("clinic is closed on Sundays", resp)
        self.assertIn("09:00", resp)


if __name__ == "__main__":
    unittest.main()
