import re
import json
from datetime import datetime
from typing import Dict, Any, List, Optional

DISTINCT_ROMAN_URDU_WORDS = {
    # Pronouns & Nouns
    "mera", "meri", "mere", "mery", "meray", "mujhe", "mujhey", "mujy", "mjhe", "mjy", "humein", "hmain",
    "aap", "tum", "unko", "inko", "inhe", "unhe", "isay", "usay", "naam", "sahib", "sahab", "jee", "yaar", "yar",
    "kisi", "wali", "wala", "wale", "inka", "unka", "in ka", "un ka",
    # Verbs & Modals
    "karna", "krna", "karwana", "krwana", "karein", "krden", "kardein", "kardo", "krdo", "karo", "kro", "kr", "kar",
    "chahiye", "chahta", "chahti", "chahte", "chahye", "hoga", "hogi", "hoge", "honge", "hona", "hua", "hui", "hue",
    "hoon", "hun", "tha", "thi", "thay", "hain", "hai", "ha", "bta", "btao", "btaen", "btaein", "batayein", "batao", "batana", "batadein", "btayein",
    "btadein", "dein", "dijiye", "dijye", "lena", "leni", "lene", "milega", "milegi", "milenge", "sakte", "sakti",
    "sakta", "skte", "skti", "skta", "jana", "jani", "jane", "aana", "aani", "aane", "aayein", "dekhna", "bolein",
    "bolna", "rakhein", "baithti", "baithty", "baithte", "hoti", "hoty", "hote", "hun gi", "hongi", "hon ge", "honge",
    # Prepositions & Connectors
    "sath", "sth", "saath", "paas", "lekin", "magar", "bhi", "liye", "lye", "kalye", "kelye", "klie", "keliye", "pehle", "pehly", "baad", "phir",
    # Adverbs & Time words
    "kal", "parso", "tarso", "aaj", "subha", "subah", "shaam", "dopahar", "raat", "baje", "bje", "bjay",
    "theek", "thik", "haan", "nahi", "nhi", "kitna", "kitni", "kitne", "konsa", "konsi", "konse", "kaun",
    "kahan", "wahan", "kyun", "kyu", "kese", "kaise", "kia", "kya", "achha", "accha", "sahi", "bilkul",
    "zarur", "zaroor", "shukriya", "meharbani", "mehrbani",
    # Dental / Medical & Booking in Roman
    "dant", "daant", "dard", "masla", "keera", "masoorhe", "masura", "takleef", "checkup"
}

ROMAN_URDU_PARTICLES = {"k", "ke", "ki", "ka", "ko", "ky", "se", "sy", "pe", "par", "mein", "mai", "ha", "hn", "hy", "ap"}

def detect_language(text: str, history_messages: Optional[List[Dict[str, Any]]] = None) -> str:
    if not text:
        text = ""

    if re.search(r'[\u0600-\u06FF]', text):
        return "urdu"

    tokens = set(re.findall(r'[a-zA-Z]+', text.lower()))
    if tokens.intersection(DISTINCT_ROMAN_URDU_WORDS):
        return "roman_urdu"

    if len(tokens.intersection(ROMAN_URDU_PARTICLES)) >= 2:
        return "roman_urdu"

    is_neutral = bool(re.match(r'^\s*(?:\d{4}-\d{2}-\d{2}|\d{1,2}[:.]\d{2}(?:\s*(?:am|pm))?|\d+|\+?\d+|yes|ok|okay|no|confirm|cancel|[a-zA-Z\s]{1,15})\s*$', text, re.IGNORECASE))
    if is_neutral and history_messages:
        for msg in reversed(history_messages):
            if msg.get("role") == "user":
                prev_text = msg.get("content", "")
                if re.search(r'[\u0600-\u06FF]', prev_text):
                    return "urdu"
                prev_tokens = set(re.findall(r'[a-zA-Z]+', prev_text.lower()))
                if prev_tokens.intersection(DISTINCT_ROMAN_URDU_WORDS):
                    return "roman_urdu"
                if len(prev_tokens.intersection(ROMAN_URDU_PARTICLES)) >= 2:
                    return "roman_urdu"

    return "english"


def _fmt_time_ampm(t_str: str) -> str:
    if not t_str:
        return ""
    try:
        parts = t_str.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        ampm = "AM" if h < 12 else "PM"
        h12 = h if (1 <= h <= 12) else (12 if h % 12 == 0 else h % 12)
        return f"{h12:02d}:{m:02d} {ampm}"
    except Exception:
        return str(t_str)


