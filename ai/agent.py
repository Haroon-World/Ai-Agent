import json
import re
import uuid
import time
import threading
from datetime import datetime, timezone, date as dt_date, timedelta as dt_td
from typing import Dict, Any, List, Optional
from sqlalchemy import event
from sqlalchemy.engine import Engine
from models import db, Business, Conversation, Message, Customer, Doctor, Service
from services.booking_service import BookingService, _get_business, _get_business_info, _get_business_tz, RequestCache
from ai.tools import CANONICAL_TOOLS, ToolDispatcher
from ai.prompts import build_system_prompt
from ai.llm_client import LLMClient, _extract_name, _fuzzy_match_roster, _extract_doctor_mention, resolve_date_string, _classify_intent
from ai.response_generator import generate_tool_response, detect_language

_local_perf_state = threading.local()

@event.listens_for(Engine, "before_cursor_execute")
def _on_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if getattr(_local_perf_state, "active", False):
        _local_perf_state.query_count = getattr(_local_perf_state, "query_count", 0) + 1



def _is_question_query(text: str) -> bool:
    """Check if the text is phrased as a question/inquiry rather than a direct statement or slot selection."""
    if not text:
        return False
    lower = text.lower().strip()
    if any(w in lower for w in ["appointment fix", "book appointment", "appointment book", "booking fix", "اپائنٹمنٹ بک", "اپائنٹمنٹ فکس", "بکنگ"]):
        return False
    if "?" in text or "؟" in text:
        return True
    question_prefixes = [
        "is there", "are there", "any other", "what about", "do you have",
        "can i", "could i", "when", "which", "how about", "available after",
        "slots after", "available before", "slots before", "what time",
        "is anything", "are any", "what are", "who is", "show me", "tell me",
        "is this", "is that", "available", "after", "before", "free", "any slot",
        "kis din", "kis kis din", "kab", "timing", "schedule", "working days",
        "کس دن", "کس کس دن", "کب", "شیڈول", "ٹائمنگ", "اوقات", "بیٹھتی", "بیٹھتے"
    ]
    return any(qp in lower for qp in question_prefixes)


_URDU_ROMAN_NUMBERS = {
    "ek": 1, "aik": 1, "ایک": 1, "۱": 1,
    "do": 2, "doo": 2, "دو": 2, "۲": 2,
    "teen": 3, "tin": 3, "تین": 3, "۳": 3,
    "chaar": 4, "char": 4, "چار": 4, "۴": 4,
    "paanch": 5, "panch": 5, "پانچ": 5, "۵": 5,
    "che": 6, "chay": 6, "chhey": 6, "چھ": 6, "۶": 6,
    "saat": 7, "sat": 7, "سات": 7, "۷": 7,
    "aath": 8, "ath": 8, "آٹھ": 8, "۸": 8,
    "nau": 9, "no": 9, "نو": 9, "۹": 9,
    "das": 10, "دس": 10, "۱۰": 10,
    "gyarah": 11, "gyara": 11, "gyaarah": 11, "گیارہ": 11, "گہرہ": 11, "گیرہ": 11, "۱۱": 11,
    "barah": 12, "bara": 12, "baarah": 12, "بارہ": 12, "۱۲": 12
}


