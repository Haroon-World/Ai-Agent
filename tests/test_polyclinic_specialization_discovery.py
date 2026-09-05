import unittest
from app import create_app
from config.config import Config
from models import db, Business, Doctor, Service, Conversation, DoctorSchedule
from ai.agent import Agent

class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'

class TestPolyclinicSpecializationDiscovery(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Clean existing seeded data
        Service.query.delete()
        DoctorSchedule.query.delete()
        Doctor.query.delete()
        Conversation.query.delete()

        clinic = db.session.get(Business, 1)
        if not clinic:
            clinic = Business(
                id=1,
                name='ClinicConnect Polyclinic',
                business_type='polyclinic',
                address='Plot 42-B, Main Boulevard, Gulberg III, Lahore',
                phone='+92 42 35789000',
                timezone='Asia/Karachi',
                opening_hours='Monday to Saturday: 09:00 AM - 05:00 PM, Sunday: Closed',
                policies='Standard polyclinic policies.'
            )
            db.session.add(clinic)
        else:
            clinic.name = 'ClinicConnect Polyclinic'
            clinic.business_type = 'polyclinic'

        # Seed multi-specialty doctors
        self.dr_ahmed = Doctor(
            id=1,
            business_id=1,
            name='Dr. Ahmed Khan',
            specialization='General Dentistry & Orthodontics',
            working_days='Monday,Tuesday,Wednesday,Thursday,Friday,Saturday',
            start_time='09:00',
            end_time='17:00',
            slot_interval=30,
            is_active=True
        )
        self.dr_sara = Doctor(
            id=2,
            business_id=1,
            name='Dr. Sara Malik',
            specialization='Dermatology & Cosmetology',
            working_days='Monday,Tuesday,Wednesday,Thursday,Friday,Saturday',
            start_time='10:00',
            end_time='18:00',
            slot_interval=30,
            is_active=True
        )
        db.session.add_all([self.dr_ahmed, self.dr_sara])
        db.session.flush()

        # Seed services per doctor
        s1 = Service(
            id=1,
            business_id=1,
            doctor_id=self.dr_ahmed.id,
            name='Tooth Extraction',
            duration=45,
            price=5000.0,
            is_active=True
        )
        s2 = Service(
            id=2,
            business_id=1,
            doctor_id=self.dr_sara.id,
            name='Acne & Skin Treatment',
            duration=30,
            price=6000.0,
            is_active=True
        )
        db.session.add_all([s1, s2])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_rebranding_identity(self):
        conv = Conversation(business_id=1, status='AI')
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider='mock')
        res = agent.process_message(conv.id, 'who are you')
        content = res['content']

        # Should identify with the polyclinic name
        self.assertTrue('ClinicConnect' in content or 'Polyclinic' in content)

    def test_dynamic_specialization_discovery_dermatologist(self):
        conv = Conversation(business_id=1, status='AI')
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider='mock')
        res = agent.process_message(conv.id, 'Do you have a skin doctor?')
        content = res['content']

        # Must dynamically recommend Dr. Sara Malik who specializes in Dermatology
        self.assertIn('Sara', content)
        self.assertTrue('dermatology' in content.lower() or 'skin' in content.lower())

    def test_dynamic_specialization_discovery_dentist(self):
        conv = Conversation(business_id=1, status='AI')
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider='mock')
        res = agent.process_message(conv.id, 'Do you have a teeth doctor?')
        content = res['content']

        # Must dynamically recommend Dr. Ahmed Khan who specializes in Dentistry
        self.assertIn('Ahmed', content)
        self.assertTrue('dentist' in content.lower() or 'orthodontics' in content.lower())

    def test_out_of_scope_specialty_dynamically_lists_roster(self):
        conv = Conversation(business_id=1, status='AI')
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider='mock')
        # Currently no ophthalmologist / eye doctor exists in this polyclinic
        res = agent.process_message(conv.id, 'I want to check my eyes')
        content = res['content']

        # Must clarify polyclinic scope and list available doctors
        self.assertTrue('eye' in content.lower() or 'polyclinic' in content.lower())
        self.assertTrue('Ahmed' in content or 'Sara' in content)

    def test_admin_adding_doctor_dynamically_adjusts_agent(self):
        # Admin adds Dr. Tariq, Cardiologist via DB
        dr_tariq = Doctor(
            id=3,
            business_id=1,
            name='Dr. Zaid Tariq',
            specialization='Cardiology & Heart Care',
            working_days='Monday,Wednesday,Friday',
            start_time='09:00',
            end_time='15:00',
            slot_interval=30,
            is_active=True
        )
        s3 = Service(
            id=3,
            business_id=1,
            doctor_id=3,
            name='Cardiac Consultation & ECG',
            duration=30,
            price=7000.0,
            is_active=True
        )
        db.session.add_all([dr_tariq, s3])
        db.session.commit()

        conv = Conversation(business_id=1, status='AI')
        db.session.add(conv)
        db.session.commit()

        agent = Agent(business_id=1, llm_provider='mock')
        # Ask for cardiologist
        res = agent.process_message(conv.id, 'Do you have a heart doctor?')
        content = res['content']

        # Agent should dynamically adjust and offer Dr. Zaid Tariq!
        self.assertIn('Zaid', content)
        self.assertTrue('cardiology' in content.lower() or 'heart' in content.lower())

if __name__ == '__main__':
    unittest.main()