def _fmt_date_title(date_str: str, day_str: Optional[str] = None) -> str:
    if not date_str:
        return ""
    try:
        dt_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return dt_obj.strftime("%A, %B %d, %Y")
    except Exception:
        return f"{day_str}, {date_str}" if day_str else date_str


_URDU_MONTH_NAMES = {
    1: "جنوری", 2: "فروری", 3: "مارچ", 4: "اپریل",
    5: "مئی", 6: "جون", 7: "جولائی", 8: "اگست",
    9: "ستمبر", 10: "اکتوبر", 11: "نومبر", 12: "دسمبر"
}

_ROMAN_MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}


def _fmt_spoken_date_urdu(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        m_name = _URDU_MONTH_NAMES.get(dt.month, str(dt.month))
        return f"{dt.day} {m_name}"
    except Exception:
        return str(date_str)


def _fmt_spoken_date_roman(date_str: str) -> str:
    if not date_str:
        return ""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        m_name = _ROMAN_MONTH_NAMES.get(dt.month, str(dt.month))
        return f"{dt.day} {m_name}"
    except Exception:
        return str(date_str)


def _fmt_spoken_time_urdu(time_str: str) -> str:
    if not time_str:
        return ""
    try:
        parts = time_str.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        h12 = h if (1 <= h <= 12) else (12 if h % 12 == 0 else h % 12)
        min_str = f":{m:02d}" if m != 0 else ""

        if h < 12:
            period = "صبح"
        elif 12 <= h < 16:
            period = "دوپہر"
        elif 16 <= h < 19:
            period = "شام"
        else:
            period = "رات"

        return f"{period} {h12}{min_str} بجے"
    except Exception:
        return str(time_str)


def _fmt_spoken_time_roman(time_str: str) -> str:
    if not time_str:
        return ""
    try:
        parts = time_str.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        h12 = h if (1 <= h <= 12) else (12 if h % 12 == 0 else h % 12)
        min_str = f":{m:02d}" if m != 0 else ""

        if h < 12:
            period = "subah"
        elif 12 <= h < 16:
            period = "dopahar"
        elif 16 <= h < 19:
            period = "shaam"
        else:
            period = "raat"

        return f"{period} {h12}{min_str} baje"
    except Exception:
        return str(time_str)


def generate_tool_response(
    tool_name: str,
    tool_result: Dict[str, Any],
    conversation_state: Optional[Dict[str, Any]] = None,
    user_message: str = "",
    history_messages: Optional[List[Dict[str, Any]]] = None
) -> str:
    conv_state = conversation_state or {}
    lang = detect_language(user_message, history_messages)
    user_text_lower = user_message.lower()

    if tool_name == "check_availability":
        return _format_availability(tool_result, lang, user_text_lower, conv_state)
    elif tool_name == "book_appointment":
        return _format_booking(tool_result, lang, user_text_lower, conv_state)
    elif tool_name == "cancel_appointment":
        return _format_cancellation(tool_result, lang)
    elif tool_name == "reschedule_appointment":
        return _format_reschedule(tool_result, lang)
    elif tool_name == "get_doctors":
        return _format_doctors(tool_result, lang, user_text_lower, conv_state)
    elif tool_name == "get_services":
        return _format_services(tool_result, lang)
    elif tool_name == "get_clinic_info":
        return _format_clinic_info(tool_result, lang)
    elif tool_name == "human_handoff":
        if lang == "urdu":
            return "میں نے کلینک کے عملے کو مطلع کر دیا ہے۔ ہمارے نمائندے جلد ہی آپ سے رابطہ کریں گے۔ برائے مہربانی انتظار فرمائیں۔"
        elif lang == "roman_urdu":
            return "Maine clinic ke reception staff ko notify kar diya hai. Hamari team jald hi aap se contact kare gi. Barah-e-karam thora intezar karein."
        return "I have notified our clinic receptionist team. A human staff member will take over this conversation shortly to assist you. Please hold on."

    return str(tool_result.get("message", "Request processed successfully."))


def _format_availability(
    tool_data: Dict[str, Any],
    lang: str,
    user_text_lower: str,
    conv_state: Dict[str, Any]
) -> str:
    date_val = tool_data.get("date", "")
    day_val = tool_data.get("day", "")
    formatted_date = _fmt_date_title(date_val, day_val)
    doc_id = conv_state.get("selected_doctor_id")

    results = tool_data.get("results", [])
    if doc_id:
        filtered = [r for r in results if r.get("doctor_id") == doc_id]
        if filtered:
            results = filtered

    # Clean out explicit date strings before searching for time tokens
    text_without_dates = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '', user_text_lower)
    text_without_dates = re.sub(r'\b\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\b', '', text_without_dates)

    # Check for time filters ("after 12", "before 2") or exact requested time
    m_time = re.search(r'\b(\d{1,2}):(\d{2})\s*(am|pm)?\b|\b(\d{1,2})\s*(am|pm|baje|bje)\b|\b(\d{1,2})\s*بجے', text_without_dates)
    time_filter = None
    if m_time:
        try:
            if m_time.group(1) is not None:
                h = int(m_time.group(1))
                mn = int(m_time.group(2)) if m_time.group(2) else 0
                ap = m_time.group(3).lower() if m_time.group(3) else None
            elif m_time.group(4) is not None:
                h = int(m_time.group(4))
                mn = 0
                ap = m_time.group(5).lower() if m_time.group(5) else None
            else:
                h = int(m_time.group(6))
                mn = 0
                ap = None

            if ap == "pm" and h < 12:
                h += 12
            elif ap == "am" and h == 12:
                h = 0
            time_filter = f"{h:02d}:{mn:02d}"
        except Exception:
            pass

    is_after = "after" in user_text_lower and time_filter
    is_before = "before" in user_text_lower and time_filter

    has_open_slots = any(res.get("available_slots") for res in results)

    if not has_open_slots:
        next_d = tool_data.get("next_available_date")
        next_day = tool_data.get("next_available_day")
        day_str = (day_val or "").lower()
        is_sunday = "sunday" in day_str or "sunday" in formatted_date.lower()

        spoken_d_urdu = _fmt_spoken_date_urdu(date_val) if date_val else formatted_date
        spoken_d_roman = _fmt_spoken_date_roman(date_val) if date_val else formatted_date
        spoken_next_urdu = _fmt_spoken_date_urdu(next_d) if next_d else next_d
        spoken_next_roman = _fmt_spoken_date_roman(next_d) if next_d else next_d

        if lang == "urdu":
            if is_sunday:
                if next_d:
                    return f"معذرت، اتوار کو کلینک بند ہوتا ہے۔ ہمارے اوقات پیر تا ہفتہ صبح 9 بجے سے شام 5 بجے تک ہیں۔ اگلا دستیاب دن: {next_day}، {spoken_next_urdu}۔ کیا آپ اس دن کے اوقات دیکھنا چاہیں گے؟"
                return "معذرت، اتوار کو کلینک بند ہوتا ہے۔ ہمارے اوقات پیر تا ہفتہ صبح 9 بجے سے شام 5 بجے تک ہیں۔ آپ کس اور تاریخ کو تشریف لانا چاہیں گے؟"
            else:
                if next_d:
                    return f"معذرت، {spoken_d_urdu} کو کوئی وقت دستیاب نہیں ہے۔ اگلا دستیاب دن: {next_day}، {spoken_next_urdu}۔ کیا آپ اس تاریخ کے اوقات دیکھنا چاہیں گے؟"
                return f"معذرت، {spoken_d_urdu} کو کوئی وقت دستیاب نہیں ہے۔ آپ کس اور تاریخ کو تشریف لانا چاہیں گے؟"

        elif lang == "roman_urdu":
            if is_sunday:
                if next_d:
                    return f"Maazrat, Sunday ko clinic off hota hai. Hamare working days Monday se Saturday (09:00 AM – 05:00 PM) hain. Agla available din: {next_day} ({spoken_next_roman}). Kya aap is din ke slots dekhna chahein ge ya koi aur date prefer karein ge?"
                return "Maazrat, Sunday ko clinic off hota hai. Hamare working days Monday se Saturday hain. Aap kis date ko visit karna chahein ge?"
            else:
                if next_d:
                    return f"Maazrat, {spoken_d_roman} ko koi slot available nahi hai. Agla available din: {next_day} ({spoken_next_roman}). Kya aap is din ke slots dekhna chahein ge?"
                return f"Maazrat, {spoken_d_roman} ko koi slot available nahi hai. Aap kis date ko visit karna chahein ge?"

        else:
            if is_sunday:
                if next_d:
                    return f"Sorry, the clinic is closed on Sundays. Our working days are Monday to Saturday (09:00 AM – 05:00 PM). The next available day is {next_day}, {next_d}. Would you like to check slots for that day?"
                return "Sorry, the clinic is closed on Sundays. Our working days are Monday to Saturday (09:00 AM – 05:00 PM). Which date would you prefer?"
            else:
                if next_d:
                    return f"I checked our schedule for **{formatted_date}**, but there are no open slots. The next available opening is on **{next_day}, {next_d}**. Would you like to check slots for that day or choose another date?"
                return f"I checked our schedule for **{formatted_date}**, but there are no open slots on that day. Would you like to check another date?"

    req_time = conv_state.get("requested_time")
    matched_doc_result = next((r for r in results if req_time in r.get("available_slots", [])), None) if req_time else None

    if req_time and matched_doc_result and not is_after and not is_before:
        d_name = matched_doc_result.get("doctor_name", "your doctor")
        cust_name = conv_state.get("pending_customer_name")
        cust_phone = conv_state.get("pending_customer_phone")
        fmt_time = _fmt_time_ampm(req_time)

        spoken_d_urdu = _fmt_spoken_date_urdu(date_val)
        spoken_t_urdu = _fmt_spoken_time_urdu(req_time)
        spoken_d_roman = _fmt_spoken_date_roman(date_val)
        spoken_t_roman = _fmt_spoken_time_roman(req_time)

        if cust_name and not cust_phone:
            if lang == "urdu":
                return f"بہترین، {cust_name} صاحب! میں نے {spoken_d_urdu} کو {spoken_t_urdu} کا وقت آپ کے لیے محفوظ کر لیا ہے۔ بکنگ کو فائنل کرنے کے لیے، برائے مہربانی اپنا فون نمبر شیئر کر دیجیے تاکہ ہم آپ کو تصدیقی میسج بھیج سکیں۔"
            elif lang == "roman_urdu":
                return f"Behtareen, {cust_name}! Maine {spoken_d_roman} ko {spoken_t_roman} ka slot aap ke لیے mehfooz kar liya hai. Booking ko final karne ke liye apna contact number share kar dijiye taake hum aap ko confirmation message bhej sakein."
            return f"Wonderful, {cust_name}! I have reserved the {fmt_time} slot on {formatted_date} with {d_name} for you. To finalize your booking, could you please share your contact phone number so we can send your confirmation details?"
        elif not cust_name and not cust_phone:
            if lang == "urdu":
                return f"بہترین! میں نے {spoken_d_urdu} کو {spoken_t_urdu} کا وقت آپ کے لیے محفوظ کر لیا ہے۔ بکنگ کو فائنل کرنے کے لیے، کیا میں آپ کا پورا نام جان سکتا ہوں؟ اور ساتھ ہی اپنا فون نمبر بھی شیئر کر دیجیے تاکہ ہم آپ کو تصدیقی میسج بھیج سکیں۔"
            elif lang == "roman_urdu":
                return f"Behtareen! Maine {spoken_d_roman} ko {spoken_t_roman} ka slot aap ke liye mehfooz kar liya hai. Booking ko final karne ke liye, kya main aap ka poora naam jaan sakta hoon? Aur sath hi apna phone number bhi share kar dijiye taake hum aap ko confirmation message bhej sakein."
            return f"Perfect! I have reserved the {fmt_time} slot on {formatted_date} with {d_name} for you. To finalize your booking, may I please have your full name and contact phone number so we can send your confirmation message?"

    # If user explicitly requested a specific time that is NOT available, notify them clearly with available slots
    if time_filter and not is_after and not is_before and not (req_time and matched_doc_result):
        is_requested_time_available = any(time_filter in r.get("available_slots", []) for r in results)
        if not is_requested_time_available:
            fmt_req_time = _fmt_time_ampm(time_filter)
            target_doc_name = results[0].get("doctor_name", "the doctor") if results else "our doctor"
            all_slots_flat = []
            for r in results:
                for s in r.get("available_slots", []):
                    if s not in all_slots_flat:
                        all_slots_flat.append(s)
            slot_bullets = "\n".join([f"• {_fmt_time_ampm(s)}" for s in all_slots_flat[:10]])
            if lang == "urdu":
                spoken_d_u = _fmt_spoken_date_urdu(date_val)
                spoken_t_u = _fmt_spoken_time_urdu(time_filter)
                return (
                    f"معذرت، {spoken_d_u} کو {spoken_t_u} کا وقت {target_doc_name} کے لیے دستیاب نہیں ہے۔\n\n"
                    f"دستیاب اوقات:\n{slot_bullets}\n\n"
                    f"براہ کرم دستیاب اوقات میں سے کوئی وقت منتخب کریں۔"
                )
            elif lang == "roman_urdu":
                spoken_d_r = _fmt_spoken_date_roman(date_val)
                return (
                    f"{fmt_req_time} is not available for {target_doc_name} on {spoken_d_r}.\n\n"
                    f"Available times include:\n{slot_bullets}\n\n"
                    f"Barah-e-karam in mein se koi time slot choose karein."
                )
            else:
                return (
                    f"{fmt_req_time} is not available for {target_doc_name} on {formatted_date}.\n\n"
                    f"Available times include:\n{slot_bullets}\n\n"
                    f"Please choose one of the available slots."
                )

    lines = []
    for res in results:
        d_name = res.get("doctor_name", "Doctor")
        slots = res.get("available_slots", [])
        if not slots:
            continue

        if is_after:
            slots = [s for s in slots if s > time_filter]
        elif is_before:
            slots = [s for s in slots if s < time_filter]

        if not slots:
            continue

        morning = [_fmt_time_ampm(s) for s in slots if int(s.split(":")[0]) < 12]
        afternoon = [_fmt_time_ampm(s) for s in slots if int(s.split(":")[0]) >= 12]

        groups = []
        if morning:
            groups.append(f"  - **Morning:** {', '.join(morning)}")
        if afternoon:
            groups.append(f"  - **Afternoon:** {', '.join(afternoon)}")
        slots_text = "\n".join(groups) if groups else "  - " + ", ".join([_fmt_time_ampm(s) for s in slots])
        lines.append(f"• **{d_name}**:\n{slots_text}")

    if not lines:
        if lang == "urdu":
            return "معذرت، اس وقت کے مطابق کوئی سلاٹ دستیاب نہیں ہے۔ کیا آپ پہلے یا بعد کے اوقات چیک کرنا چاہیں گے؟"
        elif lang == "roman_urdu":
            return "Maazrat, aap ke matlooba time ke mutabiq koi slot available nahi hai. Kya aap doosra time check karna chahein ge?"
        return f"I checked our schedule for **{formatted_date}**, but there are no matching slots. Would you like to check other times or dates?"

    slots_body = "\n\n".join(lines)
    if lang == "urdu":
        spoken_date_urdu = _fmt_spoken_date_urdu(date_val) if date_val else formatted_date
        return (
            f"{spoken_date_urdu} کے لیے دستیاب اوقات:\n\n"
            f"{slots_body}\n\n"
            f"براہ کرم بتائیں کہ آپ کے لیے کون سا وقت مناسب رہے گا؟"
        )
    elif lang == "roman_urdu":
        spoken_date_roman = _fmt_spoken_date_roman(date_val) if date_val else formatted_date
        return (
            f"{spoken_date_roman} ke liye available appointment slots yeh hain:\n\n"
            f"{slots_body}\n\n"
            f"Barah-e-karam batayein aap ke liye konsa time slot behtareen rahe ga?"
        )
    else:
        return (
            f"Here are the available appointment slots on **{formatted_date}**:\n\n"
            f"{slots_body}\n\n"
            f"Please let me know which time slot works best for you!"
        )


