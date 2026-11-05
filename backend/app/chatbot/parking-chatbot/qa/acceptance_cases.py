"""Repository-derived acceptance expectations. Production code is not changed."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from datetime import timedelta
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
from chatbot import config,engine as e,parser,llm_router as router,contracts as C,templates as T,transport
from chatbot.adapters import CachedAdapter,MockAdapter,ApiAdapter,DataUnavailable,request_deadline,now,iso
from chatbot.catalog import Catalog
from chatbot.state import Session,Pending,Flow
import run_llm_compare as original

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

compare=load('qa_compare_models',ROOT/'tools/compare_models.py')
FAKE='qa-synthetic-credential-only'
FIELDS=['question','intent','lot_id','car_id','zone']
ROWS=[dict(zip(FIELDS,(q,i,'','',''))) for q,i in
      [('합성 인수 문장 하나','get_fee'),('합성 인수 문장 둘','unknown')]]

class AcceptanceCases(unittest.TestCase):
    def setUp(self):
        for item in (patch.object(e,'data',CachedAdapter(MockAdapter())),patch.object(config,'LLM_PROVIDER','none')):
            item.start();self.addCleanup(item.stop)
        e.reset_all();self.addCleanup(e.reset_all)

    def calls(self,text):
        response=e.handle('acceptance',text=text)
        self.assertFalse(e.data.inner.payments,'문의만으로 결제하지 않음')
        return len(e.admin_calls()),response

    def test_ADM_01(self):self.assertEqual(self.calls('관리자 오지 마세요')[0],0,'명시적 거절의 새 접수는 0건')
    def test_ADM_02(self):self.assertEqual(self.calls('직원 계세요?')[0],0,'존재 문의는 호출하지 않음')
    def test_ADM_03(self):self.assertEqual(self.calls('관리자 있나요?')[0],0,'존재 문의는 호출하지 않음')
    def test_ADM_04(self):self.assertEqual(self.calls('직원 좀 불러 주세요')[0],1,'명시적 긍정 호출 유지')
    def test_ADM_05(self):self.assertEqual(self.calls('관리자 오지 말아 주세요')[0],0)

    def test_PAY_01(self):
        count,response=self.calls('결제하는 방법 알려줘')
        self.assertEqual(response['meta']['intent'],'payment_help')
        self.assertIsNone(e.get_session('acceptance').pending)

    def test_DATA_01(self):
        catalog=Catalog(deepcopy(config.catalog.settings));before=catalog.spaces('A');version=catalog.version
        future=iso(now()+timedelta(hours=1))
        lots={'items':[{'lot_id':'A','total_spaces':len(before),'empty_spaces':len(before)}],'as_of':future}
        spaces={'items':[{'space_id':label,'status':'empty','car_id':None,'space_type':'general'} for label in reversed(before)]}
        api=ApiAdapter('http://unused.invalid')
        with patch.object(config,'catalog',catalog),patch.object(api,'_request',side_effect=lambda m,p,b=None:
                (200,lots if p==config.API_PATHS['lots'] else spaces,{})):
            with self.assertRaises(DataUnavailable):CachedAdapter(api).get_empty_spots()
            self.assertEqual(catalog.spaces('A'),before,'거절된 응답에 명부도 불변')
            self.assertEqual(catalog.version,version)

    def test_RATE_01(self):
        policy={'base_minutes':30,'base_fee':1000,'unit_minutes':10,'extra_fee':500,'daily_cap':10000}
        self.assertRegex(T.fee_rule({'A':policy,'B':{**policy,'base_minutes':60,'base_fee':2000}}),r'60\s*분')

    def checker(self,vehicle):
        module=load('qa_backend_checker',ROOT/'tests/check_backend.py')
        class Fixture:
            def get_empty_spots(self):return {'lots':{'A':{'total':12,'empty':9}},'updated_at':iso(now())}
            def get_alert(self):return {'status':'unavailable','unavailable':True,'level':0,'version':0}
            def find_car(self,no):return None
            def get_fee(self,no):return {'status':'unavailable','unavailable':True}
            def list_cars(self):return []
        fixture=Fixture();C.validate('empty',fixture.get_empty_spots());C.validate('alert',fixture.get_alert())
        C.validate('fee',fixture.get_fee(vehicle))
        with patch.object(config,'DATA_SOURCE','api'),patch.object(module,'build_adapter',return_value=fixture),\
             patch.object(sys,'argv',['check_backend',vehicle]),redirect_stdout(io.StringIO()) as stream:
            code=module.main()
        return code,stream.getvalue()

    def test_CHK_01(self):
        code,text=self.checker('17')
        self.assertNotEqual(code,0,'unavailable 경보/요금을 정상 연결로 판정하지 않음: '+text)

    def test_CHK_02(self):
        code,text=self.checker('27')
        self.assertEqual(parser.normalize_car_id('27'),'27')
        self.assertNotIn('01~24',text,'최신 D-06을 적용')
        self.assertNotEqual(code,2)

    def sdk(self,provider,source):
        package='langchain_anthropic' if provider=='anthropic' else 'langchain_openai'
        if importlib.util.find_spec(package) is None:self.skipTest('선택 제공자 패키지 미설치: '+package)
        keys={key:FAKE for key in config.API_KEY_NAMES.values()}
        with patch.dict(os.environ,keys if source=='environment' else {},clear=True),patch.dict(config._dotenv,keys,clear=True):
            self.assertEqual(config.api_key(provider),FAKE)
            try:model=router._create_model((provider,'constructor-only',3,'digest'))
            except Exception as exc:self.fail('SDK 생성 실패: '+type(exc).__name__)
            secret=getattr(model,'anthropic_api_key' if provider=='anthropic' else 'openai_api_key',None)
            self.assertTrue(secret and secret.get_secret_value()==FAKE,'설정 키가 실제 SDK까지 전달되어야 함')

    def test_SDK_01(self):self.sdk('openai','dotenv')
    def test_SDK_02(self):self.sdk('anthropic','dotenv')
    def test_SDK_03(self):self.sdk('openrouter','dotenv')
    def test_SDK_04(self):self.sdk('openai','environment')
    def test_SDK_05(self):self.sdk('anthropic','environment')
    def test_SDK_06(self):self.sdk('openrouter','environment')

    def temporary(self):
        base=ROOT/'.validation';base.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(prefix='qa-',dir=base)

    def test_EVAL_01(self):
        with self.temporary() as directory:
            file=Path(directory)/'synthetic.csv'
            with file.open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader();writer.writerows(ROWS)
            with ExitStack() as stack:
                for obj,name,value in [(config,'LLM_PROVIDER','openrouter'),(config,'LLM_ENGINE','langchain'),
                    (config,'LLM_MODEL','fake'),(router,'_model',None),(router,'_model_key',None),
                    (router,'_failure',None),(router,'_initializing',False),(router,'_retry_at',0)]:
                    stack.enter_context(patch.object(obj,name,value))
                stack.enter_context(patch.object(config,'api_key',return_value=FAKE))
                stack.enter_context(patch.object(router,'_create_model',side_effect=ModuleNotFoundError('synthetic unavailable')))
                stack.enter_context(patch.object(sys,'argv',['compare',str(file),'--compare']))
                with redirect_stdout(io.StringIO()) as output:original.main()
            hybrid=json.loads(output.getvalue())['hybrid']
            self.assertTrue(hybrid.get('skipped') or hybrid.get('invalid') or hybrid.get('valid') is False,
                            '모델 미준비를 정상 hybrid 점수와 구분해야 함: '+str(hybrid))

    def test_EVAL_02(self):
        identity=('openrouter','fake',1,'digest')
        class Failure:
            def bind_tools(self,tools):return self
            def invoke(self,messages):raise PermissionError('synthetic rejection')
        with patch.object(config,'LLM_ENGINE','langchain'),patch.object(config,'LLM_FEWSHOT',False),\
            patch.object(router,'_settings',return_value=(identity,None)),patch.object(router,'warmup'),\
            patch.object(router,'_model_key',identity),patch.object(router,'_model',Failure()),patch.object(router,'_initializing',False):
            report=compare.measure(ROWS,'fake',0)
        self.assertTrue(report.get('api_error',0)>0 or report.get('valid') is False,
                        '호출 오류/무효가 결과에 남아야 함: '+str(report))

    def test_EVAL_03(self):
        fake=dict(correct=1,total=1,accuracy=1,mean_ms=1,max_ms=1,wrong_tool=0,unknown_correct=0,
                  unknown=0,timeout=0,invalid_intent=0,invalid_params=0)
        with self.temporary() as directory,patch.object(config,'LLM_PROVIDER','openrouter'),\
            patch.object(config,'LLM_ENGINE','langchain'),patch.object(config,'api_key',return_value=FAKE),\
            patch.object(compare,'read_rows',return_value=ROWS),patch.object(compare,'measure',return_value=fake) as measured:
            try:compare.main(['--models','fake','--fewshot','0','--repeats','1','--files','fake.csv',
                              '--output',str(Path(directory)/'missing'/'results.csv')])
            except (OSError,SystemExit):pass
            self.assertEqual(measured.call_count,0,'저장 경로 오류는 측정 전에 확인')

    def test_CSV_01(self):
        with self.temporary() as directory:
            file=Path(directory)/'invalid.csv'
            with file.open('w',encoding='utf-8',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=FIELDS);writer.writeheader()
                writer.writerow({**ROWS[0],'intent':'nonexistent_intent'})
            with self.assertRaises(ValueError):compare.read_rows(file)

    def test_WAIT_01(self):
        entered,done=threading.Event(),threading.Event()
        pending=Pending(Flow.CONFIRM_ACTION,'pay_fee',{'amount':1000,'entry_at':iso(now()),'idempotency_key':'synthetic'},'17')
        def waiting():
            token=request_deadline.set(time.monotonic()+.05);entered.set()
            try:e._execute_payment(Session(car_id='17'),pending)
            finally:request_deadline.reset(token);done.set()
        with patch.object(e,'_payment_active',True),patch.object(e,'data',CachedAdapter(ApiAdapter('http://unused.invalid'))),\
            patch.object(e,'current_alert',return_value={'level':0,'version':0}):
            worker=threading.Thread(target=waiting,daemon=True);worker.start();self.assertTrue(entered.wait(1))
            began=time.monotonic()
            try:returned=done.wait(.25);elapsed=time.monotonic()-began
            finally:
                with e._payment_condition:e._payment_active=False;e._payment_condition.notify_all()
                worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertTrue(returned,'예약 대기는 요청 예산 안에서 포기해야 함')
        self.assertLess(elapsed,.15)

    def test_DNS_01(self):
        def resolver(*args,**kwargs):
            threading.Event().wait(.2);raise socket.gaierror('synthetic delayed DNS')
        began=time.monotonic()
        with patch.object(socket,'getaddrinfo',side_effect=resolver):
            with self.assertRaises((TimeoutError,OSError)):
                transport.load_json(urllib.request.Request('http://unused.invalid/'),.02)
        self.assertLess(time.monotonic()-began,.15,'DNS도 호출자의 시간 예산에 포함')
