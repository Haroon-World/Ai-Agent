import pytest
from app import create_app
from config.config import Config
from models import db, Conversation, Appointment, Doctor, Service, DoctorSchedule, DoctorLeave, Business
from ai.agent import Agent
from services.booking_service import BookingService, RequestCache
from seed import seed_database


class TestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    TESTING = True
    SECRET_KEY = "test-reschedule-secret"
    LLM_PROVIDER = "mock"


@pytest.fixture
def app():
    app = create_app(TestConfig)
    with app.app_context():
        RequestCache.clear()
        seed_database(app)
        yield app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


def test_reproduction_doctor_change_updates_database_and_confirmation(app):
    """
    Scenario 1: Exact reproduction test.
    Customer books Dr. Ahmed Khan, then requests to change doctor to Dr. Sara Malik,
    selects her service, picks a time, and confirms with 'all the other data will be same'.
    Verify:
    - appt.doctor_id is updated to Dr. Sara Malik (2)
    - appt.service_id is updated to her service (2)
    - appt.appointment_time is updated to 14:00
    - Only 1 appointment row exists (no duplicate bookings created)
    - Confirmation text explicitly names Dr. Sara Malik and Dental Cleaning & Scaling
    """
    with app.app_context():
        conv = Conversation(business_id=1, status="AI")
        db.session.add(conv)
        db.session.commit()
        agent = Agent(business_id=1, llm_provider="mock")

        # Step 1: Book real appointment with Dr. Ahmed Khan
        agent.process_message(conv.id, "I want to see Dr. Ahmed Khan")
        agent.process_message(conv.id, "Dental Checkup & Consultation")
        agent.process_message(conv.id, "Tomorrow")
        agent.process_message(conv.id, "10:00")
        r_book = agent.process_message(conv.id, "My name is Usman, phone is 03009999999")

        appt = Appointment.query.first()
        assert appt is not None
        assert appt.doctor_id == 1
        assert appt.service_id == 1
        assert appt.status == "CONFIRMED"
        assert Appointment.query.count() == 1

        # Step 2: Customer requests to change doctor
        r_change = agent.process_message(conv.id, "can you change my doctor")
        assert "Dr. Ahmed Khan" in r_change["content"]
        assert "Dr. Sara Malik" in r_change["content"]

        # Step 2b: Select Dr. Sara Malik
        r_sara = agent.process_message(conv.id, "Dr. Sara Malik")
        assert "Dr. Sara Malik" in r_sara["content"]
        assert "Dental Cleaning & Scaling" in r_sara["content"]

        # Step 2c: Select her service
        r_svc = agent.process_message(conv.id, "Dental Cleaning & Scaling")
        assert "Dr. Sara Malik" in r_svc["content"]

        # Step 2d: Pick time
        r_time = agent.process_message(conv.id, "Tomorrow at 14:00")
        assert "02:00 PM" in r_time["content"]

        # Step 3: 'all the other data will be same'
        r_confirm = agent.process_message(conv.id, "all the other data will be same")
        assert "Dr. Sara Malik" in r_confirm["content"]
        assert "Dental Cleaning & Scaling" in r_confirm["content"]
        assert "02:00 PM" in r_confirm["content"]

        # Step 4: Verify actual Appointment row in database
        db.session.refresh(appt)
        assert appt.doctor_id == 2
        assert appt.doctor.name == "Dr. Sara Malik"
        assert appt.service_id == 2
        assert appt.service.name == "Dental Cleaning & Scaling"
        assert appt.appointment_time == "14:00"
        assert Appointment.query.count() == 1


def test_service_mismatch_without_new_service_returns_clear_error(app):
    """
    Scenario 2: Service-mismatch test.
    Change doctor to one who does NOT offer the appointment's current service,
    without specifying a new service.
    Verify:
    - BookingService.reschedule_appointment returns a clear error listing the new doctor's actual services and pricing.
    - Appointment row in database remains unmodified.
    """
    with app.app_context():
        # Appt 1 is with Dr. Ahmed Khan for Dental Checkup (service_id=1)
        appt = Appointment.query.filter_by(doctor_id=1).first()
        if not appt:
            appt = Appointment(
                business_id=1,
                customer_id=1,
                doctor_id=1,
                service_id=1,
                appointment_date="2026-09-10",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt)
            db.session.commit()

        orig_doc_id = appt.doctor_id
        orig_svc_id = appt.service_id

        # Attempt to reschedule to Dr. Sara Malik (doctor_id=2) without specifying a new service
        res = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt.id,
            new_date="2026-09-11",
            new_time="10:00",
            new_doctor_id=2,
            new_service_id=None
        )

        assert res["success"] is False
        assert "Dr. Sara Malik does not offer" in res["error"]
        assert "Dental Cleaning & Scaling" in res["error"]
        assert "Teeth Whitening" in res["error"]

        # Verify appt is unchanged in db
        db.session.refresh(appt)
        assert appt.doctor_id == orig_doc_id
        assert appt.service_id == orig_svc_id


