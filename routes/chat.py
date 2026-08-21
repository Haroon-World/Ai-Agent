from flask import Blueprint, render_template, request, jsonify, session
from config.config import Config
from models import db, Business, Conversation, Message, Customer
from ai.agent import Agent
from typing import Tuple

chat_bp = Blueprint("chat_bp", __name__)


def get_or_create_conversation(
    business_id: int,
    conversation_id: int = None
) -> Tuple[Conversation, bool]:
    """
    Retrieve an existing conversation or create a fresh one.
    Returns (conversation, is_new_session) — is_new_session is True when a new
    conversation was created (either because no conversation_id was given or the
    supplied conversation_id was not found/not belonging to this business).
    The caller can surface this flag to the frontend so users see a clear notice
    when their previous session has expired, rather than a silent reset.
    """
    if conversation_id:
        conv = Conversation.query.filter_by(id=conversation_id, business_id=business_id).first()
        if conv:
            return conv, False
        # Conversation not found or belongs to a different business — signal the reset

    # Create new conversation
    conv = Conversation(
        business_id=business_id,
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
            "Hello! 👋 Welcome to SmileCare Dental Clinic. "
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
    business = db.session.get(Business, Config.DEFAULT_BUSINESS_ID)
    return render_template("chat.html", business=business)


@chat_bp.route("/api/chat/init", methods=["POST"])
def init_chat():
    business_id = Config.DEFAULT_BUSINESS_ID
    conv, is_new = get_or_create_conversation(business_id)
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
    business_id = Config.DEFAULT_BUSINESS_ID
    conv = Conversation.query.filter_by(id=conversation_id, business_id=business_id).first()
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
    data = request.get_json() or {}
    message_text = data.get("message", "").strip()
    conversation_id = data.get("conversation_id")
    business_id = Config.DEFAULT_BUSINESS_ID

    if not message_text:
        return jsonify({"success": False, "error": "Message text is required"}), 400

    conv, is_new = get_or_create_conversation(business_id, conversation_id)
    agent = Agent(business_id=business_id)
    result = agent.process_message(conversation_id=conv.id, user_content=message_text)

    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "status": result.get("status"),
        "workflow_state": result.get("workflow_state"),
        "reply": result.get("content"),
        "executed_tools": result.get("executed_tools", []),
        # Surface session_reset so the frontend can show a notice when the previous
        # session was not found (e.g. after a server restart or DB wipe).
        "session_reset": is_new
    })


@chat_bp.route("/api/chat/reset", methods=["POST"])
def reset_chat():
    business_id = Config.DEFAULT_BUSINESS_ID
    conv, _ = get_or_create_conversation(business_id)
    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "status": conv.status,
        "messages": [m.to_dict() for m in conv.messages]
    })
