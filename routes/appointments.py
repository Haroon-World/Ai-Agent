from flask import Blueprint, request, jsonify
from config.config import Config
from services.booking_service import BookingService

appointments_bp = Blueprint("appointments_bp", __name__)

@appointments_bp.route("/api/availability", methods=["GET"])
def check_availability():
    """
    Public read-only availability endpoint for clinic calendar and availability checking.
    Booking mutations must occur through the AI Agent's controlled tool execution path.
    """
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
