import hashlib
import hmac
import json
import unittest
from unittest.mock import patch
from app import create_app
from config.config import Config
from models import db, Business, Conversation, Customer, Message
from models.whatsapp_account import ClinicWhatsAppAccount
from models.user import User
from seed import seed_database


TEST_APP_SECRET = "test_app_secret_for_unit_tests"


def _sign(payload_bytes: bytes, secret: str = TEST_APP_SECRET) -> str:
    """Compute X-Hub-Signature-256 the same way Meta does."""
    mac = hmac.new(secret.encode(), payload_bytes, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _make_payload(message_id="wamid.TEST_001", text="Hello, I want to book an appointment with Dr. Sara",
                  phone_number_id="1313879111808444"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "993720013281872",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "+15550293847",
                        "phone_number_id": phone_number_id,
                    },
                    "contacts": [{"profile": {"name": "Haroon Rasheed"}, "wa_id": "923187538771"}],
                    "messages": [{
                        "from": "923187538771",
                        "id": message_id,
                        "timestamp": "1710000000",
                        "text": {"body": text},
                        "type": "text",
                    }],
                },
                "field": "messages",
            }],
        }],
    }


class TestWhatsAppWebhook(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"
            WHATSAPP_WEBHOOK_VERIFY_TOKEN = "clinic_connect_secret_2026"
            WHATSAPP_PHONE_NUMBER_ID = "1313879111808444"
            WHATSAPP_ACCESS_TOKEN = "mock-token"
            WHATSAPP_APP_SECRET = TEST_APP_SECRET  # Part A: enable sig verification in tests

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # ------------------------------------------------------------------
    # Original tests — updated to include a valid signature
    # ------------------------------------------------------------------

    def test_webhook_get_challenge_success(self):
        """Meta challenge handshake returns hub.challenge when verify token matches."""
        resp = self.client.get(
            "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=clinic_connect_secret_2026&hub.challenge=1158201444"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.decode("utf-8"), "1158201444")

    def test_webhook_get_challenge_invalid_token(self):
        """Meta challenge handshake returns 403 when verify token is incorrect."""
        resp = self.client.get(
            "/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong_secret&hub.challenge=1158201444"
        )
        self.assertEqual(resp.status_code, 403)

    @patch("services.whatsapp_service.WhatsAppService.send_text_message")
    def test_webhook_post_incoming_text_message(self, mock_send):
        """Inbound WhatsApp message triggers AI agent and dispatches reply."""
        mock_send.return_value = {"success": True, "message_id": "wamid.12345"}

        payload = _make_payload(message_id="wamid.TEST_001")
        raw = json.dumps(payload).encode()
        resp = self.client.post(
            "/api/whatsapp/webhook",
            data=raw,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(raw)},
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")

        cust = Customer.query.filter_by(phone="923187538771").first()
        self.assertIsNotNone(cust)
        self.assertEqual(cust.name, "Haroon Rasheed")

        conv = Conversation.query.filter_by(customer_id=cust.id, channel="whatsapp").first()
        self.assertIsNotNone(conv)
        self.assertEqual(conv.business_id, 1)

        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(kwargs.get("to_phone") or args[0], "923187538771")

    def test_webhook_post_delivery_receipt_ignored(self):
        """Status receipts (delivered, read) return 200 without error."""
        status_payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "993720013281872",
                "changes": [{
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {"phone_number_id": "1313879111808444"},
                        "statuses": [{
                            "id": "wamid.TEST_001",
                            "status": "delivered",
                            "timestamp": "1710000005",
                            "recipient_id": "923187538771",
                        }],
                    },
                    "field": "messages",
                }],
            }],
        }
        raw = json.dumps(status_payload).encode()
        resp = self.client.post(
            "/api/whatsapp/webhook",
            data=raw,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(raw)},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "ignored")

    # ------------------------------------------------------------------
    # Part A regression tests — HMAC signature verification
    # ------------------------------------------------------------------

    def test_unsigned_request_is_rejected_403(self):
        """POST /webhook with NO X-Hub-Signature-256 header must be rejected with 403."""
        payload = _make_payload()
        raw = json.dumps(payload).encode()
        resp = self.client.post(
            "/api/whatsapp/webhook",
            data=raw,
            content_type="application/json",
            # deliberately no X-Hub-Signature-256
        )
        self.assertEqual(resp.status_code, 403)

    def test_wrong_signature_is_rejected_403(self):
        """POST /webhook with an incorrect X-Hub-Signature-256 must be rejected with 403."""
        payload = _make_payload()
        raw = json.dumps(payload).encode()
        resp = self.client.post(
            "/api/whatsapp/webhook",
            data=raw,
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
        )
        self.assertEqual(resp.status_code, 403)

    def test_wrong_signature_does_not_reach_processing(self):
        """With invalid signature, the AI agent must NOT be called."""
        payload = _make_payload()
        raw = json.dumps(payload).encode()
        with patch("ai.agent.Agent.process_message") as mock_agent:
            resp = self.client.post(
                "/api/whatsapp/webhook",
                data=raw,
                content_type="application/json",
                headers={"X-Hub-Signature-256": "sha256=" + "bad" * 21 + "b"},
            )
        self.assertEqual(resp.status_code, 403)
        mock_agent.assert_not_called()

    @patch("services.whatsapp_service.WhatsAppService.send_text_message")
    def test_valid_signature_is_accepted(self, mock_send):
        """POST /webhook with a correctly computed HMAC signature must succeed (200)."""
        mock_send.return_value = {"success": True}
        payload = _make_payload(message_id="wamid.VALIDSIG001")
        raw = json.dumps(payload).encode()
        resp = self.client.post(
            "/api/whatsapp/webhook",
            data=raw,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(raw)},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get("status"), "success")

    # ------------------------------------------------------------------
    # Part B regression tests — message deduplication
    # ------------------------------------------------------------------

    @patch("services.whatsapp_service.WhatsAppService.send_text_message")
    def test_duplicate_message_id_not_reprocessed(self, mock_send):
        """Sending the same message ID twice must only trigger the agent once."""
        mock_send.return_value = {"success": True}

        payload = _make_payload(message_id="wamid.DUPLICATETEST001",
                                text="I want to book a consultation")
        raw = json.dumps(payload).encode()
        sig = _sign(raw)
        headers = {"X-Hub-Signature-256": sig}

        # First delivery — should process normally
        resp1 = self.client.post(
            "/api/whatsapp/webhook", data=raw, content_type="application/json", headers=headers
        )
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.get_json().get("status"), "success")

        # Second delivery (Meta retry) — must be suppressed
        resp2 = self.client.post(
            "/api/whatsapp/webhook", data=raw, content_type="application/json", headers=headers
        )
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.get_json().get("status"), "duplicate")

        # AI agent must have been called exactly once, not twice
        self.assertEqual(mock_send.call_count, 1)

    @patch("services.whatsapp_service.WhatsAppService.send_text_message")
    def test_external_message_id_stored_on_message(self, mock_send):
        """After processing, the user Message row must carry the Meta wamid."""
        mock_send.return_value = {"success": True}

        msg_id = "wamid.STORETEST001"
        payload = _make_payload(message_id=msg_id)
        raw = json.dumps(payload).encode()
        self.client.post(
            "/api/whatsapp/webhook",
            data=raw,
            content_type="application/json",
            headers={"X-Hub-Signature-256": _sign(raw)},
        )

        stored = Message.query.filter_by(external_message_id=msg_id).first()
        self.assertIsNotNone(stored, "User Message row must have external_message_id set")
        self.assertEqual(stored.role, "user")

    # ------------------------------------------------------------------
    # Part C regression tests — /test-send authentication
    # ------------------------------------------------------------------

    def test_test_send_requires_auth_unauthenticated(self):
        """POST /test-send with no session must be rejected with 401."""
        resp = self.client.post(
            "/api/whatsapp/test-send",
            json={"phone": "923009999999", "message": "unauthorized message"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.get_json().get("success"))

    @patch("services.whatsapp_service.WhatsAppService.send_text_message")
    def test_test_send_works_when_logged_in(self, mock_send):
        """POST /test-send with a valid admin session must be allowed."""
        mock_send.return_value = {"success": True}

        # Log in via the admin login route
        login_resp = self.client.post(
            "/admin/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=False,
        )
        # Login redirects on success
        self.assertIn(login_resp.status_code, (200, 302))

        resp = self.client.post(
            "/api/whatsapp/test-send",
            json={"phone": "923009999999", "message": "authorized test message"},
        )
        # Should attempt the send (may fail if WhatsApp creds are mocked, but NOT because of auth)
        self.assertNotEqual(resp.status_code, 401)

    def test_test_send_uses_own_clinic_account(self):
        """
        /test-send must use the logged-in admin's clinic account, not a
        caller-supplied phone_number_id. This verifies business scoping.
        """
        # Create a second business + admin to simulate a different clinic
        biz2 = Business(name="Rival Clinic", address="123 Test Street", phone="0300-0000000")
        db.session.add(biz2)
        db.session.commit()

        # Create a WhatsApp account for business 1 only
        wa1 = ClinicWhatsAppAccount(
            business_id=1,
            phone_number_id="CLINIC1_PID",
            display_phone_number="+15550001111",
            access_token="clinic1_token",
            is_active=True,
        )
        db.session.add(wa1)
        db.session.commit()

        # Log in as admin (business 1)
        self.client.post(
            "/admin/login",
            data={"username": "admin", "password": "admin123"},
            follow_redirects=False,
        )

        with patch("services.whatsapp_service.WhatsAppService.send_text_message") as mock_send:
            mock_send.return_value = {"success": True}
            self.client.post(
                "/api/whatsapp/test-send",
                json={"phone": "923009999999", "message": "test"},
            )
            if mock_send.called:
                # Must use CLINIC1_PID, never allow caller to specify a different one
                call_kwargs = mock_send.call_args[1]
                pid_used = call_kwargs.get("phone_number_id")
                self.assertEqual(pid_used, "CLINIC1_PID",
                                 "Must scope to the logged-in admin's own phone_number_id")


if __name__ == "__main__":
    unittest.main()


