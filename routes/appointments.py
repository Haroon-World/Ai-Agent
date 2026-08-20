from flask import Blueprint, request, jsonify
from config.config import Config
from services.booking_service import BookingService
from models import Appointment

appointments_bp = Blueprint("appointments_bp", __name__)

@appointments_bp.route("/api/availability", methods=["GET"])
def check_availability():
    date_str = request.args.get("date")
    doctor_id = request.args.get("doctor_id")
    service_id = request.args.get("service_id")
    business_id = Config.DEFAULT_BUSINESS_ID

    if not date_str:
        return jsonify({"success": False, "error": "Query parameter 'date' (YYYY-MM-DD) is required."}), 400

    result = BookingService.check_availability(
        business_id=business_id,
        doctor_id=int(doctor_id) if doctor_id else None,
        service_id=int(service_id) if service_id else None,
        date_str=date_str
    )
    return jsonify(result)

@appointments_bp.route("/api/appointments", methods=["GET"])
def list_appointments():
    business_id = Config.DEFAULT_BUSINESS_ID
    appts = Appointment.query.filter_by(business_id=business_id).order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.asc()).all()
    return jsonify({
        "success": True,
        "count": len(appts),
        "appointments": [a.to_dict() for a in appts]
    })

@appointments_bp.route("/api/appointments/book", methods=["POST"])
def book_appointment():
    data = request.get_json() or {}
    business_id = Config.DEFAULT_BUSINESS_ID

    result = BookingService.book_appointment(
        business_id=business_id,
        customer_name=data.get("customer_name"),
        customer_phone=data.get("customer_phone"),
        doctor_id=data.get("doctor_id"),
        service_id=data.get("service_id"),
        appointment_date=data.get("appointment_date"),
        appointment_time=data.get("appointment_time"),
        notes=data.get("notes"),
        idempotency_key=data.get("idempotency_key")
    )
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code

@appointments_bp.route("/api/appointments/cancel", methods=["POST"])
def cancel_appointment():
    data = request.get_json() or {}
    business_id = Config.DEFAULT_BUSINESS_ID
    appointment_id = data.get("appointment_id")
    reason = data.get("reason")

    if not appointment_id:
        return jsonify({"success": False, "error": "appointment_id is required"}), 400

    result = BookingService.cancel_appointment(
        business_id=business_id,
        appointment_id=int(appointment_id),
        reason=reason
    )
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code

@appointments_bp.route("/api/appointments/reschedule", methods=["POST"])
def reschedule_appointment():
    data = request.get_json() or {}
    business_id = Config.DEFAULT_BUSINESS_ID
    appointment_id = data.get("appointment_id")
    new_date = data.get("new_date")
    new_time = data.get("new_time")

    if not appointment_id or not new_date or not new_time:
        return jsonify({"success": False, "error": "appointment_id, new_date, and new_time are required"}), 400

    result = BookingService.reschedule_appointment(
        business_id=business_id,
        appointment_id=int(appointment_id),
        new_date=new_date,
        new_time=new_time
    )
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code
