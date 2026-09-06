import uuid
from flask import Blueprint, render_template, request, jsonify, session, Response, current_app
from config.config import Config
from models import db, Business, Conversation, Message, Customer
from ai.agent import Agent
from ai.speech_client import STTClient, TTSClient
from typing import Tuple

chat_bp = Blueprint("chat_bp", __name__)


def _get_or_set_visitor_id() -> str:
    """Ensure a signed, server-side visitor_id exists in the current session."""
    if "visitor_id" not in session or not session["visitor_id"]:
        session["visitor_id"] = str(uuid.uuid4())
    return session["visitor_id"]


def _resolve_chat_business_id(clinic_id: int = None) -> int:
    """
    Dynamically resolve the target clinic for the customer chat session:
      1. Explicit route argument: /chat/<clinic_id>
      2. Query parameter: ?clinic=N or ?business_id=N
      3. JSON or form payload: {"business_id": N} or {"clinic_id": N}
      4. Logged-in clinic admin session: session.get("business_id")
      5. Fallback: Config.DEFAULT_BUSINESS_ID (1)
    """
    if clinic_id and isinstance(clinic_id, int):
        b = db.session.get(Business, clinic_id)
        if b:
            return b.id

    clinic_param = request.args.get("clinic") or request.args.get("business_id")
    if clinic_param and str(clinic_param).isdigit():
        b = db.session.get(Business, int(clinic_param))
        if b:
            return b.id

    if request.is_json:
        data = request.get_json(silent=True) or {}
        body_id = data.get("business_id") or data.get("clinic_id")
        if body_id:
            try:
                b = db.session.get(Business, int(body_id))
                if b:
                    return b.id
            except (ValueError, TypeError):
                pass
    elif request.form:
        form_id = request.form.get("business_id") or request.form.get("clinic_id")
        if form_id and str(form_id).isdigit():
            b = db.session.get(Business, int(form_id))
            if b:
                return b.id

    if session.get("business_id"):
        return session["business_id"]

    return Config.DEFAULT_BUSINESS_ID


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
    biz = db.session.get(Business, business_id)
    clinic_name = biz.name if biz else "ClinicConnect Polyclinic"
    welcome_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content=(
            f"Hello! Welcome to {clinic_name}. "
            "I am your AI receptionist. How can I help you today? "
            "You can ask about our doctors, services, schedules, "
            "or book/reschedule an appointment."
        )
    )
    db.session.add(welcome_msg)
    db.session.commit()
    return conv, True


@chat_bp.route("/chat")
@chat_bp.route("/chat/<int:clinic_id>")
def chat_view(clinic_id=None):
    _get_or_set_visitor_id()
    business_id = _resolve_chat_business_id(clinic_id)
    business = db.session.get(Business, business_id)
    if not business:
        business = db.session.get(Business, Config.DEFAULT_BUSINESS_ID)
    return render_template("chat.html", business=business)


@chat_bp.route("/api/chat/init", methods=["POST"])
def init_chat():
    visitor_id = _get_or_set_visitor_id()
    business_id = _resolve_chat_business_id()
    conv, is_new = get_or_create_conversation(business_id, visitor_id=visitor_id)
    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "business_id": conv.business_id,
        "clinic_name": conv.business.name if conv.business else "Clinic",
        "status": conv.status,
        "workflow_state": conv.workflow_state,
        "session_reset": is_new,
        "messages": [m.to_dict() for m in conv.messages]
    })