def _format_booking(
    tool_data: Dict[str, Any],
    lang: str,
    user_text_lower: str,
    conv_state: Dict[str, Any]
) -> str:
    if not tool_data.get("success"):
        err = tool_data.get("error", "Could not complete booking.")
        if lang == "urdu":
            return f"معذرت، بکنگ مکمل نہیں ہو سکی: {err}"
        elif lang == "roman_urdu":
            return f"Maazrat, aap ki booking complete nahi ho saki: {err}"
        return f"Sorry, we could not complete your booking: {err}"

    if tool_data.get("is_duplicate_request"):
        if lang == "urdu":
            return "🎉 **آپ کی اپائنٹمنٹ پہلے ہی تصدیق شدہ ہے!** ہم سمائل کیئر ڈینٹل کلینک میں آپ کے منتظر ہیں۔ کیا میں آپ کی مزید کوئی مدد کر سکتا ہوں؟"
        elif lang == "roman_urdu":
            return "Aap ki appointment already confirmed hai! Hum SmileCare Dental Clinic mein aap ke muntazir hain. Agar koi mazeed sawal ho to zaroor batayein!"
        return "Your appointment has already been confirmed! We look forward to seeing you at SmileCare Dental Clinic."

    appt = tool_data.get("appointment", {})
    appt_id = appt.get("id", 1)
    patient = appt.get("patient_name") or appt.get("customer_name") or conv_state.get("pending_customer_name") or "Valued Patient"
    doctor = appt.get("doctor_name", "Dr. Ahmed Khan")
    service = appt.get("service_name", "Dental Consultation")
    date_str = appt.get("appointment_date", "")
    time_str = appt.get("appointment_time", "")
    formatted_time = _fmt_time_ampm(time_str)
    address = "Plot 42-B, Main Boulevard, Gulberg III, Lahore"

    if lang == "urdu":
        return (
            f"🎉 **آپ کی اپائنٹمنٹ کامیابی سے بک اور تصدیق (Confirmed) ہو گئی ہے!**\n\n"
            f"• **اپائنٹمنٹ آئی ڈی:** #{appt_id}\n"
            f"• **مریض کا نام:** {patient}\n"
            f"• **ڈاکٹر:** {doctor}\n"
            f"• **سروس:** {service}\n"
            f"• **تاریخ اور وقت:** {date_str} بوقت {formatted_time}\n"
            f"• **کلینک کا پتہ:** {address}\n\n"
            f"آپ کی یاد دہانی کا شیڈول خودکار طور پر ترتیب دے دیا گیا ہے۔ براہ کرم وقت سے 10 منٹ پہلے تشریف لائیں۔ کیا میں آپ کی مزید کوئی مدد کر سکتا ہوں؟"
        )
    elif lang == "roman_urdu":
        return (
            f"🎉 **Aap ki appointment confirm ho gayi hai!**\n\n"
            f"• **Appointment ID:** #{appt_id}\n"
            f"• **Patient Name:** {patient}\n"
            f"• **Doctor:** {doctor}\n"
            f"• **Service:** {service}\n"
            f"• **Date & Time:** {date_str} at {formatted_time}\n"
            f"• **Clinic Address:** {address}\n\n"
            f"Aap ki visit ke liye reminder schedule kar diya gaya hai. Barah-e-karam 10 minute pehle tashreef layein. Agar koi mazeed sawal ho to zaroor batayein!"
        )
    else:
        return (
            f"🎉 **Your appointment has been successfully booked and confirmed!**\n\n"
            f"• **Appointment ID:** #{appt_id}\n"
            f"• **Patient Name:** {patient}\n"
            f"• **Doctor:** {doctor}\n"
            f"• **Service:** {service}\n"
            f"• **Date & Time:** {date_str} at {formatted_time}\n"
            f"• **Location:** {address}\n\n"
            f"A reminder has been scheduled. Please arrive 10 minutes prior to your scheduled time. How else may I assist you today?"
        )


