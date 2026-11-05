"""Run the frozen additional acceptance cases, preserving failing expectations."""
import argparse
from collections import Counter
from contextlib import redirect_stdout,redirect_stderr
import faulthandler
import hashlib
from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

class Result(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.rows=[];self.starts={}
    def startTest(self,test):
        self.starts[test.id()]=time.monotonic();faulthandler.dump_traceback_later(20,exit=True);super().startTest(test)
    def stopTest(self,test):faulthandler.cancel_dump_traceback_later();super().stopTest(test)
    def record(self,test,status,detail=''):
        identifier=test.id().rsplit('test_',1)[-1].replace('_','-')
        self.rows.append({'id':identifier,'test':test.id(),'status':status,'detail':detail[-5000:],
                          'ms':round((time.monotonic()-self.starts[test.id()])*1000,1)})
    def addSuccess(self,test):super().addSuccess(test);self.record(test,'PASS')
    def addFailure(self,test,err):super().addFailure(test,err);self.record(test,'FAIL',self._exc_info_to_string(err,test))
    def addError(self,test,err):super().addError(test,err);self.record(test,'ERROR',self._exc_info_to_string(err,test))
    def addSkip(self,test,reason):super().addSkip(test,reason);self.record(test,'BLOCKED',reason)

def browser_payloads():
    from chatbot import config,engine as e,templates as T,contracts as C
    from chatbot.adapters import ApiAdapter,CachedAdapter,MockAdapter,iso,now
    api=ApiAdapter('http://unused.invalid');observed=iso(now())
    location={'car_id':'17','lot_id':'A','space_id':'A-01','session_id':None,'status':'occupied','observed_at':observed,'confidence':None}
    alarm={'items':[{'alert_id':'qa','type':'mock_fire_led_observed','status':'active','level':2,'lot_id':'A','space_id':'A-01','confidence':None}],'as_of':observed}
    with patch.object(api,'_request',side_effect=lambda m,p,b=None:(200,alarm if p==config.API_PATHS['alerts'] else location,{})):
        car=api.find_car('17');alert=api.get_alert()
    C.validate('car',car);C.validate('alert',alert)
    payload={'car':{'text':T.car_location(car),'card':{'type':'car_location','data':car},'updated_at':observed},
             'alert':{'text':T.api_alert(alert),'card':{'type':'alert','data':alert},'updated_at':observed,'safety':T.safety_card(alert)}}
    with patch.object(e,'data',CachedAdapter(MockAdapter())):
        e.reset_all();e.handle('qa-browser',text='33번 차 어디 있어?');e.handle('qa-browser',text='네')
        payload['pending']=e.handle('qa-browser',chip_id='pay')
        payload['payment_records_before_confirm']=len(e.data.inner.payments)
        payload['busy']=e.busy_response(payload['pending']['runtime_id']);payload['poll']=e.updates('qa-browser')
        e.reset_all()
    return payload

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'.validation/qa_acceptance/results')
    parser.add_argument('--python-only',action='store_true')
    args=parser.parse_args(argv);out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    criteria_path=ROOT/'qa/criteria.json';criteria_hash=hashlib.sha256(criteria_path.read_bytes()).hexdigest()
    criteria=json.loads(criteria_path.read_text(encoding='utf-8'))
    os.environ.update(DATA_SOURCE='mock',LLM_PROVIDER='none',CHATBOT_EMBEDDED='0',ADMIN_TOKEN='',DEMO_ENABLED='1',
                      PYTHONIOENCODING='utf-8',LLM_FEWSHOT='0',FALLBACK_HINTS='0')
    spec=importlib.util.spec_from_file_location('qa_acceptance_cases',ROOT/'qa/acceptance_cases.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(module.AcceptanceCases)
    attempts=[]
    def blocked(*values,**kwargs):attempts.append('blocked');raise AssertionError('Acceptance forbids external connections')
    with (out/'python.log').open('w',encoding='utf-8') as stream,redirect_stdout(stream),redirect_stderr(stream),\
        patch.object(socket.socket,'connect',side_effect=blocked),patch.object(socket.socket,'connect_ex',side_effect=blocked):
        result=unittest.TextTestRunner(stream=stream,verbosity=2,resultclass=Result).run(suite)
    rows=result.rows
    browser_cases=[c for c in criteria['cases'] if c['layer']=='browser']
    browser_result={'results':[],'page_errors':[]}
    if args.python_only:
        browser_result['results']=[{'id':c['id'],'status':'NOT_RUN','detail':'--python-only'} for c in browser_cases]
    elif shutil.which('node') is None:
        browser_result['results']=[{'id':c['id'],'status':'BLOCKED','detail':'Node.js 없음'} for c in browser_cases]
    else:
        from server_dev import Handler
        inputs=out/'browser_inputs.json';inputs.write_text(json.dumps(browser_payloads(),ensure_ascii=False),encoding='utf-8')
        target=out/'browser.json'
        if target.exists():target.unlink()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            try:
                run=subprocess.run(['node',str(ROOT/'qa/browser_acceptance.cjs'),str(inputs),str(target)],cwd=ROOT,
                    env={**os.environ,'TEST_BASE_URL':f'http://127.0.0.1:{server.server_port}'},
                    capture_output=True,encoding='utf-8',errors='replace',timeout=40)
                log=run.stdout+run.stderr
                if target.exists():browser_result=json.loads(target.read_text(encoding='utf-8'))
                else:
                    status='BLOCKED' if any(x in log for x in ("Cannot find module","Executable doesn't exist")) else 'ERROR'
                    browser_result['results']=[{'id':c['id'],'status':status,'detail':log[-2000:]} for c in browser_cases]
            except subprocess.TimeoutExpired:
                log='Browser acceptance outer timeout';browser_result['results']=[{'id':c['id'],'status':'ERROR','detail':log} for c in browser_cases]
            (out/'browser.log').write_text(log,encoding='utf-8')
        finally:server.shutdown();server.server_close();worker.join(2)
    rows+=browser_result['results']
    definitions={c['id']:c for c in criteria['cases']}
    for row in rows:
        row.update({key:definitions.get(row['id'],{}).get(key,'UNMAPPED') for key in ('requirement','scope','severity','expected')})
    missing=set(definitions)-{row['id'] for row in rows}
    for identifier in sorted(missing):rows.append({**definitions[identifier],'status':'ERROR','detail':'Case not executed'})
    required_failures=[r for r in rows if r['scope']=='required' and r['status']!='PASS']
    unchanged=hashlib.sha256(criteria_path.read_bytes()).hexdigest()==criteria_hash
    report={'criteria_sha256':criteria_hash,'criteria_unchanged':unchanged,'counts':dict(Counter(r['status'] for r in rows)),
        'required_non_pass':len(required_failures),'external_connection_attempts':len(attempts),
        'page_errors':browser_result['page_errors'],'cases':rows,
        'gate':'FAIL' if required_failures or attempts or not unchanged or browser_result['page_errors'] else 'PASS'}
    (out/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='cases'},ensure_ascii=False))
    return int(report['gate']!='PASS')

if __name__=='__main__':raise SystemExit(main())
