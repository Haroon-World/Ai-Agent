import unittest
from datetime import datetime, timedelta, timezone
from app import create_app
from models import db, Business, Doctor, DoctorSchedule, User, SubscriptionRequest, Conversation, Message
from services.subscription_service import SubscriptionService
from services.booking_service import BookingService
from ai.agent import Agent


class TestEnterpriseSubscriptionAndDoctorGuard(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

            # Multi-doctor clinic
            self.multi_biz = Business(
                name="Lahore Poly Clinic",
                business_type="polyclinic",
                address="10-C Main Gulberg, Lahore",
                phone="+92 42 35789000",
                timezone="Asia/Karachi",
                opening_hours="Monday to Saturday: 09:00 AM - 05:00 PM",
                subscription_status="trial",
                trial_ends_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
            db.session.add(self.multi_biz)
            db.session.flush()

            # Add two doctors
            self.doc1 = Doctor(
                business_id=self.multi_biz.id,
                name="Dr. Ahmed Khan",
                specialization="Cardiologist",
                slot_interval=30,
                start_time="09:00",
                end_time="17:00",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
            )
            self.doc2 = Doctor(
                business_id=self.multi_biz.id,
                name="Dr. Sara Malik",
                specialization="Dental Specialist",
                slot_interval=30,
                start_time="10:00",
                end_time="16:00",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday"
            )
            db.session.add_all([self.doc1, self.doc2])
            db.session.flush()

            for doc in [self.doc1, self.doc2]:
                for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]:
                    sched = DoctorSchedule(
                        doctor_id=doc.id,
                        day_of_week=day,
                        is_available=True,
                        start_time=doc.start_time,
                        end_time=doc.end_time
                    )
                    db.session.add(sched)

            # Single-doctor clinic
            self.single_biz = Business(
                name="Dr. Tariq Dental Care",
                business_type="dental_clinic",
                address="Suite 4, Plaza 9, Lahore",
                phone="+92 42 35112233",
                timezone="Asia/Karachi",
                opening_hours="Monday to Saturday: 09:00 AM - 05:00 PM",
                subscription_status="trial",
                trial_ends_at=datetime.now(timezone.utc) + timedelta(days=30),
            )
            db.session.add(self.single_biz)
            db.session.flush()

            self.sole_doc = Doctor(
                business_id=self.single_biz.id,
                name="Dr. Tariq Mahmood",
                specialization="Orthodontist",
                slot_interval=30,
                start_time="09:00",
                end_time="17:00",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
            )
            db.session.add(self.sole_doc)
            db.session.flush()

            for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]:
                sched = DoctorSchedule(
                    doctor_id=self.sole_doc.id,
                    day_of_week=day,
                    is_available=True,
                    start_time="09:00",
                    end_time="17:00"
                )
                db.session.add(sched)

            # Clinic Admin User
            self.clinic_admin = User(
                business_id=self.multi_biz.id,
                username="lahore_admin",
                is_platform_admin=False
            )
            self.clinic_admin.set_password("pass123")
            db.session.add(self.clinic_admin)

            # Platform Super Admin
            self.platform_admin = User(
                business_id=None,
                username="platform_boss",
                is_platform_admin=True
            )
            self.platform_admin.set_password("platform_pass")
            db.session.add(self.platform_admin)

            db.session.commit()

            self.multi_biz_id = self.multi_biz.id
            self.single_biz_id = self.single_biz.id
            self.clinic_admin_id = self.clinic_admin.id
            self.platform_admin_id = self.platform_admin.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # -------------------------------------------------------------------------
    # Problem 1: Doctor Availability Guard & Date Prompt Tests
    # -------------------------------------------------------------------------

    def test_booking_service_requires_doctor_selection_in_multi_doctor_clinic(self):
        """Direct check_availability call with doctor_id=None in multi-doctor clinic must reject."""
        with self.app.app_context():
            res = BookingService.check_availability(
                business_id=self.multi_biz_id,
                doctor_id=None,
                date_str="2026-09-08"
            )
            self.assertFalse(res.get("success"))
            self.assertTrue(res.get("requires_doctor"))
            self.assertIn("Doctor selection is required", res.get("error"))
            self.assertEqual(len(res.get("doctors", [])), 2)

    def test_multi_doctor_availability_flow_prompts_doctor_choice(self):
        """When asking for availability in multi-doctor clinic, AI must ask for doctor first and not dump first doctor's slots."""
        with self.app.app_context():
            agent = Agent(business_id=self.multi_biz_id)
            conv = Conversation(
                business_id=self.multi_biz_id,
                channel="web_chat",
                status="AI",
                workflow_state="START"
            )
            db.session.add(conv)
            db.session.commit()

            # User asks for tomorrow's slots without naming doctor
            reply = agent.process_message(conv.id, "6 appointments for tomorrow.")
            content = reply.get("content", "")

            # Must NOT dump open time slots
            self.assertNotIn("09:00 AM", content)
            self.assertNotIn("Morning:", content)
            
            # Must list doctors and ask which doctor
            self.assertIn("Dr. Ahmed Khan", content)
            self.assertIn("Dr. Sara Malik", content)
            self.assertTrue("Which doctor" in content or "doctor" in content.lower())

            # Now user chooses Dr. Ahmed Khan
            reply2 = agent.process_message(conv.id, "Dr. Ahmed Khan")
            content2 = reply2.get("content", "")
            # Now slots for Dr. Ahmed Khan should be offered
            self.assertTrue("09:00 AM" in content2 or "Ahmed" in content2)

    def test_single_doctor_availability_prompts_date_or_schedule(self):
        """In a single doctor clinic, asking for availability without date asks for date/schedule rather than dumping slots."""
        with self.app.app_context():
            agent = Agent(business_id=self.single_biz_id)
            conv = Conversation(
                business_id=self.single_biz_id,
                channel="web_chat",
                status="AI",
                workflow_state="START"
            )
            db.session.add(conv)
            db.session.commit()

            reply = agent.process_message(conv.id, "check availability")
            content = reply.get("content", "")

            # Must ask which date or day and not dump slot list blindly
            self.assertTrue("date" in content.lower() or "day" in content.lower() or "available" in content.lower() or "schedule" in content.lower())
            self.assertNotIn("Morning:", content)
            self.assertNotIn("09:30 AM", content)

    # -------------------------------------------------------------------------
    # Problem 2: Portal Authentication Boundary & No Auto-Login
    # -------------------------------------------------------------------------

    def test_manage_clinic_redirects_to_login_without_session_bypass(self):
        """Accessing /platform/manage-clinic/<id> redirects to /admin/login and never sets business_id."""
        # Log in as platform admin
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_admin_id
            sess["is_platform_admin"] = True
            sess["admin_user"] = "platform_boss"

        resp = self.client.get(f"/platform/manage-clinic/{self.multi_biz_id}", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/login", resp.headers["Location"])

        # Verify session does not contain clinic business_id
        with self.client.session_transaction() as sess:
            self.assertIsNone(sess.get("business_id"))

    def test_client_login_requires_credentials_for_platform_admin(self):
        """Platform admin visiting /admin/login does not see an active session allowing backdoor entry."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_admin_id
            sess["is_platform_admin"] = True
            sess["admin_user"] = "platform_boss"

        resp = self.client.get("/admin/login")
        self.assertEqual(resp.status_code, 200)
        # Should not show active session banner bypassing login
        self.assertNotIn("Dashboard →", resp.get_data(as_text=True))

    # -------------------------------------------------------------------------
    # Problem 3: Enterprise Subscription Workflow Tests
    # -------------------------------------------------------------------------

    def test_client_renew_creates_pending_approval_request_no_instant_free_days(self):
        """Client submitting a plan renewal creates a pending SubscriptionRequest and does not instantly add days."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.clinic_admin_id
            sess["business_id"] = self.multi_biz_id
            sess["is_platform_admin"] = False
            sess["admin_user"] = "lahore_admin"

        with self.app.app_context():
            biz_before = db.session.get(Business, self.multi_biz_id)
            exp_before = biz_before.effective_expiry_date

        resp = self.client.post("/admin/subscription/renew", data={"duration_days": "90"}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Subscription request submitted", resp.get_data(as_text=True))

        with self.app.app_context():
            biz_after = db.session.get(Business, self.multi_biz_id)
            # Days must NOT be extended immediately!
            self.assertEqual(biz_after.effective_expiry_date.strftime("%Y-%m-%d"), exp_before.strftime("%Y-%m-%d"))

            # Must have a pending SubscriptionRequest
            pending = SubscriptionRequest.query.filter_by(business_id=self.multi_biz_id, status="pending").first()
            self.assertIsNotNone(pending)
            self.assertEqual(pending.duration_days, 90)
            self.assertEqual(pending.status, "pending")

    def test_platform_admin_approves_subscription_request(self):
        """Platform admin approving request activates the plan and extends expiry date."""
        with self.app.app_context():
            req_res = SubscriptionService.create_subscription_request(
                business_id=self.multi_biz_id,
                duration_days=90,
                requested_by="lahore_admin"
            )
            req_id = req_res["request"]["id"]

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_admin_id
            sess["is_platform_admin"] = True
            sess["admin_user"] = "platform_boss"

        resp = self.client.post(f"/platform/subscription-request/{req_id}/approve", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            req = db.session.get(SubscriptionRequest, req_id)
            self.assertEqual(req.status, "approved")
            self.assertEqual(req.reviewed_by, "platform_boss")

            biz = db.session.get(Business, self.multi_biz_id)
            self.assertEqual(biz.subscription_status, "active")
            self.assertTrue(biz.days_remaining >= 89)

    def test_platform_admin_rejects_subscription_request(self):
        """Platform admin rejecting request updates status and does not extend days."""
        with self.app.app_context():
            req_res = SubscriptionService.create_subscription_request(
                business_id=self.multi_biz_id,
                duration_days=180,
                requested_by="lahore_admin"
            )
            req_id = req_res["request"]["id"]

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_admin_id
            sess["is_platform_admin"] = True
            sess["admin_user"] = "platform_boss"

        resp = self.client.post(
            f"/platform/subscription-request/{req_id}/reject",
            data={"reason": "Payment not received yet"},
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            req = db.session.get(SubscriptionRequest, req_id)
            self.assertEqual(req.status, "rejected")
            self.assertIn("Payment not received yet", req.notes)

    def test_set_subscription_calendar_date_range(self):
        """Platform admin can set custom start and end dates via calendar range."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_admin_id
            sess["is_platform_admin"] = True
            sess["admin_user"] = "platform_boss"

        start_date = "2026-09-01"
        end_date = "2026-12-31"
        resp = self.client.post(
            f"/platform/clinic/{self.multi_biz_id}/subscription/set-dates",
            data={"start_date": start_date, "end_date": end_date},
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            biz = db.session.get(Business, self.multi_biz_id)
            self.assertEqual(biz.subscription_start_date.strftime("%Y-%m-%d"), start_date)
            self.assertEqual(biz.subscription_expires_at.strftime("%Y-%m-%d"), end_date)
            self.assertEqual(biz.subscription_status, "active")

    def test_subscription_cancellation_and_portal_lockout(self):
        """Cancelling subscription immediately locks portal access and shows cancellation status."""
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_admin_id
            sess["is_platform_admin"] = True
            sess["admin_user"] = "platform_boss"

        # Platform cancels subscription
        resp = self.client.post(f"/platform/clinic/{self.multi_biz_id}/subscription/cancel", follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        with self.app.app_context():
            biz = db.session.get(Business, self.multi_biz_id)
            self.assertEqual(biz.subscription_status, "cancelled")
            self.assertFalse(biz.is_subscription_valid)

        # Now clinic admin tries to access clinic portal
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.clinic_admin_id
            sess["business_id"] = self.multi_biz_id
            sess["is_platform_admin"] = False
            sess["admin_user"] = "lahore_admin"

        portal_resp = self.client.get("/admin", follow_redirects=False)
        self.assertEqual(portal_resp.status_code, 302)
        self.assertIn("/admin/subscription-expired", portal_resp.headers["Location"])

    def test_subscription_reactivation_restores_portal_access(self):
        """Reactivating a cancelled subscription restores active status and portal access."""
        with self.app.app_context():
            SubscriptionService.cancel_subscription(self.multi_biz_id)

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_admin_id
            sess["is_platform_admin"] = True
            sess["admin_user"] = "platform_boss"

        reactivate_resp = self.client.post(
            f"/platform/clinic/{self.multi_biz_id}/subscription/reactivate",
            data={"days": "45"},
            follow_redirects=True
        )
        self.assertEqual(reactivate_resp.status_code, 200)

        with self.app.app_context():
            biz = db.session.get(Business, self.multi_biz_id)
            self.assertEqual(biz.subscription_status, "active")
            self.assertTrue(biz.is_subscription_valid)
            self.assertTrue(biz.days_remaining >= 44)


if __name__ == "__main__":
    unittest.main()
