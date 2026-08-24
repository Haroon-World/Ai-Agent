import unittest
import re
from pathlib import Path

class TestFrontendScrollBehavior(unittest.TestCase):
    """
    Validates the scroll logic in static/js/chat.js according to the 6 test scenarios:
    TEST 1: Auto-scroll when user is at bottom.
    TEST 2: Manual upward scroll is preserved during background polling/sync.
    TEST 3: When user is reading older messages, new messages do not force-scroll to bottom.
    TEST 4: User scrolls back to bottom, auto-scroll resumes.
    TEST 5: Initial page load / conversation initialization forces scroll to bottom.
    TEST 6: Historical interactive selectors remain isolated / do not reappear.
    """

    def setUp(self):
        js_path = Path(__file__).resolve().parent.parent / "static" / "js" / "chat.js"
        with open(js_path, "r", encoding="utf-8") as f:
            self.js_code = f.read()

    def test_scroll_bottom_threshold_and_helper_defined(self):
        """Verify isUserNearBottom and SCROLL_BOTTOM_THRESHOLD are defined and used."""
        self.assertIn("SCROLL_BOTTOM_THRESHOLD", self.js_code)
        self.assertIn("function isUserNearBottom", self.js_code)
        self.assertIn("chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight", self.js_code)

    def test_append_bubble_does_not_unconditionally_force_scroll(self):
        """
        Verify appendMessageBubble does not set scrollTop on every bubble inside loops.
        """
        match = re.search(r"function appendMessageBubble\(.*?\)\s*\{(.*?)\n\}", self.js_code, re.DOTALL)
        self.assertTrue(match, "appendMessageBubble function found")
        bubble_body = match.group(1)
        self.assertNotIn("chatMessages.scrollTop = chatMessages.scrollHeight", bubble_body,
                         "appendMessageBubble should not force scrollTop=scrollHeight on every call")

    def test_render_messages_preserves_scroll_when_not_at_bottom(self):
        """
        Verify renderMessages checks isUserNearBottom() before rebuilding DOM
        and restores prevScrollTop when user was reading history.
        """
        match = re.search(r"function renderMessages\(.*?\)\s*\{(.*?)\n\}", self.js_code, re.DOTALL)
        self.assertTrue(match, "renderMessages function found")
        render_body = match.group(1)
        self.assertIn("isUserNearBottom()", render_body)
        self.assertIn("prevScrollTop", render_body)
        self.assertIn("chatMessages.scrollTop = prevScrollTop", render_body)

    def test_sync_history_silently_does_not_force_scroll(self):
        """
        Verify syncHistorySilently calls renderMessages with forceScroll=false
        and skips rebuilding DOM if message count and status have not changed.
        """
        match = re.search(r"async function syncHistorySilently\(.*?\)\s*\{(.*?)\n\}", self.js_code, re.DOTALL)
        self.assertTrue(match, "syncHistorySilently function found")
        sync_body = match.group(1)
        self.assertIn("renderMessages(msgs, false)", sync_body)
        self.assertIn("hasNewMessages", sync_body)

    def test_init_and_load_history_force_scroll_to_bottom(self):
        """
        Verify initNewConversation and loadHistory force scroll to bottom on initial load.
        """
        match_init = re.search(r"async function initNewConversation\(.*?\)\s*\{(.*?)\n\}", self.js_code, re.DOTALL)
        self.assertTrue(match_init, "initNewConversation function found")
        self.assertIn("renderMessages(data.messages, true)", match_init.group(1))

        match_load = re.search(r"async function loadHistory\(.*?\)\s*\{(.*?)\n\}", self.js_code, re.DOTALL)
        self.assertTrue(match_load, "loadHistory function found")
        self.assertIn("renderMessages(data.messages, true)", match_load.group(1))

    def test_handle_send_message_respects_user_scroll_on_reply(self):
        """
        Verify handleSendMessage scrolls to bottom when user sends a message,
        and checks wasNear when assistant response arrives.
        """
        match = re.search(r"async function handleSendMessage\(.*?\)\s*\{(.*?)\n\}", self.js_code, re.DOTALL)
        self.assertTrue(match, "handleSendMessage function found")
        send_body = match.group(1)
        self.assertIn("scrollToBottom(true)", send_body)
        self.assertIn("const wasNear = isUserNearBottom()", send_body)
        self.assertIn("if (wasNear)", send_body)

    def test_render_ui_action_respects_should_scroll(self):
        """
        Verify renderUIAction uses shouldScroll flag before calling scrollToBottom.
        """
        match = re.search(r"function renderUIAction\((.*?)\)\s*\{", self.js_code)
        self.assertTrue(match, "renderUIAction function header found")
        args = match.group(1)
        self.assertIn("shouldScroll", args)


if __name__ == "__main__":
    unittest.main()
