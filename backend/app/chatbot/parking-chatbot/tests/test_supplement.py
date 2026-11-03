"""보완 기능의 실패·안전 경계. 외부 서비스 호출 없이 검사한다."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from chatbot import config, engine as e, incidents, templates as T, briefing
from chatbot.adapters import CachedAdapter, MockAdapter, DataUnavailable
from chatbot.catalog import Catalog


class SupplementTests(unittest.TestCase):
    def setUp(self):
        p=patch.object(e,'data',CachedAdapter(MockAdapter()));p.start();self.addCleanup(p.stop);e.reset_all()

    def test_lv2_same_other_and_unknown_vehicle(self):
        a={'level':2,'lot_id':'A','spot_no':6}
        for car in (None,{'lot_id':'A','spot_no':7},{'lot_id':'B','spot_no':4}):
            text=T.alert_near(a,car)
            self.assertIn('들어오지',text);self.assertIn('옮기',text)
            self.assertLessEqual(len(text.splitlines()),2)

    def test_every_lv2_response_carries_safety_and_safe_actions(self):
        e.data.set_alert(2,'A',6)
        for chip in ('help','fee_rule','call_admin','empty_all','alert_where','evacuate'):
            r=e.handle(chip,chip_id=chip)
            self.assertIn('들어오지',r['safety']['guidance'])
            self.assertIn('옮기',r['safety']['guidance'])
            self.assertFalse({'find_car','fee','pay'} & {c['id']for c in r['chips']})
        for m in e.updates('poll')['messages']:
            self.assertIn('들어오지',m['safety']['guidance'])

    def test_briefing_charge_zone_and_electric_adjacent(self):
        e.data.set_alert(2,'C',2);e.current_alert()
        b=incidents.list_public()[0]['briefing']
        self.assertIn('18번 · 전기차',' / '.join(b['adjacent']))
        self.assertIn('질식소화덮개',b['equipment'])
        e.data.set_alert(2,'A',7);e.current_alert()
        self.assertIn('충전',incidents.list_public()[0]['briefing']['location'])

    def test_briefing_unknown_is_not_zero(self):
        with patch.object(e.data,'list_cars',side_effect=DataUnavailable()):
            e.data.set_alert(2,'A',6);e.current_alert()
        b=incidents.list_public()[0]['briefing']
        self.assertIsNone(b['ev_count']);self.assertIn('확인 불가',b['vehicle'])
        self.assertNotIn('0대',b['ev_count_text']);self.assertIn('미확인',b['facility'])
        self.assertIn('대비 필요',b['equipment'])

    def test_briefing_is_present_in_final_report_and_copy_text(self):
        e.data.set_alert(2,'A',6);e.current_alert();e.data.set_alert(0);e.current_alert()
        report=incidents.confirm(1)
        self.assertIn('시연용 모의 상황',report['briefing']['text'])
        self.assertIn(report['briefing']['text'],report['report'])

    def test_catalog_rejects_invalid_supplement_settings(self):
        for change in ('bad_spot','duplicate_spot','bad_vehicle','duplicate_vehicle','facility','height','exits'):
            doc=deepcopy(config.catalog.settings)
            if change=='bad_spot':doc['lots'][0]['zone_layout']={'ev':[99]}
            if change=='duplicate_spot':doc['lots'][0]['zone_layout']={'ev':[1],'disabled':[1]}
            if change=='bad_vehicle':doc['ev_vehicles']=['99']
            if change=='duplicate_vehicle':doc['ev_vehicles']=['03','03']
            if change=='facility':doc['lots'][0]['facility']={'sprinkler':'no'}
            if change=='height':doc['lots'][0]['facility']={'height_limit_m':float('nan')}
            if change=='exits':doc['lots'][0]['exits']=['']
            with self.subTest(change=change),self.assertRaises(ValueError):Catalog(doc)

    def test_zone_layout_overrides_auto_assignment(self):
        doc=deepcopy(config.catalog.settings);doc['lots'][0]['zone_layout']={'ev':[6],'disabled':[1]}
        c=Catalog(doc)
        self.assertEqual(c.zone_spots(8,'A')['ev'],[6])
        self.assertEqual(c.zone_spots(8,'A')['disabled'],[1])
        self.assertNotIn(6,c.zone_spots(8,'A')['normal'])

    def test_uncertain_is_excluded_from_empty_and_destination(self):
        e.data.fill_lot('D');e.data.clear_demo('D')
        free=sorted(set(range(1,9))-e.data.inner._occupied_spots('D'))
        for spot in free:e.data.set_uncertain('D',spot,True)
        d=e.data.get_empty_spots()['lots']['D']
        self.assertEqual(d['empty'],0);self.assertEqual(d['uncertain'],len(free))
        r=e.handle('s',text='D 빈자리')
        self.assertIn('확인 중',r['text']);self.assertNotIn('go:D',[x['id']for x in r['chips']])
        self.assertIsNone(e.handle('s',chip_id='go:D')['destination'])

    def test_uncertain_contract_optional_and_invalid(self):
        from chatbot.contracts import validate, ContractError
        d=e.data.get_empty_spots()
        for lot in d['lots'].values():
            lot.pop('uncertain')
            for zone in lot['zones'].values():zone.pop('uncertain')
        validate('empty',d)
        d['lots']['A']['uncertain']=8
        with self.assertRaises(ContractError):validate('empty',d)
        for value in (-1,True,1.5):
            d['lots']['A']['uncertain']=value
            with self.assertRaises(ContractError):validate('empty',d)

    def test_uncertain_cannot_overlap_vehicle_or_accept_invalid_type(self):
        for spot,flag in ((2,True),(99,True),(4,'false')):
            with self.subTest(spot=spot,flag=flag),self.assertRaises(ValueError):e.data.set_uncertain('A',spot,flag)

    def suggest(self,count=1):
        for _ in range(count):self.assertTrue(e.data.enter_unrecognized('D')['ok'])
        e.handle('s',text='24번 차 위치')
        return e.handle('s',text='네')

    def test_unrecognized_one_candidate_two_actions_and_no_auto_link(self):
        r=self.suggest()
        self.assertEqual(r['card']['type'],'unrecognized_candidates')
        self.assertEqual(len(r['chips']),2)
        token=r['chips'][0]['id']
        selected=e.handle('s',chip_id=token)
        self.assertIn('후보 위치',selected['text'])
        self.assertEqual(e.get_session('s').vehicle_no,'24')
        self.assertIsNone(e.get_session('s').pending)

    def test_unrecognized_candidate_cannot_pay_or_query_fees(self):
        r=self.suggest();e.handle('s',chip_id=r['chips'][0]['id'])
        with patch.object(e.data,'pay',wraps=e.data.pay)as pay,patch.object(e.data,'get_fee',wraps=e.data.get_fee)as fee:
            for chip in ('fee','pay','exit_issue'):
                result=e.handle('s',chip_id=chip)
                self.assertIn('관리자 확인 후',result['text'])
                self.assertNotEqual(result['flow'],'confirm_action')
            pay.assert_not_called();fee.assert_not_called()

    def test_unrecognized_four_candidates_asks_manager(self):
        r=self.suggest(4)
        self.assertIn('4대',r['text'])
        self.assertNotIn('candidate:',str(r['chips']))
        self.assertIn('candidate_admin',[c['id']for c in r['chips']])
        call=e.handle('s',chip_id='candidate_admin')
        self.assertIn('번호 미인식 후보 확인 요청(D-',call['card']['data']['summary'])

    def test_unrecognized_latest_first_and_max_three(self):
        r=self.suggest(3)
        from datetime import timedelta
        from chatbot.adapters import now,iso
        e.data.inner.unrecognized[0]['entered_at']=iso(now()-timedelta(minutes=10))
        r=e.handle('s',chip_id='find_car')
        self.assertNotEqual(r['card']['data']['cars'][0]['spot_no'],e.data.inner.unrecognized[0]['spot_no'])
        self.assertEqual(len(r['chips']),4)

    def test_unrecognized_candidate_ownership_expiry_and_changed_location(self):
        r=self.suggest();token=r['chips'][0]['id']
        self.assertIn('만료',e.handle('other',chip_id=token)['text'])
        e.data.inner.unrecognized.clear()
        self.assertIn('바뀌',e.handle('s',chip_id=token)['text'])
        r=e.handle('s',chip_id='find_car');self.assertIn('입차 기록이 없',r['text'])

    def test_unrecognized_candidate_admin_summary_and_report_accept_null(self):
        r=self.suggest();e.handle('s',chip_id=r['chips'][0]['id'])
        call=e.handle('s',chip_id='candidate_admin')
        self.assertIn('번호 미인식 후보 확인 요청(D-',call['card']['data']['summary'])
        e.data.set_alert(2,'D',1);e.current_alert()
        report=incidents.list_public()[0]
        self.assertIn('번호 미인식',report['report'])
        self.assertIn('미확인',report['briefing']['ev_count_text'])
        self.assertEqual(len(report['affected']),3)

    def test_unrecognized_mock_filler_is_not_a_candidate(self):
        e.data.enter_car('D');e.data.fill_lot('D')
        e.handle('s',text='24번 차 위치');r=e.handle('s',text='네')
        self.assertIsNone(r['card']);self.assertIn('입차 기록이 없',r['text'])

    def test_unrecognized_candidate_is_not_shown_during_lv2(self):
        r=self.suggest();token=r['chips'][0]['id'];e.data.set_alert(2,'D',1)
        r=e.handle('s',chip_id=token)
        self.assertIn('들어오지',r['text']);self.assertIsNone(r['card'])

    def test_audit_input_rejected_before_retention(self):
        for field in ('text','chip_id','event','confirmation_token','runtime_id'):
            with self.subTest(field=field),self.assertRaises(ValueError):e.handle('long',**{field:'x'*1048576})
        self.assertFalse(e.LOG);self.assertFalse(e.get_session('long').requests)
        e.handle('s',text='x'*200,request_id='valid')
        self.assertLessEqual(len(e.LOG[-1]['text']),200)

    def test_audit_mixed_case_id_preserved(self):
        from chatbot.parser import parse
        catalog=Catalog(config.catalog.settings)
        catalog.adopt({'East':{'name':'동관','kind':'virtual','total':8}})
        with patch.object(config,'catalog',catalog),patch('chatbot.llm.classify',return_value={'intent':'set_destination','params':{'lot_id':'East'}}):
            self.assertEqual(parse('동쪽 건물로 데려다줘').params['lot_id'],'East')

    def test_audit_vehicle_change_preserves_number_and_requires_confirmation(self):
        e.get_session('s').vehicle_no='07'
        r=e.handle('s',text='8번 차로 바꿔')
        self.assertEqual(r['flow'],'confirm_vehicle');self.assertIn('08번',r['text'])
        self.assertIsNone(e.get_session('s').vehicle_no)
        e.handle('s',text='네');self.assertEqual(e.get_session('s').vehicle_no,'08')

    def test_audit_negative_manager_synonym_never_calls_model(self):
        with patch('chatbot.llm.classify')as classify:
            r=e.handle('s',text='담당자를 보내지 마세요')
            classify.assert_not_called()
        self.assertIn('하지 않을게요',r['text']);self.assertFalse(e.admin_calls())

    def test_audit_nonfinite_config_fails_at_startup(self):
        import os,subprocess,sys
        from pathlib import Path
        root=Path(__file__).resolve().parent.parent
        for key,value in (('REQUEST_BUDGET_SEC','nan'),('REQUEST_BUDGET_SEC','inf'),('LLM_TIMEOUT_SEC','nan'),('SESSION_IDLE_SEC','-1'),('MAX_SESSIONS','0')):
            proc=subprocess.run([sys.executable,'-c','from chatbot import config'],cwd=root,env={**os.environ,key:value,'PYTHONIOENCODING':'utf-8'},capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=5)
            self.assertNotEqual(proc.returncode,0);self.assertIn(key,proc.stderr)

    def test_unknown_nearby_vehicles_are_identified_by_spot(self):
        e.data.enter_unrecognized('D');e.data.enter_unrecognized('D')
        e.data.set_alert(2,'D',1);e.current_alert()
        report=incidents.list_public()[0]
        self.assertIn(1,report['near_spots']);self.assertNotIn(3,report['near_spots'])



try:
    from fastapi.testclient import TestClient
    from server_fastapi import app
    HAS_FASTAPI=True
except ImportError:
    HAS_FASTAPI=False


@unittest.skipUnless(HAS_FASTAPI,'선택 FastAPI/httpx 미설치')
class SupplementServerTests(unittest.TestCase):
    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        from server_dev import Handler
        p=patch.object(e,'data',CachedAdapter(MockAdapter()));p.start();self.addCleanup(p.stop);e.reset_all()
        self.client=TestClient(app)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2)
        self.client.close()

    def post(self,path,body):
        import json,urllib.request,urllib.error
        req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/api/v1/chat'+path,
            data=json.dumps(body).encode('utf-8'),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=3)as r:dev=r.status
        except urllib.error.HTTPError as exc:dev=exc.code
        return dev,self.client.post('/api/v1/chat'+path,json=body).status_code

    def test_both_servers_limit_input_and_new_demo_types(self):
        self.assertTrue(all(400<=s<500 for s in self.post('',{'session_id':'long','text':'x'*201})))
        self.assertEqual(self.client.post('/api/v1/chat',json={'session_id':'large','text':'x'*1048576}).status_code,422)
        self.assertFalse(e.LOG)
        for body in ({'lot_id':'D','spot_no':1,'uncertain':'false'},{'lot_id':'D','spot_no':True,'uncertain':True}, {'lot_id':'D','spot_no':999,'uncertain':True}):
            self.assertTrue(all(400<=s<500 for s in self.post('/demo/uncertain',body)))
        self.assertEqual(self.post('/demo/uncertain',{'lot_id':'D','spot_no':1,'uncertain':True}),(200,200))
        self.assertEqual(self.post('/demo/unrecognized',{'lot_id':'D'}),(200,200))

    def test_both_servers_keep_new_demo_endpoints_privileged(self):
        for flag, token, expected in ((False,'',404),(True,'test-only',401)):
            with patch.object(config,'DEMO_ENABLED',flag),patch.object(config,'ADMIN_TOKEN',token):
                self.assertEqual(self.post('/demo/uncertain',{'lot_id':'D','spot_no':1,'uncertain':True}),(expected,expected))
                self.assertEqual(self.post('/demo/unrecognized',{'lot_id':'D'}),(expected,expected))

    def test_api_mode_cannot_execute_new_mock_mutations(self):
        from chatbot.adapters import ApiAdapter
        inner=ApiAdapter('http://127.0.0.1:1')
        with self.assertRaises(ValueError):inner.enter_unrecognized('D')
        with self.assertRaises(ValueError):inner.set_uncertain('D',1,True)
