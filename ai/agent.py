import json
import uuid
from typing import Dict, Any, List, Optional
from models import db, Conversation, Message, Customer, Doctor, Service
from ai.tools import CANONICAL_TOOLS, ToolDispatcher
from ai.prompts import build_system_prompt
from ai.llm_client import LLMClient


def _build_state_context(conv: Conversation) -> str:
    """
    Build a structured context block from persisted conversation state fields.
    This is injected as a system-level context message before conversation history
    so that the LLM is always aware of where in the booking workflow we are,
    even after many turns. Resolves doctor/service IDs to human-readable names.
    """
    lines = ["[CURRENT BOOKING CONTEXT]"]

    intent = conv.intent or "UNKNOWN"
    workflow = conv.workflow_state or "START"
    lines.append(f"Workflow State : {workflow}")
    lines.append(f"Customer Intent: {intent}")

    # Resolve doctor ID → name if set
    if conv.selected_doctor_id:
        try:
            doc = db.session.get(Doctor, conv.selected_doctor_id)
            if doc:
                lines.append(f"Selected Doctor: {doc.name} (ID: {doc.id})")
            else:
                lines.append(f"Selected Doctor: ID {conv.selected_doctor_id}")
        except Exception:
            lines.append(f"Selected Doctor: ID {conv.selected_doctor_id}")
    else:
        lines.append("Selected Doctor: (not yet chosen)")

    # Resolve service ID → name if set
    if conv.selected_service_id:
        try:
            svc = db.session.get(Service, conv.selected_service_id)
            if svc:
                lines.append(f"Selected Service: {svc.name} (ID: {svc.id}, {svc.duration} min)")
            else:
                lines.append(f"Selected Service: ID {conv.selected_service_id}")
        except Exception:
            lines.append(f"Selected Service: ID {conv.selected_service_id}")
    else:
        lines.append("Selected Service: (not yet chosen)")

    lines.append(f"Requested Date : {conv.requested_date or '(not yet set)'}")
    lines.append(f"Requested Time : {conv.requested_time or '(not yet set)'}")
    lines.append(f"Channel        : {conv.channel or 'web_chat'}")
    lines.append("")
    lines.append(
        "INSTRUCTION: Use the above context when interpreting the customer's next message. "
        "If the customer provides a time (e.g. '9:30') and a date is already set in context, "
        "proceed directly to booking without asking for the date again. "
        "If a doctor is already selected, remember that selection. "
        "Never lose information that is already stored in context."
    )
    return "\n".join(lines)


