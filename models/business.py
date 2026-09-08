from datetime import datetime, timezone
from models import db

class Business(db.Model):
    __tablename__ = "businesses"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    business_type = db.Column(db.String(100), nullable=False, default="dental_clinic")
    address = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(50), nullable=False)
    email = db.Column(db.String(120), nullable=True)
    timezone = db.Column(db.String(50), nullable=False, default="Asia/Karachi")
    opening_hours = db.Column(db.Text, nullable=False, default="Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed")
    policies = db.Column(db.Text, nullable=True)
    consultation_fee = db.Column(db.Float, nullable=True, default=2000.0)
    
    # Subscription & Multi-Tenant Lifecycle
    # Status can be: "trial", "active", "expired", "cancelled"
    subscription_status = db.Column(db.String(30), nullable=False, default="trial")
    subscription_start_date = db.Column(db.DateTime, nullable=True)
    trial_ends_at = db.Column(db.DateTime, nullable=True)
    subscription_expires_at = db.Column(db.DateTime, nullable=True)
    active_plan_name = db.Column(db.String(100), nullable=True, default="1-Month Free Trial")
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    doctors = db.relationship("Doctor", backref="business", lazy=True, cascade="all, delete-orphan")
    services = db.relationship("Service", backref="business", lazy=True, cascade="all, delete-orphan")
    customers = db.relationship("Customer", backref="business", lazy=True, cascade="all, delete-orphan")
    appointments = db.relationship("Appointment", backref="business", lazy=True, cascade="all, delete-orphan")
    conversations = db.relationship("Conversation", backref="business", lazy=True, cascade="all, delete-orphan")
    reminders = db.relationship("Reminder", backref="business", lazy=True, cascade="all, delete-orphan")
    subscription_requests = db.relationship("SubscriptionRequest", backref="business", lazy=True, cascade="all, delete-orphan", order_by="desc(SubscriptionRequest.created_at)")

    @property
    def current_pending_request(self):
        """Return the latest pending SubscriptionRequest if any."""
        for req in self.subscription_requests:
            if req.status == "pending":
                return req
        return None

    @property
    def effective_expiry_date(self):
        """Return the date when the client's current access expires."""
        if self.subscription_status == "active" and self.subscription_expires_at:
            return self.subscription_expires_at
        return self.trial_ends_at

    @property
    def is_subscription_valid(self) -> bool:
        """Return True if the clinic's trial or subscription is currently active."""
        from datetime import timezone
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        if self.subscription_status == "cancelled":
            return False

        if self.subscription_status == "active":
            if not self.subscription_expires_at:
                return True
            exp = self.subscription_expires_at.replace(tzinfo=None) if self.subscription_expires_at.tzinfo else self.subscription_expires_at
            return now_utc <= exp

        # Default is trial
        if not self.trial_ends_at:
            return True
        trial_exp = self.trial_ends_at.replace(tzinfo=None) if self.trial_ends_at.tzinfo else self.trial_ends_at
        return now_utc <= trial_exp

    @property
    def days_remaining(self) -> int:
        """Calculate integer days remaining until subscription or trial expires."""
        exp = self.effective_expiry_date
        if not exp:
            return 30
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        exp_naive = exp.replace(tzinfo=None) if exp.tzinfo else exp
        diff = (exp_naive - now_utc).total_seconds()
        return max(0, int(diff // 86400))

    @property
    def subscription_badge(self) -> dict:
        """Return formatted badge metadata for display in platform and clinic portals."""
        if self.subscription_status == "cancelled":
            return {
                "status": "cancelled",
                "label": "Subscription Cancelled",
                "color": "danger",
                "days": 0,
                "plan_name": self.active_plan_name or "Cancelled",
                "expires_at": "Cancelled"
            }
        if not self.is_subscription_valid:
            return {
                "status": "expired",
                "label": "Subscription Expired",
                "color": "danger",
                "days": 0,
                "plan_name": self.active_plan_name or "Expired",
                "expires_at": self.effective_expiry_date.strftime("%b %d, %Y") if self.effective_expiry_date else "N/A"
            }
        if self.subscription_status == "active":
            return {
                "status": "active",
                "label": f"{self.active_plan_name or 'Active Plan'}",
                "color": "success",
                "days": self.days_remaining,
                "plan_name": self.active_plan_name or "Active Plan",
                "expires_at": self.subscription_expires_at.strftime("%b %d, %Y") if self.subscription_expires_at else "Unlimited"
            }
        # Trial
        return {
            "status": "trial",
            "label": f"1-Month Free Trial ({self.days_remaining}d left)",
            "color": "warning" if self.days_remaining <= 7 else "info",
            "days": self.days_remaining,
            "plan_name": "1-Month Free Trial",
            "expires_at": self.trial_ends_at.strftime("%b %d, %Y") if self.trial_ends_at else "N/A"
        }

    def to_dict(self):
        pending = self.current_pending_request
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
            "subscription_status": self.subscription_status,
            "subscription_start_date": self.subscription_start_date.isoformat() if self.subscription_start_date else None,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "subscription_expires_at": self.subscription_expires_at.isoformat() if self.subscription_expires_at else None,
            "active_plan_name": self.active_plan_name,
            "is_subscription_valid": self.is_subscription_valid,
            "days_remaining": self.days_remaining,
            "pending_request": pending.to_dict() if pending else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

