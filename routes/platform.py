from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, jsonify, abort
)
from models import db, Business, Doctor, Service, Appointment, User, SubscriptionRequest
from config.config import Config
from services.subscription_service import SubscriptionService

platform_bp = Blueprint("platform_bp", __name__)


# ---------------------------------------------------------------------------
# Platform Access Control
# ---------------------------------------------------------------------------

def platform_admin_required(f):
    """
    Ensure the user is logged in as a Platform Owner (is_platform_admin=True).
    Checks both session flags and DB records (via platform_admin_id or user_id)
    to avoid 403 errors when concurrent clinic admin tabs are open.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Check if platform_admin_id exists in session and verify in DB
        p_id = session.get("platform_admin_id")
        if p_id:
            p_user = db.session.get(User, p_id)
            if p_user and p_user.is_platform_admin:
                return f(*args, **kwargs)

        # 2. Check primary session user_id
        u_id = session.get("user_id")
        if u_id:
            u_user = db.session.get(User, u_id)
            if u_user and u_user.is_platform_admin:
                session["platform_admin_id"] = u_user.id
                session["platform_admin_user"] = u_user.username
                session["is_platform_admin"] = True
                return f(*args, **kwargs)

        # 3. Fallback: check session flags
        if session.get("is_platform_admin"):
            return f(*args, **kwargs)

        # 4. If not logged in at all, redirect to platform login
        if not session.get("user_id") and not session.get("platform_admin_id"):
            flash("Unauthorized access. Platform administrator credentials required.", "danger")
            return redirect(url_for("platform_bp.login"))

        # User is authenticated but is a clinic user without platform admin privileges
        abort(403)
    return decorated_function


# ---------------------------------------------------------------------------
# Platform Owner Authentication
# ---------------------------------------------------------------------------

@platform_bp.route("/login", methods=["GET", "POST"])
def login():
    """Dedicated login portal exclusively for SaaS Platform Owners."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        candidates = User.query.filter_by(username=username, is_platform_admin=True).all()
        matched_user = None
        for u in candidates:
            if u.check_password(password):
                matched_user = u
                break

        if matched_user:
            session.permanent = True
            session["platform_admin_id"] = matched_user.id
            session["platform_admin_user"] = matched_user.username
            session["is_platform_admin"] = True

            if not session.get("user_id"):
                session["user_id"] = matched_user.id
                session["admin_user"] = matched_user.username

            flash("Signed into SaaS Master Console.", "success")
            return redirect(url_for("platform_bp.dashboard"))
        else:
            is_clinic_user = User.query.filter_by(username=username, is_platform_admin=False).first()
            if is_clinic_user and is_clinic_user.check_password(password):
                flash("Unauthorized: Clinic staff accounts must sign in via the Clinic Admin Portal.", "warning")
            else:
                flash("Invalid platform administrator credentials.", "danger")

    return render_template(
        "platform/login.html",
        already_logged_in=bool(session.get("platform_admin_id") or session.get("is_platform_admin")),
        current_user=session.get("platform_admin_user", "Platform Owner")
    )


@platform_bp.route("/logout", methods=["GET"])
def logout():
    """Sign out of SaaS Master Console without destroying active clinic session."""
    session.pop("platform_admin_id", None)
    session.pop("platform_admin_user", None)
    session.pop("is_platform_admin", None)
    if not session.get("business_id"):
        session.clear()
    flash("You have been logged out of the platform console.", "info")
    return redirect(url_for("platform_bp.login"))


# ---------------------------------------------------------------------------
# Master Platform Console
# ---------------------------------------------------------------------------