class Agent:
    def __init__(self, business_id: int, llm_provider: Optional[str] = None):
        self.business_id = business_id
        self.llm_client = LLMClient(provider=llm_provider)

    def process_message(self, conversation_id: int, user_content: str) -> Dict[str, Any]:
        """
        Process incoming customer message through the central AI Agent.
        Validates backend tools, updates structured state, and returns responses.
        Persisted conversation state is injected into every LLM turn so the model
        is never unaware of the current booking workflow position.
        """
        conv = db.session.get(Conversation, conversation_id)
        if not conv:
            return {"error": f"Conversation #{conversation_id} not found."}

        # Save user message first
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

        # Build dynamic system prompt from business DB data
        system_prompt = build_system_prompt(self.business_id)

        # Inject persisted conversation state as structured context (fixes "AI feels dumb" root cause)
        state_context = _build_state_context(conv)
        # Prepend as a system-level context injection — formatted to survive all adapters
        enriched_system_prompt = system_prompt + "\n\n" + state_context

        # Retrieve last 12 messages for conversational history
        past_messages = (
            Message.query
            .filter_by(conversation_id=conv.id)
            .order_by(Message.created_at.desc())
            .limit(12)
            .all()
        )
        past_messages.reverse()

        formatted_messages = []
        for m in past_messages:
            if m.role == "tool":
                formatted_messages.append({
                    "role": "tool",
                    "tool_name": m.tool_name or "tool",
                    "tool_call_id": m.tool_call_id or "call_0",
                    "content": m.content
                })
            else:
                formatted_messages.append({"role": m.role, "content": m.content})

        # Dispatcher for controlled tool execution
        dispatcher = ToolDispatcher(business_id=self.business_id, conversation_id=conv.id)

        # Initial LLM call
        response = self.llm_client.get_completion(
            system_prompt=enriched_system_prompt,
            messages=formatted_messages,
            tools=CANONICAL_TOOLS
        )

        executed_tools = []
        tool_results = []

        # Tool execution loop (max 3 iterations to prevent infinite loops)
        max_tool_iterations = 3
        iteration = 0

        while response.get("tool_calls") and iteration < max_tool_iterations:
            iteration += 1

            # If provider returned an assistant message with tool_calls, append it to history
            # (required by OpenAI/Groq protocol so tool responses are properly associated)
            if response.get("tool_calls"):
                formatted_messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": response["tool_calls"]
                })

            for tc in response["tool_calls"]:
                tool_name = tc.get("name")
                tool_args = tc.get("arguments", {})
                # Carry the real tool_call_id from the provider response
                tool_call_id = tc.get("id", f"call_{iteration}")

                # Auto-inject idempotency key for booking attempts so the service layer
                # idempotency check is exercised through the real conversational flow
                if tool_name == "book_appointment" and not tool_args.get("idempotency_key"):
                    tool_args["idempotency_key"] = f"conv-{conv.id}-attempt-{iteration}-{uuid.uuid4().hex[:8]}"

                executed_tools.append({"name": tool_name, "args": tool_args})

                # Update structured conversation state based on tool arguments
                self._update_conversation_state(conv, tool_name, tool_args)

                # Execute backend business tool
                result = dispatcher.execute(tool_name, tool_args)
                tool_results.append({"tool": tool_name, "result": result})

                # Persist tool message with IDs for Groq/OpenAI protocol round-trips
                tool_msg_content = json.dumps(result)
                tool_msg = Message(
                    conversation_id=conv.id,
                    role="tool",
                    content=tool_msg_content,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id
                )
                db.session.add(tool_msg)
                db.session.flush()

                # Add tool response to context for next LLM turn (with real IDs)
                formatted_messages.append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": tool_msg_content
                })

            # Re-query LLM with tool results to get natural language synthesis
            response = self.llm_client.get_completion(
                system_prompt=enriched_system_prompt,
                messages=formatted_messages,
                tools=CANONICAL_TOOLS
            )

        final_content = response.get(
            "content",
            "Thank you for contacting SmileCare Dental Clinic. How else may I assist you?"
        )

        # Save assistant response
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
        """
        Persist structured booking state into the conversation record after each tool call.
        This data is read back via _build_state_context() on every subsequent turn,
        forming the two-way feedback loop that keeps the LLM aware of workflow position.
        """
        if tool_name == "check_availability":
            conv.intent = "BOOK_APPOINTMENT"
            conv.workflow_state = "CHECKING_AVAILABILITY"
            if args.get("date"):
                conv.requested_date = str(args["date"])
            if args.get("doctor_id"):
                try:
                    conv.selected_doctor_id = int(args["doctor_id"])
                except Exception:
                    pass
            if args.get("service_id"):
                try:
                    conv.selected_service_id = int(args["service_id"])
                except Exception:
                    pass

        elif tool_name == "book_appointment":
            conv.intent = "BOOK_APPOINTMENT"
            conv.workflow_state = "BOOKED"
            if args.get("appointment_date"):
                conv.requested_date = str(args["appointment_date"])
            if args.get("appointment_time"):
                conv.requested_time = str(args["appointment_time"])
            if args.get("doctor_id"):
                try:
                    conv.selected_doctor_id = int(args["doctor_id"])
                except Exception:
                    pass
            if args.get("service_id"):
                try:
                    conv.selected_service_id = int(args["service_id"])
                except Exception:
                    pass

        elif tool_name == "cancel_appointment":
            conv.intent = "CANCEL_APPOINTMENT"
            conv.workflow_state = "COMPLETED"
            # Clear stale booking selections when intent changes
            conv.selected_doctor_id = None
            conv.selected_service_id = None
            conv.requested_date = None
            conv.requested_time = None

        elif tool_name == "reschedule_appointment":
            conv.intent = "RESCHEDULE_APPOINTMENT"
            conv.workflow_state = "BOOKED"
            if args.get("new_date"):
                conv.requested_date = str(args["new_date"])
            if args.get("new_time"):
                conv.requested_time = str(args["new_time"])

        elif tool_name == "human_handoff":
            conv.status = "HUMAN"
            conv.handoff_reason = args.get("reason", "Customer requested human assistance")
            conv.workflow_state = "HANDOFF_REQUESTED"

        elif tool_name == "get_doctors":
            # No workflow state change; just an info lookup
            pass

        elif tool_name == "get_services":
            # No workflow state change; just an info lookup
            pass

        elif tool_name == "get_clinic_info":
            # No workflow state change; just an info lookup
            pass

        db.session.flush()
