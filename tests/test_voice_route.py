import io
import unittest
from app import create_app
from config.config import Config
from models import db, Conversation, Message
from seed import seed_database

class TestVoiceRoute(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"
            STT_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_send_voice_route_success(self):
        """
        Verify POST /api/chat/send-voice transcribes audio using MockSTTAdapter,
        creates a Message with input_mode='voice', processes through Agent,
        and returns success with transcript and AI reply.
        """
        dummy_audio = b"RIFF....WAVEfmt ....data...."
        data = {
            "file": (io.BytesIO(dummy_audio), "test_audio.webm", "audio/webm")
        }

        resp = self.client.post(
            "/api/chat/send-voice",
            data=data,
            content_type="multipart/form-data"
        )
        self.assertEqual(resp.status_code, 200)
        res = resp.get_json()

        self.assertTrue(res.get("success"))
        self.assertIn("transcript", res)
        self.assertIn("reply", res)
        self.assertEqual(res.get("input_mode"), "voice")

        conv_id = res["conversation_id"]
        conv = db.session.get(Conversation, conv_id)
        self.assertIsNotNone(conv)

        # Verify latest user message input_mode is 'voice'
        user_msg = Message.query.filter_by(conversation_id=conv.id, role="user").order_by(Message.created_at.desc()).first()
        self.assertIsNotNone(user_msg)
        self.assertEqual(user_msg.input_mode, "voice")

    def test_send_voice_route_empty_audio_rejected(self):
        """Verify empty audio upload returns status 400 error."""
        data = {
            "file": (io.BytesIO(b""), "empty.webm", "audio/webm")
        }
        resp = self.client.post(
            "/api/chat/send-voice",
            data=data,
            content_type="multipart/form-data"
        )
        self.assertEqual(resp.status_code, 400)
        res = resp.get_json()
        self.assertFalse(res.get("success"))
        self.assertIn("error", res)

if __name__ == "__main__":
    unittest.main()

    def test_send_voice_response_flags_mock_transcription(self):
        """
        The voice response must clearly flag when it's using the mock STT
        provider, so the frontend can warn the customer that the transcript
        does not reflect their real spoken content (mock mode always
        returns a fixed placeholder transcript regardless of the audio).
        """
        dummy_audio = b"RIFF....WAVEfmt ....data....fake audio content"
        data = {
            "file": (io.BytesIO(dummy_audio), "test_audio.webm", "audio/webm")
        }
        resp = self.client.post(
            "/api/chat/send-voice",
            data=data,
            content_type="multipart/form-data"
        )
        payload = resp.get_json()
        self.assertTrue(payload["success"])
        self.assertTrue(payload["mock_transcription"])
        self.assertEqual(payload["stt_provider"], "mock")


class TestSynthesizeRoute(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = "test-secret"
            LLM_PROVIDER = "mock"
            TTS_PROVIDER = "mock"

        self.app = create_app(TestConfig)
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_synthesize_returns_playable_audio(self):
        """POST /api/chat/synthesize must return a real audio/wav payload
        for valid text, using MockTTSAdapter in test/dev mode."""
        resp = self.client.post(
            "/api/chat/synthesize",
            json={"text": "Your appointment is confirmed for 10:00 AM."}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "audio/wav")
        self.assertTrue(len(resp.data) > 0)
        self.assertTrue(resp.data.startswith(b"RIFF"))

    def test_synthesize_rejects_empty_text(self):
        resp = self.client.post("/api/chat/synthesize", json={"text": ""})
        self.assertEqual(resp.status_code, 400)
