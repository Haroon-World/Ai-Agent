import json
import re
import uuid
from datetime import datetime, date as dt_date, timedelta as dt_td
from typing import Dict, Any, List, Optional
from models import db, Business, Conversation, Message, Customer, Doctor, Service
from ai.tools import CANONICAL_TOOLS, ToolDispatcher
from ai.prompts import build_system_prompt
from ai.llm_client import LLMClient, _extract_name, _fuzzy_match_roster, resolve_date_string



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
        "is anything", "are any", "what are", "who is", "show me", "tell me",
        "is this", "is that", "available", "after", "before", "free", "any slot"
    ]
    return any(qp in lower for qp in question_prefixes)


def _extract_time_token(text: str) -> Optional[str]:
    """Extract standard HH:MM time string from user text supporting ':', '.', 'am/pm', and bare numbers after prepositions like 'after 12'."""
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
    # Match spoken forms like 10 am, 2 pm, 12 pm, 10 baje, 10 bje
    m = re.search(r'\b(\d{1,2})\s*(baje|bje|bjay|am|pm)\b', text, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "pm" and h < 12:
            h += 12
        elif unit == "am" and h == 12:
            h = 0
        elif unit in ["baje", "bje", "bjay"] and 1 <= h <= 7:
            h += 12
        return f"{h:02d}:00"
    # Match bare number after time prepositions like "after 12", "before 2", "at 10"
    m = re.search(r'\b(?:after|before|at|around|from|past)\s+(\d{1,2})\b', text, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 7:
            h += 12
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

    # Real doctor/service roster for this business, so reply-interpretation
    # (fuzzy name matching, etc.) matches against actual DB data instead of
    # hardcoded names that break on typos/spelling variants.
    doctor_roster = [
        {"id": d.id, "name": d.name, "specialization": d.specialization}
        for d in Doctor.query.filter_by(business_id=conv.business_id).all()
    ]
    service_roster = [
        {"id": s.id, "name": s.name}
        for s in Service.query.filter_by(business_id=conv.business_id).all()
    ]

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

    if conv.awaiting_input == "doctor_choice":
        doctors = Doctor.query.filter_by(business_id=conv.business_id, is_active=True).all()
        doc_str = ", ".join([f"{d.name} (id {d.id})" for d in doctors]) if doctors else "available doctors"
        lines.append(f"INSTRUCTION: The user was just asked to choose a doctor from: {doc_str}. Interpret their next reply as answering this question first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input == "service_choice":
        services = Service.query.filter_by(business_id=conv.business_id).all()
        svc_str = ", ".join([f"{s.name} (id {s.id})" for s in services]) if services else "available services"
        lines.append(f"INSTRUCTION: The user was just asked to choose a service from: {svc_str}. Interpret their next reply as answering this question first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input in ["date_choice", "date"]:
        lines.append("INSTRUCTION: The user was just asked to choose an appointment date. Interpret their next reply as choosing a date first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input == "time_choice":
        lines.append("INSTRUCTION: The user was just asked to choose an available time slot. Interpret their next reply as choosing a time slot first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input == "confirmation":
        lines.append("INSTRUCTION: The user was just asked to confirm their appointment booking details. Interpret their next reply as confirming or declining this booking first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input == "name":
        lines.append("INSTRUCTION: The user was just asked to provide their full name for the booking. Interpret their next reply as providing their full name first, before considering any other intent, unless they clearly change the subject.")
    elif conv.awaiting_input == "phone":
        lines.append("INSTRUCTION: The user was just asked to provide their contact phone number for the booking. Interpret their next reply as providing their phone number first, before considering any other intent, unless they clearly change the subject.")
    else:
        lines.append(
            "INSTRUCTION: Use the above context when interpreting the customer's next message. "
            "If the customer provides a time (e.g. '9:30') and a date is already set in context, "
            "proceed directly to booking without asking for the date again. "
            "If a doctor is already selected, remember that selection. "
            "If customer name or phone is already provided in context, do not ask for it again. "
            "Never lose information that is already stored in context."
        )
    return "\n".join(lines)


def _resolve_workflow_input(conv: Conversation, user_content: str):
    """
    Dynamically resolve parameters provided in the user message against the database for conv.business_id.
    Prevents redundant questions when details like doctor, service, date, or time are already specified.
    """
    if not user_content or conv.status == "HUMAN":
        return

    text_lower = user_content.lower()
    is_explicit_change = any(w in text_lower for w in ["change", "modify", "reset", "switch", "different", "instead", "another", "actually i want", "actually want", "prefer dr"])

    # 1. Resolve Doctor from DB for current business_id — fuzzy-matched so
    # spelling variants/typos (e.g. "dr ahmad" for "Dr. Ahmed Khan") still
    # resolve correctly instead of requiring an exact substring match.
    doctors = Doctor.query.filter_by(business_id=conv.business_id, is_active=True).all()
    doctor_roster = [{"id": d.id, "name": d.name} for d in doctors]
    matched_doc = _fuzzy_match_roster(user_content, doctor_roster)
    if matched_doc:
        if not conv.selected_doctor_id or is_explicit_change or conv.awaiting_input == "doctor_choice":
            if conv.selected_doctor_id and conv.selected_doctor_id != matched_doc["id"]:
                conv.requested_time = None
            conv.selected_doctor_id = matched_doc["id"]
            if conv.awaiting_input == "doctor_choice":
                conv.awaiting_input = "date_choice" if not conv.requested_date else None

    # 2. Resolve Service from DB for current business_id — same fuzzy
    # matching, so a typo'd or partially-remembered service name resolves
    # instead of silently failing to update state.
    services = Service.query.filter_by(business_id=conv.business_id, is_active=True).all()
    service_roster = [{"id": s.id, "name": s.name} for s in services]
    matched_svc = _fuzzy_match_roster(user_content, service_roster)
    if matched_svc:
        if not conv.selected_service_id or is_explicit_change or conv.awaiting_input == "service_choice":
            if conv.selected_service_id and conv.selected_service_id != matched_svc["id"]:
                conv.requested_time = None
            conv.selected_service_id = matched_svc["id"]
            if conv.awaiting_input == "service_choice":
                conv.awaiting_input = "doctor_choice" if not conv.selected_doctor_id else ("date_choice" if not conv.requested_date else None)
    elif not conv.selected_service_id and any(w in text_lower for w in ["dont know", "don't know", "not sure", "unsure", "tooth hurts", "toothache", "pain", "hurting", "problem", "consultation", "checkup", "consult"]):
        consult_svc = Service.query.filter(
            Service.business_id == conv.business_id,
            Service.is_active == True,
            (Service.name.ilike("%consultation%") | Service.name.ilike("%checkup%"))
        ).first() or (services[0] if services else None)
        if consult_svc:
            conv.selected_service_id = consult_svc.id
            if conv.awaiting_input in [None, "service_choice"]:
                conv.awaiting_input = "doctor_choice" if not conv.selected_doctor_id else ("date_choice" if not conv.requested_date else None)

    # 3. Resolve Date using robust date resolver (relative & explicit formats)
    parsed_date = resolve_date_string(user_content, business_id=conv.business_id)
    if parsed_date:
        conv.requested_date = parsed_date
        if conv.awaiting_input == "date_choice":
            conv.awaiting_input = None

    # 4. Resolve Time (only if not a question query like "is there any slot after 12")
    if not _is_question_query(user_content):
        time_token = _extract_time_token(user_content)
        if time_token:
            conv.requested_time = time_token
            if not conv.pending_customer_name:
                conv.awaiting_input = "name"
            elif not conv.pending_customer_phone:
                conv.awaiting_input = "phone"
            else:
                conv.awaiting_input = "confirmation"

    # 5. Resolve Customer Phone
    phone_match = re.search(r'\b(03\d{2}[- ]?\d{7}|\+92\d{10}|03\d{9})\b', user_content)
    if phone_match:
        clean_phone = phone_match.group(1).replace(" ", "").replace("-", "")
        conv.pending_customer_phone = clean_phone
        if not conv.pending_customer_name:
            conv.awaiting_input = "name"
        else:
            conv.awaiting_input = "confirmation"

    # 6. Resolve Customer Name (if not a question query, doctor name, or service name)
    doctors_for_name_check = Doctor.query.filter_by(business_id=conv.business_id).all()
    services_for_name_check = Service.query.filter_by(business_id=conv.business_id, is_active=True).all()
    _roster_names_for_exclusion = [d.name for d in doctors_for_name_check] + [s.name for s in services_for_name_check]
    cand_name = _extract_name(user_content, roster_names=_roster_names_for_exclusion)
    if cand_name and not _is_question_query(user_content):
        conv.pending_customer_name = cand_name
        if conv.requested_time:
            if not conv.pending_customer_phone:
                conv.awaiting_input = "phone"
            else:
                conv.awaiting_input = "confirmation"

    # 7. Intent and change triggers
    if is_explicit_change:
        if "doctor" in text_lower and not matched_doc:
            conv.selected_doctor_id = None
            conv.requested_time = None
        elif ("date" in text_lower or "day" in text_lower) and not parsed_date:
            conv.requested_date = None
            conv.requested_time = None
        elif "time" in text_lower or "slot" in text_lower:
            conv.requested_time = None
        elif "service" in text_lower and not matched_svc:
            conv.selected_service_id = None
            conv.requested_time = None
        elif not matched_doc and not matched_svc and not parsed_date:
            conv.requested_time = None

    # 8. Cancel trigger
    if any(k in text_lower for k in ["cancel booking", "cancel appointment", "cancel my booking"]):
        conv.workflow_state = "START"
        conv.intent = "UNKNOWN"
        conv.awaiting_input = None
        conv.requested_time = None
        conv.requested_date = None
        conv.selected_doctor_id = None
        conv.selected_service_id = None
        conv.pending_customer_name = None
        conv.pending_customer_phone = None

    # Default service or doctor if date/time are specified and one of them was not explicitly selected
    if conv.selected_doctor_id and (conv.requested_date or conv.requested_time) and not conv.selected_service_id:
        def_svc = Service.query.filter_by(business_id=conv.business_id, is_active=True).first()
        if def_svc:
            conv.selected_service_id = def_svc.id

    if conv.selected_service_id and (conv.requested_date or conv.requested_time) and not conv.selected_doctor_id:
        def_doc = Doctor.query.filter_by(business_id=conv.business_id, is_active=True).first()
        if def_doc:
            conv.selected_doctor_id = def_doc.id

    # Update intent/state when customer wants appointment or gives parameters
    if (
        conv.selected_service_id or
        conv.selected_doctor_id or
        conv.requested_date or
        conv.requested_time or
        conv.pending_customer_name or
        conv.pending_customer_phone or
        any(w in text_lower for w in ["appointment", "book", "reserve", "consultation", "checkup", "visit"])
    ):
        if conv.intent in [None, "UNKNOWN"]:
            conv.intent = "BOOK_APPOINTMENT"
        if conv.workflow_state in [None, "START"]:
            conv.workflow_state = "COLLECTING_INFO"
        
        # Calculate the next missing field in strict logical sequence
        if conv.workflow_state != "BOOKED":
            if not conv.selected_service_id and not conv.selected_doctor_id:
                conv.awaiting_input = "service_choice"
            elif not conv.selected_doctor_id and conv.selected_service_id:
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

    db.session.flush()


def _build_ui_action(conv: Conversation) -> Optional[Dict[str, Any]]:
    """
    Build structured UI action payload for frontend rendering following the guided flow:
    Service -> Doctor -> Date -> Real Time Slots -> Confirmation
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

    # 1. Final Booking Confirmation Card
    if (
        conv.selected_doctor_id and
        conv.requested_date and
        conv.requested_time and
        conv.pending_customer_name and
        conv.pending_customer_phone and
        conv.workflow_state != "BOOKED"
    ):
        doc = db.session.get(Doctor, conv.selected_doctor_id)
        svc = db.session.get(Service, conv.selected_service_id) if conv.selected_service_id else Service.query.filter_by(business_id=conv.business_id, is_active=True).first()
        
        try:
            d_obj = datetime.strptime(conv.requested_date, "%Y-%m-%d")
            formatted_date = d_obj.strftime("%A, %B %d, %Y")
        except Exception:
            formatted_date = conv.requested_date

        formatted_time = _fmt_ampm(conv.requested_time)

        return {
            "type": "booking_confirmation",
            "interactive_type": "button",
            "title": "Review & Confirm Your Appointment",
            "details": {
                "service_name": svc.name if svc else "Dental Consultation",
                "service_price": f"PKR {svc.price:,.0f}" if (svc and svc.price) else "PKR 2,000",
                "service_duration": f"{svc.duration} mins" if svc else "30 mins",
                "doctor_name": doc.name if doc else "Our Practicing Dentist",
                "doctor_specialization": doc.specialization if doc else "General Dentistry",
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

    # 2. Service Selection (Step 1 of Flow)
    if not conv.selected_service_id and (conv.intent == "BOOK_APPOINTMENT" or conv.awaiting_input == "service_choice" or conv.workflow_state in ["START", "COLLECTING_INFO", "CHECKING_AVAILABILITY"]):
        services = Service.query.filter_by(business_id=conv.business_id, is_active=True).all()
        if services:
            business = db.session.get(Business, conv.business_id)
            consultation_fee = (getattr(business, 'consultation_fee', 2000.0) if business else 2000.0) or 2000.0
            options = [
                {
                    "id": f"svc_{s.id}",
                    "name": s.name,
                    "title": s.name,
                    "label": s.name,
                    "value": s.name,
                    "duration": s.duration,
                    "price": s.price,
                    "price_formatted": f"PKR {s.price:,.0f}" if s.price else "",
                    "description": s.description or f"{s.duration} mins"
                }
                for s in services
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

    # 3. Doctor Selection (Step 2 of Flow)
    if not conv.selected_doctor_id and (conv.intent == "BOOK_APPOINTMENT" or conv.awaiting_input == "doctor_choice" or conv.workflow_state in ["COLLECTING_INFO", "CHECKING_AVAILABILITY"]):
        doctors = Doctor.query.filter_by(business_id=conv.business_id, is_active=True).all()
        if doctors:
            return {
                "type": "doctor_selection",
                "interactive_type": "list",
                "title": "Choose Your Doctor",
                "options": [
                    {
                        "id": f"doc_{d.id}",
                        "name": d.name,
                        "title": d.name,
                        "label": d.name,
                        "value": d.name,
                        "specialization": d.specialization,
                        "description": d.specialization or "General Dentistry",
                        "working_days": d.working_days
                    }
                    for d in doctors
                ]
            }

    # 4. Date Selection (Step 3 of Flow)
    if not conv.requested_date and (conv.intent == "BOOK_APPOINTMENT" or conv.awaiting_input in ["date_choice", "date"] or conv.workflow_state in ["COLLECTING_INFO", "CHECKING_AVAILABILITY"]):
        from datetime import date as dt_date, timedelta as dt_td
        today = dt_date.today()
        date_options = []
        for i in range(1, 6):
            target_d = today + dt_td(days=i)
            label = "Tomorrow" if i == 1 else target_d.strftime("%a, %b %d")
            date_options.append({
                "id": f"date_{target_d.strftime('%Y%m%d')}",
                "title": label,
                "label": label,
                "value": target_d.strftime("%Y-%m-%d"),
                "day": target_d.strftime("%A"),
                "display": f"{label} ({target_d.strftime('%A')})"
            })
        return {
            "type": "date_selection",
            "interactive_type": "quick_reply",
            "title": "Choose an Appointment Date",
            "options": date_options,
            "allow_custom_date": True
        }

    # 5. Time Slot Selection (Step 4 of Flow)
    if not conv.requested_time and conv.selected_doctor_id and conv.requested_date:
        slots = []
        last_tool = Message.query.filter_by(conversation_id=conv.id, role="tool", tool_name="check_availability").order_by(Message.created_at.desc()).first()
        if last_tool and last_tool.content:
            try:
                data = json.loads(last_tool.content)
                slots = data.get("available_slots") or data.get("next_available_slots") or []
            except Exception:
                pass

        if not slots:
            try:
                from services.booking_service import BookingService
                avail_res = BookingService.check_availability(
                    business_id=conv.business_id,
                    date_str=conv.requested_date,
                    doctor_id=conv.selected_doctor_id,
                    service_id=conv.selected_service_id
                )
                slots = avail_res.get("available_slots", [])
            except Exception:
                pass

        if slots:
            doc = db.session.get(Doctor, conv.selected_doctor_id)
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
                d_obj = datetime.strptime(conv.requested_date, "%Y-%m-%d")
                formatted_d = d_obj.strftime("%A, %B %d, %Y")
            except Exception:
                formatted_d = conv.requested_date

            return {
                "type": "time_slot_selection",
                "interactive_type": "list",
                "title": f"Available Time Slots on {formatted_d}",
                "doctor_name": doc.name if doc else "",
                "date": conv.requested_date,
                "options": time_options
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
        """
        conv = db.session.get(Conversation, conversation_id)
        if not conv:
            raise ValueError(f"Conversation {conversation_id} not found.")

        # Human receptionist override: if status is HUMAN, bypass AI completely
        if conv.status == "HUMAN":
            return {
                "conversation_id": conv.id,
                "status": "HUMAN",
                "content": "Our human staff has taken over this conversation and will reply shortly. Please wait for their reply or call our reception directly.",
                "executed_tools": [],
                "ui_action": None
            }

        # 1. Persist user message to DB
        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=user_content
        )
        db.session.add(user_msg)
        db.session.commit()

        # 2. Intelligently extract booking parameters from user text & update state
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
        max_iterations = 5

        # Get completion from adapter
        response = self.llm_client.get_completion(
            system_prompt=system_prompt,
            messages=formatted_messages,
            tools=CANONICAL_TOOLS,
            conversation_state=state_dict
        )

        # Tool execution loop
        while response.get("tool_calls") and iteration < max_iterations:
            iteration += 1

            if response.get("tool_calls"):
                formatted_messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": response["tool_calls"]
                })

            for tc in response["tool_calls"]:
                tool_name = tc.get("name")
                tool_args = tc.get("arguments", {})
                tool_call_id = tc.get("id", f"call_{iteration}")

                if tool_name == "book_appointment" and not tool_args.get("idempotency_key"):
                    tool_args["idempotency_key"] = f"conv-{conv.id}-attempt-{iteration}-{uuid.uuid4().hex[:8]}"

                executed_tools.append({"name": tool_name, "args": tool_args})

                self._update_conversation_state(conv, tool_name, tool_args)

                result = dispatcher.execute(tool_name, tool_args)
                tool_results.append({"tool": tool_name, "result": result})

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

                formatted_messages.append({
                    "role": "tool",
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": tool_msg_content
                })

            state_dict = _build_state_dict(conv)
            enriched_system_prompt = system_prompt + "\n\n" + _build_state_context(conv)

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

        return {
            "conversation_id": conv.id,
            "status": conv.status,
            "content": final_content,
            "executed_tools": executed_tools,
            "tool_results": tool_results,
            "intent": conv.intent,
            "workflow_state": conv.workflow_state,
            "ui_action": ui_act
        }

    def _update_conversation_state(self, conv: Conversation, tool_name: str, args: Dict[str, Any]):
        """
        Persist structured booking state into the conversation record after each tool call.
        """
        if tool_name == "check_availability":
            conv.intent = "BOOK_APPOINTMENT"
            conv.workflow_state = "CHECKING_AVAILABILITY"
            conv.awaiting_input = "time_choice"
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
            conv.awaiting_input = "doctor_choice"

        elif tool_name == "get_services":
            conv.awaiting_input = "service_choice"

        elif tool_name == "get_clinic_info":
            conv.awaiting_input = None

        db.session.flush()
