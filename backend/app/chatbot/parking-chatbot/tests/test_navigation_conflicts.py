"""목적지 상태·알림 충돌과 동시 요청 회귀 검사."""
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch
from chatbot import engine as e, navigation as N
from chatbot.adapters import CachedAdapter, MockAdapter


class NavigationConflictTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, 'data', CachedAdapter(MockAdapter()))
        p.start(); self.addCleanup(p.stop); e.reset_all()

    def choose(self, lot):
        return e.handle('s', chip_id=N.go_id(lot))

    def full(self, lot):
        from chatbot.adapters import now
        with e.data.inner.lock:
            occupied = {c['spot_no'] for c in e.data.inner.cars.values() if c['lot_id'] == lot}
            for spot in range(1, e.data.inner.capacity[lot] + 1):
                if spot not in occupied:
                    e.data.inner.cars['test-'+lot+str(spot)] = {'lot_id':lot, 'spot_no':spot, 'entered_at':now(), 'paid_at':None}

    def empty(self, lot):
        with e.data.inner.lock:
            for no, car in list(e.data.inner.cars.items()):
                if car['lot_id'] == lot: e.data.inner.cars.pop(no)

    def test_rejected_switch_preserves_pending_destination_notice(self):
        self.choose('D'); self.full('D')
        first = e.updates('s')
        self.assertTrue(first['messages'])
        rejected = self.choose('B')
        self.assertEqual(rejected['destination']['lot_id'], 'D')
        retry = e.updates('s', cursor=0)
        self.assertTrue(any(m.get('meta', {}).get('notification') == 'destination' for m in retry['messages']))

    def test_recovery_removes_unacknowledged_obsolete_destination_notice(self):
        self.choose('D'); self.full('D'); e.updates('s')
        self.empty('D')
        result = e.updates('s', cursor=0)
        self.assertFalse(any(m.get('meta', {}).get('notification') == 'destination' for m in result['messages']))

    def test_latest_problem_replaces_unacknowledged_previous_problem(self):
        self.choose('D'); self.full('D'); e.updates('s')
        e.data.inner.restrictions['D'] = {'entry_allowed': False, 'restriction_reason': '점검 중'}
        result = e.updates('s', cursor=0)
        notices = [m for m in result['messages'] if m.get('meta', {}).get('notification') == 'destination']
        self.assertEqual(len(notices), 1)
        self.assertIn('이용할 수 없', notices[0]['text'])

    def test_missing_zone_does_not_hide_stale_state(self):
        snapshot = e.data.get_empty_spots()
        snapshot['lots']['D'].pop('zones'); snapshot['stale'] = True
        text, _, _, _ = N.query(snapshot, {'level': 0}, {'lot_id':'D', 'zone':'ev'})
        self.assertIn('확인할 수 없어요', text)

    def test_same_session_simultaneous_duplicate_selection(self):
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _:e.handle('s',chip_id='go:D',request_id='same'),range(60)))
        self.assertEqual(len({r['destination']['set_at'] for r in results}),1)
        self.assertEqual(sum(not r.get('meta',{}).get('replayed',False) for r in results),1)
        self.assertEqual(len({r['state_version'] for r in results}),60)

    def test_simultaneous_poll_and_selection_do_not_deadlock(self):
        def action(i):
            sid = 'stress-' + str(i % 8)
            if i % 3 == 0:return e.handle(sid,chip_id='go:D')
            if i % 3 == 1:return e.updates(sid)
            return e.handle(sid,chip_id='destination_cancel')
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(action, range(480)))
        self.assertEqual(len({r['state_version'] for r in results}),480)
        for i in range(8):
            sid = 'stress-' + str(i)
            e.handle(sid,chip_id='destination_cancel')
            self.assertIsNone(e.updates(sid)['destination'])
