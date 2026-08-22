from datetime import datetime, timezone
from models import db

class DoctorLeave(db.Model):
    __tablename__ = "doctor_leaves"

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False, index=True)
    leave_date = db.Column(db.String(20), nullable=False, index=True)  # YYYY-MM-DD
    start_time = db.Column(db.String(10), nullable=True)  # Optional HH:MM (if partial day)
    end_time = db.Column(db.String(10), nullable=True)    # Optional HH:MM (if partial day)
    is_all_day = db.Column(db.Boolean, nullable=False, default=True)
    reason = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "doctor_id": self.doctor_id,
            "leave_date": self.leave_date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "is_all_day": self.is_all_day,
            "reason": self.reason
        }
