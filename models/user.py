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

    def __repr__(self) -> str:
        return f"<User id={self.id} business_id={self.business_id} username={self.username!r}>"
