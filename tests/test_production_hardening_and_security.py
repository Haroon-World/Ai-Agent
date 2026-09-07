import unittest
import json
from app import create_app
from config.config import Config
from models import db, Business, User, Doctor, Service, DoctorLeave, Customer, Appointment, ClinicWhatsAppAccount
from services.booking_service import BookingService
from seed import seed_database


class HardeningTestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-hardening-secret"
    WTF_CSRF_ENABLED = False
    LLM_PROVIDER = "mock"
    STT_PROVIDER = "mock"
    TTS_PROVIDER = "mock"


class TestProductionHardeningAndSecurity(unittest.TestCase):
    def setUp(self):
        self.app = create_app(HardeningTestConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # 1. Password reset token non-exposure and user enumeration prevention
    def test_password_reset_does_not_leak_token_and_prevents_user_enumeration(self):
        """
        Test that /admin/forgot-password:
        - NEVER exposes the reset URL or token directly in the response.
        - Returns the exact same generic message for both existent and non-existent accounts.
        """
        # Existent user
        res_valid = self.client.post("/admin/forgot-password", data={"username": "admin"}, follow_redirects=True)
        self.assertEqual(res_valid.status_code, 200)
        body_valid = res_valid.get_data(as_text=True)
        self.assertNotIn("token=", body_valid)
        self.assertNotIn("reset-password?token=", body_valid)
        self.assertIn("If an account exists for that username, password reset instructions have been dispatched.", body_valid)

        # Non-existent user
        res_invalid = self.client.post("/admin/forgot-password", data={"username": "non_existent_user_999"}, follow_redirects=True)
        self.assertEqual(res_invalid.status_code, 200)
        body_invalid = res_invalid.get_data(as_text=True)
        self.assertNotIn("token=", body_invalid)
        self.assertNotIn("reset-password?token=", body_invalid)
        self.assertIn("If an account exists for that username, password reset instructions have been dispatched.", body_invalid)

    # 2. Platform owner password stability across seed_database()
    def test_seed_database_preserves_platform_owner_password(self):
        """
        Test that re-running seed_database() does NOT overwrite an existing platform admin's password.
        """
        owner = User.query.filter_by(is_platform_admin=True).first()
        self.assertIsNotNone(owner)
        custom_password = "CustomSuperSecretPassword!999"
        owner.set_password(custom_password)
        db.session.commit()
        saved_hash = owner.password_hash

        # Re-run seed_database
        seed_database(self.app)

        owner_after = User.query.filter_by(is_platform_admin=True).first()
        self.assertEqual(owner_after.password_hash, saved_hash)
        self.assertTrue(owner_after.check_password(custom_password))

    # 3. Clinic admin login disambiguation and isolation
    def test_clinic_admin_login_isolation_and_disambiguation(self):
        """
        Test that when multiple clinics share the username 'admin':
        - Login without clinic disambiguation halts with an explicit error.
        - Login with clinic_id or clinic name correctly isolates to that specific tenant.
        """
        # Create second business and admin
        biz2 = Business(name="Second Downtown Dental", address="456 Mall Rd, Lahore", phone="03009998877")
        db.session.add(biz2)
        db.session.commit()

        user2 = User(business_id=biz2.id, username="admin", is_platform_admin=False)
        user2.set_password("ClinicTwoPass123!")
        db.session.add(user2)
        db.session.commit()

        # Attempt login without specifying clinic
        res_ambiguous = self.client.post("/admin/login", data={"username": "admin", "password": "ClinicTwoPass123!"}, follow_redirects=True)
        body_ambiguous = res_ambiguous.get_data(as_text=True)
        self.assertIn("Multiple clinics found", body_ambiguous)

        # Login with clinic_id specifying clinic 2
        res_clinic2 = self.client.post("/admin/login", data={"username": "admin", "password": "ClinicTwoPass123!", "clinic": str(biz2.id)}, follow_redirects=True)
        self.assertEqual(res_clinic2.status_code, 200)
        with self.client.session_transaction() as sess:
            self.assertEqual(sess.get("business_id"), biz2.id)

    # 4. Cross-tenant doctor leave deletion IDOR prevented
    def test_cross_tenant_doctor_leave_deletion_idor_prevented(self):
        """
        Test that clinic admin cannot delete doctor leave belonging to another clinic.
        """
        # Clinic 1 doctor leave
        doc1 = Doctor.query.filter_by(business_id=1).first()
        leave1 = DoctorLeave(doctor_id=doc1.id, leave_date="2026-10-15", reason="Conference")
        db.session.add(leave1)

        # Clinic 2
        biz2 = Business(name="Ortho Specialists", address="789 Canal Rd, Lahore", phone="03004445566")
        db.session.add(biz2)
        db.session.commit()
        user2 = User(business_id=biz2.id, username="ortho_admin", is_platform_admin=False)
        user2.set_password("OrthoPass123!")
        db.session.add(user2)
        db.session.commit()

        # Log in as Clinic 2 admin
        self.client.post("/admin/login", data={"username": "ortho_admin", "password": "OrthoPass123!"})

        # Attempt to delete Clinic 1's leave
        res = self.client.post(f"/admin/doctors/leave/delete/{leave1.id}")
        self.assertEqual(res.status_code, 403)

        # Verify leave record was NOT deleted
        db.session.expire_all()
        persisted = db.session.get(DoctorLeave, leave1.id)
        self.assertIsNotNone(persisted)

    # 5. Cross-tenant service creation IDOR prevented
    def test_cross_tenant_service_creation_idor_prevented(self):
        """
        Test that clinic admin cannot add a service assigned to another clinic's doctor.
        """
        doc1 = Doctor.query.filter_by(business_id=1).first()

        biz2 = Business(name="City Smile Care", address="101 Jail Rd, Lahore", phone="03007778899")
        db.session.add(biz2)
        db.session.commit()
        user2 = User(business_id=biz2.id, username="city_admin", is_platform_admin=False)
        user2.set_password("CityPass123!")
        db.session.add(user2)
        db.session.commit()

        # Log in as Clinic 2 admin
        self.client.post("/admin/login", data={"username": "city_admin", "password": "CityPass123!"})

        # Attempt to create a service assigned to Clinic 1 doctor via form
        res = self.client.post("/admin/services/add", data={
            "name": "Unauthorized Root Canal",
            "doctor_id": doc1.id,
            "duration": 45,
            "price": 5000
        }, follow_redirects=True)
        self.assertIn("Selected doctor does not belong to your clinic", res.get_data(as_text=True))

        # Attempt to create a service assigned to Clinic 1 doctor via AJAX
        res_ajax = self.client.post("/admin/services/add", data={
            "name": "Unauthorized Root Canal AJAX",
            "doctor_id": doc1.id,
            "duration": 45,
            "price": 5000
        }, headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(res_ajax.status_code, 403)

        # Verify service was NOT created
        svc = Service.query.filter_by(name="Unauthorized Root Canal").first()
        self.assertIsNone(svc)
        svc_ajax = Service.query.filter_by(name="Unauthorized Root Canal AJAX").first()
        self.assertIsNone(svc_ajax)

    # 6. Cross-tenant customer phone lookup isolation
    def test_cross_tenant_customer_phone_lookup_isolation(self):
        """
        Test that BookingService.get_appointment_details strictly isolates customer lookup by business_id.
        """
        # Customer in Clinic 1
        cust1 = Customer(business_id=1, name="Ali Khan", phone="03009988776")
        db.session.add(cust1)
        db.session.commit()

        appt1 = Appointment(
            business_id=1,
            customer_id=cust1.id,
            doctor_id=1,
            service_id=1,
            appointment_date="2026-10-20",
            appointment_time="11:00",
            status="CONFIRMED"
        )
        db.session.add(appt1)
        db.session.commit()

        # Clinic 2 queries with same phone -> must NOT see Clinic 1's appointment
        res2 = BookingService.get_appointment_details(business_id=2, customer_phone="03009988776")
        self.assertEqual(res2["total_found"], 0)
        self.assertIsNone(res2["active_appointment"])
        self.assertEqual(res2["appointments"], [])

        # Clinic 1 queries with same phone -> finds Clinic 1's appointment
        res1 = BookingService.get_appointment_details(business_id=1, customer_phone="03009988776")
        self.assertEqual(res1["total_found"], 1)
        self.assertIsNotNone(res1["active_appointment"])

    # 7. Absence of runtime DDL hooks
    def test_no_runtime_ddl_on_request(self):
        """
        Verify that no before_request hook performs database DDL (ensure_db_ready removed).
        """
        before_hooks = self.app.before_request_funcs.get(None, [])
        hook_names = [f.__name__ for f in before_hooks]
        self.assertNotIn("ensure_db_ready", hook_names)

    # 8. WhatsApp account model and sensitive token masking
    def test_whatsapp_account_model_and_sensitive_token_masking(self):
        """
        Test ClinicWhatsAppAccount creation and that to_dict() masks sensitive credentials by default.
        """
        account = ClinicWhatsAppAccount(
            business_id=1,
            phone_number_id="10987654321",
            display_phone_number="+923001234567",
            waba_id="98765432109",
            access_token="EAAXSuperSecretToken12345",
            webhook_verify_token="VerifyTokenSecret999",
            is_active=True
        )
        db.session.add(account)
        db.session.commit()

        # Public to_dict
        safe_dict = account.to_dict(include_sensitive=False)
        self.assertEqual(safe_dict["phone_number_id"], "10987654321")
        self.assertNotIn("access_token", safe_dict)
        self.assertNotIn("webhook_verify_token", safe_dict)

        # Internal to_dict
        sensitive_dict = account.to_dict(include_sensitive=True)
        self.assertEqual(sensitive_dict["access_token"], "EAAXSuperSecretToken12345")
        self.assertEqual(sensitive_dict["webhook_verify_token"], "VerifyTokenSecret999")


if __name__ == "__main__":
    unittest.main()
