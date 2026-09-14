import hashlib
import hmac
import logging
from flask import Blueprint, request, jsonify, Response, current_app
from config.config import Config
from models import db, Business, Conversation, Customer, Message
from models.whatsapp_account import ClinicWhatsAppAccount
from services.whatsapp_service import WhatsAppService
from ai.agent import Agent

logger = logging.getLogger(__name__)

whatsapp_bp = Blueprint("whatsapp_bp", __name__)


# ---------------------------------------------------------------------------
# Part A helpers
# ---------------------------------------------------------------------------

def _verify_meta_signature(raw_body: bytes) -> bool:
    """
    Verify the X-Hub-Signature-256 header Meta sends with every webhook POST.
    Returns True if the signature matches (or if WHATSAPP_APP_SECRET is not
    configured, so development environments without the secret still work —
    remove that short-circuit before going to production).
    """
    app_secret = current_app.config.get("WHATSAPP_APP_SECRET", "") or Config.WHATSAPP_APP_SECRET
    if not app_secret:
        logger.warning(
            "[WhatsApp Webhook] WHATSAPP_APP_SECRET is not set — "
            "skipping signature verification. Set it in .env before production!"
        )
        return True  # Fail-open only in dev; set the secret in prod

    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header.startswith("sha256="):
        logger.warning("[WhatsApp Webhook] Missing or malformed X-Hub-Signature-256 header")
        return False

    expected_sig = "sha256=" + hmac.new(
        app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(expected_sig, signature_header)


@whatsapp_bp.route("/webhook", methods=["GET"])
def verify_webhook():
    """
    Meta WhatsApp Webhook Verification Handshake.
    Meta sends a GET request with hub.mode, hub.challenge, and hub.verify_token.
    """
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    logger.info(f"[WhatsApp Webhook] Verification attempt: mode={mode}, token={token}")

    if mode and token:
        # Check against global verify token or any clinic-specific verify token
        valid_tokens = [Config.WHATSAPP_WEBHOOK_VERIFY_TOKEN, "clinic_connect_secret_2026"]
        
        # Also query registered clinic tokens
        clinic_tokens = [
            acc.webhook_verify_token for acc in ClinicWhatsAppAccount.query.filter_by(is_active=True).all()
            if acc.webhook_verify_token
        ]
        valid_tokens.extend(clinic_tokens)

        if mode == "subscribe" and token in valid_tokens:
            logger.info("[WhatsApp Webhook] Verification successful!")
            resp = Response(challenge, status=200, mimetype="text/plain")
            # Bypass localtunnel interstitial so Meta's bot can reach us directly
            resp.headers["bypass-tunnel-reminder"] = "true"
            resp.headers["Ngrok-Skip-Browser-Warning"] = "true"
            return resp
        else:
            logger.warning(f"[WhatsApp Webhook] Verification token mismatch. Received: {token}")
            return Response("Verification token mismatch", status=403)

    return Response("Invalid request parameters", status=400)


@whatsapp_bp.route("/webhook", methods=["POST"])
def handle_webhook():
    """
    Meta WhatsApp Incoming Events Webhook.
    Handles user messages, routes them to the corresponding clinic AI agent,
    and returns immediate 200 OK to Meta.
    """
    # ------------------------------------------------------------------
    # Part A: Verify Meta's HMAC-SHA256 signature BEFORE any processing.
    # ------------------------------------------------------------------
    raw_body = request.get_data()
    if not _verify_meta_signature(raw_body):
        logger.warning(
            "[WhatsApp Webhook] Rejected request — invalid X-Hub-Signature-256. "
            "This was NOT from Meta (or WHATSAPP_APP_SECRET is wrong)."
        )
        return Response("Invalid signature", status=403)

    data = request.get_json(silent=True) or {}
    logger.info(f"[WhatsApp Webhook] Incoming payload: {data}")

    parsed = WhatsAppService.parse_incoming_payload(data)
    if not parsed:
        # Acknowledge receipt for delivery receipts, read receipts, etc.
        return jsonify({"status": "ignored", "reason": "non_message_event"}), 200

    phone_number_id = parsed.get("phone_number_id")
    from_phone = parsed.get("from_phone")
    patient_name = parsed.get("patient_name")
    user_text = parsed.get("text")
    msg_type = parsed.get("msg_type", "text")
    media_id = parsed.get("media_id")
    mime_type = parsed.get("mime_type", "audio/ogg; codecs=opus")

    # ------------------------------------------------------------------
    # Part B: Extract Meta's message ID for deduplication.
    # ------------------------------------------------------------------
    meta_message_id = parsed.get("message_id")

    if not from_phone:
        return jsonify({"status": "ignored", "reason": "missing_phone"}), 200

    # ------------------------------------------------------------------
    # Part B: Check for duplicate delivery BEFORE any heavy processing.
    # If we've already handled this exact wamid, acknowledge immediately.
    # ------------------------------------------------------------------
    if meta_message_id:
        existing = Message.query.filter_by(external_message_id=meta_message_id).first()
        if existing:
            logger.info(
                f"[WhatsApp Webhook] Duplicate delivery detected for message_id={meta_message_id} "
                f"(already stored as Message.id={existing.id}). Returning 200 without reprocessing."
            )
            return jsonify({"status": "duplicate", "message_id": meta_message_id}), 200

    try:
        # 1. Multi-Tenant Clinic Resolution
        wa_account = None
        if phone_number_id:
            wa_account = ClinicWhatsAppAccount.query.filter_by(
                phone_number_id=phone_number_id,
                is_active=True
            ).first()

        if wa_account:
            business_id = wa_account.business_id
            # Always prefer DB token — it can be updated without server restart.
            # Fall back to Config (env) token only if DB has none stored.
            access_token = wa_account.access_token or current_app.config.get("WHATSAPP_ACCESS_TOKEN")
        else:
            business_id = Config.DEFAULT_BUSINESS_ID
            access_token = current_app.config.get("WHATSAPP_ACCESS_TOKEN") or Config.WHATSAPP_ACCESS_TOKEN

        business = db.session.get(Business, business_id)
        if not business:
            logger.error(f"[WhatsApp Webhook] Business {business_id} not found")
            return jsonify({"status": "error", "error": "Business not found"}), 200

        # Handle Voice Note / Audio Message
        if msg_type == "audio" and media_id:
            logger.info(f"[WhatsApp Webhook] Downloading voice note {media_id}...")
            audio_bytes = WhatsAppService.download_media(media_id, access_token=access_token)
            if audio_bytes and len(audio_bytes) > 0:
                try:
                    from ai.speech_client import STTClient
                    stt_provider = current_app.config.get("STT_PROVIDER", Config.STT_PROVIDER)
                    stt = STTClient(stt_provider=stt_provider)
                    user_text = stt.transcribe(audio_bytes, mime_type=mime_type)
                    logger.info(f"[WhatsApp Webhook] Voice note transcribed: '{user_text}'")
                except Exception as ex:
                    logger.error(f"[WhatsApp Webhook] Audio transcription error: {ex}", exc_info=True)
                    user_text = None

            if not user_text:
                WhatsAppService.send_text_message(
                    to_phone=from_phone,
                    text="Maazrat, main aap ki aawaz theek se nahi sun saka. Barah-e-karam apna paighaam text mein likh dein ya dobara voice note bhejein.",
                    phone_number_id=phone_number_id or Config.WHATSAPP_PHONE_NUMBER_ID,
                    access_token=access_token
                )
                return jsonify({"status": "error", "error": "audio_transcription_failed"}), 200

        if not user_text:
            return jsonify({"status": "ignored", "reason": "empty_text"}), 200

        # 2. Customer Lookup or Creation
        clean_phone = WhatsAppService.clean_phone_number(from_phone)
        customer = Customer.query.filter(
            Customer.business_id == business_id,
            (Customer.phone == from_phone) | (Customer.phone == clean_phone)
        ).first()

        if not customer:
            customer = Customer(
                business_id=business_id,
                name=patient_name or f"WhatsApp Patient ({clean_phone[-4:] if len(clean_phone) >= 4 else clean_phone})",
                phone=clean_phone
            )
            db.session.add(customer)
            db.session.commit()
        elif patient_name and ("WhatsApp Patient" in (customer.name or "")):
            customer.name = patient_name
            db.session.commit()

        # 3. Unified Conversation Lookup — Strictly ONE continuous thread per WhatsApp Phone Number
        wa_visitor_id = f"wa_{clean_phone}"
        conv = Conversation.query.filter(
            Conversation.business_id == business_id,
            Conversation.channel == "whatsapp",
            (Conversation.visitor_id == wa_visitor_id) | 
            (Conversation.pending_customer_phone == clean_phone) |
            (Conversation.customer_id == customer.id)
        ).order_by(Conversation.id.desc()).first()

        if not conv:
            conv = Conversation(
                business_id=business_id,
                customer_id=customer.id,
                visitor_id=wa_visitor_id,
                channel="whatsapp",
                status="AI",
                intent="UNKNOWN",
                workflow_state="START",
                pending_customer_name=customer.name,
                pending_customer_phone=customer.phone
            )
            db.session.add(conv)
            db.session.commit()
        else:
            # Ensure visitor_id and customer_id are pinned
            if conv.visitor_id != wa_visitor_id:
                conv.visitor_id = wa_visitor_id
            if conv.customer_id != customer.id:
                conv.customer_id = customer.id
            if conv.status == "CLOSED":
                conv.status = "AI"
                conv.workflow_state = "START"

            # If previous state was BOOKED and user asks a new question or wants another booking/reschedule,
            # transition state cleanly so they don't get stuck in finished booking state
            if conv.workflow_state == "BOOKED":
                text_l = user_text.lower()
                is_status = any(k in text_l for k in ["booking", "appointment", "detail", "status", "bta", "bata", "check", "kya", "kab", "id", "#"])
                is_new_booking = any(k in text_l for k in ["new", "another", "book", "rakh", "schedule", "dr", "doctor", "service", "change", "reschedule", "make", "off", "tarikh", "date", "hi", "hello", "salam"])
                if is_new_booking and not is_status:
                    conv.workflow_state = "START"
                    conv.intent = "BOOK_APPOINTMENT"
                    conv.requested_date = None
                    conv.requested_time = None
                    conv.selected_doctor_id = None
                    conv.selected_service_id = None
                    conv.awaiting_input = None
            db.session.commit()

        # 4. Invoke AI Agent
        llm_provider = current_app.config.get("LLM_PROVIDER", Config.LLM_PROVIDER)
        agent = Agent(business_id=business_id, llm_provider=llm_provider)
        result = agent.process_message(conversation_id=conv.id, user_content=user_text)

        # ------------------------------------------------------------------
        # Part B: Stamp the external_message_id onto the user Message that
        # agent.process_message() just persisted, so future dedup checks work.
        # ------------------------------------------------------------------
        if meta_message_id:
            user_msg = (
                Message.query
                .filter_by(conversation_id=conv.id, role="user")
                .order_by(Message.id.desc())
                .first()
            )
            if user_msg and not user_msg.external_message_id:
                user_msg.external_message_id = meta_message_id
                db.session.commit()

        reply_content = result.get("content") or "Thank you for reaching out. How can I assist you with your clinic appointment?"

        # 5. Dispatch AI response back to Patient's WhatsApp
        send_res = WhatsAppService.send_text_message(
            to_phone=from_phone,
            text=reply_content,
            phone_number_id=phone_number_id or Config.WHATSAPP_PHONE_NUMBER_ID,
            access_token=access_token
        )

        if send_res.get("success"):
            print(f"[WhatsApp] Reply successfully sent to {from_phone}")
            logger.info(f"[WhatsApp Webhook] Reply sent to {from_phone}: success=True")
        else:
            err_msg = send_res.get("error")
            print(f"[WhatsApp Error] Failed to send reply to {from_phone}: {err_msg}")
            logger.error(f"[WhatsApp Webhook] Outbound send failed: {err_msg}")

        return jsonify({
            "status": "success",
            "conversation_id": conv.id,
            "business_id": business_id,
            "send_result": send_res
        }), 200

    except Exception as e:
        logger.error(f"[WhatsApp Webhook Error]: {e}", exc_info=True)
        # Always return 200 to Meta so it does not retry failed invocations infinitely
        return jsonify({"status": "error", "message": str(e)}), 200


# ---------------------------------------------------------------------------
# Part C: /test-send — requires a logged-in admin, scoped to their clinic
# ---------------------------------------------------------------------------

from functools import wraps
from flask import session

def _whatsapp_login_required(f):
    """
    Lightweight auth guard for WhatsApp blueprint endpoints.
    Reuses the same session keys set by admin_bp.login.
    Returns 401 JSON (not a redirect) since this is a JSON API endpoint.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id") or not session.get("business_id"):
            return jsonify({"success": False, "error": "Authentication required"}), 401
        return f(*args, **kwargs)
    return decorated


@whatsapp_bp.route("/test-send", methods=["POST"])
@_whatsapp_login_required
def test_send_message():
    """
    Direct endpoint for testing outbound WhatsApp messages from admin console.
    Requires admin login. Scoped to the logged-in admin's own clinic account —
    cannot be used to send from a different clinic's WhatsApp number.
    """
    # Part C: Scope to the logged-in admin's business — ignore any caller-supplied account
    business_id = session.get("business_id")

    wa_account = ClinicWhatsAppAccount.query.filter_by(
        business_id=business_id,
        is_active=True
    ).first()

    if wa_account:
        phone_number_id = wa_account.phone_number_id
        access_token = wa_account.access_token or Config.WHATSAPP_ACCESS_TOKEN
    else:
        phone_number_id = Config.WHATSAPP_PHONE_NUMBER_ID
        access_token = Config.WHATSAPP_ACCESS_TOKEN

    data = request.get_json(silent=True) or {}
    to_phone = data.get("phone") or request.form.get("phone")
    text = data.get("message") or request.form.get("message", "Hello from ClinicConnect AI Agent!")
    template = data.get("template")

    if not to_phone:
        return jsonify({"success": False, "error": "Recipient phone number required"}), 400

    if template:
        res = WhatsAppService.send_template(
            to_phone=to_phone,
            template_name=template,
            phone_number_id=phone_number_id,
            access_token=access_token
        )
    else:
        res = WhatsAppService.send_text_message(
            to_phone=to_phone,
            text=text,
            phone_number_id=phone_number_id,
            access_token=access_token
        )

    return jsonify(res)


