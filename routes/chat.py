import uuid
from flask import Blueprint, render_template, request, jsonify, session
from config.config import Config
from models import db, Business, Conversation, Message, Customer
from ai.agent import Agent
from ai.speech_client import STTClient
from typing import Tuple

chat_bp = Blueprint("chat_bp", __name__)


def _get_or_set_visitor_id() -> str:
    """Ensure a signed, server-side visitor_id exists in the current session."""
    if "visitor_id" not in session or not session["visitor_id"]:
        session["visitor_id"] = str(uuid.uuid4())
    return session["visitor_id"]


def get_or_create_conversation(
    business_id: int,
    conversation_id: int = None,
    visitor_id: str = None
) -> Tuple[Conversation, bool]:
    """
    Retrieve an existing conversation owned by this visitor or create a fresh one.
    Enforces visitor session ownership to prevent IDOR attacks.
    Returns (conversation, is_new_session) — is_new_session is True when a new
    conversation was created.
    """
    if conversation_id:
        query = Conversation.query.filter_by(id=conversation_id, business_id=business_id)
        if visitor_id:
            query = query.filter_by(visitor_id=visitor_id)
        conv = query.first()
        if conv:
            return conv, False

    # Create new conversation bound to this visitor
    conv = Conversation(
        business_id=business_id,
        visitor_id=visitor_id,
        channel="web_chat",
        status="AI",
        intent="UNKNOWN",
        workflow_state="START"
    )
    db.session.add(conv)
    db.session.commit()

    # Initial welcome message
    welcome_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=(
            "Hello! Welcome to SmileCare Dental Clinic. "
            "I am your AI receptionist. How can I help you today? "
            "You can ask about our dental services, doctor schedules, "
            "or book/reschedule an appointment."
        )
    )
    db.session.add(welcome_msg)
    db.session.commit()
    return conv, True


@chat_bp.route("/chat")
def chat_view():
    _get_or_set_visitor_id()
    business = db.session.get(Business, Config.DEFAULT_BUSINESS_ID)
    return render_template("chat.html", business=business)


@chat_bp.route("/api/chat/init", methods=["POST"])
def init_chat():
    visitor_id = _get_or_set_visitor_id()
    business_id = Config.DEFAULT_BUSINESS_ID
    conv, is_new = get_or_create_conversation(business_id, visitor_id=visitor_id)
    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "status": conv.status,
        "workflow_state": conv.workflow_state,
        "session_reset": is_new,
        "messages": [m.to_dict() for m in conv.messages]
    })


@chat_bp.route("/api/chat/history/<int:conversation_id>", methods=["GET"])
def get_history(conversation_id):
    visitor_id = _get_or_set_visitor_id()
    business_id = Config.DEFAULT_BUSINESS_ID
    conv = Conversation.query.filter_by(id=conversation_id, business_id=business_id, visitor_id=visitor_id).first()
    if not conv:
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    # Only return user and assistant messages for customer chat display
    visible_messages = [m.to_dict() for m in conv.messages if m.role in ["user", "assistant"]]

    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "status": conv.status,
        "workflow_state": conv.workflow_state,
        "handoff_reason": conv.handoff_reason,
        "messages": visible_messages
    })


@chat_bp.route("/api/chat/send", methods=["POST"])
def send_message():
    visitor_id = _get_or_set_visitor_id()
    data = request.get_json() or {}
    message_text = data.get("message", "").strip()
    conversation_id = data.get("conversation_id")
    business_id = Config.DEFAULT_BUSINESS_ID

    if not message_text:
        return jsonify({"success": False, "error": "Message text is required"}), 400

    conv, is_new = get_or_create_conversation(business_id, conversation_id, visitor_id=visitor_id)
    agent = Agent(business_id=business_id)
    result = agent.process_message(conversation_id=conv.id, user_content=message_text)

    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "status": result.get("status"),
        "workflow_state": result.get("workflow_state"),
        "reply": result.get("content"),
        "executed_tools": result.get("executed_tools", []),
        "ui_action": result.get("ui_action"),
        "session_reset": is_new
    })


@chat_bp.route("/api/chat/reset", methods=["POST"])
def reset_chat():
    visitor_id = _get_or_set_visitor_id()
    business_id = Config.DEFAULT_BUSINESS_ID
    conv, _ = get_or_create_conversation(business_id, visitor_id=visitor_id)
    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "status": conv.status,
        "messages": [m.to_dict() for m in conv.messages]
    })


@chat_bp.route("/api/chat/send-voice", methods=["POST"])
def send_voice():
    visitor_id = _get_or_set_visitor_id()
    business_id = Config.DEFAULT_BUSINESS_ID

    audio_file = request.files.get("file") or request.files.get("audio")
    conversation_id_str = request.form.get("conversation_id")
    conversation_id = int(conversation_id_str) if conversation_id_str and conversation_id_str.isdigit() else None

    if not audio_file:
        return jsonify({"success": False, "error": "Audio file is required"}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes or len(audio_bytes) == 0:
        return jsonify({"success": False, "error": "Empty audio file received"}), 400

    mime_type = audio_file.mimetype or "audio/webm"

    try:
        stt_client = STTClient()
        transcript = stt_client.transcribe(audio_bytes, mime_type=mime_type)
    except Exception as e:
        return jsonify({"success": False, "error": f"Speech transcription failed: {str(e)}"}), 400

    if not transcript or not transcript.strip():
        return jsonify({"success": False, "error": "Could not transcribe audio content"}), 400

    conv, is_new = get_or_create_conversation(business_id, conversation_id, visitor_id=visitor_id)
    agent = Agent(business_id=business_id)
    result = agent.process_message(conversation_id=conv.id, user_content=transcript)

    user_msg = Message.query.filter_by(conversation_id=conv.id, role="user").order_by(Message.created_at.desc()).first()
    if user_msg:
        user_msg.input_mode = "voice"
        db.session.commit()

    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "status": result.get("status"),
        "workflow_state": result.get("workflow_state"),
        "transcript": transcript,
        "reply": result.get("content"),
        "input_mode": "voice",
        "executed_tools": result.get("executed_tools", []),
        "ui_action": result.get("ui_action"),
        "session_reset": is_new
    })
