"""LLM extracts location/time only; forecasting never runs in the model."""
import json
import os
import re
from datetime import datetime,timedelta
from urllib.request import Request,urlopen
from . import prompts
from backend.app.forecasting import KST

def wants(text):
    return bool(re.search(r'혼잡|붐비|붐빌|북적|예측|예상|한산',text) or
                (re.search(r'내일|모레|\d+\s*시|도착|주말',text) and re.search(r'빈자리|만차|자리.{0,8}(?:있|없|남)',text)))

def fallback(text,now,context=None):
    """Merge explicit changes into previous validated slots; never guess a new AM/PM."""
    context=context or {}
    day=(now+timedelta(days=2 if '모레' in text else 1 if '내일' in text else 0)).date().isoformat()
    date_given=bool(re.search(r'오늘|내일|모레|요일|주말|\d+\s*월|\d{4}-',text))
    if not date_given:day=context.get('date') or day
    weekday=re.search(r'(월|화|수|목|금|토|일)요일',text)
    if weekday:
        index='월화수목금토일'.index(weekday[1])
        offset=(index-now.weekday())%7
        if '다음 주' in text or '다음주' in text:offset=7-now.weekday()+index
        elif '이번 주' in text or '이번주' in text:offset=index-now.weekday()
        day=(now+timedelta(days=offset)).date().isoformat()
    elif '주말' in text:day=''
    absolute=re.search(r'(\d{4})-(\d{2})-(\d{2})|(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일',text)
    if absolute:
        try:day=datetime(int(absolute[1] or absolute[4] or now.year),int(absolute[2] or absolute[5]),int(absolute[3] or absolute[6])).date().isoformat()
        except ValueError:day=''
    hour=context.get('hour',-1);minute=context.get('minute',0)
    matches=list(re.finditer(r'(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?',text))
    m=matches[-1] if matches else None
    if m:
        hour=-1
        h=int(m[1]);minute=int(m[2] or 0)
        if '반' in text[m.end():]:minute=30
        if re.search(r'오후|저녁|밤',text) and 1<=h<=12:hour=h%12+12
        elif re.search(r'오전|새벽|아침',text) and 1<=h<=12:hour=h%12
        elif 13<=h<=23 or h==0:hour=h
        elif 1<=h<=12 and context.get('hour',-1)>=0:hour=h%12+(12 if context['hour']>=12 else 0)
    return {'lot_id':context.get('lot_id','none'),'date':day,'hour':hour,'minute':minute}

def interpret(text,focus,now,context=None):
    if not os.getenv('REPORT_OPENROUTER_KEY'):return fallback(text,now,context)
    client=None;handler=None
    if os.getenv('REPORT_LANGFUSE')=='1':
        from langfuse import get_client
        from langfuse.langchain import CallbackHandler
        client=get_client();handler=CallbackHandler()
    prompt=prompts.load('forecast',client)
    def extract(_):
        schema={'type':'object','properties':{
            'lot_id':{'type':'string','enum':['none','A','B','C','D']},
            'date':{'type':'string'},'hour':{'type':'integer','minimum':-1,'maximum':23},
            'minute':{'type':'integer','minimum':0,'maximum':59}},
            'required':['lot_id','date','hour','minute'],'additionalProperties':False}
        body={'model':os.getenv('REPORT_MODEL','openrouter/free'),'temperature':0,
              'provider':{'require_parameters':True},'response_format':{'type':'json_schema',
              'json_schema':{'name':'forecast_query','strict':True,'schema':schema}},
              'messages':[{'role':'system','content':prompt.compile()},
                          {'role':'user','content':json.dumps({'text':text,'now_kst':now.isoformat(),'focus_lot':focus,'previous_forecast':context},ensure_ascii=False)}]}
        req=Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),
                    headers={'Authorization':'Bearer '+os.environ['REPORT_OPENROUTER_KEY'],'Content-Type':'application/json'})
        with urlopen(req,timeout=8) as response:raw=json.load(response)['choices'][0]['message']['content'].strip()
        if raw.startswith('```'):raw=raw.split('\n',1)[1].rsplit('```',1)[0]
        result=json.loads(raw)
        if set(result)!={'lot_id','date','hour','minute'} or result['lot_id'] not in ('none','A','B','C','D'):raise ValueError('Invalid slots')
        if type(result['hour']) is not int or not -1<=result['hour']<=23 or type(result['minute']) is not int or not 0<=result['minute']<=59:raise ValueError('Invalid time')
        if result['date']:datetime.strptime(result['date'],'%Y-%m-%d')
        return result
    if handler:
        from langchain_core.runnables import RunnableLambda
        result=RunnableLambda(extract).invoke({},config={'run_name':'forecast-query-interpretation','callbacks':[handler],'metadata':prompt.metadata()})
        result['trace_id']=handler.last_trace_id
    else:result=extract({})
    return result
