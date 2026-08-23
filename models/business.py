from datetime import datetime, timezone
from models import db

class Business(db.Model):
    __tablename__ = "businesses"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    business_type = db.Column(db.String(100), nullable=False, default="dental_clinic")
    address = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    timezone = db.Column(db.String(50), nullable=False, default="Asia/Karachi")
    opening_hours = db.Column(db.Text, nullable=False)
    policies = db.Column(db.Text, nullable=True)
    consultation_fee = db.Column(db.Float, nullable=True, default=2000.0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    doctors = db.relationship("Doctor", backref="business", lazy=True, cascade="all, delete-orphan")
    services = db.relationship("Service", backref="business", lazy=True, cascade="all, delete-orphan")
    customers = db.relationship("Customer", backref="business", lazy=True, cascade="all, delete-orphan")
    appointments = db.relationship("Appointment", backref="business", lazy=True, cascade="all, delete-orphan")
    conversations = db.relationship("Conversation", backref="business", lazy=True, cascade="all, delete-orphan")
    reminders = db.relationship("Reminder", backref="business", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "business_type": self.business_type,
            "address": self.address,
            "phone": self.phone,
            "timezone": self.timezone,
            "opening_hours": self.opening_hours,
            "policies": self.policies,
            "consultation_fee": self.consultation_fee,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
