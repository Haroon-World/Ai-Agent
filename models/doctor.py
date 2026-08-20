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
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    appointments = db.relationship("Appointment", backref="doctor", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "business_id": self.business_id,
            "name": self.name,
            "specialization": self.specialization,
            "working_days": self.working_days.split(",") if self.working_days else [],
            "start_time": self.start_time,
            "end_time": self.end_time,
        }