def _extract_time_token(text: str) -> Optional[str]:
    """
    Extract standard HH:MM time string from user text supporting:
    - 24-hour and 12-hour: 14:00, 2:00 PM, 2:30 pm, 02:00 PM
    - Spoken English/Roman Urdu: 2 PM, 2 pm, 2 baje, do baje, 10 am, subah 10 baje
    - Urdu script: دو بجے, ۲ بجے, دن دو بجے, دوپہر ۲ بجے, صبح ۱۰ بجے, شام ۴ بجے
    - Prepositions: at 2, around 2, after 12, before 2, ko 2
    """
    if not text:
        return None
    lower = text.lower().strip()

    # 1. Match standard HH:MM or HH.MM (e.g. 9:30, 09:30, 14:00, 2:00 PM, 2.00pm)
    m = re.search(r'\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b', text, re.IGNORECASE)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        ampm = m.group(3).lower() if m.group(3) else None
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"

    # 2. Match H am / H pm (e.g. 10 am, 2 pm, 12 pm)
    m = re.search(r'\b(\d{1,2})\s*(am|pm)\b', text, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        ampm = m.group(2).lower()
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"

    # 3. Match number/word + baje / بجے / بجی / o'clock (e.g. 2 baje, do baji, دو بجی, دو بجے, دن دو بجی)
    num_pattern = r'(\d{1,2}|ek|aik|do|doo|teen|tin|chaar|char|paanch|panch|che|chay|chhey|saat|sat|aath|ath|nau|no|das|gyarah|gyara|gyaarah|barah|bara|baarah|ایک|دو|تین|چار|پانچ|چھ|سات|آٹھ|نو|دس|گیارہ|گہرہ|گیرہ|بارہ|[۱-۹]|۱۰|۱۱|۱۲)'
    baje_pattern = r'(?:baje|bje|bjay|baji|bajy|bajeh|o\'?clock|بجے|بجی)'
    prefix_pattern = r'(?:(?:din|dopahar|shaam|raat|subah|دن|دوپہر|شام|رات|صبح)(?:\s+(?:ko|ke|ki|کو|کے|کی))?\s+)?'
    m = re.search(prefix_pattern + num_pattern + r'\s*' + baje_pattern, text, re.IGNORECASE)
    if m:
        token = m.group(1).lower()
        h = int(token) if token.isdigit() else _URDU_ROMAN_NUMBERS.get(token)
        if h is not None:
            is_pm = any(w in lower for w in ["pm", "dopahar", "shaam", "raat", "دوپہر", "شام", "رات", "دن"])
            is_am = any(w in lower for w in ["am", "subah", "صبح"])
            if is_pm and h < 12:
                h += 12
            elif is_am and h == 12:
                h = 0
            elif not is_pm and not is_am and 1 <= h <= 7:
                h += 12
            return f"{h:02d}:00"

    # 4. Match bare number/word after prepositions like "at 2", "after 12", "ko 2", "ki 2"
    m = re.search(r'\b(?:after|before|at|around|from|past|ko|ke|ki|pe|par|کو|پر|کے|کی)\s+' + num_pattern + r'\b', text, re.IGNORECASE)
    if m:
        token = m.group(1).lower()
        h = int(token) if token.isdigit() else _URDU_ROMAN_NUMBERS.get(token)
        if h is not None:
            is_pm = any(w in lower for w in ["pm", "dopahar", "shaam", "raat", "دوپہر", "شام", "رات", "دن"])
            is_am = any(w in lower for w in ["am", "subah", "صبح"])
            if is_pm and h < 12:
                h += 12
            elif is_am and h == 12:
                h = 0
            elif not is_pm and not is_am and 1 <= h <= 7:
                h += 12
            return f"{h:02d}:00"

    return None


def _build_state_dict(conv: Conversation) -> Dict[str, Any]:
    """
    Extract structured conversation state as a programmatic dictionary.
    Includes last offered slot list parsed from previous tool messages.
    Uses RequestCache to eliminate repeated roster SQL queries.
    """
    doctor_roster = BookingService.get_doctors(conv.business_id)
    service_roster = BookingService.get_services(conv.business_id)

    doc_name = None
    if conv.selected_doctor_id:
        match = next((d for d in doctor_roster if d["id"] == conv.selected_doctor_id), None)
        if match:
            doc_name = match["name"]
        else:
            try:
                doc = db.session.get(Doctor, conv.selected_doctor_id)
                if doc:
                    doc_name = doc.name
            except Exception:
                pass

    svc_name = None
    if conv.selected_service_id:
        match = next((s for s in service_roster if s["id"] == conv.selected_service_id), None)
        if match:
            svc_name = match["name"]
        else:
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
            if isinstance(data, dict):
                if "available_slots" in data:
                    top_slots = data.get("available_slots", []) or []
                    all_offered_slots.extend(top_slots)
                    if data.get("doctor_id"):
                        last_offered_slots[str(data["doctor_id"])] = top_slots
                if "results" in data:
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
        "awaiting_input": conv.awaiting_input,
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
        "all_offered_slots": all_offered_slots,
        "doctor_roster": doctor_roster,
        "service_roster": service_roster
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
    awaiting = conv.awaiting_input or "(none)"
    lines.append(f"Workflow State : {workflow}")
    lines.append(f"Customer Intent: {intent}")
    lines.append(f"Awaiting Input : {awaiting}")

    doctor_roster = BookingService.get_doctors(conv.business_id)
    service_roster = BookingService.get_services(conv.business_id)

    # Resolve doctor ID -> name if set
    if conv.selected_doctor_id:
        doc_entry = next((d for d in doctor_roster if d["id"] == conv.selected_doctor_id), None)
        if doc_entry:
            lines.append(f"Selected Doctor: {doc_entry['name']} (ID: {doc_entry['id']})")
        else:
            lines.append(f"Selected Doctor: ID {conv.selected_doctor_id}")
    else:
        lines.append("Selected Doctor: (not yet chosen)")

    # Resolve service ID -> name if set
    if conv.selected_service_id:
        svc_entry = next((s for s in service_roster if s["id"] == conv.selected_service_id), None)
        if svc_entry:
            lines.append(f"Selected Service: {svc_entry['name']} (ID: {svc_entry['id']}, {svc_entry.get('duration', 30)} min)")
        else:
            lines.append(f"Selected Service: ID {conv.selected_service_id}")
    else:
        lines.append("Selected Service: (not yet chosen)")

    lines.append(f"Requested Date : {conv.requested_date or '(not yet set)'}")
    lines.append(f"Requested Time : {conv.requested_time or '(not yet set)'}")
    lines.append(f"Customer Name  : {conv.pending_customer_name or '(not yet provided)'}")
    lines.append(f"Customer Phone : {conv.pending_customer_phone or '(not yet provided)'}")
    lines.append(f"Channel        : {conv.channel or 'web_chat'}")
    lines.append("")

    if conv.awaiting_input == "doctor_choice":
        doc_str = ", ".join([f"{d['name']} (id {d['id']})" for d in doctor_roster]) if doctor_roster else "available doctors"
        lines.append(f"INSTRUCTION: Ask the user to choose their preferred doctor from: {doc_str}. Do NOT force service selection.")
    elif conv.awaiting_input == "service_choice":
        svc_str = ", ".join([f"{s['name']} (id {s['id']})" for s in service_roster]) if service_roster else "available services"
        lines.append(f"INSTRUCTION: The user was asked to choose a service from: {svc_str}. Interpret their next reply as answering this question.")
    elif conv.awaiting_input in ["date_choice", "date"]:
        lines.append("INSTRUCTION: Doctor is chosen. Ask ONLY for their preferred appointment date (e.g. 'Tomorrow', specific date). Do NOT ask for a service and do NOT call get_doctors.")
    elif conv.awaiting_input == "time_choice":
        lines.append("INSTRUCTION: The user was just asked to choose an available time slot. Check availability or present open slots for their chosen doctor & date.")
    elif conv.awaiting_input == "confirmation":
        lines.append("INSTRUCTION: The user was just asked to confirm their appointment booking details. Interpret their next reply as confirming or declining this booking first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input == "name":
        lines.append("INSTRUCTION: The user was just asked to provide their full name for the booking. Interpret their next reply as providing their full name first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input == "phone":
        lines.append("INSTRUCTION: The user was just asked to provide their contact phone number for the booking. Interpret their next reply as providing their phone number first, before considering any other intent, unless they clearly change the subject.")
    else:
        lines.append("INSTRUCTION: Use the above context to assist the customer seamlessly without losing previously selected details.")

    # Language Mirroring Mandate
    history_list = [{"role": m.role, "content": m.content} for m in conv.messages]
    last_user_msg = next((m for m in reversed(conv.messages) if m.role == "user"), None)
    cur_text = last_user_msg.content if last_user_msg else ""
    lang = detect_language(cur_text, history_list)

    if lang == "urdu":
        lines.append("LANGUAGE MANDATE: The customer is communicating in Urdu script (اردو). You MUST reply in fluent, natural, polite Urdu (اردو). Do NOT reply in English or Roman Urdu.")
    elif lang == "roman_urdu":
        lines.append("LANGUAGE MANDATE: The customer is communicating in Roman Urdu / Roman English (e.g. 'mera naam ahmed hai', 'dr sara k sath appointment fix kr do', 'kal 11:30 baje', 'theek hai confirm krdo'). You MUST reply in natural, polite Roman Urdu / Roman English (e.g. 'Ji bilkul! Main Dr. Sara Malik ke sath aap ki appointment book kar deta hoon. Barah-e-karam apni pasand ki date batayein...'). Do NOT reply in English or Urdu script.")
    else:
        lines.append("LANGUAGE MANDATE: The customer is communicating in English. Reply in clear, polite English.")

    return "\n".join(lines)


def _resolve_workflow_input(conv: Conversation, user_content: str):
    """
    Dynamically resolve parameters provided in the user message against cached rosters.
    Prevents redundant questions when details like doctor, service, date, or time are already specified.
    Eliminates duplicate availability queries.
    """
    if not user_content or conv.status == "HUMAN":
        return

    text_lower = user_content.lower()
    is_explicit_change = any(w in text_lower for w in ["change", "modify", "reset", "switch", "different", "instead", "another", "actually i want", "actually want", "prefer dr"])

    # ── EARLY INTENT CLASSIFICATION — must run before any state mutation ────
    # If this message is a weekly/day schedule query, skip all booking state
    # mutations (date, time, awaiting_input) to prevent stale state leakage.
    _msg_intent_class = _classify_intent(user_content, {
        "doctor_roster": [{"id": d["id"], "name": d["name"]} for d in BookingService.get_doctors(conv.business_id)],
        "service_roster": [],
    })
    _is_schedule_query = _msg_intent_class in ("DOCTOR_WEEKLY_SCHEDULE", "DOCTOR_DAY_SCHEDULE")
    if _is_schedule_query:
        # Still resolve doctor (user may be asking about a specific doctor)
        doctor_roster = BookingService.get_doctors(conv.business_id)
        matched_doc = _fuzzy_match_roster(user_content, doctor_roster)
        if matched_doc:
            conv.selected_doctor_id = matched_doc["id"]
        # Do NOT update date, time, awaiting_input, or intent for schedule queries.
        db.session.flush()
        return

    # Shared question-detection for the roster guards below.
    # Condition (d): allow a roster match to overwrite state when the message
    # is NOT phrased as a question.  _is_question_query() catches "?"-terminated
    # messages and known inquiry prefixes; the regex additionally catches
    # question-verb openers ("does", "is", "are", "has", "have", "did", "do",
    # "can", "could", "would", "will") that _is_question_query's prefix list
    # does not cover.  Together they distinguish a bare selection ("ahmad") from
    # an information query ("does dr ahmed have experience with kids").
    _msg_is_question = _is_question_query(user_content) or bool(
        re.match(
            r'^(does|is|are|was|were|has|have|can|could|would|will|do|did)\s',
            user_content.lower().strip()
        )
    )

    # 1. Resolve Doctor using cached roster
    doctor_roster = BookingService.get_doctors(conv.business_id)
    matched_doc = _fuzzy_match_roster(user_content, doctor_roster)
    if matched_doc:
        if not conv.selected_doctor_id or is_explicit_change or conv.awaiting_input == "doctor_choice" or not _msg_is_question:
            if conv.selected_doctor_id and conv.selected_doctor_id != matched_doc["id"]:
                conv.requested_time = None
            conv.selected_doctor_id = matched_doc["id"]
            if conv.awaiting_input == "doctor_choice":
                conv.awaiting_input = "date_choice" if not conv.requested_date else None
    else:
        # Check if user mentioned an unregistered/unknown doctor
        _doc_mention_raw = _extract_doctor_mention(user_content)
        if _doc_mention_raw:
            conv.selected_doctor_id = None
            conv.awaiting_input = "doctor_choice"

    # 2. Resolve Service using cached roster
    service_roster = BookingService.get_services(conv.business_id)
    matched_svc = _fuzzy_match_roster(user_content, service_roster)
    if matched_svc:
        if not conv.selected_service_id or is_explicit_change or conv.awaiting_input == "service_choice" or not _msg_is_question:
            if conv.selected_service_id and conv.selected_service_id != matched_svc["id"]:
                conv.requested_time = None
            conv.selected_service_id = matched_svc["id"]
            if conv.awaiting_input == "service_choice":
                conv.awaiting_input = "doctor_choice" if not conv.selected_doctor_id else ("date_choice" if not conv.requested_date else None)
    elif not conv.selected_service_id:
        consultation_keywords = [
            "dont know", "don't know", "not sure", "unsure", "tooth hurts", "toothache", "pain",
            "hurting", "problem", "consultation", "checkup", "consult", "check up", "general appointment",
            "dant", "dard", "masla", "pata nahi", "nahi pata", "maloom nahi", "check karwana", "check krwana",
            "چیک اپ", "چیکپ", "مشورہ", "معائنہ", "دانت", "درد", "پروبلم", "مسئلہ", "نہیں پتا", "نہیں معلوم"
        ]
        if any(w in text_lower for w in consultation_keywords):
            consult_svc = next((s for s in service_roster if "consultation" in s["name"].lower() or "checkup" in s["name"].lower()), service_roster[0] if service_roster else None)
            if consult_svc:
                conv.selected_service_id = consult_svc["id"]
                if conv.awaiting_input in [None, "service_choice"]:
                    conv.awaiting_input = "doctor_choice" if not conv.selected_doctor_id else ("date_choice" if not conv.requested_date else None)

    # 3. Resolve Date using robust date resolver (relative & explicit formats)
    parsed_date = resolve_date_string(user_content, business_id=conv.business_id)
    if parsed_date:
        conv.requested_date = parsed_date
        if conv.awaiting_input == "date_choice":
            conv.awaiting_input = None

    # 4. Resolve Time with authoritative live availability validation
    if not _is_question_query(user_content):
        time_token = _extract_time_token(user_content)
        if time_token:
            if conv.requested_date:
                avail = BookingService.check_availability(
                    business_id=conv.business_id,
                    doctor_id=conv.selected_doctor_id,
                    service_id=conv.selected_service_id,
                    date_str=conv.requested_date
                )
                valid_slots = avail.get("available_slots", []) if avail.get("success") else []
                if time_token in valid_slots:
                    conv.requested_time = time_token
                    if not conv.pending_customer_name:
                        conv.awaiting_input = "name"
                    elif not conv.pending_customer_phone:
                        conv.awaiting_input = "phone"
                    else:
                        conv.awaiting_input = "confirmation"
                else:
                    # Requested slot is NOT available — reject and keep waiting for valid slot
                    conv.requested_time = None
                    conv.awaiting_input = "time_choice"
                    conv.workflow_state = "CHECKING_AVAILABILITY"
            else:
                conv.awaiting_input = "date_choice"

    # Ensure customer info from linked customer record is carried over if not yet populated
    if conv.customer:
        if not conv.pending_customer_name and conv.customer.name:
            conv.pending_customer_name = conv.customer.name
        if not conv.pending_customer_phone and conv.customer.phone:
            conv.pending_customer_phone = conv.customer.phone

    # If in BOOKED state and user initiates a new booking request with new parameters
    if conv.workflow_state == "BOOKED":
        is_ack = any(k in text_lower for k in ["confirm", "yes", "yeah", "sure", "ok", "okay", "haan", "theek", "thanks", "thank you", "done", "alright"])
        if not is_ack and (matched_doc or parsed_date or _extract_time_token(user_content) or any(w in text_lower for w in ["naya", "nayi", "new", "another", "dobara", "doosri"])):
            conv.workflow_state = "COLLECTING_INFO"

    # 5. Extract customer name (excluding doctor & service names)
    _roster_names = [d["name"] for d in doctor_roster] + [s["name"] for s in service_roster]
    cand_name = _extract_name(user_content, roster_names=_roster_names)
    if cand_name and not conv.pending_customer_name:
        conv.pending_customer_name = cand_name

    # 6. Extract customer phone
    phone_match = re.search(r'\b(03\d{2}[- ]?\d{7}|\+92\d{10}|03\d{9})\b', user_content)
    if phone_match and not conv.pending_customer_phone:
        conv.pending_customer_phone = phone_match.group(1).replace(" ", "").replace("-", "")

    # 7. Confirmation triggers
    if any(k in text_lower for k in ["confirm", "confirm booking", "confirm appointment", "yes", "yeah", "sure", "book it", "please book", "go ahead", "haan", "theek hai", "theek", "confirm kar do", "confirm karein"]):
        if conv.pending_customer_name and conv.pending_customer_phone and conv.requested_date and conv.requested_time:
            conv.awaiting_input = "confirmation"

    # 8. Cancel trigger
    cancel_keywords = [
        "cancel booking", "cancel appointment", "cancel my appointment", "cancel my booking",
        "appointment cancel", "booking cancel", "cancel kr do", "cancel kar do", "cancel kar dein",
        "cancel kardein", "cancel krdein", "cancel kardo", "cancel please", "please cancel",
        "کینسل", "منسوخ"
    ]
    if any(k in text_lower for k in cancel_keywords) or (
        "cancel" in text_lower and any(w in text_lower for w in ["appointment", "booking", "slot", "meri", "my"])
    ):
        conv.workflow_state = "START"
        conv.intent = "CANCEL_APPOINTMENT"
        conv.awaiting_input = None
        conv.requested_time = None
        conv.requested_date = None
        conv.selected_doctor_id = None
        conv.selected_service_id = None

    # 9. Reschedule trigger
    if any(k in text_lower for k in ["move it to", "move to", "reschedule", "change time to", "change appointment time", "postpone to"]):
        conv.intent = "RESCHEDULE_APPOINTMENT"
        time_token = _extract_time_token(user_content)
        if time_token:
            conv.requested_time = time_token
        if parsed_date:
            conv.requested_date = parsed_date

    # 10. Inquiry trigger
    has_doc_term = any(w in text_lower for w in ["doctor", "doctors", "dentist", "dentists"])
    has_inquiry_term = any(w in text_lower for w in ["tell", "show", "list", "who", "which", "what", "available", "names", "info", "about"])
    if (has_doc_term and has_inquiry_term) or any(w in text_lower for w in ["what doctors are available", "who are your doctors", "list doctors", "available doctors"]):
        if not any(w in text_lower for w in ["appointment", "book", "reserve", "with", "dr "]):
            conv.intent = "INQUIRY"

    # Default consultation service only when full booking payload is present
    if conv.pending_customer_name and conv.pending_customer_phone and conv.requested_date and conv.requested_time:
        if not conv.selected_service_id:
            def_svc = service_roster[0] if service_roster else None
            if def_svc:
                conv.selected_service_id = def_svc["id"]
        if not conv.selected_doctor_id:
            def_doc = doctor_roster[0] if doctor_roster else None
            if def_doc:
                conv.selected_doctor_id = def_doc["id"]

    # Update intent/state when customer wants appointment or gives parameters
    if conv.intent not in ["CANCEL_APPOINTMENT", "INQUIRY"] and (
        conv.selected_service_id or
        conv.selected_doctor_id or
        conv.requested_date or
        conv.requested_time or
        conv.pending_customer_name or
        conv.pending_customer_phone or
        any(w in text_lower for w in ["appointment", "book", "reserve", "consultation", "checkup", "visit", "dentist", "doctor", "اپائنٹمنٹ", "چیک اپ", "بک"])
    ):
        if conv.intent in [None, "UNKNOWN"]:
            conv.intent = "BOOK_APPOINTMENT"
        if conv.workflow_state in [None, "START"]:
            conv.workflow_state = "COLLECTING_INFO"
        
        # Calculate the next missing field in logical sequence:
        if conv.workflow_state != "BOOKED":
            if conv.selected_doctor_id:
                if not conv.requested_date:
                    conv.awaiting_input = "date_choice"
                elif not conv.requested_time:
                    conv.awaiting_input = "time_choice"
                elif not conv.pending_customer_phone:
                    conv.awaiting_input = "phone"
                elif not conv.pending_customer_name:
                    conv.awaiting_input = "name"
                else:
                    conv.awaiting_input = "confirmation"
            elif conv.selected_service_id:
                if not conv.selected_doctor_id:
                    conv.awaiting_input = "doctor_choice"
                elif not conv.requested_date:
                    conv.awaiting_input = "date_choice"
                elif not conv.requested_time:
                    conv.awaiting_input = "time_choice"
                elif not conv.pending_customer_phone:
                    conv.awaiting_input = "phone"
                elif not conv.pending_customer_name:
                    conv.awaiting_input = "name"
                else:
                    conv.awaiting_input = "confirmation"
            else:
                if any(w in text_lower for w in ["dentist", "doctor"]):
                    conv.awaiting_input = "doctor_choice"
                else:
                    conv.awaiting_input = "service_choice"

    if is_explicit_change and not any(w in text_lower for w in ["dr ", "doctor ", "03", "am", "pm", "baje"]):
        conv.requested_time = None

    db.session.flush()


def _build_ui_action(conv: Conversation) -> Optional[Dict[str, Any]]:
    """
    Build structured UI action payload for frontend rendering following the guided flow:
    Service / Doctor -> Date -> Real Time Slots -> Confirmation
    """
    if conv.status == "HUMAN" or conv.workflow_state == "BOOKED":
        return None

    def _fmt_ampm(t_str: str) -> str:
        try:
            h, m = map(int, str(t_str).split(":"))
            ap = "AM" if h < 12 else "PM"
            h12 = h if (1 <= h <= 12) else (12 if h % 12 == 0 else h % 12)
            return f"{h12:02d}:{m:02d} {ap}"
        except Exception:
            return str(t_str)

    doctor_roster = BookingService.get_doctors(conv.business_id)
    service_roster = BookingService.get_services(conv.business_id)

    # 1. Final Booking Confirmation Card
    if (
        conv.selected_doctor_id and
        conv.requested_date and
        conv.requested_time and
        conv.pending_customer_name and
        conv.pending_customer_phone and
        conv.workflow_state != "BOOKED"
    ):
        doc = next((d for d in doctor_roster if d["id"] == conv.selected_doctor_id), None)
        svc = next((s for s in service_roster if s["id"] == conv.selected_service_id), service_roster[0] if service_roster else None)
        
        try:
            d_obj = datetime.strptime(conv.requested_date, "%Y-%m-%d")
            formatted_date = d_obj.strftime("%A, %B %d, %Y")
        except Exception:
            formatted_date = conv.requested_date

        formatted_time = _fmt_ampm(conv.requested_time)
        svc_price = svc.get("price", 2000.0) if svc else 2000.0

        return {
            "type": "booking_confirmation",
            "interactive_type": "button",
            "title": "Review & Confirm Your Appointment",
            "details": {
                "service_name": svc.get("name", "Dental Consultation") if svc else "Dental Consultation",
                "service_price": f"PKR {svc_price:,.0f}",
                "service_duration": f"{svc.get('duration', 30)} mins" if svc else "30 mins",
                "doctor_name": doc.get("name", "Our Practicing Dentist") if doc else "Our Practicing Dentist",
                "doctor_specialization": doc.get("specialization", "General Dentistry") if doc else "General Dentistry",
                "date": conv.requested_date,
                "formatted_date": formatted_date,
                "time": conv.requested_time,
                "formatted_time": formatted_time,
                "customer_name": conv.pending_customer_name,
                "customer_phone": conv.pending_customer_phone
            },
            "actions": [
                {"id": "action_confirm", "title": "Confirm Booking", "label": "✅ Confirm Booking", "value": "Confirm Appointment", "primary": True},
                {"id": "action_change", "title": "Change Details", "label": "✏️ Change", "value": "I want to change my appointment details", "primary": False},
                {"id": "action_cancel", "title": "Cancel", "label": "❌ Cancel", "value": "Cancel booking", "primary": False}
            ]
        }

    # 2. Time Slot Selection
    if not conv.requested_time and conv.selected_doctor_id and conv.requested_date:
        slots = []
        last_tool = Message.query.filter_by(conversation_id=conv.id, role="tool", tool_name="check_availability").order_by(Message.created_at.desc()).first()
        effective_date_str = conv.requested_date
        
        if last_tool and last_tool.content:
            try:
                data = json.loads(last_tool.content)
                if data.get("available_slots"):
                    slots = data.get("available_slots")
            except Exception:
                pass

        if not slots and conv.requested_date:
            try:
                avail_res = BookingService.check_availability(
                    business_id=conv.business_id,
                    date_str=conv.requested_date,
                    doctor_id=conv.selected_doctor_id,
                    service_id=conv.selected_service_id
                )
                if avail_res.get("available_slots"):
                    slots = avail_res.get("available_slots")
            except Exception:
                pass

        if slots:
            doc = next((d for d in doctor_roster if d["id"] == conv.selected_doctor_id), None)
            time_options = []
            for s in slots:
                try:
                    h = int(s.split(":")[0])
                    period = "Morning" if h < 12 else "Afternoon"
                except Exception:
                    period = "Morning"
                time_options.append({
                    "id": f"slot_{s.replace(':', '')}",
                    "title": _fmt_ampm(s),
                    "label": _fmt_ampm(s),
                    "value": s,
                    "period": period,
                    "description": f"{period} Slot"
                })

            try:
                d_obj = datetime.strptime(effective_date_str, "%Y-%m-%d")
                formatted_d = d_obj.strftime("%A, %B %d, %Y")
            except Exception:
                formatted_d = effective_date_str

            return {
                "type": "time_slot_selection",
                "interactive_type": "list",
                "title": f"Available Time Slots on {formatted_d}",
                "doctor_name": doc.get("name", "") if doc else "",
                "date": effective_date_str,
                "options": time_options
            }

    # 3. Doctor Selection (Must come before Date Selection if doctor is not yet chosen)
    if not conv.selected_doctor_id and (conv.awaiting_input in ["doctor_choice", "doctor"] or conv.selected_service_id):
        if doctor_roster:
            def _format_wk(d_entry):
                wk = d_entry.get("working_days", "")
                if isinstance(wk, list):
                    return ", ".join([x[:3] for x in wk])
                return str(wk)

            return {
                "type": "doctor_selection",
                "interactive_type": "list",
                "title": "Choose Your Doctor",
                "options": [
                    {
                        "id": f"doc_{d['id']}",
                        "name": d["name"],
                        "title": d["name"],
                        "label": d["name"],
                        "value": d["name"],
                        "specialization": d.get("specialization", "General Dentistry"),
                        "description": d.get("specialization") or "General Dentistry",
                        "working_days": _format_wk(d)
                    }
                    for d in doctor_roster
                ]
            }

    # 4. Date Selection (Strictly personalized to the chosen doctor's active weekly schedule)
    if not conv.requested_date and (conv.selected_doctor_id or conv.awaiting_input in ["date_choice", "date"]):
        tz = _get_business_tz(conv.business_id)
        today = datetime.now(tz).date()
        doc = next((d for d in doctor_roster if d["id"] == conv.selected_doctor_id), None) if conv.selected_doctor_id else None

        active_days = []
        if doc:
            if doc.get("weekly_schedule"):
                active_days = [s["day_of_week"] for s in doc["weekly_schedule"] if s.get("is_available")]
            elif doc.get("working_days"):
                wk = doc["working_days"]
                active_days = [d.strip() for d in (wk if isinstance(wk, list) else wk.split(",")) if d.strip()]
        else:
            # If no doctor selected yet, collect union of active days across all active doctors
            for d in doctor_roster:
                if d.get("weekly_schedule"):
                    for s in d["weekly_schedule"]:
                        if s.get("is_available") and s["day_of_week"] not in active_days:
                            active_days.append(s["day_of_week"])
                elif d.get("working_days"):
                    wk = d["working_days"]
                    for day_item in (wk if isinstance(wk, list) else wk.split(",")):
                        if day_item.strip() and day_item.strip() not in active_days:
                            active_days.append(day_item.strip())

        if not active_days:
            active_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

        date_options = []

        # Check if today qualifies as a tappable option (working day with real remaining slots)
        today_day_name = today.strftime("%A")
        if today_day_name in active_days:
            today_str = today.strftime("%Y-%m-%d")
            if doc:
                # Specific doctor selected — check that doctor's remaining slots today
                today_avail = BookingService.check_availability(
                    business_id=conv.business_id, doctor_id=doc["id"], date_str=today_str
                )
                today_has_slots = today_avail.get("success") and len(today_avail.get("available_slots", [])) > 0
            else:
                # No doctor selected — check if ANY active doctor has remaining slots today
                today_has_slots = False
                for d in doctor_roster:
                    d_avail = BookingService.check_availability(
                        business_id=conv.business_id, doctor_id=d["id"], date_str=today_str
                    )
                    if d_avail.get("success") and len(d_avail.get("available_slots", [])) > 0:
                        today_has_slots = True
                        break

            if today_has_slots:
                date_options.append({
                    "id": f"date_{today.strftime('%Y%m%d')}",
                    "title": "Today",
                    "label": "Today",
                    "value": today_str,
                    "day": today_day_name,
                    "display": f"Today ({today_day_name})"
                })

        # Future days (offset 1..21) — existing logic preserved
        offset = 1
        while len(date_options) < 5 and offset <= 21:
            target_d = today + dt_td(days=offset)
            day_name = target_d.strftime("%A")
            offset += 1
            if day_name not in active_days:
                continue

            label = "Tomorrow" if (target_d - today).days == 1 else target_d.strftime("%a, %b %d")
            date_options.append({
                "id": f"date_{target_d.strftime('%Y%m%d')}",
                "title": label,
                "label": label,
                "value": target_d.strftime("%Y-%m-%d"),
                "day": day_name,
                "display": f"{label} ({day_name})"
            })

        doc_title = f" for {doc['name']}" if doc else ""
        return {
            "type": "date_selection",
            "interactive_type": "quick_reply",
            "title": f"Choose an Appointment Date{doc_title}",
            "options": date_options,
            "allow_custom_date": True
        }

    # 5. Service Selection (Step 1 of standard flow when neither service nor doctor is chosen, or awaiting service_choice)
    if not conv.selected_service_id and (conv.awaiting_input == "service_choice" or not conv.selected_doctor_id):
        if service_roster:
            business = _get_business_info(conv.business_id)
            consultation_fee = business.get("consultation_fee", 2000.0)
            options = [
                {
                    "id": f"svc_{s['id']}",
                    "name": s["name"],
                    "title": s["name"],
                    "label": s["name"],
                    "value": s["name"],
                    "duration": s.get("duration", 30),
                    "price": s.get("price", 0),
                    "price_formatted": f"PKR {s['price']:,.0f}" if s.get("price") else "",
                    "description": s.get("description") or f"{s.get('duration', 30)} mins"
                }
                for s in service_roster
            ]
            options.append({
                "id": "svc_consultation",
                "name": "I don't know / I need a consultation",
                "title": "I don't know / I need a consultation",
                "label": "🩺 I don't know / I need a consultation",
                "value": "I don't know, I need a consultation",
                "duration": 30,
                "price": consultation_fee,
                "price_formatted": f"PKR {consultation_fee:,.0f}",
                "description": "General oral checkup & examination"
            })
            return {
                "type": "service_selection",
                "interactive_type": "list",
                "title": "Select a Dental Service",
                "options": options
            }


    return None


class Agent:
    def __init__(self, business_id: int, llm_provider: Optional[str] = None):
        self.business_id = business_id
        self.llm_client = LLMClient(provider=llm_provider)

    def process_message(self, conversation_id: int, user_content: str) -> Dict[str, Any]:
        """
        Process incoming customer message through the central AI Agent.
        Validates backend tools, updates structured state, and returns responses.
        Eliminates redundant second LLM calls by generating deterministic localized tool responses.
        Includes request-level timing and DB query instrumentation.
        """
        RequestCache.clear()
        _local_perf_state.active = True
        _local_perf_state.query_count = 0
        t_start = time.perf_counter()
        llm_call_1_ms = 0.0
        tool_time_ms = 0.0
        llm_call_2_ms = 0.0
        response_gen_ms = 0.0

        conv = db.session.get(Conversation, conversation_id)
        if not conv:
            raise ValueError(f"Conversation {conversation_id} not found.")

        # 1. Persist user message to DB unconditionally & update conversation timestamp
        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=user_content
        )
        conv.updated_at = datetime.now(timezone.utc)
        db.session.add(user_msg)
        db.session.commit()

        # Human receptionist override: if status is HUMAN, bypass AI completely
        if conv.status == "HUMAN":
            total_turn_ms = (time.perf_counter() - t_start) * 1000.0
            return {
                "conversation_id": conv.id,
                "status": "HUMAN",
                "content": "Our human staff has taken over this conversation and will reply shortly. Please wait for their reply or call our reception directly.",
                "executed_tools": [],
                "ui_action": None,
                "metrics": {
                    "db_queries": getattr(_local_perf_state, "query_count", 0),
                    "llm_call_1_ms": 0.0,
                    "tool_time_ms": 0.0,
                    "llm_call_2_ms": 0.0,
                    "response_gen_ms": 0.0,
                    "total_turn_ms": round(total_turn_ms, 2)
                }
            }

        # 2. Intelligently extract booking parameters from user text & update state (using cached rosters)
        _resolve_workflow_input(conv, user_content)

        # 3. Build enriched context and query LLM
        state_dict = _build_state_dict(conv)
        base_prompt = build_system_prompt(self.business_id)
        state_context = _build_state_context(conv)
        system_prompt = f"{base_prompt}\n\n{state_context}"

        # Fetch visible history
        history_msgs = (
            Message.query
            .filter_by(conversation_id=conv.id)
            .order_by(Message.created_at.asc())
            .all()
        )

        formatted_messages = []
        for m in history_msgs:
            if m.role == "tool":
                formatted_messages.append({
                    "role": "tool",
                    "tool_name": m.tool_name or "tool",
                    "tool_call_id": m.tool_call_id or f"call_{m.id}",
                    "content": m.content
                })
            else:
                formatted_messages.append({
                    "role": m.role,
                    "content": m.content
                })

        dispatcher = ToolDispatcher(business_id=self.business_id, conversation_id=conv.id)
        executed_tools = []
        tool_results = []
        iteration = 0

        # LLM Call #1
        t_llm1 = time.perf_counter()
        response = self.llm_client.get_completion(
            system_prompt=system_prompt,
            messages=formatted_messages,
            tools=CANONICAL_TOOLS,
            conversation_state=state_dict
        )
        llm_call_1_ms = (time.perf_counter() - t_llm1) * 1000.0

        # Tool execution & deterministic response generation (Bypasses second LLM call!)
        if response.get("tool_calls"):
            t_tool = time.perf_counter()
            iteration += 1

            for tc in response["tool_calls"]:
                tool_name = tc.get("name")
                tool_args = tc.get("arguments", {})
                tool_call_id = tc.get("id", f"call_{iteration}")

                if tool_name == "book_appointment" and not tool_args.get("idempotency_key"):
                    tool_args["idempotency_key"] = f"conv-{conv.id}-attempt-{uuid.uuid4().hex[:8]}"

                executed_tools.append({"name": tool_name, "args": tool_args})
                self._update_conversation_state(conv, tool_name, tool_args)

                result = dispatcher.execute(tool_name, tool_args)
                tool_results.append({"tool": tool_name, "result": result})

                # If check_availability ran, update state if date has 0 slots or requested time is unavailable
                if tool_name == "check_availability":
                    avail_slots = result.get("available_slots", [])
                    if not avail_slots:
                        # Day is closed or has NO available slots! Clear requested_date & time so subsequent turns prompt for valid date
                        conv.requested_date = None
                        conv.requested_time = None
                        conv.awaiting_input = "date_choice"
                        conv.workflow_state = "CHECKING_AVAILABILITY"
                    elif conv.requested_time and conv.requested_time not in avail_slots:
                        conv.requested_time = None
                        conv.awaiting_input = "time_choice"
                        conv.workflow_state = "CHECKING_AVAILABILITY"

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

            tool_time_ms = (time.perf_counter() - t_tool) * 1000.0

            # Deterministic localized response generation (Instant, no LLM #2!)
            t_gen = time.perf_counter()
            state_dict = _build_state_dict(conv)
            primary_tool = tool_results[0]
            final_content = generate_tool_response(
                tool_name=primary_tool["tool"],
                tool_result=primary_tool["result"],
                conversation_state=state_dict,
                user_message=user_content,
                history_messages=formatted_messages
            )
            response_gen_ms = (time.perf_counter() - t_gen) * 1000.0
        else:
            final_content = response.get(
                "content",
                "Thank you for contacting SmileCare Dental Clinic. How else may I assist you?"
            )
            if any(phrase in final_content.lower() for phrase in ["is not available", "not available", "no available slots", "unavailable"]):
                if conv.requested_time and not any(w in final_content.lower() for w in ["confirmed", "id #"]):
                    conv.requested_time = None
                    conv.awaiting_input = "time_choice"
                    conv.workflow_state = "CHECKING_AVAILABILITY"

            # Safeguard: Never allow unbacked booking confirmation messages if no successful booking tool ran
            confirmation_markers = [
                "your appointment is confirmed",
                "your appointment has been successfully booked",
                "aap ki appointment confirm ho gayi hai",
                "aap ki appointment already confirmed hai",
                "آپ کی اپائنٹمنٹ کامیابی سے بک اور تصدیق",
                "آپ کی اپائنٹمنٹ تصدیق شدہ ہے"
            ]
            has_booking_tool_success = any(
                t.get("name") in ["book_appointment", "reschedule_appointment"]
                for t in executed_tools
            )
            if not has_booking_tool_success and any(m in final_content.lower() for m in confirmation_markers):
                if conv.workflow_state != "BOOKED":
                    avail = BookingService.check_availability(
                        business_id=self.business_id,
                        doctor_id=conv.selected_doctor_id,
                        service_id=conv.selected_service_id,
                        date_str=conv.requested_date
                    )
                    final_content = generate_tool_response(
                        tool_name="check_availability",
                        tool_result=avail,
                        conversation_state=_build_state_dict(conv),
                        user_message=user_content,
                        history_messages=formatted_messages
                    )

        ui_act = _build_ui_action(conv)

        # Save assistant response with persistent interactive_data
        asst_msg = Message(
            conversation_id=conv.id,
            role="assistant",
            content=final_content,
            interactive_data=json.dumps(ui_act) if ui_act else None
        )
        db.session.add(asst_msg)
        db.session.commit()

        total_turn_ms = (time.perf_counter() - t_start) * 1000.0
        db_queries_count = getattr(_local_perf_state, "query_count", 0)

        return {
            "conversation_id": conv.id,
            "status": conv.status,
            "content": final_content,
            "executed_tools": executed_tools,
            "tool_results": tool_results,
            "intent": conv.intent,
            "workflow_state": conv.workflow_state,
            "ui_action": ui_act,
            "metrics": {
                "db_queries": db_queries_count,
                "llm_call_1_ms": round(llm_call_1_ms, 2),
                "tool_time_ms": round(tool_time_ms, 2),
                "llm_call_2_ms": round(llm_call_2_ms, 2),
                "response_gen_ms": round(response_gen_ms, 2),
                "total_turn_ms": round(total_turn_ms, 2)
            }
        }

    def _update_conversation_state(self, conv: Conversation, tool_name: str, args: Dict[str, Any]):
        """
        Persist structured booking state into the conversation record after each tool call.
        """
        if tool_name == "check_availability":
            conv.intent = "BOOK_APPOINTMENT"
            if not conv.requested_time:
                conv.workflow_state = "CHECKING_AVAILABILITY"
                conv.awaiting_input = "time_choice"
            if args.get("date"):
                conv.requested_date = str(args["date"])
            # NOTE: Do NOT set conv.selected_doctor_id from check_availability args.
            # The LLM routinely fills doctor_id with a fallback default (e.g. "doc_id or 1")
            # when no doctor has been selected yet; persisting that default silently
            # corrupts state.  Doctor selection is handled exclusively by
            # _resolve_workflow_input (runs before the LLM call) and by the
            # explicit book_appointment path.
            if args.get("service_id"):
                try:
                    conv.selected_service_id = int(args["service_id"])
                except Exception:
                    pass

        elif tool_name == "book_appointment":
            conv.intent = "BOOK_APPOINTMENT"
            conv.workflow_state = "BOOKED"
            conv.awaiting_input = None
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
            conv.awaiting_input = None
            # Clear stale booking selections when intent changes
            conv.selected_doctor_id = None
            conv.selected_service_id = None
            conv.requested_date = None
            conv.requested_time = None

        elif tool_name == "reschedule_appointment":
            conv.intent = "RESCHEDULE_APPOINTMENT"
            conv.workflow_state = "BOOKED"
            conv.awaiting_input = None
            if args.get("new_date"):
                conv.requested_date = str(args["new_date"])
            if args.get("new_time"):
                conv.requested_time = str(args["new_time"])

        elif tool_name == "human_handoff":
            conv.status = "HUMAN"
            conv.handoff_reason = args.get("reason", "Customer requested human assistance")
            conv.workflow_state = "HANDOFF_REQUESTED"
            conv.awaiting_input = None

        elif tool_name == "get_doctors":
            if not conv.selected_doctor_id:
                conv.awaiting_input = "doctor_choice"

        elif tool_name == "get_services":
            if not conv.selected_service_id:
                conv.awaiting_input = "service_choice"

        elif tool_name == "get_clinic_info":
            conv.awaiting_input = None

        db.session.flush()