@platform_bp.route("", methods=["GET"])
@platform_bp.route("/dashboard", methods=["GET"])
@platform_admin_required
def dashboard():
    """Master overview of all onboarded clinics, subscriptions, and platform KPIs."""
    clinics = Business.query.order_by(Business.id.asc()).all()

    clinic_records = []
    total_doctors = 0
    total_services = 0
    total_appointments = 0

    for clinic in clinics:
        doc_count = len(clinic.doctors)
        svc_count = len(clinic.services)
        appt_count = len(clinic.appointments)

        total_doctors += doc_count
        total_services += svc_count
        total_appointments += appt_count

        admin_user = User.query.filter_by(business_id=clinic.id, is_platform_admin=False).first()
        admin_username = admin_user.username if admin_user else "None"
        admin_email = admin_user.email if admin_user else (clinic.email or "")

        clinic_records.append({
            "business": clinic,
            "doctors_count": doc_count,
            "services_count": svc_count,
            "appointments_count": appt_count,
            "admin_username": admin_username,
            "admin_email": admin_email,
            "subscription": clinic.subscription_badge,
        })

    pending_requests = SubscriptionService.get_pending_requests()

    metrics = {
        "total_clinics": len(clinics),
        "total_doctors": total_doctors,
        "total_services": total_services,
        "total_appointments": total_appointments,
        "pending_subscriptions": len(pending_requests)
    }

    return render_template(
        "platform/dashboard.html",
        clinics=clinic_records,
        metrics=metrics,
        pending_requests=pending_requests
    )


@platform_bp.route("/manage-clinic/<int:clinic_id>", methods=["GET"])
def manage_clinic(clinic_id):
    """Direct link to clinic client login; strictly prohibits credential bypass."""
    flash("Please sign in with the clinic administrator credentials to access the clinic portal.", "info")
    return redirect(url_for("admin_bp.login"))


# ---------------------------------------------------------------------------
# Dedicated Onboard New Clinic View & Action
# ---------------------------------------------------------------------------

