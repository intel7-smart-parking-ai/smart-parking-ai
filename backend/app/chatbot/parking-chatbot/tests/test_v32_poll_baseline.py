"""새 대화가 'start' 없이 시작돼도 지난 경보·'경보 없음'을 말풍선으로 다시 받지 않는다 (PM 11/09 화면).

화면(chatSession.js)은 start 이벤트 없이 첫 질문을 보내므로, 서버가 첫 updates()에서 기준점을 잡는다.
"""
import unittest

from chatbot import engine as e
import test_api_contract as fixture

ALARM = dict(alert_id='p1', lot_id='A', space_id='A-03', level=2, type='mock_fire_led',
             occurred_at='2026-11-03T05:02:00+00:00')


class PollBaselineTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ApiContractTests('test_01_server_counts_and_uncertain')
        self.f.setUp()
        self.f.mock_header = False

    def tearDown(self):
        self.f.tearDown()

    def test_new_session_without_start_gets_no_old_alarm_bubbles(self):
        self.f.alerts = [dict(ALARM, status='active')]
        self.f.ask('경보')
        self.f.alerts = [dict(ALARM, status='resolved')]
        self.f.ask('경보')
        self.f.alerts = []
        self.f.ask('경보')
        e.handle('fresh-no-start', text='넌 이름이 뭐니')
        self.assertEqual(e.updates('fresh-no-start')['messages'], [])

    def test_alarm_raised_after_session_start_is_still_pushed(self):
        e.handle('early-no-start', text='넌 이름이 뭐니')
        e.updates('early-no-start')
        self.f.alerts = [dict(ALARM, status='active')]
        self.f.ask('경보')
        texts = [m['text'] for m in e.updates('early-no-start')['messages']]
        self.assertTrue(any('모의 화재' in t for t in texts), texts)


if __name__ == '__main__':
    unittest.main()
