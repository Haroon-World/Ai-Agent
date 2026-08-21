from datetime import datetime, timezone
from models import db

class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True, index=True)
    visitor_id = db.Column(db.String(100), nullable=True, index=True) # Server-issued signed session/visitor ID
    channel = db.Column(db.String(50), nullable=False, default="web_chat")
    status = db.Column(db.String(30), nullable=False, default="AI") # AI, HUMAN, CLOSED
    
    # Structured conversation state
    intent = db.Column(db.String(50), nullable=True, default="UNKNOWN")
    workflow_state = db.Column(db.String(50), nullable=False, default="START") # START, COLLECTING_INFO, CHECKING_AVAILABILITY, AWAITING_CONFIRMATION, BOOKED
    selected_service_id = db.Column(db.Integer, nullable=True)
    selected_doctor_id = db.Column(db.Integer, nullable=True)
    requested_date = db.Column(db.String(20), nullable=True)
    requested_time = db.Column(db.String(10), nullable=True)
    pending_customer_name = db.Column(db.String(100), nullable=True)
    pending_customer_phone = db.Column(db.String(50), nullable=True)
    handoff_reason = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    messages = db.relationship("Message", backref="conversation", lazy=True, cascade="all, delete-orphan", order_by="Message.created_at")

    def to_dict(self):
        return {
            "id": self.id,
            "business_id": self.business_id,
            "customer_id": self.customer_id,
            "customer_name": self.customer.name if self.customer else (self.pending_customer_name or "Guest Visitor"),
            "customer_phone": self.customer.phone if self.customer else self.pending_customer_phone,
            "visitor_id": self.visitor_id,
            "channel": self.channel,
            "status": self.status,
            "intent": self.intent,
            "workflow_state": self.workflow_state,
            "selected_service_id": self.selected_service_id,
            "selected_doctor_id": self.selected_doctor_id,
            "requested_date": self.requested_date,
            "requested_time": self.requested_time,
            "pending_customer_name": self.pending_customer_name,
            "pending_customer_phone": self.pending_customer_phone,
            "handoff_reason": self.handoff_reason,
            "message_count": len(self.messages),
            "last_message": self.messages[-1].content if self.messages else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
