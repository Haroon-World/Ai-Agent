from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

from models.business import Business
from models.doctor import Doctor
from models.service import Service
from models.customer import Customer
from models.appointment import Appointment
from models.conversation import Conversation
from models.message import Message
from models.reminder import Reminder

__all__ = [
    "db",
    "Business",
    "Doctor",
    "Service",
    "Customer",
    "Appointment",
    "Conversation",
    "Message",
    "Reminder",
]
