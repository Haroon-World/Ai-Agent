import json
import re
import time
import difflib
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from config.config import Config
from ai.tools import CANONICAL_TOOLS
from ai.response_generator import detect_language, _format_doctor_schedule_lines

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None  # type: ignore
    genai_types = None  # type: ignore

try:
    from groq import Groq
except ImportError:
    Groq = None  # type: ignore

WEEKDAY_MAP = {
    "monday": 0, "mon": 0, "somwar": 0, "peer": 0, "پیر": 0, "سوموار": 0,
    "tuesday": 1, "tue": 1, "mangal": 1, "منگل": 1,
    "wednesday": 2, "wed": 2, "budh": 2, "بدھ": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "jumeraat": 3, "jumerat": 3, "جمعرات": 3,
    "friday": 4, "fri": 4, "jummah": 4, "juma": 4, "jumma": 4, "جمعہ": 4,
    "saturday": 5, "sat": 5, "hafta": 5, "ہفتہ": 5,
    "sunday": 6, "sun": 6, "itwar": 6, "اتوار": 6
}

MONTH_MAP = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12
}


def resolve_date_string(user_content: str, business_id: int = 1) -> Optional[str]:
    """
    Parse a natural language date expression into 'YYYY-MM-DD'.
    Handles ISO dates (2026-08-25), relative words (today, tomorrow, parson, kal, aaj, کل, آج, پرسوں),
    weekdays (Friday, jummah, جمعہ), and explicit dates (August 28, 28th Aug).
    Uses the configured clinic business timezone (Asia/Karachi).
    """
    if not user_content:
        return None

    text_lower = user_content.lower().strip()

    try:
        from services.booking_service import _get_business_tz
        tz = _get_business_tz(business_id)
        today = datetime.now(tz).date()
    except Exception:
        today = datetime.now().date()

    # 1. ISO format YYYY-MM-DD
    iso_match = re.search(r'\b(\d{4}-\d{2}-\d{2})\b', user_content)
    if iso_match:
        return iso_match.group(1)

    # 2. Relative keywords (English, Roman Urdu, and Urdu script) with strict word boundaries
    if re.search(r'\b(?:day after tomorrow|parson|parso|پرسوں|پر چوتھ)\b', text_lower):
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")
    if re.search(r'\b(?:tomorrow|kal|کل)\b', text_lower):
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")
    if re.search(r'\b(?:today|aaj|آج)\b', text_lower):
        return today.strftime("%Y-%m-%d")

    # 3. Explicit Month + Day (e.g. August 24, 24 August, Aug 24th)
    m1 = re.search(r'\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)\s+(\d{1,2})(st|nd|rd|th)?\b', text_lower)
    if m1:
        month_num = MONTH_MAP.get(m1.group(1).lower(), 1)
        day_num = int(m1.group(2))
        try:
            target_year = today.year
            d = date(target_year, month_num, day_num)
            if d < today - timedelta(days=30):
                d = date(target_year + 1, month_num, day_num)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    m2 = re.search(r'\b(\d{1,2})(st|nd|rd|th)?\s+(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|august|aug|september|sep|sept|october|oct|november|nov|december|dec)\b', text_lower)
    if m2:
        day_num = int(m2.group(1))
        month_num = MONTH_MAP.get(m2.group(3).lower(), 1)
        try:
            target_year = today.year
            d = date(target_year, month_num, day_num)
            if d < today - timedelta(days=30):
                d = date(target_year + 1, month_num, day_num)
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # 4. Relative Weekdays (Monday..Sunday, Roman Urdu & Urdu script)
    is_schedule_time_query = any(w in text_lower for w in [
        "ka time", "ki timing", "ka schedule", "ki schedule", "ke schedule", "weekly schedule", "working hours",
        "skedule", "skedyool", "timetable",
        "شیڈول", "سکیجویل", "سکیڈول", "ہفتہ وار", "ٹائمنگ", "اوقات", "کا وقت", "کی ٹائمنگ", "کا شیڈول"
    ]) or (
        any(w in text_lower for w in ["schedule", "timing", "timings", "شیڈول", "سکیجویل", "سکیڈول", "ٹائمنگ"])
        and not any(w in text_lower for w in ["book", "appointment", "fix", "اپائنٹمنٹ", "reserve", "slot", "slots", "available on", "available at", "kal", "tomorrow"])
    )
    if not is_schedule_time_query:
        for day_word, target_weekday in WEEKDAY_MAP.items():
            pattern = r'\b' + re.escape(day_word) + r'\b' if day_word.isascii() else r'(?:^|\s)' + re.escape(day_word) + r'(?:$|\s)'
            if re.search(pattern, text_lower):
                days_ahead = (target_weekday - today.weekday()) % 7
                if days_ahead == 0 and ("next" in text_lower or "coming" in text_lower or "اگلے" in text_lower):
                    days_ahead = 7
                elif days_ahead == 0 and not any(w in text_lower for w in ["today", "aaj", "آج"]):
                    days_ahead = 7
                return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return None


_NAME_PREFIX_RE = re.compile(r'\b(dr\.?|doctor|ڈاکٹر)\b\s*', re.IGNORECASE)

# Generic words that appear across many roster entries (e.g. "Dental Checkup",
# "Dental Cleaning", "Dental Braces" all share "dental") and so must never be
# treated as a strong/distinctive match signal on their own — otherwise the
# first roster entry containing the shared word wins by coincidence of
# iteration order rather than actually matching what the user said.
_GENERIC_MATCH_STOPWORDS = {
    "dental", "and", "the", "for", "with", "clinic", "care", "treatment",
    "services", "service", "appointment", "dr", "doctor",
    "tooth", "teeth", "dant", "daant", "problem", "masla", "issue"
}


def _fmt_time_ampm(t_str: str) -> str:
    """Format 24-hour time HH:MM into clean human-friendly 12-hour AM/PM format (e.g. 09:00 AM, 02:30 PM)."""
    if not t_str:
        return ""
    try:
        parts = t_str.strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        ap = "AM" if h < 12 else "PM"
        h12 = h if (1 <= h <= 12) else (12 if h % 12 == 0 else h % 12)
        return f"{h12:02d}:{m:02d} {ap}"
    except Exception:
        return str(t_str)


def _extract_doctor_mention(text: str) -> Optional[str]:
    """
    Extract a doctor name explicitly mentioned in user text (e.g. 'Dr. Hassan', 'Dr Sara', 'doctor ahmed', 'ڈاکٹر حسن').
    """
    if not text:
        return None
    lower = text.lower()
    general_inquiry_phrases = [
        "doctors name", "doctor name", "doctor names", "doctors list", "doctor list",
        "tell me your doctors", "tell me doctors", "who are your doctors", "which doctors",
        "available doctors", "list of doctors", "our doctors", "your doctors", "about your doctors",
        "ڈاکٹرز", "ڈاکٹروں", "ڈاکٹر کون", "کون سے ڈاکٹر", "ڈاکٹر کے نام"
    ]
    if any(p in lower for p in general_inquiry_phrases):
        return None

    # Match patterns like "dr. hassan", "dr hassan", "doctor hassan" (require space after dr/doctor)
    m = re.search(r'\b(?:dr\.?|doctor)\s+([a-zA-Z]{3,20})\b', text, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        non_names = {
            'appointment', 'booking', 'clinic', 'schedule', 'timing', 'timings',
            'fee', 'fees', 'checkup', 'cleaning', 'consultation', 'available', 'availability',
            'name', 'names', 'list', 'details', 'info', 'information', 'who', 'which', 'what',
            'please', 'help', 'time', 'times', 'hours', 'service', 'services'
        }
        if name.lower() not in non_names:
            return name

    # Urdu script doctor match:
    m_urdu = re.search(r'ڈاکٹر\s+([\u0600-\u06FF]{3,20})', text)
    if m_urdu:
        name = m_urdu.group(1).strip()
        if name not in ['صاحب', 'صاحبہ', 'کا', 'کی', 'کے', 'سے', 'کون', 'نام']:
            return name
    return None


def _fuzzy_match_roster(user_text: str, roster: List[Dict[str, Any]], threshold: float = 0.75) -> Optional[Dict[str, Any]]:
    """
    Match a user's free-text reply against a real DB roster (doctors or
    services) using token-level matching, spelling normalizations, and word boundaries.
    """
    if not user_text or not roster:
        return None

    raw_lower = user_text.lower().strip()

    # 1. Mask out self-name expressions so customer's name isn't confused for a doctor
    # e.g., "my name is Ahmed", "mera naam Ahmed hai", "میرا نام احمد ہے"
    name_masked_text = raw_lower
    name_patterns = [
        r'(?:my\s+(?:own\s+)?name\s+is|mera\s+naam|i\'?m|i\s+am|this\s+is|name\s*:)\s+([a-zA-Z\u0600-\u06FF]+(?:\s+[a-zA-Z\u0600-\u06FF]+)?)',
        r'(?:میرا\s+نام|نام\s*:\s*)\s*([\u0600-\u06FF\w]+(?:\s+[\u0600-\u06FF\w]+)?)'
    ]
    for np in name_patterns:
        m = re.search(np, name_masked_text, re.IGNORECASE)
        if m:
            name_masked_text = name_masked_text.replace(m.group(0), " [customer_name] ")

    cleaned = _NAME_PREFIX_RE.sub('', name_masked_text).strip()
    if not cleaned or not any(c.isalnum() for c in cleaned):
        return None

    # Common Urdu script & Roman transliteration synonyms
    synonym_map = {
        "سارہ": "sara", "سارا": "sara", "سارے": "sara", "ساری": "sara", "ساراہ": "sara",
        "احمد": "ahmed", "احسن": "ahsan", "خان": "khan", "ملک": "malik", "علی": "ali",
        "ڈاکٹر": "dr", "صفائی": "cleaning", "کلیننگ": "cleaning", "چیک اپ": "checkup",
        "چیکپ": "checkup", "مشورہ": "consultation", "وائٹننگ": "whitening",
        "بریسز": "braces", "روٹ کینال": "root canal", "دانت نکالنا": "extraction",
        "ahmad": "ahmed", "ahmd": "ahmed", "sarah": "sara", "drahmed": "ahmed", "drsara": "sara",
        "check-up": "checkup", "check up": "checkup", "regular checkup": "checkup",
        "routine checkup": "checkup", "dental checkup": "checkup"
    }
    expanded_text = cleaned
    for u_word, e_trans in synonym_map.items():
        if u_word in name_masked_text or u_word in expanded_text:
            expanded_text += f" {e_trans}"

    tokens = [w for w in re.findall(r'[a-zA-Z\u0600-\u06FF]+', expanded_text.lower()) if len(w) >= 2]

    best_entry = None
    best_score = 0.0

    for entry in roster:
        name = entry.get("name", "")
        if not name:
            continue
        name_lower = name.lower()
        name_clean = _NAME_PREFIX_RE.sub('', name_lower).strip()
        word_candidates = [
            w for w in name_clean.split()
            if len(w) >= 3 and w not in _GENERIC_MATCH_STOPWORDS
        ]
        candidates = [name_clean] + word_candidates

        for cand in candidates:
            if not cand or len(cand) < 3:
                continue

            # Check if this candidate is explicitly negated in user_text
            negation_patterns = [
                r'(?:didn\'?t\s+select|did\s+not\s+select|not|don\'?t\s+want|do\s+not\s+want|instead\s+of|rather\s+than|change\s+from|nahi|nahin|na)\s+(?:(?:dr\.?|doctor|ڈاکٹر)\s*)?' + re.escape(cand),
                r'(?:(?:dr\.?|doctor|ڈاکٹر)\s*)?' + re.escape(cand) + r'\s+(?:nahi|nahin|mat|ko\s+nahi|ko\s+nahin)'
            ]
            is_negated = any(re.search(pat, raw_lower) for pat in negation_patterns)
            if is_negated:
                continue

            score = 0.0
            # Exact whole-word or substring match
            if re.search(r'\b' + re.escape(cand) + r'\b', expanded_text):
                score = 1.0
                positive_patterns = [
                    r'(?:select|please\s+select|prefer|choose|with|see|book\s+with|appointment\s+with|dr\.?|doctor|ڈاکٹر)\s+(?:(?:dr\.?|doctor|ڈاکٹر)\s*)?' + re.escape(cand),
                    re.escape(cand) + r'\s+(?:ke\s+sath|ky\s+sath|se\s+milna|chahiye|ke\s+paas|theek|sahi|ok|is\s+ok|سے|کے\s+ساتھ|کی\s+اپائنٹمنٹ|کی\s+اپوائنٹمنٹ)'
                ]
                if any(re.search(pp, raw_lower) for pp in positive_patterns):
                    score = 2.5
            elif len(cand) >= 4 and cand in expanded_text:
                score = 0.95
            else:
                # Token-level fuzzy ratio (e.g. "ahmad" vs "ahmed")
                for tok in tokens:
                    if len(tok) >= 3:
                        r = difflib.SequenceMatcher(None, cand, tok).ratio()
                        if r >= 0.8:
                            score = max(score, r)

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


def _extract_time_str(text: str) -> Optional[str]:
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


def _extract_name(text: str, roster_names: Optional[List[str]] = None) -> Optional[str]:
    """
    Extract person name from customer booking text.
    Supports English ("My name is Ali"), Roman Urdu ("Mera naam Ali hai"), and Urdu script ("میرا نام علی ہے").
    """
    if not text:
        return None

    # Urdu script name matching (e.g. میرا نام علی ہے)
    m_urdu = re.search(r'(?:میرا\s+نام\s+|نام\s+ہے\s+|نام\s*:\s*)([\u0600-\u06FF\w]+)', text)
    if m_urdu:
        raw_urdu = m_urdu.group(1).strip()
        urdu_name_map = {
            "علی": "Ali", "ہارون": "Haroon", "حارون": "Haroon", "محمد": "Muhammad", "طارق": "Tariq",
            "عمر": "Umar", "احمد": "Ahmed", "سارہ": "Sara", "حمزہ": "Hamza",
            "عثمان": "Usman", "حسن": "Hassan", "بلال": "Bilal", "زید": "Zaid",
            "اسگر": "Asghar", "اصغر": "Asghar", "اسد": "Asad", "وقاص": "Waqas"
        }
        return urdu_name_map.get(raw_urdu, raw_urdu)

    # Roman Urdu & English patterns
    name_patterns = [
        (r'(?:mera\s+naam|meray\s+naam|naam\s+hai|naam\s+hy)\s+([a-zA-Z]+)', True),
        (r'(?:my\s+(?:own\s+)?name\s+is\s+|name\s+is\s+|i\'?m\s+|i\s+am\s+|im\s+|this\s+is\s+|name\s*:\s*)([a-zA-Z]+(?:\s+[a-zA-Z]+)*)', True),
        (r'(?:booking|appointment|cleaning|checkup|consultation|service)?\s*for\s+([a-zA-Z]+(?:\s+[a-zA-Z]+)*)', False)
    ]
    for np, is_explicit_self in name_patterns:
        m = re.search(np, text, re.IGNORECASE)
        if m:
            raw = m.group(1)
            name = re.split(r'[,.]|\bphone\b|\bcontact\b|\bat\b|\bon\b|\bdate\b|\bfor\b|\bwith\b|\bi\s+need\b|\bi\s+want\b|\band\b|\bhai\b|\bhein\b|\bhy\b|\bha\b|\bplease\b|\bselect\b|\bprefer\b|\bdr\b|\bdoctor\b', raw, flags=re.IGNORECASE)[0].strip()
            name = re.sub(r'^(?:a\s+|an\s+|the\s+)?(?:cleaning|checkup|consultation|appointment|booking|regular|routine)\s+(?:for\s+)?', '', name, flags=re.IGNORECASE).strip()
            invalid_names = {
                "patient", "a", "the", "an", "cleaning", "checkup", "appointment", "booking", "doctor", "dr",
                "tomorrow", "today", "me", "us", "him", "her", "regular checkup", "regular", "routine", "consultation",
                "teeth", "braces", "extraction", "root canal", "whitening", "dental", "care", "clinic"
            }
            if name and name.lower() not in invalid_names:
                if not is_explicit_self and roster_names:
                    roster_entries = [{"id": i, "name": n} for i, n in enumerate(roster_names)]
                    if _fuzzy_match_roster(name, roster_entries):
                        continue
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
            "confirm", "confirmed", "confirmation", "change", "modify", "reset", "details", "haan", "theek",
            "who", "what", "where", "when", "why", "how", "are", "you", "your", "is", "am", "i", "a", "an", "the",
            "for", "with", "at", "to", "in", "on", "can", "could", "should", "will", "shall", "do", "does", "did",
            "have", "has", "had", "tell", "show", "list", "give", "please", "not", "dont", "good", "morning", "afternoon",
            "kal", "aaj", "parso", "parson", "subah", "shaam", "dopahar", "raat", "baje", "bje", "bjay",
            "kro", "krdo", "kardo", "kar", "kr", "karna", "krna", "btao", "batao", "batayein",
            "check", "up", "kalye", "kelye", "klie", "keliye",
            "pehly", "pehle", "ka", "ki", "ke", "ko", "se", "sy", "hain", "hai", "ha", "hun", "hoon",
            "nhi", "nahi", "karwana", "krwana", "chahiye", "chahta", "chahti"
        ]
        if any(nw in lower_txt.split() or lower_txt == nw for nw in non_name_words):
            return None
        if roster_names:
            roster_entries = [{"id": i, "name": n} for i, n in enumerate(roster_names)]
            if _fuzzy_match_roster(text, roster_entries):
                return None
        return text.strip().title()
    return None



