import os
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from config.config import Config
from ai.tools import CANONICAL_TOOLS

class BaseLLMAdapter:
    def chat_completion(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]]
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
    """
    def chat_completion(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        if not messages:
            return {"content": "Hello! Welcome to SmileCare Dental Clinic. How can I assist you with your dental care today?", "tool_calls": []}

        # Check if previous turn was a tool response
        last_msg = messages[-1]
        if last_msg["role"] == "tool":
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

        # Analyze latest user query
        user_text = ""
        for m in reversed(messages):
            if m["role"] == "user":
                user_text = m["content"].lower()
                break

        # 1. Human handoff / Out-of-scope triggers
        if any(w in user_text for w in ["human", "receptionist", "speak to someone", "representative", "real person", "manager", "staff"]):
            return {
                "content": "Connecting you with our reception team...",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": "Customer requested human representative"}}]
            }

        # Medical advice or insurance out-of-scope
        if any(w in user_text for w in ["insurance", "prescription", "antibiotic", "diagnose", "severe bleeding"]):
            return {
                "content": "I cannot provide medical advice or verify insurance coverage directly. Let me connect you with our medical staff.",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": f"Out-of-scope / medical query: {user_text}"}}]
            }

        # 2. Services inquiry
        if any(w in user_text for w in ["service", "price", "cost", "treatment", "charges", "what do you offer"]):
            return {
                "content": "Let me fetch our dental services and pricing for you.",
                "tool_calls": [{"name": "get_services", "arguments": {}}]
            }

        # 3. Clinic Info inquiry
        if any(w in user_text for w in ["address", "location", "timing", "hours", "where are you", "contact", "phone number"]):
            return {
                "content": "Checking clinic details...",
                "tool_calls": [{"name": "get_clinic_info", "arguments": {}}]
            }

        # 4. Booking intent detection
        # Check if booking details (name, phone, date, time) are present in the text
        phone_match = re.search(r'(\+?\d[\d\s\-]{8,14}\d)', user_text)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', user_text)
        time_match = re.search(r'(\d{1,2}:\d{2})', user_text)

        # Check if user mentioned tomorrow / next week date
        from datetime import date, timedelta
        target_date_str = None
        if date_match:
            target_date_str = date_match.group(1)
        elif "tomorrow" in user_text:
            target_date_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        elif "today" in user_text:
            target_date_str = date.today().strftime("%Y-%m-%d")
        else:
            # Default to tomorrow if they ask to book
            target_date_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

        # Determine doctor
        doctor_id = 1
        if "sara" in user_text:
            doctor_id = 2
        elif "ahmed" in user_text:
            doctor_id = 1

        # Determine service
        service_id = 2 # default cleaning
        if "whitening" in user_text:
            service_id = 3
        elif "checkup" in user_text or "consultation" in user_text:
            service_id = 1
        elif "root canal" in user_text:
            service_id = 5
        elif "extraction" in user_text or "tooth pull" in user_text:
            service_id = 4
        elif "brace" in user_text or "aligner" in user_text:
            service_id = 6

        # Check if customer provided name, phone and time to book
        if phone_match and (time_match or "10:00" in user_text or "10 am" in user_text or "11:00" in user_text or "02:00" in user_text or "2 pm" in user_text):
            chosen_time = time_match.group(1) if time_match else ("10:00" if "10" in user_text else "14:00")
            phone = phone_match.group(1).replace(" ", "").replace("-", "")
            # Extract possible name
            name = "Patient"
            for part in user_text.split():
                if part.isalpha() and part not in ["hi", "hello", "my", "name", "is", "please", "book", "at", "on", "for", "pm", "am"]:
                    name = part.capitalize()
                    break
            return {
                "content": f"Booking your appointment with Dr. {'Sara Malik' if doctor_id==2 else 'Ahmed Khan'} for {target_date_str} at {chosen_time}...",
                "tool_calls": [{
                    "name": "book_appointment",
                    "arguments": {
                        "customer_name": name,
                        "customer_phone": phone,
                        "doctor_id": doctor_id,
                        "service_id": service_id,
                        "appointment_date": target_date_str,
                        "appointment_time": chosen_time,
                        "notes": "Booked via AI Assistant"
                    }
                }]
            }

        # If user asks to book or check slots
        if any(w in user_text for w in ["book", "appointment", "slot", "schedule", "clean", "teeth", "doctor", "dentist", "visit"]):
            return {
                "content": f"Checking open slots for you on {target_date_str}...",
                "tool_calls": [{
                    "name": "check_availability",
                    "arguments": {
                        "date": target_date_str,
                        "doctor_id": doctor_id,
                        "service_id": service_id
                    }
                }]
            }

        # Default conversational greeting
        return {
            "content": "Hello! I am the AI receptionist at SmileCare Dental Clinic. I can help you book, reschedule, or cancel dental appointments, check doctor availability, and share service details. How may I help you today?",
            "tool_calls": []
        }


class GeminiAdapter(BaseLLMAdapter):
    """Google Gemini Provider Adapter with structured function declarations."""
    def __init__(self, api_key: str, model_name: str = "gemini-2.5-flash"):
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
        tools: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            
            # Format messages for Gemini
            contents = []
            for m in messages:
                role = "user" if m["role"] in ["user", "system"] else "model"
                if m["role"] == "tool":
                    # Tool response part
                    contents.append(types.Content(
                        role="user",
                        parts=[types.Part.from_function_response(
                            name=m.get("tool_name", "tool"),
                            response={"result": m["content"]}
                        )]
                    ))
                else:
                    contents.append(types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=m["content"])]
                    ))

            gemini_tools = self._translate_tools_to_gemini(tools)
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=gemini_tools,
                temperature=0.2
            )

            response = client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )

            # Check for tool calls
            tool_calls = []
            text_content = ""
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        tool_calls.append({
                            "name": part.function_call.name,
                            "arguments": dict(part.function_call.args) if part.function_call.args else {}
                        })
                    if part.text:
                        text_content += part.text

            return {
                "content": text_content.strip(),
                "tool_calls": tool_calls
            }

        except Exception as e:
            print(f"[GeminiAdapter Error]: {e}")
            # Fallback to MockAdapter on API error so flow does not crash
            mock = MockAdapter()
            return mock.chat_completion(system_prompt, messages, tools)


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
        tools: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        try:
            from groq import Groq
            client = Groq(api_key=self.api_key)

            formatted_messages = [{"role": "system", "content": system_prompt}]
            for i, m in enumerate(messages):
                role = m.get("role")
                if role == "assistant" and m.get("tool_calls"):
                    # Replay the assistant turn that requested tool calls — required by
                    # OpenAI/Groq protocol so subsequent tool messages are correctly associated.
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
                    # Echo the real tool_call_id from history — never hardcode "call_1"
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
                        "id": tc.id,         # preserve real Groq-assigned ID
                        "name": tc.function.name,
                        "arguments": args
                    })

            return {
                "content": choice.content or "",
                "tool_calls": tool_calls
            }
        except Exception as e:
            print(f"[GroqAdapter Error]: {e}")
            mock = MockAdapter()
            return mock.chat_completion(system_prompt, messages, tools)




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
        tools: List[Dict[str, Any]] = CANONICAL_TOOLS
    ) -> Dict[str, Any]:
        return self.adapter.chat_completion(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools
        )
