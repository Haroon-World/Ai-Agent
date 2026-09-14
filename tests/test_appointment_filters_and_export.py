import unittest
import subprocess
import re
from datetime import datetime, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Appointment, Customer, Doctor, Service, User
from seed import seed_database


class TestAppointmentFiltersAndExport(unittest.TestCase):
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
        doc = Doctor.query.filter_by(business_id=self.business_id).first()
        svc = Service.query.filter_by(business_id=self.business_id).first()

        # Create a test customer and appointment so appointments table is populated
        cust = Customer(
            business_id=self.business_id,
            name="Ali Khan",
            phone="+923001234567"
        )
        db.session.add(cust)
        db.session.flush()

        today_str = datetime.now().strftime("%Y-%m-%d")
        appt = Appointment(
            business_id=self.business_id,
            customer_id=cust.id,
            doctor_id=doc.id if doc else 1,
            service_id=svc.id if svc else 1,
            appointment_date=today_str,
            appointment_time="10:00 AM",
            status="CONFIRMED"
        )
        db.session.add(appt)
        db.session.commit()

        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["business_id"] = self.business_id
            sess["is_platform_admin"] = True

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_appointments_page_renders_with_filters(self):
        """Verify the appointments page loads 200 and includes all filter elements."""
        res = self.client.get("/admin/appointments")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        self.assertIn('id="apptSearchInput"', html)
        self.assertIn('id="apptDoctorFilter"', html)
        self.assertIn('id="apptStatusFilter"', html)
        self.assertIn('id="apptDatePicker"', html)
        self.assertIn('id="apptDatePreset"', html)
        self.assertIn('id="apptResetBtn"', html)

        self.assertIn("filterAppointments", html)
        self.assertIn("onApptDatePicked", html)
        self.assertIn("onApptPresetChanged", html)
        self.assertIn("clearApptDatePicker", html)
        self.assertIn("resetApptFilters", html)

    def test_appointment_rows_have_filter_attributes(self):
        """Verify appointment rows render with expected data attributes for filtering."""
        res = self.client.get("/admin/appointments")
        self.assertEqual(res.status_code, 200)
        html = res.data.decode("utf-8")

        self.assertIn('class="appt-row"', html)
        self.assertIn('data-id=', html)
        self.assertIn('data-patient="ali khan"', html)
        self.assertIn('data-phone="+923001234567"', html)
        self.assertIn('data-doctor=', html)
        self.assertIn('data-status="CONFIRMED"', html)
        self.assertIn('data-date=', html)

    def test_appointments_javascript_syntax(self):
        """Verify templates/appointments.html inline JavaScript passes node syntax check."""
        with open("templates/appointments.html", "r", encoding="utf-8") as f:
            content = f.read()

        script_match = re.search(r"<script>(.*?)</script>", content, re.DOTALL)
        self.assertIsNotNone(script_match, "No <script> tag found in appointments.html")
        script_code = script_match.group(1)

        open_b = script_code.count("{")
        close_b = script_code.count("}")
        self.assertEqual(open_b, close_b, f"Mismatched braces in appointments.html: {open_b} {{ vs {close_b} }}")

        with open("temp_test_check.js", "w", encoding="utf-8") as f_temp:
            f_temp.write(script_code)
        try:
            check_res = subprocess.run(
                ["node", "--check", "temp_test_check.js"],
                capture_output=True,
                text=True
            )
            self.assertEqual(check_res.returncode, 0, f"JS Syntax Error: {check_res.stderr}")
        finally:
            import os
            if os.path.exists("temp_test_check.js"):
                os.remove("temp_test_check.js")

    def test_export_appointments_endpoint(self):
        """Verify export appointments endpoint outputs CSV with UTF-8 BOM."""
        res = self.client.get("/admin/appointments/export")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.headers.get("Content-Type", ""))
        self.assertIn("Appointments_", res.headers.get("Content-Disposition", ""))
        data = res.data.decode("utf-8-sig")
        self.assertIn("Appointment ID,Date,Time,Patient Name", data)
        self.assertIn("Ali Khan", data)


if __name__ == "__main__":
    unittest.main()
