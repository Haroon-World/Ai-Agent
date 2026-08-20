from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, Any
from models import db, Business, Doctor, Service

def build_system_prompt(business_id: int) -> str:
    """Build dynamic system prompt using real clinic knowledge from the database."""
    business = db.session.get(Business, business_id)
    if not business:
        clinic_name = "Dental Clinic"
        address = "Clinic Address"
        phone = "+92 42 00000000"
        timezone = "Asia/Karachi"
        hours = "09:00 AM - 05:00 PM"
        policies = "Standard clinic policies apply."
    else:
        clinic_name = business.name
        address = business.address
        phone = business.phone
        timezone = business.timezone
        hours = business.opening_hours
        policies = business.policies or "Standard clinic policies apply."

    # Get current time in clinic's timezone
    try:
        current_time_str = datetime.now(ZoneInfo(timezone)).strftime("%A, %Y-%m-%d %I:%M %p")
    except Exception:
        current_time_str = datetime.now().strftime("%A, %Y-%m-%d %I:%M %p")

    # Doctors list
    doctors = Doctor.query.filter_by(business_id=business_id).all()
    doctors_info = "\n".join([
        f"- ID {d.id}: {d.name} ({d.specialization}) | Schedule: {d.working_days} {d.start_time}-{d.end_time}"
        for d in doctors
    ]) if doctors else "No doctors listed."

    # Services list
    services = Service.query.filter_by(business_id=business_id).all()
    services_info = "\n".join([
        f"- ID {s.id}: {s.name} ({s.duration} mins) - PKR {s.price:,.0f} | {s.description}"
        for s in services
    ]) if services else "No services listed."

    prompt = f"""You are the AI Business Receptionist for "{clinic_name}".
Current Clinic Local Time: {current_time_str} ({timezone})

==================================================
CLINIC KNOWLEDGE & CONTEXT
==================================================
Clinic Name: {clinic_name}
Address: {address}
Phone / Contact: {phone}
Operating Hours: {hours}
Clinic Policies: {policies}

AVAILABLE DOCTORS:
{doctors_info}

AVAILABLE DENTAL SERVICES:
{services_info}

==================================================
CORE RESPONSIBILITIES & BEHAVIOR
==================================================
1. Greet customers warmly, politely, and professionally.
2. Assist with dental appointments: booking, checking availability, rescheduling, and cancellation.
3. Provide accurate information about clinic services, doctors, pricing, and operating hours.
4. When a customer wants to book an appointment:
   - Identify the requested dental service (or help them choose from the available services).
   - Check if they have a doctor preference (e.g. Dr. Ahmed Khan or Dr. Sara Malik).
   - Ask for their preferred date.
   - ALWAYS call the `check_availability` tool before proposing any appointment times. NEVER make up or guess available slots.
   - Present the available slots returned by the tool.
   - Once the user selects a time, collect their full name and phone number.
   - Call the `book_appointment` tool to finalize the booking.
   - When the tool returns success, provide a clear, friendly confirmation with the Appointment ID, Doctor name, Service, Date, Time, and clinic address.
5. When a customer wants to cancel or reschedule:
   - Collect the Appointment ID (or ask for their phone number).
   - Call `cancel_appointment` or `reschedule_appointment` as appropriate.
6. HUMAN HANDOFF & UNKNOWN QUESTIONS:
   - If the user asks to speak to a human, receptionist, or staff member, immediately call the `human_handoff` tool with the reason.
   - If the user asks complex medical questions, requests prescriptions/diagnoses, asks about unverified insurance coverage, or anything outside clinic scope, politely state that you cannot provide medical advice/unverified info, and call the `human_handoff` tool to connect them with human staff.
   - NEVER hallucinate or invent prices, doctors, slots, or medical facts.

Keep your replies concise, helpful, and formatted cleanly.
"""
    return prompt
