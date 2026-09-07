from datetime import datetime, timezone
from models import db

class SubscriptionRequest(db.Model):
    """
    Tracks subscription upgrade/renewal requests submitted by clinic clients
    and awaiting review/approval by the SaaS platform onboarding team.
    """
    __tablename__ = "subscription_requests"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id"), nullable=False, index=True)
    
    plan_name = db.Column(db.String(50), nullable=False)  # "1_month", "3_months", "6_months", "1_year", "custom"
    plan_display_name = db.Column(db.String(100), nullable=False)  # "Standard Monthly Plan (30 Days)"
    duration_days = db.Column(db.Integer, nullable=False, default=30)
    
    requested_by_user = db.Column(db.String(80), nullable=True)
    # Status: "pending", "approved", "rejected", "cancelled"
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    notes = db.Column(db.Text, nullable=True)
    
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.String(80), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "business_id": self.business_id,
            "business_name": self.business.name if self.business else "Unknown Clinic",
            "plan_name": self.plan_name,
            "plan_display_name": self.plan_display_name,
            "duration_days": self.duration_days,
            "requested_by_user": self.requested_by_user,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M UTC") if self.created_at else None,
            "reviewed_at": self.reviewed_at.strftime("%Y-%m-%d %H:%M UTC") if self.reviewed_at else None,
            "reviewed_by": self.reviewed_by,
        }
