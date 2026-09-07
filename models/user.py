from datetime import datetime, timezone
from werkzeug.security import generate_password_hash, check_password_hash
from models import db


class User(db.Model):
    """
    Per-clinic admin user account.

    Username is unique *per business* (not globally), so two different clinics
    can both have a user named "admin" without collision — the composite unique
    constraint (business_id, username) enforces this.

    is_platform_admin=True marks the SaaS platform owner's accounts; these
    accounts can access the /admin/platform/onboard-clinic page. Regular clinic
    admins (is_platform_admin=False) cannot.
    """

    __tablename__ = "users"
    __table_args__ = (
        db.UniqueConstraint("business_id", "username", name="uq_user_business_username"),
    )

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    username = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_platform_admin = db.Column(db.Boolean, nullable=False, default=False)
    reset_token = db.Column(db.String(128), nullable=True, index=True)
    reset_token_expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # Relationship back to business (for easy access to business.name on login)
    business = db.relationship("Business", backref=db.backref("users", lazy=True))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def set_password(self, plain_text: str) -> None:
        """Hash and store a plain-text password."""
        self.password_hash = generate_password_hash(plain_text)

    def check_password(self, plain_text: str) -> bool:
        """Return True if plain_text matches the stored hash."""
        return check_password_hash(self.password_hash, plain_text)

    def generate_reset_token(self, expires_in_hours: int = 1) -> str:
        """Generate a cryptographically secure reset token with expiration."""
        import secrets
        from datetime import timedelta
        token = secrets.token_urlsafe(32)
        self.reset_token = token
        self.reset_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
        return token

    def verify_reset_token(self, token: str) -> bool:
        """Verify that the provided token matches and has not expired."""
        if not self.reset_token or not token or self.reset_token != token:
            return False
        if not self.reset_token_expires_at:
            return False
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        exp = self.reset_token_expires_at.replace(tzinfo=None) if self.reset_token_expires_at.tzinfo else self.reset_token_expires_at
        return now_utc <= exp

    def clear_reset_token(self) -> None:
        """Clear reset token once used or invalidated."""
        self.reset_token = None
        self.reset_token_expires_at = None

    def __repr__(self) -> str:
        return f"<User id={self.id} business_id={self.business_id} username={self.username!r}>"