def _has_booking_intent(text: str) -> bool:
    """Check if user text conveys booking/schedule intent, even with common typos or misspellings."""
    if not text:
        return False
    lower = text.lower()
    pattern = r'\b(book|booking|appo?i?n?t?m?e?n?t?s?|sch?e?d?u?l?e?s?|skedule|slots?|time|timing|timings|available|availability|doctor|dentist|teeth|cleaning|checkup|visit|consultants?|physicians?|practitioners?|braces|whitening|extraction|root canal|filling|scaling|aligners?|implants?|toothache|cavity|pain|applied|apply|consultation)\b'
    if re.search(pattern, lower, re.IGNORECASE):
        return True
    typos = ["appoinment", "apointment", "appintment", "schdeule", "scedule", "skedule", "appoint", "sched", "timings", "lagwana", "lagwany", "karwana", "karwani"]
    return any(t in lower for t in typos)


# ─────────────────────────────────────────────────────────────────────────────
# CENTRAL INTENT CLASSIFIER
# Returns one of: "DOCTOR_WEEKLY_SCHEDULE", "DOCTOR_DAY_SCHEDULE",
#                 "CHECK_AVAILABILITY", "BOOK_APPOINTMENT", None
# ─────────────────────────────────────────────────────────────────────────────

# Weekday canonicalisation map (used by classifier too)
_CLASSIFIER_WEEKDAY_MAP = {
    "monday": "Monday", "mon": "Monday", "somwar": "Monday", "peer": "Monday",
    "پیر": "Monday", "سوموار": "Monday",
    "tuesday": "Tuesday", "tue": "Tuesday", "mangal": "Tuesday", "منگل": "Tuesday",
    "wednesday": "Wednesday", "wed": "Wednesday", "budh": "Wednesday", "بدھ": "Wednesday",
    "thursday": "Thursday", "thu": "Thursday", "jumeraat": "Thursday", "jumerat": "Thursday", "جمعرات": "Thursday",
    "friday": "Friday", "fri": "Friday", "jummah": "Friday", "juma": "Friday", "jumma": "Friday", "جمعہ": "Friday",
    "saturday": "Saturday", "sat": "Saturday", "hafta": "Saturday", "ہفتہ": "Saturday",
    "sunday": "Sunday", "sun": "Sunday", "itwar": "Sunday", "اتوار": "Sunday",
}

# Words that SIGNAL a recurring weekly/day schedule query (NOT a specific date)
_SCHEDULE_SIGNAL_WORDS = [
    # English
    "schedule", "weekly schedule", "timetable", "working hours", "working days", "work days",
    "timing", "timings", "hours",
    # Roman Urdu
    "ka schedule", "ki schedule", "ka time", "ka waqt", "ki timing",
    "btao", "batao", "bata do", "bataiye", "batayein", "btadein", "bta",
    "kis din", "kis kis din", "kab aate", "kab hote", "kab baithte",
    "kab available", "kab hoti", "kab hoty", "kab baithti",
    "skedule", "skedyool",
    # Urdu script — including colloquial/informal spellings
    "شیڈول", "سکیجویل", "سکیڈول", "ہفتہ وار", "هفته وار",
    "ٹائمنگ", "اوقات", "کس دن", "کس کس دن", "کا وقت", "کی ٹائمنگ", "کا شیڈول",
    "کب آتے", "کب ہوتے", "کب بیٹھتے",
]

# Words that signal a SPECIFIC DATE availability check (override schedule signals)
_AVAILABILITY_SIGNAL_WORDS = [
    "kal", "tomorrow", "today", "aaj", "parso", "parson",
    "available hain", "available hai", "available ho", "slots", "slot",
    "kal ke slots", "kal available", "ke slots", "ke slot",
    "کل کے سلاٹ", "کل دستیاب",
]

# Words that signal BOOKING intent
_BOOKING_SIGNAL_WORDS = [
    "book", "fix", "reserve", "schedule karo", "schedule kr", "appointment fix",
    "appointment book", "lena hai", "karwana hai", "krwana hai", "lagwana",
    "اپائنٹمنٹ", "بک", "فکس", "ریزرو",
]

# Words that are EXPLICIT CALENDAR DATE hints
_CALENDAR_DATE_WORDS = [
    "kal", "tomorrow", "today", "aaj", "parso", "parson",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "january", "february", "march", "april", "june", "july", "august",
    "september", "october", "november", "december",
]


