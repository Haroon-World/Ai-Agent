import os
import json
import re
import difflib
from typing import List, Dict, Any, Optional, Tuple
from config.config import Config
from ai.tools import CANONICAL_TOOLS


_NAME_PREFIX_RE = re.compile(r'^\s*(dr\.?|doctor)\s+', re.IGNORECASE)

# Generic words that appear across many roster entries (e.g. "Dental Checkup",
# "Dental Cleaning", "Dental Braces" all share "dental") and so must never be
# treated as a strong/distinctive match signal on their own — otherwise the
# first roster entry containing the shared word wins by coincidence of
# iteration order rather than actually matching what the user said.
_GENERIC_MATCH_STOPWORDS = {
    "dental", "and", "the", "for", "with", "clinic", "care", "treatment",
    "services", "service", "appointment", "consultation", "dr", "doctor"
}


def _fuzzy_match_roster(user_text: str, roster: List[Dict[str, Any]], threshold: float = 0.6) -> Optional[Dict[str, Any]]:
    """
    Match a user's free-text reply against a real DB roster (doctors or
    services) instead of a hardcoded keyword list, so spelling variants,
    typos, and "dr"/"doctor" prefixes still resolve correctly (e.g.
    "dr ahmad" -> "Dr. Ahmed Khan", "sara" -> "Dr. Sara Malik").

    Strategy: for each roster entry, compare the user text against the
    full name and against each DISTINCTIVE word in the name (generic words
    shared across many entries, like "dental" or "consultation", are
    excluded so they can't win a match by coincidence), using difflib's
    sequence-matching ratio for near-misses and substring containment for
    strong direct hits. Returns the best entry above `threshold`, or None.
    """
    if not user_text or not roster:
        return None

    cleaned = _NAME_PREFIX_RE.sub('', user_text.strip().lower())
    if not cleaned:
        return None

    best_entry = None
    best_score = 0.0

    for entry in roster:
        name = entry.get("name", "")
        if not name:
            continue
        name_lower = name.lower()
        name_clean = _NAME_PREFIX_RE.sub('', name_lower)
        word_candidates = [
            w for w in name_clean.split()
            if len(w) >= 4 and w not in _GENERIC_MATCH_STOPWORDS
        ]
        # Always include the full name as a candidate (handles short full
        # names like "Sara Malik" whose individual words are still fine),
        # plus every distinctive word.
        candidates = [name_clean] + word_candidates

        for cand in candidates:
            if not cand:
                continue
            # Exact substring match is only a strong (0.95) signal when the
            # candidate is specific enough not to be a coincidental shared
            # word — short/generic tokens fall through to ratio scoring.
            if len(cand) >= 4 and (cand in cleaned or cleaned in cand):
                score = 0.95
            else:
                score = difflib.SequenceMatcher(None, cand, cleaned).ratio()
            if score > best_score:
                best_score = score
                best_entry = entry

    if best_score >= threshold:
        return best_entry
    return None


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


