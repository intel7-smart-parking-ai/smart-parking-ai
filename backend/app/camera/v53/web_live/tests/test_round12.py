from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import Mock, patch

from web_live.policy import to_events
from web_live.tests.test_contract import fixture
from web_live.transport import EventTransport
from web_live.live import LiveSession, prevent_idle_sleep


class DispatchAfterPauseTests(TestCase):
    def test_watchdog_after_host_pause_requires_fresh_page_and_registration(self):
        for page_returns in (True,False):
            with self.subTest(page_returns=page_returns):
                live=LiveSession(Mock(),transport_factory=Mock)
                live.hub=Mock()
                live.hub.snapshot.return_value={}
                live.last_client=0
                live.results['1']={'old':True}
                clock=[0.0]
                def advance(seconds):
                    if clock[0]==0:
                        clock[0]=241.6
                    elif seconds==.05:
                        self.assertFalse(live.results)
                        live.pipeline.invalidate.assert_called_once()
                        if page_returns:live.last_client=clock[0]
                        clock[0]+=11
                    else:live.stop_event.set()
                with patch('web_live.live.time') as timing, patch.object(live.stop_event,'wait',side_effect=advance):
                    timing.monotonic.side_effect=lambda:clock[0]
                    live._infer_frames()
                self.assertEqual(live.hub.snapshot.call_count,2 if page_returns else 1)
                self.assertTrue(live.stop_event.is_set())
                self.assertEqual(live.watchdog_stopped,not page_returns)
                self.assertEqual(live.last_pause_seconds,241.6)

    def test_windows_idle_sleep_request_is_released_on_failure(self):
        api=Mock(return_value=1)
        with patch('web_live.live.sys.platform','win32'), patch('ctypes.windll',create=True) as dll:
            dll.kernel32.SetThreadExecutionState=api
            with self.assertRaises(RuntimeError), prevent_idle_sleep() as enabled:
                self.assertTrue(enabled)
                raise RuntimeError('end capture')
        self.assertEqual([c.args[0] for c in api.call_args_list],[0x80000001,0x80000000])

    def test_expired_during_validator_never_reaches_socket(self):
        sender=EventTransport()
        self.addCleanup(sender.close)
        result,packet=fixture()
        event=to_events(result,packet,'camera_a')[0]
        stamp=datetime.fromisoformat(event['observed_at'])
        now=[stamp]
        def slow_valid(_):
            now[0]+=timedelta(seconds=241.6)
            return True
        with patch('web_live.transport.datetime',wraps=datetime) as clock, patch('web_live.transport.urllib.request.build_opener') as opener:
            clock.now.side_effect=lambda tz:now[0]
            outcome=sender.send_one(event,slow_valid,'http://127.0.0.1','test',sender.generation)
            opener.return_value.open.assert_not_called()
            self.assertEqual(outcome,'withheld_or_expired')

    def test_cancel_during_validator_never_reaches_socket(self):
        sender=EventTransport()
        self.addCleanup(sender.close)
        result,packet=fixture()
        event=to_events(result,packet,'camera_a')[0]
        def cancel(_):
            sender.cancel()
            return True
        with patch('web_live.transport.urllib.request.build_opener') as opener:
            outcome=sender.send_one(event,cancel,'http://127.0.0.1','test',sender.generation)
            opener.return_value.open.assert_not_called()
            self.assertEqual(outcome,'withheld_or_expired')
