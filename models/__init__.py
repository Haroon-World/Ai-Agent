from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text

db = SQLAlchemy()

from models.business import Business
from models.doctor import Doctor
from models.service import Service
from models.customer import Customer
from models.appointment import Appointment
from models.conversation import Conversation
from models.message import Message
from models.reminder import Reminder
from models.doctor_schedule import DoctorSchedule
from models.doctor_leave import DoctorLeave

DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def auto_migrate_db(app=None):
    """Automatically inspect existing database tables, add missing columns, and populate default doctor schedules."""
    def _migrate():
        db.create_all()
        try:
            inspector = inspect(db.engine)
            for table_name, table in db.metadata.tables.items():
                if inspector.has_table(table_name):
                    existing_columns = {col['name'] for col in inspector.get_columns(table_name)}
                    for column in table.columns:
                        if column.name not in existing_columns:
                            col_type = column.type.compile(db.engine.dialect)
                            sql = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {col_type}"
                            db.session.execute(text(sql))
                            db.session.commit()
                            print(f"[Auto-Migrate] Added column '{column.name}' to table '{table_name}'.")

            # Seed default DoctorSchedule entries for any existing doctor lacking schedule entries
            doctors = Doctor.query.all()
            for doc in doctors:
                existing_schedules = {s.day_of_week: s for s in doc.schedules}
                working_days_list = [d.strip() for d in (doc.working_days or "").split(",")] if doc.working_days else ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
                for day in DAYS_OF_WEEK:
                    if day not in existing_schedules:
                        is_avail = day in working_days_list
                        sched = DoctorSchedule(
                            doctor_id=doc.id,
                            day_of_week=day,
                            is_available=is_avail,
                            start_time=doc.start_time or "09:00",
                            end_time=doc.end_time or "17:00"
                        )
                        db.session.add(sched)
                db.session.commit()

        except Exception as e:
            db.session.rollback()
            print(f"[Auto-Migrate] Warning during migration: {e}")

    if app:
        with app.app_context():
            _migrate()
    else:
        _migrate()

__all__ = [
    "db",
    "Business",
    "Doctor",
    "Service",
    "Customer",
    "Appointment",
    "Conversation",
    "Message",
    "Reminder",
    "DoctorSchedule",
    "DoctorLeave",
    "auto_migrate_db",
]

