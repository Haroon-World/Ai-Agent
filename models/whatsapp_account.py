from datetime import datetime, timezone
from models import db


class ClinicWhatsAppAccount(db.Model):
    """
    Multi-tenant WhatsApp Cloud API configuration model.
    Maps a Meta Phone Number ID to a specific Business/Clinic tenant.

    Architecture:
    Clinic A -> WhatsApp Phone Number A (phone_number_id_A) -> business_id A
    Clinic B -> WhatsApp Phone Number B (phone_number_id_B) -> business_id B
    """

    __tablename__ = "whatsapp_accounts"

    id = db.Column(db.Integer, primary_key=True)
    business_id = db.Column(
        db.Integer,
        db.ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True
    )
    # Meta Graph API Phone Number ID (unique identifier received in webhook entry[0].changes[0].value.metadata.phone_number_id)
    phone_number_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    # WhatsApp Business Account ID (WABA)
    waba_id = db.Column(db.String(64), nullable=True, index=True)
    # Clean display phone number, e.g. "+923001234567"
    display_phone_number = db.Column(db.String(32), nullable=False)
    # Meta Graph API System User Access Token (or encrypted token reference)
    access_token = db.Column(db.String(512), nullable=True)
    # Webhook verification secret token for Meta challenge verification
    webhook_verify_token = db.Column(db.String(128), nullable=True)
    # Webhook signature validation app secret (optional per-tenant or shared)
    app_secret = db.Column(db.String(128), nullable=True)
    # Channel active toggle
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationship back to Business
    business = db.relationship("Business", backref=db.backref("whatsapp_account", uselist=False, cascade="all, delete-orphan"))

    def to_dict(self, include_sensitive: bool = False):
        """Serialize configuration. By default, hides access tokens and secrets."""
        data = {
            "id": self.id,
            "business_id": self.business_id,
            "business_name": self.business.name if self.business else None,
            "phone_number_id": self.phone_number_id,
            "waba_id": self.waba_id,
            "display_phone_number": self.display_phone_number,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_sensitive:
            data["access_token"] = self.access_token
            data["webhook_verify_token"] = self.webhook_verify_token
        return data

    def __repr__(self):
        return f"<ClinicWhatsAppAccount id={self.id} business_id={self.business_id} phone_number_id={self.phone_number_id!r}>"