def _format_cancellation(tool_data: Dict[str, Any], lang: str) -> str:
    if not tool_data.get("success"):
        err = tool_data.get("error", "Could not cancel appointment.")
        if lang == "urdu":
            return f"معذرت، اپائنٹمنٹ منسوخ نہیں ہو سکی: {err}"
        elif lang == "roman_urdu":
            return f"Maazrat, appointment cancel nahi ho saki: {err}"
        return f"Sorry, your appointment could not be cancelled: {err}"

    if lang == "urdu":
        return "آپ کی اپائنٹمنٹ کامیابی سے منسوخ کر دی گئی ہے۔ کیا آپ کسی اور تاریخ یا وقت کے لیے بکنگ کروانا چاہیں گے؟"
    elif lang == "roman_urdu":
        return "Aap ki appointment successfully cancel kar di gayi hai. Kya aap kisi aur date ya time ke liye reschedule karwana chahein ge?"
    return "Your appointment has been successfully cancelled. Would you like to reschedule for another date?"


def _format_reschedule(tool_data: Dict[str, Any], lang: str) -> str:
    if not tool_data.get("success"):
        err = tool_data.get("error", "Could not reschedule appointment.")
        if lang == "urdu":
            return f"معذرت، تاریخ تبدیل نہیں ہو سکی: {err}"
        elif lang == "roman_urdu":
            return f"Maazrat, appointment reschedule nahi ho saki: {err}"
        return f"Sorry, we could not reschedule your appointment: {err}"

    appt = tool_data.get("appointment", {})
    new_date = appt.get("appointment_date", "")
    new_time = _fmt_time_ampm(appt.get("appointment_time", ""))
    doctor = appt.get("doctor_name", "Your Doctor")

    if lang == "urdu":
        return f"آپ کی اپائنٹمنٹ {doctor} کے ساتھ تبدیل کر کے **{new_date} بوقت {new_time}** طے کر دی گئی ہے۔"
    elif lang == "roman_urdu":
        return f"Aap ki appointment successfully update kar di gayi hai: **{new_date} at {new_time}** with {doctor}."
    return f"Your appointment has been successfully rescheduled to **{new_date} at {new_time}** with {doctor}."


