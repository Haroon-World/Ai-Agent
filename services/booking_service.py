import re
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Tuple, Any
from sqlalchemy.exc import IntegrityError
from models import db, Business, Doctor, Service, Customer, Appointment, DoctorSchedule, DoctorLeave
from services.reminder_service import ReminderService

import threading
from flask import has_request_context, g
from sqlalchemy.orm import joinedload

# Fallback timezone used only when no business record is found
_DEFAULT_TZ = "Asia/Karachi"

# Minimum lead time in minutes required for same-day bookings to prevent offering slots that are past or starting immediately
SAME_DAY_LEAD_TIME_MINUTES = 15

_thread_local_cache = threading.local()

class RequestCache:
    """Request-scoped cache falling back to thread-local cache outside Flask requests."""
    @staticmethod
    def get(key: str) -> Any:
        if has_request_context():
            return getattr(g, f"_req_cache_{key}", None)
        local_dict = getattr(_thread_local_cache, "data", None)
        return local_dict.get(key) if local_dict else None

    @staticmethod
    def set(key: str, value: Any):
        if has_request_context():
            setattr(g, f"_req_cache_{key}", value)
            return
        if not hasattr(_thread_local_cache, "data") or _thread_local_cache.data is None:
            _thread_local_cache.data = {}
        _thread_local_cache.data[key] = value

    @staticmethod
    def clear():
        if not has_request_context():
            _thread_local_cache.data = {}


def _get_business_info(business_id: int) -> Dict[str, Any]:
    cache_key = f"biz_info_{business_id}"
    info = RequestCache.get(cache_key)
    if info is None:
        biz = db.session.get(Business, business_id)
        if biz:
            info = {
                "id": biz.id,
                "name": biz.name,
                "address": biz.address,
                "phone": biz.phone,
                "timezone": biz.timezone or _DEFAULT_TZ,
                "opening_hours": biz.opening_hours,
                "policies": biz.policies or "Standard clinic policies apply.",
                "consultation_fee": getattr(biz, "consultation_fee", 2000.0) or 2000.0
            }
        else:
            info = {
                "id": business_id,
                "name": "SmileCare Dental Clinic",
                "address": "Plot 42-B, Main Boulevard, Gulberg III, Lahore",
                "phone": "+92 42 35789000",
                "timezone": _DEFAULT_TZ,
                "opening_hours": "09:00 AM - 05:00 PM",
                "policies": "Standard clinic policies apply.",
                "consultation_fee": 2000.0
            }
        RequestCache.set(cache_key, info)
    return info


def _get_business(business_id: int) -> Optional[Business]:
    return db.session.get(Business, business_id)


def _get_business_tz(business_id: int) -> ZoneInfo:
    """Return the ZoneInfo for the given business, falling back to Asia/Karachi."""
    cache_key = f"biz_tz_{business_id}"
    tz = RequestCache.get(cache_key)
    if tz is None:
        info = _get_business_info(business_id)
        tz_name = info.get("timezone") or _DEFAULT_TZ
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = ZoneInfo(_DEFAULT_TZ)
        RequestCache.set(cache_key, tz)
    return tz


def _parse_time_str(t_str: Any, default: Tuple[int, int] = (9, 0)) -> Tuple[int, int]:
    """Robustly parse time strings in 24-hour ('17:00', '23:30') or 12-hour ('11:30 PM', '9:00 AM') format."""
    if not t_str:
        return default
    t_clean = str(t_str).strip().lower()
    is_pm = "pm" in t_clean
    is_am = "am" in t_clean
    clean_num = re.sub(r'[^\d:]', '', t_clean)
    parts = clean_num.split(":")
    if not parts or not parts[0]:
        return default
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 and parts[1] else 0
        if is_pm and h < 12:
            h += 12
        elif is_am and h == 12:
            h = 0
        return (h, m)
    except Exception:
        return default


