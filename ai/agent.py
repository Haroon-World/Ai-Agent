import json
import re
import uuid
import time
import threading
from datetime import datetime, timezone, date as dt_date, timedelta as dt_td
from typing import Dict, Any, List, Optional
from sqlalchemy import event
from sqlalchemy.engine import Engine
from models import db, Business, Conversation, Message, Customer, Doctor, Service, Appointment
from services.booking_service import BookingService, _get_business, _get_business_info, _get_business_tz, RequestCache
from ai.tools import CANONICAL_TOOLS, ToolDispatcher
from ai.prompts import build_system_prompt
from ai.llm_client import LLMClient, _extract_name, _extract_phone_number, _fuzzy_match_roster, _extract_doctor_mention, resolve_date_string, _classify_intent
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
    "barah": 12, "bara": 12, "baarah": 12, "بارہ": 12, "۱۲": 12,
    # English spoken words
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12
}


def _extract_time_token(text: str) -> Optional[str]:
    """
    Extract standard HH:MM time string from user text supporting:
    - 24-hour and 12-hour: 14:00, 2:00 PM, 2:30 pm, 02:00 PM
    - Spoken STT with dots / variants: 10 a.m., 10 a.m, 10:00 a.m., 2 p.m., 2 p.m
    - Spoken English word numbers: ten am, ten a.m., two pm, ten o'clock, two thirty, half past ten
    - Spoken Roman Urdu / Urdu: 2 baje, do baje, 10 am, subah 10 baje, دو بجے, ۲ بجے
    - Conversational phrases: i want 10, book at 10, fix at 10, slot 10, for 10
    """
    if not text:
        return None
    raw_lower = text.lower().strip()
    # Normalize speech-to-text dotted "a.m." and "p.m." to "am" and "pm"
    norm_text = re.sub(r'\ba\.m\.?', 'am', raw_lower)
    norm_text = re.sub(r'\bp\.m\.?', 'pm', norm_text)

    # 0. Check compound spoken phrases: "half past X", "X thirty"
    num_token_pattern = r'(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|ek|aik|do|doo|teen|tin|chaar|char|paanch|panch|che|chay|chhey|saat|sat|aath|ath|nau|no|das|gyarah|gyara|gyaarah|barah|bara|baarah|ایک|دو|تین|چار|پانچ|چھ|سات|آٹھ|نو|دس|گیارہ|گہرہ|گیرہ|بارہ|[۱-۹]|۱۰|۱۱|۱۲)'
    
    m_half = re.search(r'\bhalf\s+past\s+' + num_token_pattern + r'\b', norm_text)
    if m_half:
        tok = m_half.group(1)
        h = int(tok) if tok.isdigit() else _URDU_ROMAN_NUMBERS.get(tok)
        if h is not None:
            is_pm = any(w in norm_text for w in ["pm", "dopahar", "shaam", "raat", "دوپہر", "شام", "رات"])
            if is_pm and h < 12:
                h += 12
            elif not is_pm and 1 <= h <= 7:
                h += 12
            return f"{h:02d}:30"

    m_thirty = re.search(r'\b' + num_token_pattern + r'\s+thirty\s*(am|pm)?\b', norm_text)
    if m_thirty:
        tok = m_thirty.group(1)
        h = int(tok) if tok.isdigit() else _URDU_ROMAN_NUMBERS.get(tok)
        ampm = m_thirty.group(2)
        if h is not None:
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            elif not ampm and 1 <= h <= 7:
                h += 12
            return f"{h:02d}:30"

    # 1. Match standard HH:MM or HH.MM (e.g. 9:30, 09:30, 14:00, 2:00 PM, 2.00pm)
    m = re.search(r'\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b', norm_text)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        ampm = m.group(3)
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"

    # 2. Match H am / H pm (e.g. 10 am, 2 pm, 12 pm, ten am, two pm)
    m = re.search(r'\b' + num_token_pattern + r'\s*(am|pm)\b', norm_text)
    if m:
        token = m.group(1)
        h = int(token) if token.isdigit() else _URDU_ROMAN_NUMBERS.get(token)
        if h is not None:
            ampm = m.group(2)
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            return f"{h:02d}:00"

    # 3. Match number/word + baje / بجے / بجی / o'clock / oclock (e.g. 2 baje, do baji, دو بجی, دو بجے, ten o'clock)
    baje_pattern = r'(?:baje|bje|bjay|baji|bajy|bajeh|o\'?clock|oclock|بجے|بجی)'
    prefix_pattern = r'(?:(?:din|dopahar|shaam|raat|subah|دن|دوپہر|شام|رات|صبح)(?:\s+(?:ko|ke|ki|کو|کے|کی))?\s+)?'
    m = re.search(prefix_pattern + num_token_pattern + r'\s*' + baje_pattern, norm_text)
    if m:
        token = m.group(1)
        h = int(token) if token.isdigit() else _URDU_ROMAN_NUMBERS.get(token)
        if h is not None:
            is_pm = any(w in norm_text for w in ["pm", "dopahar", "shaam", "raat", "دوپہر", "شام", "رات", "دن"])
            is_am = any(w in norm_text for w in ["am", "subah", "صبح"])
            if is_pm and h < 12:
                h += 12
            elif is_am and h == 12:
                h = 0
            elif not is_pm and not is_am and 1 <= h <= 7:
                h += 12
            return f"{h:02d}:00"

    # 4. Match conversational phrasing like "want 10", "for 10", "book 10", "slot 10", "fix at 10", "at 2", "after 12", "ko 2"
    m = re.search(r'\b(?:after|before|at|around|from|past|ko|ke|ki|pe|par|کو|پر|کے|کی|want|for|book|fix|slot|time)\s+' + num_token_pattern + r'(?:\s*(am|pm))?\b', norm_text)
    if m:
        token = m.group(1)
        ampm = m.group(2)
        h = int(token) if token.isdigit() else _URDU_ROMAN_NUMBERS.get(token)
        if h is not None:
            is_pm = (ampm == "pm") or any(w in norm_text for w in ["pm", "dopahar", "shaam", "raat", "دوپہر", "شام", "رات", "دن"])
            is_am = (ampm == "am") or any(w in norm_text for w in ["am", "subah", "صبح"])
            if is_pm and h < 12:
                h += 12
            elif is_am and h == 12:
                h = 0
            elif not is_pm and not is_am and 1 <= h <= 7:
                h += 12
            return f"{h:02d}:00"

    # 5. Fallback for clean isolated single/double digit or word number (e.g. user just said "10" or "ten")
    cleaned = norm_text.strip(". ,!?:")
    if cleaned.isdigit():
        val = int(cleaned)
        if 0 <= val <= 23:
            h = val
            if 1 <= h <= 7:
                h += 12
            return f"{h:02d}:00"
    elif cleaned in _URDU_ROMAN_NUMBERS:
        h = _URDU_ROMAN_NUMBERS[cleaned]
        if 1 <= h <= 7:
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
    if conv.selected_doctor_id:
        service_roster = BookingService.get_services(conv.business_id, doctor_id=conv.selected_doctor_id)
    else:
        service_roster = BookingService.get_services(conv.business_id)

    # Validate that selected_service_id actually belongs to selected_doctor_id
    if conv.selected_doctor_id and conv.selected_service_id:
        if not any(s["id"] == conv.selected_service_id for s in service_roster):
            all_clinic_svcs = BookingService.get_services(conv.business_id)
            is_consult = any("consultation" in s.get("name", "").lower() or "checkup" in s.get("name", "").lower() for s in all_clinic_svcs if s.get("id") == conv.selected_service_id)
            if is_consult and conv.intent != "RESCHEDULE_APPOINTMENT":
                doc_consult = next((s for s in service_roster if "consultation" in s["name"].lower() or "checkup" in s["name"].lower()), service_roster[0] if service_roster else None)
                if doc_consult:
                    conv.selected_service_id = doc_consult["id"]
                # If doctor has no specific services registered, keep the consultation service without wiping
            else:
                conv.selected_service_id = None
                conv.requested_time = None
                if conv.awaiting_input not in ["doctor_choice", "doctor"]:
                    conv.awaiting_input = "service_choice"
            db.session.flush()

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

    active_appt_id = None
    active_appt_doc_id = None
    active_appt_svc_id = None
    active_appt_date = None
    active_appt_time = None
    try:
        from models import Appointment, Customer
        active_appt = Appointment.query.filter(
            Appointment.business_id == conv.business_id,
            Appointment.status == "CONFIRMED"
        ).filter(
            (Appointment.idempotency_key.like(f"conv-{conv.id}-%")) |
            ((Appointment.customer_id == conv.customer_id) if conv.customer_id else False)
        ).order_by(Appointment.id.desc()).first()

        if not active_appt and conv.pending_customer_phone:
            cust = Customer.query.filter_by(business_id=conv.business_id, phone=conv.pending_customer_phone.strip()).first()
            if cust:
                active_appt = Appointment.query.filter_by(
                    business_id=conv.business_id, customer_id=cust.id, status="CONFIRMED"
                ).order_by(Appointment.id.desc()).first()

        if active_appt:
            active_appt_id = active_appt.id
            active_appt_doc_id = active_appt.doctor_id
            active_appt_svc_id = active_appt.service_id
            active_appt_date = active_appt.appointment_date
            active_appt_time = active_appt.appointment_time
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
        "active_appointment_id": active_appt_id,
        "active_appointment_doctor_id": active_appt_doc_id,
        "active_appointment_service_id": active_appt_svc_id,
        "active_appointment_date": active_appt_date,
        "active_appointment_time": active_appt_time,
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
    if conv.selected_doctor_id:
        service_roster = BookingService.get_services(conv.business_id, doctor_id=conv.selected_doctor_id)
    else:
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

    # If user previously cancelled an appointment and is now sending a new request, reset state cleanly
    cancel_keywords = [
        "cancel booking", "cancel appointment", "cancel my appointment", "cancel my booking",
        "appointment cancel", "booking cancel", "cancel kr do", "cancel kar do", "cancel kar dein",
        "cancel kardein", "cancel krdein", "cancel kardo", "cancel please", "please cancel",
        "کینسل", "منسوخ"
    ]
    is_cancel_msg = any(k in text_lower for k in cancel_keywords) or (
        "cancel" in text_lower and any(w in text_lower for w in ["appointment", "booking", "slot", "meri", "my"])
    )
    if conv.intent == "CANCEL_APPOINTMENT" and not is_cancel_msg:
        conv.intent = "BOOK_APPOINTMENT"
        conv.workflow_state = "START"
        conv.selected_doctor_id = None
        conv.selected_service_id = None
        conv.requested_date = None
        conv.requested_time = None
        conv.awaiting_input = None

    # If in BOOKED state and user initiates a new message (inquiry, new booking, doctor question, etc.)
    if conv.workflow_state == "BOOKED":
        is_ack = any(k in text_lower for k in ["confirm", "yes", "yeah", "sure", "ok", "okay", "haan", "theek", "thanks", "thank you", "done", "alright"])
        if not is_ack and not is_cancel_msg:
            contact_update_phrases = [
                "change my mobile", "change my number", "change my phone", "change mobile number", "change phone number",
                "update my mobile", "update my number", "update my phone", "update phone", "update mobile",
                "wrong number", "wrong mobile", "wrong phone", "number was of", "number was wrong", "mobile was of",
                "correct my number", "correct my phone", "correct my name", "change my name", "update my name",
                "write my mobile", "write my phone", "write my number", "mera number change", "number badal", "phone change",
                "change number", "change name", "update contact", "change contact"
            ]
            is_contact_update = any(k in text_lower for k in contact_update_phrases) or (
                _extract_phone_number(user_content) and any(w in text_lower for w in ["change", "update", "correct", "wrong", "instead", "brother", "sister", "badal"])
            )
            reschedule_keywords = [
                "move it to", "move to", "reschedule", "change time to", "change appointment time", "postpone to",
                "change my doctor", "change doctor", "different doctor", "switch doctor", "switch my doctor",
                "change my appointment", "change appointment", "update my appointment"
            ]
            is_reschedule = any(k in text_lower for k in reschedule_keywords)
            if not is_contact_update and not is_reschedule:
                conv.workflow_state = "START"
                conv.intent = None
                conv.selected_doctor_id = None
                conv.selected_service_id = None
                conv.requested_date = None
                conv.requested_time = None
                conv.awaiting_input = None

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
            if conv.selected_doctor_id != matched_doc["id"] or conv.intent == "RESCHEDULE_APPOINTMENT":
                conv.requested_time = None
                # Doctor selected or changed mid-conversation or in reschedule: revalidate selected_service_id against new doctor's roster
                if conv.selected_service_id:
                    new_doc_services = BookingService.get_services(conv.business_id, doctor_id=matched_doc["id"])
                    all_clinic_svcs = BookingService.get_services(conv.business_id)
                    is_consult = any("consultation" in s.get("name", "").lower() or "checkup" in s.get("name", "").lower() for s in all_clinic_svcs if s.get("id") == conv.selected_service_id)
                    if is_consult and conv.intent != "RESCHEDULE_APPOINTMENT":
                        doc_consult = next((s for s in new_doc_services if "consultation" in s["name"].lower() or "checkup" in s["name"].lower()), new_doc_services[0] if new_doc_services else None)
                        if doc_consult:
                            conv.selected_service_id = doc_consult["id"]
                    elif not any(s["id"] == conv.selected_service_id for s in new_doc_services):
                        conv.selected_service_id = None
                        conv.awaiting_input = "service_choice"
            conv.selected_doctor_id = matched_doc["id"]
            if conv.awaiting_input == "doctor_choice":
                if conv.intent == "RESCHEDULE_APPOINTMENT" and not conv.selected_service_id:
                    conv.awaiting_input = "service_choice"
                elif not conv.selected_service_id and not conv.requested_date:
                    is_urdu_flow = any('\u0600' <= ch <= '\u06FF' for ch in user_content) or (
                        any(w in text_lower for w in ["kr do", "kar do", "kar dein", "kr dein", "k sath", "ke sath"]) and
                        any(w in text_lower for w in ["appointment fix", "appointment book", "book kr", "fix kr"])
                    )
                    if is_urdu_flow:
                        conv.awaiting_input = "date_choice"
                    else:
                        conv.awaiting_input = "service_choice"
                else:
                    conv.awaiting_input = "date_choice" if not conv.requested_date else None
    else:
        # Check if user mentioned an unregistered/unknown doctor
        _doc_mention_raw = _extract_doctor_mention(user_content)
        if _doc_mention_raw:
            conv.selected_doctor_id = None
            conv.selected_service_id = None
            conv.awaiting_input = "doctor_choice"

    # 2. Resolve Service using cached roster (doctor-scoped if doctor is chosen)
    active_doc_id = matched_doc["id"] if matched_doc else conv.selected_doctor_id
    if active_doc_id:
        service_roster = BookingService.get_services(conv.business_id, doctor_id=active_doc_id)
    else:
        service_roster = BookingService.get_services(conv.business_id)

    has_specific_service_mention = any(k in text_lower for k in [
        "dental checkup", "dant ki check up", "دانت کی چیک اپ", "scaling", "cleaning", "whitening", "extraction", "root canal", "braces"
    ])
    is_general_consultation = any(w in text_lower for w in [
        "dont know", "don't know", "not sure", "unsure", "need a consultation", "i need a consultation",
        "general consultation", "normal checkup", "normal check up", "general checkup", "routine checkup",
        "regular checkup", "doctor consultation", "medical checkup", "just checkup",
        "pata nahi", "nahi pata", "maloom nahi", "check karwana", "check krwana",
        "نہیں پتا", "نہیں معلوم"
    ]) and not has_specific_service_mention

    matched_svc = _fuzzy_match_roster(user_content, service_roster)
    if not matched_svc and active_doc_id and not matched_doc:
        all_services = BookingService.get_services(conv.business_id)
        matched_svc = _fuzzy_match_roster(user_content, all_services)
    if matched_svc:
        if not conv.selected_service_id or is_explicit_change or conv.awaiting_input == "service_choice" or not _msg_is_question:
            if conv.selected_service_id and conv.selected_service_id != matched_svc["id"]:
                conv.requested_time = None
            conv.selected_service_id = matched_svc["id"]
            if matched_svc.get("doctor_id") and not matched_doc:
                if not is_general_consultation:
                    if conv.selected_doctor_id and conv.selected_doctor_id != matched_svc["doctor_id"]:
                        conv.requested_time = None
                    conv.selected_doctor_id = matched_svc["doctor_id"]
            if conv.awaiting_input == "service_choice":
                conv.awaiting_input = "doctor_choice" if not conv.selected_doctor_id else ("date_choice" if not conv.requested_date else None)
    elif not conv.selected_service_id:
        consultation_keywords = [
            "dont know", "don't know", "not sure", "unsure", "tooth hurts", "toothache", "pain",
            "hurting", "problem", "consultation", "checkup", "consult", "check up", "general appointment",
            "normal checkup", "normal check up", "general checkup", "general check up", "routine checkup",
            "regular checkup", "doctor consultation", "medical checkup", "just checkup", "aam checkup",
            "check up karwana", "doctor ko dikhana", "dikhana hai", "dikhaana hai",
            "dant", "dard", "masla", "pata nahi", "nahi pata", "maloom nahi", "check karwana", "check krwana",
            "چیک اپ", "چیکپ", "مشورہ", "معائنہ", "دانت", "درد", "پروبلم", "مسئلہ", "نہیں پتا", "نہیں معلوم"
        ]
        if any(w in text_lower for w in consultation_keywords):
            consult_svc = next((s for s in service_roster if "consultation" in s["name"].lower() or "checkup" in s["name"].lower()), service_roster[0] if service_roster else None)
            if consult_svc:
                conv.selected_service_id = consult_svc["id"]
                if conv.awaiting_input == "service_choice":
                    conv.awaiting_input = "doctor_choice" if not conv.selected_doctor_id else ("date_choice" if not conv.requested_date else None)
                # In a polyclinic, consultation is doctor-agnostic until customer chooses a doctor.
                # Do NOT auto-assign conv.selected_doctor_id!
    if not conv.selected_doctor_id and (conv.selected_service_id or conv.intent in ["BOOK_APPOINTMENT", "RESCHEDULE_APPOINTMENT"]):
        if not conv.awaiting_input or conv.awaiting_input in ["service_choice", "date_choice", "date"]:
            conv.awaiting_input = "doctor_choice"

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
    phone_found = _extract_phone_number(user_content)
    if phone_found and not conv.pending_customer_phone:
        conv.pending_customer_phone = phone_found

    # Contact details update trigger (name or phone update)
    contact_update_phrases = [
        "change my mobile", "change my number", "change my phone", "change mobile number", "change phone number",
        "update my mobile", "update my number", "update my phone", "update phone", "update mobile",
        "wrong number", "wrong mobile", "wrong phone", "number was of", "number was wrong", "mobile was of",
        "correct my number", "correct my phone", "correct my name", "change my name", "update my name",
        "write my mobile", "write my phone", "write my number", "mera number change", "number badal", "phone change",
        "change number", "change name", "update contact", "change contact"
    ]
    is_contact_update = any(k in text_lower for k in contact_update_phrases) or (
        phone_found and any(w in text_lower for w in ["change", "update", "correct", "wrong", "instead", "brother", "sister", "badal"])
    )
    if is_contact_update and not parsed_date and not _extract_time_token(user_content):
        conv.intent = "UPDATE_CUSTOMER_DETAILS"
        if phone_found:
            conv.pending_customer_phone = phone_found
        if cand_name and any(w in text_lower for w in ["name", "naam"]):
            conv.pending_customer_name = cand_name

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
    is_cancel_msg = any(k in text_lower for k in cancel_keywords) or (
        "cancel" in text_lower and any(w in text_lower for w in ["appointment", "booking", "slot", "meri", "my"])
    )
    if is_cancel_msg:
        conv.workflow_state = "START"
        conv.intent = "CANCEL_APPOINTMENT"
        conv.awaiting_input = None
        conv.requested_time = None
        conv.requested_date = None
        conv.selected_doctor_id = None
        conv.selected_service_id = None

    # 9. Reschedule trigger
    reschedule_keywords = [
        "move it to", "move to", "reschedule", "change time to", "change appointment time", "postpone to",
        "change my doctor", "change doctor", "different doctor", "switch doctor", "switch my doctor",
        "change my appointment", "change appointment", "update my appointment"
    ]
    if any(k in text_lower for k in reschedule_keywords):
        conv.intent = "RESCHEDULE_APPOINTMENT"
        if conv.workflow_state == "BOOKED":
            conv.workflow_state = "COLLECTING_INFO"
        if any(w in text_lower for w in ["change my doctor", "change doctor", "different doctor", "switch doctor", "switch my doctor"]):
            if not matched_doc:
                conv.selected_doctor_id = None
                conv.requested_time = None
                conv.awaiting_input = "doctor_choice"
        time_token = _extract_time_token(user_content)
        if time_token:
            conv.requested_time = time_token
        if parsed_date:
            conv.requested_date = parsed_date

    # 10. Inquiry trigger
    has_doc_term = any(w in text_lower for w in ["doctor", "doctors", "dentist", "dentists"])
    has_inquiry_term = any(w in text_lower for w in ["tell", "show", "list", "who", "which", "what", "available", "names", "info", "about"])
    is_doc_inquiry = ((has_doc_term and has_inquiry_term) or any(w in text_lower for w in ["what doctors are available", "who are your doctors", "list doctors", "available doctors"])) and not any(w in text_lower for w in ["appointment", "book", "reserve", "booking", "fix"])

    has_svc_inquiry_phrase = any(phrase in text_lower for phrase in [
        "what does he provide", "what does he provides", "what does she provide", "what does she provides",
        "what do they provide", "what do you provide", "what does dr", "what does doctor",
        "what services", "which services", "services offered", "services does", "services provide",
        "what does he offer", "what does she offer", "what do they offer", "what does he do", "what does she do",
        "tell me services", "show services", "list services", "prices", "pricing", "charges", "fees", "cost",
        "kya provide", "kya service", "kya services", "kya karte hain", "kya karti hain", "کیا سروس", "کیا فراہم"
    ]) and not any(w in text_lower for w in ["book", "reserve", "اپائنٹمنٹ", "بک"])

    if is_doc_inquiry or has_svc_inquiry_phrase:
        conv.intent = "INQUIRY"
        conv.awaiting_input = None

    # Default consultation service only when full booking payload is present AND doctor is selected
    if conv.selected_doctor_id and conv.pending_customer_name and conv.pending_customer_phone and conv.requested_date and conv.requested_time:
        if not conv.selected_service_id:
            doc_services = BookingService.get_services(conv.business_id, doctor_id=conv.selected_doctor_id)
            if doc_services:
                conv.selected_service_id = doc_services[0]["id"]

    # Update intent/state when customer wants appointment or gives parameters
    if conv.intent not in ["INQUIRY"] and not is_cancel_msg and (
        conv.selected_service_id or
        conv.selected_doctor_id or
        conv.requested_date or
        conv.requested_time or
        conv.pending_customer_name or
        conv.pending_customer_phone or
        any(w in text_lower for w in ["appointment", "book", "reserve", "consultation", "checkup", "visit", "dentist", "doctor", "اپائنٹمنٹ", "چیک اپ", "بک"])
    ):
        if conv.intent in [None, "UNKNOWN", "CANCEL_APPOINTMENT"]:
            conv.intent = "BOOK_APPOINTMENT"
        if conv.workflow_state in [None, "START", "COMPLETED"]:
            conv.workflow_state = "COLLECTING_INFO"
        
        # Calculate the next missing field in logical sequence:
        if conv.workflow_state != "BOOKED":
            if conv.selected_doctor_id:
                if conv.intent == "RESCHEDULE_APPOINTMENT" and not conv.selected_service_id:
                    conv.awaiting_input = "service_choice"
                elif not conv.selected_service_id and not conv.requested_date:
                    is_urdu_flow = any('\u0600' <= ch <= '\u06FF' for ch in user_content) or (
                        any(w in text_lower for w in ["kr do", "kar do", "kar dein", "kr dein", "k sath", "ke sath"]) and
                        any(w in text_lower for w in ["appointment fix", "appointment book", "book kr", "fix kr"])
                    )
                    if is_urdu_flow:
                        conv.awaiting_input = "date_choice"
                    else:
                        conv.awaiting_input = "service_choice"
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
                conv.awaiting_input = "doctor_choice"

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
    if conv.selected_doctor_id:
        service_roster = BookingService.get_services(conv.business_id, doctor_id=conv.selected_doctor_id)
    else:
        service_roster = BookingService.get_services(conv.business_id)

    # 1. Final Booking Confirmation Card
    if (
        conv.intent not in ["INQUIRY", "CANCEL_APPOINTMENT", "UPDATE_CUSTOMER_DETAILS"] and
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
                "service_name": svc.get("name", "Consultation") if svc else "Consultation",
                "service_price": f"PKR {svc_price:,.0f}",
                "service_duration": f"{svc.get('duration', 30)} mins" if svc else "30 mins",
                "doctor_name": doc.get("name", "Our Practicing Specialist") if doc else "Our Practicing Specialist",
                "doctor_specialization": doc.get("specialization", "Specialist") if doc else "Specialist",
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
    if conv.intent not in ["INQUIRY", "CANCEL_APPOINTMENT", "UPDATE_CUSTOMER_DETAILS"] and not conv.requested_time and conv.selected_doctor_id and conv.requested_date:
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
                        "specialization": d.get("specialization", "Specialist"),
                        "description": d.get("specialization") or "Specialist",
                        "working_days": _format_wk(d)
                    }
                    for d in doctor_roster
                ]
            }

    # 4. Date Selection (Strictly personalized to the chosen doctor's active weekly schedule)
    if conv.intent not in ["INQUIRY", "CANCEL_APPOINTMENT", "UPDATE_CUSTOMER_DETAILS"] and not conv.requested_date and conv.awaiting_input != "service_choice" and (conv.selected_doctor_id or conv.awaiting_input in ["date_choice", "date"]):
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

    # 5. Service Selection (Only during booking flow or when awaiting service_choice; never on casual greetings)
    if not conv.selected_service_id and (conv.awaiting_input == "service_choice" or (conv.intent in ["BOOK_APPOINTMENT", "RESCHEDULE_APPOINTMENT"] and not conv.selected_doctor_id)):
        if service_roster:
            business = _get_business_info(conv.business_id)
            consultation_fee = business.get("consultation_fee", 2000.0)
            doc = next((d for d in doctor_roster if d["id"] == conv.selected_doctor_id), None) if conv.selected_doctor_id else None
            title = f"Select a Service with {doc['name']}" if doc else "Select a Service"
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
            has_consultation = any("consultation" in s["name"].lower() or "checkup" in s["name"].lower() for s in service_roster)
            if not has_consultation:
                options.append({
                    "id": "svc_consultation",
                    "name": "I don't know / I need a consultation",
                    "title": "I don't know / I need a consultation",
                    "label": "🩺 I don't know / I need a consultation",
                    "value": "I don't know, I need a consultation",
                    "duration": 30,
                    "price": consultation_fee,
                    "price_formatted": f"PKR {consultation_fee:,.0f}",
                    "description": "General consultation & medical checkup"
                })
            return {
                "type": "service_selection",
                "interactive_type": "list",
                "title": title,
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

                if tool_name in ["book_appointment", "reschedule_appointment"] and result.get("success"):
                    cust_id = result.get("customer_id") or (result.get("appointment") or {}).get("customer_id")
                    if cust_id:
                        conv.customer_id = int(cust_id)
                elif tool_name == "update_customer_details" and result.get("success"):
                    cust_id = result.get("customer_id") or (result.get("customer") or {}).get("id")
                    if cust_id:
                        conv.customer_id = int(cust_id)

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
            booking_tool = next((tr for tr in tool_results if tr["tool"] in ["book_appointment", "reschedule_appointment"] and tr["result"].get("success")), None)
            primary_tool = booking_tool or tool_results[0]
            final_content = generate_tool_response(
                tool_name=primary_tool["tool"],
                tool_result=primary_tool["result"],
                conversation_state=state_dict,
                user_message=user_content,
                history_messages=formatted_messages
            )
            response_gen_ms = (time.perf_counter() - t_gen) * 1000.0
        else:
            clinic_display_name = _get_business_info(self.business_id).get("name", "ClinicConnect Polyclinic")
            final_content = response.get(
                "content",
                f"Thank you for contacting {clinic_display_name}. How else may I assist you?"
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
                    real_appt = Appointment.query.filter(
                        Appointment.business_id == self.business_id,
                        Appointment.status == "CONFIRMED"
                    ).filter(
                        (Appointment.idempotency_key.like(f"conv-{conv.id}-%")) |
                        ((Appointment.customer_id == conv.customer_id) if conv.customer_id else False)
                    ).order_by(Appointment.id.desc()).first()

                    if real_appt:
                        conv.workflow_state = "BOOKED"
                        conv.awaiting_input = None
                        final_content = generate_tool_response(
                            tool_name="book_appointment",
                            tool_result={"success": True, "appointment": real_appt.to_dict()},
                            conversation_state=_build_state_dict(conv),
                            user_message=user_content,
                            history_messages=formatted_messages
                        )
                    else:
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

        # If assistant explicitly acknowledged/selected a slot (e.g. "I have selected the 10:00 AM slot")
        # ensure conv.requested_time is synchronized so time_slot_selection is not re-emitted
        if not conv.requested_time and conv.workflow_state != "BOOKED":
            m_asst_time = re.search(r'\b(?:selected|booked|fix(?:ed)?|choose|chosen)\s+(?:the\s+)?(\d{1,2}[:.]\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))\b', final_content, re.IGNORECASE)
            if m_asst_time:
                cand_t = _extract_time_token(m_asst_time.group(1))
                if cand_t:
                    conv.requested_time = cand_t
                    if conv.awaiting_input == "time_choice":
                        conv.awaiting_input = "name" if not conv.pending_customer_name else ("phone" if not conv.pending_customer_phone else "confirmation")

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
            if args.get("new_doctor_id"):
                try:
                    conv.selected_doctor_id = int(args["new_doctor_id"])
                except Exception:
                    pass
            if args.get("new_service_id"):
                try:
                    conv.selected_service_id = int(args["new_service_id"])
                except Exception:
                    pass

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
