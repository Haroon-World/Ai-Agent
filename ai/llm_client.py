import os
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from config.config import Config
from ai.tools import CANONICAL_TOOLS


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


def _extract_time_str(text: str) -> Optional[str]:
    """Extract standard HH:MM time string from user text supporting both ':' and '.' as separators."""
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
    return None


def _extract_name(text: str) -> Optional[str]:
    """Extract person name from customer booking text."""
    if not text:
        return None
    m = re.search(r'(?:my\s+name\s+is\s+|name\s+is\s+|i\s+am\s+|name\s*:\s*|for\s+)([a-zA-Z]+(?:\s+[a-zA-Z]+)*)', text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        name = re.split(r'[,.]|\bphone\b|\bcontact\b|\bat\b|\bon\b|\bdate\b', raw, flags=re.IGNORECASE)[0].strip()
        if name and name.lower() not in ["patient", "a", "the", "an", "cleaning", "checkup", "appointment", "doctor", "dr", "tomorrow", "today"]:
            return name.title()
    # Check if the entire text consists of a person's name (1-4 alphabetic words)
    words = text.strip().split()
    if 1 <= len(words) <= 4 and all(w.replace(".", "").replace("-", "").isalpha() for w in words):
        lower_txt = text.strip().lower()
        if lower_txt not in ["yes", "no", "ok", "okay", "sure", "thanks", "thank you", "cancel", "help", "hello", "hi", "hey", "booking", "appointment", "checkup", "cleaning", "dentist", "doctor"]:
            return text.strip().title()
    return None



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
                slots_list = []
                for res in tool_data.get("results", []):
                    doc_name = res.get("doctor_name", "Doctor")
                    slots = res.get("available_slots", [])
                    if slots:
                        slots_preview = ", ".join(slots[:4])
                        slots_list.append(f"• {doc_name}: {slots_preview}")
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
                    "content": "Here are our practicing dentists at SmileCare:\n\n" + "\n\n".join(doc_list) + "\n\nWould you like to check available appointment slots with any doctor?",
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
        cand_name = _extract_name(user_text)
        effective_name = cand_name or pending_name
        effective_phone = (phone_match.group(1).replace(" ", "").replace("-", "") if phone_match else None) or pending_phone


        from datetime import date, timedelta
        if date_match:
            target_date_str = date_match.group(1)
        elif "tomorrow" in user_text:
            target_date_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        elif "today" in user_text:
            target_date_str = date.today().strftime("%Y-%m-%d")
        elif req_date:
            target_date_str = req_date
        else:
            target_date_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")


        # Doctor / service overrides from text
        if "sara" in user_text:
            doc_id = 2
            doc_name = "Dr. Sara Malik"
        elif "ahmed" in user_text:
            doc_id = 1
            doc_name = "Dr. Ahmed Khan"

        if "whitening" in user_text:
            svc_id = 3
            svc_name = "Teeth Whitening"
        elif "checkup" in user_text or "consultation" in user_text:
            svc_id = 1
            svc_name = "Dental Checkup & Consultation"
        elif "root canal" in user_text:
            svc_id = 5
            svc_name = "Root Canal Treatment"
        elif "extraction" in user_text or "tooth pull" in user_text:
            svc_id = 4
            svc_name = "Tooth Extraction"
        elif "brace" in user_text or "aligner" in user_text:
            svc_id = 6
            svc_name = "Dental Braces Consultation"

        # 1. Non-Dental Out-of-Scope Medical Specialists
        out_of_scope_specialties = [
            "neurosurgeon", "neurologist", "neurology", "cardiologist", "cardiology",
            "dermatologist", "dermatology", "oncologist", "oncology", "orthopedic",
            "gynecologist", "psychiatrist", "ent specialist", "ophthalmologist",
            "general surgeon", "urologist", "nephrologist", "gastroenterologist"
        ]
        if any(s in user_text for s in out_of_scope_specialties) or ("pediatrician" in user_text and "dent" not in user_text):
            return {
                "content": "SmileCare is a dedicated dental clinic and does not offer non-dental medical specialties. I am connecting you with our human receptionist to see if they can assist or refer you.",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": f"Customer inquired about non-dental medical specialty: {user_text}"}}]
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

        # 4. State-Aware Slot/Time Selection & Booking
        effective_date = req_date or target_date_str
        doc_slots = last_offered_slots.get(str(doc_id)) or all_offered_slots

        # Case A: User is asking an availability question (e.g. "is there any other slots after 12:00pm", "what about 2pm?")
        if is_question and (time_token or any(w in user_text for w in ["slot", "other", "after", "before", "time", "available", "availability", "when"])):
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
            # Validate whether the time_token was actually offered in available_slots
            if doc_slots and time_token not in doc_slots:
                slots_preview = ", ".join(doc_slots[:4]) if doc_slots else "no open slots"
                return {
                    "content": f"The {time_token} slot is not available for {doc_name} on {effective_date}. The available slots on that day are: {slots_preview}. Please choose one of the available times or let me know if you would like to check another date.",
                    "tool_calls": []
                }

            effective_time = time_token
            return {
                "content": f"I have selected the {effective_time} slot on {effective_date} with {doc_name} for {svc_name}. To complete and confirm your booking, please provide your full name and contact phone number.",
                "tool_calls": []
            }

        # Case C: Both name and phone are available (either from current turn or pending state) -> proceed to book
        if (effective_phone and effective_name) and (time_token or req_time or "10:00" in user_text or "10 am" in user_text or "11:00" in user_text or "02:00" in user_text or "2 pm" in user_text or "9:30" in user_text or "9.30" in user_text):
            chosen_time = time_token or req_time or ("10:00" if "10" in user_text else (doc_slots[0] if doc_slots else "09:00"))
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

        # 5. Doctor Inquiry
        if any(w in user_text for w in ["which doctor", "who is the doctor", "who are the doctors", "list doctor", "available doctor", "dentist name", "doctors at"]):
            return {
                "content": "Let me retrieve our list of doctors for you.",
                "tool_calls": [{"name": "get_doctors", "arguments": {}}]
            }

        # 6. Services Inquiry
        if any(w in user_text for w in ["service", "price", "cost", "treatment", "charges", "what do you offer"]):
            return {
                "content": "Let me fetch our dental services and pricing for you.",
                "tool_calls": [{"name": "get_services", "arguments": {}}]
            }

        # 7. Clinic Info Inquiry
        if any(w in user_text for w in ["address", "location", "located", "where is", "where are", "timing", "hours", "contact", "phone number", "clinic info", "directions"]):
            return {
                "content": "Checking clinic details...",
                "tool_calls": [{"name": "get_clinic_info", "arguments": {}}]
            }

        # 8. Check Availability / Booking intent
        if any(w in user_text for w in ["book", "appointment", "slot", "schedule", "clean", "teeth", "doctor", "dentist", "visit", "available", "availability"]):
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

        # 9. Fallback handling:
        user_message_count = len([m for m in messages if m.get("role") == "user"])
        has_prior_assistant = any(m.get("role") == "assistant" for m in messages[:-1]) if len(messages) > 1 else False

        if user_message_count <= 1 and not has_prior_assistant:
            # Truly turn 0 / start of conversation
            return {
                "content": "Hello! Welcome to SmileCare Dental Clinic. How can I assist you with your dental care today?",
                "tool_calls": []
            }

        # Mid-conversation ambiguous or unhandled query -> ask clarifying question, never repeat greeting
        return {
            "content": "Sorry, I didn't quite catch that — are you asking about booking an appointment, our services, or something else?",
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
