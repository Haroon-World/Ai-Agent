from datetime import datetime, timezone
from models import db

class Service(db.Model):
    __tablename__ = "services"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    duration = db.Column(db.Integer, nullable=False, default=30)  # In minutes
    price = db.Column(db.Float, nullable=False, default=0.0)      # Currency units (PKR / USD)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    appointments = db.relationship("Appointment", backref="service", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "business_id": self.business_id,
            "doctor_id": self.doctor_id,
            "name": self.name,
            "description": self.description,
            "duration": self.duration,
            "price": self.price,
            "is_active": self.is_active,
        }
