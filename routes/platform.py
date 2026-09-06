from functools import wraps
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, jsonify, abort
)
from models import db, Business, Doctor, Service, Appointment, User
from config.config import Config

platform_bp = Blueprint("platform_bp", __name__)


# ---------------------------------------------------------------------------
# Platform Access Control
# ---------------------------------------------------------------------------

def platform_admin_required(f):
    """
    Ensure the user is logged in as a Platform Owner (is_platform_admin=True).
    Redirects unauthenticated users to the secret platform login.
    Returns 403 Forbidden for any clinic admin attempting to access.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("platform_bp.login"))
        if not session.get("is_platform_admin"):
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------------------------
# Stealth Platform Login & Logout
# ---------------------------------------------------------------------------

@platform_bp.route("/login", methods=["GET", "POST"])
def login():
    """Dedicated, unlinked master login for SaaS Platform Owners."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = (request.form.get("password") or "").strip()

        # Find platform admin user
        matched_user = None
        candidates = User.query.filter_by(username=username, is_platform_admin=True).all()
        for candidate in candidates:
            if candidate.check_password(password):
                matched_user = candidate
                break

        if matched_user:
            session.clear()
            session.permanent = True
            session["user_id"] = matched_user.id
            session["business_id"] = None
            session["admin_user"] = matched_user.username
            session["is_platform_admin"] = True
            session["clinic_name"] = "ClinicConnectAI Platform"
            flash("Welcome to the SaaS Master Console.", "success")
            return redirect(url_for("platform_bp.dashboard"))
        else:
            # Check if this username belonged to a clinic admin to prevent accidental lockout
            is_clinic_user = User.query.filter_by(username=username, is_platform_admin=False).first()
            if is_clinic_user:
                flash("Unauthorized: Clinic staff accounts must sign in via the Clinic Admin Portal.", "warning")
            else:
                flash("Invalid platform credentials.", "danger")

    already_logged_in = bool(session.get("user_id") and session.get("is_platform_admin"))
    if already_logged_in:
        return redirect(url_for("platform_bp.dashboard"))

    return render_template("platform/login.html")


@platform_bp.route("/logout")
def logout():
    """Sign out of the platform owner session."""
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
    """Master overview of all onboarded clinics and platform KPIs."""
    clinics = Business.query.order_by(Business.id.asc()).all()

    # Collect statistics and tenant admin info
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

        # Primary admin for this clinic
        admin_user = User.query.filter_by(business_id=clinic.id, is_platform_admin=False).first()
        admin_username = admin_user.username if admin_user else "None"

        clinic_records.append({
            "business": clinic,
            "doctors_count": doc_count,
            "services_count": svc_count,
            "appointments_count": appt_count,
            "admin_username": admin_username,
        })

    metrics = {
        "total_clinics": len(clinics),
        "total_doctors": total_doctors,
        "total_services": total_services,
        "total_appointments": total_appointments,
    }

    return render_template("platform/dashboard.html", clinics=clinic_records, metrics=metrics)


# ---------------------------------------------------------------------------
# Onboard New Clinic Action
# ---------------------------------------------------------------------------

@platform_bp.route("/onboard-clinic", methods=["GET", "POST"])
@platform_admin_required
def onboard_clinic():
    """Onboard a new clinic tenant and generate initial admin credentials."""
    success_info = None

    if request.method == "POST":
        clinic_name = request.form.get("clinic_name", "").strip()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        business_type = request.form.get("business_type", "dental_clinic").strip() or "dental_clinic"
        timezone = request.form.get("timezone", "Asia/Karachi").strip() or "Asia/Karachi"
        opening_hours = request.form.get("opening_hours", "").strip() or "Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed"
        admin_username = request.form.get("admin_username", "").strip()
        admin_password = request.form.get("admin_password", "").strip()

        errors = []
        if not clinic_name:
            errors.append("Clinic name is required.")
        if not address:
            errors.append("Address is required.")
        if not phone:
            errors.append("Phone is required.")
        if not admin_username:
            errors.append("Admin username is required.")
        if not admin_password or len(admin_password) < 6:
            errors.append("Admin password must be at least 6 characters.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return redirect(url_for("platform_bp.dashboard"))

        new_business = Business(
            name=clinic_name,
            business_type=business_type,
            address=address,
            phone=phone,
            timezone=timezone,
            opening_hours=opening_hours,
        )
        db.session.add(new_business)
        db.session.flush()

        existing = User.query.filter_by(
            business_id=new_business.id, username=admin_username
        ).first()
        if existing:
            db.session.rollback()
            flash(f"Username '{admin_username}' already exists for this clinic.", "danger")
            return redirect(url_for("platform_bp.dashboard"))

        new_user = User(
            business_id=new_business.id,
            username=admin_username,
            is_platform_admin=False,
        )
        new_user.set_password(admin_password)
        db.session.add(new_user)
        db.session.commit()

        success_info = {
            "clinic_name": clinic_name,
            "business_id": new_business.id,
            "admin_username": admin_username,
            "admin_password": admin_password,
        }
        flash(f"Clinic '{clinic_name}' onboarded successfully!", "success")

    # Render dashboard with the success info modal/card
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

        clinic_records.append({
            "business": clinic,
            "doctors_count": doc_count,
            "services_count": svc_count,
            "appointments_count": appt_count,
            "admin_username": admin_username,
        })

    metrics = {
        "total_clinics": len(clinics),
        "total_doctors": total_doctors,
        "total_services": total_services,
        "total_appointments": total_appointments,
    }

    return render_template(
        "platform/dashboard.html",
        clinics=clinic_records,
        metrics=metrics,
        success_info=success_info
    )
