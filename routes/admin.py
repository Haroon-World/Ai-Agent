from datetime import datetime, date
from functools import wraps
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, jsonify, abort
)
from config.config import Config
from models import db, Business, Appointment, Conversation, Message, Reminder, Customer, Doctor, Service
from models.user import User
from services.handoff_service import HandoffService
from services.subscription_service import SubscriptionService

admin_bp = Blueprint("admin_bp", __name__)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def login_required(f):
    """Redirect to login if not authenticated, missing clinic context, or expired subscription."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("admin_bp.login"))
        if not session.get("business_id"):
            if session.get("is_platform_admin"):
                return redirect(url_for("platform_bp.dashboard"))
            return redirect(url_for("admin_bp.login"))

        # Subscription enforcement for clinic staff accounts
        exempt_endpoints = [
            "admin_bp.subscription_expired",
            "admin_bp.subscription_view",
            "admin_bp.renew_subscription",
            "admin_bp.logout"
        ]
        if not session.get("is_platform_admin") and request.endpoint not in exempt_endpoints:
            from services.subscription_service import SubscriptionService
            if not SubscriptionService.check_access(session.get("business_id")):
                return redirect(url_for("admin_bp.subscription_expired"))

        return f(*args, **kwargs)
    return decorated_function


def platform_admin_required(f):
    """Allow only verified platform admins; resilient against multi-tab clinic switching."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Check if platform_admin_id exists in session and verify in DB
        p_id = session.get("platform_admin_id")
        if p_id:
            p_user = db.session.get(User, p_id)
            if p_user and p_user.is_platform_admin:
                session["is_platform_admin"] = True
                return f(*args, **kwargs)

        # 2. Check if user_id in session is a verified platform admin in DB
        u_id = session.get("user_id")
        if u_id:
            u_user = db.session.get(User, u_id)
            if u_user and u_user.is_platform_admin:
                session["is_platform_admin"] = True
                session["platform_admin_id"] = u_user.id
                session["platform_admin_user"] = u_user.username
                return f(*args, **kwargs)

        if not session.get("user_id") and not session.get("platform_admin_id"):
            return redirect(url_for("platform_bp.login"))

        abort(403)
    return decorated_function


