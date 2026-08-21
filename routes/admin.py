from datetime import datetime, date
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from config.config import Config
from models import db, Business, Appointment, Conversation, Message, Reminder, Customer, Doctor, Service
from services.handoff_service import HandoffService

admin_bp = Blueprint("admin_bp", __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_bp.login"))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_bp.dashboard"))

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == Config.ADMIN_USERNAME and password == Config.ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            session["admin_user"] = username
            flash("Logged in successfully.", "success")
            return redirect(url_for("admin_bp.dashboard"))
        else:
            flash("Invalid admin username or password.", "danger")

    return render_template("login.html")

@admin_bp.route("/admin/logout")
def logout():
    session.pop("admin_logged_in", None)
    session.pop("admin_user", None)
    flash("You have been logged out.", "info")
    return redirect(url_for("admin_bp.login"))

@admin_bp.route("/admin")
@login_required
def dashboard():
    business_id = Config.DEFAULT_BUSINESS_ID
    business = db.session.get(Business, business_id)

    today_str = date.today().strftime("%Y-%m-%d")

    # Metrics
    today_appointments = Appointment.query.filter_by(
        business_id=business_id,
        appointment_date=today_str,
        status="CONFIRMED"
    ).all()

    upcoming_appointments = Appointment.query.filter(
        Appointment.business_id == business_id,
        Appointment.appointment_date >= today_str,
        Appointment.status == "CONFIRMED"
    ).order_by(Appointment.appointment_date.asc(), Appointment.appointment_time.asc()).limit(8).all()

    total_conversations = Conversation.query.filter_by(business_id=business_id).count()
    human_handoffs = Conversation.query.filter_by(business_id=business_id, status="HUMAN").all()
    scheduled_reminders_count = Reminder.query.filter_by(business_id=business_id, status="SCHEDULED").count()

    return render_template(
        "dashboard.html",
        business=business,
        today_count=len(today_appointments),
        today_appointments=today_appointments,
        upcoming_appointments=upcoming_appointments,
        total_conversations=total_conversations,
        human_handoff_count=len(human_handoffs),
        human_handoffs=human_handoffs,
        reminder_count=scheduled_reminders_count
    )

@admin_bp.route("/admin/appointments")
@login_required
def appointments_view():
    business_id = Config.DEFAULT_BUSINESS_ID
    business = db.session.get(Business, business_id)
    all_appointments = Appointment.query.filter_by(business_id=business_id).order_by(Appointment.appointment_date.desc(), Appointment.appointment_time.asc()).all()
    return render_template("appointments.html", business=business, appointments=all_appointments)

@admin_bp.route("/admin/conversations")
@login_required
def conversations_view():
    business_id = Config.DEFAULT_BUSINESS_ID
    business = db.session.get(Business, business_id)
    all_conversations = Conversation.query.filter_by(business_id=business_id).order_by(Conversation.updated_at.desc()).all()
    return render_template("conversations.html", business=business, conversations=all_conversations)

@admin_bp.route("/admin/reminders")
@login_required
def reminders_view():
    business_id = Config.DEFAULT_BUSINESS_ID
    business = db.session.get(Business, business_id)
    all_reminders = Reminder.query.filter_by(business_id=business_id).order_by(Reminder.scheduled_for.desc()).all()
    return render_template("reminders.html", business=business, reminders=all_reminders)

# Admin API actions
@admin_bp.route("/api/admin/takeover", methods=["POST"])
@login_required
def takeover_conversation():
    data = request.get_json() or {}
    conversation_id = data.get("conversation_id")
    reason = data.get("reason", "Admin manually took over the conversation")
    business_id = Config.DEFAULT_BUSINESS_ID

    if not conversation_id:
        return jsonify({"success": False, "error": "conversation_id is required"}), 400

    result = HandoffService.trigger_handoff(
        conversation_id=int(conversation_id),
        reason=reason,
        business_id=business_id
    )
    status_code = 403 if result.get("code") == 403 else 200
    return jsonify(result), status_code

@admin_bp.route("/api/admin/release", methods=["POST"])
@login_required
def release_to_ai():
    data = request.get_json() or {}
    conversation_id = data.get("conversation_id")
    business_id = Config.DEFAULT_BUSINESS_ID

    if not conversation_id:
        return jsonify({"success": False, "error": "conversation_id is required"}), 400

    result = HandoffService.release_to_ai(
        conversation_id=int(conversation_id),
        business_id=business_id
    )
    status_code = 403 if result.get("code") == 403 else 200
    return jsonify(result), status_code

@admin_bp.route("/api/admin/reply", methods=["POST"])
@login_required
def staff_reply():
    data = request.get_json() or {}
    conversation_id = data.get("conversation_id")
    message = data.get("message", "").strip()
    business_id = Config.DEFAULT_BUSINESS_ID

    if not conversation_id or not message:
        return jsonify({"success": False, "error": "conversation_id and message are required"}), 400

    result = HandoffService.admin_reply(
        conversation_id=int(conversation_id),
        message_content=message,
        business_id=business_id
    )
    status_code = 403 if result.get("code") == 403 else 200
    return jsonify(result), status_code
