from datetime import datetime, timezone
from models import db

class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(50), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    appointments = db.relationship("Appointment", backref="customer", lazy=True)
    conversations = db.relationship("Conversation", backref="customer", lazy=True)

    __table_args__ = (
        db.UniqueConstraint("business_id", "phone", name="uq_business_customer_phone"),
    )

    def to_dict(self):
        return {
            "id": self.id,
            "business_id": self.business_id,
            "name": self.name,
            "phone": self.phone,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
