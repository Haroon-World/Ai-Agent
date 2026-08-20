from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from models import db, Reminder, Appointment

class ReminderService:
    @staticmethod
    def schedule_for_appointment(appointment: Appointment) -> Optional[Reminder]:
        """
        Creates a scheduled reminder for an appointment.
        By default schedules for 24 hours prior to the appointment date/time.
        """
        try:
            appt_dt_str = f"{appointment.appointment_date} {appointment.appointment_time}"
            appt_datetime = datetime.strptime(appt_dt_str, "%Y-%m-%d %H:%M")
            # 24 hours prior
            scheduled_for = appt_datetime - timedelta(days=1)
            # If 24h prior is already in the past, schedule for 1 hour before appointment or now
            if scheduled_for < datetime.now():
                scheduled_for = max(datetime.now(), appt_datetime - timedelta(hours=2))

            reminder = Reminder(
                business_id=appointment.business_id,
                appointment_id=appointment.id,
                scheduled_for=scheduled_for,
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
        reminders = Reminder.query.filter_by(business_id=business_id).order_by(Reminder.scheduled_for.desc()).all()
        return [r.to_dict() for r in reminders]
