import json
import re
import uuid
from typing import Dict, Any, List, Optional
from models import db, Conversation, Message, Customer, Doctor, Service
from ai.tools import CANONICAL_TOOLS, ToolDispatcher
from ai.prompts import build_system_prompt
from ai.llm_client import LLMClient, _extract_name



def _is_question_query(text: str) -> bool:
    """Check if the text is phrased as a question/inquiry rather than a direct statement or slot selection."""
    if not text:
        return False
    if "?" in text:
        return True
    lower = text.lower().strip()
    question_prefixes = [
        "is there", "are there", "any other", "what about", "do you have",
        "can i", "could i", "when", "which", "how about", "available after",
        "slots after", "available before", "slots before", "what time",
        "is anything", "are any", "what are", "who is", "show me", "tell me"
    ]
    return any(qp in lower for qp in question_prefixes)


def _extract_time_token(text: str) -> Optional[str]:
    """Extract standard HH:MM time string from user text supporting both ':' and '.' as separators."""
    if not text:
        return None
    # Match standard HH:MM or HH.MM (e.g. 9:30, 09:30, 12.00, 12.00pm, 14:00)
    m = re.search(r'\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b', text, re.IGNORECASE)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        ampm = m.group(3).lower() if m.group(3) else None
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"
    # Match spoken forms like 10 am, 2 pm, 12 pm
    m = re.search(r'\b(\d{1,2})\s*(am|pm)\b', text, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        ampm = m.group(2).lower()
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    return None


def _build_state_dict(conv: Conversation) -> Dict[str, Any]:
    """
    Extract structured conversation state as a programmatic dictionary.
    Includes last offered slot list parsed from previous tool messages.
    """
    doc_name = None
    if conv.selected_doctor_id:
        try:
            doc = db.session.get(Doctor, conv.selected_doctor_id)
            if doc:
                doc_name = doc.name
        except Exception:
            pass

    svc_name = None
    if conv.selected_service_id:
        try:
            svc = db.session.get(Service, conv.selected_service_id)
            if svc:
                svc_name = svc.name
        except Exception:
            pass

    # Extract last offered slots from most recent check_availability tool execution
    last_offered_slots: Dict[str, List[str]] = {}
    all_offered_slots: List[str] = []
    try:
        last_tool_msg = (
            Message.query
            .filter_by(conversation_id=conv.id, role="tool", tool_name="check_availability")
            .order_by(Message.created_at.desc())
            .first()
        )
        if not last_tool_msg:
            last_tool_msg = (
                Message.query
                .filter_by(conversation_id=conv.id, role="tool")
                .order_by(Message.created_at.desc())
                .first()
            )
        if last_tool_msg and last_tool_msg.content:
            data = json.loads(last_tool_msg.content)
            if isinstance(data, dict) and "results" in data:
                for r in data.get("results", []):
                    d_id = str(r.get("doctor_id"))
                    slots = r.get("available_slots", [])
                    last_offered_slots[d_id] = slots
                    for s in slots:
                        if s not in all_offered_slots:
                            all_offered_slots.append(s)
    except Exception:
        pass

    return {
        "workflow_state": conv.workflow_state or "START",
        "intent": conv.intent or "UNKNOWN",
        "selected_doctor_id": conv.selected_doctor_id,
        "selected_doctor_name": doc_name,
        "selected_service_id": conv.selected_service_id,
        "selected_service_name": svc_name,
        "requested_date": conv.requested_date,
        "requested_time": conv.requested_time,
        "pending_customer_name": conv.pending_customer_name,
        "pending_customer_phone": conv.pending_customer_phone,
        "customer_id": conv.customer_id,
        "channel": conv.channel or "web_chat",
        "business_id": conv.business_id,
        "last_offered_slots": last_offered_slots,
        "all_offered_slots": all_offered_slots
    }


def _build_state_context(conv: Conversation) -> str:
    """
    Build a structured context block from persisted conversation state fields.
    This is injected as a system-level context message into the system prompt
    for real LLM providers (Gemini, Groq) so they maintain context across turns.
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
    lines.append(f"Customer Name  : {conv.pending_customer_name or '(not yet provided)'}")
    lines.append(f"Customer Phone : {conv.pending_customer_phone or '(not yet provided)'}")
    lines.append(f"Channel        : {conv.channel or 'web_chat'}")
    lines.append("")
    lines.append(
        "INSTRUCTION: Use the above context when interpreting the customer's next message. "
        "If the customer provides a time (e.g. '9:30') and a date is already set in context, "
        "proceed directly to booking without asking for the date again. "
        "If a doctor is already selected, remember that selection. "
        "If customer name or phone is already provided in context, do not ask for it again. "
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

        # Build structured conversation state dict
        state_dict = _build_state_dict(conv)
        all_offered = state_dict.get("all_offered_slots", [])

        # If conversation is checking availability and user supplies a non-question time selection, capture it
        if conv.workflow_state in ["CHECKING_AVAILABILITY", "COLLECTING_INFO"] and user_content:
            if not _is_question_query(user_content):
                time_token = _extract_time_token(user_content)
                # Only set if time_token is an offered slot or if no offered list exists yet
                if time_token and (not all_offered or time_token in all_offered):
                    conv.requested_time = time_token
                    db.session.flush()
                    state_dict["requested_time"] = time_token

        # Capture phone number if present in user message
        phone_match = re.search(r'\b(03\d{2}[- ]?\d{7}|\+92\d{10}|03\d{9})\b', user_content)
        if phone_match:
            clean_phone = phone_match.group(1).replace(" ", "").replace("-", "")
            conv.pending_customer_phone = clean_phone
            db.session.flush()
            state_dict["pending_customer_phone"] = clean_phone

        # Capture customer name if present in user message
        cand_name = _extract_name(user_content)
        if cand_name and not _is_question_query(user_content):
            conv.pending_customer_name = cand_name
            db.session.flush()
            state_dict["pending_customer_name"] = cand_name


        # Build dynamic system prompt from business DB data
        system_prompt = build_system_prompt(self.business_id)

        # Inject persisted conversation state as structured text context (for Gemini/Groq)
        state_context = _build_state_context(conv)
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

        # Initial LLM call (passing both enriched system prompt and structured state_dict)
        response = self.llm_client.get_completion(
            system_prompt=enriched_system_prompt,
            messages=formatted_messages,
            tools=CANONICAL_TOOLS,
            conversation_state=state_dict
        )

        executed_tools = []
        tool_results = []

        # Tool execution loop (max 5 iterations to prevent infinite loops)
        max_tool_iterations = 5
        iteration = 0


        while response.get("tool_calls") and iteration < max_tool_iterations:
            iteration += 1

            # If provider returned an assistant message with tool_calls, append it to history
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

                # Auto-inject idempotency key for booking attempts
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

                # Add tool response to context for next LLM turn
                formatted_messages.append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": tool_msg_content
                })

            # Update state context and state dict after tool execution
            state_dict = _build_state_dict(conv)
            enriched_system_prompt = system_prompt + "\n\n" + _build_state_context(conv)

            # Re-query LLM with tool results to get natural language synthesis
            response = self.llm_client.get_completion(
                system_prompt=enriched_system_prompt,
                messages=formatted_messages,
                tools=CANONICAL_TOOLS,
                conversation_state=state_dict
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