def test_plain_date_time_reschedule_preserves_doctor_and_service(app):
    """
    Scenario 3: Plain date/time-only reschedule.
    Calling reschedule_appointment with only new_date and new_time
    must update date and time, and leave doctor_id and service_id untouched.
    """
    with app.app_context():
        appt = Appointment.query.filter_by(doctor_id=1).first()
        if not appt:
            appt = Appointment(
                business_id=1,
                customer_id=1,
                doctor_id=1,
                service_id=1,
                appointment_date="2026-09-10",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt)
            db.session.commit()

        res = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt.id,
            new_date="2026-09-12",
            new_time="11:00"
        )

        assert res["success"] is True
        db.session.refresh(appt)
        assert appt.appointment_date == "2026-09-12"
        assert appt.appointment_time == "11:00"
        assert appt.doctor_id == 1
        assert appt.service_id == 1


def test_same_service_kept_when_offered_by_new_doctor(app):
    """
    Scenario 4: If a service is offered by the new doctor (or has doctor_id == target_doctor.id),
    it is kept and doctor/date/time are updated.
    """
    with app.app_context():
        doc3 = Doctor(
            business_id=1,
            name="Dr. Tariq Mahmood",
            specialization="General Dentistry",
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time="09:00",
            end_time="17:00",
            is_active=True
        )
        db.session.add(doc3)
        db.session.commit()

        svc3 = Service(
            business_id=1,
            doctor_id=doc3.id,
            name="General Consultation",
            duration=30,
            price=2500,
            is_active=True
        )
        db.session.add(svc3)
        db.session.commit()

        appt = Appointment(
            business_id=1,
            customer_id=1,
            doctor_id=doc3.id,
            service_id=svc3.id,
            appointment_date="2026-09-15",
            appointment_time="10:00",
            status="CONFIRMED"
        )
        db.session.add(appt)
        db.session.commit()

        # Reschedule with new_doctor_id=doc3.id and no new_service_id
        res = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt.id,
            new_date="2026-09-15",
            new_time="11:00",
            new_doctor_id=doc3.id
        )
        assert res["success"] is True
        db.session.refresh(appt)
        assert appt.doctor_id == doc3.id
        assert appt.service_id == svc3.id
        assert appt.appointment_time == "11:00"


def test_conflict_checking_validates_against_new_doctor_schedule(app):
    """
    Scenario 5: Conflict checking validates against the target doctor's schedule.
    - If target doctor is closed on that day -> rejected.
    - If target doctor has a break at that time -> rejected.
    - If target doctor has an existing confirmed appointment -> rejected.
    - If target doctor is on leave -> rejected.
    """
    with app.app_context():
        appt = Appointment.query.filter_by(doctor_id=1).first()
        if not appt:
            appt = Appointment(
                business_id=1,
                customer_id=1,
                doctor_id=1,
                service_id=1,
                appointment_date="2026-09-10",
                appointment_time="10:00",
                status="CONFIRMED"
            )
            db.session.add(appt)
            db.session.commit()

        # 5a. Sunday closed check
        res_sunday = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt.id,
            new_date="2026-09-13",
            new_time="10:00",
            new_doctor_id=2,
            new_service_id=2
        )
        assert res_sunday["success"] is False
        assert "practice" in res_sunday["error"].lower() or "closed" in res_sunday["error"].lower()

        # 5b. Break check
        doc2 = Doctor.query.get(2)
        doc2.break_start_time = "13:00"
        doc2.break_end_time = "14:00"
        db.session.commit()

        res_break = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt.id,
            new_date="2026-09-11",
            new_time="13:00",
            new_doctor_id=2,
            new_service_id=2
        )
        assert res_break["success"] is False
        assert "break" in res_break["error"].lower() or "not available" in res_break["error"].lower()

        # 5c. Existing appointment overlap check
        existing_sara_appt = Appointment(
            business_id=1,
            customer_id=2,
            doctor_id=2,
            service_id=2,
            appointment_date="2026-09-11",
            appointment_time="11:00",
            status="CONFIRMED"
        )
        db.session.add(existing_sara_appt)
        db.session.commit()

        res_conflict = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt.id,
            new_date="2026-09-11",
            new_time="11:00",
            new_doctor_id=2,
            new_service_id=2
        )
        assert res_conflict["success"] is False
        assert "overlap" in res_conflict["error"].lower() or "not available" in res_conflict["error"].lower()

        # 5d. Leave check
        leave = DoctorLeave(
            doctor_id=2,
            leave_date="2026-09-18",
            is_all_day=True,
            reason="Medical Conference"
        )
        db.session.add(leave)
        db.session.commit()

        res_leave = BookingService.reschedule_appointment(
            business_id=1,
            appointment_id=appt.id,
            new_date="2026-09-18",
            new_time="10:00",
            new_doctor_id=2,
            new_service_id=2
        )
        assert res_leave["success"] is False
        assert "leave" in res_leave["error"].lower() or "not available" in res_leave["error"].lower()
