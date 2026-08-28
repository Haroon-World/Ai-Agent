import unittest
from ai.llm_client import _classify_intent, resolve_date_string

class TestClassifyIntent(unittest.TestCase):
    def test_A_roman_urdu_weekly_schedule(self):
        self.assertEqual(_classify_intent('dr sara ka weekly schedule batao'), 'DOCTOR_WEEKLY_SCHEDULE')
    def test_B_skedule_not_recognised(self):
        result = _classify_intent('Dr Ahmad ka skedule batao')
        self.assertEqual(result, 'DOCTOR_WEEKLY_SCHEDULE', f'Got: {result}')
    def test_C_availability_kal(self):
        self.assertEqual(_classify_intent('Dr Sara kal available hain?'), 'CHECK_AVAILABILITY')
    def test_D_slots_kal(self):
        self.assertEqual(_classify_intent('Dr Sara ke kal ke slots batao'), 'CHECK_AVAILABILITY')
    def test_E_weekday_day_schedule(self):
        self.assertEqual(_classify_intent('Dr Sara ka Monday ka time kya hai?'), 'DOCTOR_DAY_SCHEDULE')
    def test_G_stale_date_not_used(self):
        result = _classify_intent('Dr Ahmad ka schedule batao', conv_state={'requested_date': '2026-08-28'})
        self.assertEqual(result, 'DOCTOR_WEEKLY_SCHEDULE', f'Got: {result}')
    def test_H_roman_urdu_btao(self):
        self.assertEqual(_classify_intent('dr sara ka weekly schedule btao'), 'DOCTOR_WEEKLY_SCHEDULE')
    def test_I_hafta_war(self):
        self.assertIn(_classify_intent('Dr Sara ka hafta war schedule kya hai'), ['DOCTOR_WEEKLY_SCHEDULE', 'DOCTOR_DAY_SCHEDULE'])
    def test_J_english_tomorrow(self):
        self.assertEqual(_classify_intent('Can I check Dr Ahmed available slots for tomorrow?'), 'CHECK_AVAILABILITY')
    def test_kal_ka_schedule_is_availability(self):
        self.assertEqual(_classify_intent('Dr Sara kal ka schedule batao'), 'CHECK_AVAILABILITY')
    def test_book_intent(self):
        self.assertEqual(_classify_intent('mujhe Dr Sara se appointment fix karni hai'), 'BOOK_APPOINTMENT')
    def test_iso_date_is_availability(self):
        self.assertEqual(_classify_intent('2026-08-30 ko kya slots hain?'), 'CHECK_AVAILABILITY')
    def test_timing_without_date(self):
        self.assertEqual(_classify_intent('dr ahmed ki timing kya hai'), 'DOCTOR_WEEKLY_SCHEDULE')

class TestResolveDateIsolation(unittest.TestCase):
    def test_monday_schedule_no_date(self):
        result = resolve_date_string('Dr Sara ka Monday ka schedule batao')
        self.assertIsNone(result, f'Got: {result}')
    def test_kal_slots_has_date(self):
        result = resolve_date_string('kal ke slots dikhao')
        self.assertIsNotNone(result)
    def test_skedule_no_date(self):
        result = resolve_date_string('Dr Ahmad ka skedule batao')
        self.assertIsNone(result, f'Got: {result}')

if __name__ == '__main__':
    unittest.main(verbosity=2)
