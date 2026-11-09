"""Bounded fallback intent interpretation. No free-form factual answers/actions."""
import json
import os
import re
from urllib.request import Request,urlopen
from . import prompts

TASKS={'spaces':'빈자리 알려줘','vehicle':'내 차 어디 있어?','vehicle_fee':'내 차 요금 알려줘',
       'fees':'기본 요금 알려줘','policies':'이용 정책 알려줘','charging':'충전 현황 알려줘'}
READ_TASKS = {"space_state", "vehicle_entry", "vehicle_duration", "vehicle_exit"}
INTENTS=('greeting','thanks','complaint','explain','clarify','business','unsupported')


def enabled():
    return bool(os.getenv('REPORT_OPENROUTER_KEY'))


def interpret(text,lot=None):
    if not enabled():return None
    client=None;handler=None
    if os.getenv('REPORT_LANGFUSE')=='1':
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler
        client=get_client();handler=CallbackHandler()
    prompt=prompts.load('conversation',client)
    def classify(_input):
        body={'model':os.getenv('REPORT_MODEL','openrouter/free'),'temperature':0,
              'provider':{'require_parameters':True},
              'response_format':{'type':'json_schema','json_schema':{'name':'parking_conversation','strict':True,
                 'schema':{'type':'object','properties':{
                    'intent':{'type':'string','enum':list(INTENTS)},
                    'task':{'type':'string','enum':['none', *TASKS, *sorted(READ_TASKS)]},
                    'lot_id':{'type':'string','enum':['none','A','B','C','D']},
                    'vehicle_numbers':{'type':'array','items':{'type':'string'},'maxItems':4},
                    'space_id':{'type':'string'}},
                    'required':['intent','task','lot_id','vehicle_numbers','space_id'],'additionalProperties':False}}},
              'messages':[{'role':'system','content':prompt.compile() + (
                  "\nExtract read intent and scope only. A=개발원, B=부산역, C=김해공항, D=신세계. "
                  "Use none or empty fields if unknown. Copy vehicle and space numbers from user input. "
                  "Use space_state for one parking space and vehicle_entry, vehicle_duration, vehicle_exit for session questions.")},
                          {'role':'user','content':json.dumps({'text':text,'focus_lot':lot},ensure_ascii=False)}]}
        req=Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),
                    headers={'Authorization':'Bearer '+os.environ['REPORT_OPENROUTER_KEY'],'Content-Type':'application/json'})
        with urlopen(req,timeout=3) as response:
            raw=json.load(response)['choices'][0]['message']['content'].strip()
        if raw.startswith('```'):raw=raw.split('\n',1)[1].rsplit('```',1)[0]
        decision=json.loads(raw)
        if set(decision)!={'intent','task','lot_id','vehicle_numbers','space_id'} or decision['intent'] not in INTENTS or decision['task'] not in ('none',*TASKS,*READ_TASKS):
            raise ValueError('Invalid conversational intent')
        numbers = decision['vehicle_numbers']
        if (decision['lot_id'] not in ('none','A','B','C','D') or not isinstance(numbers, list) or len(numbers) > 4
                or any(not isinstance(n, str) or not re.fullmatch(r'[1-9]\d|\d{4}', n)
                       or not re.search(r'(?<!\d)' + re.escape(n) + r'(?!\d)', text) for n in numbers)):
            raise ValueError('Invalid conversational scope')
        space = decision['space_id']
        if not isinstance(space, str) or (space and (not re.fullmatch(r'[A-D]-\d{2}', space) or space not in text.upper())):
            raise ValueError('Invalid space scope')
        if decision['intent']=='business' and decision['task']=='none':raise ValueError('Missing business task')
        return decision
    if handler:
        from langchain_core.runnables import RunnableLambda
        decision=RunnableLambda(classify).invoke({},config={'run_name':'conversation-interpretation','callbacks':[handler],
                                                           'metadata':prompt.metadata()})
        decision['trace_id']=handler.last_trace_id
    else:
        decision=classify({})
    decision['prompt_version']=prompt.version
    return decision
