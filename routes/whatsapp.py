import logging
from flask import Blueprint, request, jsonify, Response, current_app
from config.config import Config
from models import db, Business, Conversation, Customer
from models.whatsapp_account import ClinicWhatsAppAccount
from services.whatsapp_service import WhatsAppService
from ai.agent import Agent

logger = logging.getLogger(__name__)

whatsapp_bp = Blueprint("whatsapp_bp", __name__)


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

    if not from_phone:
        return jsonify({"status": "ignored", "reason": "missing_phone"}), 200

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
            access_token = wa_account.access_token or Config.WHATSAPP_ACCESS_TOKEN
        else:
            business_id = Config.DEFAULT_BUSINESS_ID
            access_token = Config.WHATSAPP_ACCESS_TOKEN

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

        # 3. Conversation Lookup by WhatsApp Unique Visitor ID (ensures continuity across bookings)
        wa_visitor_id = f"wa_{clean_phone}"
        conv = Conversation.query.filter_by(
            business_id=business_id,
            visitor_id=wa_visitor_id
        ).order_by(Conversation.updated_at.desc()).first()

        # Fallback to customer_id if no visitor_id conversation yet
        if not conv:
            conv = Conversation.query.filter_by(
                business_id=business_id,
                customer_id=customer.id,
                channel="whatsapp"
            ).order_by(Conversation.updated_at.desc()).first()
            if conv and not conv.visitor_id:
                conv.visitor_id = wa_visitor_id
                db.session.commit()

        if not conv or conv.status == "CLOSED":
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
            # If previous state was BOOKED and user asks a new question or wants another booking,
            # transition state cleanly so they don't get stuck
            if conv.workflow_state == "BOOKED":
                text_l = user_text.lower()
                is_status = any(k in text_l for k in ["booking", "appointment", "detail", "status", "bta", "bata", "check", "kya", "kab", "id", "#"])
                is_new_booking = any(k in text_l for k in ["new", "another", "book", "rakh", "schedule", "dr", "doctor", "service"])
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

        reply_content = result.get("content") or "Thank you for reaching out. How can I assist you with your clinic appointment?"

        # 5. Dispatch AI response back to Patient's WhatsApp
        send_res = WhatsAppService.send_text_message(
            to_phone=from_phone,
            text=reply_content,
            phone_number_id=phone_number_id or Config.WHATSAPP_PHONE_NUMBER_ID,
            access_token=access_token
        )

        logger.info(f"[WhatsApp Webhook] Reply sent to {from_phone}: success={send_res.get('success')}")

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


@whatsapp_bp.route("/test-send", methods=["POST"])
def test_send_message():
    """
    Direct endpoint for testing outbound WhatsApp messages from admin console.
    """
    data = request.get_json(silent=True) or {}
    to_phone = data.get("phone") or request.form.get("phone")
    text = data.get("message") or request.form.get("message", "Hello from ClinicConnect AI Agent!")
    template = data.get("template")

    if not to_phone:
        return jsonify({"success": False, "error": "Recipient phone number required"}), 400

    if template:
        res = WhatsAppService.send_template(to_phone=to_phone, template_name=template)
    else:
        res = WhatsAppService.send_text_message(to_phone=to_phone, text=text)

    return jsonify(res)