def _format_doctor_schedule_lines(doc: Dict[str, Any], target_day: Optional[str] = None) -> List[str]:
    WEEKDAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    sched_list = doc.get("weekly_schedule", [])

    if sched_list:
        lines = []
        sched_map = {s["day_of_week"]: s for s in sched_list}
        days_to_show = [target_day] if target_day else WEEKDAY_ORDER
        for day in days_to_show:
            s_entry = sched_map.get(day)
            if s_entry and s_entry.get("is_available"):
                st = _fmt_time_ampm(s_entry.get("start_time", "09:00"))
                et = _fmt_time_ampm(s_entry.get("end_time", "17:00"))
                lines.append(f"• {day}: {st} – {et}")
            else:
                lines.append(f"• {day}: Closed")
        return lines

    working_days = [d.strip() for d in (doc.get("working_days") or "").split(",") if d.strip()]
    st = _fmt_time_ampm(doc.get("start_time", "09:00"))
    et = _fmt_time_ampm(doc.get("end_time", "17:00"))

    lines = []
    days_to_show = [target_day] if target_day else WEEKDAY_ORDER
    for day in days_to_show:
        if day in working_days:
            lines.append(f"• {day}: {st} – {et}")
        else:
            lines.append(f"• {day}: Closed")
    return lines