def _classify_intent(user_text: str, conv_state: Optional[Dict[str, Any]] = None) -> str:
    """
    Classify user message into one of:
      "DOCTOR_WEEKLY_SCHEDULE"  — recurring weekly/weekday schedule from DB
      "DOCTOR_DAY_SCHEDULE"     — specific weekday recurring schedule (e.g. Monday)
      "CHECK_AVAILABILITY"      — date-specific available slots
      "BOOK_APPOINTMENT"        — explicit booking action
      None                      — unknown, continue normal flow

    CRITICAL: This classifier reads ONLY the current message.
    It does NOT use conv_state.requested_date to avoid stale date leakage.
    """
    if not user_text:
        return None

    lower = user_text.lower().strip()
    has_urdu_script = bool(re.search(r'[\u0600-\u06FF]', user_text))

    # 1. Check for explicit date strings (ISO or month-day) → availability
    if re.search(r'\b\d{4}-\d{2}-\d{2}\b', user_text):
        return "CHECK_AVAILABILITY"
    if re.search(r'\b\d{1,2}(st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', lower):
        return "CHECK_AVAILABILITY"
    if re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+\d{1,2}\b', lower):
        return "CHECK_AVAILABILITY"

    # 2. Check for explicit availability signals (these override schedule signals)
    has_availability_signal = any(w in lower for w in _AVAILABILITY_SIGNAL_WORDS) or (
        has_urdu_script and any(w in user_text for w in ["کل", "آج", "دستیاب", "سلاٹ"])
    )

    # 3. Check for schedule signals
    has_time_token = bool(_extract_time_str(user_text))
    has_time_token = bool(_extract_time_str(user_text))
    has_schedule_signal = not has_time_token and (any(w in lower for w in _SCHEDULE_SIGNAL_WORDS) or (
        has_urdu_script and any(w in user_text for w in [
            "شیڈول", "سکیجویل", "سکیڈول", "ہفتہ وار", "ٹائمنگ", "اوقات",
            "کس دن", "کا وقت", "کب آتے", "کب ہوتے"
        ])
    ))

    # 4. Check for booking signals
    has_booking_signal = any(w in lower for w in _BOOKING_SIGNAL_WORDS) or (
        has_urdu_script and any(w in user_text for w in ["اپائنٹمنٹ", "بک", "فکس"])
    )

    # 5. Check for calendar date words (relative)
    has_calendar_date_word = any(re.search(r'\b' + re.escape(w) + r'\b', lower) for w in _CALENDAR_DATE_WORDS)

    # 6. Detect weekday name in current message (canonical) — case-insensitive
    # GUARD: "ہفتہ وار" means "weekly" (NOT Saturday). If present, skip Saturday detection.
    _is_full_weekly_phrase = "ہفتہ وار" in user_text or "hafte war" in lower or "hafta war" in lower or "weekly" in lower
    detected_weekday = None
    for wd_lower, wd_canonical in _CLASSIFIER_WEEKDAY_MAP.items():
        # Skip Saturday signals when the message is a full weekly query
        if wd_canonical == "Saturday" and _is_full_weekly_phrase:
            continue
        pat = (r'\b' + re.escape(wd_lower) + r'\b') if wd_lower.isascii() else (r'(?:^|\s)' + re.escape(wd_lower) + r'(?:$|\s)')
        if re.search(pat, user_text, re.IGNORECASE):
            detected_weekday = wd_canonical
            break

    # ── ROUTING RULES ────────────────────────────────────────────────────────
    # Rule A: Explicit booking with no schedule terms → BOOK
    if has_booking_signal and not has_schedule_signal and not has_availability_signal:
        return "BOOK_APPOINTMENT"

    # Rule B: Availability signal (kal, tomorrow, slots) + possible schedule word
    #         → Calendar-specific CHECK_AVAILABILITY wins
    if has_availability_signal and has_calendar_date_word and not has_booking_signal:
        return "CHECK_AVAILABILITY"
    if has_availability_signal and not has_booking_signal and not has_schedule_signal:
        return "CHECK_AVAILABILITY"

    # Rule E: Schedule signal WITH calendar date word (e.g. "kal ka schedule") → availability
    if has_schedule_signal and has_calendar_date_word:
        return "CHECK_AVAILABILITY"

    # Rule C: Schedule signal, weekday present, NO calendar date word → day schedule
    if has_schedule_signal and detected_weekday and not has_calendar_date_word and not has_booking_signal:
        return "DOCTOR_DAY_SCHEDULE"

    # Rule D: Schedule signal, NO calendar date word, NO booking → weekly schedule
    if has_schedule_signal and not has_calendar_date_word and not has_booking_signal:
        return "DOCTOR_WEEKLY_SCHEDULE"

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

        conv_state = conversation_state or {}
        doc_id = conv_state.get("selected_doctor_id")
        svc_id = conv_state.get("selected_service_id")

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

                # Format friendly date title: "Monday, August 24, 2026"
                date_val = tool_data.get("date", "")
                day_val = tool_data.get("day", "")
                try:
                    dt_obj = datetime.strptime(date_val, "%Y-%m-%d")
                    formatted_date_title = dt_obj.strftime("%A, %B %d, %Y")
                except Exception:
                    formatted_date_title = f"{day_val}, {date_val}"

                results = tool_data.get("results", [])
                
                # If specific doctor was selected, filter display to that doctor
                if doc_id:
                    results = [r for r in results if r.get("doctor_id") == doc_id] or results

                if is_after_query:
                    filtered_lines = []
                    for res in results:
                        d_name = res.get("doctor_name", "Doctor")
                        after_slots = [_fmt_time_ampm(s) for s in res.get("available_slots", []) if s > user_time_token]
                        if after_slots:
                            filtered_lines.append(f"• **{d_name}**: {', '.join(after_slots)}")
                    if filtered_lines:
                        return {
                            "content": f"Yes, the available slots on **{formatted_date_title}** after {_fmt_time_ampm(user_time_token)} are:\n\n" + "\n".join(filtered_lines) + "\n\nPlease let me know which time works best for you!",
                            "tool_calls": []
                        }
                    else:
                        return {
                            "content": f"I checked our schedule for **{formatted_date_title}**, but there are no available slots after {_fmt_time_ampm(user_time_token)}. Would you like to check earlier times or another date?",
                            "tool_calls": []
                        }

                if is_before_query:
                    filtered_lines = []
                    for res in results:
                        d_name = res.get("doctor_name", "Doctor")
                        before_slots = [_fmt_time_ampm(s) for s in res.get("available_slots", []) if s < user_time_token]
                        if before_slots:
                            filtered_lines.append(f"• **{d_name}**: {', '.join(before_slots)}")
                    if filtered_lines:
                        return {
                            "content": f"Yes, the available slots on **{formatted_date_title}** before {_fmt_time_ampm(user_time_token)} are:\n\n" + "\n".join(filtered_lines) + "\n\nPlease let me know which time works best for you!",
                            "tool_calls": []
                        }
                    else:
                        return {
                            "content": f"I checked our schedule for **{formatted_date_title}**, but there are no available slots before {_fmt_time_ampm(user_time_token)}. Would you like to check later times or another date?",
                            "tool_calls": []
                        }

                lines = []
                for res in results:
                    d_name = res.get("doctor_name", "Doctor")
                    slots = res.get("available_slots", [])
                    if not slots:
                        msg = res.get("message") or f"{d_name} is closed / not available on this date."
                        lines.append(f"• **{d_name}**: {msg}")
                        continue

                    morning_slots = [_fmt_time_ampm(s) for s in slots if int(s.split(":")[0]) < 12]
                    afternoon_slots = [_fmt_time_ampm(s) for s in slots if int(s.split(":")[0]) >= 12]

                    groups = []
                    if morning_slots:
                        groups.append(f"  - **Morning:** {', '.join(morning_slots)}")
                    if afternoon_slots:
                        groups.append(f"  - **Afternoon:** {', '.join(afternoon_slots)}")

                    slots_text = "\n".join(groups) if groups else "  - " + ", ".join([_fmt_time_ampm(s) for s in slots])
                    lines.append(f"• **{d_name}**:\n{slots_text}")

                if lines and any(res.get("available_slots") for res in results):
                    return {
                        "content": f"Here are the available appointment slots on **{formatted_date_title}**:\n\n" + "\n\n".join(lines) + "\n\nPlease let me know which time slot works best for you!",
                        "tool_calls": []
                    }
                else:
                    next_d = tool_data.get("next_available_date")
                    next_day = tool_data.get("next_available_day")
                    if next_d:
                        return {
                            "content": f"I checked our schedule for **{formatted_date_title}**, but there are no open slots on that day. The next available opening is on **{next_day}, {next_d}**. Would you like to check slots for that day?",
                            "tool_calls": []
                        }
                    return {
                        "content": f"I checked our schedule for **{formatted_date_title}**, but unfortunately there are no open slots on that day. Would you like to check another date?",
                        "tool_calls": []
                    }

            elif "appointment_id" in tool_data and tool_data.get("success"):
                appt = tool_data.get("appointment", {})
                return {
                    "content": (
                        f"🎉 **Your appointment is confirmed!**\n\n"
                        f"• **Appointment ID:** #{tool_data.get('appointment_id')}\n"
                        f"• **Patient Name:** {appt.get('customer_name')}\n"
                        f"• **Doctor:** {appt.get('doctor_name')}\n"
                        f"• **Service:** {appt.get('service_name')}\n"
                        f"• **Date & Time:** {appt.get('appointment_date')} at {_fmt_time_ampm(appt.get('appointment_time'))}\n"
                        f"• **Clinic Address:** Plot 42-B, Main Boulevard, Gulberg III, Lahore\n\n"
                        f"A reminder has been automatically scheduled for your visit. Please arrive 10 minutes early. Let us know if you need anything else!"
                    ),
                    "tool_calls": []
                }

            elif tool_data.get("status") == "HUMAN":
                return {
                    "content": "I have notified our clinic receptionist team. A human staff member will take over this conversation shortly to assist you. Please hold on.",
                    "tool_calls": []
                }

            elif "doctors" in tool_data:
                doc_items = []
                for d in tool_data.get("doctors", []):
                    wk_days = d.get("working_days", [])
                    wk_str = ", ".join(wk_days) if isinstance(wk_days, list) else str(wk_days)
                    start_str = _fmt_time_ampm(d.get("start_time", "09:00"))
                    end_str = _fmt_time_ampm(d.get("end_time", "17:00"))
                    lunch_str = f" | Lunch: {_fmt_time_ampm(d['break_start_time'])}–{_fmt_time_ampm(d['break_end_time'])}" if (d.get("break_start_time") and d.get("break_end_time")) else ""
                    doc_items.append(f"• **{d['name']}** - {d.get('specialization', 'Dentist')} (Working Days: {wk_str}, Hours: {start_str} – {end_str}{lunch_str})")
                body = "\n\n".join(doc_items)
                if lang == "urdu":
                    return {
                        "content": f"مسکراہٹ کلینک میں ہمارے دستیاب ڈاکٹرز درج ذیل ہیں:\n\n{body}\n\nآپ کس ڈاکٹر سے اپائنٹمنٹ لینا پسند کریں گے؟",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"SmileCare ke practicing dentists yeh hain:\n\n{body}\n\nAap kis doctor ke sath appointment book karna pasand karein ge?",
                        "tool_calls": []
                    }
                return {
                    "content": "Of course. Here are our practicing dentists at SmileCare:\n\n" + body + "\n\nWhich doctor would you prefer?",
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
        doc_id = conv_state.get("selected_doctor_id")
        doc_name = conv_state.get("selected_doctor_name") or ("Dr. Ahmed Khan" if doc_id == 1 else ("Dr. Sara Malik" if doc_id == 2 else None))
        svc_id = conv_state.get("selected_service_id")
        svc_name = conv_state.get("selected_service_name")
        last_offered_slots = conv_state.get("last_offered_slots", {})
        all_offered_slots = conv_state.get("all_offered_slots", [])
        # Token extractions
        time_token = _extract_time_str(user_text)
        phone_match = re.search(r'\b(03\d{2}[- ]?\d{7}|\+92\d{10}|03\d{9})\b', user_text)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', user_text)
        is_question = _is_question_query(user_text)
        lang = detect_language(user_text, messages)

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


        # ── CLASSIFY INTENT FROM CURRENT MESSAGE ONLY (before any state lookup) ──
        _msg_intent = _classify_intent(user_text, conv_state)
        _is_schedule_intent = _msg_intent in ("DOCTOR_WEEKLY_SCHEDULE", "DOCTOR_DAY_SCHEDULE")

        explicit_date_given = False
        parsed_target_date = resolve_date_string(user_text, business_id=conv_state.get("business_id", 1))
        if parsed_target_date:
            target_date_str = parsed_target_date
            explicit_date_given = True
        elif req_date and not _is_schedule_intent:
            # Only use stale date from conv_state when NOT a schedule query.
            # This prevents previous "kal" from bleeding into "Dr Ahmed ka schedule batao".
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
        else:
            # Check if user explicitly requested an unknown/unregistered doctor
            _mentioned_doc_raw = _extract_doctor_mention(user_text)
            if _mentioned_doc_raw:
                doc_display_requested = f"Dr. {_mentioned_doc_raw.title()}" if not bool(re.search(r'[؀-ۿ]', _mentioned_doc_raw)) else f"ڈاکٹر {_mentioned_doc_raw}"
                doc_bullets = []
                for d in doctor_roster:
                    d_spec = d.get("specialization", "General Dentist")
                    doc_bullets.append(f"• **{d.get('name')}** - {d_spec}")
                docs_list_str = "\n".join(doc_bullets) if doc_bullets else "Our practicing dental specialists"

                if lang == "urdu":
                    greeting = f"{effective_name} صاحب، " if effective_name else ""
                    return {
                        "content": (
                            f"معذرت، {greeting}سمائل کیئر ڈینٹل کلینک میں {doc_display_requested} پریکٹس نہیں کرتے۔\n\n"
                            f"ہمارے کلینک میں دستیاب ڈاکٹرز درج ذیل ہیں:\n{docs_list_str}\n\n"
                            f"آپ کس ڈاکٹر سے اپائنٹمنٹ لینا پسند کریں گے؟"
                        ),
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    greeting = f"{effective_name}, " if effective_name else ""
                    return {
                        "content": (
                            f"SmileCare Dental Clinic mein {doc_display_requested} available nahi hain.\n\n"
                            f"Hamare practicing dentists yeh hain:\n{docs_list_str}\n\n"
                            f"{greeting}Aap kis doctor ke sath appointment book karwana chahein ge?"
                        ),
                        "tool_calls": []
                    }
                else:
                    greeting = f", {effective_name}" if effective_name else ""
                    return {
                        "content": (
                            f"We do not have {doc_display_requested} practicing at SmileCare Dental Clinic.\n\n"
                            f"Our available practicing dentists are:\n{docs_list_str}\n\n"
                            f"Which doctor would you prefer for your appointment{greeting}?"
                        ),
                        "tool_calls": []
                    }

        _svc_override = _fuzzy_match_roster(user_text, service_roster)
        if _svc_override:
            svc_id = _svc_override["id"]
            svc_name = _svc_override["name"]

        # --- OUT-OF-SCOPE & SPECIALTY CHECKS (Run FIRST regardless of awaiting_input) ---
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

        if any(w in user_text for w in ["insurance", "prescription", "antibiotic", "diagnose", "severe bleeding"]):
            return {
                "content": "I cannot provide medical advice or verify insurance coverage directly. Let me connect you with our medical staff.",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": f"Out-of-scope / medical query: {user_text}"}}]
            }

        # --- EXPLICIT AWAITING_INPUT RESOLUTION (Runs FIRST before Case A-E & keyword matching) ---
        # Check BOOKED state FIRST
        if workflow_state == "BOOKED":
            if any(w in user_text for w in ["yes", "yeah", "confirm", "sure", "go ahead", "ok", "okay", "haan", "theek", "book it", "please book", "book", "thanks", "thank you", "done", "alright"]):
                if lang == "urdu":
                    return {
                        "content": "🎉 **آپ کی اپائنٹمنٹ پہلے ہی تصدیق شدہ ہے!** ہم سمائل کیئر ڈینٹل کلینک میں آپ کے منتظر ہیں۔ کیا میں آپ کی مزید کوئی مدد کر سکتا ہوں؟",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": "Aap ki appointment already confirmed hai! Hum SmileCare Dental Clinic mein aap ke muntazir hain. Agar koi mazeed sawal ho to zaroor batayein!",
                        "tool_calls": []
                    }
                return {
                    "content": "Your appointment is already confirmed! We look forward to seeing you at SmileCare Dental Clinic. Please let us know if you need anything else.",
                    "tool_calls": []
                }

        # Check Cancellation Request FIRST
        if any(w in user_text for w in ["cancel booking", "cancel appointment", "cancel my appointment", "cancel my booking"]):
            if lang == "urdu":
                return {
                    "content": "آپ کی بکنگ کی درخواست منسوخ کر دی گئی ہے۔ جب بھی آپ دوبارہ اپائنٹمنٹ لینا چاہیں، مجھے ضرور بتائیے گا!",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": "Aap ki booking request cancel kar di gayi hai. Jab bhi aap dobara appointment book karna chahein, batayein!",
                    "tool_calls": []
                }
            return {
                "content": "Your booking request has been cancelled. Please let me know whenever you would like to schedule a new appointment or ask any questions about our services!",
                "tool_calls": []
            }

        # Check Reschedule / Move Request
        if any(w in user_text for w in ["move it to", "move to", "reschedule", "change time to", "change appointment time", "postpone to"]):
            if time_token:
                return {
                    "content": f"I can help reschedule your appointment to {_fmt_time_ampm(time_token)}. Checking open slots for {doc_name or 'our practicing dentist'}...",
                    "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str or req_date or "2026-08-28", "doctor_id": doc_id or 1, "service_id": svc_id or 1}}]
                }
            if lang == "urdu":
                return {
                    "content": "جی بالکل، ہم آپ کی اپائنٹمنٹ تبدیل کر دیتے ہیں۔ آپ کس نئی تاریخ یا وقت کو ترجیح دیں گے؟",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": "Ji bilkul, hum aap ki appointment update kar dete hain. Aap kis new date ya time slot ko prefer karein ge?",
                    "tool_calls": []
                }
            return {
                "content": "Sure, let's update your appointment. What new date or time slot would you prefer?",
                "tool_calls": []
            }

        # Check Change Details Request
        if any(w in user_text for w in ["change my appointment", "change appointment", "change details"]):
            if lang == "urdu":
                return {
                    "content": "جی بالکل، ہم اپائنٹمنٹ کی تفصیلات تبدیل کر لیتے ہیں۔ آپ کون سا ڈاکٹر، تاریخ یا وقت منتخب کرنا چاہیں گے؟",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": "Ji bilkul, hum appointment details update kar dete hain. Aap konsa doctor, date ya time slot choose karna chahein ge?",
                    "tool_calls": []
                }
            return {
                "content": "Sure, let's update your appointment details. Which doctor, date, or time slot would you like to choose instead?",
                "tool_calls": []
            }

        # Check Explicit Doctor Switch / Change Request
        if any(w in user_text for w in ["instead", "switch", "change doctor", "different doctor", "actually i want", "prefer dr"]):
            matched_switch_doc = _fuzzy_match_roster(user_text, doctor_roster)
            if matched_switch_doc:
                doc_id = matched_switch_doc["id"]
                doc_name = matched_switch_doc["name"]
                if target_date_str:
                    return {
                        "content": f"Switched to {doc_name}. Checking available slots on {target_date_str}...",
                        "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": doc_id, "service_id": svc_id or 1}}]
                    }
                if lang == "urdu":
                    return {
                        "content": f"میں نے {doc_name} کا انتخاب کر لیا ہے۔ آپ کس تاریخ کو اپائنٹمنٹ لینا چاہیں گے؟",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Maine {doc_name} select kar liya hai. Aap kis date ko appointment book karwana chahein ge?",
                        "tool_calls": []
                    }
                return {
                    "content": f"{doc_name} selected. Which date would you prefer for your appointment?",
                    "tool_calls": []
                }

        # ── SCHEDULE INTENT HANDLER (uses _msg_intent from classifier) ──────────
        # Detects weekday from current message using the same canonical map
        # GUARD: "ہفتہ وار" means "weekly" (not Saturday) — skip Saturday when full-weekly
        _is_full_weekly_phrase_mock = (
            "ہفتہ وار" in user_text or "hafte war" in user_text or "hafta war" in user_text or "weekly" in user_text
        )
        _sched_weekday = None
        for wd_lower_k, wd_canonical_k in _CLASSIFIER_WEEKDAY_MAP.items():
            if wd_canonical_k == "Saturday" and _is_full_weekly_phrase_mock:
                continue
            pat_k = (r'\b' + re.escape(wd_lower_k) + r'\b') if wd_lower_k.isascii() else (r'(?:^|\s)' + re.escape(wd_lower_k) + r'(?:$|\s)')
            if re.search(pat_k, user_text, re.IGNORECASE):
                _sched_weekday = wd_canonical_k
                break

        if _is_schedule_intent:
            # Resolve doctor: current message override takes priority over conv_state
            target_d_entry = _doc_override or (next((d for d in doctor_roster if d["id"] == doc_id), None) if doc_id else None)
            if not target_d_entry and len(doctor_roster) == 1:
                target_d_entry = doctor_roster[0]

            if target_d_entry:
                t_name = target_d_entry.get("name", "Doctor")
                _target_wd = _sched_weekday if _msg_intent == "DOCTOR_DAY_SCHEDULE" else None
                sched_lines = _format_doctor_schedule_lines(target_d_entry, target_day=_target_wd)
                sched_body = "\n".join(sched_lines)

                if _target_wd:
                    if lang == "urdu":
                        return {"content": f"{t_name} کا {_target_wd} کا شیڈول:\n\n{sched_body}\n\nآپ کس تاریخ یا وقت کے لیے اپائنٹمنٹ بک کروانا چاہیں گے؟", "tool_calls": []}
                    elif lang == "roman_urdu":
                        return {"content": f"{t_name} ka {_target_wd} ka schedule:\n\n{sched_body}\n\nAap kis date ya time ke liye appointment book karwana chahein ge?", "tool_calls": []}
                    return {"content": f"Here is {t_name}'s schedule for {_target_wd}:\n\n{sched_body}\n\nWhich date or time would you like to book your appointment for?", "tool_calls": []}
                else:
                    if lang == "urdu":
                        return {"content": f"{t_name} کا ہفتہ وار شیڈول:\n\n{sched_body}\n\nآپ کس تاریخ کے لیے اپائنٹمنٹ بک کروانا چاہیں گے؟", "tool_calls": []}
                    elif lang == "roman_urdu":
                        return {"content": f"Bilkul! {t_name} ka weekly schedule:\n\n{sched_body}\n\nAap kis date ya din ke liye appointment book karwana chahein ge?", "tool_calls": []}
                    return {"content": f"Here is {t_name}'s weekly schedule:\n\n{sched_body}\n\nWhich date would you like to book your appointment for?", "tool_calls": []}
            else:
                # No specific doctor identified — fetch all doctors
                return {"content": "Let me retrieve our doctors' schedules for you.", "tool_calls": [{"name": "get_doctors", "arguments": {}}]}


        # --- EXPLICIT AWAITING_INPUT RESOLUTION (Runs FIRST before Case A-E & keyword matching) ---
        awaiting_input = conv_state.get("awaiting_input")
        
        # Check if user clearly changed subject or asked a question
        is_topic_change = is_question or any(w in user_text for w in ["address", "location", "timing", "hours", "insurance", "human", "receptionist", "eye", "skin", "cancel"])

        if awaiting_input and not is_topic_change:
            # Check for "I don't know / consultation / toothache" first
            consultation_keywords = [
                "dont know", "don't know", "not sure", "unsure", "tooth hurts", "toothache", "pain",
                "hurting", "problem", "consultation", "checkup", "consult", "check up", "general appointment",
                "dant", "dard", "masla", "pata nahi", "nahi pata", "maloom nahi", "check karwana", "check krwana",
                "چیک اپ", "چیکپ", "مشورہ", "معائنہ", "دانت", "درد", "پروبلم", "مسئلہ", "نہیں پتا", "نہیں معلوم"
            ]
            if any(w in user_text.lower() for w in consultation_keywords):
                consultation_svc = next((s for s in service_roster if "consultation" in s["name"].lower() or "checkup" in s["name"].lower()), service_roster[0] if service_roster else {"id": 1, "name": "Dental Checkup & Consultation", "price": 2000})
                svc_id = consultation_svc["id"]
                svc_name = consultation_svc["name"]
                fee = consultation_svc.get("price", 2000.0)
                if not doc_name:
                    if lang == "urdu":
                        return {
                            "content": f"کوئی مسئلہ نہیں۔ ہم مشورہ بک کر لیتے ہیں (فیس: PKR {fee:,.0f})۔ آپ کس ڈاکٹر کو ترجیح دیں گے؟",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        return {
                            "content": f"Koi masla nahi! Hum consultation book kar lete hain (Fee: PKR {fee:,.0f}). Aap kis doctor ko prefer karein ge?",
                            "tool_calls": []
                        }
                    return {
                        "content": f"No problem. We can book a consultation. The consultation fee is PKR {fee:,.0f}. Which doctor would you prefer?",
                        "tool_calls": []
                    }
                elif not target_date_str:
                    if lang == "urdu":
                        greeting = f"جی {effective_name} صاحب! " if effective_name else "جی بالکل! "
                        return {
                            "content": f"{greeting}ہم {doc_name} کے ساتھ ڈینٹل چیک اپ اور مشورہ (فیس: PKR {fee:,.0f}) طے کر لیتے ہیں۔ آپ کس تاریخ کو تشریف لانا چاہیں گے؟",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        greeting = f"Ji {effective_name}! " if effective_name else "Ji bilkul! "
                        return {
                            "content": f"{greeting}Hum {doc_name} ke sath consultation (Fee: PKR {fee:,.0f}) schedule kar dete hain. Aap kis date ko appointment book karwana chahein ge?",
                            "tool_calls": []
                        }
                    greeting = f"Sure {effective_name}. " if effective_name else "Sure! "
                    return {
                        "content": f"{greeting}We'll arrange a consultation with {doc_name} (Fee: PKR {fee:,.0f}). What date would you prefer for your appointment?",
                        "tool_calls": []
                    }

            if awaiting_input in ["date_choice", "date"]:
                if target_date_str and not is_question:
                    if time_token or req_time:
                        chosen_time = time_token or req_time
                        # Authoritative live availability check
                        from services.booking_service import BookingService
                        avail_check = BookingService.check_availability(
                            business_id=conv_state.get('business_id', 1),
                            doctor_id=doc_id or 1,
                            service_id=svc_id or 1,
                            date_str=target_date_str
                        )
                        avail_slots = avail_check.get("available_slots", []) if avail_check.get("success") else []
                        if chosen_time not in avail_slots:
                            d_display_name = doc_name or "our doctor"
                            fmt_t = _fmt_time_ampm(chosen_time)
                            slot_bullets = "\n".join([f"• {_fmt_time_ampm(s)}" for s in avail_slots[:10]])
                            if lang == "urdu":
                                spoken_d_u = _fmt_spoken_date_urdu(target_date_str)
                                spoken_t_u = _fmt_spoken_time_urdu(chosen_time)
                                return {
                                    "content": f"معذرت، {spoken_d_u} کو {spoken_t_u} کا وقت {d_display_name} کے لیے دستیاب نہیں ہے۔\n\nدستیاب اوقات:\n{slot_bullets}\n\nبراہ کرم دستیاب اوقات میں سے کوئی وقت منتخب کریں۔",
                                    "tool_calls": []
                                }
                            elif lang == "roman_urdu":
                                spoken_d_r = _fmt_spoken_date_roman(target_date_str)
                                return {
                                    "content": f"{fmt_t} {d_display_name} ke liye {spoken_d_r} ko available nahi hai.\n\nAvailable times include:\n{slot_bullets}\n\nBarah-e-karam in mein se koi time slot choose karein.",
                                    "tool_calls": []
                                }
                            else:
                                return {
                                    "content": f"{fmt_t} is not available for {d_display_name} on {target_date_str}.\n\nAvailable times include:\n{slot_bullets}\n\nPlease choose one of the available slots.",
                                    "tool_calls": []
                                }

                        if effective_name and effective_phone:
                            return {
                                "content": f"Booking your appointment with {doc_name or 'our practicing dentist'} for {target_date_str} at {chosen_time}...",
                                "tool_calls": [{
                                    "name": "book_appointment",
                                    "arguments": {
                                        "customer_name": effective_name,
                                        "customer_phone": effective_phone,
                                        "doctor_id": doc_id or 1,
                                        "service_id": svc_id or 1,
                                        "appointment_date": target_date_str,
                                        "appointment_time": chosen_time,
                                        "notes": "Booked via AI Assistant"
                                    }
                                }]
                            }
                        elif effective_name and not effective_phone:
                            spoken_d_u = _fmt_spoken_date_urdu(target_date_str)
                            spoken_t_u = _fmt_spoken_time_urdu(chosen_time)
                            spoken_d_r = _fmt_spoken_date_roman(target_date_str)
                            spoken_t_r = _fmt_spoken_time_roman(chosen_time)
                            if lang == "urdu":
                                return {
                                    "content": f"بہترین، {effective_name} صاحب! میں نے {spoken_d_u} کو {spoken_t_u} کا وقت آپ کے لیے محفوظ کر لیا ہے۔ بکنگ کو فائنل کرنے کے لیے، برائے مہربانی اپنا فون نمبر شیئر کر دیجیے تاکہ ہم آپ کو تصدیقی میسج بھیج سکیں۔",
                                    "tool_calls": []
                                }
                            elif lang == "roman_urdu":
                                return {
                                    "content": f"Behtareen, {effective_name}! Maine {spoken_d_r} ko {spoken_t_r} ka slot aap ke liye mehfooz kar liya hai. Booking ko final karne ke liye apna contact number share kar dijiye taake hum confirmation bhej sakein.",
                                    "tool_calls": []
                                }
                            return {
                                "content": f"Wonderful, {effective_name}! I have reserved the {_fmt_time_ampm(chosen_time)} slot on {target_date_str} for you. To finalize your booking, please provide your contact phone number so we can send your confirmation details.",
                                "tool_calls": []
                            }
                        else:
                            spoken_d_u = _fmt_spoken_date_urdu(target_date_str)
                            spoken_t_u = _fmt_spoken_time_urdu(chosen_time)
                            spoken_d_r = _fmt_spoken_date_roman(target_date_str)
                            spoken_t_r = _fmt_spoken_time_roman(chosen_time)
                            if lang == "urdu":
                                return {
                                    "content": f"بہترین! میں نے {spoken_d_u} کو {spoken_t_u} کا وقت آپ کے لیے محفوظ کر لیا ہے۔ بکنگ کو فائنل کرنے کے لیے، کیا میں آپ کا پورا نام جان سکتا ہوں؟ اور ساتھ ہی اپنا فون نمبر بھی شیئر کر دیجیے تاکہ ہم آپ کو تصدیقی میسج بھیج سکیں۔",
                                    "tool_calls": []
                                }
                            elif lang == "roman_urdu":
                                return {
                                    "content": f"Behtareen! Maine {spoken_d_r} ko {spoken_t_r} ka slot aap ke liye mehfooz kar liya hai. Booking ko final karne ke liye, kya main aap ka poora naam jaan sakta hoon? Aur sath hi apna phone number bhi share kar dijiye taake hum aap ko confirmation message bhej sakein.",
                                    "tool_calls": []
                                }
                            return {
                                "content": f"Perfect! I have reserved the {_fmt_time_ampm(chosen_time)} slot on {target_date_str} for you. To finalize your booking, please provide your full name and contact phone number.",
                                "tool_calls": []
                            }
                    else:
                        return {
                            "content": f"Checking open slots for {doc_name or 'our practicing dentist'} on {target_date_str}...",
                            "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": doc_id or 1, "service_id": svc_id or 1}}]
                        }
                elif not is_question:
                    if doc_name and svc_name:
                        if lang == "urdu":
                            greeting = f"جی {effective_name} صاحب! " if effective_name else "جی بالکل! "
                            return {
                                "content": f"{greeting}ہم {doc_name} کے ساتھ {svc_name} طے کر لیتے ہیں۔ آپ کس تاریخ کو تشریف لانا چاہیں گے؟",
                                "tool_calls": []
                            }
                        elif lang == "roman_urdu":
                            greeting = f"Ji {effective_name}! " if effective_name else "Ji bilkul! "
                            return {
                                "content": f"{greeting}Hum {doc_name} ke sath {svc_name} schedule kar dete hain. Aap kis date ko appointment book karwana chahein ge?",
                                "tool_calls": []
                            }
                        greeting = f"Sure {effective_name}. " if effective_name else "Sure! "
                        return {
                            "content": f"{greeting}We'll arrange a {svc_name} with {doc_name}. What date would you prefer for your appointment?",
                            "tool_calls": []
                        }
                    elif doc_name:
                        if lang == "urdu":
                            greeting = f"بالکل، {effective_name} صاحب! " if effective_name else "بالکل! "
                            return {
                                "content": f"{greeting}میں {doc_name} کے ساتھ آپ کی اپائنٹمنٹ بک کر دیتا ہوں۔ براہِ کرم اپنی پسند کی تاریخ بتائیں...",
                                "tool_calls": []
                            }
                        elif lang == "roman_urdu":
                            greeting = f"Ji {effective_name}! " if effective_name else "Ji bilkul! "
                            return {
                                "content": f"{greeting}Main {doc_name} ke sath aap ki appointment book kar deta hoon. Barah-e-karam apni pasand ki date batayein...",
                                "tool_calls": []
                            }
                        greeting = f"Certainly, {effective_name}! " if effective_name else ""
                        return {
                            "content": f"{greeting}{doc_name} selected. Which date would you prefer for your appointment?",
                            "tool_calls": []
                        }
                    else:
                        if lang == "urdu":
                            return {
                                "content": "آپ کس تاریخ کو اپائنٹمنٹ لینا پسند کریں گے؟",
                                "tool_calls": []
                            }
                        elif lang == "roman_urdu":
                            return {
                                "content": "Aap kis date ko appointment prefer karein ge?",
                                "tool_calls": []
                            }
                        return {
                            "content": "Which date would you prefer for your appointment?",
                            "tool_calls": []
                        }

            elif awaiting_input == "doctor_choice":
                matched_doc = _fuzzy_match_roster(user_text, doctor_roster) or ({"id": doc_id, "name": doc_name} if doc_id else None)
                if matched_doc:
                    doc_id = matched_doc["id"]
                    doc_name = matched_doc["name"]
                    cand_name = None
                    effective_name = pending_name
                    if target_date_str:
                        return {
                            "content": f"Checking open slots for {doc_name} on {target_date_str}...",
                            "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": doc_id, "service_id": svc_id or 1}}]
                        }
                    if lang == "urdu":
                        greeting = f"بالکل، {effective_name} صاحب! " if effective_name else "بالکل! "
                        return {
                            "content": f"{greeting}میں {doc_name} کے ساتھ آپ کی اپائنٹمنٹ بک کر دیتا ہوں۔ براہِ کرم اپنی پسند کی تاریخ بتائیں...",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        greeting = f"Ji {effective_name}! " if effective_name else "Ji bilkul! "
                        return {
                            "content": f"{greeting}Main {doc_name} ke sath aap ki appointment book kar deta hoon. Barah-e-karam apni pasand ki date batayein...",
                            "tool_calls": []
                        }
                    greeting = f"Certainly, {effective_name}! " if effective_name else ""
                    return {
                        "content": f"{greeting}{doc_name} selected. Which date would you prefer for your appointment?",
                        "tool_calls": []
                    }
                matched_svc_here = _fuzzy_match_roster(user_text, service_roster)
                if matched_svc_here:
                    svc_id = matched_svc_here["id"]
                    svc_name = matched_svc_here["name"]
                    if target_date_str:
                        effective_doc_id = doc_id or (doctor_roster[0]["id"] if doctor_roster else 1)
                        return {
                            "content": f"Checking open slots for {svc_name} on {target_date_str}...",
                            "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": effective_doc_id, "service_id": svc_id}}]
                        }
                    if doc_id:
                        if lang == "urdu":
                            return {
                                "content": f"بہت خوب! میں نے {doc_name} کے ساتھ {svc_name} منتخب کر لی ہے۔ آپ کس تاریخ کو تشریف لانا چاہیں گے؟",
                                "tool_calls": []
                            }
                        elif lang == "roman_urdu":
                            return {
                                "content": f"Ji bilkul! Maine {doc_name} ke sath {svc_name} select kar li hai. Aap kis date ko appointment prefer karein ge?",
                                "tool_calls": []
                            }
                        return {
                            "content": f"You selected {svc_name} with {doc_name}. Which date would you prefer?",
                            "tool_calls": []
                        }
                    if lang == "urdu":
                        return {
                            "content": f"آپ نے {svc_name} کا انتخاب کیا ہے۔ آپ کس ڈاکٹر کو ترجیح دیں گے؟",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        return {
                            "content": f"Aap ne {svc_name} select ki hai. Aap kis doctor ko prefer karein ge?",
                            "tool_calls": []
                        }
                    return {
                        "content": f"You selected {svc_name}. Which doctor would you prefer?",
                        "tool_calls": []
                    }
                elif not is_question and not target_date_str:
                    roster_names = ", ".join(d["name"] for d in doctor_roster) or "Dr. Ahmed Khan or Dr. Sara Malik"
                    if lang == "urdu":
                        return {
                            "content": f"براہ کرم ہمارے کلینک کے ڈاکٹرز میں سے کسی ایک کا انتخاب کریں: {roster_names}۔",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        return {
                            "content": f"Barah-e-karam hamare practicing doctors mein se select karein: {roster_names}.",
                            "tool_calls": []
                        }
                    return {
                        "content": f"Please select a doctor from our roster: {roster_names}.",
                        "tool_calls": []
                    }

            elif awaiting_input == "service_choice":
                matched_svc = _fuzzy_match_roster(user_text, service_roster) or ({"id": svc_id, "name": svc_name} if svc_id else None)
                if matched_svc:
                    svc_id = matched_svc["id"]
                    svc_name = matched_svc["name"]
                    if effective_phone and effective_name and (req_time or time_token) and target_date_str:
                        effective_doc_id = doc_id or (doctor_roster[0]["id"] if doctor_roster else 1)
                        effective_doc_name = doc_name or (doctor_roster[0]["name"] if doctor_roster else "our practicing dentist")
                        chosen_time = time_token or req_time or "10:00"
                        return {
                            "content": f"Booking your appointment with {effective_doc_name} for {target_date_str} at {chosen_time}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": effective_doc_id,
                                    "service_id": svc_id,
                                    "appointment_date": target_date_str,
                                    "appointment_time": chosen_time,
                                    "notes": "Booked via AI Assistant"
                                }
                            }]
                        }
                    elif target_date_str and (req_time or time_token):
                        chosen_time = time_token or req_time
                        if effective_name and not effective_phone:
                            return {
                                "content": f"Thanks, {effective_name}. Please provide your contact phone number to complete and confirm your booking.",
                                "tool_calls": []
                            }
                        return {
                            "content": f"I have selected the {_fmt_time_ampm(chosen_time)} slot on {target_date_str} with {doc_name or 'Dr. Sara Malik'} for {svc_name}. To complete and confirm your booking, please provide your full name and contact phone number.",
                            "tool_calls": []
                        }
                    elif target_date_str:
                        effective_doc_id = doc_id or (doctor_roster[0]["id"] if doctor_roster else 1)
                        return {
                            "content": f"Checking open slots for {svc_name} on {target_date_str}...",
                            "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": effective_doc_id, "service_id": svc_id}}]
                        }
                    elif doc_id:
                        return {
                            "content": f"You selected {svc_name} with {doc_name}. Which date would you prefer?",
                            "tool_calls": []
                        }
                    return {
                        "content": f"You selected {svc_name}. Which doctor would you prefer?",
                        "tool_calls": []
                    }
                elif not is_question and not target_date_str:
                    roster_names = ", ".join(s["name"] for s in service_roster) or "Dental Checkup, Dental Cleaning, Teeth Whitening, Tooth Extraction, Root Canal, or Braces"
                    return {
                        "content": f"Which of our available dental services would you like to select? {roster_names}. (If you are not sure what you need, we can book a general consultation.)",
                        "tool_calls": []
                    }

            elif awaiting_input == "confirmation":
                if any(w in user_text for w in ["yes", "yeah", "confirm", "sure", "go ahead", "ok", "okay", "haan", "theek", "book it", "please book", "book"]):
                    if effective_name and effective_phone and target_date_str:
                        chosen_time = req_time or "10:00"
                        return {
                            "content": f"Booking your appointment with {doc_name or 'our practicing dentist'} for {target_date_str} at {chosen_time}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": doc_id or 1,
                                    "service_id": svc_id or 1,
                                    "appointment_date": target_date_str,
                                    "appointment_time": chosen_time,
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
                    if effective_phone and target_date_str and req_time:
                        return {
                            "content": f"Booking your appointment with {doc_name or 'our practicing dentist'} for {target_date_str} at {req_time}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": doc_id or 1,
                                    "service_id": svc_id or 1,
                                    "appointment_date": target_date_str,
                                    "appointment_time": req_time,
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
                    if effective_name and target_date_str and req_time:
                        return {
                            "content": f"Booking your appointment with {doc_name or 'our practicing dentist'} for {target_date_str} at {req_time}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": doc_id or 1,
                                    "service_id": svc_id or 1,
                                    "appointment_date": target_date_str,
                                    "appointment_time": req_time,
                                    "notes": "Booked via AI Assistant"
                                }
                            }]
                        }
                    return {
                        "content": "Thank you. Please provide your full name to complete and confirm your booking.",
                        "tool_calls": []
                    }

            elif awaiting_input == "time_choice":
                if time_token:
                    from services.booking_service import BookingService
                    avail_check = BookingService.check_availability(
                        business_id=conv_state.get('business_id', 1),
                        doctor_id=doc_id or 1,
                        service_id=svc_id or 1,
                        date_str=target_date_str
                    ) if target_date_str else {"success": False, "available_slots": []}
                    avail_slots = avail_check.get("available_slots", []) if avail_check.get("success") else (conv_state.get("all_offered_slots") or [])
                    if time_token not in avail_slots:
                        d_display_name = doc_name or "our doctor"
                        fmt_t = _fmt_time_ampm(time_token)
                        slot_bullets = "\n".join([f"• {_fmt_time_ampm(s)}" for s in avail_slots[:10]])
                        if lang == "urdu":
                            spoken_d_u = _fmt_spoken_date_urdu(target_date_str) if target_date_str else ""
                            spoken_t_u = _fmt_spoken_time_urdu(time_token)
                            return {
                                "content": f"معذرت، {spoken_d_u} کو {spoken_t_u} کا وقت {d_display_name} کے لیے دستیاب نہیں ہے۔\n\nدستیاب اوقات:\n{slot_bullets}\n\nبراہ کرم دستیاب اوقات میں سے کوئی وقت منتخب کریں۔",
                                "tool_calls": []
                            }
                        elif lang == "roman_urdu":
                            spoken_d_r = _fmt_spoken_date_roman(target_date_str) if target_date_str else ""
                            return {
                                "content": f"{fmt_t} {d_display_name} ke liye {spoken_d_r} ko available nahi hai.\n\nAvailable times include:\n{slot_bullets}\n\nBarah-e-karam in mein se koi time slot choose karein.",
                                "tool_calls": []
                            }
                        else:
                            return {
                                "content": f"{fmt_t} is not available for {d_display_name} on {target_date_str}.\n\nAvailable times include:\n{slot_bullets}\n\nPlease choose one of the available slots.",
                                "tool_calls": []
                            }
                    req_time = time_token
                    if effective_name and effective_phone and target_date_str:
                        return {
                            "content": f"Booking your appointment with {doc_name or 'our practicing dentist'} for {target_date_str} at {time_token}...",
                            "tool_calls": [{
                                "name": "book_appointment",
                                "arguments": {
                                    "customer_name": effective_name,
                                    "customer_phone": effective_phone,
                                    "doctor_id": doc_id or 1,
                                    "service_id": svc_id or 1,
                                    "appointment_date": target_date_str,
                                    "appointment_time": time_token,
                                    "notes": "Booked via AI Assistant"
                                }
                            }]
                        }
                    elif effective_name and not effective_phone:
                        return {
                            "content": f"Thanks, {effective_name}. Please provide your contact phone number to complete and confirm your booking.",
                            "tool_calls": []
                        }
                    else:
                        svc_str = f" for {svc_name}" if svc_name else ""
                        return {
                            "content": f"I have selected the {_fmt_time_ampm(time_token)} slot on {target_date_str or 'the requested date'} with {doc_name or 'Dr. Ahmed Khan'}{svc_str}. To complete and confirm your booking, please provide your full name and contact phone number.",
                            "tool_calls": []
                        }

        # 3. Explicit Human Handoff Request
        if any(w in user_text for w in ["human", "receptionist", "speak to someone", "representative", "real person", "manager", "staff"]):
            return {
                "content": "Connecting you with our reception team...",
                "tool_calls": [{"name": "human_handoff", "arguments": {"reason": "Customer requested human representative"}}]
            }

        # 4. Informational Request Priority (Doctor Weekly Schedule, Day Schedule, Doctor Roster Inquiry, Services Inquiry, Clinic Info Inquiry)
        WEEKDAYS_LIST = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        target_weekday = None
        for wd in WEEKDAYS_LIST:
            if re.search(r'\b' + wd.lower() + r'\b', user_text):
                target_weekday = wd
                break
        if not target_weekday:
            URDU_WEEKDAYS = {
                "پیر": "Monday", "سوموار": "Monday", "منگل": "Tuesday", "بدھ": "Wednesday",
                "جمعرات": "Thursday", "جمعہ": "Friday", "ہفتہ": "Saturday", "اتوار": "Sunday",
                "jummah": "Friday", "itwar": "Sunday", "peer": "Monday", "mangal": "Tuesday",
                "budh": "Wednesday", "jumeraat": "Thursday", "juma": "Friday", "hafta": "Saturday"
            }
            for u_wd, e_wd in URDU_WEEKDAYS.items():
                if re.search(r'\b' + re.escape(u_wd) + r'\b' if u_wd.isascii() else r'(?:^|\s)' + re.escape(u_wd) + r'(?:$|\s)', user_text):
                    target_weekday = e_wd
                    break

        has_schedule_term = any(w in user_text for w in [
            "weekly schedule", "weekly", "schedule", "timing", "timings", "hours", "working days",
            "ka time", "ki timing", "ka schedule", "kis din", "kis kis din", "kab available",
            "kab hoti", "kab hoty", "kab baithti", "kab aati", "kab aate", "pure hafte", "pure haftey",
            "شیڈول", "ہفتہ وار", "ٹائمنگ", "اوقات", "کس دن", "کس کس دن", "کب", "کا وقت", "کی ٹائمنگ", "کا شیڈول"
        ]) or (target_weekday is not None and any(w in user_text for w in ["time", "timing", "schedule", "hours", "waqt", "کب", "وقت", "ٹائم", "شیڈول", "kya hai", "btao", "batao", "batayein"]))

        if has_schedule_term and not any(w in user_text for w in ["kal", "tomorrow", "today", "aaj"]):
            target_d_entry = _doc_override or (next((d for d in doctor_roster if d["id"] == doc_id), None) if doc_id else None)
            if not target_d_entry and len(doctor_roster) == 1:
                target_d_entry = doctor_roster[0]

            if target_d_entry:
                t_name = target_d_entry.get("name", "Doctor")
                sched_lines = _format_doctor_schedule_lines(target_d_entry, target_day=target_weekday)
                sched_body = "\n".join(sched_lines)

                if target_weekday:
                    if lang == "urdu":
                        return {
                            "content": f"{t_name} کا {target_weekday} کا شیڈول درج ذیل ہے:\n\n{sched_body}\n\nآپ کس تاریخ یا وقت کے لیے اپائنٹمنٹ بک کروانا چاہیں گے؟",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        return {
                            "content": f"{t_name} ka {target_weekday} ka schedule:\n\n{sched_body}\n\nAap kis date ya time ke liye appointment book karwana chahein ge?",
                            "tool_calls": []
                        }
                    return {
                        "content": f"Here is {t_name}'s schedule for {target_weekday}:\n\n{sched_body}\n\nWhich date or time would you like to book your appointment for?",
                        "tool_calls": []
                    }
                else:
                    if lang == "urdu":
                        return {
                            "content": f"{t_name} کا ہفتہ وار شیڈول درج ذیل ہے:\n\n{sched_body}\n\nآپ کس تاریخ کے لیے اپائنٹمنٹ بک کروانا چاہیں گے؟",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        return {
                            "content": f"Bilkul! {t_name} ka weekly schedule:\n\n{sched_body}\n\nAap kis date ya din ke liye appointment book karwana chahein ge?",
                            "tool_calls": []
                        }
                    return {
                        "content": f"Here is {t_name}'s weekly schedule:\n\n{sched_body}\n\nWhich date would you like to book your appointment for?",
                        "tool_calls": []
                    }
            elif any(w in user_text for w in ["in ka", "unka", "inka", "un ka", "doctor", "doctors", "dr"]):
                return {
                    "content": "Let me retrieve our doctor schedules for you.",
                    "tool_calls": [{"name": "get_doctors", "arguments": {}}]
                }

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

        is_dont_know_treatment = any(phrase in user_text for phrase in ["dont know", "don't know", "not sure", "unsure", "pata nahi", "nahi pata", "maloom nahi"])
        is_service_inquiry = not is_dont_know_treatment and any(w in user_text for w in ["which service", "what service", "what services", "list service", "list services", "treatment", "treatments", "price", "prices", "cost", "costs", "charge", "charges", "what do you offer", "how much"])
        if is_service_inquiry:
            return {
                "content": "Let me fetch our dental services and pricing for you.",
                "tool_calls": [{"name": "get_services", "arguments": {}}]
            }

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
                if lang == "urdu":
                    return {
                        "content": f"بہت اچھا ({_fmt_time_ampm(time_token)})، لیکن براہ کرم پہلے اپنی پسند کی تاریخ بتائیں تاکہ ہم اس دن کے دستیاب اوقات چیک کر سکیں۔",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Theek hai ({_fmt_time_ampm(time_token)}), lekin barah-e-karam pehle apni pasand ki date batayein taake hum us din ke available slots check kar sakein.",
                        "tool_calls": []
                    }
                return {
                    "content": f"Got it ({_fmt_time_ampm(time_token)}), but please let us know which date you would like to visit so we can check available slots for you.",
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
            if doc_slots and time_token not in doc_slots:
                slots_preview = ", ".join(doc_slots[:4]) if doc_slots else "no open slots"
                if lang == "urdu":
                    return {
                        "content": f"معذرت، {effective_date} کو {_fmt_time_ampm(time_token)} کا وقت دستیاب نہیں ہے۔ اس دن دستیاب اوقات: {slots_preview} ہیں۔ براہ کرم ان میں سے انتخاب کریں۔",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Maazrat, {effective_date} ko {_fmt_time_ampm(time_token)} slot available nahi hai. Available slots: {slots_preview} hain. Please in mein se choose karein.",
                        "tool_calls": []
                    }
                return {
                    "content": f"The {time_token} slot is not available for {doc_name} on {effective_date}. The available slots on that day are: {slots_preview}. Please choose one of the available times or let me know if you would like to check another date.",
                    "tool_calls": []
                }

            effective_time = time_token
            svc_str = f" for {svc_name}" if svc_name else ""
            spoken_d_u = _fmt_spoken_date_urdu(effective_date)
            spoken_t_u = _fmt_spoken_time_urdu(effective_time)
            spoken_d_r = _fmt_spoken_date_roman(effective_date)
            spoken_t_r = _fmt_spoken_time_roman(effective_time)

            if effective_name and not effective_phone:
                if lang == "urdu":
                    return {
                        "content": f"بہترین، {effective_name} صاحب! میں نے {spoken_d_u} کو {spoken_t_u} کا وقت آپ کے لیے محفوظ کر لیا ہے۔ بکنگ کو فائنل کرنے کے لیے، برائے مہربانی اپنا فون نمبر شیئر کر دیجیے تاکہ ہم آپ کو تصدیقی میسج بھیج سکیں۔",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Behtareen, {effective_name}! Maine {spoken_d_r} ko {spoken_t_r} ka slot aap ke liye mehfooz kar liya hai. Booking ko final karne ke liye apna contact number share kar dijiye taake hum confirmation bhej sakein.",
                        "tool_calls": []
                    }
                return {
                    "content": f"Wonderful, {effective_name}! I have reserved the {_fmt_time_ampm(effective_time)} slot on {effective_date} for you. To finalize your booking, could you please share your contact phone number so we can send your confirmation details?",
                    "tool_calls": []
                }
            if lang == "urdu":
                return {
                    "content": f"بہترین! میں نے {spoken_d_u} کو {spoken_t_u} کا وقت آپ کے لیے محفوظ کر لیا ہے۔ بکنگ کو فائنل کرنے کے لیے، کیا میں آپ کا پورا نام جان سکتا ہوں؟ اور ساتھ ہی اپنا فون نمبر بھی شیئر کر دیجیے تاکہ ہم آپ کو تصدیقی میسج بھیج سکیں۔",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": f"Behtareen! Maine {spoken_d_r} ko {spoken_t_r} ka slot aap ke liye mehfooz kar liya hai. Booking ko final karne ke liye, kya main aap ka poora naam jaan sakta hoon? Aur sath hi apna phone number bhi share kar dijiye taake hum aap ko confirmation message bhej sakein.",
                    "tool_calls": []
                }
            return {
                "content": f"Perfect! I have reserved the {_fmt_time_ampm(effective_time)} slot on {effective_date} for you. To finalize your booking, may I please have your full name and contact phone number so we can send your confirmation message?",
                "tool_calls": []
            }

        # Already booked confirmation message
        if workflow_state == "BOOKED":
            effective_doc_name = doc_name or (doctor_roster[0]["name"] if doctor_roster else "our practicing dentist")
            chosen_time = time_token or req_time or "09:00"
            if lang == "urdu":
                return {
                    "content": f"🎉 **آپ کی اپائنٹمنٹ تصدیق شدہ ہے!**\n\n• ڈاکٹر: {effective_doc_name}\n• تاریخ اور وقت: {effective_date} بوقت {_fmt_time_ampm(chosen_time)}\n• مریض کا نام: {effective_name or 'Ahmed'}\n• فون نمبر: {effective_phone or '03187538771'}",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": f"🎉 **Aap ki appointment already confirmed hai!**\n\n• Doctor: {effective_doc_name}\n• Date & Time: {effective_date} ko {_fmt_time_ampm(chosen_time)}\n• Patient: {effective_name or 'Ahmed'}\n• Phone: {effective_phone or '03187538771'}",
                    "tool_calls": []
                }
            return {
                "content": f"🎉 **Your appointment is confirmed!**\n\n• Doctor: {effective_doc_name}\n• Date & Time: {effective_date} at {_fmt_time_ampm(chosen_time)}\n• Patient: {effective_name}\n• Phone: {effective_phone}",
                "tool_calls": []
            }

        # Case C: Both name and phone are available -> proceed to book
        if effective_phone and effective_name and (req_time or time_token or target_date_str) and effective_date:
            effective_doc_id = doc_id or (doctor_roster[0]["id"] if doctor_roster else 1)
            effective_doc_name = doc_name or (doctor_roster[0]["name"] if doctor_roster else "our practicing dentist")
            effective_svc_id = svc_id or (service_roster[0]["id"] if service_roster else 1)
            chosen_time = time_token or req_time or (doc_slots[0] if doc_slots else "09:00")

            return {
                "content": f"Booking your appointment with {effective_doc_name} for {effective_date} at {chosen_time}...",
                "tool_calls": [{
                    "name": "book_appointment",
                    "arguments": {
                        "customer_name": effective_name,
                        "customer_phone": effective_phone,
                        "doctor_id": effective_doc_id,
                        "service_id": effective_svc_id,
                        "appointment_date": effective_date,
                        "appointment_time": chosen_time,
                        "notes": "Booked via AI Assistant"
                    }
                }]
            }

        # Direct name stated by user in booking context (e.g. "Name is Haroon", "My name is Ali", "Mera naam Ahmed hai")
        if cand_name and not phone_match and not is_question:
            if not req_time and not time_token:
                if lang == "urdu":
                    return {
                        "content": f"شکریہ {cand_name} صاحب! براہ کرم اپنی پسند کا وقت منتخب کریں اور بکنگ مکمل کرنے کے لیے فون نمبر بتائیں۔",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Shukriya {cand_name}! Barah-e-karam apna preferred time slot select karein aur booking confirm karne ke liye phone number provide karein.",
                        "tool_calls": []
                    }
                return {
                    "content": f"Thank you, {cand_name}! Please select your preferred time slot and provide your contact phone number to complete and confirm your booking.",
                    "tool_calls": []
                }
            if not effective_phone:
                if lang == "urdu":
                    return {
                        "content": f"شکریہ {cand_name} صاحب! بکنگ مکمل کرنے کے لیے براہ کرم اپنا رابطہ فون نمبر فراہم کریں۔",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Shukriya {cand_name}! Booking complete karne ke liye barah-e-karam apna contact phone number provide karein.",
                        "tool_calls": []
                    }
                return {
                    "content": f"Thank you, {cand_name}. Please provide your contact phone number to complete and confirm your booking.",
                    "tool_calls": []
                }

        # Case D: Name provided but phone still missing in booking context -> ask specifically for phone ONLY when slot & date are selected
        if effective_name and not effective_phone and (req_time or time_token) and effective_date:
            if lang == "urdu":
                return {
                    "content": f"شکریہ {effective_name} صاحب! بکنگ مکمل کرنے کے لیے براہ کرم اپنا رابطہ فون نمبر فراہم کریں۔",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": f"Shukriya {effective_name}! Booking complete karne ke liye barah-e-karam apna contact phone number provide karein.",
                    "tool_calls": []
                }
            return {
                "content": f"Thank you, {effective_name}. Please provide your contact phone number to complete and confirm your booking.",
                "tool_calls": []
            }

        # Case E: Phone provided but name still missing in booking context -> ask specifically for name ONLY when slot & date are selected
        if effective_phone and not effective_name and (req_time or time_token) and effective_date:
            if lang == "urdu":
                return {
                    "content": "شکریہ! بکنگ مکمل کرنے کے لیے براہ کرم اپنا پورا نام بتائیں۔",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": "Shukriya! Booking complete karne ke liye barah-e-karam apna full name provide karein.",
                    "tool_calls": []
                }
            return {
                "content": "Thank you. Please provide your full name to complete and confirm your booking.",
                "tool_calls": []
            }

        # 5. Chit-Chat, Gratitude & Off-Topic Queries (e.g. weather, sports, jokes, news)
        if any(w in user_text for w in ["how are you", "who are you", "what is your name", "what can you do", "good morning", "good afternoon", "good evening", "hi there", "hello there", "kaise ho", "kese ho", "kia haal", "kya haal"]):
            if lang == "urdu":
                return {
                    "content": "ہیلو! میں سمائل کیئر ڈینٹل کلینک کا AI اسسٹنٹ ہوں۔ میں بالکل ٹھیک ہوں اور آپ کی مدد کے لیے حاضر ہوں۔ آج میں آپ کے دانتوں کی کیا خدمت کر سکتا ہوں؟",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": "Hello! Main SmileCare Dental Clinic ka AI receptionist hoon. Main bilkul theek hoon! Main aap ke daanton ke ilaj aur appointment ke liye hazir hoon. Aaj main aap ki kya madad kar sakta hoon?",
                    "tool_calls": []
                }
            return {
                "content": "Hello! I am your AI receptionist at SmileCare Dental Clinic. I'm doing great and ready to assist you! I can help you check doctor schedules, explore our dental services, or book an appointment. How can I help with your teeth today?",
                "tool_calls": []
            }

        if any(w in user_text for w in ["thank you", "thanks", "thx", "appreciation", "great", "awesome", "perfect", "shukriya", "meharbani"]):
            if lang == "urdu":
                return {
                    "content": "آپ کا بہت شکریہ! اگر آپ کو دانتوں کی دیکھ بھال کے حوالے سے کچھ اور پوچھنا ہو تو ضرور بتائیے گا۔",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": "Bohat shukriya! Agar aap ko dental care ya appointment ke hawalay se mazeed kuch poochna ho to zaroor batayein.",
                    "tool_calls": []
                }
            return {
                "content": "You're very welcome! Is there anything else I can assist you with regarding your dental care or appointments today?",
                "tool_calls": []
            }

        # Doctor Selection
        matched_doc_any = _fuzzy_match_roster(user_text, doctor_roster)
        if matched_doc_any and not is_question:
            doc_id = matched_doc_any["id"]
            doc_name = matched_doc_any["name"]
            if target_date_str:
                return {
                    "content": f"Checking open slots for {doc_name} on {target_date_str}...",
                    "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": doc_id, "service_id": svc_id}}]
                }

            # Language mirroring
            if lang == "urdu":
                greeting = f"بالکل، {cand_name} صاحب! " if cand_name else "بالکل! "
                return {
                    "content": f"{greeting}میں {doc_name} کے ساتھ آپ کی اپائنٹمنٹ بک کر دیتا ہوں۔ براہِ کرم اپنی پسند کی تاریخ بتائیں...",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                greeting = f"Ji {cand_name}! " if cand_name else "Ji bilkul! "
                return {
                    "content": f"{greeting}Main {doc_name} ke sath aap ki appointment book kar deta hoon. Barah-e-karam apni pasand ki date batayein...",
                    "tool_calls": []
                }

            greeting = f"Certainly, {cand_name}! " if cand_name else ""
            return {
                "content": f"{greeting}{doc_name} selected. Which date would you prefer for your appointment?",
                "tool_calls": []
            }

        # Service Selection (e.g. "For Braces i want to applied", "teeth whitening", "i need braces", "root canal", "consultation", "tooth hurts")
        matched_svc_any = _fuzzy_match_roster(user_text, service_roster)
        if matched_svc_any and not is_question:
            svc_id = matched_svc_any["id"]
            svc_name = matched_svc_any["name"]
            if target_date_str:
                effective_doc_id = doc_id or (doctor_roster[0]["id"] if doctor_roster else 1)
                return {
                    "content": f"Checking open slots for {svc_name} on {target_date_str}...",
                    "tool_calls": [{"name": "check_availability", "arguments": {"date": target_date_str, "doctor_id": effective_doc_id, "service_id": svc_id}}]
                }
            if doc_id:
                if lang == "urdu":
                    return {
                        "content": f"بہت خوب! میں نے {doc_name} کے ساتھ {svc_name} منتخب کر لی ہے۔ آپ کس تاریخ کو تشریف لانا چاہیں گے؟",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Zabardast! Maine {doc_name} ke sath {svc_name} select kar li hai. Aap kis date ko appointment book karwana chahein ge?",
                        "tool_calls": []
                    }
                return {
                    "content": f"Great! I have selected {svc_name} with {doc_name}. Which date would you like to book your appointment for?",
                    "tool_calls": []
                }
            else:
                if lang == "urdu":
                    return {
                        "content": f"آپ نے {svc_name} کا انتخاب کیا ہے۔ آپ کس ڈاکٹر کو ترجیح دیں گے؟",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    return {
                        "content": f"Aap ne {svc_name} select ki hai. Aap kis doctor ko prefer karein ge?",
                        "tool_calls": []
                    }
                return {
                    "content": f"You selected {svc_name}. Which doctor would you prefer?",
                    "tool_calls": []
                }
        elif any(w in user_text for w in ["dont know", "don't know", "not sure", "unsure", "tooth hurts", "toothache", "pain", "hurting", "problem", "consultation", "checkup"]):
            consultation_svc = next((s for s in service_roster if "consultation" in s["name"].lower() or "checkup" in s["name"].lower()), service_roster[0] if service_roster else {"id": 1, "name": "Dental Checkup & Consultation", "price": 2000})
            fee = consultation_svc.get("price", 2000.0)
            if doc_name:
                if lang == "urdu":
                    greeting = f"جی {effective_name} صاحب! " if effective_name else "جی بالکل! "
                    return {
                        "content": f"{greeting}ہم {doc_name} کے ساتھ ڈینٹل چیک اپ اور مشورہ (فیس: PKR {fee:,.0f}) طے کر لیتے ہیں۔ آپ کس تاریخ کو تشریف لانا چاہیں گے؟",
                        "tool_calls": []
                    }
                elif lang == "roman_urdu":
                    greeting = f"Ji {effective_name}! " if effective_name else "Ji bilkul! "
                    return {
                        "content": f"{greeting}Hum {doc_name} ke sath consultation (Fee: PKR {fee:,.0f}) schedule kar dete hain. Aap kis date ko prefer karein ge?",
                        "tool_calls": []
                    }
                greeting = f"Sure {effective_name}. " if effective_name else "Sure! "
                return {
                    "content": f"{greeting}We'll arrange a Dental Checkup & Consultation with {doc_name} (Fee: PKR {fee:,.0f}). What date would you prefer for your appointment?",
                    "tool_calls": []
                }
            if lang == "urdu":
                return {
                    "content": f"کوئی مسئلہ نہیں۔ ہم مشورہ بک کر لیتے ہیں (فیس: PKR {fee:,.0f})۔ آپ کس ڈاکٹر کو ترجیح دیں گے؟",
                    "tool_calls": []
                }
            elif lang == "roman_urdu":
                return {
                    "content": f"Koi masla nahi! Hum consultation book kar lete hain (Fee: PKR {fee:,.0f}). Aap kis doctor ko prefer karein ge?",
                    "tool_calls": []
                }
            return {
                "content": f"No problem. We can book a consultation. The consultation fee is PKR {fee:,.0f}. Which doctor would you prefer?",
                "tool_calls": []
            }

        # 6. Booking intent or explicit date provided in active booking context -> Check availability if date is known, or ask for service/doctor/date
        if _has_booking_intent(user_text) or (explicit_date_given and target_date_str and (workflow_state in ["CHECKING_AVAILABILITY", "COLLECTING_INFO", "START"] or conv_state.get("intent") in ["BOOK_APPOINTMENT", "UNKNOWN"])):
            if explicit_date_given and target_date_str:
                return {
                    "content": f"Checking open slots for you on {target_date_str}...",
                    "tool_calls": [{
                        "name": "check_availability",
                        "arguments": {
                            "date": target_date_str,
                            "doctor_id": doc_id or 1,
                            "service_id": svc_id
                        }
                    }]
                }
            else:
                if not doc_id:
                    if lang == "urdu":
                        greeting = f"ہیلو {effective_name} صاحب! " if effective_name else "ہیلو! "
                        return {
                            "content": f"{greeting}سمائل کیئر کلینک میں ہمارے دو ڈاکٹرز پریکٹس کر رہے ہیں: ڈاکٹر احمد خان اور ڈاکٹر سارہ ملک۔ آپ کس ڈاکٹر سے اپائنٹمنٹ لینا چاہیں گے؟",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        greeting = f"Hello {effective_name}! " if effective_name else "Hello! "
                        return {
                            "content": f"{greeting}SmileCare Dental Clinic mein hamare 2 practicing dentists hain: Dr. Ahmed Khan aur Dr. Sara Malik. Aap kis doctor ke sath appointment prefer karein ge?",
                            "tool_calls": []
                        }
                    greeting = f"Hello {effective_name}! " if effective_name else "Hello! "
                    return {
                        "content": f"{greeting}We have two practicing dentists at SmileCare: Dr. Ahmed Khan and Dr. Sara Malik. Which doctor would you prefer for your appointment?",
                        "tool_calls": []
                    }
                else:
                    if lang == "urdu":
                        return {
                            "content": f"آپ {doc_name} کے ساتھ کس تاریخ کو تشریف لانا چاہیں گے؟",
                            "tool_calls": []
                        }
                    elif lang == "roman_urdu":
                        return {
                            "content": f"Aap {doc_name} ke sath kis date ko appointment book karwana chahein ge?",
                            "tool_calls": []
                        }
                    return {
                        "content": f"Which date would you like to book your appointment with {doc_name}?",
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
            content_text = m.get("content") or ""

            if role == "assistant" and m.get("tool_calls"):
                parts = []
                if content_text.strip():
                    parts.append(types.Part.from_text(text=content_text.strip()))
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

                tool_part = types.Part.from_function_response(
                    name=m.get("tool_name", "tool"),
                    response=parsed_resp
                )
                if contents and contents[-1].role == "user" and any(getattr(p, "function_response", None) for p in contents[-1].parts):
                    contents[-1].parts.append(tool_part)
                else:
                    contents.append(types.Content(role="user", parts=[tool_part]))

            else:
                gemini_role = "user" if role in ["user", "system"] else "model"
                if not content_text.strip():
                    continue
                part = types.Part.from_text(text=content_text.strip())
                if contents and contents[-1].role == gemini_role and not any(getattr(p, "function_call", None) or getattr(p, "function_response", None) for p in contents[-1].parts):
                    contents[-1].parts.append(part)
                else:
                    contents.append(types.Content(role=gemini_role, parts=[part]))

        # Ensure history never ends with a model turn
        while contents and contents[-1].role == "model":
            contents.pop()

        gemini_tools = self._translate_tools_to_gemini(tools)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=gemini_tools,
            temperature=0.2
        )

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
                    wait_sec = float(match.group(1)) if match else 2.0
                    if wait_sec > 5.0:
                        # Long quota exhaustion limit (e.g. 50s-14m) -> immediately raise for fallback rather than freezing HTTP connection
                        raise e
                    print(f"[GeminiAdapter Rate-Limit 429]: Retrying in {wait_sec:.1f}s...")
                    time.sleep(wait_sec)
                else:
                    raise e


        # Check for tool calls
        tool_calls = []
        text_content = ""
        if response and getattr(response, "candidates", None) and response.candidates:
            first_candidate = response.candidates[0]
            if getattr(first_candidate, "content", None) and getattr(first_candidate.content, "parts", None):
                for part in first_candidate.content.parts:
                    if getattr(part, "function_call", None):
                        fc = part.function_call
                        ts_hex = (
                            part.thought_signature.hex()
                            if getattr(part, "thought_signature", None)
                            else None
                        )
                        tool_calls.append({
                            "id": getattr(fc, "id", None) or f"call_{len(tool_calls)}",
                            "name": fc.name,
                            "arguments": dict(fc.args) if fc.args else {},
                            "thought_signature": ts_hex
                        })
                    if getattr(part, "text", None):
                        text_content += part.text

        return {
            "content": text_content.strip(),
            "tool_calls": tool_calls
        }


class GroqAdapter(BaseLLMAdapter):
    """Groq Provider Adapter using OpenAI-compatible function calling format."""
    def __init__(self, api_key: str, model_name: str = "openai/gpt-oss-120b"):
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
                tool_name = m.get("tool_name") or "check_availability"
                formatted_messages.append({
                    "role": "tool",
                    "name": tool_name,
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

        tool_calls = []
        text_content = ""
        if response and getattr(response, "choices", None) and response.choices:
            choice = response.choices[0].message
            text_content = choice.content or ""
            if getattr(choice, "tool_calls", None) and choice.tool_calls:
                for tc in choice.tool_calls:
                    args = {}
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        pass
                    tool_calls.append({
                        "id": getattr(tc, "id", None) or f"call_{len(tool_calls)}",
                        "name": getattr(tc.function, "name", ""),
                        "arguments": args
                    })

        return {
            "content": text_content,
            "tool_calls": tool_calls
        }



class LLMClient:
    """Unified LLM Client Factory and Router with resilient graceful fallback."""
    def __init__(self, provider: Optional[str] = None):
        from flask import has_app_context, current_app
        app_provider = current_app.config.get("LLM_PROVIDER") if has_app_context() else None
        self.provider = (provider or app_provider or Config.LLM_PROVIDER or "mock").lower()

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
        try:
            return self.adapter.chat_completion(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                conversation_state=conversation_state
            )
        except Exception as e:
            print(f"[LLMClient Warning]: Provider '{self.provider}' failed with: {e}. Gracefully falling back to deterministic adapter.")
            return MockAdapter().chat_completion(
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                conversation_state=conversation_state
            )