def _get_slots_for_doctor_on_date(doc: Doctor, target_date: Any, duration: int, business_id: int) -> Tuple[List[str], str]:
    """Calculate available time slots for a doctor on target_date. Returns (slots, unavailability_message)."""
    if hasattr(doc, "is_active") and not doc.is_active:
        return [], f"{doc.name} is currently not active."

    day_name = target_date.strftime("%A")
    date_str = target_date.strftime("%Y-%m-%d")

    sched = DoctorSchedule.query.filter_by(doctor_id=doc.id, day_of_week=day_name).first()
    is_day_available = sched.is_available if sched else (day_name in [d.strip() for d in (doc.working_days or "").split(",")])
    start_time_str = sched.start_time if sched else (doc.start_time or "09:00")
    end_time_str = sched.end_time if sched else (doc.end_time or "17:00")

    if not is_day_available:
        return [], f"{doc.name} is closed / not practicing on {day_name}s."

    start_h, start_m = _parse_time_str(start_time_str, default=(9, 0))
    end_h, end_m = _parse_time_str(end_time_str, default=(17, 0))

    leaves = DoctorLeave.query.filter_by(doctor_id=doc.id, leave_date=date_str).all()
    if any(l.is_all_day for l in leaves):
        return [], f"{doc.name} is on leave / unavailable on {date_str}."

    blocked_ranges: List[Tuple[int, int]] = []
    for l in leaves:
        if not l.is_all_day and l.start_time and l.end_time:
            try:
                l_sh, l_sm = _parse_time_str(l.start_time)
                l_eh, l_em = _parse_time_str(l.end_time)
                blocked_ranges.append((l_sh * 60 + l_sm, l_eh * 60 + l_em))
            except Exception:
                pass

    booked_appts = Appointment.query.filter_by(
        business_id=business_id,
        doctor_id=doc.id,
        appointment_date=date_str,
        status="CONFIRMED"
    ).all()

    for a in booked_appts:
        try:
            ah, am = _parse_time_str(a.appointment_time)
            a_start = ah * 60 + am
            svc_dur = a.service.duration if a.service else 30
            blocked_ranges.append((a_start, a_start + svc_dur))
        except Exception:
            pass

    if getattr(doc, "break_start_time", None) and getattr(doc, "break_end_time", None):
        try:
            b_sh, b_sm = _parse_time_str(doc.break_start_time)
            b_eh, b_em = _parse_time_str(doc.break_end_time)
            b_start_min = b_sh * 60 + b_sm
            b_end_min = b_eh * 60 + b_em
            if b_start_min < b_end_min:
                blocked_ranges.append((b_start_min, b_end_min))
        except Exception:
            pass

    step_interval = getattr(doc, "slot_interval", None) or 30

    slots = []
    curr = datetime.combine(target_date, time(start_h, start_m))
    end_time_dt = datetime.combine(target_date, time(end_h, end_m))

    while curr + timedelta(minutes=duration) <= end_time_dt:
        slot_str = curr.strftime("%H:%M")
        s_min = curr.hour * 60 + curr.minute
        e_min = s_min + duration

        overlaps = any(s_min < blk_end and e_min > blk_start for blk_start, blk_end in blocked_ranges)
        if not overlaps:
            slots.append(slot_str)

        curr += timedelta(minutes=step_interval)

    return slots, ""


