from datetime import datetime, timezone
from models import db

class Reminder(db.Model):
    __tablename__ = "reminders"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.id"), nullable=False, index=True)
    scheduled_for = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="SCHEDULED") # SCHEDULED, SENT, CANCELLED
    reminder_type = db.Column(db.String(50), nullable=False, default="24H_BEFORE")
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "business_id": self.business_id,
            "appointment_id": self.appointment_id,
            "customer_name": self.appointment.customer.name if self.appointment and self.appointment.customer else "N/A",
            "customer_phone": self.appointment.customer.phone if self.appointment and self.appointment.customer else "N/A",
            "doctor_name": self.appointment.doctor.name if self.appointment and self.appointment.doctor else "N/A",
            "appointment_date": self.appointment.appointment_date if self.appointment else "N/A",
            "appointment_time": self.appointment.appointment_time if self.appointment else "N/A",
            "scheduled_for": self.scheduled_for.strftime("%Y-%m-%d %H:%M") if self.scheduled_for else None,
            "status": self.status,
            "reminder_type": self.reminder_type,
            "sent_at": self.sent_at.strftime("%Y-%m-%d %H:%M") if self.sent_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
