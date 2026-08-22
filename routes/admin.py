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

from models import DoctorSchedule, DoctorLeave, DAYS_OF_WEEK

# --- Doctor Schedule & Profile Management Routes ---
@admin_bp.route("/admin/doctors")
@login_required
def doctors_view():
    business_id = Config.DEFAULT_BUSINESS_ID
    business = db.session.get(Business, business_id)
    doctors = Doctor.query.filter_by(business_id=business_id).all()
    return render_template("doctors.html", business=business, doctors=doctors, days_of_week=DAYS_OF_WEEK)

@admin_bp.route("/admin/doctors/add", methods=["POST"])
@login_required
def add_doctor():
    business_id = Config.DEFAULT_BUSINESS_ID
    name = request.form.get("name", "").strip()
    specialization = request.form.get("specialization", "").strip()
    start_time_global = request.form.get("start_time", "09:00").strip()
    end_time_global = request.form.get("end_time", "17:00").strip()
    working_days_form = request.form.getlist("working_days")

    try:
        slot_interval = int(request.form.get("slot_interval", "30"))
    except Exception:
        slot_interval = 30
    break_start_time = request.form.get("break_start_time", "").strip() or None
    break_end_time = request.form.get("break_end_time", "").strip() or None

    if name and specialization:
        doctor = Doctor(
            business_id=business_id,
            name=name,
            specialization=specialization,
            working_days="Monday,Tuesday,Wednesday,Thursday,Friday,Saturday",
            start_time=start_time_global,
            end_time=end_time_global,
            slot_interval=slot_interval,
            break_start_time=break_start_time,
            break_end_time=break_end_time,
            is_active=True
        )
        db.session.add(doctor)
        db.session.flush()

        # Save per-day DoctorSchedule entries
        active_days = []
        for day in DAYS_OF_WEEK:
            is_avail = (f"is_available_{day}" in request.form) or (day in working_days_form)
            s_time = request.form.get(f"start_time_{day}", start_time_global).strip()
            e_time = request.form.get(f"end_time_{day}", end_time_global).strip()
            if is_avail:
                active_days.append(day)

            sched = DoctorSchedule(
                doctor_id=doctor.id,
                day_of_week=day,
                is_available=is_avail,
                start_time=s_time,
                end_time=e_time
            )
            db.session.add(sched)

        if active_days:
            doctor.working_days = ",".join(active_days)

        db.session.commit()
        flash(f"Doctor '{name}' added successfully with weekly schedule.", "success")
    else:
        flash("Name and specialization are required.", "danger")

    return redirect(url_for("admin_bp.doctors_view"))

@admin_bp.route("/admin/doctors/edit/<int:doctor_id>", methods=["POST"])
@login_required
def edit_doctor(doctor_id):
    business_id = Config.DEFAULT_BUSINESS_ID
    doctor = Doctor.query.filter_by(id=doctor_id, business_id=business_id).first()
    if not doctor:
        flash("Doctor not found.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    doctor.name = request.form.get("name", doctor.name).strip()
    doctor.specialization = request.form.get("specialization", doctor.specialization).strip()
    if "start_time" in request.form:
        doctor.start_time = request.form.get("start_time").strip()
    if "end_time" in request.form:
        doctor.end_time = request.form.get("end_time").strip()
    try:
        doctor.slot_interval = int(request.form.get("slot_interval", doctor.slot_interval or 30))
    except Exception:
        pass
    doctor.break_start_time = request.form.get("break_start_time", "").strip() or None
    doctor.break_end_time = request.form.get("break_end_time", "").strip() or None
    doctor.is_active = "is_active" in request.form

    working_days_form = request.form.getlist("working_days")

    # Update 7-day weekly schedule
    active_days = []
    for day in DAYS_OF_WEEK:
        is_avail = (f"is_available_{day}" in request.form) or (day in working_days_form)
        s_time = request.form.get(f"start_time_{day}", request.form.get("start_time", doctor.start_time)).strip()
        e_time = request.form.get(f"end_time_{day}", request.form.get("end_time", doctor.end_time)).strip()
        if is_avail:
            active_days.append(day)

        sched = DoctorSchedule.query.filter_by(doctor_id=doctor.id, day_of_week=day).first()
        if not sched:
            sched = DoctorSchedule(doctor_id=doctor.id, day_of_week=day)
            db.session.add(sched)

        sched.is_available = is_avail
        sched.start_time = s_time
        sched.end_time = e_time

    if active_days:
        doctor.working_days = ",".join(active_days)

    db.session.commit()
    flash(f"Weekly schedule & profile for '{doctor.name}' updated successfully.", "success")
    return redirect(url_for("admin_bp.doctors_view"))

@admin_bp.route("/admin/doctors/toggle/<int:doctor_id>", methods=["POST"])
@login_required
def toggle_doctor(doctor_id):
    business_id = Config.DEFAULT_BUSINESS_ID
    doctor = Doctor.query.filter_by(id=doctor_id, business_id=business_id).first()
    if doctor:
        doctor.is_active = not doctor.is_active
        db.session.commit()
        status_text = "activated" if doctor.is_active else "deactivated"
        flash(f"Doctor '{doctor.name}' {status_text}.", "info")
    return redirect(url_for("admin_bp.doctors_view"))

@admin_bp.route("/admin/doctors/leave/add", methods=["POST"])
@login_required
def add_doctor_leave():
    business_id = Config.DEFAULT_BUSINESS_ID
    doctor_id = int(request.form.get("doctor_id", 0))
    doctor = Doctor.query.filter_by(id=doctor_id, business_id=business_id).first()
    if not doctor:
        flash("Invalid doctor selected.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    leave_date = request.form.get("leave_date", "").strip()
    reason = request.form.get("reason", "").strip()
    is_all_day = "is_all_day" in request.form
    start_time = request.form.get("start_time", "").strip() or None
    end_time = request.form.get("end_time", "").strip() or None

    if leave_date:
        leave = DoctorLeave(
            doctor_id=doctor.id,
            leave_date=leave_date,
            is_all_day=is_all_day,
            start_time=start_time if not is_all_day else None,
            end_time=end_time if not is_all_day else None,
            reason=reason or "Leave / Blocked Time"
        )
        db.session.add(leave)
        db.session.commit()
        flash(f"Leave/Blocked date on {leave_date} added for Dr. '{doctor.name}'.", "success")
    else:
        flash("Leave date is required.", "danger")

    return redirect(url_for("admin_bp.doctors_view"))

@admin_bp.route("/admin/doctors/leave/delete/<int:leave_id>", methods=["POST"])
@login_required
def delete_doctor_leave(leave_id):
    leave = db.session.get(DoctorLeave, leave_id)
    if leave:
        db.session.delete(leave)
        db.session.commit()
        flash("Leave entry removed.", "info")
    return redirect(url_for("admin_bp.doctors_view"))