@chat_bp.route("/api/chat/history/<int:conversation_id>", methods=["GET"])
def get_history(conversation_id):
    visitor_id = _get_or_set_visitor_id()
    conv = Conversation.query.filter_by(id=conversation_id, visitor_id=visitor_id).first()
    if not conv:
        return jsonify({"success": False, "error": "Conversation not found"}), 404

    # Only return user and assistant messages for customer chat display
    visible_messages = [m.to_dict() for m in conv.messages if m.role in ["user", "assistant"]]

    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "business_id": conv.business_id,
        "clinic_name": conv.business.name if conv.business else "Clinic",
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

    target_business_id = _resolve_chat_business_id()
    if conversation_id:
        conv_obj = db.session.get(Conversation, conversation_id)
        if not conv_obj or conv_obj.business_id != target_business_id:
            conversation_id = None

    try:
        conv, is_new = get_or_create_conversation(target_business_id, conversation_id, visitor_id=visitor_id)
        llm_provider = current_app.config.get("LLM_PROVIDER", Config.LLM_PROVIDER)
        agent = Agent(business_id=target_business_id, llm_provider=llm_provider)
        result = agent.process_message(conversation_id=conv.id, user_content=message_text)

        return jsonify({
            "success": True,
            "conversation_id": conv.id,
            "business_id": conv.business_id,
            "status": result.get("status"),
            "workflow_state": result.get("workflow_state"),
            "reply": result.get("content"),
            "executed_tools": result.get("executed_tools", []),
            "ui_action": result.get("ui_action"),
            "metrics": result.get("metrics"),
            "session_reset": is_new
        })
    except Exception as e:
        current_app.logger.error(f"[Chat Send Error]: {e}", exc_info=True)
        return jsonify({
            "success": False,
            "error": "We encountered an issue processing your request. Please try again."
        }), 500


@chat_bp.route("/api/chat/reset", methods=["POST"])
def reset_chat():
    visitor_id = _get_or_set_visitor_id()
    business_id = _resolve_chat_business_id()
    conv, _ = get_or_create_conversation(business_id, visitor_id=visitor_id)
    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "business_id": conv.business_id,
        "clinic_name": conv.business.name if conv.business else "Clinic",
        "status": conv.status,
        "messages": [m.to_dict() for m in conv.messages]
    })


@chat_bp.route("/api/chat/send-voice", methods=["POST"])
def send_voice():
    visitor_id = _get_or_set_visitor_id()
    audio_file = request.files.get("file") or request.files.get("audio")
    conversation_id_str = request.form.get("conversation_id")
    conversation_id = int(conversation_id_str) if conversation_id_str and conversation_id_str.isdigit() else None

    if not audio_file:
        return jsonify({"success": False, "error": "Audio file is required"}), 400

    audio_bytes = audio_file.read()
    if not audio_bytes or len(audio_bytes) == 0:
        return jsonify({"success": False, "error": "Empty audio file received"}), 400

    mime_type = audio_file.mimetype or "audio/webm"

    stt_provider = current_app.config.get("STT_PROVIDER", Config.STT_PROVIDER)
    try:
        stt_client = STTClient(stt_provider=stt_provider)
        transcript = stt_client.transcribe(audio_bytes, mime_type=mime_type)
    except Exception as e:
        return jsonify({"success": False, "error": f"Speech transcription failed: {str(e)}"}), 400

    target_business_id = _resolve_chat_business_id()
    if conversation_id:
        conv_obj = db.session.get(Conversation, conversation_id)
        if not conv_obj or conv_obj.business_id != target_business_id:
            conversation_id = None

    conv, is_new = get_or_create_conversation(target_business_id, conversation_id, visitor_id=visitor_id)
    llm_provider = current_app.config.get("LLM_PROVIDER", Config.LLM_PROVIDER)
    agent = Agent(business_id=target_business_id, llm_provider=llm_provider)
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
        "metrics": result.get("metrics"),
        "session_reset": is_new,
        "stt_provider": stt_provider,
        "mock_transcription": stt_provider == "mock"
    })


@chat_bp.route("/api/chat/synthesize", methods=["POST"])
def synthesize_speech():
    """
    Convert a given text (typically the AI's most recent reply) into a
    playable audio clip, so a customer using voice input can receive a
    voice reply back instead of only text. Stateless — takes text
    directly rather than re-reading conversation state, since the caller
    (the chat UI) already has the reply text it wants spoken.
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"success": False, "error": "Text is required for speech synthesis"}), 400

    tts_provider = current_app.config.get("TTS_PROVIDER", getattr(Config, "TTS_PROVIDER", "gemini"))
    try:
        tts_client = TTSClient(tts_provider=tts_provider)
        audio_bytes = tts_client.synthesize(text)
    except Exception as e:
        return jsonify({"success": False, "error": f"Speech synthesis failed: {str(e)}"}), 400

    return Response(
        audio_bytes,
        mimetype="audio/wav",
        headers={"Content-Disposition": "inline; filename=reply.wav"}
    )