class BookingService:
    @staticmethod
    def get_clinic_info(business_id: int) -> Dict[str, Any]:
        """Fetch clinic details, opening hours, policies and contact info."""
        business = _get_business(business_id)
        if not business:
            return {"error": f"Business with ID {business_id} not found"}
        return business.to_dict()

    @staticmethod
    def get_services(business_id: int) -> List[Dict[str, Any]]:
        """Fetch all dental services offered by the business."""
        cache_key = f"services_dict_{business_id}"
        svcs = RequestCache.get(cache_key)
        if svcs is None:
            services = Service.query.filter_by(business_id=business_id).all()
            svcs = [s.to_dict() for s in services]
            RequestCache.set(cache_key, svcs)
        return svcs

    @staticmethod
    def get_doctors(business_id: int) -> List[Dict[str, Any]]:
        """Fetch all doctors for the business."""
        cache_key = f"doctors_dict_{business_id}"
        docs = RequestCache.get(cache_key)
        if docs is None:
            doctors = Doctor.query.filter_by(business_id=business_id).options(joinedload(Doctor.schedules)).all()
            docs = [d.to_dict() for d in doctors]
            RequestCache.set(cache_key, docs)
        return docs

    @staticmethod
    def check_availability(
        business_id: int,
        doctor_id: Optional[int] = None,
        service_id: Optional[int] = None,
        date_str: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculate actual available time slots using:
        - Normalized DoctorSchedule (per day of week: is_available, start_time, end_time)
        - Service duration (e.g. 30, 45, 60 minutes)
        - Active confirmed appointments (excluding CANCELLED)
        - DoctorLeave / blocked time ranges
        """
        if not date_str:
            return {"success": False, "error": "Date is required in YYYY-MM-DD format"}

        tz = _get_business_tz(business_id)
        now_dt = datetime.now(tz)
        today = now_dt.date()

        clean_date_str = str(date_str).strip().lower()
        if clean_date_str in ["today", "aaj", "آج"]:
            target_date = today
            date_str = today.strftime("%Y-%m-%d")
        elif clean_date_str in ["tomorrow", "kal"]:
            target_date = today + timedelta(days=1)
            date_str = target_date.strftime("%Y-%m-%d")
        else:
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                return {"success": False, "error": "Invalid date format. Please use YYYY-MM-DD"}

        # Validate date is not in the past — compare against clinic's local date
        if target_date < today:
            return {"success": False, "error": f"The date {date_str} is in the past. Please select a future date."}

        day_name = target_date.strftime("%A")

        # Doctors query
        query = Doctor.query.filter_by(business_id=business_id)
        if doctor_id:
            query = query.filter_by(id=doctor_id)
        doctors = query.all()

        if not doctors:
            return {"success": False, "error": "No matching doctors found for this clinic."}

        service = None
        duration_override = None
        if service_id:
            service = Service.query.filter_by(id=service_id, business_id=business_id).first()
            if service:
                duration_override = service.duration

        results = []
        all_available_slots = []

        for doc in doctors:
            eff_duration = duration_override or getattr(doc, "slot_interval", None) or 30
            slots, msg = _get_slots_for_doctor_on_date(doc, target_date, eff_duration, business_id)

            # Filter already-passed time slots when target_date is today
            if target_date == today and slots:
                cutoff_dt = now_dt + timedelta(minutes=SAME_DAY_LEAD_TIME_MINUTES)
                filtered_slots = []
                for s in slots:
                    try:
                        sh, sm = _parse_time_str(s)
                        slot_dt = datetime.combine(target_date, time(sh, sm), tzinfo=tz)
                        if slot_dt >= cutoff_dt:
                            filtered_slots.append(s)
                    except Exception:
                        pass
                slots = filtered_slots
                if not slots and not msg:
                    msg = f"No more available slots for {doc.name} today."

            if not slots and msg:
                results.append({
                    "doctor_id": doc.id,
                    "doctor_name": doc.name,
                    "date": date_str,
                    "day": day_name,
                    "available_slots": [],
                    "message": msg
                })
            else:
                results.append({
                    "doctor_id": doc.id,
                    "doctor_name": doc.name,
                    "specialization": doc.specialization,
                    "date": date_str,
                    "day": day_name,
                    "available_slots": slots,
                    "total_slots": len(slots)
                })

            if len(doctors) == 1 or not all_available_slots:
                all_available_slots = slots

        target_doc = doctors[0] if doctors else None
        target_doc_dur = duration_override or (getattr(target_doc, "slot_interval", None) if target_doc else 30) or 30

        # Next available date lookup if requested date has no slots
        next_available_date = None
        next_available_day = None
        next_available_slots = []
        if not all_available_slots and target_doc:
            for offset in range(1, 15):
                next_dt = target_date + timedelta(days=offset)
                n_slots, _ = _get_slots_for_doctor_on_date(target_doc, next_dt, target_doc_dur, business_id)
                if n_slots:
                    next_available_date = next_dt.strftime("%Y-%m-%d")
                    next_available_day = next_dt.strftime("%A")
                    next_available_slots = n_slots
                    break

        return {
            "success": True,
            "doctor": target_doc.name if (doctor_id and target_doc) else "All Doctors",
            "doctor_id": doctor_id,
            "date": date_str,
            "day": day_name,
            "service": service.name if service else "Dental Consultation",
            "duration_minutes": target_doc_dur,
            "available_slots": all_available_slots,
            "is_closed": len(all_available_slots) == 0,
            "next_available_date": next_available_date,
            "next_available_day": next_available_day,
            "next_available_slots": next_available_slots,
            "results": results
        }

    @staticmethod
    def book_appointment(
        business_id: int,
        customer_name: str,
        customer_phone: str,
        doctor_id: int,
        service_id: int,
        appointment_date: str,
        appointment_time: str,
        notes: Optional[str] = None,
        idempotency_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Atomic appointment booking transaction with:
        - Customer deduplication
        - Idempotency check
        - Strict schedule revalidation (day availability, working hours, leaves, overlaps)
        - DB-level unique constraint as final safety net
        - Automatic reminder scheduling
        """
        missing_fields = []
        name_str = str(customer_name).strip() if customer_name else ""
        if not name_str or name_str.lower() in ["valued patient", "patient", "customer", "user", "anonymous", "guest", "test", "n/a", "none"]:
            missing_fields.append("customer_name")

        phone_str = str(customer_phone).strip() if customer_phone else ""
        biz = db.session.get(Business, business_id)
        biz_phone = biz.phone.strip() if (biz and biz.phone) else ""
        clean_p = phone_str.replace(" ", "").replace("-", "")
        clean_bp = biz_phone.replace(" ", "").replace("-", "")
        if not phone_str or phone_str.replace("0", "").replace("+", "").replace("-", "").replace(" ", "") == "" or (clean_bp and clean_p == clean_bp):
            missing_fields.append("customer_phone")

        if not doctor_id:
            missing_fields.append("doctor_id")
        if not service_id:
            missing_fields.append("service_id")
        if not appointment_date or not str(appointment_date).strip():
            missing_fields.append("appointment_date")
        if not appointment_time or not str(appointment_time).strip():
            missing_fields.append("appointment_time")

        if missing_fields:
            return {
                "success": False,
                "error": f"Missing required booking fields: {', '.join(missing_fields)}",
                "missing_fields": missing_fields
            }

        # --- Idempotency guard ---
        if idempotency_key:
            existing = Appointment.query.filter_by(idempotency_key=idempotency_key).first()
            if existing:
                return {
                    "success": True,
                    "is_duplicate_request": True,
                    "appointment": existing.to_dict(),
                    "message": "Appointment already processed successfully."
                }

        # --- Validate Doctor ---
        doctor = Doctor.query.filter_by(id=doctor_id, business_id=business_id).first()
        if not doctor:
            return {"success": False, "error": f"Doctor with ID {doctor_id} not found."}
        if hasattr(doctor, "is_active") and not doctor.is_active:
            return {"success": False, "error": f"Dr. {doctor.name} is currently not active."}

        # --- Validate Service ---
        service = Service.query.filter_by(id=service_id, business_id=business_id).first()
        if not service:
            return {"success": False, "error": f"Service with ID {service_id} not found."}

        # --- Validate Date & Day of Week ---
        tz = _get_business_tz(business_id)
        now_dt = datetime.now(tz)
        today = now_dt.date()

        clean_appt_date = str(appointment_date).strip().lower()
        if clean_appt_date in ["today", "aaj", "آج"]:
            target_date = today
            appointment_date = today.strftime("%Y-%m-%d")
        elif clean_appt_date in ["tomorrow", "kal"]:
            target_date = today + timedelta(days=1)
            appointment_date = target_date.strftime("%Y-%m-%d")
        else:
            try:
                target_date = datetime.strptime(appointment_date, "%Y-%m-%d").date()
            except ValueError:
                return {"success": False, "error": "Invalid appointment_date format. Use YYYY-MM-DD."}

        day_name = target_date.strftime("%A")

        # Past-date revalidation
        if target_date < today:
            return {"success": False, "error": f"The date {appointment_date} is in the past."}

        # --- Revalidate DoctorSchedule ---
        sched = DoctorSchedule.query.filter_by(doctor_id=doctor.id, day_of_week=day_name).first()
        is_day_available = sched.is_available if sched else (day_name in [d.strip() for d in (doctor.working_days or "").split(",")])
        start_time_str = sched.start_time if sched else (doctor.start_time or "09:00")
        end_time_str = sched.end_time if sched else (doctor.end_time or "17:00")

        if not is_day_available:
            return {
                "success": False,
                "error": f"Dr. {doctor.name} is closed / not practicing on {day_name}s."
            }

        # --- Revalidate Working Hours ---
        req_h, req_m = _parse_time_str(appointment_time, default=(-1, -1))
        start_h, start_m = _parse_time_str(start_time_str, default=(9, 0))
        end_h, end_m = _parse_time_str(end_time_str, default=(17, 0))

        if req_h < 0 or req_m < 0:
            return {"success": False, "error": "Invalid time format. Use HH:MM."}

        req_start_m = req_h * 60 + req_m
        req_end_m = req_start_m + service.duration
        start_m = start_h * 60 + start_m
        end_m = end_h * 60 + end_m

        if not (start_m <= req_start_m and req_end_m <= end_m):
            return {
                "success": False,
                "error": (
                    f"Requested slot {appointment_time} ({service.duration} mins) is outside Dr. {doctor.name}'s "
                    f"working hours ({start_time_str}–{end_time_str}) on {day_name}s."
                )
            }

        # --- Revalidate DoctorLeave / Blocked Period ---
        leaves = DoctorLeave.query.filter_by(doctor_id=doctor.id, leave_date=appointment_date).all()
        for l in leaves:
            if l.is_all_day:
                return {
                    "success": False,
                    "error": f"Dr. {doctor.name} is on leave on {appointment_date} ({l.reason or 'All day'})."
                }
            if l.start_time and l.end_time:
                try:
                    l_sh, l_sm = _parse_time_str(l.start_time)
                    l_eh, l_em = _parse_time_str(l.end_time)
                    l_start = l_sh * 60 + l_sm
                    l_end = l_eh * 60 + l_em
                    if req_start_m < l_end and req_end_m > l_start:
                        return {
                            "success": False,
                            "error": f"Dr. {doctor.name} is unavailable from {l.start_time} to {l.end_time} on {appointment_date}."
                        }
                except Exception:
                    pass

        # --- Duration-aware overlap conflict check against existing confirmed appointments ---
        booked_appts = Appointment.query.filter_by(
            business_id=business_id,
            doctor_id=doctor_id,
            appointment_date=appointment_date,
            status="CONFIRMED"
        ).all()

        for a in booked_appts:
            try:
                ex_h, ex_m = _parse_time_str(a.appointment_time)
                ex_start_m = ex_h * 60 + ex_m
                ex_svc_dur = a.service.duration if a.service else 30
                ex_end_m = ex_start_m + ex_svc_dur
                if req_start_m < ex_end_m and req_end_m > ex_start_m:
                    return {
                        "success": False,
                        "error": (
                            f"The requested slot at {appointment_time} overlaps an existing "
                            f"{ex_svc_dur}-minute appointment at {a.appointment_time}. "
                            "Please choose a different time."
                        )
                    }
            except Exception:
                pass

        # --- Authoritative Generated Availability Check ---
        avail_slots, _ = _get_slots_for_doctor_on_date(doctor, target_date, service.duration, business_id)
        if appointment_time not in avail_slots:
            return {
                "success": False,
                "error": f"The slot {appointment_time} is not available for Dr. {doctor.name} on {appointment_date}."
            }

        try:
            # --- Customer deduplication ---
            customer = Customer.query.filter_by(
                business_id=business_id, phone=customer_phone.strip()
            ).first()
            if not customer:
                customer = Customer(
                    business_id=business_id,
                    name=customer_name.strip(),
                    phone=customer_phone.strip()
                )
                db.session.add(customer)
                db.session.flush()
            else:
                if customer_name.strip() and customer.name != customer_name.strip():
                    customer.name = customer_name.strip()
                    db.session.flush()

            # --- Create appointment ---
            appointment = Appointment(
                business_id=business_id,
                customer_id=customer.id,
                doctor_id=doctor.id,
                service_id=service.id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                status="CONFIRMED",
                notes=notes,
                idempotency_key=idempotency_key
            )
            db.session.add(appointment)
            db.session.flush()

            # --- Schedule automated reminder ---
            ReminderService.schedule_for_appointment(appointment)

            db.session.commit()

            return {
                "success": True,
                "appointment_id": appointment.id,
                "appointment": appointment.to_dict(),
                "message": (
                    f"Appointment successfully confirmed for {customer.name} on "
                    f"{appointment_date} at {appointment_time} with {doctor.name} for {service.name}."
                )
            }

        except IntegrityError:
            db.session.rollback()
            return {
                "success": False,
                "error": (
                    f"The slot at {appointment_time} on {appointment_date} with {doctor.name} "
                    "is already booked. Please choose a different slot."
                )
            }
        except Exception as e:
            db.session.rollback()
            return {
                "success": False,
                "error": f"An unexpected error occurred during booking: {str(e)}"
            }

    @staticmethod
    def update_doctor_schedule(doctor_id: int, schedule_data: List[Dict[str, Any]]) -> bool:
        """Update or create normalized DoctorSchedule entries for a doctor."""
        for item in schedule_data:
            day = item.get("day_of_week")
            if not day:
                continue
            sched = DoctorSchedule.query.filter_by(doctor_id=doctor_id, day_of_week=day).first()
            if not sched:
                sched = DoctorSchedule(doctor_id=doctor_id, day_of_week=day)
                db.session.add(sched)
            sched.is_available = bool(item.get("is_available", True))
            sched.start_time = item.get("start_time", "09:00")
            sched.end_time = item.get("end_time", "17:00")
        db.session.commit()
        return True

    @staticmethod
    def cancel_appointment(
        business_id: int,
        appointment_id: int,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """Cancel an existing appointment and update its reminders."""
        appt = Appointment.query.filter_by(id=appointment_id, business_id=business_id).first()
        if not appt:
            return {"success": False, "error": f"Appointment #{appointment_id} not found."}

        if appt.status == "CANCELLED":
            return {"success": False, "error": f"Appointment #{appointment_id} is already cancelled."}

        appt.status = "CANCELLED"
        if reason:
            appt.notes = f"{appt.notes or ''} [Cancelled: {reason}]".strip()

        ReminderService.cancel_for_appointment(appt.id)

        db.session.commit()
        return {
            "success": True,
            "appointment_id": appt.id,
            "message": (
                f"Appointment #{appt.id} for {appt.customer.name} on "
                f"{appt.appointment_date} at {appt.appointment_time} has been cancelled."
            )
        }

    @staticmethod
    def reschedule_appointment(
        business_id: int,
        appointment_id: int,
        new_date: str,
        new_time: str
    ) -> Dict[str, Any]:
        """Reschedule an existing appointment to a new date and time."""
        appt = Appointment.query.filter_by(id=appointment_id, business_id=business_id).first()
        if not appt:
            return {"success": False, "error": f"Appointment #{appointment_id} not found."}

        if appt.status == "CANCELLED":
            return {
                "success": False,
                "error": f"Cannot reschedule cancelled appointment #{appointment_id}. Please book a new appointment."
            }

        service = appt.service
        duration = service.duration if service else 30
        try:
            new_h, new_m = map(int, new_time.split(":"))
        except (ValueError, TypeError):
            return {"success": False, "error": "Invalid new_time format. Use HH:MM."}

        new_start_m = new_h * 60 + new_m
        new_end_m = new_start_m + duration

        conflicts = Appointment.query.filter_by(
            business_id=business_id,
            doctor_id=appt.doctor_id,
            appointment_date=new_date,
            status="CONFIRMED"
        ).filter(Appointment.id != appointment_id).all()

        for c in conflicts:
            try:
                ch, cm = map(int, c.appointment_time.split(":"))
                c_start_m = ch * 60 + cm
                c_dur = c.service.duration if c.service else 30
                c_end_m = c_start_m + c_dur
                if new_start_m < c_end_m and new_end_m > c_start_m:
                    return {
                        "success": False,
                        "error": (
                            f"The slot at {new_time} on {new_date} overlaps an existing appointment. "
                            "Please select another slot."
                        )
                    }
            except Exception:
                pass

        try:
            appt.appointment_date = new_date
            appt.appointment_time = new_time
            appt.status = "CONFIRMED"

            ReminderService.cancel_for_appointment(appt.id)
            ReminderService.schedule_for_appointment(appt)

            db.session.commit()
            return {
                "success": True,
                "appointment_id": appt.id,
                "appointment": appt.to_dict(),
                "message": (
                    f"Appointment #{appt.id} successfully rescheduled to "
                    f"{new_date} at {new_time} with {appt.doctor.name}."
                )
            }
        except IntegrityError:
            db.session.rollback()
            return {
                "success": False,
                "error": f"The requested slot on {new_date} at {new_time} is not available."
            }
        except Exception as e:
            db.session.rollback()
            return {"success": False, "error": f"Failed to reschedule: {str(e)}"}

    @staticmethod
    def update_customer_details(
        business_id: int,
        conversation_id: int,
        customer_name: Optional[str] = None,
        customer_phone: Optional[str] = None
    ) -> Dict[str, Any]:
        """Update customer contact information (name/phone) scoped to conversation & business_id."""
        from models import Conversation, Appointment, Customer
        conv = Conversation.query.filter_by(id=conversation_id, business_id=business_id).first()
        if not conv:
            return {"success": False, "error": f"Conversation #{conversation_id} not found."}

        customer = None
        # 1. Primary: Use conversation's linked customer_id
        if conv.customer_id:
            customer = Customer.query.filter_by(id=conv.customer_id, business_id=business_id).first()

        # 2. Secondary: If conv.customer_id is not set, resolve customer from existing appointment in this conversation
        if not customer:
            appt = Appointment.query.filter(
                Appointment.business_id == business_id,
                Appointment.idempotency_key.like(f"conv-{conv.id}-%")
            ).order_by(Appointment.id.desc()).first()
            if appt and appt.customer_id:
                customer = Customer.query.filter_by(id=appt.customer_id, business_id=business_id).first()
                if customer:
                    conv.customer_id = customer.id

        # 3. Tertiary: Look up by pending customer phone if available
        if not customer and conv.pending_customer_phone:
            customer = Customer.query.filter_by(
                business_id=business_id, phone=conv.pending_customer_phone.strip()
            ).first()
            if customer:
                conv.customer_id = customer.id

        clean_name = customer_name.strip() if customer_name and customer_name.strip() else None
        clean_phone = customer_phone.strip().replace(" ", "").replace("-", "") if customer_phone and customer_phone.strip() else None

        if not clean_name and not clean_phone:
            return {"success": False, "error": "At least one of customer_name or customer_phone must be provided."}

        try:
            if customer:
                if clean_name:
                    customer.name = clean_name
                if clean_phone and customer.phone != clean_phone:
                    # Check if clean_phone already belongs to another Customer row in the same business
                    existing_other = Customer.query.filter_by(business_id=business_id, phone=clean_phone).first()
                    if existing_other and existing_other.id != customer.id:
                        # Re-link existing appointments to existing_other and update its name
                        for a in Appointment.query.filter_by(customer_id=customer.id).all():
                            a.customer_id = existing_other.id
                        if clean_name:
                            existing_other.name = clean_name
                        customer = existing_other
                    else:
                        customer.phone = clean_phone
                conv.customer_id = customer.id
            else:
                lookup_phone = clean_phone or conv.pending_customer_phone or "0000000000"
                customer = Customer.query.filter_by(business_id=business_id, phone=lookup_phone).first()
                if not customer:
                    customer = Customer(
                        business_id=business_id,
                        name=clean_name or conv.pending_customer_name or "Valued Patient",
                        phone=lookup_phone
                    )
                    db.session.add(customer)
                    db.session.flush()
                else:
                    if clean_name:
                        customer.name = clean_name
                    if clean_phone:
                        customer.phone = clean_phone
                conv.customer_id = customer.id

            if clean_name:
                conv.pending_customer_name = clean_name
            if clean_phone:
                conv.pending_customer_phone = clean_phone

            db.session.commit()

            return {
                "success": True,
                "customer_id": customer.id,
                "customer": customer.to_dict(),
                "message": f"Customer contact details updated successfully: Name='{customer.name}', Phone='{customer.phone}'."
            }
        except Exception as e:
            db.session.rollback()
            return {"success": False, "error": f"Failed to update customer details: {str(e)}"}
