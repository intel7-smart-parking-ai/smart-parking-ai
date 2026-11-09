"""Forecast bridge: validated slots -> read-only simulation API -> fixed response."""
import json
import os
import re
import time
from datetime import datetime,timedelta
from urllib.parse import urlencode
from . import config,dialog_context as DC
from .parser import extract_lots
from backend.app.forecasting import KST
from backend.app.chatbot.report_lab import forecast_query
from backend.app.chatbot.report_lab.source import read

def handle(engine,s,text,chip_id,event):
    if event=='start':s.forecast_question={};s.forecast_at=0
    if event or chip_id or not text:return None
    pending=s.forecast_question if time.monotonic()-s.forecast_at<config.QUERY_CONTEXT_TTL_SEC else {}
    # A slot-only answer continues clarification, never captures a different business request.
    short=re.sub(r'^\s*(?:그럼|그러면)\s*','',text).strip()
    time_only=bool(re.search(r'오늘|내일|모레|요일|\d+\s*(?:시|월)',short) and
          re.fullmatch(r'(?:(?:오늘|내일|모레|(?:(?:이번|다음)\s*주\s*)?[월화수목금토일]요일|\d{1,2}월\s*\d{1,2}일)\s*)?(?:(?:오전|오후|아침|저녁|밤)\s*)?(?:\d{1,2}\s*시(?:\s*\d{1,2}\s*분|\s*반)?)?(?:에는|에|은|는|도)?[?!.\s]*',short))
    followup=bool(pending and (time_only or re.fullmatch(r'.{0,12}(?:공항|신세계|주차장)(?:은|는|도)?[?!.\s]*',short)))
    if not forecast_query.wants(text) and not followup and not time_only:return None
    if engine.current_alert().get('level',0)>=2:
        return engine._respond(s,'경보 중에는 현장 안전 안내를 먼저 따라 주세요.',chip_set='alert')
    s.history.append(('user',text))
    previous=pending.get('slots') or {}
    query=text
    # During incomplete clarification retain the original slots, not concatenated questions.
    if followup and not previous:query=pending.get('text','')+' '+text
    now=datetime.now(KST)
    lots=extract_lots(query+' 주차')
    if len(lots)>1:
        return engine._respond(s,'한 번에 한 주차장을 선택해 주세요. 김해공항과 신세계 중 어디가 궁금하세요?')
    try:
        slots=forecast_query.interpret(query,DC.focus(s),now,previous);source='llm' if os.getenv('REPORT_OPENROUTER_KEY') else 'rule'
    except Exception:
        slots=forecast_query.fallback(query,now,previous);source='rule-fallback'
    # Recognized explicit date/time changes are deterministic even if the model misses them.
    checked=forecast_query.fallback(query,now,previous)
    if re.search(r'오늘|내일|모레|요일|\d+\s*월|\d{4}-',query):slots['date']=checked['date']
    elif previous.get('date'):slots['date']=previous['date']
    if re.search(r'\d{1,2}\s*시',query):slots['hour']=checked['hour'];slots['minute']=checked['minute']
    elif previous.get('hour',-1)>=0:slots['hour']=previous['hour'];slots['minute']=previous.get('minute',0)
    last_time=list(re.finditer(r'(\d{1,2})\s*시',query))
    if last_time and 1<=int(last_time[-1][1])<=12 and not re.search(r'오전|오후|아침|저녁|밤|새벽',query) and previous.get('hour',-1)<0:slots['hour']=-1
    engine.request_deadline.set(time.monotonic()+config.BACKEND_TIMEOUT_SEC)
    engine._alert_snapshot.set(None)
    # Explicit parser lot beats model output. Model cannot invent an unmentioned lot.
    lot=lots[0] if lots else previous.get('lot_id') if previous.get('lot_id') in config.catalog.ids() else DC.focus(s)
    slots['lot_id']=lot or 'none'
    s.forecast_question={'text':query,'slots':dict(slots)};s.forecast_at=time.monotonic()
    meta={'intent':'congestion_forecast','interpretation_source':source,'interpretation_trace_id':slots.get('trace_id')}
    if lot not in ('C','D'):
        message='혼잡 예측은 김해공항·신세계 시연 주차장에서 제공해요. 어느 주차장이 궁금하세요?'
        return engine._respond(s,message,meta=meta)
    DC.remember(s,lot=lot)
    if slots['hour']<0 or not slots['date']:
        return engine._respond(s,'언제 방문하시나요? “내일 오후 2시”처럼 오전·오후를 함께 알려주세요.',meta=meta)
    try:
        target=datetime.fromisoformat(slots['date']).replace(hour=slots['hour'],minute=slots['minute'],tzinfo=KST)
        # A bare weekday means the next occurrence, including when today's time passed.
        date_hint=query if re.search(r'오늘|내일|모레|요일|\d+\s*월|\d{4}-',query) else pending.get('text','')
        if target<=now and re.search(r'[월화수목금토일]요일',date_hint) and not re.search(r'오늘|어제|지난|이번\s*주|다음\s*주|\d+\s*월|\d{4}-',date_hint):
            target+=timedelta(days=7)
            slots['date']=target.date().isoformat()
        if not 0<(target-now).total_seconds()<=7*86400:raise ValueError()
    except ValueError:
        message='말씀하신 시각은 이미 지났어요. 방문할 날짜나 시간을 다시 알려주세요.' if 'target' in locals() and target<=now else '예측은 앞으로 일주일까지만 제공해요. 그 안의 방문 날짜를 알려주세요.'
        return engine._respond(s,message,meta=meta)
    base=os.getenv('REPORT_API_BASE','').rstrip('/')
    if not base:return engine._respond(s,'시연 혼잡 예측 서버에 연결되지 않았어요.',meta={**meta,'unavailable':True})
    try:
        result=read('/api/v1/congestion/forecast?'+urlencode({'lot_id':lot,'at':target.isoformat()}),None)
        if result.get('source')!='simulation' or result.get('is_mock') is not True or result.get('lot_id')!=lot:raise ValueError()
        selected=result['selected']
        if selected['level'] not in ('혼잡','보통','여유') or not 0<=selected['empty']<=selected['capacity']:raise ValueError()
    except Exception:
        return engine._respond(s,'시연 예측을 조회하지 못했어요. 잠시 후 다시 시도해 주세요.',meta={**meta,'unavailable':True})
    # Refresh safety evidence after the external model/API calls.
    engine.request_deadline.set(time.monotonic()+config.BACKEND_TIMEOUT_SEC)
    engine._alert_snapshot.set(None)
    if engine.current_alert().get('level',0)>=2:return engine._respond(s,'경보가 발생했어요. 현장 안전 안내를 먼저 따라 주세요.',chip_set='alert')
    # Successful forecasts remain available for date/time/lot-only follow-up questions.
    s.forecast_question={'text':query,'slots':{'lot_id':lot,'date':slots['date'],'hour':slots['hour'],'minute':slots['minute']}}
    s.forecast_at=time.monotonic()
    phrase={'여유':'여유로울','보통':'보통 수준일','혼잡':'혼잡할'}[selected['level']]
    day='오늘' if target.date()==now.date() else f'{target.month}월 {target.day}일'
    clock=f'{"오전" if target.hour<12 else "오후"} {target.hour%12 or 12}시'+(f' {target.minute}분' if target.minute else '')
    reply=f'{day} {clock} {config.catalog.name(lot)}은 {phrase} 것으로 예상돼요.\n예상 빈자리: 약 {selected["empty"]}자리\n※ 시뮬레이션 기반 예측'
    response=engine._respond(s,reply,card={'type':'congestion_forecast','data':result},meta={**meta,'forecast_source':'simulation'})
    response['_interpreted_query']=f'{config.catalog.name(lot)} {slots["date"]} {slots["hour"]}시 {slots["minute"]}분 혼잡 예측'
    return response
