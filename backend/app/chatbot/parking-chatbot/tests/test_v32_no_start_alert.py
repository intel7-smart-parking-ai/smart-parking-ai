"""v3.2: 팀 화면은 event=start 없이 바로 질문한다. 첫 updates에서 '경보 없음'을 새 알림처럼 보내지 않는다.

화면 증상: "넌 이름이 뭐니" 답 아래에 "현재 (모의) 화재 경보는 없어요." 말풍선이 따로 붙음.
"""
import unittest
from chatbot import engine as e
import test_api_contract as fixture


class NoStartAlertTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.ApiContractTests('test_01_server_counts_and_uncertain')
        self.f.setUp()
        self.f.mock_header = False

    def tearDown(self):
        self.f.tearDown()

    def alarm(self, status='active', level=1):
        self.f.alerts = [dict(alert_id='nostart', lot_id='A', space_id='A-03', level=level, status=status,
                              type='mock_fire_led', occurred_at='2026-11-09T05:02:00+00:00')]

    def alert_messages(self, sid):
        return [m for m in e.updates(sid)['messages'] if m.get('meta', {}).get('notification') == 'alert']

    def test_resolved_before_session_is_not_pushed(self):
        # 이전 시연에서 울렸다가 해제된 경보 → 서버 경보 버전은 올라가 있음
        self.alarm(); e.current_alert()
        self.alarm(status='resolved'); e.current_alert()
        e.handle('fresh', text='넌 이름이 뭐니')
        self.assertEqual(self.alert_messages('fresh'), [])

    def test_no_alert_ever_is_not_pushed(self):
        self.f.alerts = []
        e.handle('quiet', text='빈자리 있어?')
        self.assertEqual(self.alert_messages('quiet'), [])

    def test_updates_before_any_message_is_quiet(self):
        self.alarm(); e.current_alert()
        self.alarm(status='resolved'); e.current_alert()
        self.assertEqual(self.alert_messages('poll-first'), [])

    def test_active_alert_still_pushed_without_start(self):
        self.alarm()
        e.handle('active', text='빈자리 있어?')
        msgs = self.alert_messages('active')
        self.assertEqual(len(msgs), 1)
        self.assertIn('경보', msgs[0]['text'])

    def test_new_alert_after_session_is_pushed(self):
        self.f.alerts = []
        e.handle('later', text='빈자리 있어?')
        self.assertEqual(self.alert_messages('later'), [])
        self.alarm()
        self.assertEqual(len(self.alert_messages('later')), 1)


if __name__ == '__main__':
    unittest.main()
