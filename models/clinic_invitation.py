import secrets
from datetime import datetime, timezone, timedelta
from models import db


class ClinicInvitation(db.Model):
    """
    Stores pending clinic onboarding invitations sent by the SaaS platform team.
    Allows client administrators to click a secure link and choose their own
    unique username and password.
    """
    __tablename__ = "clinic_invitations"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(db.Integer, db.ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    email = db.Column(db.String(120), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    is_used = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Relationship back to the provisioned Business
    business = db.relationship("Business", backref=db.backref("invitations", lazy=True, cascade="all, delete-orphan"))

    @classmethod
    def create_invitation(cls, business_id: int, email: str, expires_in_days: int = 7) -> "ClinicInvitation":
        """Generate and stage a cryptographically secure onboarding invitation."""
        token = secrets.token_urlsafe(32)
        now_utc = datetime.now(timezone.utc)
        expires_at = now_utc + timedelta(days=expires_in_days)
        invitation = cls(
            business_id=business_id,
            email=email.strip().lower(),
            token=token,
            expires_at=expires_at,
            is_used=False
        )
        db.session.add(invitation)
        return invitation

    def is_valid(self) -> bool:
        """Return True if the invitation exists, is unused, and has not expired."""
        if self.is_used:
            return False
        if not self.expires_at:
            return False
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        exp = self.expires_at.replace(tzinfo=None) if self.expires_at.tzinfo else self.expires_at
        return now_utc <= exp

    def mark_used(self) -> None:
        """Mark this invitation as consumed."""
        self.is_used = True
