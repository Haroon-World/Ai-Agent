import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Optional
from config.config import Config

logger = logging.getLogger(__name__)


class WhatsAppService:
    """
    Service wrapper for Meta WhatsApp Cloud API (Graph API).
    Supports outbound text messaging, template messaging, and payload parsing.
    """

    @staticmethod
    def clean_phone_number(phone: str) -> str:
        """Strip all non-numeric characters from a phone number."""
        if not phone:
            return ""
        clean = re.sub(r"[^\d]", "", str(phone))
        # If number starts with 0 and looks like Pakistani local format (03001234567), convert to 923001234567
        if clean.startswith("0") and len(clean) == 11:
            clean = "92" + clean[1:]
        return clean

    @classmethod
    def send_text_message(
        cls,
        to_phone: str,
        text: str,
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a standard text message via WhatsApp Cloud API.
        Falls back to Config settings if credentials are not provided.
        """
        pid = phone_number_id or Config.WHATSAPP_PHONE_NUMBER_ID
        token = access_token or Config.WHATSAPP_ACCESS_TOKEN
        version = Config.WHATSAPP_API_VERSION or "v19.0"

        if not pid or not token:
            logger.error("[WhatsAppService] Missing phone_number_id or access_token")
            return {"success": False, "error": "WhatsApp credentials not configured"}

        clean_to = cls.clean_phone_number(to_phone)
        if not clean_to:
            return {"success": False, "error": "Invalid recipient phone number"}

        url = f"https://graph.facebook.com/{version}/{pid}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": clean_to,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": text
            }
        }

        return cls._post_request(url, payload, token)

    @classmethod
    def send_template(
        cls,
        to_phone: str,
        template_name: str = "hello_world",
        language_code: str = "en_US",
        phone_number_id: Optional[str] = None,
        access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send a pre-approved template message (useful for initiating conversations outside 24h window).
        """
        pid = phone_number_id or Config.WHATSAPP_PHONE_NUMBER_ID
        token = access_token or Config.WHATSAPP_ACCESS_TOKEN
        version = Config.WHATSAPP_API_VERSION or "v19.0"

        clean_to = cls.clean_phone_number(to_phone)
        url = f"https://graph.facebook.com/{version}/{pid}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": clean_to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": language_code
                }
            }
        }
        return cls._post_request(url, payload, token)

    @staticmethod
    def _post_request(url: str, payload: Dict[str, Any], token: str) -> Dict[str, Any]:
        """Internal helper to dispatch HTTP POST request to Meta Graph API."""
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "ClinicConnect-WhatsApp-Agent/1.0"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp_body = resp.read().decode("utf-8")
                parsed = json.loads(resp_body)
                logger.info(f"[WhatsAppService] Dispatched message to Meta: {parsed}")
                return {
                    "success": True,
                    "status_code": resp.status,
                    "data": parsed
                }
        except urllib.error.HTTPError as he:
            err_text = he.read().decode("utf-8", errors="replace")
            logger.error(f"[WhatsAppService] Meta HTTPError {he.code}: {err_text}")
            return {
                "success": False,
                "status_code": he.code,
                "error": err_text
            }
        except Exception as ex:
            logger.error(f"[WhatsAppService] Request exception: {ex}")
            return {
                "success": False,
                "error": str(ex)
            }

    @classmethod
    def parse_incoming_payload(cls, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Parse Meta WhatsApp Webhook POST body.
        Extracts sender phone, sender name, message body, message ID, and receiver phone_number_id.
        Returns None if not an actionable user message (e.g. read receipts or status updates).
        """
        if not data or not isinstance(data, dict):
            return None

        entries = data.get("entry", [])
        if not entries:
            return None

        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id")

                # Handle inbound user messages
                messages = value.get("messages", [])
                if not messages:
                    # Ignore status updates (sent, delivered, read)
                    continue

                msg = messages[0]
                msg_type = msg.get("type", "text")
                sender_phone = msg.get("from")
                msg_id = msg.get("id")

                # Resolve sender contact name if provided
                contacts = value.get("contacts", [])
                sender_name = None
                if contacts and isinstance(contacts, list):
                    profile = contacts[0].get("profile", {})
                    sender_name = profile.get("name")

                body_text = None
                if msg_type == "text":
                    body_text = msg.get("text", {}).get("body", "")
                elif msg_type == "interactive":
                    interactive = msg.get("interactive", {})
                    i_type = interactive.get("type")
                    if i_type == "button_reply":
                        body_text = interactive.get("button_reply", {}).get("title") or interactive.get("button_reply", {}).get("id")
                    elif i_type == "list_reply":
                        body_text = interactive.get("list_reply", {}).get("title") or interactive.get("list_reply", {}).get("id")
                elif msg_type in ["button", "quick_reply"]:
                    body_text = msg.get("button", {}).get("text") or msg.get("text")
                elif msg_type in ["audio", "voice"]:
                    audio_obj = msg.get("audio") or msg.get("voice") or {}
                    media_id = audio_obj.get("id")
                    mime_type = audio_obj.get("mime_type", "audio/ogg; codecs=opus")
                    return {
                        "phone_number_id": phone_number_id,
                        "from_phone": sender_phone,
                        "patient_name": sender_name,
                        "message_id": msg_id,
                        "text": None,
                        "media_id": media_id,
                        "mime_type": mime_type,
                        "msg_type": "audio",
                        "raw_message": msg
                    }

                if body_text is not None:
                    return {
                        "phone_number_id": phone_number_id,
                        "from_phone": sender_phone,
                        "patient_name": sender_name,
                        "message_id": msg_id,
                        "text": body_text.strip(),
                        "msg_type": "text",
                        "raw_message": msg
                    }

        return None

    @classmethod
    def download_media(cls, media_id: str, access_token: Optional[str] = None) -> Optional[bytes]:
        """
        Download media (such as an audio voice note) from Meta WhatsApp Cloud API.
        Step 1: Retrieve temporary CDN URL from Graph API endpoint.
        Step 2: Fetch raw binary data from CDN URL using Bearer token.
        """
        token = access_token or Config.WHATSAPP_ACCESS_TOKEN
        version = Config.WHATSAPP_API_VERSION or "v19.0"
        if not media_id or not token:
            logger.error("[WhatsAppService] download_media: Missing media_id or token")
            return None

        # Step 1: Get media URL
        url = f"https://graph.facebook.com/{version}/{media_id}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "ClinicConnect-WhatsApp-Agent/1.0"
            }
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                media_url = data.get("url")
                if not media_url:
                    logger.error(f"[WhatsAppService] No URL in media metadata: {data}")
                    return None
        except Exception as e:
            logger.error(f"[WhatsAppService] Failed to retrieve media URL for {media_id}: {e}")
            return None

        # Step 2: Download raw binary
        media_req = urllib.request.Request(
            media_url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "ClinicConnect-WhatsApp-Agent/1.0"
            }
        )
        try:
            with urllib.request.urlopen(media_req, timeout=30) as media_resp:
                return media_resp.read()
        except Exception as e:
            logger.error(f"[WhatsAppService] Failed to download media bytes from {media_url}: {e}")
            return None