@platform_bp.route("/onboard-clinic", methods=["GET", "POST"])
@platform_admin_required
def onboard_clinic_view():
    """Dedicated standalone onboarding page for registering a new clinic client."""
    success_info = None

    if request.method == "POST":
        clinic_name = request.form.get("clinic_name", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        business_type = request.form.get("business_type", "dental_clinic").strip() or "dental_clinic"
        timezone_str = request.form.get("timezone", "Asia/Karachi").strip() or "Asia/Karachi"
        opening_hours = request.form.get("opening_hours", "").strip() or "Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed"
        admin_username = request.form.get("admin_username", "").strip()
        admin_email = request.form.get("admin_email", "").strip().lower()
        admin_password = request.form.get("admin_password", "").strip()
        plan_type = request.form.get("plan_type", "trial_30").strip()
        custom_expiry_date = request.form.get("custom_expiry_date", "").strip()

        errors = []
        if not clinic_name:
            errors.append("Clinic name is required.")
        if not address:
            errors.append("Address is required.")
        if not phone:
            errors.append("Phone is required.")
        if not admin_username:
            errors.append("Admin username is required.")
        elif User.query.filter_by(username=admin_username).first():
            errors.append(f"Username '{admin_username}' is already taken across the platform. Please choose a unique username.")

        if not admin_email:
            admin_email = f"{admin_username}@clinic.local"

        if "@" not in admin_email:
            errors.append("Valid admin contact email is required.")
        elif User.query.filter(db.func.lower(User.email) == admin_email).first():
            errors.append(f"Email '{admin_email}' is already registered with another clinic account.")
        if not admin_password or len(admin_password) < 6:
            errors.append("Admin password must be at least 6 characters.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("platform/onboard.html")

        # Subscription dates setup
        now_utc = datetime.now(timezone.utc)
        if plan_type == "trial_30":
            sub_status = "trial"
            trial_end = now_utc + timedelta(days=30)
            sub_exp = None
        elif plan_type == "sub_90":
            sub_status = "active"
            trial_end = now_utc + timedelta(days=30)
            sub_exp = now_utc + timedelta(days=90)
        elif plan_type == "sub_180":
            sub_status = "active"
            trial_end = now_utc + timedelta(days=30)
            sub_exp = now_utc + timedelta(days=180)
        elif plan_type == "sub_365":
            sub_status = "active"
            trial_end = now_utc + timedelta(days=30)
            sub_exp = now_utc + timedelta(days=365)
        elif plan_type == "custom" and custom_expiry_date:
            try:
                sub_exp = datetime.strptime(custom_expiry_date, "%Y-%m-%d")
                sub_status = "active"
                trial_end = now_utc + timedelta(days=30)
            except ValueError:
                sub_status = "trial"
                trial_end = now_utc + timedelta(days=30)
                sub_exp = None
        else:
            sub_status = "trial"
            trial_end = now_utc + timedelta(days=30)
            sub_exp = None

        new_business = Business(
            name=clinic_name,
            business_type=business_type,
            address=address,
            phone=phone,
            email=admin_email,
            timezone=timezone_str,
            opening_hours=opening_hours,
            subscription_status=sub_status,
            trial_ends_at=trial_end,
            subscription_expires_at=sub_exp,
        )
        db.session.add(new_business)
        db.session.flush()

        new_user = User(
            business_id=new_business.id,
            username=admin_username,
            email=admin_email,
            is_platform_admin=False,
        )
        new_user.set_password(admin_password)
        db.session.add(new_user)
        db.session.commit()

        success_info = {
            "clinic_name": clinic_name,
            "business_id": new_business.id,
            "business_type": business_type.replace('_', ' ').title(),
            "phone": phone,
            "address": address,
            "timezone": timezone_str,
            "admin_username": admin_username,
            "admin_email": admin_email,
            "admin_password": admin_password,
            "subscription_status": sub_status,
            "plan_name": "1-Month Free Trial" if sub_status == "trial" else (new_business.active_plan_name or "Active Subscription"),
            "effective_expiry": new_business.effective_expiry_date.strftime("%B %d, %Y") if new_business.effective_expiry_date else "30 Days",
        }
        flash(f"Clinic '{clinic_name}' onboarded successfully with 1-Month Free Trial!", "success")

    return render_template("platform/onboard.html", success_info=success_info)


# ---------------------------------------------------------------------------
# Platform Clinic Subscription & Credential Management
# ---------------------------------------------------------------------------

@platform_bp.route("/clinic/<int:clinic_id>/subscription/extend", methods=["POST"])
@platform_admin_required
def extend_clinic_subscription(clinic_id):
    """Platform owner extends a clinic's subscription by specified days."""
    try:
        days = int(request.form.get("days", "30"))
    except (ValueError, TypeError):
        days = 30

    res = SubscriptionService.extend_subscription(clinic_id, days=days)
    if res.get("success"):
        flash(f"Clinic #{clinic_id} subscription extended by {days} days.", "success")
    else:
        flash(f"Failed to extend subscription: {res.get('error')}", "danger")

    return redirect(url_for("platform_bp.dashboard"))


@platform_bp.route("/subscription-request/<int:request_id>/approve", methods=["POST"])
@platform_admin_required
def approve_subscription_request_action(request_id):
    """Platform owner approves a pending subscription request."""
    reviewer = session.get("admin_user", "Platform Admin")
    res = SubscriptionService.approve_subscription_request(request_id, reviewer_username=reviewer)
    if res.get("success"):
        req = db.session.get(SubscriptionRequest, request_id)
        if req and req.business and req.business.email:
            from services.email_service import EmailService
            exp_str = req.business.subscription_expires_at.strftime("%B %d, %Y") if req.business.subscription_expires_at else None
            EmailService.send_subscription_approved_email(
                to_email=req.business.email,
                clinic_name=req.business.name,
                plan_name=req.plan_name,
                expires_at=exp_str
            )
        flash(res.get("message", "Subscription request approved successfully."), "success")
    else:
        flash(res.get("error", "Failed to approve subscription request."), "danger")
    return redirect(url_for("platform_bp.dashboard"))


@platform_bp.route("/subscription-request/<int:request_id>/reject", methods=["POST"])
@platform_admin_required
def reject_subscription_request_action(request_id):
    """Platform owner rejects a pending subscription request."""
    reviewer = session.get("admin_user", "Platform Admin")
    reason = (request.form.get("reason") or "").strip() or "Rejected by platform team"
    res = SubscriptionService.reject_subscription_request(request_id, reviewer_username=reviewer, reason=reason)
    if res.get("success"):
        flash(res.get("message", "Subscription request rejected."), "warning")
    else:
        flash(res.get("error", "Failed to reject subscription request."), "danger")
    return redirect(url_for("platform_bp.dashboard"))


@platform_bp.route("/clinic/<int:clinic_id>/subscription/cancel", methods=["POST"])
@platform_admin_required
def cancel_clinic_subscription(clinic_id):
    """Platform owner immediately cancels a clinic's subscription, locking portal access."""
    reason = (request.form.get("reason") or "").strip() or "Cancelled by SaaS Platform Admin"
    res = SubscriptionService.cancel_subscription(clinic_id, reason=reason)
    if res.get("success"):
        flash(f"Subscription for Clinic #{clinic_id} has been cancelled. Portal access is now locked.", "warning")
    else:
        flash(f"Failed to cancel subscription: {res.get('error')}", "danger")
    return redirect(url_for("platform_bp.dashboard"))


@platform_bp.route("/clinic/<int:clinic_id>/subscription/reactivate", methods=["POST"])
@platform_admin_required
def reactivate_clinic_subscription(clinic_id):
    """Platform owner reactivates a cancelled or expired clinic subscription."""
    try:
        days = int(request.form.get("days", "30"))
    except (ValueError, TypeError):
        days = 30
    res = SubscriptionService.reactivate_subscription(clinic_id, days=days)
    if res.get("success"):
        flash(f"Clinic #{clinic_id} subscription reactivated with {days} days of access.", "success")
    else:
        flash(f"Failed to reactivate subscription: {res.get('error')}", "danger")
    return redirect(url_for("platform_bp.dashboard"))


@platform_bp.route("/clinic/<int:clinic_id>/subscription/set-dates", methods=["POST"])
@platform_admin_required
def set_clinic_subscription_dates(clinic_id):
    """Platform owner sets custom calendar start and end dates for a clinic subscription."""
    start_str = (request.form.get("start_date") or "").strip()
    end_str = (request.form.get("end_date") or "").strip()

    if not start_str or not end_str:
        flash("Both start date and end date are required.", "danger")
        return redirect(url_for("platform_bp.dashboard"))

    try:
        start_dt = datetime.strptime(start_str, "%Y-%m-%d")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d")
    except ValueError:
        flash("Invalid date format. Please use YYYY-MM-DD.", "danger")
        return redirect(url_for("platform_bp.dashboard"))

    res = SubscriptionService.set_date_range(clinic_id, start_date=start_dt, end_date=end_dt)
    if res.get("success"):
        flash(f"Clinic #{clinic_id} subscription dates updated: {start_str} to {end_str}.", "success")
    else:
        flash(f"Failed to set subscription dates: {res.get('error')}", "danger")

    return redirect(url_for("platform_bp.dashboard"))


@platform_bp.route("/clinic/<int:clinic_id>/reset-password", methods=["POST"])
@platform_admin_required
def reset_clinic_password(clinic_id):
    """Platform owner sets a new password directly for a clinic admin."""
    new_password = (request.form.get("new_password") or "").strip()
    if len(new_password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("platform_bp.dashboard"))

    admin_user = User.query.filter_by(business_id=clinic_id, is_platform_admin=False).first()
    if not admin_user:
        flash("No clinic admin found for this clinic.", "danger")
        return redirect(url_for("platform_bp.dashboard"))

    admin_user.set_password(new_password)
    admin_user.clear_reset_token()
    db.session.commit()

    flash(f"Password for clinic admin '{admin_user.username}' (Clinic #{clinic_id}) updated successfully.", "success")
    return redirect(url_for("platform_bp.dashboard"))


@platform_bp.route("/clinic/<int:clinic_id>/update-email", methods=["POST"])
@platform_admin_required
def update_clinic_email(clinic_id):
    """Platform owner/onboarding team updates the registered email address for a clinic."""
    new_email = (request.form.get("new_email") or "").strip().lower()
    if not new_email or "@" not in new_email:
        flash("Please provide a valid email address.", "danger")
        return redirect(url_for("platform_bp.dashboard"))

    clinic = db.session.get(Business, clinic_id)
    if not clinic:
        flash(f"Clinic #{clinic_id} not found.", "danger")
        return redirect(url_for("platform_bp.dashboard"))

    admin_user = User.query.filter_by(business_id=clinic_id, is_platform_admin=False).first()
    existing_user = User.query.filter(db.func.lower(User.email) == new_email).first()
    if existing_user and admin_user and existing_user.id != admin_user.id:
        flash(f"Email '{new_email}' is already in use by another clinic account.", "danger")
        return redirect(url_for("platform_bp.dashboard"))

    clinic.email = new_email
    if admin_user:
        admin_user.email = new_email
    db.session.commit()

    flash(f"Email for '{clinic.name}' (Clinic #{clinic_id}) successfully updated to '{new_email}'.", "success")
    return redirect(url_for("platform_bp.dashboard"))
