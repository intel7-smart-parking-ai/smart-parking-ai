"""Claude 검수(2026-11-02) 수정 2건 회귀 검사: 브리핑 장비 판단 범위, 확인 중 칸 문구 귀속."""
import unittest
from unittest.mock import patch
from chatbot import engine as e, incidents
from chatbot.adapters import CachedAdapter, MockAdapter


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, 'data', CachedAdapter(MockAdapter())); p.start(); self.addCleanup(p.stop); e.reset_all()

    def briefing(self, lot, spot):
        e.data.set_alert(2, lot, spot); e.current_alert()
        b = incidents.list_public()[0]['briefing']
        e.data.set_alert(0); e.current_alert()
        return b

    def test_far_unrecognized_car_does_not_override_charge_zone(self):
        # 개발원주차장 미인식 차량은 빈 칸 중 가장 앞 칸(A-04)에 들어간다. 화재는 충전 구역 A-06(양옆 A-05만).
        from chatbot import config
        e.data.enter_unrecognized('A')
        far = [c['spot_no'] for c in e.data.list_cars('A') if c['car_id'] is None]
        self.assertTrue(far and all(not config.catalog.is_near('A', 6, s) for s in far))
        b = self.briefing('A', 6)
        self.assertIn('필요 가능성', b['equipment'])
        self.assertIn('전체 수 미확인', b['ev_count_text'])  # 주차장 전체 수는 여전히 미확인으로 표시

    def test_known_ev_neighbor_wins_over_unrecognized_at_fire_spot(self):
        e.data.enter_unrecognized('A')  # A-04, 옆 A-03은 전기차 23
        b = self.briefing('A', 4)
        self.assertIn('번호 미인식', b['vehicle'])
        self.assertIn('필요 가능성', b['equipment'])

    def test_unrecognized_neighbor_without_evidence_asks_to_prepare(self):
        e.data.enter_unrecognized('C')  # 김해공항(C) A-02. 화재 A-01: 옆 A-02 미인식, 충전 구역 아님, 전기차 근거 없음
        b = self.briefing('C', 1)
        self.assertIn('대비 필요', b['equipment'])

    def test_no_evidence_and_all_known_asks_site_check(self):
        b = self.briefing('A', 10)  # B-04 and its neighbors have no EV or charging bay.
        self.assertIn('현장에서 확인', b['equipment'])

    def mark_all_free_uncertain(self, lot):
        free = sorted(set(range(1, e.data.inner.capacity[lot] + 1)) - e.data.inner._occupied_spots(lot))
        for spot in free: e.data.set_uncertain(lot, spot, True)
        return len(free)

    def test_overall_query_names_the_lot_with_uncertain_spots(self):
        n = self.mark_all_free_uncertain('D')
        text = e.handle('s', text='빈자리 있어?')['text']
        self.assertIn(f'카메라 확인 중: 신세계백화점주차장 {n}칸', text)
        self.assertNotIn(f'(카메라 확인 중 {n}칸)', text)

    def test_lot_query_attaches_uncertain_to_that_lot_line(self):
        n = self.mark_all_free_uncertain('D')
        first = e.handle('s', text='D 자리 있어?')['text'].splitlines()[0]
        self.assertIn('신세계백화점주차장', first)
        self.assertIn(f'카메라 확인 중 {n}칸', first)


if __name__ == '__main__':
    unittest.main()