def _format_doctors(
    tool_data: Dict[str, Any],
    lang: str,
    user_text_lower: str,
    conv_state: Dict[str, Any]
) -> str:
    doctors = tool_data if isinstance(tool_data, list) else tool_data.get("doctors", [])
    if not doctors:
        if lang == "urdu":
            return "اس وقت کوئی ڈاکٹر دستیاب نہیں ہے۔"
        elif lang == "roman_urdu":
            return "Is waqt koi doctor available nahi hai."
        return "No doctors are currently available."

    target_doc = None
    if conv_state.get("selected_doctor_id"):
        target_doc = next((d for d in doctors if d.get("id") == conv_state.get("selected_doctor_id")), None)

    if not target_doc:
        for d in doctors:
            d_name_lower = d.get("name", "").lower()
            for part in d_name_lower.split():
                if len(part) > 3 and part in user_text_lower:
                    target_doc = d
                    break
            if target_doc:
                break

    is_full_weekly_query = any(w in user_text_lower for w in [
        "weekly", "weekly schedule", "ہفتہ وار", "hafte war", "hafta war", "pure hafte", "pure haftey", "saare din", "sare din"
    ])

    WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    target_day = None
    if not is_full_weekly_query:
        for wd in WEEKDAYS:
            if re.search(r'\b' + wd.lower() + r'\b', user_text_lower):
                target_day = wd
                break
        if not target_day:
            URDU_WEEKDAYS = {
                "پیر": "Monday", "سوموار": "Monday", "منگل": "Tuesday", "بدھ": "Wednesday",
                "جمعرات": "Thursday", "جمعہ": "Friday", "ہفتہ": "Saturday", "اتوار": "Sunday",
                "jummah": "Friday", "itwar": "Sunday", "peer": "Monday", "mangal": "Tuesday",
                "budh": "Wednesday", "jumeraat": "Thursday", "juma": "Friday", "hafta": "Saturday"
            }
            for u_wd, e_wd in URDU_WEEKDAYS.items():
                if re.search(r'\b' + re.escape(u_wd) + r'\b' if u_wd.isascii() else r'(?:^|\s)' + re.escape(u_wd) + r'(?:$|\s)', user_text_lower):
                    target_day = e_wd
                    break

    has_schedule_intent = is_full_weekly_query or target_day is not None or any(w in user_text_lower for w in [
        "schedule", "timing", "timings", "hours", "waqt", "time", "working days", "ka time", "ki timing",
        "ka schedule", "kis din", "kis کس din", "kab available", "kab hoti", "kab hoty", "kab baithti",
        "شیڈول", "ہفتہ وار", "ٹائمنگ", "اوقات", "کس دن", "کس کس دن", "کب", "کا وقت", "کی ٹائمنگ", "کا شیڈول"
    ])

    if target_doc and has_schedule_intent:
        sched_lines = _format_doctor_schedule_lines(target_doc, target_day=target_day)
        sched_body = "\n".join(sched_lines)

        if lang == "urdu":
            header = f"{target_doc['name']} کا {target_day + ' کا ' if target_day else 'ہفتہ وار '}شیڈول:"
            footer = "آپ کس تاریخ کے لیے اپائنٹمنٹ بک کروانا چاہیں گے؟"
            return f"{header}\n\n{sched_body}\n\n{footer}"
        elif lang == "roman_urdu":
            if target_day:
                header = f"{target_doc['name']} ka {target_day} ka schedule:"
                footer = "Aap kis date ya time ke liye appointment book karwana chahein ge?"
            else:
                header = f"Bilkul! {target_doc['name']} ka weekly schedule:"
                footer = "Aap kis date ya din ke liye appointment book karwana chahein ge?"
            return f"{header}\n\n{sched_body}\n\n{footer}"
        else:
            header = f"{target_doc['name']}'s {'schedule for ' + target_day if target_day else 'Weekly Schedule'}:"
            footer = "Which date or time would you prefer for your appointment?"
            return f"{header}\n\n{sched_body}\n\n{footer}"

    lines = []
    for d in doctors:
        wk_days = d.get("working_days", "Monday to Saturday")
        st = _fmt_time_ampm(d.get("start_time", "09:00"))
        et = _fmt_time_ampm(d.get("end_time", "17:00"))
        lines.append(f"• **{d['name']}** - {d.get('specialization', 'General Dentistry')} (Working Days: {wk_days}, Hours: {st} – {et})")

    docs_body = "\n\n".join(lines)
    if lang == "urdu":
        return f"سمائل کیئر میں ہمارے پریکٹس کرنے والے دانتوں کے ڈاکٹر یہ ہیں:\n\n{docs_body}\n\nآپ کس ڈاکٹر سے اپائنٹمنٹ لینا پسند کریں گے؟"
    elif lang == "roman_urdu":
        return f"SmileCare ke available doctors yeh hain:\n\n{docs_body}\n\nBarah-e-karam batayein aap kis doctor ke sath appointment book karwana chahein ge?"
    return f"Of course. Here are our practicing dentists at SmileCare:\n\n{docs_body}\n\nWhich doctor would you prefer?"


