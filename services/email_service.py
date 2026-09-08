import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

logger = logging.getLogger("email_service")


class EmailService:
    """
    Centralized email delivery service for password resets, subscription notices,
    and platform administrative alerts.
    """

    @classmethod
    def is_configured(cls) -> bool:
        """Check if SMTP credentials are provided in the environment."""
        return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD"))

    @classmethod
    def send_email(
        cls,
        to_email: str,
        subject: str,
        text_content: str,
        html_content: Optional[str] = None,
    ) -> bool:
        """
        Send an email via SMTP. If SMTP is not configured, logs the email content
        to the server console to allow development and testing without SMTP credentials.
        """
        if not to_email or "@" not in to_email:
            logger.warning(f"[EmailService] Invalid recipient email: {to_email}")
            return False

        smtp_host = os.getenv("SMTP_HOST", "").strip()
        smtp_port = int(os.getenv("SMTP_PORT", "587"))
        smtp_user = os.getenv("SMTP_USER", "").strip()
        smtp_password = os.getenv("SMTP_PASSWORD", "").replace(" ", "").strip()
        sender_email = os.getenv("MAIL_DEFAULT_SENDER", smtp_user or "noreply@clinicconnectai.com").strip()
        use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")

        # Fallback for local development/testing without live SMTP credentials
        if not (smtp_host and smtp_user and smtp_password):
            print(f"\n=======================================================")
            print(f"[EmailService DEV/MOCK DISPATCH]")
            print(f"TO:      {to_email}")
            print(f"FROM:    {sender_email}")
            print(f"SUBJECT: {subject}")
            print(f"BODY:\n{text_content}")
            print(f"=======================================================\n")
            logger.info(f"[EmailService Mock] Dispatched email to {to_email} with subject '{subject}'")
            return True

        # Production SMTP dispatch
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender_email
            msg["To"] = to_email

            part1 = MIMEText(text_content, "plain")
            msg.attach(part1)

            if html_content:
                part2 = MIMEText(html_content, "html")
                msg.attach(part2)

            with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
                if use_tls:
                    server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(sender_email, [to_email], msg.as_string())

            logger.info(f"[EmailService] Successfully sent email to {to_email}")
            return True
        except Exception as e:
            logger.error(f"[EmailService] Failed to send email to {to_email}: {e}")
            return False

    @classmethod
    def send_password_reset_email(cls, to_email: str, reset_url: str, username: Optional[str] = None) -> bool:
        """Send password reset instructions with secure time-limited token link."""
        user_display = username or "Clinic Administrator"
        subject = "ClinicConnectAI - Password Reset Request"
        text_content = f"""Hello {user_display},

We received a request to reset the password for your ClinicConnectAI administrator account.

Please click the secure link below to set a new password:
{reset_url}

This link is valid for 1 hour. If you did not request this password reset, please ignore this email or contact support.

Best regards,
ClinicConnectAI Team
"""
        html_content = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
  .card {{ max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; padding: 32px; }}
  .btn {{ display: inline-block; background-color: #4f46e5; color: #ffffff !important; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: 600; margin: 20px 0; }}
  .footer {{ font-size: 0.8rem; color: #64748b; margin-top: 24px; border-top: 1px solid #e2e8f0; padding-top: 16px; }}
</style>
</head>
<body>
<div class="card">
  <h2>🔑 Password Reset Request</h2>
  <p>Hello <strong>{user_display}</strong>,</p>
  <p>We received a request to reset the password for your ClinicConnectAI administrator account.</p>
  <p><a href="{reset_url}" class="btn">Reset My Password</a></p>
  <p style="font-size: 0.88rem; color: #475569;">Or copy and paste this URL into your browser:<br>
  <a href="{reset_url}" style="color: #4f46e5; word-break: break-all;">{reset_url}</a></p>
  <p style="font-size: 0.85rem; color: #dc2626;">⏱️ This link will expire in 1 hour.</p>
  <div class="footer">
    If you did not request a password reset, you can safely ignore this email.<br>
    ClinicConnectAI Platform
  </div>
</div>
</body>
</html>
"""
        return cls.send_email(to_email, subject, text_content, html_content)

    @classmethod
    def send_subscription_approved_email(
        cls,
        to_email: str,
        clinic_name: str,
        plan_name: str,
        expires_at: Optional[str] = None
    ) -> bool:
        """Send confirmation email when a B2B subscription request is approved by platform admins."""
        subject = f"ClinicConnectAI - Subscription Approved for {clinic_name}"
        expiry_info = f"Valid until: {expires_at}" if expires_at else "Active license provisioned."
        text_content = f"""Hello,

Great news! Your subscription renewal for '{clinic_name}' has been reviewed and approved by the platform onboarding team.

Plan: {plan_name}
Status: Active Access Granted
{expiry_info}

You can access your clinic dashboard immediately to manage doctors, appointments, and AI services.

Best regards,
ClinicConnectAI SaaS Platform Team
"""
        html_content = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
  .card {{ max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; padding: 32px; }}
  .badge {{ display: inline-block; background: #dcfce7; color: #15803d; padding: 4px 12px; border-radius: 6px; font-weight: bold; font-size: 0.85rem; }}
  .footer {{ font-size: 0.8rem; color: #64748b; margin-top: 24px; border-top: 1px solid #e2e8f0; padding-top: 16px; }}
</style>
</head>
<body>
<div class="card">
  <h2>🎉 Subscription Approved!</h2>
  <p>Hello <strong>{clinic_name}</strong>,</p>
  <p>Your subscription request has been approved by the platform onboarding team.</p>
  <div style="background: #f1f5f9; padding: 16px; border-radius: 8px; margin: 16px 0;">
    <div><strong>Plan:</strong> {plan_name}</div>
    <div style="margin-top: 6px;"><strong>Status:</strong> <span class="badge">Active</span></div>
    {f'<div style="margin-top: 6px;"><strong>Expiry Date:</strong> {expires_at}</div>' if expires_at else ''}
  </div>
  <p>Your clinic dashboard is fully active.</p>
  <div class="footer">
    ClinicConnectAI Multi-Tenant Platform
  </div>
</div>
</body>
</html>
"""
        return cls.send_email(to_email, subject, text_content, html_content)
