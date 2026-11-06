"""11/06 화면 피드백 회귀: 주차장 미지정 빈자리, 다른 이름의 주차장, 데이터 없는 시설·거리·혼잡 예측 질문."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from chatbot import catalog as catalog_module, config, engine as e, info_gaps, parser
from chatbot.adapters import CachedAdapter, MockAdapter
from chatbot.catalog import Catalog


class ParserTests(unittest.TestCase):
    def test_other_names_for_lots(self):
        for text, lot in [('기차 주차장', 'B'), ('기차역 빈자리', 'B'), ('KTX 주차장', 'B'),
                          ('공항 주차장', 'C'), ('김해 공항 주차장', 'C'), ('국제공항 주차장 빈자리', 'C'),
                          ('센텀시티 주차장', 'D'), ('신세계 백화점 빈자리', 'D')]:
            with self.subTest(text=text):
                p = parser.parse_rules(text)
                self.assertEqual((p.intent, p.params.get('lot_id')), ('get_empty_spots', lot))

    def test_screenshot_questions_no_longer_unknown(self):
        cases = [
            ('비행기가 2시20분 출발인데 3시간전에 도착해도 혼잡하지 않을까?', 'get_empty_spots', 'C', 'forecast'),
            ('신세계백화점 엘리베이터 몇개야', 'help', 'D', 'facility'),
            ('김해 공항 주차장에서 국제선 청사로 가는길이 가장 가까운 주차구역이 어디야?', 'get_empty_spots', 'C', 'nearest'),
            ('화장실 어디야?', 'help', None, 'facility'),
            ('내일 오후 3시에 신세계 주차장 붐빌까?', 'get_empty_spots', 'D', 'forecast'),
        ]
        for text, intent, lot, gap in cases:
            with self.subTest(text=text):
                p = parser.parse_rules(text)
                self.assertEqual((p.intent, p.params.get('lot_id'), p.params.get('info_gap')), (intent, lot, gap))
                self.assertNotIn('car_id', p.params)

    def test_current_crowding_with_lot_is_plain_vacancy(self):
        p = parser.parse_rules('부산역 지금 붐벼?')
        self.assertEqual((p.intent, p.params), ('get_empty_spots', {'lot_id': 'B'}))

    def test_supported_and_safety_routes_keep_priority(self):
        for text, intent in [('공항 주차장 화재 경보 있어?', 'get_alerts'), ('엘리베이터 쪽으로 대피해야 해?', 'evacuation_guide'),
                             ('신세계 주차장 요금 얼마야?', 'get_fee'), ('화장실 가야 하는데 관리자 불러줘', 'call_admin'),
                             ('출구 차단기가 안 열려요', 'exit_help'), ('23번 차 어디 있어?', 'find_my_car')]:
            with self.subTest(text=text):
                self.assertEqual(parser.parse_rules(text).intent, intent)

    def test_unrelated_question_stays_unknown(self):
        self.assertEqual(parser.parse_rules('오늘 날씨 어때?').intent, 'unknown')

    def test_guide_keys_match_topics(self):
        self.assertEqual(catalog_module.GUIDE_KEYS, {key for key, _, _ in info_gaps.TOPICS})


class EngineTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(e, 'data', CachedAdapter(MockAdapter())); p.start(); self.addCleanup(p.stop); e.reset_all()
        self.addCleanup(config.catalog.configure, deepcopy(config.catalog.settings))

    def test_vacancy_without_lot_summarises_all_and_offers_choice(self):
        for kwargs in ({'text': '빈자리 몇 개 있어?'}, {'chip_id': 'empty_all'}):
            with self.subTest(**kwargs):
                r = e.handle('s', **kwargs)
                self.assertIn('전체 주차장 빈자리는 총', r['text'])
                for key in config.catalog.ids():
                    self.assertIn(config.catalog.name(key), r['text'])
                self.assertTrue(r['text'].endswith('어느 주차장을 안내해 드릴까요?'))
                self.assertEqual([c['id'] for c in r['chips']], ['lot_' + k for k in config.catalog.ids()][:4])
                self.assertEqual(r['card']['type'], 'empty_spots')

    def test_total_matches_card(self):
        r = e.handle('s', text='빈자리 몇 개 있어?')
        total = sum(v['display_empty'] or 0 for v in r['card']['data']['lots'].values() if v['status_reason'] in (None, '만차'))
        self.assertIn(f'총 {total}자리', r['text'])

    def test_named_lot_answer_unchanged(self):
        r = e.handle('s', text='김해공항 주차장 빈자리')
        self.assertTrue(r['text'].startswith('김해공항 주차장에 '))

    def test_forecast_is_honest_and_shows_current_data(self):
        r = e.handle('s', text='비행기가 2시20분 출발인데 3시간전에 도착해도 혼잡하지 않을까?')
        self.assertIn('혼잡 예측은 아직 제공하지 않아요', r['text'])
        self.assertIn('지금 기준으로 김해공항 주차장', r['text'])
        self.assertEqual(r['card']['type'], 'empty_spots')

    def test_nearest_without_guide_does_not_invent_a_zone(self):
        r = e.handle('s', text='김해 공항 주차장에서 국제선 청사로 가는길이 가장 가까운 주차구역이 어디야?')
        self.assertIn('국제선 청사까지의 거리 정보가 아직 등록되지 않아서', r['text'])
        self.assertNotIn('잘 이해하지 못했어요', r['text'])

    def test_facility_without_guide_says_unregistered(self):
        r = e.handle('s', text='신세계백화점 엘리베이터 몇개야')
        self.assertIn('신세계백화점주차장의 엘리베이터 정보는 아직 챗봇에 등록되지 않았어요', r['text'])
        self.assertIsNone(r['card'])
        self.assertEqual([c['id'] for c in r['chips']], ['lot_D', 'empty_all', 'find_car', 'call_admin'])

    def test_configured_guide_is_answered_verbatim(self):
        doc = deepcopy(config.catalog.settings)
        lot = next(x for x in doc['lots'] if x['lot_id'] == 'D')
        lot['guide'] = {'elevator': '주차장 각 층 중앙에 2대씩 있어요.'}
        c = next(x for x in doc['lots'] if x['lot_id'] == 'C')
        c['guide'] = {'terminal': 'B 구역이 국제선 청사 출입구와 가장 가까워요.'}
        config.catalog.configure(doc)
        r = e.handle('s', text='신세계백화점 엘리베이터 몇개야')
        self.assertEqual(r['text'], '신세계백화점주차장 엘리베이터 안내: 주차장 각 층 중앙에 2대씩 있어요.')
        r = e.handle('s2', text='김해공항 주차장 국제선 청사랑 가장 가까운 구역 어디야?')
        self.assertIn('B 구역이 국제선 청사 출입구와 가장 가까워요.', r['text'])

    def test_invalid_guide_is_rejected(self):
        for guide in ([], {'elevater': 'x'}, {'elevator': 3}):
            doc = deepcopy(config.catalog.settings); doc['lots'][0]['guide'] = guide
            with self.subTest(guide=guide), self.assertRaises(ValueError) as ctx:
                Catalog(doc)
            self.assertIn('A 주차장 guide', str(ctx.exception))

    def test_new_routes_do_not_call_a_model(self):
        with patch('chatbot.llm.classify', side_effect=AssertionError('unexpected model route')), \
             patch('chatbot.llm_router.route', side_effect=AssertionError('unexpected model route')):
            for text in ['신세계백화점 엘리베이터 몇개야', '비행기 출발 3시간 전에 가면 공항 주차장 혼잡할까?', '기차 주차장']:
                with self.subTest(text=text):
                    self.assertNotEqual(parser.parse(text).intent, 'unknown')


if __name__ == '__main__':
    unittest.main()
