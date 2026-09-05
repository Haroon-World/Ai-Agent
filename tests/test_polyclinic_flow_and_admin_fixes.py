import unittest
import json
from datetime import date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Appointment, Conversation, Message, Customer
from ai.agent import Agent, _build_ui_action
from services.booking_service import BookingService, RequestCache

class TestPolyclinicFlowAndAdminFixes(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()

        db.drop_all()
        db.create_all()
        RequestCache.clear()

        # Seed Business
        self.business = Business(
            id=1,
            name="Arfa Polyclinic",
            business_type="polyclinic",
            address="Plot 42-B, Main Boulevard, Gulberg III, Lahore",
            phone="+92 42 35789000",
            consultation_fee=2000.0,
            opening_hours="Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed"
        )
        db.session.add(self.business)

        # Seed Doctors
        self.dr_ahmed = Doctor(
            id=1,
            business_id=1,
            name="Dr. Ahmed Khan",
            specialization="General Dentistry & Orthodontics",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        self.dr_sara = Doctor(
            id=2,
            business_id=1,
            name="Dr. Sara Malik",
            specialization="Pediatric & Cosmetic Dentistry",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            slot_interval=30,
            is_active=True
        )
        db.session.add_all([self.dr_ahmed, self.dr_sara])

        # Seed Services
        self.svc_ahmed_checkup = Service(
            id=1,
            business_id=1,
            doctor_id=1,
            name="Dental Checkup & Consultation",
            duration=30,
            price=2000.0,
            is_active=True
        )
        self.svc_sara_cleaning = Service(
            id=2,
            business_id=1,
            doctor_id=2,
            name="Dental Cleaning & Scaling",
            duration=45,
            price=4000.0,
            is_active=True
        )
        self.svc_sara_whitening = Service(
            id=3,
            business_id=1,
            doctor_id=2,
            name="Teeth Whitening",
            duration=60,
            price=8000.0,
            is_active=True
        )
        db.session.add_all([self.svc_ahmed_checkup, self.svc_sara_cleaning, self.svc_sara_whitening])
        db.session.commit()
        RequestCache.clear()

        self.agent = Agent(business_id=1, llm_provider="mock")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_deactivated_services_filtered(self):
        """1. Deactivated services must NEVER be returned by BookingService.get_services."""
        # Deactivate Teeth Whitening
        self.svc_sara_whitening.is_active = False
        db.session.commit()
        RequestCache.clear()

        svcs = BookingService.get_services(business_id=1, doctor_id=2)
        svc_ids = [s["id"] for s in svcs]
        self.assertNotIn(3, svc_ids, "Deactivated service must NOT be in get_services")

    def test_hi_greeting_does_not_return_services_list(self):
        """2. On casual greeting 'hi', AI greets warmly and does NOT return a service list UI widget."""
        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "hi")
        # Text should be warm receptionist greeting
        self.assertTrue("welcome" in res["content"].lower() or "hello" in res["content"].lower())
        # UI action should NOT be service_selection
        ui_act = res.get("ui_action")
        if ui_act:
            self.assertNotEqual(ui_act.get("type"), "service_selection", "Casual 'hi' must not force service_selection UI")

    def test_consultation_flow_with_doctor_selection_succeeds(self):
        """
        3. Full consultation flow:
        - User selects consultation
        - User selects Dr. Sara Malik
        - User selects date
        - User selects time slot
        - State does NOT re-prompt service list after slot
        - Final booking succeeds without 'not offering any services' error
        """
        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        # Step 1: User says 'I need a consultation'
        res1 = self.agent.process_message(conv.id, "I need a consultation")
        conv_state = db.session.get(Conversation, conv.id)
        self.assertIsNotNone(conv_state.selected_service_id)

        # Step 2: User chooses Dr. Sara Malik
        res2 = self.agent.process_message(conv.id, "Dr. Sara Malik")
        conv_state = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_state.selected_doctor_id, 2)
        self.assertIsNotNone(conv_state.selected_service_id, "Consultation service should be mapped to Dr. Sara")

        # Step 3: User picks a future weekday date
        target_date = date.today() + timedelta(days=2)
        while target_date.strftime("%A") == "Sunday":
            target_date += timedelta(days=1)
        target_date_str = target_date.strftime("%Y-%m-%d")

        res3 = self.agent.process_message(conv.id, target_date_str)
        conv_state = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_state.requested_date, target_date_str)

        # Step 4: User selects slot 10:00
        res4 = self.agent.process_message(conv.id, "10:00")
        # Must NOT ask "Would you like to book an appointment with Dr. Sara Malik for any of these services?"
        self.assertNotIn("would you like to book an appointment with dr. sara malik for any of these services", res4["content"].lower())
        # Should ask for patient details
        self.assertTrue("name" in res4["content"].lower() or "phone" in res4["content"].lower())

        # Step 5: User provides name and phone
        res5 = self.agent.process_message(conv.id, "Hassan 03001234567")
        # Booking should succeed or confirm, NOT fail with "doctor not offering any services"
        self.assertNotIn("does not offer", res5["content"].lower())
        self.assertNotIn("not offering", res5["content"].lower())

        # Verify an appointment row exists in DB
        appt = Appointment.query.filter_by(business_id=1, doctor_id=2, appointment_date=target_date_str).first()
        self.assertIsNotNone(appt, "Appointment should be successfully booked")
        self.assertEqual(appt.customer.name, "Hassan")
        self.assertEqual(appt.status, "CONFIRMED")

    def test_admin_service_ajax_toggle(self):
        """4. Admin toggle endpoint returns JSON on AJAX request and flips is_active."""
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_user"] = "admin"

        # Toggle service 2 (Dental Cleaning) to inactive
        resp = self.client.post("/admin/services/toggle/2", headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertFalse(data["is_active"])
        self.assertEqual(data["status_text"], "INACTIVE")

        # Toggle back to active
        resp2 = self.client.post("/admin/services/toggle/2", headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.get_json()
        self.assertTrue(data2["success"])
        self.assertTrue(data2["is_active"])
        self.assertEqual(data2["status_text"], "ACTIVE")

    def test_admin_service_delete(self):
        """5. Admin service delete removes unbooked service."""
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_user"] = "admin"

        # Delete service 3
        resp = self.client.post("/admin/services/delete/3", headers={"X-Requested-With": "XMLHttpRequest"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIsNone(db.session.get(Service, 3))

    def test_admin_slots_view_and_manual_booking(self):
        """6. Slot occupancy endpoint returns data and manual booking endpoint creates appointment."""
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_user"] = "admin"

        target_date = date.today() + timedelta(days=1)
        while target_date.strftime("%A") == "Sunday":
            target_date += timedelta(days=1)
        target_date_str = target_date.strftime("%Y-%m-%d")
        resp = self.client.get(f"/admin/slots?date={target_date_str}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Doctor Slot Occupancy", resp.data)

        # Manual booking
        payload = {
            "doctor_id": 1,
            "service_id": 1,
            "appointment_date": target_date_str,
            "appointment_time": "11:00",
            "customer_name": "Ayesha Khan",
            "customer_phone": "03123456789",
            "notes": "Walk-in patient"
        }
        book_resp = self.client.post("/api/admin/appointments/manual-book", json=payload)
        self.assertEqual(book_resp.status_code, 200)
        book_data = book_resp.get_json()
        self.assertTrue(book_data["success"])

        # Check in DB
        appt = Appointment.query.filter_by(business_id=1, doctor_id=1, appointment_date=target_date_str, appointment_time="11:00").first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.customer.name, "Ayesha Khan")

    def test_upfront_all_in_one_message_booking(self):
        """7. Upfront message containing doctor, date, time, name and phone should be extracted seamlessly."""
        target_date = date.today() + timedelta(days=3)
        while target_date.strftime("%A") == "Sunday":
            target_date += timedelta(days=1)
        target_date_str = target_date.strftime("%Y-%m-%d")

        conv = Conversation(business_id=1, status="AI", intent="UNKNOWN", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        msg = f"Hi, my name is Tariq, 03009998877. I want an appointment with Dr. Ahmed Khan on {target_date_str} at 09:30"
        res = self.agent.process_message(conv.id, msg)

        conv_state = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_state.selected_doctor_id, 1)
        self.assertEqual(conv_state.requested_date, target_date_str)
        self.assertEqual(conv_state.requested_time, "09:30")
        self.assertEqual(conv_state.pending_customer_name, "Tariq")
        self.assertEqual(conv_state.pending_customer_phone, "03009998877")

    def test_navbar_active_highlighting_and_chat_neutral(self):
        """8. Issue 1: Navbar active highlighting correctly marks current page and keeps Customer Chat neutral when on admin pages."""
        with self.client.session_transaction() as sess:
            sess["admin_logged_in"] = True
            sess["admin_user"] = "admin"

        # On /admin dashboard
        resp_admin = self.client.get("/admin")
        self.assertEqual(resp_admin.status_code, 200)
        html_admin = resp_admin.data.decode("utf-8")
        self.assertIn('href="/admin" class="nav-item active"', html_admin)
        self.assertNotIn('href="/chat" class="nav-item nav-badge-item active"', html_admin)

        # On /admin/appointments
        resp_appts = self.client.get("/admin/appointments")
        self.assertEqual(resp_appts.status_code, 200)
        html_appts = resp_appts.data.decode("utf-8")
        self.assertIn('href="/admin/appointments" class="nav-item active"', html_appts)
        self.assertNotIn('href="/chat" class="nav-item nav-badge-item active"', html_appts)

        # On /chat
        resp_chat = self.client.get("/chat")
        self.assertEqual(resp_chat.status_code, 200)
        html_chat = resp_chat.data.decode("utf-8")
        self.assertIn('href="/chat" class="nav-item nav-badge-item active"', html_chat)

    def test_voice_stt_time_extraction_variants(self):
        """9. Issue 2: Spoken voice STT time formats (dots, words, phrases) extract cleanly to HH:MM."""
        from ai.agent import _extract_time_token
        from ai.llm_client import _extract_time_str

        test_cases = [
            ("10 a.m.", "10:00"),
            ("10 a.m", "10:00"),
            ("10:00 a.m.", "10:00"),
            ("10:30 a.m.", "10:30"),
            ("ten am", "10:00"),
            ("ten a.m.", "10:00"),
            ("ten o'clock", "10:00"),
            ("i want 10", "10:00"),
            ("book at 10", "10:00"),
            ("two pm", "14:00"),
            ("2 p.m.", "14:00"),
            ("two thirty pm", "14:30"),
            ("half past ten", "10:30"),
            ("10 baje", "10:00"),
            ("do baje", "14:00"),
        ]

        for text, expected in test_cases:
            res_token = _extract_time_token(text)
            self.assertEqual(res_token, expected, f"_extract_time_token failed for '{text}': got {res_token}, expected {expected}")
            res_str = _extract_time_str(text)
            self.assertEqual(res_str, expected, f"_extract_time_str failed for '{text}': got {res_str}, expected {expected}")

    def test_voice_spoken_time_prevents_redundant_slot_selection(self):
        """10. Issue 2: Speaking a slot via microphone sets requested_time and does NOT re-show time_slot_selection widget."""
        target_date = date.today() + timedelta(days=2)
        while target_date.strftime("%A") == "Sunday":
            target_date += timedelta(days=1)
        target_date_str = target_date.strftime("%Y-%m-%d")

        conv = Conversation(
            business_id=1,
            status="AI",
            intent="BOOK_APPOINTMENT",
            workflow_state="CHECKING_AVAILABILITY",
            awaiting_input="time_choice",
            selected_doctor_id=1,
            requested_date=target_date_str
        )
        db.session.add(conv)
        db.session.commit()

        # User speaks time into microphone: "10 a.m."
        res = self.agent.process_message(conv.id, "10 a.m.")

        conv_state = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_state.requested_time, "10:00")
        # Ensure time_slot_selection is NOT re-rendered!
        ui_act = res.get("ui_action")
        if ui_act:
            self.assertNotEqual(ui_act.get("type"), "time_slot_selection", "Redundant time_slot_selection must not be returned once slot is spoken")

    def test_doctor_scoped_service_selection_interactive_widget(self):
        """11. Issue 3: When a doctor is selected, service_selection UI widget contains ONLY that doctor's services."""
        conv = Conversation(
            business_id=1,
            status="AI",
            intent="BOOK_APPOINTMENT",
            workflow_state="COLLECTING_INFO",
            awaiting_input="service_choice",
            selected_doctor_id=2  # Dr. Sara Malik
        )
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action["type"], "service_selection")
        self.assertIn("Dr. Sara Malik", ui_action["title"])

        option_names = [opt["name"] for opt in ui_action["options"]]
        # Dr. Sara's services:
        self.assertIn("Dental Cleaning & Scaling", option_names)
        self.assertIn("Teeth Whitening", option_names)
        # Dr. Ahmed's service must NOT be in the options:
        self.assertNotIn("Dental Checkup & Consultation", option_names)

if __name__ == "__main__":
    unittest.main()
