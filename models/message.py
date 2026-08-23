import json
from datetime import datetime, timezone
from models import db

class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)  # user, assistant, system, tool
    content = db.Column(db.Text, nullable=False)
    # Stored on tool-role messages so that Groq/OpenAI-protocol adapters can echo the
    # correct tool_call_id when replaying history in subsequent LLM turns.
    tool_name = db.Column(db.String(100), nullable=True)
    tool_call_id = db.Column(db.String(100), nullable=True)
    input_mode = db.Column(db.String(20), nullable=True, default="text") # text, voice
    interactive_data = db.Column(db.Text, nullable=True) # WhatsApp-compatible structured UI options
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    def to_dict(self):
        parsed_interactive = None
        if self.interactive_data:
            try:
                parsed_interactive = json.loads(self.interactive_data)
            except Exception:
                parsed_interactive = None

        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "input_mode": self.input_mode or "text",
            "interactive_data": parsed_interactive,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
