import unittest
import json
from unittest.mock import patch
from app import create_app
from config.config import Config
from models import db, Business, Conversation, Customer
from models.whatsapp_account import ClinicWhatsAppAccount
from seed import seed_database


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

        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "993720013281872",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "+15550293847",
                                    "phone_number_id": "1313879111808444"
                                },
                                "contacts": [
                                    {
                                        "profile": {
                                            "name": "Haroon Rasheed"
                                        },
                                        "wa_id": "923187538771"
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "923187538771",
                                        "id": "wamid.TEST_001",
                                        "timestamp": "1710000000",
                                        "text": {
                                            "body": "Hello, I want to book an appointment with Dr. Sara"
                                        },
                                        "type": "text"
                                    }
                                ]
                            },
                            "field": "messages"
                        }
                    ]
                }
            ]
        }

        resp = self.client.post(
            "/api/whatsapp/webhook",
            data=json.dumps(payload),
            content_type="application/json"
        )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")

        # Verify customer created/found
        cust = Customer.query.filter_by(phone="923187538771").first()
        self.assertIsNotNone(cust)
        self.assertEqual(cust.name, "Haroon Rasheed")

        # Verify conversation created with channel='whatsapp'
        conv = Conversation.query.filter_by(customer_id=cust.id, channel="whatsapp").first()
        self.assertIsNotNone(conv)
        self.assertEqual(conv.business_id, 1)

        # Verify outbound WhatsApp message dispatched
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(kwargs.get("to_phone") or args[0], "923187538771")

    def test_webhook_post_delivery_receipt_ignored(self):
        """Status receipts (delivered, read) return 200 without error."""
        status_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "993720013281872",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "phone_number_id": "1313879111808444"
                                },
                                "statuses": [
                                    {
                                        "id": "wamid.TEST_001",
                                        "status": "delivered",
                                        "timestamp": "1710000005",
                                        "recipient_id": "923187538771"
                                    }
                                ]
                            },
                            "field": "messages"
                        }
                    ]
                }
            ]
        }
        resp = self.client.post(
            "/api/whatsapp/webhook",
            data=json.dumps(status_payload),
            content_type="application/json"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "ignored")


if __name__ == "__main__":
    unittest.main()