def _format_services(tool_data: Dict[str, Any], lang: str) -> str:
    services = tool_data if isinstance(tool_data, list) else tool_data.get("services", [])
    if not services:
        if lang == "urdu":
            return "اس وقت کوئی سروس دستیاب نہیں ہے۔"
        elif lang == "roman_urdu":
            return "Is waqt koi service available nahi hai."
        return "No services are currently listed."

    lines = []
    for s in services:
        price = s.get("price", 0)
        dur = s.get("duration", 30)
        desc = s.get("description", "")
        lines.append(f"• **{s['name']}** ({dur} mins) - PKR {price:,.0f}: {desc}")

    svcs_body = "\n\n".join(lines)
    if lang == "urdu":
        return f"ہماری ڈینٹل سروسز کی مکمل فہرست درج ذیل ہے:\n\n{svcs_body}\n\nکیا آپ ان میں سے کسی سروس کے لیے بکنگ کروانا چاہتے ہیں؟"
    elif lang == "roman_urdu":
        return f"SmileCare ki dental services aur unki pricing yeh hai:\n\n{svcs_body}\n\nKya aap in mein se kisi service ke liye appointment book karna chahte hain?"
    return f"Here is our complete list of dental services:\n\n{svcs_body}\n\nWould you like to book an appointment for any of these services?"