def _current_business_id() -> int:
    """Return the business_id of the currently logged-in admin from the session."""
    bid = session.get("business_id")
    if bid is not None:
        return bid
    # Fallback when platform admin inspects /admin pages directly
    clinic_param = request.args.get("clinic") or request.args.get("clinic_id") or session.get("active_clinic_id")
    if clinic_param and str(clinic_param).isdigit():
        return int(clinic_param)
    first_b = Business.query.order_by(Business.id.asc()).first()
    return first_b.id if first_b else 1


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/login", methods=["GET", "POST"])
def login():
    clinic_param = (
        request.form.get("clinic") or
        request.form.get("clinic_id") or
        request.args.get("clinic") or
        request.args.get("clinic_id") or
        ""
    ).strip()

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        matched_user = None

        if clinic_param:
            target_business = None
            if clinic_param.isdigit():
                target_business = db.session.get(Business, int(clinic_param))
            else:
                target_business = Business.query.filter_by(name=clinic_param).first()

            if not target_business:
                flash(f"Clinic '{clinic_param}' not found.", "danger")
                return render_template(
                    "login.html",
                    already_logged_in=bool(session.get("user_id")),
                    current_user=session.get("admin_user", "admin"),
                    require_clinic_id=True,
                    clinic_param=clinic_param
                )

            candidate = User.query.filter_by(
                username=username,
                business_id=target_business.id,
                is_platform_admin=False
            ).first()
            if candidate and candidate.check_password(password):
                matched_user = candidate
            else:
                flash("Invalid username or password for this clinic.", "danger")
                return render_template(
                    "login.html",
                    already_logged_in=bool(session.get("user_id")),
                    current_user=session.get("admin_user", "admin"),
                    require_clinic_id=True,
                    clinic_param=clinic_param
                )
        else:
            candidates = User.query.filter_by(username=username).all()
            clinic_candidates = [c for c in candidates if not c.is_platform_admin]
            platform_candidates = [c for c in candidates if c.is_platform_admin]

            # Check if this username belongs to platform admin
            for pc in platform_candidates:
                if pc.check_password(password):
                    flash("Unauthorized: Platform Owner accounts cannot sign in through the Clinic Client Portal. Please use the dedicated Platform Master Login at /platform/login.", "warning")
                    return render_template(
                        "login.html",
                        already_logged_in=bool(session.get("user_id")),
                        current_user=session.get("admin_user", "admin")
                    )

            if len(clinic_candidates) == 0:
                flash("Invalid username or password.", "danger")
            elif len(clinic_candidates) == 1:
                if clinic_candidates[0].check_password(password):
                    matched_user = clinic_candidates[0]
                else:
                    flash("Invalid username or password.", "danger")
            else:
                # Multiple clinics share this username — refuse to guess
                flash(f"Multiple clinics found with username '{username}'. Please specify your Clinic ID to sign in.", "warning")
                return render_template(
                    "login.html",
                    already_logged_in=bool(session.get("user_id")),
                    current_user=session.get("admin_user", "admin"),
                    username=username,
                    require_clinic_id=True
                )

        if matched_user:
            # Block platform owners from using the clinic client login portal
            if matched_user.is_platform_admin:
                flash("Unauthorized: Platform Owner accounts cannot sign in through the Clinic Client Portal. Please use the dedicated Platform Master Login at /platform/login.", "warning")
                return render_template(
                    "login.html",
                    already_logged_in=bool(session.get("user_id")),
                    current_user=session.get("admin_user", "admin")
                )

            # Preserve active platform admin session if logged in concurrently
            p_admin_id = session.get("platform_admin_id")
            p_admin_user = session.get("platform_admin_user")

            session.clear()
            session.permanent = True

            if p_admin_id:
                session["platform_admin_id"] = p_admin_id
                session["platform_admin_user"] = p_admin_user
                session["is_platform_admin"] = True

            session["user_id"] = matched_user.id
            session["business_id"] = matched_user.business_id
            session["admin_user"] = matched_user.username
            if not p_admin_id:
                session["is_platform_admin"] = False

            business = db.session.get(Business, matched_user.business_id)
            session["clinic_name"] = business.name if business else "Clinic"
            
            # Check subscription access upon sign in
            if business and not business.is_subscription_valid:
                flash(f"Subscription for {session['clinic_name']} has expired. Please renew to resume operations.", "warning")
                return redirect(url_for("admin_bp.subscription_expired"))

            flash(f"Logged in to {session['clinic_name']} Admin Portal.", "success")
            return redirect(url_for("admin_bp.dashboard"))

    already_logged_in = bool(session.get("user_id")) and not bool(session.get("is_platform_admin"))
    return render_template(
        "login.html",
        already_logged_in=already_logged_in,
        current_user=session.get("admin_user", "admin"),
        clinic_param=clinic_param
    )


