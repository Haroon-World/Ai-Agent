import unittest
from datetime import datetime, date, timedelta
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Appointment, Conversation, Message, Customer
from ai.agent import Agent
from services.booking_service import BookingService

class TestControlledPricingAndWorkflow(unittest.TestCase):
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

        self.business = db.session.get(Business, 1)
        if not self.business:
            self.business = Business(
                id=1,
                name="SmileCare Dental Clinic",
                business_type="dental_clinic",
                address="Plot 42-B, Main Boulevard, Gulberg III, Lahore",
                phone="+92 42 35789000",
                timezone="Asia/Karachi",
                opening_hours="Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed",
                policies="Please arrive 10 mins early.",
                consultation_fee=2000.0
            )
            db.session.add(self.business)
            db.session.commit()

        self.agent = Agent(business_id=1)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _next_working_date(self, doctor_id=2):
        today = date.today()
        for i in range(1, 8):
            target = today + timedelta(days=i)
            if target.weekday() in [0, 1, 3]: # Mon, Tue, Thu
                return target.strftime("%Y-%m-%d")
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    def test_a_natural_appointment_request_sequential_flow(self):
        """
        Test A — Natural appointment request:
        User: My name is Ali and I want an appointment.
        AI: What would you like help with? [services + I don't know / consultation]
        User: I don't know, my tooth hurts.
        AI: No problem. A consultation is PKR 2,000. Which doctor would you prefer?
        User: Sara.
        AI: Which date?
        User: Tomorrow.
        AI: Available slots for Dr. Sara...
        User: 10
        AI: Please provide your phone number.
        User: 03001234567
        AI: confirmation summary
        User: Yes
        AI: Appointment confirmed.
        """
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        # Turn 1: User introduces name and asks for appointment
        res1 = self.agent.process_message(conv.id, "My name is Ali and I want an appointment.")
        conv = db.session.get(Conversation, conv.id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertIn("service", res1["content"].lower() + str(res1.get("ui_action")))

        # Turn 2: User doesn't know treatment
        res2 = self.agent.process_message(conv.id, "I don't know, my tooth hurts.")
        conv = db.session.get(Conversation, conv.id)
        self.assertIn("consultation", res2["content"].lower())
        self.assertIn("doctor", res2["content"].lower())

        # Turn 3: Doctor selection
        res3 = self.agent.process_message(conv.id, "Sara.")
        conv = db.session.get(Conversation, conv.id)
        self.assertEqual(conv.selected_doctor_id, 2)
        self.assertIn("date", res3["content"].lower())

        # Turn 4: Date selection
        target_date = self._next_working_date(2)
        res4 = self.agent.process_message(conv.id, target_date)
        conv = db.session.get(Conversation, conv.id)
        self.assertIsNotNone(conv.requested_date)
        self.assertTrue("available" in res4["content"].lower() or "slot" in res4["content"].lower() or res4.get("ui_action") is not None)

        # Turn 5: Time selection
        res5 = self.agent.process_message(conv.id, "10:00")
        conv = db.session.get(Conversation, conv.id)
        # Name "Ali" was already known, so it asks for phone
        self.assertIn("phone", res5["content"].lower())

        # Turn 6: Phone number
        res6 = self.agent.process_message(conv.id, "03001234567")
        conv = db.session.get(Conversation, conv.id)
        # Should ask for confirmation
        self.assertTrue("confirm" in res6["content"].lower() or (res6.get("ui_action") and res6["ui_action"]["type"] == "booking_confirmation"))

        # Turn 7: Confirmation
        res7 = self.agent.process_message(conv.id, "Yes")
        self.assertIn("confirmed", res7["content"].lower())

        # Check DB appointment record
        appt = Appointment.query.filter_by(business_id=1, doctor_id=2).first()
        self.assertIsNotNone(appt)
        self.assertEqual(appt.customer.name, "Ali")
        self.assertEqual(appt.status, "CONFIRMED")

    def test_b_everything_in_one_sentence(self):
        """
        Test B — Everything in one sentence:
        User: "Hi, I'm Ali. I need a cleaning appointment with Dr Sara on {target_date}."
        Expected:
        - Extracts Ali
        - Extracts cleaning
        - Extracts Sara
        - Extracts target date
        - Checks availability
        - Asks only for missing phone upon selecting slot
        - Completes booking
        """
        target_date = self._next_working_date(2)
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res1 = self.agent.process_message(conv.id, f"Hi, I'm Ali. I need a cleaning appointment with Dr Sara on {target_date}.")
        conv = db.session.get(Conversation, conv.id)
        self.assertEqual(conv.pending_customer_name, "Ali")
        self.assertEqual(conv.selected_doctor_id, 2)
        self.assertEqual(conv.selected_service_id, 2)
        self.assertIsNotNone(conv.requested_date)

        # Select time
        res2 = self.agent.process_message(conv.id, "10:00")
        # Asks for phone
        self.assertIn("phone", res2["content"].lower())

        # Provide phone
        res3 = self.agent.process_message(conv.id, "03001234567")
        # Show confirmation
        self.assertTrue("confirm" in res3["content"].lower() or (res3.get("ui_action") and res3["ui_action"]["type"] == "booking_confirmation"))

        # Confirm
        res4 = self.agent.process_message(conv.id, "Confirm please")
        self.assertIn("confirmed", res4["content"].lower())

    def test_c_user_does_not_know_treatment(self):
        """
        Test C — User does not know treatment:
        User: "My tooth hurts and I don't know what treatment I need."
        Expected: Consultation / Appointment option offered at PKR 2,000.
        """
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "My tooth hurts and I don't know what treatment I need.")
        self.assertIn("consultation", res["content"].lower())

    def test_d_admin_changes_price(self):
        """
        Test D — Admin changes price:
        Change Consultation = PKR 2,000 to PKR 2,500.
        Then ask AI: How much is a consultation?
        Expected: PKR 2,500 without changing python code.
        """
        # Change price in DB
        svc = db.session.get(Service, 1)
        svc.price = 2500.0
        self.business.consultation_fee = 2500.0
        db.session.commit()
        from services.booking_service import RequestCache
        RequestCache.clear()

        # Prompt building and AI dynamic reading
        from ai.prompts import build_system_prompt
        prompt_text = build_system_prompt(1)
        self.assertIn("2,500", prompt_text)

        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "How much is checkup and consultation?")
        # Tool get_services returns real DB price
        self.assertTrue("2,500" in res["content"] or "2500" in res["content"])

    def test_e_cancel_and_rebook(self):
        """
        Test E — Cancel and rebook:
        Book 10:00 -> Cancel appointment -> Book 10:00 again.
        Expected: Second booking succeeds because cancelled slot is released.
        """
        target_date = self._next_working_date(2)
        
        # 1. Book first appointment
        res1 = BookingService.book_appointment(
            business_id=1,
            customer_name="Ali",
            customer_phone="03001234567",
            doctor_id=2,
            service_id=1,
            appointment_date=target_date,
            appointment_time="10:00"
        )
        self.assertTrue(res1["success"])
        appt_id = res1["appointment_id"]

        # 2. Verify 10:00 is now unavailable
        avail1 = BookingService.check_availability(
            business_id=1,
            doctor_id=2,
            date_str=target_date,
            service_id=1
        )
        self.assertNotIn("10:00", avail1["available_slots"])

        # 3. Cancel appointment
        res_cancel = BookingService.cancel_appointment(
            business_id=1,
            appointment_id=appt_id,
            reason="Rescheduling"
        )
        self.assertTrue(res_cancel["success"])

        # 4. Verify 10:00 is freed up again
        avail2 = BookingService.check_availability(
            business_id=1,
            doctor_id=2,
            date_str=target_date,
            service_id=1
        )
        self.assertIn("10:00", avail2["available_slots"])

        # 5. Book 10:00 again
        res2 = BookingService.book_appointment(
            business_id=1,
            customer_name="Hamza",
            customer_phone="03009876543",
            doctor_id=2,
            service_id=1,
            appointment_date=target_date,
            appointment_time="10:00"
        )
        self.assertTrue(res2["success"])
        self.assertEqual(res2["appointment"]["customer_name"], "Hamza")

    def test_f_human_handoff(self):
        """
        Test F — Human handoff:
        User: I want to speak to a human.
        Expected: AI calls human_handoff, status becomes HUMAN.
        """
        conv = Conversation(business_id=1, status="AI", workflow_state="START")
        db.session.add(conv)
        db.session.commit()

        res = self.agent.process_message(conv.id, "I want to speak to a human.")
        conv = db.session.get(Conversation, conv.id)
        self.assertEqual(conv.status, "HUMAN")
        self.assertIn("reception", res["content"].lower())

    def test_g_cancel_and_rebook_in_same_conversation(self):
        """
        Test G — Cancel and rebook in same conversation:
        1. Book confirmed appointment with Dr. Ahmed Khan.
        2. Send Roman Urdu cancellation ("meri appointment cancel kr do").
           Verifies cancel_appointment is executed and status becomes CANCELLED in DB.
        3. Send new booking request with same details ("meri sari detail wohi hai, Dr Sara ke sath kal 3 baje ki appointment rakh dein").
           Verifies the AI does not falsely claim the new appointment is confirmed before booking.
        4. Confirm booking -> verifies a real NEW row for Dr. Sara is created in Appointment table,
           and the original CANCELLED row is preserved.
        """
        import freezegun
        from services.booking_service import RequestCache

        with freezegun.freeze_time("2026-08-31 10:00:00+05:00"):
            RequestCache.clear()
            conv = Conversation(business_id=1, status="AI", workflow_state="START")
            db.session.add(conv)
            db.session.commit()

            # Step 1: Pre-book appointment with Dr. Ahmed Khan
            cust = Customer(business_id=1, name="Ali Khan", phone="03001234567")
            db.session.add(cust)
            db.session.flush()

            appt1 = Appointment(
                business_id=1,
                customer_id=cust.id,
                doctor_id=1,
                service_id=1,
                appointment_date="2026-09-01",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt1)
            conv.customer_id = cust.id
            conv.workflow_state = "BOOKED"
            conv.selected_doctor_id = 1
            conv.selected_service_id = 1
            conv.requested_date = "2026-09-01"
            conv.requested_time = "10:00"
            conv.pending_customer_name = "Ali Khan"
            conv.pending_customer_phone = "03001234567"
            db.session.commit()

            # Step 2: Send cancellation
            r2 = self.agent.process_message(conv.id, "meri appointment cancel kr do")
            self.assertEqual(r2["status"], "AI")
            self.assertIn("cancel_appointment", [t["name"] for t in r2.get("executed_tools", [])])
            appt1_db = db.session.get(Appointment, appt1.id)
            self.assertEqual(appt1_db.status, "CANCELLED")

            # Step 3: Rebook with Dr. Sara at 15:00 tomorrow in same conversation
            r3 = self.agent.process_message(conv.id, "meri sari detail wohi hai, Dr Sara ke sath kal 3 baje ki appointment rakh dein")
            self.assertEqual(r3["status"], "AI")
            # Must NOT claim appointment is confirmed yet
            self.assertNotIn("Aap ki appointment confirm ho gayi hai", r3["content"])
            self.assertNotIn("Your appointment has been successfully booked", r3["content"])

            # Step 4: Confirm booking
            r4 = self.agent.process_message(conv.id, "haan confirm kar dein")
            self.assertEqual(r4["status"], "AI")
            self.assertIn("book_appointment", [t["name"] for t in r4.get("executed_tools", [])])
            self.assertTrue("confirm" in r4["content"].lower() or "confirmed" in r4["content"].lower())

            # Verify Database rows
            appts = Appointment.query.filter_by(business_id=1, customer_id=cust.id).order_by(Appointment.id.asc()).all()
            self.assertEqual(len(appts), 2, "Must be 2 appointments: 1 cancelled, 1 newly confirmed")
            self.assertEqual(appts[0].id, appt1.id)
            self.assertEqual(appts[0].status, "CANCELLED")
            self.assertEqual(appts[0].doctor_id, 1)

            self.assertEqual(appts[1].status, "CONFIRMED")
            self.assertEqual(appts[1].doctor_id, 2)
            self.assertEqual(appts[1].appointment_date, "2026-09-01")
            self.assertEqual(appts[1].appointment_time, "15:00")

    def test_h_reschedule_cancelled_appointment_rejected(self):
        """
        Test H — Rescheduling a CANCELLED appointment must be rejected by BookingService.
        """
        target_date = self._next_working_date(2)
        res1 = BookingService.book_appointment(
            business_id=1,
            customer_name="Test User",
            customer_phone="03001122334",
            doctor_id=2,
            service_id=1,
            appointment_date=target_date,
            appointment_time="11:00"
        )
        self.assertTrue(res1["success"])
        appt_id = res1["appointment_id"]

        # Cancel
        res_cancel = BookingService.cancel_appointment(1, appt_id)
        self.assertTrue(res_cancel["success"])

        # Attempt to reschedule cancelled appointment
        res_reschedule = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt_id,
            new_date=target_date,
            new_time="12:00"
        )
        self.assertFalse(res_reschedule["success"])
        self.assertIn("cannot reschedule cancelled appointment", res_reschedule["error"].lower())

    def test_i_update_customer_contact_details_without_cancelling_or_rescheduling(self):
        """
        Test I — Patient updates/corrects their own contact details (phone number).
        Verifies:
        1. update_customer_details tool is executed.
        2. Appointment status is strictly UNCHANGED (CONFIRMED).
        3. Customer phone number is updated in the database.
        4. Response explicitly confirms contact details update rather than rescheduling/cancelling.
        """
        import freezegun
        from services.booking_service import RequestCache

        with freezegun.freeze_time("2026-08-31 10:00:00+05:00"):
            RequestCache.clear()
            conv = Conversation(business_id=1, status="AI", workflow_state="BOOKED")
            cust = Customer(business_id=1, name="Ali Khan", phone="03001234567")
            db.session.add(cust)
            db.session.flush()

            conv.customer_id = cust.id
            conv.selected_doctor_id = 1
            conv.selected_service_id = 1
            conv.requested_date = "2026-09-01"
            conv.requested_time = "10:00"
            conv.pending_customer_name = "Ali Khan"
            conv.pending_customer_phone = "03001234567"
            db.session.add(conv)
            db.session.flush()

            appt = Appointment(
                business_id=1,
                customer_id=cust.id,
                doctor_id=1,
                service_id=1,
                appointment_date="2026-09-01",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt)
            db.session.commit()

            msg = "Can you change my mobile number? Actually I didn't notice that that mobile number was of my brother. So write my mobile number 031-875-38771."
            res = self.agent.process_message(conv.id, msg)

            self.assertEqual(res["status"], "AI")
            self.assertIn("update_customer_details", [t["name"] for t in res.get("executed_tools", [])])
            self.assertNotIn("cancel_appointment", [t["name"] for t in res.get("executed_tools", [])])
            self.assertNotIn("reschedule_appointment", [t["name"] for t in res.get("executed_tools", [])])

            # Appointment status MUST remain CONFIRMED
            appt_db = db.session.get(Appointment, appt.id)
            self.assertEqual(appt_db.status, "CONFIRMED")
            self.assertEqual(appt_db.appointment_date, "2026-09-01")
            self.assertEqual(appt_db.appointment_time, "10:00")

            # Customer phone MUST be updated in database
            cust_db = db.session.get(Customer, cust.id)
            self.assertEqual(cust_db.phone, "03187538771")

            # Message content confirms details updated, not appointment cancelled
            self.assertIn("contact details have been successfully updated", res["content"].lower())
            self.assertNotIn("cancelled", res["content"].lower())

    def test_j_genuine_cancel_still_works(self):
        """
        Test J — Genuine cancellation request triggers cancel_appointment and sets status to CANCELLED.
        """
        import freezegun
        from services.booking_service import RequestCache

        with freezegun.freeze_time("2026-08-31 10:00:00+05:00"):
            RequestCache.clear()
            conv = Conversation(business_id=1, status="AI", workflow_state="BOOKED")
            cust = Customer(business_id=1, name="Hassan Raza", phone="03007654321")
            db.session.add(cust)
            db.session.flush()

            conv.customer_id = cust.id
            conv.pending_customer_phone = "03007654321"
            db.session.add(conv)

            appt = Appointment(
                business_id=1,
                customer_id=cust.id,
                doctor_id=1,
                service_id=1,
                appointment_date="2026-09-01",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt)
            db.session.commit()

            res = self.agent.process_message(conv.id, "Please cancel my appointment")
            self.assertIn("cancel_appointment", [t["name"] for t in res.get("executed_tools", [])])
            appt_db = db.session.get(Appointment, appt.id)
            self.assertEqual(appt_db.status, "CANCELLED")
            self.assertIn("cancelled", res["content"].lower())

    def test_k_genuine_reschedule_service_execution(self):
        """
        Test K — Genuine rescheduling updates appointment date & time via BookingService.reschedule_appointment.
        """
        target_date = self._next_working_date(2)
        res_book = BookingService.book_appointment(
            business_id=1,
            customer_name="Usman Tariq",
            customer_phone="03005544332",
            doctor_id=2,
            service_id=1,
            appointment_date=target_date,
            appointment_time="09:00"
        )
        self.assertTrue(res_book["success"])
        appt_id = res_book["appointment_id"]

        res_resched = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt_id,
            new_date=target_date,
            new_time="14:00"
        )
        self.assertTrue(res_resched["success"])
        appt_db = db.session.get(Appointment, appt_id)
        self.assertEqual(appt_db.status, "CONFIRMED")
        self.assertEqual(appt_db.appointment_date, target_date)
        self.assertEqual(appt_db.appointment_time, "14:00")


if __name__ == "__main__":
    unittest.main()
