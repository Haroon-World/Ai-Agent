from typing import Dict, Any, Optional
from models import db, Conversation, Message


class HandoffService:
    @staticmethod
    def trigger_handoff(
        conversation_id: int,
        reason: Optional[str] = "Customer requested human assistance",
        business_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Transfers conversation control from AI to human staff.
        When status is HUMAN, the backend halts automatic AI responses.
        business_id scoping prevents cross-tenant privilege escalation.
        """
        conv = Conversation.query.filter_by(id=conversation_id).first()
        if not conv:
            return {"success": False, "error": f"Conversation #{conversation_id} not found."}

        # Tenant isolation: reject if the conversation belongs to a different business
        if business_id is not None and conv.business_id != business_id:
            return {
                "success": False,
                "error": "Not authorized: conversation does not belong to your business.",
                "code": 403
            }

        conv.status = "HUMAN"
        conv.handoff_reason = reason or "Customer requested human assistance"

        sys_msg = Message(
            conversation_id=conv.id,
            role="system",
            content=f"Human handoff initiated: {conv.handoff_reason}"
        )
        db.session.add(sys_msg)
        db.session.commit()

        return {
            "success": True,
            "conversation_id": conv.id,
            "status": "HUMAN",
            "reason": conv.handoff_reason,
            "message": "Conversation successfully transferred to human staff."
        }

    @staticmethod
    def release_to_ai(
        conversation_id: int,
        business_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Releases conversation control from human staff back to AI.
        Subsequent messages from the customer will resume automatic AI handling.
        business_id scoping prevents cross-tenant privilege escalation.
        """
        conv = Conversation.query.filter_by(id=conversation_id).first()
        if not conv:
            return {"success": False, "error": f"Conversation #{conversation_id} not found."}

        if business_id is not None and conv.business_id != business_id:
            return {
                "success": False,
                "error": "Not authorized: conversation does not belong to your business.",
                "code": 403
            }

        conv.status = "AI"
        conv.handoff_reason = None

        sys_msg = Message(
            conversation_id=conv.id,
            role="system",
            content="Conversation released back to AI receptionist."
        )
        db.session.add(sys_msg)
        db.session.commit()

        return {
            "success": True,
            "conversation_id": conv.id,
            "status": "AI",
            "message": "Conversation released back to AI mode."
        }

    @staticmethod
    def admin_reply(
        conversation_id: int,
        message_content: str,
        business_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Allows a human receptionist/admin to send a reply directly into the conversation.
        business_id scoping prevents cross-tenant privilege escalation.
        """
        conv = Conversation.query.filter_by(id=conversation_id).first()
        if not conv:
            return {"success": False, "error": f"Conversation #{conversation_id} not found."}

        if business_id is not None and conv.business_id != business_id:
            return {
                "success": False,
                "error": "Not authorized: conversation does not belong to your business.",
                "code": 403
            }

        msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=f"[Staff]: {message_content}"
        )
        db.session.add(msg)
        db.session.commit()

        return {
            "success": True,
            "message": msg.to_dict()
        }