@admin_bp.route("/admin/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("admin_bp.login"))


@admin_bp.route("/admin/switch-clinic/<int:clinic_id>")
def switch_clinic(clinic_id):
    """Switch active clinic context for authorized admin or platform owner."""
    clinic = db.session.get(Business, clinic_id)
    if not clinic:
        flash("Clinic not found.", "danger")
        return redirect(request.referrer or url_for("admin_bp.dashboard"))

    # Platform owners can switch to any clinic freely
    if session.get("is_platform_admin"):
        session["business_id"] = clinic.id
        session["clinic_name"] = clinic.name
        session["active_clinic_id"] = clinic.id
        flash(f"Switched to {clinic.name}.", "info")
        return redirect(request.referrer or url_for("admin_bp.dashboard"))

    # Check if currently logged in user belongs to this clinic
    if session.get("user_id"):
        user = db.session.get(User, session.get("user_id"))
        if user and user.business_id == clinic.id:
            session["business_id"] = clinic.id
            session["clinic_name"] = clinic.name
            session["active_clinic_id"] = clinic.id
            flash(f"Switched to {clinic.name}.", "info")
            return redirect(request.referrer or url_for("admin_bp.dashboard"))

    # For visitors / customer sessions, update active clinic and view chat
    session["active_clinic_id"] = clinic.id
    return redirect(url_for("chat_bp.chat_view", clinic_id=clinic.id))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@admin_bp.route("/admin")
@login_required
def dashboard():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)

    today_str = date.today().strftime("%Y-%m-%d")

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

    sub_info = SubscriptionService.get_subscription_info(business_id)
    rejection_notice = None
    if sub_info and sub_info.get("latest_rejected_request"):
        rej = sub_info["latest_rejected_request"]
        if not session.get(f"dismissed_rejection_{rej['id']}"):
            rejection_notice = rej

    return render_template(
        "dashboard.html",
        business=business,
        today_count=len(today_appointments),
        today_appointments=today_appointments,
        upcoming_appointments=upcoming_appointments,
        total_conversations=total_conversations,
        human_handoff_count=len(human_handoffs),
        human_handoffs=human_handoffs,
        reminder_count=scheduled_reminders_count,
        sub_info=sub_info,
        rejection_notice=rejection_notice
    )


# ---------------------------------------------------------------------------
# Appointments / Conversations / Reminders views
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/appointments")
@login_required
def appointments_view():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    all_appointments = Appointment.query.filter_by(business_id=business_id).order_by(
        Appointment.appointment_date.desc(), Appointment.appointment_time.asc()
    ).all()
    return render_template("appointments.html", business=business, appointments=all_appointments)


@admin_bp.route("/admin/conversations")
@login_required
def conversations_view():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    all_conversations = Conversation.query.filter_by(business_id=business_id).order_by(
        Conversation.updated_at.desc()
    ).all()
    return render_template("conversations.html", business=business, conversations=all_conversations)


@admin_bp.route("/admin/reminders")
@login_required
def reminders_view():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    all_reminders = Reminder.query.filter_by(business_id=business_id).order_by(
        Reminder.scheduled_for.desc()
    ).all()
    return render_template("reminders.html", business=business, reminders=all_reminders)


# ---------------------------------------------------------------------------
# Admin API actions
# ---------------------------------------------------------------------------

@admin_bp.route("/api/admin/takeover", methods=["POST"])
@login_required
def takeover_conversation():
    data = request.get_json() or {}
    conversation_id = data.get("conversation_id")
    reason = data.get("reason", "Admin manually took over the conversation")
    business_id = _current_business_id()

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
    business_id = _current_business_id()

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
    business_id = _current_business_id()

    if not conversation_id or not message:
        return jsonify({"success": False, "error": "conversation_id and message are required"}), 400

    result = HandoffService.admin_reply(
        conversation_id=int(conversation_id),
        message_content=message,
        business_id=business_id
    )
    status_code = 403 if result.get("code") == 403 else 200
    return jsonify(result), status_code


from services.booking_service import BookingService

