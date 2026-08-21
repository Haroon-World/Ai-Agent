from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Tuple, Any
from sqlalchemy.exc import IntegrityError
from models import db, Business, Doctor, Service, Customer, Appointment
from services.reminder_service import ReminderService

# Fallback timezone used only when no business record is found
_DEFAULT_TZ = "Asia/Karachi"


def _get_business_tz(business_id: int) -> ZoneInfo:
    """Return the ZoneInfo for the given business, falling back to Asia/Karachi."""
    try:
        biz = db.session.get(Business, business_id)
        tz_name = (biz.timezone if biz and biz.timezone else _DEFAULT_TZ)
    except Exception:
        tz_name = _DEFAULT_TZ
    return ZoneInfo(tz_name)


class BookingService:
    @staticmethod
    def get_clinic_info(business_id: int) -> Dict[str, Any]:
        """Fetch clinic details, opening hours, policies and contact info."""
        business = db.session.get(Business, business_id)
        if not business:
            return {"error": f"Business with ID {business_id} not found"}
        return business.to_dict()

    @staticmethod
    def get_services(business_id: int) -> List[Dict[str, Any]]:
        """Fetch all dental services offered by the business."""
        services = Service.query.filter_by(business_id=business_id).all()
        return [s.to_dict() for s in services]

    @staticmethod
    def get_doctors(business_id: int) -> List[Dict[str, Any]]:
        """Fetch all doctors for the business."""
        doctors = Doctor.query.filter_by(business_id=business_id).all()
        return [d.to_dict() for d in doctors]

    @staticmethod
    def check_availability(
        business_id: int,
        doctor_id: Optional[int] = None,
        service_id: Optional[int] = None,
        date_str: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Calculate available time slots for a given doctor (or all doctors) on a specific date.
        Past-date check uses the clinic's configured timezone so that server-UTC and
        clinic-local date disagreements are correctly handled.
        Format of date_str: 'YYYY-MM-DD'
        """
        if not date_str:
            return {"error": "Date is required in YYYY-MM-DD format"}

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Invalid date format. Please use YYYY-MM-DD"}

        # Validate date is not in the past — compare against clinic's local date, not server UTC
        tz = _get_business_tz(business_id)
        today = datetime.now(tz).date()
        if target_date < today:
            return {"error": f"The date {date_str} is in the past. Please select a future date."}

        day_name = target_date.strftime("%A")

        # Doctors query
        query = Doctor.query.filter_by(business_id=business_id)
        if doctor_id:
            query = query.filter_by(id=doctor_id)
        doctors = query.all()

        if not doctors:
            return {"error": "No matching doctors found for this clinic."}

        service = None
        duration = 30
        if service_id:
            service = Service.query.filter_by(id=service_id, business_id=business_id).first()
            if service:
                duration = service.duration

        results = []

        for doc in doctors:
            working_days = [d.strip() for d in doc.working_days.split(",")] if doc.working_days else []
            if day_name not in working_days:
                results.append({
                    "doctor_id": doc.id,
                    "doctor_name": doc.name,
                    "date": date_str,
                    "day": day_name,
                    "available_slots": [],
                    "message": f"{doc.name} does not practice on {day_name}s."
                })
                continue

            # Parse start and end times
            try:
                start_h, start_m = map(int, doc.start_time.split(":"))
                end_h, end_m = map(int, doc.end_time.split(":"))
            except Exception:
                start_h, start_m, end_h, end_m = 9, 0, 17, 0

            # Get all confirmed appointments for this doctor on target_date (with service info)
            booked_appts = Appointment.query.filter_by(
                business_id=business_id,
                doctor_id=doc.id,
                appointment_date=date_str,
                status="CONFIRMED"
            ).all()

            # Build blocked-time ranges: list of (start_min, end_min) for each booked appt
            blocked_ranges: List[Tuple[int, int]] = []
            for a in booked_appts:
                try:
                    ah, am = map(int, a.appointment_time.split(":"))
                    a_start = ah * 60 + am
                    svc_dur = a.service.duration if a.service else 30
                    blocked_ranges.append((a_start, a_start + svc_dur))
                except Exception:
                    pass

            # Generate slots using requested service duration as step — skip any slot that overlaps a blocked range
            slots = []
            curr = datetime.combine(target_date, time(start_h, start_m))
            end_time_dt = datetime.combine(target_date, time(end_h, end_m))

            while curr + timedelta(minutes=duration) <= end_time_dt:
                slot_str = curr.strftime("%H:%M")
                s_min = curr.hour * 60 + curr.minute
                e_min = s_min + duration
                # Check overlap with every blocked range
                overlaps = any(s_min < blk_end and e_min > blk_start for blk_start, blk_end in blocked_ranges)
                if not overlaps:
                    slots.append(slot_str)
                curr += timedelta(minutes=30)

            results.append({
                "doctor_id": doc.id,
                "doctor_name": doc.name,
                "specialization": doc.specialization,
                "date": date_str,
                "day": day_name,
                "available_slots": slots,
                "total_slots": len(slots)
            })

        return {
            "date": date_str,
            "day": day_name,
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
        - Schedule revalidation (working day + working hours)
        - Duration-aware overlap conflict detection
        - DB-level unique constraint as final safety net
        - Automatic reminder scheduling
        """
        # --- Basic input validation with structured missing_fields ---
        missing_fields = []
        name_str = str(customer_name).strip() if customer_name else ""
        if not name_str or name_str.lower() in ["valued patient", "patient", "customer", "user", "anonymous", "guest", "test", "n/a", "none"]:
            missing_fields.append("customer_name")

        phone_str = str(customer_phone).strip() if customer_phone else ""
        if not phone_str or phone_str.replace("0", "").replace("+", "").replace("-", "").replace(" ", "") == "":
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

        # --- Validate Service ---
        service = Service.query.filter_by(id=service_id, business_id=business_id).first()
        if not service:
            return {"success": False, "error": f"Service with ID {service_id} not found."}

        # --- Schedule revalidation: working day ---
        try:
            day_name = datetime.strptime(appointment_date, "%Y-%m-%d").strftime("%A")
        except ValueError:
            return {"success": False, "error": "Invalid appointment_date format. Use YYYY-MM-DD."}

        working_days = [d.strip() for d in (doctor.working_days or "").split(",")]
        if day_name not in working_days:
            return {
                "success": False,
                "error": f"Dr. {doctor.name} does not work on {day_name}s. "
                         f"Available days: {', '.join(working_days)}."
            }

        # --- Schedule revalidation: working hours ---
        try:
            req_time = datetime.strptime(appointment_time, "%H:%M").time()
            start_time = datetime.strptime(doctor.start_time, "%H:%M").time()
            end_time = datetime.strptime(doctor.end_time, "%H:%M").time()
        except (ValueError, TypeError):
            return {"success": False, "error": "Invalid time format. Use HH:MM."}

        if not (start_time <= req_time < end_time):
            return {
                "success": False,
                "error": (
                    f"Requested time {appointment_time} is outside Dr. {doctor.name}'s "
                    f"working hours ({doctor.start_time}–{doctor.end_time})."
                )
            }

        # --- Duration-aware overlap conflict check ---
        req_start_m = req_time.hour * 60 + req_time.minute
        req_end_m = req_start_m + service.duration

        booked_appts = Appointment.query.filter_by(
            business_id=business_id,
            doctor_id=doctor_id,
            appointment_date=appointment_date,
            status="CONFIRMED"
        ).all()

        for existing_appt in booked_appts:
            try:
                ex_h, ex_m = map(int, existing_appt.appointment_time.split(":"))
                ex_start_m = ex_h * 60 + ex_m
                ex_svc_dur = existing_appt.service.duration if existing_appt.service else 30
                ex_end_m = ex_start_m + ex_svc_dur
                if req_start_m < ex_end_m and req_end_m > ex_start_m:
                    return {
                        "success": False,
                        "error": (
                            f"The requested slot at {appointment_time} overlaps an existing "
                            f"{ex_svc_dur}-minute appointment at {existing_appt.appointment_time}. "
                            "Please choose a different time."
                        )
                    }
            except Exception:
                pass

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
                # Update name if it has changed
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
                    f"{appointment_date} at {appointment_time} with {doctor.name}."
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

        # Cancel associated scheduled reminders
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

        # Duration-aware overlap check for the new slot (excluding the appointment being rescheduled)
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

            # Reschedule reminder
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