def _format_clinic_info(tool_data: Dict[str, Any], lang: str) -> str:
    name = tool_data.get("name", "SmileCare Dental Clinic")
    address = tool_data.get("address", "Plot 42-B, Main Boulevard, Gulberg III, Lahore")
    phone = tool_data.get("phone", "+92 42 35789000")
    hours = tool_data.get("opening_hours", "Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed")
    policies = tool_data.get("policies", "Please arrive 10 minutes before your appointment.")
    fee = tool_data.get("consultation_fee", 2000.0)

    if lang == "urdu":
        return (
            f"**{name} معلومات:**\n\n"
            f"• **پتہ:** {address}\n"
            f"• **فون نمبر:** {phone}\n"
            f"• **اوقات کار:** {hours}\n"
            f"• **چیک اپ فیس:** PKR {fee:,.0f}\n"
            f"• **پالیسی:** {policies}\n\n"
            f"کیا آپ اپائنٹمنٹ بک کروانا چاہیں گے؟"
        )
    elif lang == "roman_urdu":
        return (
            f"**{name} Clinic Information:**\n\n"
            f"• **Address:** {address}\n"
            f"• **Phone:** {phone}\n"
            f"• **Hours:** {hours}\n"
            f"• **Consultation Fee:** PKR {fee:,.0f}\n"
            f"• **Policy:** {policies}\n\n"
            f"Kya aap appointment book karwana chahein ge?"
        )
    return (
        f"**{name} Information:**\n\n"
        f"• **Address:** {address}\n"
        f"• **Phone:** {phone}\n"
        f"• **Hours:** {hours}\n"
        f"• **Consultation Fee:** PKR {fee:,.0f}\n"
        f"• **Policies:** {policies}\n\n"
        f"How may I assist you with booking?"
    )
