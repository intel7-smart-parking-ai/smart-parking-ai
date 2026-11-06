import csv
from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import json
import time
import unittest
from unittest.mock import patch
from chatbot import engine as e,config,navigation as N
from chatbot.adapters import CachedAdapter,MockAdapter,ApiAdapter,DataUnavailable,iso,now
from chatbot.parser import parse_rules


class NavigationTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(e,'data',CachedAdapter(MockAdapter()));p.start();self.addCleanup(p.stop);e.reset_all()

    def empty(self,lot):
        with e.data.inner.lock:
            for no,c in list(e.data.inner.cars.items()):
                if c['lot_id']==lot:e.data.inner.cars.pop(no)

    def full(self,lot):
        with e.data.inner.lock:
            occupied={c['spot_no']for c in e.data.inner.cars.values()if c['lot_id']==lot}
            for spot in range(1,e.data.inner.capacity[lot]+1):
                if spot not in occupied:e.data.inner.cars['test-'+lot+str(spot)]={'lot_id':lot,'spot_no':spot,'entry_at':now(),'paid_at':None}

    def choose(self,lot='D',zone=None):
        return e.handle('s',chip_id=N.go_id(lot,zone))

    def test_development_questions(self):
        with Path(__file__).with_name('questions_navigation_dev.csv').open(encoding='utf-8')as f:
            for row in csv.DictReader(f):
                with self.subTest(q=row['question']):
                    r=parse_rules(row['question'])
                    self.assertEqual((r.intent,r.params.get('lot_id',''),r.params.get('car_id',''),r.params.get('zone','')),
                                     tuple(row[k]for k in ['intent','lot_id','car_id','zone']))

    def test_four_lots_metadata_and_missing_vehicle(self):
        d=e.data.get_empty_spots()
        self.assertEqual(list(d['lots']),['A','B','C','D'])
        self.assertEqual(d['lots']['B']['empty'],0)
        self.assertEqual(d['lots']['D']['kind'],'virtual')
        self.assertIsNone(e.data.find_car('99'))
        for v in d['lots'].values():
            self.assertEqual(sum(z['total']for z in v['zones'].values()),v['total'])
            self.assertEqual(sum(z['empty']for z in v['zones'].values()),v['empty'])

    def test_full_destination_rejected_with_two_alternatives(self):
        r=self.choose('B')
        self.assertIsNone(r['destination'])
        self.assertIn('만차',r['text'])
        self.assertEqual([x['id']for x in r['chips']][:2],['go:C','go:D'])  # 빈자리 많은 순 (D-57: C 김해공항 64칸 > D)

    def test_selected_lot_full_edge_only_once(self):
        self.empty('B');self.choose('B');self.full('B')
        first=e.updates('s');second=e.updates('s',cursor=first['cursor'])
        self.assertEqual(sum(m['meta']['notification']=='destination'for m in first['messages']),1)
        self.assertFalse(second['messages'])
        retry=e.updates('s')
        self.assertFalse(retry['messages']) # 이미 확인한 알림은 오래된 커서로 다시 실행하지 않는다

    def test_selected_lot_alarm_excludes_it_from_alternatives(self):
        self.choose();e.data.set_alert(2,'D',3)
        r=e.updates('s')
        m=next(x for x in r['messages']if x['meta']['notification']=='destination')
        self.assertNotIn('go:D',[x['id']for x in m['actions']])
        self.assertIn('go:C',[x['id']for x in m['actions']])
        self.assertEqual(r['banner']['level'],2)

    def test_no_available_candidates_offers_lookup_and_admin(self):
        for lot in e.data.inner.capacity: self.full(lot)
        r = self.choose('D')
        self.assertIsNone(r['destination'])
        self.assertIn('empty_all',[x['id']for x in r['chips']])
        self.assertIn('call_admin',[x['id']for x in r['chips']])

    def test_arrival_stops_monitoring(self):
        self.choose();e.handle('s',text='33번 차 위치');r=e.handle('s',text='네')
        self.assertIsNone(r['destination']);self.full('D')
        self.assertFalse(any(m['meta']['notification']=='destination'for m in e.updates('s')['messages']))

    def test_old_destination_button_rechecks_capacity(self):
        r=e.handle('s',text='D 빈자리');self.assertIn('go:D',[c['id']for c in r['chips']])
        self.full('D');r=self.choose('D')
        self.assertIsNone(r['destination']);self.assertIn('만차',r['text'])

    def test_destination_ttl_and_cancel(self):
        self.choose('D')
        e.get_session('s').destination_expires=time.monotonic()-1
        self.assertIsNone(e.updates('s')['destination'])
        self.choose('D');r=e.handle('s',chip_id='destination_cancel')
        self.assertIsNone(r['destination'])

    def test_zone_context_example_and_clear_on_other_intent(self):
        e.handle('s',text='B 충전 구역 자리 있어?')
        r=e.handle('s',text='그럼 C는?');self.assertEqual((r['card']['data']['focus'],r['card']['data']['zone']),('C','ev'))
        r=e.handle('s',text='일반 구역은?');self.assertEqual((r['card']['data']['focus'],r['card']['data']['zone']),('C','general'))
        e.handle('s',chip_id='fee_rule');r=e.handle('s',text='그럼 D는?');self.assertIsNone(r['card']['data']['zone'])

    def test_context_timeout_and_unrelated_unknown(self):
        e.handle('s',text='C 충전 구역 자리')
        e.get_session('s').query_context_at-=config.QUERY_CONTEXT_TTL_SEC+1
        r=e.handle('s',text='그럼 D는?');self.assertIsNone(r['card']['data']['zone'])
        r=e.handle('s',text='이디야 근처');self.assertEqual(r['meta']['intent'],'unknown')

    def test_zone_destination_keeps_condition_and_zone_full_triggers_notice(self):
        self.choose('D','ev')
        for i,spot in enumerate(config.catalog.zone_spots(None,'D')['ev']):e.data.inner.cars[str(60+i)]={'lot_id':'D','spot_no':spot,'entry_at':now(),'paid_at':None}
        r=e.updates('s');m=next(x for x in r['messages']if x['meta']['notification']=='destination')
        self.assertIn('go:C:ev',[c['id']for c in m['actions']]);self.assertNotIn('go:A',[c['id']for c in m['actions']])

    def test_missing_zones_is_unknown_with_total_fallback(self):
        d=e.data.inner.get_empty_spots()
        for v in d['lots'].values():v.pop('zones')
        with patch.object(e.data.inner,'get_empty_spots',return_value=d):
            r=e.handle('s',text='D 충전 자리')
            self.assertIn('구역별 정보는 아직 제공되지 않아요',r['text'])
            self.assertIsNone(r['card']['data']['lots']['D']['display_empty'])
            self.assertIsNone(self.choose('D','ev')['destination'])

    def test_operating_restriction_filters_query_and_destination(self):
        e.data.inner.restrictions['D']={'entry_allowed':False,'restriction_reason':'점검 중'}
        r=self.choose('D');self.assertIsNone(r['destination']);self.assertIn('이용할 수 없',r['text']);self.assertIn('점검 중',r['text'])
        self.assertNotIn('go:D',[c['id']for c in r['chips']])

    def test_d_wait_alert_incident_and_admin_summary(self):
        self.full('D');e.data.exit_car('44')
        e.data.set_alert(2,'D',5)
        r=e.handle('s',chip_id='call_admin')
        self.assertIn('Lv2 신세계백화점주차장',r['card']['data']['summary'])
        self.assertEqual(r['card']['data']['incident_id'],e.current_alert()['incident_id'])



    def test_backend_catalog_priority_accepts_new_id_and_capacity(self):
        old=deepcopy(config.catalog.settings);self.addCleanup(config.catalog.configure,old)
        adapter=CachedAdapter(ApiAdapter('http://127.0.0.1:1'))
        payload={'lots':{'P9':{'name':'서쪽 주차장','kind':'virtual','total':3,'empty':2}},'updated_at':iso(now())}
        with patch.object(adapter.inner,'get_empty_spots',return_value=payload):adapter.get_empty_spots()
        self.assertEqual(config.catalog.ids(),['P9'])
        self.assertEqual(config.catalog.lot('P9')['capacity'],3)
        self.assertEqual(parse_rules('P9로 갈게').params['lot_id'],'P9')

    def test_three_four_and_renamed_lots_via_settings_only(self):
        settings=deepcopy(config.catalog.settings)
        for size in (3,4):
            with self.subTest(size=size),tempfile.TemporaryDirectory()as directory:
                doc=deepcopy(settings);doc['lots']=doc['lots'][:size];doc['lots'][-1]['name']='새 이름 주차장';doc['lots'][-1]['spaces']=doc['lots'][-1]['spaces'][:5];doc['lots'][-1].pop('zone_layout',None)
                path=Path(directory)/'lots.json';path.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
                code="from chatbot import engine as e,config; from chatbot.parser import parse_rules; e.reset_all(); d=e.data.get_empty_spots(); assert len(d['lots'])=="+str(size)+"; assert d['lots'][config.catalog.ids()[-1]]['total']==5; assert e.handle('s',chip_id='lot_'+config.catalog.ids()[-1])['card']['type']=='empty_spots'"
                import os
                r=subprocess.run([sys.executable,'-c',code],env={**os.environ,'PARKING_SETTINGS_PATH':str(path),'PYTHONIOENCODING':'utf-8'},capture_output=True,text=True,encoding='utf-8',errors='replace')
                self.assertEqual(r.returncode,0,r.stderr)

    def test_arrival_ends_monitor_even_when_occupancy_is_unavailable(self):
        self.choose();e.get_session('s').car_id='33'
        with patch.object(e.data,'get_empty_spots',side_effect=DataUnavailable()):
            self.assertIsNone(e.updates('s')['destination'])

    def test_unavailable_monitor_notifies_once_without_fabricated_counts(self):
        self.choose()
        with patch.object(e.data,'get_empty_spots',side_effect=DataUnavailable()):
            first=e.updates('s');second=e.updates('s',cursor=first['cursor'])
        message=next(m for m in first['messages']if m['meta']['notification']=='destination')
        self.assertIn('확인하지 못했어요',message['text']);self.assertIsNone(message['card'])
        self.assertFalse(second['messages']);self.assertIsNotNone(second['destination'])

    def test_declined_destination_does_not_select_it(self):
        for phrase in ('D로 안내하지 말아줘','D로 가지 마세요','D로 안 갈래'):
            with self.subTest(phrase=phrase):self.assertIsNone(e.handle('s',text=phrase)['destination'])

    def test_ambiguous_lots_do_not_guess_and_explicit_replacement_wins(self):
        self.assertIsNone(e.handle('s',text='A나 D로 갈게')['destination'])
        self.assertEqual(e.handle('s',text='A 말고 D로 갈게')['destination']['lot_id'],'D')

    def test_selected_lot_operating_restriction_triggers_one_notice(self):
        self.choose();e.data.inner.restrictions['D']={'entry_allowed':False,'restriction_reason':'점검 중'}
        first=e.updates('s');second=e.updates('s',cursor=first['cursor'])
        m=next(x for x in first['messages']if x['meta']['notification']=='destination')
        self.assertIn('이용할 수 없',m['text']);self.assertNotIn('경보',m['text']);self.assertFalse(second['messages'])

    def test_stale_data_does_not_set_destination(self):
        d={**e.data.get_empty_spots(),'stale':True}
        with patch.object(e.data,'get_empty_spots',return_value=d):self.assertIsNone(self.choose()['destination'])

    def test_warning_copy_is_contextual_and_cancel_copy_is_consistent(self):
        r=e.handle('s',text='호출 하지 말아 주세요');self.assertEqual(r['text'],'알겠어요. 새 관리자 호출은 하지 않을게요.')


    def test_reserved_questions_after_first_evaluation(self):
        with Path(__file__).with_name('questions_navigation_holdout.csv').open(encoding='utf-8')as f:
            for row in csv.DictReader(f):
                with self.subTest(q=row['question']):
                    r=parse_rules(row['question'])
                    self.assertEqual((r.intent,r.params.get('lot_id',''),r.params.get('car_id',''),r.params.get('zone','')),
                                     tuple(row[k]for k in ['intent','lot_id','car_id','zone']))

    def test_invalid_backend_catalog_is_not_adopted(self):
        old=deepcopy(config.catalog.settings);self.addCleanup(config.catalog.configure,old)
        adapter=CachedAdapter(ApiAdapter('http://127.0.0.1:1'))
        payload={'lots':{'P9':{'total':3,'empty':2,'entry_allowed':'false'}},'updated_at':iso(now())}
        with patch.object(adapter.inner,'get_empty_spots',return_value=payload):
            with self.assertRaises(DataUnavailable):adapter.get_empty_spots()
        self.assertEqual(config.catalog.ids(),['A','B','C','D'])

    def test_backend_catalog_bootstraps_alert_and_car_validation(self):
        old=deepcopy(config.catalog.settings);self.addCleanup(config.catalog.configure,old)
        adapter=CachedAdapter(ApiAdapter('http://127.0.0.1:1'))
        payload={'lots':{'P9':{'name':'서쪽 주차장','kind':'virtual','total':3,'empty':2}},'updated_at':iso(now())}
        alert={'level':2,'version':1,'lot_id':'P9','spot_no':3,'started_at':iso(now())}
        car={'car_id':'33','lot_id':'P9','spot_no':3,'capacity':3,'entry_at':iso(now()),'updated_at':iso(now())}
        with patch.object(adapter.inner,'get_empty_spots',return_value=payload),patch.object(adapter.inner,'get_alert',return_value=alert),patch.object(adapter.inner,'find_car',return_value=car):
            self.assertEqual(adapter.get_alert()['lot_id'],'P9')
            self.assertEqual(adapter.find_car('33')['capacity'],3)
