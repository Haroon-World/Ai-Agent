"""
tests/test_multitenant_auth.py

Regression tests for the multi-tenant auth layer (Part A / B / C).
Covers all 6 verification scenarios from the implementation prompt:

  1. Arfa Polyclinic admin logs in with original credentials → same data.
  2. Platform admin creates a second test clinic via the onboarding page.
  3. Second clinic admin logs in → empty panel (no data from Arfa).
  4. Second clinic admin adds a doctor → scoped correctly, invisible to Arfa.
  5. Regular clinic admin gets 403 on /admin/platform/onboard-clinic.
  6. Chat/booking route still works for Arfa Polyclinic.
"""
import unittest
from datetime import date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Appointment, Conversation
from models.user import User
from werkzeug.security import generate_password_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _admin_session(client, user_id, business_id, username="admin",
                   is_platform_admin=False, clinic_name="Test"):
    """Set the session to simulate a logged-in admin."""
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["business_id"] = business_id
        sess["admin_user"] = username
        sess["is_platform_admin"] = is_platform_admin
        sess["clinic_name"] = clinic_name


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestMultiTenantAuth(unittest.TestCase):

    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            WTF_CSRF_ENABLED = False
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()

        db.session.rollback()
        db.session.remove()
        db.drop_all()
        db.create_all()

        # ── Business 1: Arfa Polyclinic ──────────────────────────────────
        self.arfa = db.session.get(Business, 1)
        if not self.arfa:
            self.arfa = Business(
                id=1,
                name="Arfa Polyclinic",
                business_type="polyclinic",
                address="Plot 42-B, Gulberg III, Lahore",
                phone="+92 42 35789000",
                timezone="Asia/Karachi",
                opening_hours="Mon-Sat 09:00-17:00",
            )
            db.session.add(self.arfa)
            db.session.flush()

        # Clinic admin user for Arfa (pure clinic tenant, is_platform_admin=False)
        self.arfa_admin = User.query.filter_by(business_id=1, username=Config.ADMIN_USERNAME).first()
        if not self.arfa_admin:
            self.arfa_admin = User(
                business_id=1,
                username=Config.ADMIN_USERNAME,
                password_hash=generate_password_hash(Config.ADMIN_PASSWORD),
                is_platform_admin=False,
            )
            db.session.add(self.arfa_admin)

        # Dedicated platform owner (is_platform_admin=True, business_id=None)
        self.platform_admin = User.query.filter_by(username=Config.PLATFORM_ADMIN_USERNAME, is_platform_admin=True).first()
        if not self.platform_admin:
            self.platform_admin = User(
                business_id=None,
                username=Config.PLATFORM_ADMIN_USERNAME,
                password_hash=generate_password_hash(Config.PLATFORM_ADMIN_PASSWORD),
                is_platform_admin=True,
            )
            db.session.add(self.platform_admin)

        # One doctor under Arfa
        self.arfa_doctor = db.session.get(Doctor, 1)
        if not self.arfa_doctor:
            self.arfa_doctor = Doctor(
                id=1,
                business_id=1,
                name="Dr. Ahmed Khan",
                specialization="General Dentistry",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
                start_time="09:00",
                end_time="17:00",
                slot_interval=30,
                is_active=True,
            )
            db.session.add(self.arfa_doctor)

        # One service under Arfa
        self.arfa_service = db.session.get(Service, 1)
        if not self.arfa_service:
            self.arfa_service = Service(
                id=1,
                business_id=1,
                doctor_id=1,
                name="Dental Checkup",
                duration=30,
                price=2000.0,
                is_active=True,
            )
            db.session.add(self.arfa_service)

        db.session.commit()


    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # ── 1. Existing Arfa admin can still log in ───────────────────────────

    def test_arfa_admin_login_with_original_credentials(self):
        """Scenario 1 — existing credentials still work via the new User-table login."""
        resp = self.client.post(
            "/admin/login",
            data={"username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD},
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        # Should land on dashboard (not stay on login)
        self.assertNotIn(b"Invalid username or password", resp.data)
        self.assertIn(b"Logged in", resp.data)

    def test_arfa_admin_sees_own_data_on_dashboard(self):
        """Scenario 1 — after login, dashboard shows Arfa's doctor/service data."""
        _admin_session(self.client, self.arfa_admin.id, 1,
                       username=Config.ADMIN_USERNAME,
                       is_platform_admin=False, clinic_name="Arfa Polyclinic")
        resp = self.client.get("/admin/doctors")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Dr. Ahmed Khan", resp.data)

    def test_arfa_admin_wrong_password_rejected(self):
        """Login with wrong password must fail cleanly."""
        resp = self.client.post(
            "/admin/login",
            data={"username": Config.ADMIN_USERNAME, "password": "wrong-password"},
            follow_redirects=True,
        )
        self.assertIn(b"Invalid username or password", resp.data)

    # ── 2. Platform admin can onboard a second clinic ─────────────────────

    def test_onboard_clinic_creates_business_and_user(self):
        """Scenario 2 — POST to onboard-clinic creates Business + User rows."""
        _admin_session(self.client, self.platform_admin.id, None,
                       username=Config.PLATFORM_ADMIN_USERNAME,
                       is_platform_admin=True, clinic_name="ClinicConnectAI Platform")

        resp = self.client.post(
            "/platform/onboard-clinic",
            data={
                "clinic_name": "Test Clinic 2",
                "address": "123 Test Street",
                "phone": "+92 300 1234567",
                "business_type": "dental_clinic",
                "timezone": "Asia/Karachi",
                # opening_hours omitted to verify automatic default application
                "admin_username": "clinic2admin",
                "admin_password": "securePass99",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"onboarded successfully", resp.data.lower())

        # Business row created with automatic opening hours default
        new_biz = Business.query.filter_by(name="Test Clinic 2").first()
        self.assertIsNotNone(new_biz)
        self.assertNotEqual(new_biz.id, 1)
        self.assertIn("09:00 AM", new_biz.opening_hours)

        # User row created with correct business scope
        new_user = User.query.filter_by(
            business_id=new_biz.id, username="clinic2admin"
        ).first()
        self.assertIsNotNone(new_user)
        self.assertFalse(new_user.is_platform_admin)
        self.assertTrue(new_user.check_password("securePass99"))

    # ── 3. Second clinic admin sees empty panel ───────────────────────────

    def test_second_clinic_admin_sees_empty_panel(self):
        """Scenario 3 — second clinic admin has no doctors, services, or appointments."""
        # Create a second business + user directly in DB (not via onboard form)
        clinic2 = Business(
            id=2,
            name="Test Clinic 2",
            business_type="dental_clinic",
            address="123 Test St",
            phone="+92 300 0000000",
            timezone="Asia/Karachi",
            opening_hours="Mon-Sat 09:00-17:00",
        )
        db.session.add(clinic2)
        db.session.flush()

        user2 = User(
            business_id=2,
            username="clinic2admin",
            password_hash=generate_password_hash("pass123"),
            is_platform_admin=False,
        )
        db.session.add(user2)
        db.session.commit()

        _admin_session(self.client, user2.id, 2,
                       username="clinic2admin", clinic_name="Test Clinic 2")

        # Doctors page should show no doctors
        resp_doctors = self.client.get("/admin/doctors")
        self.assertEqual(resp_doctors.status_code, 200)
        self.assertNotIn(b"Dr. Ahmed Khan", resp_doctors.data)

        # Services page should show no services
        resp_services = self.client.get("/admin/services")
        self.assertEqual(resp_services.status_code, 200)
        self.assertNotIn(b"Dental Checkup", resp_services.data)

    # ── 4. Second clinic data is scoped — invisible to Arfa ───────────────

    def test_second_clinic_doctor_not_visible_to_arfa(self):
        """Scenario 4 — doctor added under clinic 2 is invisible when logged in as Arfa."""
        clinic2 = Business(
            id=2, name="Test Clinic 2", business_type="dental_clinic",
            address="123 Test St", phone="+92 300 0000000",
            timezone="Asia/Karachi", opening_hours="Mon-Sat 09:00-17:00",
        )
        db.session.add(clinic2)
        db.session.flush()

        user2 = User(
            business_id=2, username="clinic2admin",
            password_hash=generate_password_hash("pass123"),
            is_platform_admin=False,
        )
        db.session.add(user2)

        # Doctor under clinic 2
        doc2 = Doctor(
            business_id=2, name="Dr. Clinic2 Only",
            specialization="Optometry",
            working_days="Monday,Tuesday",
            start_time="09:00", end_time="17:00",
            slot_interval=30, is_active=True,
        )
        db.session.add(doc2)
        db.session.commit()

        # Log in as Arfa → Dr. Clinic2 Only must NOT appear
        _admin_session(self.client, self.arfa_admin.id, 1,
                       username=Config.ADMIN_USERNAME,
                       is_platform_admin=True, clinic_name="Arfa Polyclinic")
        resp = self.client.get("/admin/doctors")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(b"Dr. Clinic2 Only", resp.data)
        self.assertIn(b"Dr. Ahmed Khan", resp.data)

        # Log in as clinic2 → Dr. Ahmed Khan must NOT appear
        _admin_session(self.client, user2.id, 2,
                       username="clinic2admin", clinic_name="Test Clinic 2")
        resp2 = self.client.get("/admin/doctors")
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b"Dr. Clinic2 Only", resp2.data)
        self.assertNotIn(b"Dr. Ahmed Khan", resp2.data)

    # ── 5. Regular clinic admin gets 403 on onboard page ─────────────────

    def test_regular_admin_cannot_access_onboard_page(self):
        """Scenario 5 — non-platform admin gets 403 on /admin/platform/onboard-clinic."""
        clinic2 = Business(
            id=2, name="Test Clinic 2", business_type="dental_clinic",
            address="123 Test St", phone="+92 300 0000000",
            timezone="Asia/Karachi", opening_hours="Mon-Sat 09:00-17:00",
        )
        db.session.add(clinic2)
        db.session.flush()

        user2 = User(
            business_id=2, username="clinic2admin",
            password_hash=generate_password_hash("pass123"),
            is_platform_admin=False,
        )
        db.session.add(user2)
        db.session.commit()

        # Login as regular clinic admin (NOT platform admin)
        _admin_session(self.client, user2.id, 2,
                       username="clinic2admin", is_platform_admin=False,
                       clinic_name="Test Clinic 2")

        resp_get = self.client.get("/admin/platform/onboard-clinic")
        self.assertEqual(resp_get.status_code, 403)

        resp_post = self.client.post(
            "/admin/platform/onboard-clinic",
            data={"clinic_name": "Hacker Clinic", "admin_username": "evil", "admin_password": "evilpass"}
        )
        self.assertEqual(resp_post.status_code, 403)

    def test_unauthenticated_cannot_access_onboard_page(self):
        """Unauthenticated request to onboard page redirects to platform login."""
        resp = self.client.get("/admin/platform/onboard-clinic")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/platform/login", resp.headers["Location"])


    # ── 6. Chat route still works for Arfa Polyclinic ────────────────────

    def test_arfa_chat_route_still_works(self):
        """Scenario 6 — /chat route (customer-facing) is unaffected by auth changes."""
        resp = self.client.get("/chat")
        self.assertEqual(resp.status_code, 200)

    def test_chat_api_creates_conversation_for_arfa(self):
        """Scenario 6 — /api/chat/init creates a conversation scoped to business_id 1."""
        resp = self.client.post(
            "/api/chat/init",
            json={},
            content_type="application/json",
        )
        # Should get a 200 (not an auth error)
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsNotNone(data)
        self.assertTrue(data.get("success"))

        # Verify conversation was created under business 1
        conv = Conversation.query.filter_by(business_id=1).first()
        self.assertIsNotNone(conv)

    # ── Username scoping — two clinics can share a username ───────────────

    def test_same_username_allowed_across_different_businesses(self):
        """Username unique-per-business: two different clinics can both have 'admin'."""
        # setUp already created self.arfa_admin with username=Config.ADMIN_USERNAME for biz 1.
        # Create a second business and give IT also an admin called "admin".
        # This must NOT raise a unique constraint error because the constraint
        # is composite (business_id, username).
        clinic2 = Business(
            id=2, name="Test Clinic 2", business_type="dental_clinic",
            address="123 Test St", phone="+92 300 0000000",
            timezone="Asia/Karachi", opening_hours="Mon-Sat 09:00-17:00",
        )
        db.session.add(clinic2)
        db.session.flush()

        # clinic2 gets username "admin" — same username as Arfa's admin, different business
        clinic2_admin = User(
            business_id=2, username=Config.ADMIN_USERNAME,
            password_hash=generate_password_hash("clinic2Pass"),
            is_platform_admin=False,
        )
        db.session.add(clinic2_admin)
        # This commit must NOT raise an IntegrityError
        db.session.commit()

        # Both "admin" users exist, scoped to their respective businesses
        arfa_count = User.query.filter_by(
            business_id=1, username=Config.ADMIN_USERNAME
        ).count()
        clinic2_count = User.query.filter_by(
            business_id=2, username=Config.ADMIN_USERNAME
        ).count()
        self.assertEqual(arfa_count, 1)
        self.assertEqual(clinic2_count, 1)

    # ── login_required redirect ───────────────────────────────────────────

    def test_admin_routes_require_login(self):
        """Unauthenticated requests to admin routes redirect to login."""
        for url in ["/admin", "/admin/appointments", "/admin/doctors",
                    "/admin/services", "/admin/conversations"]:
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 302,
                             msg=f"Expected 302 for {url}, got {resp.status_code}")
            self.assertIn("/admin/login", resp.headers["Location"],
                          msg=f"Expected login redirect for {url}")

    # ── Session isolation on logout ───────────────────────────────────────

    def test_logout_clears_session(self):
        """Logout must clear session so subsequent admin requests redirect to login."""
        _admin_session(self.client, self.arfa_admin.id, 1,
                       is_platform_admin=False, clinic_name="Arfa Polyclinic")
        # Confirm access works before logout
        pre_resp = self.client.get("/admin")
        self.assertEqual(pre_resp.status_code, 200)

        # Logout
        self.client.get("/admin/logout")

        # Now access must redirect to login
        post_resp = self.client.get("/admin")
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn("/admin/login", post_resp.headers["Location"])

    # ── Onboard form validation & platform isolation ─────────────────────

    def test_onboard_clinic_requires_password_min_length(self):
        """Onboard form must reject passwords shorter than 6 characters."""
        _admin_session(self.client, self.platform_admin.id, None,
                       username=Config.PLATFORM_ADMIN_USERNAME,
                       is_platform_admin=True, clinic_name="ClinicConnectAI Platform")
        resp = self.client.post(
            "/platform/onboard-clinic",
            data={
                "clinic_name": "Short Pass Clinic",
                "address": "Somewhere",
                "phone": "+92 300 0000000",
                "business_type": "dental_clinic",
                "timezone": "Asia/Karachi",
                "opening_hours": "Mon-Sat 09:00-17:00",
                "admin_username": "shortadmin",
                "admin_password": "123",   # Too short
            },
            follow_redirects=True,
        )
        self.assertEqual(resp.status_code, 200)
        # Business should NOT have been created
        self.assertIsNone(Business.query.filter_by(name="Short Pass Clinic").first())

    def test_arfa_admin_cannot_access_platform_dashboard(self):
        """Arfa Polyclinic admin (regular clinic tenant) gets 403 on /platform dashboard."""
        _admin_session(self.client, self.arfa_admin.id, 1,
                       username=Config.ADMIN_USERNAME,
                       is_platform_admin=False, clinic_name="Arfa Polyclinic")
        resp = self.client.get("/platform")
        self.assertEqual(resp.status_code, 403)

    def test_unauthenticated_platform_redirects_to_platform_login(self):
        """Unauthenticated requests to /platform redirect to secret /platform/login."""
        resp = self.client.get("/platform")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/platform/login", resp.headers["Location"])

    def test_stealth_platform_login_flow(self):
        """Dedicated /platform/login accepts platform owner and loads master console."""
        # 1. GET login page
        resp_get = self.client.get("/platform/login")
        self.assertEqual(resp_get.status_code, 200)
        self.assertIn(b"Master Access", resp_get.data)

        # 2. POST valid platform owner credentials
        resp_post = self.client.post(
            "/platform/login",
            data={"username": Config.PLATFORM_ADMIN_USERNAME, "password": Config.PLATFORM_ADMIN_PASSWORD},
            follow_redirects=True
        )
        self.assertEqual(resp_post.status_code, 200)
        self.assertIn(b"Platform Master Operations", resp_post.data)
        self.assertIn(b"Arfa Polyclinic", resp_post.data)

    def test_clinic_admin_cannot_login_at_platform_portal(self):
        """Clinic staff credentials entered at /platform/login must be rejected."""
        resp = self.client.post(
            "/platform/login",
            data={"username": Config.ADMIN_USERNAME, "password": Config.ADMIN_PASSWORD},
            follow_redirects=True
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Unauthorized: Clinic staff accounts must sign in via the Clinic Admin Portal", resp.data)

    def test_no_platform_links_in_public_nav(self):
        """Public pages (overview, chat) must have 0 links to /platform."""
        for path in ["/", "/chat"]:
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn(b'href="/platform', resp.data)
            self.assertNotIn(b'Onboard Clinic', resp.data)

    def test_chat_view_scopes_to_requested_clinic(self):
        """Customer chat UI adapts dynamically to clinic ID in URL or query parameter."""
        # Create second clinic
        clinic2 = Business(
            id=2,
            name="City Dental Care",
            business_type="dental_clinic",
            address="Street 5, Blue Area, Islamabad",
            phone="+92 51 2223344",
            opening_hours="Mon-Fri 10:00-18:00"
        )
        db.session.add(clinic2)
        db.session.commit()

        # 1. Default /chat falls back to Arfa Polyclinic (id=1)
        resp1 = self.client.get("/chat")
        self.assertEqual(resp1.status_code, 200)
        self.assertIn(b"Arfa Polyclinic", resp1.data)
        self.assertIn(b"window.CURRENT_CLINIC_ID = 1", resp1.data)

        # 2. Scoped /chat/2 renders City Dental Care
        resp2 = self.client.get("/chat/2")
        self.assertEqual(resp2.status_code, 200)
        self.assertIn(b"City Dental Care", resp2.data)
        self.assertIn(b"window.CURRENT_CLINIC_ID = 2", resp2.data)

        # 3. Query param /chat?clinic=2 also works
        resp3 = self.client.get("/chat?clinic=2")
        self.assertEqual(resp3.status_code, 200)
        self.assertIn(b"City Dental Care", resp3.data)
        self.assertIn(b"window.CURRENT_CLINIC_ID = 2", resp3.data)

    def test_chat_api_init_scopes_to_requested_clinic(self):
        """Chat init API initializes conversation and welcome message for the targeted clinic."""
        clinic2 = Business(
            id=2,
            name="Al-Shifa Eye Hospital",
            business_type="eye_clinic",
            address="Jhelum Road, Rawalpindi",
            phone="+92 51 5487821"
        )
        db.session.add(clinic2)
        db.session.commit()

        # Init chat for clinic 2
        resp = self.client.post("/api/chat/init", json={"business_id": 2})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["business_id"], 2)
        self.assertEqual(data["clinic_name"], "Al-Shifa Eye Hospital")
        # Welcome message mentions clinic 2
        self.assertIn("Al-Shifa Eye Hospital", data["messages"][0]["content"])

    def test_chat_tenant_isolation_rejects_cross_clinic_conversation(self):
        """Sending messages with mismatched clinic ID isolates conversation to target clinic."""
        clinic2 = Business(
            id=2,
            name="Beacon Skin Clinic",
            business_type="skin_clinic",
            address="DHA Phase 5, Lahore",
            phone="+92 42 37890123"
        )
        db.session.add(clinic2)
        db.session.commit()

        # 1. Start a conversation in Clinic 1
        resp1 = self.client.post("/api/chat/init", json={"business_id": 1})
        conv1_id = resp1.get_json()["conversation_id"]

        # 2. Client sends message intended for Clinic 2, passing conv1_id
        resp2 = self.client.post("/api/chat/send", json={
            "conversation_id": conv1_id,
            "business_id": 2,
            "message": "Hi, what are your opening hours?"
        })
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertTrue(data2["success"])
        self.assertEqual(data2["business_id"], 2)
        # It must NOT be conversation 1
        self.assertNotEqual(data2["conversation_id"], conv1_id)

    def test_platform_dashboard_renders_chat_links_for_tenants(self):
        """Platform master console renders direct 'Open Chat' links for each tenant."""
        _admin_session(self.client, self.platform_admin.id, None,
                       username=Config.PLATFORM_ADMIN_USERNAME,
                       is_platform_admin=True, clinic_name="Platform Owner")
        resp = self.client.get("/platform")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'href="/chat/1"', resp.data)
        self.assertIn(b'Open Chat', resp.data)


if __name__ == "__main__":
    unittest.main()


