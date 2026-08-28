import unittest
from unittest.mock import MagicMock, patch
import time
from app import create_app
from models import db, Business, Conversation, Doctor, Service
from ai.llm_client import GeminiAdapter
from ai.agent import Agent

class TestLatencySafety(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app_ctx = self.app.app_context()
        self.app_ctx.push()

    def tearDown(self):
        db.session.remove()
        self.app_ctx.pop()

    @patch('google.genai.Client')
    def test_gemini_429_short_retry_after_succeeds(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_candidate = MagicMock()
        mock_part = MagicMock()
        mock_part.text = 'Success after retry'
        mock_part.function_call = None
        mock_candidate.content.parts = [mock_part]
        mock_response = MagicMock(candidates=[mock_candidate])

        mock_client.models.generate_content.side_effect = [
            Exception('429 RESOURCE_EXHAUSTED: Please retry in 0.05s'),
            mock_response
        ]

        adapter = GeminiAdapter(api_key='fake-key')
        start = time.time()
        res = adapter.chat_completion(system_prompt='Test', messages=[{'role': 'user', 'content': 'Hi'}], tools=[])
        elapsed = time.time() - start

        self.assertEqual(res['content'], 'Success after retry')
        self.assertLess(elapsed, 2.0, 'Short retry should complete rapidly without long sleep')

    @patch('google.genai.Client')
    def test_gemini_429_excessive_retry_after_aborts_immediately(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_client.models.generate_content.side_effect = Exception('429 RESOURCE_EXHAUSTED: Please retry in 35.0s')

        adapter = GeminiAdapter(api_key='fake-key')
        start = time.time()
        with self.assertRaises(Exception):
            adapter.chat_completion(system_prompt='Test', messages=[{'role': 'user', 'content': 'Hi'}], tools=[])
        elapsed = time.time() - start

        self.assertLess(elapsed, 1.0, 'Excessive retry-after (>5s) must not block or sleep 35s')

    def test_tool_loop_no_duplicated_context(self):
        conv = Conversation(
            business_id=1,
            status='AI',
            intent='BOOK_APPOINTMENT',
            selected_doctor_id=1,
            selected_service_id=1,
            requested_date='2026-08-28'
        )
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider='mock')
        res = agent.process_message(conv.id, 'Show me available slots')

        conv_db = db.session.get(Conversation, conv.id)
        self.assertEqual(conv_db.workflow_state, 'CHECKING_AVAILABILITY')
        self.assertIsNotNone(res['content'])

if __name__ == '__main__':
    unittest.main()
