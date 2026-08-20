from datetime import datetime, timedelta, time
from typing import Dict, List, Optional, Tuple, Any
from sqlalchemy.exc import IntegrityError
from models import db, Business, Doctor, Service, Customer, Appointment
from services.reminder_service import ReminderService

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
    def check_availability(business_id: int, doctor_id: Optional[int] = None, service_id: Optional[int] = None, date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculate available time slots for a given doctor (or all doctors) on a specific date.
        Format of date_str: 'YYYY-MM-DD'
        """
        if not date_str:
            return {"error": "Date is required in YYYY-MM-DD format"}

        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"error": "Invalid date format. Please use YYYY-MM-DD"}

        # Validate date is not in the past
        today = datetime.now().date()
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

            # Get booked appointments for this doctor on target_date
            booked_appts = Appointment.query.filter_by(
                business_id=business_id,
                doctor_id=doc.id,
                appointment_date=date_str,
                status="CONFIRMED"
            ).all()
            booked_times = {a.appointment_time for a in booked_appts}

            # Generate slots (using 30-minute intervals)
            slots = []
            curr = datetime.combine(target_date, time(start_h, start_m))
            end_time_dt = datetime.combine(target_date, time(end_h, end_m))

            while curr + timedelta(minutes=duration) <= end_time_dt:
                slot_time_str = curr.strftime("%H:%M")
                if slot_time_str not in booked_times:
                    slots.append(slot_time_str)
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
        Atomic appointment booking transaction with customer deduplication,
        double-booking conflict checks, database constraints, and automatic reminder scheduling.
        """
        # Validate inputs
        if not customer_name or not customer_name.strip():
            return {"success": False, "error": "Customer name is required."}
        if not customer_phone or not customer_phone.strip():
            return {"success": False, "error": "Customer phone number is required."}
        if not doctor_id or not service_id or not appointment_date or not appointment_time:
            return {"success": False, "error": "Doctor, service, date, and time are required."}

        # Check idempotency
        if idempotency_key:
            existing = Appointment.query.filter_by(idempotency_key=idempotency_key).first()
            if existing:
                return {
                    "success": True,
                    "is_duplicate_request": True,
                    "appointment": existing.to_dict(),
                    "message": "Appointment already processed successfully."
                }

        # Validate Doctor
        doctor = Doctor.query.filter_by(id=doctor_id, business_id=business_id).first()
        if not doctor:
            return {"success": False, "error": f"Doctor with ID {doctor_id} not found."}

        # Validate Service
        service = Service.query.filter_by(id=service_id, business_id=business_id).first()
        if not service:
            return {"success": False, "error": f"Service with ID {service_id} not found."}

        try:
            # Check if customer exists or create new
            customer = Customer.query.filter_by(business_id=business_id, phone=customer_phone.strip()).first()
            if not customer:
                customer = Customer(
                    business_id=business_id,
                    name=customer_name.strip(),
                    phone=customer_phone.strip()
                )
                db.session.add(customer)
                db.session.flush()
            else:
                # Update name if provided and not empty
                if customer_name.strip() and customer.name != customer_name.strip():
                    customer.name = customer_name.strip()
                    db.session.flush()

            # Pre-check slot availability in current transaction
            conflict = Appointment.query.filter_by(
                business_id=business_id,
                doctor_id=doctor_id,
                appointment_date=appointment_date,
                appointment_time=appointment_time,
                status="CONFIRMED"
            ).first()

            if conflict:
                return {
                    "success": False,
                    "error": f"The slot at {appointment_time} on {appointment_date} with {doctor.name} was just booked. Please select another time."
                }

            # Create appointment
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

            # Schedule automated reminder
            ReminderService.schedule_for_appointment(appointment)

            db.session.commit()

            return {
                "success": True,
                "appointment_id": appointment.id,
                "appointment": appointment.to_dict(),
                "message": f"Appointment successfully confirmed for {customer.name} on {appointment_date} at {appointment_time} with {doctor.name}."
            }

        except IntegrityError:
            db.session.rollback()
            return {
                "success": False,
                "error": f"The slot at {appointment_time} on {appointment_date} with {doctor.name} is already booked. Please choose a different slot."
            }
        except Exception as e:
            db.session.rollback()
            return {
                "success": False,
                "error": f"An unexpected error occurred during booking: {str(e)}"
            }

    @staticmethod
    def cancel_appointment(business_id: int, appointment_id: int, reason: Optional[str] = None) -> Dict[str, Any]:
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
            "message": f"Appointment #{appt.id} for {appt.customer.name} on {appt.appointment_date} at {appt.appointment_time} has been cancelled."
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

        # Check if new slot is available
        conflict = Appointment.query.filter_by(
            business_id=business_id,
            doctor_id=appt.doctor_id,
            appointment_date=new_date,
            appointment_time=new_time,
            status="CONFIRMED"
        ).filter(Appointment.id != appointment_id).first()

        if conflict:
            return {
                "success": False,
                "error": f"The slot at {new_time} on {new_date} is already occupied. Please select another slot."
            }

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
                "message": f"Appointment #{appt.id} successfully rescheduled to {new_date} at {new_time} with {appt.doctor.name}."
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
