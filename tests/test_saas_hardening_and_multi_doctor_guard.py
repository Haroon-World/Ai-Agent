import unittest
from datetime import datetime, timedelta, timezone
from app import create_app
from models import db, Business, Doctor, DoctorSchedule, User, SubscriptionRequest, Conversation, Message
from services.subscription_service import SubscriptionService
from services.booking_service import BookingService
from ai.agent import Agent


class TestSaaSHardeningAndMultiDoctorGuard(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

        with self.app.app_context():
            db.create_all()

            # Multi-doctor clinic
            self.biz = Business(
                name="Gulberg Health Center",
                business_type="polyclinic",
                address="77 Main Boulevard, Gulberg, Lahore",
                phone="+92 42 35711111",
                timezone="Asia/Karachi",
                opening_hours="Monday to Saturday: 09:00 AM - 05:00 PM",
                subscription_status="active",
                subscription_expires_at=datetime.now(timezone.utc) + timedelta(days=60),
            )
            db.session.add(self.biz)
            db.session.flush()

            # Add two practicing doctors
            self.doc1 = Doctor(
                business_id=self.biz.id,
                name="Dr. Haroon Rasheed",
                specialization="Dental Surgeon",
                slot_interval=30,
                start_time="09:00",
                end_time="17:00",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday"
            )
            self.doc2 = Doctor(
                business_id=self.biz.id,
                name="Dr. Ayesha Zahid",
                specialization="Dermatologist",
                slot_interval=30,
                start_time="10:00",
                end_time="16:00",
                working_days="Tuesday,Wednesday,Thursday,Friday,Saturday"
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

            # Clinic admin user
            self.clinic_user = User(
                business_id=self.biz.id,
                username="gulberg_admin",
                is_platform_admin=False
            )
            self.clinic_user.set_password("clinic_pass123")
            db.session.add(self.clinic_user)

            # Platform super admin user
            self.platform_user = User(
                business_id=None,
                username="saas_master",
                is_platform_admin=True
            )
            self.platform_user.set_password("master_secret123")
            db.session.add(self.platform_user)

            db.session.commit()

            self.biz_id = self.biz.id
            self.doc1_id = self.doc1.id
            self.doc2_id = self.doc2.id
            self.clinic_user_id = self.clinic_user.id
            self.platform_user_id = self.platform_user.id

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    # -------------------------------------------------------------------------
    # Problem 1: Doctor Availability & Schedule Guard in Multi-Doctor Clinic
    # -------------------------------------------------------------------------

    def test_check_doctor_availability_prompts_doctor_choice_not_first_doctor(self):
        """Asking 'check doctor availability' in a multi-doctor clinic must ask which doctor first."""
        with self.app.app_context():
            agent = Agent(business_id=self.biz_id)
            conv = Conversation(
                business_id=self.biz_id,
                channel="web_chat",
                status="AI",
                workflow_state="START"
            )
            db.session.add(conv)
            db.session.commit()

            res = agent.process_message(conv.id, "check doctor availability")
            content = res.get("content", "")

            # Must NOT dump open slots or times of Dr. Haroon
            self.assertNotIn("09:00 AM", content)
            self.assertNotIn("Morning:", content)

            # Must present both doctors and prompt for selection
            self.assertIn("Dr. Haroon Rasheed", content)
            self.assertIn("Dr. Ayesha Zahid", content)
            self.assertTrue("Which doctor" in content or "doctor" in content.lower())

            # Verify conv state did not blindly bind to doc1
            updated_conv = db.session.get(Conversation, conv.id)
            self.assertIsNone(updated_conv.selected_doctor_id)
            self.assertEqual(updated_conv.awaiting_input, "doctor_choice")

    def test_doctor_schedule_inquiry_prompts_which_doctor_schedule(self):
        """Asking 'doctor schedule' in multi-doctor clinic must prompt for doctor selection."""
        with self.app.app_context():
            agent = Agent(business_id=self.biz_id)
            conv = Conversation(
                business_id=self.biz_id,
                channel="web_chat",
                status="AI",
                workflow_state="START"
            )
            db.session.add(conv)
            db.session.commit()

            res = agent.process_message(conv.id, "when is doctor available")
            content = res.get("content", "")

            # Must present doctors and ask which one
            self.assertIn("Dr. Haroon Rasheed", content)
            self.assertIn("Dr. Ayesha Zahid", content)
            self.assertNotIn("09:00 AM", content)

    def test_multi_doctor_availability_without_naming_doctor_ui_action_is_doctor_selection(self):
        """When AI asks customer to choose between multiple doctors for availability, ui_action must be doctor_selection, not date_selection."""
        from unittest.mock import patch
        with self.app.app_context():
            agent = Agent(business_id=self.biz_id)
            conv = Conversation(
                business_id=self.biz_id,
                channel="web_chat",
                status="AI",
                workflow_state="START"
            )
            db.session.add(conv)
            db.session.commit()

            # Simulate LLM tool call for checking availability with date but without doctor
            mock_response = {
                "content": None,
                "tool_calls": [{
                    "name": "check_availability",
                    "arguments": {"date": "2026-09-09"},
                    "id": "call_avail_1"
                }]
            }
            with patch.object(agent.llm_client, "get_completion", return_value=mock_response):
                res = agent.process_message(conv.id, "what slots are available tomorrow")

            # Reply text must ask which doctor
            content = res.get("content", "")
            self.assertIn("Dr. Haroon Rasheed", content)
            self.assertIn("Dr. Ayesha Zahid", content)
            self.assertTrue("Which doctor" in content or "doctor" in content.lower())

            # ui_action must be doctor_selection (NOT date_selection)
            ui_act = res.get("ui_action")
            self.assertIsNotNone(ui_act, "ui_action must not be None")
            self.assertEqual(ui_act.get("type"), "doctor_selection", f"Expected doctor_selection but got {ui_act.get('type')}")
            self.assertEqual(ui_act.get("interactive_type"), "list")

            doc_names = [opt.get("name") for opt in ui_act.get("options", [])]
            self.assertIn("Dr. Haroon Rasheed", doc_names)
            self.assertIn("Dr. Ayesha Zahid", doc_names)

    def test_multi_doctor_availability_two_turn_progression(self):
        """Turn 1 prompts doctor_selection; Turn 2 after naming doctor seamlessly returns time_slot_selection for tomorrow."""
        from unittest.mock import patch
        with self.app.app_context():
            agent = Agent(business_id=self.biz_id)
            conv = Conversation(
                business_id=self.biz_id,
                channel="web_chat",
                status="AI",
                workflow_state="START"
            )
            db.session.add(conv)
            db.session.commit()

            # Turn 1: customer asks for slots tomorrow without specifying doctor
            mock_t1 = {
                "content": None,
                "tool_calls": [{
                    "name": "check_availability",
                    "arguments": {"date": "2026-09-09"},
                    "id": "call_t1"
                }]
            }
            with patch.object(agent.llm_client, "get_completion", return_value=mock_t1):
                res1 = agent.process_message(conv.id, "what slots are available tomorrow")
            self.assertEqual(res1.get("ui_action", {}).get("type"), "doctor_selection")

            # Turn 2: customer chooses Dr. Haroon Rasheed
            mock_t2 = {
                "content": None,
                "tool_calls": [{
                    "name": "check_availability",
                    "arguments": {"date": "2026-09-09", "doctor_id": self.doc1_id},
                    "id": "call_t2"
                }]
            }
            with patch.object(agent.llm_client, "get_completion", return_value=mock_t2):
                res2 = agent.process_message(conv.id, "Dr. Haroon Rasheed")

            # Turn 2 must now return time slots for Dr. Haroon on tomorrow's date
            self.assertEqual(res2.get("ui_action", {}).get("type"), "time_slot_selection")
            self.assertIn("09:00 AM", res2.get("content", ""))

    def test_single_doctor_clinic_availability_does_not_prompt_doctor_selection(self):
        """In a clinic with a single doctor, asking for slots tomorrow must auto-bind and NOT show doctor_selection."""
        from unittest.mock import patch
        with self.app.app_context():
            single_biz = Business(
                name="Solo Smile Studio",
                business_type="dental_clinic",
                address="100 Lahore",
                phone="+923000000000"
            )
            db.session.add(single_biz)
            db.session.flush()

            solo_doc = Doctor(
                business_id=single_biz.id,
                name="Dr. Solo Dentist",
                specialization="General Dentist",
                slot_interval=30,
                start_time="09:00",
                end_time="17:00",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday"
            )
            db.session.add(solo_doc)
            db.session.flush()

            for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
                db.session.add(DoctorSchedule(
                    doctor_id=solo_doc.id, day_of_week=day, is_available=True,
                    start_time="09:00", end_time="17:00"
                ))
            db.session.commit()

            agent = Agent(business_id=single_biz.id)
            conv = Conversation(business_id=single_biz.id, channel="web_chat", status="AI", workflow_state="START")
            db.session.add(conv)
            db.session.commit()

            mock_t = {
                "content": None,
                "tool_calls": [{
                    "name": "check_availability",
                    "arguments": {"date": "2026-09-09", "doctor_id": solo_doc.id},
                    "id": "call_solo"
                }]
            }
            with patch.object(agent.llm_client, "get_completion", return_value=mock_t):
                res = agent.process_message(conv.id, "what slots are available tomorrow")

            # Must NOT prompt for doctor selection
            self.assertNotEqual(res.get("ui_action", {}).get("type"), "doctor_selection")
            self.assertEqual(res.get("ui_action", {}).get("type"), "time_slot_selection")

    # -------------------------------------------------------------------------
    # Problem 2: Subscription Rejection & Cancellation In-App Notification
    # -------------------------------------------------------------------------

    def test_rejected_subscription_request_shows_alert_on_dashboard(self):
        """When platform rejects a subscription request, clinic admin dashboard displays rejection banner with notes."""
        with self.app.app_context():
            # Submit a renewal request
            req = SubscriptionRequest(
                business_id=self.biz_id,
                plan_name="3_months",
                plan_display_name="Quarterly Plan (90 Days)",
                duration_days=90,
                status="pending"
            )
            db.session.add(req)
            db.session.commit()
            req_id = req.id

            # Platform rejects the request
            res = SubscriptionService.reject_subscription_request(
                req_id, reviewer_username="saas_master", reason="Proof of payment verification failed"
            )
            self.assertTrue(res.get("success"))

        # Log in as clinic admin and check dashboard
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.clinic_user_id
            sess["business_id"] = self.biz_id
            sess["admin_user"] = "gulberg_admin"
            sess["is_platform_admin"] = False

        resp = self.client.get("/admin")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Subscription Request Rejected by Platform Administration", html)
        self.assertIn("Proof of payment verification failed", html)
        self.assertIn("Quarterly Plan (90 Days)", html)

        # Test dismissal endpoint
        dismiss_resp = self.client.post("/admin/subscription/dismiss-notice", data={"request_id": str(req_id)})
        self.assertEqual(dismiss_resp.status_code, 200)

        # After dismissal, alert is no longer shown on dashboard
        resp2 = self.client.get("/admin")
        self.assertNotIn("subscriptionRejectionAlert", resp2.get_data(as_text=True))

    def test_subscription_message_wiped_out_when_new_plan_approved(self):
        """Subscription rejection/warning message must be wiped out once client's new plan is approved by onboarding team."""
        with self.app.app_context():
            # 1. Create and reject an initial request
            req1 = SubscriptionRequest(
                business_id=self.biz_id,
                plan_name="plan_90d",
                plan_display_name="Quarterly Plan (90 Days)",
                duration_days=90,
                requested_by_user="gulberg_admin",
                status="pending"
            )
            db.session.add(req1)
            db.session.commit()
            SubscriptionService.reject_subscription_request(req1.id, reviewer_username="onboarding_lead", reason="Invalid receipt")

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.clinic_user_id
            sess["business_id"] = self.biz_id
            sess["admin_user"] = "gulberg_admin"
            sess["is_platform_admin"] = False

        # Rejection alert shows on dashboard and subscription page
        resp1 = self.client.get("/admin")
        self.assertIn("subscriptionRejectionAlert", resp1.get_data(as_text=True))
        sub_resp1 = self.client.get("/admin/subscription")
        self.assertIn("Previous Subscription Request Rejected", sub_resp1.get_data(as_text=True))

        # 2. Client submits a new request
        with self.app.app_context():
            res_new = SubscriptionService.create_subscription_request(
                business_id=self.biz_id,
                duration_days=180,
                plan_name="plan_180d",
                requested_by="gulberg_admin"
            )
            self.assertTrue(res_new["success"])
            req2_id = res_new["request"]["id"]

            # 3. Onboarding team approves the new plan
            app_res = SubscriptionService.approve_subscription_request(req2_id, reviewer_username="onboarding_team")
            self.assertTrue(app_res["success"])

        # 4. Now verify the subscription message is completely wiped out on dashboard and subscription pages
        resp2 = self.client.get("/admin")
        self.assertEqual(resp2.status_code, 200)
        dash_html2 = resp2.get_data(as_text=True)
        self.assertNotIn("subscriptionRejectionAlert", dash_html2)
        self.assertNotIn("Subscription Request Rejected by Platform Administration", dash_html2)

        sub_resp2 = self.client.get("/admin/subscription")
        self.assertEqual(sub_resp2.status_code, 200)
        sub_html2 = sub_resp2.get_data(as_text=True)
        self.assertNotIn("Previous Subscription Request Rejected", sub_html2)
        self.assertNotIn("Invalid receipt", sub_html2)

        # 5. sub_info has latest_rejected_request as None
        with self.app.app_context():
            sub_info = SubscriptionService.get_subscription_info(self.biz_id)
            self.assertIsNone(sub_info.get("latest_rejected_request"))

    def test_cancelled_subscription_shows_explicit_cancellation_notice(self):
        """When subscription is cancelled, /admin/subscription-expired indicates cancellation."""
        with self.app.app_context():
            res = SubscriptionService.cancel_subscription(self.biz_id, reason="Policy violation / test suspension")
            self.assertTrue(res.get("success"))

        with self.client.session_transaction() as sess:
            sess["user_id"] = self.clinic_user_id
            sess["business_id"] = self.biz_id
            sess["admin_user"] = "gulberg_admin"
            sess["is_platform_admin"] = False

        resp = self.client.get("/admin/subscription-expired")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertIn("Subscription Cancelled / Suspended", html)
        self.assertIn("Policy violation / test suspension", html)

    # -------------------------------------------------------------------------
    # Problem 3: Show/Hide Password Interactive Toggle
    # -------------------------------------------------------------------------

    def test_password_toggle_present_on_login_and_platform_pages(self):
        """Password fields must have password-toggle-btn on login and platform templates."""
        # 1. Clinic Login
        resp1 = self.client.get("/admin/login")
        self.assertEqual(resp1.status_code, 200)
        html1 = resp1.get_data(as_text=True)
        self.assertIn("password-toggle-btn", html1)
        self.assertIn("togglePasswordVisibility", html1)

        # 2. Platform Master Login
        resp2 = self.client.get("/platform/login")
        self.assertEqual(resp2.status_code, 200)
        html2 = resp2.get_data(as_text=True)
        self.assertIn("password-toggle-btn", html2)
        self.assertIn("togglePlatformPassword", html2)

        # 3. Platform Onboard Clinic
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.platform_user_id
            sess["platform_admin_id"] = self.platform_user_id
            sess["is_platform_admin"] = True

        resp3 = self.client.get("/platform/onboard-clinic")
        self.assertEqual(resp3.status_code, 200)
        html3 = resp3.get_data(as_text=True)
        self.assertIn("password-toggle-btn", html3)
        self.assertIn("admin_password", html3)

    # -------------------------------------------------------------------------
    # Problem 4: Multi-Tab Platform vs Clinic Session Isolation (No 403)
    # -------------------------------------------------------------------------

    def test_concurrent_clinic_admin_browsing_does_not_403_onboarding_page(self):
        """When platform admin has onboarding page open and logs into/browses a clinic admin page, onboarding does not 403."""
        # 1. Platform login
        resp = self.client.post("/platform/login", data={"username": "saas_master", "password": "master_secret123"}, follow_redirects=True)
        self.assertEqual(resp.status_code, 200)

        # 2. In another tab, user logs into clinic admin portal
        resp_clinic = self.client.post("/admin/login", data={"username": "gulberg_admin", "password": "clinic_pass123"}, follow_redirects=True)
        self.assertEqual(resp_clinic.status_code, 200)

        # 3. Now user returns to Tab 1 and opens /platform/onboard-clinic
        resp_onboard = self.client.get("/platform/onboard-clinic")
        # Must NEVER return 403 Forbidden!
        self.assertEqual(resp_onboard.status_code, 200)
        self.assertIn("Onboard New Clinic", resp_onboard.get_data(as_text=True))

        # 4. User submits an onboarding POST request
        post_data = {
            "clinic_name": "Al-Shifa Dental Complex",
            "address": "Plaza 14, DHA Phase 5, Lahore",
            "phone": "+92 42 35890011",
            "business_type": "dental_clinic",
            "admin_username": "alshifa_admin",
            "admin_password": "securepassword123",
            "plan_type": "trial_30"
        }
        resp_post = self.client.post("/platform/onboard-clinic", data=post_data, follow_redirects=True)
        self.assertEqual(resp_post.status_code, 200)
        self.assertIn("Al-Shifa Dental Complex", resp_post.get_data(as_text=True))

        # 5. Verify /admin/platform/onboard-clinic also works seamlessly
        resp_legacy = self.client.get("/admin/platform/onboard-clinic")
        self.assertEqual(resp_legacy.status_code, 200)
