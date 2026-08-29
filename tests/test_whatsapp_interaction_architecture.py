import unittest, json
from datetime import date, timedelta
from app import create_app
from config.config import Config
from models import db, Conversation, Appointment, Doctor, Service, Message, Customer
from ai.agent import Agent, _build_ui_action, _build_state_dict
from seed import seed_database


class TestWhatsAppInteractionArchitecture(unittest.TestCase):
    def setUp(self):
        class TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
            SQLALCHEMY_TRACK_MODIFICATIONS = False
            TESTING = True
            SECRET_KEY = 'test-secret'
            LLM_PROVIDER = 'mock'

        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        seed_database(self.app)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _next_working_date(self, doctor_id=2):
        today = date.today()
        for i in range(1, 8):
            target = today + timedelta(days=i)
            if target.weekday() in [0, 1, 3]: # Mon, Tue, Thu
                return target.strftime('%Y-%m-%d')
        return (today + timedelta(days=1)).strftime('%Y-%m-%d')

    def test_1_service_selection_schema(self):
        conv = Conversation(business_id=1, status='AI', intent='BOOK_APPOINTMENT', workflow_state='START')
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action['type'], 'service_selection')
        self.assertEqual(ui_action['interactive_type'], 'list')
        self.assertTrue(len(ui_action['options']) > 0)
        opt = ui_action['options'][0]
        self.assertIn('id', opt)
        self.assertIn('title', opt)
        self.assertIn('label', opt)
        self.assertIn('value', opt)
        self.assertIn('description', opt)

    def test_2_doctor_selection_schema(self):
        conv = Conversation(business_id=1, status='AI', intent='BOOK_APPOINTMENT', workflow_state='COLLECTING_INFO', selected_service_id=1)
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action['type'], 'doctor_selection')
        self.assertEqual(ui_action['interactive_type'], 'list')
        self.assertTrue(len(ui_action['options']) > 0)
        opt = ui_action['options'][0]
        self.assertIn('id', opt)
        self.assertIn('title', opt)
        self.assertIn('label', opt)
        self.assertIn('value', opt)

    def test_3_date_selection_schema(self):
        conv = Conversation(business_id=1, status='AI', intent='BOOK_APPOINTMENT', workflow_state='COLLECTING_INFO', selected_service_id=1, selected_doctor_id=2)
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action['type'], 'date_selection')
        self.assertEqual(ui_action['interactive_type'], 'quick_reply')
        self.assertTrue(len(ui_action['options']) > 0)
        opt = ui_action['options'][0]
        self.assertIn('id', opt)
        self.assertIn('title', opt)
        self.assertIn('value', opt)

    def test_4_real_time_slot_selection_schema(self):
        target_date = self._next_working_date(doctor_id=2)
        conv = Conversation(business_id=1, status='AI', intent='BOOK_APPOINTMENT', workflow_state='CHECKING_AVAILABILITY', selected_service_id=1, selected_doctor_id=2, requested_date=target_date)
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action['type'], 'time_slot_selection')
        self.assertEqual(ui_action['interactive_type'], 'list')
        self.assertTrue(len(ui_action['options']) > 0)
        opt = ui_action['options'][0]
        self.assertIn('id', opt)
        self.assertIn('title', opt)
        self.assertIn('value', opt)
        self.assertIn('period', opt)

    def test_5_confirmation_card_schema(self):
        target_date = self._next_working_date(doctor_id=2)
        conv = Conversation(
            business_id=1, status='AI', intent='BOOK_APPOINTMENT', workflow_state='COLLECTING_INFO',
            selected_service_id=1, selected_doctor_id=2, requested_date=target_date, requested_time='09:00',
            pending_customer_name='Haroon', pending_customer_phone='03001234567'
        )
        db.session.add(conv)
        db.session.commit()

        ui_action = _build_ui_action(conv)
        self.assertIsNotNone(ui_action)
        self.assertEqual(ui_action['type'], 'booking_confirmation')
        self.assertEqual(ui_action['interactive_type'], 'button')
        self.assertIn('details', ui_action)
        self.assertIn('actions', ui_action)
        actions = ui_action['actions']
        self.assertEqual(len(actions), 3) # Confirm, Change, Cancel

    def test_6_message_persistence_and_to_dict(self):
        conv = Conversation(business_id=1, status='AI')
        db.session.add(conv)
        db.session.commit()

        payload = {'type': 'service_selection', 'options': [{'id': '1', 'title': 'Cleaning'}]}
        msg = Message(conversation_id=conv.id, role='assistant', content='Please choose:', interactive_data=json.dumps(payload))
        db.session.add(msg)
        db.session.commit()

        d = msg.to_dict()
        self.assertIsNotNone(d['interactive_data'])
        self.assertEqual(d['interactive_data']['type'], 'service_selection')

    def test_7_change_and_cancel_workflow(self):
        biz_id = 1
        agent = Agent(business_id=biz_id, llm_provider='mock')
        conv = Conversation(business_id=biz_id, status='AI', intent='BOOK_APPOINTMENT', workflow_state='START')
        db.session.add(conv)
        db.session.commit()

        target_date = self._next_working_date(doctor_id=2)
        agent.process_message(conv.id, f'I want Sara on {target_date} at 09:00')

        # Test Change
        r_change = agent.process_message(conv.id, 'I want to change my appointment details')
        self.assertIn('update your appointment', r_change['content'].lower())
        conv_db = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv_db.requested_time)

        # Test Cancel
        r_cancel = agent.process_message(conv.id, 'Cancel booking')
        self.assertIn('cancelled', r_cancel['content'].lower())
        conv_db2 = db.session.get(Conversation, conv.id)
        self.assertIsNone(conv_db2.requested_date)
        self.assertIsNone(conv_db2.selected_doctor_id)

    def test_8_human_handoff(self):
        biz_id = 1
        agent = Agent(business_id=biz_id, llm_provider='mock')
        conv = Conversation(business_id=biz_id, status='AI', intent='UNKNOWN', workflow_state='START')
        db.session.add(conv)
        db.session.commit()

        res = agent.process_message(conv.id, 'I want to speak to a human receptionist')
        self.assertEqual(res['status'], 'HUMAN')
        self.assertIn('human_handoff', [t['name'] for t in res.get('executed_tools', [])])

        # Subsequent message while in HUMAN status bypasses AI
        res2 = agent.process_message(conv.id, 'Hello?')
        self.assertEqual(res2['status'], 'HUMAN')
        self.assertIn('human staff', res2['content'].lower())

        # Customer message must be persisted to DB
        msg2 = Message.query.filter_by(conversation_id=conv.id, content='Hello?').first()
        self.assertIsNotNone(msg2)
        self.assertEqual(msg2.role, 'user')

if __name__ == '__main__':
    unittest.main()
