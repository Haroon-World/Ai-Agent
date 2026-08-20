from datetime import datetime, timezone
from models import db

class Appointment(db.Model):
    __tablename__ = "appointments"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False, index=True)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False, index=True)
    
    appointment_date = db.Column(db.String(20), nullable=False, index=True)  # YYYY-MM-DD
    appointment_time = db.Column(db.String(10), nullable=False)             # HH:MM
    status = db.Column(db.String(30), nullable=False, default="CONFIRMED")   # CONFIRMED, CANCELLED, COMPLETED, PENDING
    notes = db.Column(db.Text, nullable=True)
    idempotency_key = db.Column(db.String(100), nullable=True, unique=True, index=True)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    reminders = db.relationship("Reminder", backref="appointment", lazy=True, cascade="all, delete-orphan")

    __table_args__ = (
        db.UniqueConstraint("business_id", "doctor_id", "appointment_date", "appointment_time", name="uq_doctor_appointment_slot"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "business_id": self.business_id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else None,
            "customer_phone": self.customer.phone if self.customer else None,
            "doctor_id": self.doctor_id,
            "doctor_name": self.doctor.name if self.doctor else None,
            "service_id": self.service_id,
            "service_name": self.service.name if self.service else None,
            "duration": self.service.duration if self.service else 30,
            "price": self.service.price if self.service else 0.0,
            "appointment_date": self.appointment_date,
            "appointment_time": self.appointment_time,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
