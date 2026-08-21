from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from models import db, Reminder, Appointment, Business


class ReminderService:
    # Fallback timezone if business record is missing
    _DEFAULT_TZ = "Asia/Karachi"

    @staticmethod
    def _get_tz(appointment: Appointment) -> ZoneInfo:
        """Retrieve the clinic's ZoneInfo object from the linked business record."""
        try:
            biz = db.session.get(Business, appointment.business_id)
            tz_name = (biz.timezone if biz and biz.timezone else ReminderService._DEFAULT_TZ)
        except Exception:
            tz_name = ReminderService._DEFAULT_TZ
        return ZoneInfo(tz_name)

    @staticmethod
    def schedule_for_appointment(appointment: Appointment) -> Optional[Reminder]:
        """
        Creates a scheduled reminder for an appointment.
        By default schedules for 24 hours prior to the appointment date/time.
        All datetime comparisons use the clinic's configured timezone to avoid
        UTC/local-clock disagreements.
        """
        try:
            tz = ReminderService._get_tz(appointment)
            appt_dt_str = f"{appointment.appointment_date} {appointment.appointment_time}"
            # Parse as a naive datetime, then localise to the clinic timezone
            appt_naive = datetime.strptime(appt_dt_str, "%Y-%m-%d %H:%M")
            appt_aware = appt_naive.replace(tzinfo=tz)

            # 24 hours prior
            scheduled_for_aware = appt_aware - timedelta(days=1)
            now_aware = datetime.now(tz)

            # If 24h prior is already in the past, fall back to 2 hours before appointment
            if scheduled_for_aware < now_aware:
                scheduled_for_aware = max(now_aware, appt_aware - timedelta(hours=2))

            # Store as UTC-aware datetime in the database
            scheduled_for_utc = scheduled_for_aware.astimezone(ZoneInfo("UTC"))

            reminder = Reminder(
                business_id=appointment.business_id,
                appointment_id=appointment.id,
                scheduled_for=scheduled_for_utc,
                status="SCHEDULED",
                reminder_type="24H_BEFORE"
            )
            db.session.add(reminder)
            db.session.flush()
            return reminder
        except Exception as e:
            print(f"Error scheduling reminder: {e}")
            return None

    @staticmethod
    def cancel_for_appointment(appointment_id: int):
        """Cancel any pending scheduled reminders for an appointment."""
        reminders = Reminder.query.filter_by(appointment_id=appointment_id, status="SCHEDULED").all()
        for r in reminders:
            r.status = "CANCELLED"
        db.session.flush()

    @staticmethod
    def get_all_reminders(business_id: int) -> List[Dict[str, Any]]:
        """Retrieve all reminder records for the business."""
        reminders = (
            Reminder.query
            .filter_by(business_id=business_id)
            .order_by(Reminder.scheduled_for.desc())
            .all()
        )
        return [r.to_dict() for r in reminders]
