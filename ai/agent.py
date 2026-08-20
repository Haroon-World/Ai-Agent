import json
from typing import Dict, Any, List, Optional
from models import db, Conversation, Message, Customer
from ai.tools import CANONICAL_TOOLS, ToolDispatcher
from ai.prompts import build_system_prompt
from ai.llm_client import LLMClient

class Agent:
    def __init__(self, business_id: int, llm_provider: Optional[str] = None):
        self.business_id = business_id
        self.llm_client = LLMClient(provider=llm_provider)

    def process_message(self, conversation_id: int, user_content: str) -> Dict[str, Any]:
        """
        Process incoming customer message through the central AI Agent,
        validating backend tools, updating structured state, and returning responses.
        """
        conv = db.session.get(Conversation, conversation_id)
        if not conv:
            return {"error": f"Conversation #{conversation_id} not found."}

        # Save user message
        user_msg = Message(conversation_id=conv.id, role="user", content=user_content)
        db.session.add(user_msg)
        db.session.flush()

        # Strict Server-Side Check: If conversation is in HUMAN mode, AI does not auto-reply
        if conv.status == "HUMAN":
            db.session.commit()
            return {
                "conversation_id": conv.id,
                "status": "HUMAN",
                "content": "Our human staff has taken over this conversation and will reply shortly.",
                "tool_calls": [],
                "workflow_state": conv.workflow_state
            }

        # Build dynamic system prompt
        system_prompt = build_system_prompt(self.business_id)

        # Retrieve message history (last 12 messages for conversational context)
        past_messages = Message.query.filter_by(conversation_id=conv.id).order_by(Message.created_at.desc()).limit(12).all()
        past_messages.reverse()

        formatted_messages = []
        for m in past_messages:
            formatted_messages.append({"role": m.role, "content": m.content})

        # Dispatcher for controlled tool execution
        dispatcher = ToolDispatcher(business_id=self.business_id, conversation_id=conv.id)

        # Call LLM
        response = self.llm_client.get_completion(
            system_prompt=system_prompt,
            messages=formatted_messages,
            tools=CANONICAL_TOOLS
        )

        executed_tools = []
        tool_results = []

        # Tool execution loop
        max_tool_iterations = 3
        iteration = 0

        while response.get("tool_calls") and iteration < max_tool_iterations:
            iteration += 1
            for tc in response["tool_calls"]:
                tool_name = tc.get("name")
                tool_args = tc.get("arguments", {})
                executed_tools.append({"name": tool_name, "args": tool_args})

                # Update structured conversation state based on tool arguments
                self._update_conversation_state(conv, tool_name, tool_args)

                # Execute backend business tool
                result = dispatcher.execute(tool_name, tool_args)
                tool_results.append({"tool": tool_name, "result": result})

                # Record tool execution in history
                tool_msg_content = json.dumps(result)
                tool_msg = Message(
                    conversation_id=conv.id,
                    role="tool",
                    content=tool_msg_content
                )
                db.session.add(tool_msg)
                db.session.flush()

                # Add to context for next LLM turn
                formatted_messages.append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": tool_msg_content
                })

            # Re-query LLM with tool result to get natural language synthesis
            response = self.llm_client.get_completion(
                system_prompt=system_prompt,
                messages=formatted_messages,
                tools=CANONICAL_TOOLS
            )

        final_content = response.get("content", "Thank you for contacting SmileCare Dental Clinic. How else may I assist you?")

        # Save assistant message
        asst_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=final_content
        )
        db.session.add(asst_msg)
        db.session.commit()

        return {
            "conversation_id": conv.id,
            "status": conv.status,
            "content": final_content,
            "executed_tools": executed_tools,
            "tool_results": tool_results,
            "intent": conv.intent,
            "workflow_state": conv.workflow_state
        }

    def _update_conversation_state(self, conv: Conversation, tool_name: str, args: Dict[str, Any]):
        """Persist structured state in conversation record."""
        if tool_name == "check_availability":
            conv.intent = "BOOK_APPOINTMENT"
            conv.workflow_state = "CHECKING_AVAILABILITY"
            if args.get("date"):
                conv.requested_date = str(args.get("date"))
            if args.get("doctor_id"):
                try:
                    conv.selected_doctor_id = int(args.get("doctor_id"))
                except Exception:
                    pass
            if args.get("service_id"):
                try:
                    conv.selected_service_id = int(args.get("service_id"))
                except Exception:
                    pass

        elif tool_name == "book_appointment":
            conv.intent = "BOOK_APPOINTMENT"
            conv.workflow_state = "BOOKED"
            if args.get("appointment_date"):
                conv.requested_date = str(args.get("appointment_date"))
            if args.get("appointment_time"):
                conv.requested_time = str(args.get("appointment_time"))
            if args.get("doctor_id"):
                try:
                    conv.selected_doctor_id = int(args.get("doctor_id"))
                except Exception:
                    pass
            if args.get("service_id"):
                try:
                    conv.selected_service_id = int(args.get("service_id"))
                except Exception:
                    pass

        elif tool_name == "cancel_appointment":
            conv.intent = "CANCEL_APPOINTMENT"
            conv.workflow_state = "COMPLETED"

        elif tool_name == "reschedule_appointment":
            conv.intent = "RESCHEDULE_APPOINTMENT"
            conv.workflow_state = "BOOKED"
            if args.get("new_date"):
                conv.requested_date = str(args.get("new_date"))
            if args.get("new_time"):
                conv.requested_time = str(args.get("new_time"))

        elif tool_name == "human_handoff":
            conv.status = "HUMAN"
            conv.handoff_reason = args.get("reason", "Customer requested human assistance")
            conv.workflow_state = "HANDOFF_REQUESTED"
