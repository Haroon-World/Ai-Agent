"""
Shared date helpers for test suite determinism.
"""
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Union
from models import db, Business, Doctor, DoctorSchedule, DoctorLeave


def get_next_open_weekday(business_id: int = 1, from_date: Optional[Union[str, date, datetime]] = None, doctor_id: Optional[int] = None) -> str:
    """
    Returns the date string (YYYY-MM-DD) of the next day the given business
    (or specific doctor if doctor_id given) is actually open,
    starting from tomorrow (or from_date if given).
    Queries real Doctor/DoctorSchedule/DoctorLeave records.
    """
    biz = db.session.get(Business, business_id)
    tz_name = (biz.timezone if biz and biz.timezone else None) or "Asia/Karachi"
    tz = ZoneInfo(tz_name)

    if from_date is None:
        curr = datetime.now(tz).date()
    elif isinstance(from_date, str):
        curr = datetime.strptime(from_date, "%Y-%m-%d").date()
    elif isinstance(from_date, datetime):
        curr = from_date.date()
    else:
        curr = from_date

    if doctor_id is not None:
        doc = db.session.get(Doctor, doctor_id)
        doctors = [doc] if doc else []
    else:
        doctors = Doctor.query.filter_by(business_id=business_id).all()

    for i in range(1, 60):
        cand = curr + timedelta(days=i)
        day_name = cand.strftime("%A")
        cand_str = cand.strftime("%Y-%m-%d")
        for doc in doctors:
            if hasattr(doc, "is_active") and not doc.is_active:
                continue
            sched = DoctorSchedule.query.filter_by(doctor_id=doc.id, day_of_week=day_name).first()
            is_avail = sched.is_available if sched else (day_name in [d.strip() for d in (doc.working_days or "").split(",")])
            if is_avail:
                on_leave = DoctorLeave.query.filter_by(doctor_id=doc.id, leave_date=cand_str).first()
                if not on_leave:
                    return cand_str

    return (curr + timedelta(days=1)).strftime("%Y-%m-%d")


def get_next_closed_day(business_id: int = 1, from_date: Optional[Union[str, date, datetime]] = None) -> str:
    """
    Returns the date string (YYYY-MM-DD) of the next day the given business
    is completely closed (no doctors available/scheduled), starting from tomorrow.
    """
    biz = db.session.get(Business, business_id)
    tz_name = (biz.timezone if biz and biz.timezone else None) or "Asia/Karachi"
    tz = ZoneInfo(tz_name)

    if from_date is None:
        curr = datetime.now(tz).date()
    elif isinstance(from_date, str):
        curr = datetime.strptime(from_date, "%Y-%m-%d").date()
    elif isinstance(from_date, datetime):
        curr = from_date.date()
    else:
        curr = from_date

    doctors = Doctor.query.filter_by(business_id=business_id).all()
    for i in range(1, 60):
        cand = curr + timedelta(days=i)
        day_name = cand.strftime("%A")
        cand_str = cand.strftime("%Y-%m-%d")
        any_open = False
        for doc in doctors:
            if hasattr(doc, "is_active") and not doc.is_active:
                continue
            sched = DoctorSchedule.query.filter_by(doctor_id=doc.id, day_of_week=day_name).first()
            is_avail = sched.is_available if sched else (day_name in [d.strip() for d in (doc.working_days or "").split(",")])
            if is_avail:
                on_leave = DoctorLeave.query.filter_by(doctor_id=doc.id, leave_date=cand_str).first()
                if not on_leave:
                    any_open = True
                    break
        if not any_open:
            return cand_str

    return (curr + timedelta(days=1)).strftime("%Y-%m-%d")


from contextlib import contextmanager
from unittest.mock import patch
from ai.llm_client import resolve_date_string as real_resolve_date_string


def make_open_date_resolver(open_date: str):
    """
    Returns a resolver function that substitutes open_date when the user query
    refers to 'tomorrow', 'kal', or 'کل', but delegates all other inputs to the
    real resolve_date_string.
    """
    def _resolver(user_content: str, business_id: int = 1):
        if not user_content:
            return None
        lower = user_content.lower()
        if any(w in lower for w in ["tomorrow", "kal", "کل", "day after tomorrow", "parson", "پرسوں"]):
            return open_date
        return real_resolve_date_string(user_content, business_id=business_id)
    return _resolver


@contextmanager
def patch_open_date(business_id: int = 1, doctor_id: Optional[int] = None, from_date: Optional[Union[str, date, datetime]] = None, return_value: Optional[str] = None):
    """
    Context manager that patches resolve_date_string across both ai.agent and
    ai.llm_client to return an open date for relative date inquiries (tomorrow/kal).
    """
    open_date = return_value or get_next_open_weekday(business_id, from_date=from_date, doctor_id=doctor_id)
    resolver = make_open_date_resolver(open_date)
    with patch("ai.agent.resolve_date_string", side_effect=resolver), \
         patch("ai.llm_client.resolve_date_string", side_effect=resolver):
        yield open_date


