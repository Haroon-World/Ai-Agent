import os
from flask import Flask, current_app
from config.config import Config
from models import db, Business, Doctor, Service, Customer, Appointment, Conversation, Message, Reminder, DoctorSchedule, DAYS_OF_WEEK
from models.user import User
from werkzeug.security import generate_password_hash

def seed_database(app=None):
    """Seed default dental clinic, doctors, and services into the database."""
    def _seed():
        db.create_all()

        # 1. Seed Business
        clinic = db.session.get(Business, Config.DEFAULT_BUSINESS_ID)
        if not clinic:
            clinic = Business(
                id=Config.DEFAULT_BUSINESS_ID,
                name="Arfa Polyclinic",
                business_type="polyclinic",
                address="Plot 42-B, Main Boulevard, Gulberg III, Lahore",
                phone="+92 42 35789000",
                email="admin@arfaclinic.com",
                timezone="Asia/Karachi",
                opening_hours="Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed",
                policies="Please arrive 10 minutes prior to your appointment. Cancellations should be requested at least 2 hours in advance. Emergency walk-ins welcome during working hours."
            )
            db.session.add(clinic)
            db.session.flush()
        elif not clinic.email:
            clinic.email = "admin@arfaclinic.com"
            db.session.commit()

        # 2. Seed Doctors if not existing
        doc_count = Doctor.query.filter_by(business_id=clinic.id).count()
        if doc_count == 0:
            dr_ahmed = Doctor(
                id=1,
                business_id=clinic.id,
                name="Dr. Ahmed Khan",
                specialization="General Dentistry & Orthodontics",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
                start_time="09:00",
                end_time="17:00",
                slot_interval=30
            )
            dr_sara = Doctor(
                id=2,
                business_id=clinic.id,
                name="Dr. Sara Malik",
                specialization="Pediatric & Cosmetic Dentistry",
                working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
                start_time="09:00",
                end_time="17:00",
                slot_interval=30
            )
            db.session.add_all([dr_ahmed, dr_sara])
            db.session.flush()

            # Seed DoctorSchedules (Monday-Saturday Available, Sunday Closed)
            for doc in [dr_ahmed, dr_sara]:
                for day in DAYS_OF_WEEK:
                    sched = DoctorSchedule(
                        doctor_id=doc.id,
                        day_of_week=day,
                        is_available=(day != "Sunday"),
                        start_time="09:00",
                        end_time="17:00"
                    )
                    db.session.add(sched)

        # 3. Seed Services if not existing (split by doctor specialization)
        if Service.query.filter_by(business_id=clinic.id).count() == 0:
            services = [
                # Dr. Ahmed Khan (General Dentistry & Orthodontics)
                Service(
                    id=1,
                    business_id=clinic.id,
                    doctor_id=dr_ahmed.id,
                    name="Dental Checkup & Consultation",
                    description="Comprehensive oral examination, diagnostic x-rays review, and consultation.",
                    duration=30,
                    price=2000.0
                ),
                Service(
                    id=4,
                    business_id=clinic.id,
                    doctor_id=dr_ahmed.id,
                    name="Tooth Extraction",
                    description="Safe, painless tooth removal under local anesthesia.",
                    duration=45,
                    price=5000.0
                ),
                Service(
                    id=5,
                    business_id=clinic.id,
                    doctor_id=dr_ahmed.id,
                    name="Root Canal Treatment",
                    description="In-depth endodontic treatment to save infected or damaged teeth.",
                    duration=60,
                    price=12000.0
                ),
                Service(
                    id=6,
                    business_id=clinic.id,
                    doctor_id=dr_ahmed.id,
                    name="Dental Braces Consultation",
                    description="Orthodontic evaluation and alignment planning for braces or clear aligners.",
                    duration=30,
                    price=3000.0
                ),
                # Dr. Sara Malik (Pediatric & Cosmetic Dentistry)
                Service(
                    id=2,
                    business_id=clinic.id,
                    doctor_id=dr_sara.id,
                    name="Dental Cleaning & Scaling",
                    description="Professional tartar removal, plaque scaling, and teeth polishing.",
                    duration=45,
                    price=4000.0
                ),
                Service(
                    id=3,
                    business_id=clinic.id,
                    doctor_id=dr_sara.id,
                    name="Teeth Whitening",
                    description="Advanced laser teeth whitening for a bright, radiant smile.",
                    duration=60,
                    price=8000.0
                ),
                Service(
                    id=7,
                    business_id=clinic.id,
                    doctor_id=dr_sara.id,
                    name="Pediatric & General Consultation",
                    description="Oral health checkup, pediatric examination, and general consultation.",
                    duration=30,
                    price=2000.0
                ),
            ]
            db.session.add_all(services)

        db.session.commit()

        # 4. Seed Clinic Admin for Arfa Polyclinic (is_platform_admin=False: pure clinic tenant)
        existing_clinic_admin = User.query.filter_by(
            username=Config.ADMIN_USERNAME
        ).first()
        if not existing_clinic_admin:
            clinic_admin = User(
                business_id=clinic.id,
                username=Config.ADMIN_USERNAME,
                email="admin@arfaclinic.com",
                password_hash=generate_password_hash(Config.ADMIN_PASSWORD),
                is_platform_admin=False,
            )
            db.session.add(clinic_admin)
            db.session.commit()
            print(f"[Seed] Clinic admin user '{Config.ADMIN_USERNAME}' created for '{clinic.name}'.")
        else:
            # Ensure Arfa clinic admin is NOT marked as platform admin and has email
            updated = False
            if existing_clinic_admin.is_platform_admin:
                existing_clinic_admin.is_platform_admin = False
                updated = True
            if not existing_clinic_admin.email:
                existing_clinic_admin.email = "admin@arfaclinic.com"
                updated = True
            if existing_clinic_admin.business_id != clinic.id:
                existing_clinic_admin.business_id = clinic.id
                updated = True
            if updated:
                db.session.commit()

        # 5. Seed Dedicated Platform Owner (Separate from any clinic, business_id=None)
        existing_platform_admin = User.query.filter_by(
            username=Config.PLATFORM_ADMIN_USERNAME,
            is_platform_admin=True
        ).first()
        if not existing_platform_admin:
            if Config.PLATFORM_ADMIN_PASSWORD:
                platform_owner = User(
                    business_id=None,
                    username=Config.PLATFORM_ADMIN_USERNAME,
                    email="platform@clinicconnectai.com",
                    password_hash=generate_password_hash(Config.PLATFORM_ADMIN_PASSWORD),
                    is_platform_admin=True,
                )
                db.session.add(platform_owner)
                db.session.commit()
                print(f"[Seed] Separate Platform Owner '{Config.PLATFORM_ADMIN_USERNAME}' created.")
        else:
            # Preserve existing platform admin password — NEVER overwrite on startup
            updated = False
            if existing_platform_admin.business_id is not None:
                existing_platform_admin.business_id = None
                updated = True
            if not existing_platform_admin.email:
                existing_platform_admin.email = "platform@clinicconnectai.com"
                updated = True
            if updated:
                db.session.commit()

    if app:
        with app.app_context():
            _seed()
    elif current_app:
        _seed()
    else:
        flask_app = Flask(__name__)
        flask_app.config.from_object(Config)
        db.init_app(flask_app)
        with flask_app.app_context():
            _seed()

if __name__ == "__main__":
    seed_database()
