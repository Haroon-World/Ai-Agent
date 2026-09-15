from datetime import datetime, timezone
from models import db

class DoctorSchedule(db.Model):
    __tablename__ = "doctor_schedules"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False, index=True)
    day_of_week = db.Column(db.String(20), nullable=False)  # Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday
    is_available = db.Column(db.Boolean, nullable=False, default=True)
    start_time = db.Column(db.String(10), nullable=False, default="09:00")  # HH:MM
    end_time = db.Column(db.String(10), nullable=False, default="17:00")    # HH:MM
    shift_2_start_time = db.Column(db.String(10), nullable=True)
    shift_2_end_time = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        db.UniqueConstraint("doctor_id", "day_of_week", name="uq_doctor_day_of_week"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "doctor_id": self.doctor_id,
            "day_of_week": self.day_of_week,
            "is_available": self.is_available,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "shift_2_start_time": self.shift_2_start_time,
            "shift_2_end_time": self.shift_2_end_time
        }
