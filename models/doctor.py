from datetime import datetime, timezone
from models import db

class Doctor(db.Model):
    __tablename__ = "doctors"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    specialization = db.Column(db.String(150), nullable=False)
    working_days = db.Column(db.String(255), nullable=False, default="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday")
    start_time = db.Column(db.String(10), nullable=False, default="09:00")
    end_time = db.Column(db.String(10), nullable=False, default="17:00")
    slot_interval = db.Column(db.Integer, nullable=False, default=30)  # Slot interval in minutes (e.g. 15, 30, 45, 60)
    break_start_time = db.Column(db.String(10), nullable=True)         # Lunch/break start (e.g. "13:00")
    break_end_time = db.Column(db.String(10), nullable=True)           # Lunch/break end (e.g. "14:00")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    appointments = db.relationship("Appointment", backref="doctor", lazy=True)
    schedules = db.relationship("DoctorSchedule", backref="doctor", lazy=True, cascade="all, delete-orphan")
    leaves = db.relationship("DoctorLeave", backref="doctor", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        schedules_list = [s.to_dict() for s in sorted(self.schedules, key=lambda x: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(x.day_of_week) if x.day_of_week in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] else 99)] if self.schedules else []
        if self.schedules:
            active_sched_days = [s.day_of_week for s in sorted(self.schedules, key=lambda x: ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(x.day_of_week) if x.day_of_week in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] else 99) if s.is_available]
            working_days_list = active_sched_days
            # Hours vary per day when per-day schedules exist — the legacy flat
            # columns are stale defaults. Omit them so consumers rely on
            # weekly_schedule instead.
            flat_start_time = None
            flat_end_time = None
        else:
            working_days_list = [d.strip() for d in (self.working_days or "").split(",") if d.strip()]
            flat_start_time = self.start_time
            flat_end_time = self.end_time
        return {
            "id": self.id,
            "business_id": self.business_id,
            "name": self.name,
            "specialization": self.specialization,
            "working_days": working_days_list,
            "start_time": flat_start_time,
            "end_time": flat_end_time,
            "slot_interval": self.slot_interval or 30,
            "break_start_time": self.break_start_time,
            "break_end_time": self.break_end_time,
            "is_active": self.is_active,
            "weekly_schedule": schedules_list,
            "leaves": [l.to_dict() for l in self.leaves] if self.leaves else []
        }