@admin_bp.route("/api/admin/appointments/cancel", methods=["POST"])
@admin_bp.route("/api/appointments/cancel", methods=["POST"])
@login_required
def admin_cancel_appointment():
    data = request.get_json() or {}
    appointment_id = data.get("appointment_id")
    reason = data.get("reason", "Cancelled by Admin Staff")
    business_id = _current_business_id()

    if not appointment_id:
        return jsonify({"success": False, "error": "appointment_id is required"}), 400

    try:
        appt_id_int = int(appointment_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid appointment_id format."}), 400

    result = BookingService.cancel_appointment(
        business_id=business_id,
        appointment_id=appt_id_int,
        reason=reason
    )
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


from models import DoctorSchedule, DoctorLeave, DAYS_OF_WEEK

# ---------------------------------------------------------------------------
# Doctor Schedule & Profile Management Routes
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/doctors")
@login_required
def doctors_view():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    doctors = Doctor.query.filter_by(business_id=business_id).all()
    return render_template("doctors.html", business=business, doctors=doctors, days_of_week=DAYS_OF_WEEK)


@admin_bp.route("/admin/doctors/add", methods=["POST"])
@login_required
def add_doctor():
    business_id = _current_business_id()
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

    if break_start_time and break_end_time and break_start_time >= break_end_time:
        flash("Lunch break start time must be before end time.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    if start_time_global and end_time_global and start_time_global >= end_time_global:
        flash("Global end time must be after start time.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

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

        active_days = []
        for day in DAYS_OF_WEEK:
            is_avail = (f"is_available_{day}" in request.form) or (day in working_days_form)
            s_time = request.form.get(f"start_time_{day}", start_time_global).strip()
            e_time = request.form.get(f"end_time_{day}", end_time_global).strip()
            if s_time >= e_time and is_avail:
                flash(f"End time for {day} must be after start time.", "danger")
                db.session.rollback()
                return redirect(url_for("admin_bp.doctors_view"))

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
    business_id = _current_business_id()
    doctor = Doctor.query.filter_by(id=doctor_id, business_id=business_id).first()
    if not doctor:
        flash("Doctor not found.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    b_start = request.form.get("break_start_time", "").strip() or None
    b_end = request.form.get("break_end_time", "").strip() or None
    if b_start and b_end and b_start >= b_end:
        flash("Lunch break start time must be before end time.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    doctor.name = request.form.get("name", doctor.name).strip()
    doctor.specialization = request.form.get("specialization", doctor.specialization).strip()
    if "start_time" in request.form:
        doctor.start_time = request.form.get("start_time").strip()
    if "end_time" in request.form:
        doctor.end_time = request.form.get("end_time").strip()

    if doctor.start_time and doctor.end_time and doctor.start_time >= doctor.end_time:
        flash("Global end time must be after start time.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    try:
        doctor.slot_interval = int(request.form.get("slot_interval", doctor.slot_interval or 30))
    except Exception:
        pass
    doctor.break_start_time = b_start
    doctor.break_end_time = b_end
    doctor.is_active = "is_active" in request.form

    working_days_form = request.form.getlist("working_days")

    active_days = []
    for day in DAYS_OF_WEEK:
        is_avail = (f"is_available_{day}" in request.form) or (day in working_days_form)
        s_time = request.form.get(f"start_time_{day}", request.form.get("start_time", doctor.start_time)).strip()
        e_time = request.form.get(f"end_time_{day}", request.form.get("end_time", doctor.end_time)).strip()

        if is_avail and s_time >= e_time:
            flash(f"End time for {day} must be after start time.", "danger")
            return redirect(url_for("admin_bp.doctors_view"))

        if is_avail:
            active_days.append(day)

        sched = DoctorSchedule.query.filter_by(doctor_id=doctor.id, day_of_week=day).first()
        if not sched:
            sched = DoctorSchedule(doctor_id=doctor.id, day_of_week=day)
            db.session.add(sched)

        sched.is_available = is_avail
        sched.start_time = s_time
        sched.end_time = e_time

    doctor.working_days = ",".join(active_days)

    db.session.commit()
    from services.booking_service import RequestCache
    RequestCache.clear()
    flash(f"Weekly schedule & profile for '{doctor.name}' updated successfully.", "success")
    return redirect(url_for("admin_bp.doctors_view"))


@admin_bp.route("/admin/doctors/toggle/<int:doctor_id>", methods=["POST"])
@login_required
def toggle_doctor(doctor_id):
    business_id = _current_business_id()
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
    business_id = _current_business_id()
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

    if not leave_date:
        flash("Leave date is required.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    try:
        datetime.strptime(leave_date, "%Y-%m-%d")
    except ValueError:
        flash("Invalid leave date format. Use YYYY-MM-DD.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

    if not is_all_day and start_time and end_time and start_time >= end_time:
        flash("Partial day leave start time must be before end time.", "danger")
        return redirect(url_for("admin_bp.doctors_view"))

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
    return redirect(url_for("admin_bp.doctors_view"))


@admin_bp.route("/admin/doctors/leave/delete/<int:leave_id>", methods=["POST"])
@login_required
def delete_doctor_leave(leave_id):
    leave = db.session.get(DoctorLeave, leave_id)
    if not leave:
        flash("Leave entry not found.", "warning")
        return redirect(url_for("admin_bp.doctors_view"))

    business_id = _current_business_id()
    if not leave.doctor or leave.doctor.business_id != business_id:
        if not session.get("is_platform_admin"):
            abort(403)

    db.session.delete(leave)
    db.session.commit()
    flash("Leave entry removed.", "info")
    return redirect(url_for("admin_bp.doctors_view"))


# ---------------------------------------------------------------------------
# Services & Pricing Management Routes
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/services")
@login_required
def services_view():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    doctors = Doctor.query.filter_by(business_id=business_id).all()
    services = Service.query.filter_by(business_id=business_id).order_by(Service.id.asc()).all()
    consultation_fee = getattr(business, "consultation_fee", 2000.0) or 2000.0

    doctors_data = []
    for doc in doctors:
        doc_svcs = [s for s in services if s.doctor_id == doc.id]
        has_consultation = any(
            ("consultation" in s.name.lower() or "checkup" in s.name.lower() or "check up" in s.name.lower())
            for s in doc_svcs if s.is_active
        )
        doctors_data.append({
            "doctor": doc,
            "services": doc_svcs,
            "active_count": len([s for s in doc_svcs if s.is_active]),
            "has_consultation": has_consultation,
            "default_consultation_fee": consultation_fee
        })

    return render_template(
        "services.html",
        business=business,
        services=services,
        doctors=doctors,
        doctors_data=doctors_data,
        default_consultation_fee=consultation_fee
    )


@admin_bp.route("/admin/services/add", methods=["POST"])
@login_required
def add_service():
    business_id = _current_business_id()
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    try:
        doctor_id = int(request.form.get("doctor_id", "1"))
    except Exception:
        doctor_id = 1
    try:
        duration = int(request.form.get("duration", "30"))
    except Exception:
        duration = 30
    try:
        price = float(request.form.get("price", "2000"))
    except Exception:
        price = 2000.0
    is_active = "is_active" in request.form

    if not name:
        flash("Service name is required.", "danger")
        return redirect(url_for("admin_bp.services_view"))

    # Verify doctor belongs to this clinic
    doctor = Doctor.query.filter_by(id=doctor_id, business_id=business_id).first()
    if not doctor:
        flash("Selected doctor does not belong to your clinic.", "danger")
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": "Selected doctor does not belong to your clinic."}), 403
        return redirect(url_for("admin_bp.services_view"))

    service = Service(
        business_id=business_id,
        doctor_id=doctor.id,
        name=name,
        description=description or None,
        duration=duration,
        price=price,
        is_active=is_active
    )
    db.session.add(service)
    db.session.commit()
    from services.booking_service import RequestCache
    RequestCache.clear()
    flash(f"Service '{name}' added successfully at PKR {price:,.0f}.", "success")
    return redirect(url_for("admin_bp.services_view"))


@admin_bp.route("/admin/services/edit/<int:service_id>", methods=["POST"])
@login_required
def edit_service(service_id):
    business_id = _current_business_id()
    service = Service.query.filter_by(id=service_id, business_id=business_id).first()
    if not service:
        flash("Service not found.", "danger")
        return redirect(url_for("admin_bp.services_view"))

    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    try:
        duration = int(request.form.get("duration", str(service.duration)))
    except Exception:
        duration = service.duration
    try:
        price = float(request.form.get("price", str(service.price)))
    except Exception:
        price = service.price
    is_active = "is_active" in request.form

    if not name:
        flash("Service name is required.", "danger")
        return redirect(url_for("admin_bp.services_view"))

    service.name = name
    service.description = description or None
    service.duration = duration
    service.price = price
    service.is_active = is_active

    db.session.commit()
    from services.booking_service import RequestCache
    RequestCache.clear()
    flash(f"Service '{name}' pricing and settings updated successfully.", "success")
    return redirect(url_for("admin_bp.services_view"))


@admin_bp.route("/admin/services/toggle/<int:service_id>", methods=["POST"])
@login_required
def toggle_service(service_id):
    business_id = _current_business_id()
    service = Service.query.filter_by(id=service_id, business_id=business_id).first()
    if not service:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": "Service not found."}), 404
        flash("Service not found.", "danger")
        return redirect(url_for("admin_bp.services_view"))

    service.is_active = not service.is_active
    db.session.commit()
    from services.booking_service import RequestCache
    RequestCache.clear()
    status_text = "activated" if service.is_active else "deactivated"

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "service_id": service.id,
            "is_active": service.is_active,
            "status_text": "ACTIVE" if service.is_active else "INACTIVE",
            "message": f"Service '{service.name}' {status_text}."
        })

    flash(f"Service '{service.name}' {status_text}.", "info")
    return redirect(url_for("admin_bp.services_view"))


@admin_bp.route("/admin/services/delete/<int:service_id>", methods=["POST"])
@login_required
def delete_service(service_id):
    business_id = _current_business_id()
    service = Service.query.filter_by(id=service_id, business_id=business_id).first()
    if not service:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "error": "Service not found."}), 404
        flash("Service not found.", "danger")
        return redirect(url_for("admin_bp.services_view"))

    name = service.name
    has_appts = Appointment.query.filter_by(service_id=service.id).first()
    if has_appts:
        service.is_active = False
        db.session.commit()
        msg = f"Service '{name}' has existing appointment records, so it was deactivated instead of permanently deleted."
    else:
        db.session.delete(service)
        db.session.commit()
        msg = f"Service '{name}' deleted successfully."

    from services.booking_service import RequestCache
    RequestCache.clear()

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "service_id": service_id, "message": msg})

    flash(msg, "info")
    return redirect(url_for("admin_bp.services_view"))


@admin_bp.route("/admin/settings/edit", methods=["POST"])
@login_required
def edit_settings():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    if not business:
        flash("Business record not found.", "danger")
        return redirect(url_for("admin_bp.services_view"))

    business.name = request.form.get("name", business.name).strip()
    business.phone = request.form.get("phone", business.phone).strip()
    business.address = request.form.get("address", business.address).strip()
    business.opening_hours = request.form.get("opening_hours", business.opening_hours).strip()
    business.policies = request.form.get("policies", business.policies).strip()

    try:
        consultation_fee = float(request.form.get("consultation_fee", "2000"))
        business.consultation_fee = consultation_fee
    except Exception:
        pass

    db.session.commit()
    # Update the cached clinic name in the session to reflect the rename
    session["clinic_name"] = business.name
    from services.booking_service import RequestCache
    RequestCache.clear()
    flash("Clinic business information and consultation fee updated successfully.", "success")
    return redirect(url_for("admin_bp.services_view"))


# ---------------------------------------------------------------------------
# Slot Occupancy Dashboard & Manual Booking Routes
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/slots")
@login_required
def slots_view():
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    today_str = date.today().strftime("%Y-%m-%d")
    selected_date = request.args.get("date", today_str).strip()
    selected_doctor_id = request.args.get("doctor_id", "").strip()

    all_active_doctors = Doctor.query.filter_by(business_id=business_id, is_active=True).all()
    all_active_services = Service.query.filter_by(business_id=business_id, is_active=True).all()

    doctors_to_show = all_active_doctors
    if selected_doctor_id and selected_doctor_id.isdigit():
        filtered = [d for d in all_active_doctors if d.id == int(selected_doctor_id)]
        if filtered:
            doctors_to_show = filtered

    occupancy_data = []
    for doc in doctors_to_show:
        avail_res = BookingService.check_availability(
            business_id=business_id,
            doctor_id=doc.id,
            date_str=selected_date
        )
        avail_slots = set(avail_res.get("available_slots", []))

        booked_appts = Appointment.query.filter_by(
            business_id=business_id,
            doctor_id=doc.id,
            appointment_date=selected_date,
            status="CONFIRMED"
        ).order_by(Appointment.appointment_time.asc()).all()

        booked_dict = {a.appointment_time: a for a in booked_appts}
        all_slots = sorted(list(avail_slots.union(set(booked_dict.keys()))))
        total_slots = len(all_slots)
        occupied_count = len(booked_dict)
        remaining_count = len(avail_slots)
        occupancy_percent = int((occupied_count / total_slots * 100)) if total_slots > 0 else 0

        slots_detail = []
        for s in all_slots:
            if s in booked_dict:
                appt = booked_dict[s]
                slots_detail.append({
                    "time": s,
                    "status": "OCCUPIED",
                    "patient_name": appt.customer.name if appt.customer else "Patient",
                    "patient_phone": appt.customer.phone if appt.customer else "N/A",
                    "service_name": appt.service.name if appt.service else "Consultation",
                    "appointment_id": appt.id
                })
            else:
                slots_detail.append({"time": s, "status": "AVAILABLE"})

        occupancy_data.append({
            "doctor": doc,
            "total_slots": total_slots,
            "occupied_slots": occupied_count,
            "remaining_slots": remaining_count,
            "occupancy_percent": occupancy_percent,
            "is_closed": len(all_slots) == 0,
            "slots_detail": slots_detail
        })

    return render_template(
        "slots.html",
        business=business,
        selected_date=selected_date,
        selected_doctor_id=int(selected_doctor_id) if selected_doctor_id and selected_doctor_id.isdigit() else None,
        doctors=all_active_doctors,
        services=all_active_services,
        occupancy_data=occupancy_data
    )


@admin_bp.route("/api/admin/appointments/manual-book", methods=["POST"])
@login_required
def admin_manual_book():
    data = request.get_json() or {}
    business_id = _current_business_id()
    customer_name = data.get("customer_name", "").strip()
    customer_phone = data.get("customer_phone", "").strip()
    doctor_id = data.get("doctor_id")
    service_id = data.get("service_id")
    appointment_date = data.get("appointment_date", "").strip()
    appointment_time = data.get("appointment_time", "").strip()
    notes = data.get("notes", "Booked manually by Staff")

    try:
        doctor_id_int = int(doctor_id)
        service_id_int = int(service_id)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Valid Doctor and Service must be selected."}), 400

    result = BookingService.book_appointment(
        business_id=business_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        doctor_id=doctor_id_int,
        service_id=service_id_int,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        notes=notes
    )
    status_code = 200 if result.get("success") else 400
    return jsonify(result), status_code


# ---------------------------------------------------------------------------
# Platform Owner — Onboard New Clinic
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/platform/onboard-clinic", methods=["GET", "POST"])
@platform_admin_required
def onboard_clinic():
    """Forward legacy URL directly to the dedicated Platform Console handler."""
    from routes.platform import onboard_clinic_view
    return onboard_clinic_view()


# ---------------------------------------------------------------------------
# Password Reset Routes for Clinic Admins (Clients)
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Allow clinic owners/staff to initiate a secure password reset."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        if not username:
            flash("Please enter your admin username.", "warning")
            return render_template("forgot_password.html")

        # Find user account (clinic staff only)
        user = User.query.filter_by(username=username, is_platform_admin=False).first()
        if user:
            user.generate_reset_token(expires_in_hours=1)
            db.session.commit()

        # Always return generic success notice to prevent username enumeration and never expose token
        flash("If an account exists for that username, password reset instructions have been dispatched.", "info")
        return redirect(url_for("admin_bp.login"))

    return render_template("forgot_password.html")


@admin_bp.route("/admin/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Validate token and allow setting a new password."""
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.verify_reset_token(token):
        flash("The password reset link is invalid or has expired. Please request a new one.", "danger")
        return redirect(url_for("admin_bp.forgot_password"))

    if request.method == "POST":
        password = (request.form.get("password") or "").strip()
        confirm_password = (request.form.get("confirm_password") or "").strip()

        if len(password) < 6:
            flash("New password must be at least 6 characters long.", "danger")
            return render_template("reset_password.html", token=token, user=user)

        if password != confirm_password:
            flash("Passwords do not match. Please re-enter.", "danger")
            return render_template("reset_password.html", token=token, user=user)

        user.set_password(password)
        user.clear_reset_token()
        db.session.commit()
        flash("Your password has been successfully reset! You may now sign in.", "success")
        return redirect(url_for("admin_bp.login"))

    return render_template("reset_password.html", token=token, user=user)


# ---------------------------------------------------------------------------
# Subscription Management Views for Clinic Owners (Clients)
# ---------------------------------------------------------------------------

@admin_bp.route("/admin/subscription")
@login_required
def subscription_view():
    """Display the clinic's active subscription status, trial details, and renewal options."""
    business_id = _current_business_id()
    business = db.session.get(Business, business_id)
    if not business:
        flash("Clinic record not found.", "danger")
        return redirect(url_for("admin_bp.dashboard"))

    from services.subscription_service import SubscriptionService
    sub_info = SubscriptionService.get_subscription_info(business_id)

    return render_template("subscription.html", business=business, sub_info=sub_info)


@admin_bp.route("/admin/subscription/renew", methods=["POST"])
@login_required
def renew_subscription():
    """Client initiates subscription renewal / plan upgrade request for platform approval."""
    business_id = _current_business_id()
    try:
        duration_days = int(request.form.get("duration_days", "30"))
    except (ValueError, TypeError):
        duration_days = 30

    from services.subscription_service import SubscriptionService
    username = session.get("admin_user", "Clinic Admin")
    res = SubscriptionService.create_subscription_request(
        business_id=business_id,
        duration_days=duration_days,
        requested_by=username
    )
    if res.get("success"):
        flash("Subscription request submitted successfully! The platform onboarding team will review and activate your plan.", "success")
    else:
        flash(res.get("error", "Failed to submit subscription request. Please contact support."), "danger")

    return redirect(url_for("admin_bp.subscription_view"))


@admin_bp.route("/admin/subscription/cancel", methods=["POST"])
@login_required
def cancel_own_subscription():
    """Client requests immediate cancellation / unsubscribes from software license."""
    business_id = _current_business_id()
    from services.subscription_service import SubscriptionService
    username = session.get("admin_user", "Clinic Admin")
    res = SubscriptionService.cancel_subscription(business_id, reason=f"Cancelled by client administrator '{username}'")
    if res.get("success"):
        flash("Your subscription has been cancelled. Access to clinic features is now locked.", "warning")
    else:
        flash(res.get("error", "Failed to cancel subscription."), "danger")

    return redirect(url_for("admin_bp.subscription_view"))


@admin_bp.route("/admin/subscription-expired")
def subscription_expired():
    """Lockout notice page displayed when a clinic's subscription or trial has expired or been cancelled."""
    business_id = session.get("business_id")
    business = db.session.get(Business, business_id) if business_id else None
    sub_info = SubscriptionService.get_subscription_info(business_id) if business_id else None
    return render_template("subscription_expired.html", business=business, sub_info=sub_info)


@admin_bp.route("/admin/subscription/dismiss-notice", methods=["POST"])
@login_required
def dismiss_subscription_notice():
    """Dismiss a rejected subscription request notification for the current admin session."""
    req_id = request.form.get("request_id")
    if req_id:
        session[f"dismissed_rejection_{req_id}"] = True
    return jsonify({"success": True})