def _extract_time_str(text: str) -> Optional[str]:
    """Extract standard HH:MM time string from user text supporting ':', '.', 'am/pm', and bare numbers after prepositions like 'after 12'."""
    if not text:
        return None
    # Match standard HH:MM or HH.MM with optional am/pm (e.g. 9:30, 09:30, 12.00, 12.00pm, 14:00)
    m = re.search(r'\b(\d{1,2})[:.](\d{2})\s*(am|pm)?\b', text, re.IGNORECASE)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        ampm = m.group(3).lower() if m.group(3) else None
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mn:02d}"
    # Match H am / H pm (e.g. 10 am, 2 pm, 12 pm)
    m = re.search(r'\b(\d{1,2})\s*(am|pm)\b', text, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        ampm = m.group(2).lower()
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        return f"{h:02d}:00"
    # Match bare number after time prepositions like "after 12", "before 2", "at 10"
    m = re.search(r'\b(?:after|before|at|around|from|past)\s+(\d{1,2})\b', text, re.IGNORECASE)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 7:
            h += 12
        return f"{h:02d}:00"
    return None


def _extract_name(text: str, roster_names: Optional[List[str]] = None) -> Optional[str]:
    """
    Extract person name from customer booking text.

    roster_names (optional): real doctor/service names for this business.
    When the "entire text is a name" fallback heuristic would fire, it is
    checked against this roster first — a bare reply that actually matches
    a doctor or service (e.g. "ahmad", a spelling variant of "Ahmed Khan")
    must never be treated as the customer's own name, regardless of the
    current conversation state. This does NOT apply to the explicit "my
    name is X" pattern above, since a deliberate statement of intent should
    still be honored even in the rare case it happens to coincide with a
    doctor's name.
    """
    if not text:
        return None
    m = re.search(r'(?:my\s+name\s+is\s+|name\s+is\s+|i\s+am\s+|name\s*:\s*|for\s+)([a-zA-Z]+(?:\s+[a-zA-Z]+)*)', text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        name = re.split(r'[,.]|\bphone\b|\bcontact\b|\bat\b|\bon\b|\bdate\b|\bfor\b', raw, flags=re.IGNORECASE)[0].strip()
        name = re.sub(r'^(?:a\s+|an\s+|the\s+)?(?:cleaning|checkup|consultation|appointment|booking)\s+(?:for\s+)?', '', name, flags=re.IGNORECASE).strip()
        if name and name.lower() not in ["patient", "a", "the", "an", "cleaning", "checkup", "appointment", "doctor", "dr", "tomorrow", "today", "me", "us", "him", "her"]:
            return name.title()
    # Check if the entire text consists of a person's name (1-4 alphabetic words)
    words = text.strip().split()
    if 1 <= len(words) <= 4 and all(w.replace(".", "").replace("-", "").isalpha() for w in words):
        lower_txt = text.strip().lower()
        non_name_words = [
            "want", "need", "like", "would", "schedule", "book", "available", "availability",
            "slot", "slots", "time", "timing", "day", "tomorrow", "today", "appointment",
            "checkup", "cleaning", "dentist", "doctor", "dr", "info", "information", "price",
            "yes", "no", "ok", "okay", "sure", "thanks", "thank you", "cancel", "help", "hello", "hi", "hey",
            "root", "canal", "treatment", "extraction", "whitening", "braces", "consultation", "scaling", "polishing",
        ]
        if any(nw in lower_txt for nw in non_name_words):
            return None
        # Dynamic roster check (replaces the old hardcoded "sara"/"ahmed"/
        # "khan"/"malik" list, which broke on any spelling variant like
        # "ahmad"): if this bare reply fuzzy-matches a real doctor or
        # service name for this business, it's almost certainly a
        # selection, not the customer stating their own name.
        if roster_names:
            roster_entries = [{"id": i, "name": n} for i, n in enumerate(roster_names)]
            if _fuzzy_match_roster(text, roster_entries, threshold=0.6):
                return None
        return text.strip().title()
    return None



def _has_booking_intent(text: str) -> bool:
    """Check if user text conveys booking/schedule intent, even with common typos or misspellings."""
    if not text:
        return False
    lower = text.lower()
    pattern = r'\b(book|booking|appo?i?n?t?m?e?n?t?s?|sch?e?d?u?l?e?s?|skedule|slots?|time|timing|timings|available|availability|doctor|dentist|teeth|cleaning|checkup|visit|consultants?|physicians?|practitioners?)\b'
    if re.search(pattern, lower, re.IGNORECASE):
        return True
    typos = ["appoinment", "apointment", "appintment", "schdeule", "scedule", "skedule", "appoint", "sched", "timings"]
    return any(t in lower for t in typos)


class BaseLLMAdapter:
    def chat_completion(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        conversation_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute chat completion.
        Returns dict format:
        {
            "content": "Assistant response text",
            "tool_calls": [
                {
                    "name": "tool_name",
                    "arguments": { ... }
                }
            ]
        }
        """
        raise NotImplementedError


class MockAdapter(BaseLLMAdapter):
    """
    Intelligent simulated LLM adapter for deterministic local development,
    tool execution testing, and CI environments without requiring external API keys.
    Programmatically reads conversation_state to maintain multi-turn workflow continuity.
    """
    def chat_completion(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        conversation_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        if not messages:
            return {
                "content": "Hello! Welcome to SmileCare Dental Clinic. How can I assist you with your dental care today?",
                "tool_calls": []
            }

        # Check if previous turn was a tool response
        last_msg = messages[-1]
        if last_msg.get("role") == "tool":
            tool_content = last_msg.get("content", "")
            try:
                tool_data = json.loads(tool_content) if isinstance(tool_content, str) else tool_content
            except Exception:
                tool_data = {}

            # Synthesize response from tool result
            if "results" in tool_data and "available_slots" in str(tool_data):
                # Inspect last user message prior to tool call for time filters (e.g. "after 12")
                last_user_text = ""
                for m in reversed(messages[:-1]):
                    if m.get("role") == "user":
                        last_user_text = m.get("content", "").lower().strip()
                        break

                user_time_token = _extract_time_str(last_user_text)
                is_after_query = "after" in last_user_text and user_time_token
                is_before_query = "before" in last_user_text and user_time_token

                slots_list = []
                filtered_slots_list = []
                for res in tool_data.get("results", []):
                    doc_name = res.get("doctor_name", "Doctor")
                    slots = res.get("available_slots", [])
                    if slots:
                        slots_preview = ", ".join(slots[:4])
                        slots_list.append(f"• {doc_name}: {slots_preview}")
                        if is_after_query:
                            after_slots = [s for s in slots if s > user_time_token]
                            if after_slots:
                                filtered_slots_list.append(f"• {doc_name}: {', '.join(after_slots[:4])}")
                        elif is_before_query:
                            before_slots = [s for s in slots if s < user_time_token]
                            if before_slots:
                                filtered_slots_list.append(f"• {doc_name}: {', '.join(before_slots[:4])}")

                if is_after_query:
                    if filtered_slots_list:
                        return {
                            "content": f"Yes, the available slots for {tool_data.get('date')} ({tool_data.get('day')}) after {user_time_token} are:\n" + "\n".join(filtered_slots_list) + "\n\nPlease let me know which time slot works best for you, along with your full name and phone number to confirm!",
                            "tool_calls": []
                        }
                    else:
                        all_preview = "\n".join(slots_list) if slots_list else "No open slots."
                        return {
                            "content": f"I checked our schedule for {tool_data.get('date')} ({tool_data.get('day')}), but there are no available slots after {user_time_token}.\n\nThe open slots on that day are:\n{all_preview}\n\nWould you like one of these morning slots or would you like to check another date?",
                            "tool_calls": []
                        }

                if is_before_query:
                    if filtered_slots_list:
                        return {
                            "content": f"Yes, the available slots for {tool_data.get('date')} ({tool_data.get('day')}) before {user_time_token} are:\n" + "\n".join(filtered_slots_list) + "\n\nPlease let me know which time slot works best for you, along with your full name and phone number to confirm!",
                            "tool_calls": []
                        }
                    else:
                        all_preview = "\n".join(slots_list) if slots_list else "No open slots."
                        return {
                            "content": f"I checked our schedule for {tool_data.get('date')} ({tool_data.get('day')}), but there are no available slots before {user_time_token}.\n\nThe open slots on that day are:\n{all_preview}\n\nWould you like one of these slots or would you like to check another date?",
                            "tool_calls": []
                        }

                if slots_list:
                    return {
                        "content": f"Here are the available slots for {tool_data.get('date')} ({tool_data.get('day')}):\n" + "\n".join(slots_list) + "\n\nPlease let me know which time slot works best for you, along with your full name and phone number to confirm the booking!",
                        "tool_calls": []
                    }
                else:
                    return {
                        "content": f"I checked our schedule for {tool_data.get('date')}, but unfortunately there are no open slots on that day. Would you like to check the following day?",
                        "tool_calls": []
                    }

            elif "appointment_id" in tool_data and tool_data.get("success"):
                appt = tool_data.get("appointment", {})
                return {
                    "content": f"🎉 **Your appointment is confirmed!**\n\n• **Appointment ID:** #{tool_data.get('appointment_id')}\n• **Patient:** {appt.get('customer_name')}\n• **Doctor:** {appt.get('doctor_name')}\n• **Service:** {appt.get('service_name')}\n• **Date & Time:** {appt.get('appointment_date')} at {appt.get('appointment_time')}\n• **Location:** Plot 42-B, Main Boulevard, Gulberg III, Lahore\n\nA reminder has been automatically scheduled for your visit. Please arrive 10 minutes early. Let us know if you need anything else!",
                    "tool_calls": []
                }

            elif tool_data.get("status") == "HUMAN":
                return {
                    "content": "I have notified our clinic receptionist team. A human staff member will take over this conversation shortly to assist you. Please hold on.",
                    "tool_calls": []
                }

            elif "doctors" in tool_data:
                doc_list = [f"• **{d['name']}** - {d.get('specialization', 'Dentist')} (Working Days: {d.get('working_days', 'Mon-Sat')}, Hours: {d.get('start_time', '09:00')} - {d.get('end_time', '17:00')})" for d in tool_data.get("doctors", [])]
                return {
                    "content": "Of course. Here are our practicing dentists at SmileCare:\n\n" + "\n\n".join(doc_list) + "\n\nWhich doctor would you prefer?",
                    "tool_calls": []
                }

            elif "services" in tool_data:
                svc_list = [f"• **{s['name']}** ({s['duration']} mins) - PKR {s['price']:,.0f}: {s['description']}" for s in tool_data.get("services", [])]
                return {
                    "content": "Here is our complete list of dental services:\n\n" + "\n\n".join(svc_list) + "\n\nWould you like to book an appointment for any of these services?",
                    "tool_calls": []
                }

            elif "opening_hours" in tool_data:
                return {
                    "content": f"**SmileCare Dental Clinic Information:**\n• **Address:** {tool_data.get('address')}\n• **Phone:** {tool_data.get('phone')}\n• **Hours:** {tool_data.get('opening_hours')}\n• **Policies:** {tool_data.get('policies')}\n\nHow else can I help you?",
                    "tool_calls": []
                }

        # Analyze latest user message
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = m.get("content", "").lower().strip()
                break

        # State extraction
        conv_state = conversation_state or {}
        workflow_state = conv_state.get("workflow_state")
        req_date = conv_state.get("requested_date")
        req_time = conv_state.get("requested_time")
        doc_id = conv_state.get("selected_doctor_id") or 1
        doc_name = conv_state.get("selected_doctor_name") or ("Dr. Ahmed Khan" if doc_id == 1 else "Dr. Sara Malik")
        svc_id = conv_state.get("selected_service_id") or 2
        svc_name = conv_state.get("selected_service_name") or "Dental Cleaning & Scaling"
        last_offered_slots = conv_state.get("last_offered_slots", {})
        all_offered_slots = conv_state.get("all_offered_slots", [])

        # Token extractions
        time_token = _extract_time_str(user_text)
        phone_match = re.search(r'(\+?\d[\d\s\-]{8,14}\d)', user_text)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', user_text)
        is_question = _is_question_query(user_text)

        # Name extraction & state resolution
        pending_name = conv_state.get("pending_customer_name")
        pending_phone = conv_state.get("pending_customer_phone")
        _roster_names_for_exclusion = (
            [d.get("name") for d in (conv_state.get("doctor_roster") or [])] +
            [s.get("name") for s in (conv_state.get("service_roster") or [])]
        )
        cand_name = _extract_name(user_text, roster_names=_roster_names_for_exclusion)
        effective_name = cand_name or pending_name
        effective_phone = (phone_match.group(1).replace(" ", "").replace("-", "") if phone_match else None) or pending_phone


        from datetime import date, timedelta
        explicit_date_given = False
        if date_match:
            target_date_str = date_match.group(1)
            explicit_date_given = True
        elif "tomorrow" in user_text:
            target_date_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
            explicit_date_given = True
        elif "today" in user_text:
            target_date_str = date.today().strftime("%Y-%m-%d")
            explicit_date_given = True
        elif req_date:
            target_date_str = req_date
            explicit_date_given = True
        else:
            target_date_str = None
            explicit_date_given = False


        # Doctor / service overrides from text — fuzzy-matched against the
        # real per-business roster (not hardcoded names) so spelling
        # variants like "dr ahmad" still resolve to "Dr. Ahmed Khan".
        doctor_roster = conv_state.get("doctor_roster") or []
        service_roster = conv_state.get("service_roster") or []

        _doc_override = _fuzzy_match_roster(user_text, doctor_roster)
        if _doc_override:
            doc_id = _doc_override["id"]
            doc_name = _doc_override["name"]

        _svc_override = _fuzzy_match_roster(user_text, service_roster)
        if _svc_override:
            svc_id = _svc_override["id"]
            svc_name = _svc_override["name"]

        # --- EXPLICIT AWAITING_INPUT RESOLUTION (Runs FIRST before Case A-E & keyword matching) ---
        awaiting_input = conv_state.get("awaiting_input")
        
        # Check if user clearly changed subject or asked a question
        is_topic_change = is_question or any(w in user_text for w in ["address", "location", "timing", "hours", "insurance", "human", "receptionist", "eye", "skin"])

        if awaiting_input and not is_topic_change:
            if awaiting_input == "doctor_choice":
                matched_doc = _fuzzy_match_roster(user_text, doctor_roster)
                if matched_doc:
                    doc_id = matched_doc["id"]
                    doc_name = matched_doc["name"]
                    cand_name = None
                    effective_name = pending_name
                    if target_date_str:
                        return {
                            "content": f"Checking open slots for {doc_name} on {target_date_str}...",
                            "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": doc_id, "service_id": svc_id}}]
                        }
                    return {
                        "content": f"Thank you! You selected {doc_name}. Which date would you like to book your appointment for?",
                        "tool_calls": []
                    }
                elif not is_question:
                    roster_names = ", ".join(d["name"] for d in doctor_roster) or "Dr. Ahmed Khan or Dr. Sara Malik"
                    return {
                        "content": f"Please select a doctor from our roster: {roster_names}.",
                        "tool_calls": []
                    }

            elif awaiting_input == "service_choice":
                matched_svc = _fuzzy_match_roster(user_text, service_roster)
                if matched_svc:
                    svc_id = matched_svc["id"]
                    svc_name = matched_svc["name"]
                    if target_date_str:
                        return {
                            "content": f"Checking open slots for {svc_name} on {target_date_str}...",
                            "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": doc_id, "service_id": svc_id}}]
                        }
                    return {
                        "content": f"You selected {svc_name}. Which date or doctor would you prefer?",
                        "tool_calls": []
                    }
                elif not is_question:
                    roster_names = ", ".join(s["name"] for s in service_roster) or "Dental Checkup, Dental Cleaning, Teeth Whitening, Tooth Extraction, Root Canal, or Braces"
                    return {
                        "content": f"Please select from our available dental services: {roster_names}.",
                        "tool_calls": []
                    }

            elif awaiting_input == "confirmation":
                if any(w in user_text for w in ["yes", "yeah", "confirm", "sure", "go ahead", "ok", "okay", "haan", "theek"]):
                    if effective_name and effective_phone and target_date_str:
                        return {
                            "content": f"Booking your appointment with {doc_name} for {target_date_str} at {req_time or '09:00'}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": doc_id,
                                    "service_id": svc_id,
                                    "appointment_date": target_date_str,
                                    "appointment_time": req_time or "09:00",
                                    "notes": "Booked via AI Assistant"
                                }
                            }]
                        }
                elif any(w in user_text for w in ["no", "cancel", "nevermind", "nahi"]):
                    return {
                        "content": "I have cancelled your booking request. How else may I assist you?",
                        "tool_calls": []
                    }

            elif awaiting_input == "name":
                if cand_name:
                    effective_name = cand_name
                    if effective_phone:
                        return {
                            "content": f"Booking your appointment with {doc_name} for {target_date_str or 'tomorrow'} at {req_time or '09:00'}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": doc_id,
                                    "service_id": svc_id,
                                    "appointment_date": target_date_str or "tomorrow",
                                    "appointment_time": req_time or "09:00",
                                    "notes": "Booked via AI Assistant"
                                }
                            }]
                        }
                    return {
                        "content": f"Thank you, {effective_name}. Please provide your contact phone number to complete and confirm your booking.",
                        "tool_calls": []
                    }

            elif awaiting_input == "phone":
                if phone_match:
                    effective_phone = phone_match.group(1).replace(" ", "").replace("-", "")
                    if effective_name:
                        return {
                            "content": f"Booking your appointment with {doc_name} for {target_date_str or 'tomorrow'} at {req_time or '09:00'}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": doc_id,
                                    "service_id": svc_id,
                                    "appointment_date": target_date_str or "tomorrow",
                                    "appointment_time": req_time or "09:00",
                                    "notes": "Booked via AI Assistant"
                                }
                            }]
                        }
                    return {
                        "content": "Thank you. Please provide your full name to complete and confirm your booking.",
                        "tool_calls": []
                    }

        # 1. Non-Dental / Non-Oral Out-of-Scope Health Inquiries (e.g. eyes, vision, skin, heart, etc.)
        specialist_terms = [
            "neurosurgeon", "neurologist", "neurology", "cardiologist", "cardiology",
            "dermatologist", "dermatology", "oncologist", "oncology", "orthopedic",
            "gynecologist", "psychiatrist", "ent specialist", "ophthalmologist",
            "general surgeon", "urologist", "nephrologist", "gastroenterologist"
        ]
        non_dental_terms = [
            "eye", "eyes", "eyesight", "vision", "optometrist", "optometry", "glasses",
            "skin", "acne", "heart", "ear", "ears", "hearing", "lung", "stomach"
        ]
        if any(s in user_text for s in specialist_terms) or ("pediatrician" in user_text and "dent" not in user_text):
            return {
                "content": "SmileCare is a dedicated dental clinic and does not offer non-dental medical specialties. I am connecting you with our human receptionist to see if they can assist or refer you.",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": f"Customer inquired about non-dental medical specialty: {user_text}"}}]
            }

        if any(w in user_text for w in non_dental_terms):
            return {
                "content": "SmileCare is a dedicated dental clinic specializing exclusively in teeth and oral healthcare (such as teeth cleaning, dental checkups, root canals, braces, extractions, and whitening). We do not offer eye checkups or general medical services. However, if you or a family member need any dental care, checkups, or teeth cleaning, I'd be happy to assist you with booking an appointment or checking our doctor schedules!",
                "tool_calls": []
            }

        # 2. Medical advice / prescription / insurance triggers
        if any(w in user_text for w in ["insurance", "prescription", "antibiotic", "diagnose", "severe bleeding"]):
            return {
                "content": "I cannot provide medical advice or verify insurance coverage directly. Let me connect you with our medical staff.",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": f"Out-of-scope / medical query: {user_text}"}}]
            }

        # 3. Explicit Human Handoff Request
        if any(w in user_text for w in ["human", "receptionist", "speak to someone", "representative", "real person", "manager", "staff"]):
            return {
                "content": "Connecting you with our reception team...",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": "Customer requested human representative"}}]
            }

        # 4. Informational Request Priority (Doctor Inquiry, Services Inquiry, Clinic Info Inquiry)
        # Check Doctor Inquiry first (e.g. "tell me doctors name", "who are your doctors", "how many doctors", "are doctor available")
        has_doc_term = any(w in user_text for w in ["doctor", "doctors", "dentist", "dentists"])
        has_inquiry_term = any(w in user_text for w in ["tell", "show", "list", "who", "which", "what", "how", "many", "count", "available", "name", "names", "info", "information", "detail", "details", "about"])
        is_doctor_inquiry = (has_doc_term and has_inquiry_term) or any(p in user_text for p in [
            "tell me doctor", "tell me doctors", "doctor name", "doctors name", "doctor names", "doctors names",
            "names of doctor", "names of doctors", "who are your doctor", "who are your doctors", "tell me about your doctor",
            "which doctor", "who is the doctor", "who are the doctors", "list doctor", "list doctors", "available doctor",
            "available doctors", "are doctor available", "doctor available", "how many doctors", "how many doctor", "dentist name", "dentist names", "doctors at", "what doctor", "what doctors", "which dentist"
        ])
        if is_doctor_inquiry:
            return {
                "content": "Let me retrieve our list of doctors for you.",
                "tool_calls": [{"name": "get_doctors", "arguments": {}}]
            }

        # Check Services Inquiry (e.g. "what services do you offer", "how much is cleaning")
        is_service_inquiry = any(w in user_text for w in ["which service", "what service", "what services", "list service", "list services", "treatment", "treatments", "price", "prices", "cost", "costs", "charge", "charges", "what do you offer", "how much"])
        if is_service_inquiry:
            return {
                "content": "Let me fetch our dental services and pricing for you.",
                "tool_calls": [{"name": "get_services", "arguments": {}}]
            }

        # Check Clinic Info Inquiry
        is_clinic_info_inquiry = any(w in user_text for w in ["address", "location", "located", "where is", "where are", "timing", "hours", "contact", "phone number", "clinic info", "directions"])
        if is_clinic_info_inquiry:
            return {
                "content": "Checking clinic details...",
                "tool_calls": [{"name": "get_clinic_info", "arguments": {}}]
            }

        # 5. State-Aware Slot/Time Selection & Booking
        effective_date = target_date_str
        doc_slots = last_offered_slots.get(str(doc_id)) or all_offered_slots

        # Case A: User is asking an availability question on an explicit date
        if is_question and (time_token or any(w in user_text for w in ["slot", "other", "after", "before", "time", "available", "availability", "when"])):
            if not explicit_date_given or not effective_date:
                return {
                    "content": f"Sure! I'd be happy to check availability for {doc_name}. Which date would you like to visit us?",
                    "tool_calls": []
                }
            if time_token and "after" in user_text and doc_slots:
                matching_slots = [s for s in doc_slots if s > time_token]
                if matching_slots:
                    slots_preview = ", ".join(matching_slots[:4])
                    return {
                        "content": f"Yes, for {doc_name} on {effective_date}, the available slots after {time_token} are: {slots_preview}. Please let me know which time works best for you, along with your full name and phone number to confirm!",
                        "tool_calls": []
                    }
                else:
                    return {
                        "content": f"I checked our schedule for {effective_date}, but there are no available slots for {doc_name} after {time_token}. The available slots on that day are: {', '.join(doc_slots[:4])}. Would you like one of these or another date?",
                        "tool_calls": []
                    }
            elif time_token and "before" in user_text and doc_slots:
                matching_slots = [s for s in doc_slots if s < time_token]
                if matching_slots:
                    slots_preview = ", ".join(matching_slots[:4])
                    return {
                        "content": f"Yes, for {doc_name} on {effective_date}, the available slots before {time_token} are: {slots_preview}. Please let me know which time works best for you, along with your full name and phone number to confirm!",
                        "tool_calls": []
                    }
                else:
                    return {
                        "content": f"I checked our schedule for {effective_date}, but there are no available slots for {doc_name} before {time_token}. The available slots on that day are: {', '.join(doc_slots[:4])}. Would you like one of these or another date?",
                        "tool_calls": []
                    }
            elif doc_slots and any(w in user_text for w in ["any other", "other slot", "what other", "all slot"]):
                slots_preview = ", ".join(doc_slots[:6])
                return {
                    "content": f"The available slots for {doc_name} on {effective_date} are: {slots_preview}. Please let me know which time slot works best for you!",
                    "tool_calls": []
                }
            else:
                return {
                    "content": f"Checking open slots for {doc_name} on {effective_date}...",
                    "tool_calls": [{
                        "name": "check_availability",
                        "arguments": {
                            "date": effective_date,
                            "doctor_id": doc_id,
                            "service_id": svc_id
                        }
                    }]
                }

        # Case B: User provides a bare time token (NOT a question) while in booking context without name/phone yet
        if not is_question and time_token and not phone_match and not cand_name:
            if not effective_date:
                return {
                    "content": f"Got it, {time_token}. Which date would you like to book this appointment for?",
                    "tool_calls": []
                }
            if doc_slots and time_token not in doc_slots:
                slots_preview = ", ".join(doc_slots[:4]) if doc_slots else "no open slots"
                return {
                    "content": f"The {time_token} slot is not available for {doc_name} on {effective_date}. The available slots on that day are: {slots_preview}. Please choose one of the available times or let me know if you would like to check another date.",
                    "tool_calls": []
                }
            if not doc_slots:
                return {
                    "content": f"Checking open slots for {doc_name} on {effective_date}...",
                    "tool_calls": [{
                        "name": "check_availability",
                        "arguments": {
                            "date": effective_date,
                            "doctor_id": doc_id,
                            "service_id": svc_id
                        }
                    }]
                }

            effective_time = time_token
            return {
                "content": f"I have selected the {effective_time} slot on {effective_date} with {doc_name} for {svc_name}. To complete and confirm your booking, please provide your full name and contact phone number.",
                "tool_calls": []
            }

        # Case C: Both name and phone are available (either from current turn or pending state) -> proceed to book
        if (effective_phone and effective_name):
            if not effective_date:
                return {
                    "content": f"Thank you, {effective_name}. Which date would you like to book your appointment for?",
                    "tool_calls": []
                }
            valid_time_token = time_token if (time_token and (not doc_slots or time_token in doc_slots)) else None
            chosen_time = valid_time_token or req_time or (doc_slots[0] if doc_slots else "09:00")
            return {
                "content": f"Booking your appointment with {doc_name} for {effective_date} at {chosen_time}...",
                "tool_calls": [{
                    "name": "book_appointment",
                    "arguments": {
                        "customer_name": effective_name,
                        "customer_phone": effective_phone,
                        "doctor_id": doc_id,
                        "service_id": svc_id,
                        "appointment_date": effective_date,
                        "appointment_time": chosen_time,
                        "notes": "Booked via AI Assistant"
                    }
                }]
            }

        # Case D: Name provided but phone still missing in booking context -> ask specifically for phone
        if effective_name and not effective_phone and (req_time or time_token or workflow_state in ["CHECKING_AVAILABILITY", "COLLECTING_INFO"]):
            return {
                "content": f"Thank you, {effective_name}. Please provide your contact phone number to complete and confirm your booking.",
                "tool_calls": []
            }

        # Case E: Phone provided but name still missing in booking context -> ask specifically for name
        if effective_phone and not effective_name and (req_time or time_token or workflow_state in ["CHECKING_AVAILABILITY", "COLLECTING_INFO"]):
            return {
                "content": "Thank you. Please provide your full name to complete and confirm your booking.",
                "tool_calls": []
            }

        # 5. Chit-Chat, Gratitude & Off-Topic Queries (e.g. weather, sports, jokes, news)
        if any(w in user_text for w in ["how are you", "who are you", "what is your name", "what can you do", "good morning", "good afternoon", "good evening", "hi there", "hello there"]):
            return {
                "content": "Hello! I am your AI receptionist at SmileCare Dental Clinic. I'm doing great and ready to assist you! I can help you check doctor schedules, explore our dental services, or book an appointment. How can I help with your teeth today?",
                "tool_calls": []
            }

        if any(w in user_text for w in ["thank you", "thanks", "thx", "appreciation", "great", "awesome", "perfect"]):
            return {
                "content": "You're very welcome! Is there anything else I can assist you with regarding your dental care or appointments today?",
                "tool_calls": []
            }

        if any(w in user_text for w in ["weather", "sports", "match", "joke", "news", "movie", "song", "code", "python", "capital", "president", "prime minister", "who won"]):
            return {
                "content": "I'm the AI receptionist for SmileCare Dental Clinic! While I'm focused specifically on dental healthcare, doctor schedules, and appointment bookings, I'd be happy to assist you with any teeth cleaning, checkup, or dental service. How can I help with your smile today?",
                "tool_calls": []
            }

        # 6. Booking intent or explicit date provided in active booking context -> Check availability if date is known, or ask for date
        if _has_booking_intent(user_text) or (explicit_date_given and target_date_str and (workflow_state in ["CHECKING_AVAILABILITY", "COLLECTING_INFO", "START"] or conv_state.get("intent") in ["BOOK_APPOINTMENT", "UNKNOWN"])):
            if explicit_date_given and target_date_str:
                return {
                    "content": f"Checking open slots for you on {target_date_str}...",
                    "tool_calls": [{
                        "name": "check_availability",
                        "arguments": {
                            "date": target_date_str,
                            "doctor_id": doc_id,
                            "service_id": svc_id
                        }
                    }]
                }
            else:
                if "ahmed" in user_text:
                    return {
                        "content": "Sure! I'd be happy to help you book an appointment with Dr. Ahmed Khan. Which date would you like to visit us?",
                        "tool_calls": []
                    }
                elif "sara" in user_text:
                    return {
                        "content": "Sure! I'd be happy to help you book an appointment with Dr. Sara Malik. Which date would you like to visit us?",
                        "tool_calls": []
                    }
                else:
                    return {
                        "content": "Sure! I'd be happy to help you book a dental appointment. Which doctor would you prefer, and what date would you like to visit us?",
                        "tool_calls": []
                    }

        # 7. Fallback & Chit-Chat handling setup
        user_message_count = len([m for m in messages if m.get("role") == "user"])
        has_prior_assistant = any(m.get("role") == "assistant" for m in messages[:-1]) if len(messages) > 1 else False

        # 11. Smart Active Guidance Fallback (Never cold/robot fallback)
        if user_message_count <= 1 and not has_prior_assistant:
            return {
                "content": "Hello! Welcome to SmileCare Dental Clinic. I am your AI receptionist. How can I help you today? You can ask about our dental services, doctor schedules, or book an appointment!",
                "tool_calls": []
            }

        return {
            "content": "I am here to assist you with all your dental care needs at SmileCare Dental Clinic! You can ask me about our available dental services, check doctor schedules, or book a consultation. How can I help you today?",
            "tool_calls": []
        }


class GeminiAdapter(BaseLLMAdapter):
    """Google Gemini Provider Adapter with structured function declarations."""
    def __init__(self, api_key: str, model_name: str = "gemini-3.5-flash-lite"):
        self.api_key = api_key
        self.model_name = model_name




    def _translate_tools_to_gemini(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        declarations = []
        for t in tools:
            decl = {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"]
            }
            declarations.append(decl)
        return [{"function_declarations": declarations}]

    def chat_completion(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        conversation_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        
        # Format messages for Gemini with proper multi-turn function calling history
        contents = []
        for m in messages:
            role = m.get("role")
            if role == "assistant" and m.get("tool_calls"):
                parts = []
                for tc in m["tool_calls"]:
                    ts_bytes = None
                    if tc.get("thought_signature"):
                        try:
                            ts_bytes = bytes.fromhex(tc["thought_signature"])
                        except Exception:
                            ts_bytes = None
                    parts.append(types.Part(
                        function_call=types.FunctionCall(
                            name=tc["name"],
                            args=tc.get("arguments", {}),
                            id=tc.get("id")
                        ),
                        thought_signature=ts_bytes
                    ))
                contents.append(types.Content(role="model", parts=parts))
            elif role == "tool":
                tool_content = m.get("content", "")
                if isinstance(tool_content, str):
                    try:
                        parsed_resp = json.loads(tool_content)
                    except Exception:
                        parsed_resp = {"result": tool_content}
                elif isinstance(tool_content, dict):
                    parsed_resp = tool_content
                else:
                    parsed_resp = {"result": str(tool_content)}

                contents.append(types.Content(
                    role="user",
                    parts=[types.Part.from_function_response(
                        name=m.get("tool_name", "tool"),
                        response=parsed_resp
                    )]
                ))
            else:
                gemini_role = "user" if role in ["user", "system"] else "model"
                contents.append(types.Content(
                    role=gemini_role,
                    parts=[types.Part.from_text(text=m.get("content") or "")]
                ))

        gemini_tools = self._translate_tools_to_gemini(tools)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=gemini_tools,
            temperature=0.2
        )

        import time
        max_retries = 3
        response = None
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=contents,
                    config=config
                )
                break
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and attempt < max_retries - 1:
                    match = re.search(r'retry in (\d+(?:\.\d+)?)s', err_str, re.IGNORECASE)
                    wait_sec = float(match.group(1)) + 2.0 if match else 35.0
                    print(f"[GeminiAdapter Rate-Limit 429]: Waiting {wait_sec:.1f}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(wait_sec)
                else:
                    raise e


        # Check for tool calls
        tool_calls = []
        text_content = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if part.function_call:
                    ts_hex = (
                        part.thought_signature.hex()
                        if getattr(part, "thought_signature", None)
                        else None
                    )
                    tool_calls.append({
                        "id": getattr(part.function_call, "id", None) or f"call_{len(tool_calls)}",
                        "name": part.function_call.name,
                        "arguments": dict(part.function_call.args) if part.function_call.args else {},
                        "thought_signature": ts_hex
                    })
                if part.text:
                    text_content += part.text


        return {
            "content": text_content.strip(),
            "tool_calls": tool_calls
        }


class GroqAdapter(BaseLLMAdapter):
    """Groq Provider Adapter using OpenAI-compatible function calling format."""
    def __init__(self, api_key: str, model_name: str = "llama3-70b-8192"):
        self.api_key = api_key
        self.model_name = model_name

    def _translate_tools_to_groq(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"]
                }
            }
            for t in tools
        ]

    def chat_completion(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        conversation_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        from groq import Groq
        client = Groq(api_key=self.api_key)

        formatted_messages = [{"role": "system", "content": system_prompt}]
        for i, m in enumerate(messages):
            role = m.get("role")
            if role == "assistant" and m.get("tool_calls"):
                groq_tool_calls = []
                for tc in m["tool_calls"]:
                    groq_tool_calls.append({
                        "id": tc.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": json.dumps(tc.get("arguments", {}))
                        }
                    })
                formatted_messages.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": groq_tool_calls
                })
            elif role == "tool":
                tool_call_id = m.get("tool_call_id") or f"call_{i}"
                formatted_messages.append({
                    "role": "tool",
                    "content": (
                        json.dumps(m["content"])
                        if isinstance(m["content"], dict)
                        else str(m["content"])
                    ),
                    "tool_call_id": tool_call_id
                })
            else:
                formatted_messages.append({
                    "role": role,
                    "content": m.get("content", "")
                })

        groq_tools = self._translate_tools_to_groq(tools)
        response = client.chat.completions.create(
            model=self.model_name,
            messages=formatted_messages,
            tools=groq_tools,
            tool_choice="auto",
            temperature=0.2
        )

        choice = response.choices[0].message
        tool_calls = []
        if choice.tool_calls:
            for tc in choice.tool_calls:
                args = {}
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    pass
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args
                })

        return {
            "content": choice.content or "",
            "tool_calls": tool_calls
        }



class LLMClient:
    """Unified LLM Client Factory and Router."""
    def __init__(self, provider: Optional[str] = None):
        self.provider = (provider or Config.LLM_PROVIDER or "mock").lower()

        if self.provider == "gemini" and Config.GEMINI_API_KEY:
            self.adapter = GeminiAdapter(api_key=Config.GEMINI_API_KEY, model_name=Config.GEMINI_MODEL)
        elif self.provider == "groq" and Config.GROQ_API_KEY:
            self.adapter = GroqAdapter(api_key=Config.GROQ_API_KEY, model_name=Config.GROQ_MODEL)
        else:
            self.adapter = MockAdapter()

    def get_completion(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] = CANONICAL_TOOLS,
        conversation_state: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        return self.adapter.chat_completion(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            conversation_state=conversation_state
        )
