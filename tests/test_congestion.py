import sqlite3
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from importlib import import_module
from urllib.parse import urlsplit,parse_qs
from backend.app.forecasting import KST
from backend.app.simulation.congestion import simulate,ensure_history,history
from backend.app.congestion import forecast
from backend.app.chatbot.report_lab import forecast_query
from backend.app.chatbot import router

engine=import_module('backend.app.chatbot._parking_runtime.engine')
adapters=import_module('backend.app.chatbot._parking_runtime.adapters')
bridge=import_module('backend.app.chatbot._parking_runtime.forecast_chat')

class CongestionTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,8,10,tzinfo=KST)
        self.rows=simulate('D',datetime(2026,8,1,tzinfo=KST),98)

    def test_patterns_and_bounded_entry_exit_records(self):
        for lot in ('C','D'):
            rows=simulate(lot,datetime(2026,8,1,tzinfo=KST),70)
            occupied=16
            for row in rows:
                occupied+=row['arrivals']-row['departures']
                self.assertEqual(row['capacity']-row['empty'],occupied)
                self.assertTrue(0<=occupied<=64)
        day=[r['empty'] for r in self.rows if datetime.fromisoformat(r['bucket']).hour==15]
        night=[r['empty'] for r in self.rows if datetime.fromisoformat(r['bucket']).hour==3]
        self.assertLess(sum(day)/len(day),sum(night)/len(night))

    def test_storage_isolated_idempotent_and_excludes_future(self):
        with sqlite3.connect(':memory:') as conn:
            conn.row_factory=sqlite3.Row
            ensure_history(conn,self.now);first=history(conn,'C',self.now)
            ensure_history(conn,self.now)
            self.assertEqual(first,history(conn,'C',self.now))
            self.assertTrue(all(datetime.fromisoformat(r['bucket'])<self.now for r in first))
            self.assertTrue(all(r['is_mock'] for r in first))

    def test_future_data_cannot_change_prediction(self):
        target=self.now+timedelta(days=1,hours=4)
        original=forecast(self.rows,target,self.now)
        fake=[{**r,'bucket':(target+timedelta(hours=i)).isoformat(),'empty':0} for i,r in enumerate(self.rows[:24])]
        self.assertEqual(original,forecast(self.rows+fake,target,self.now))
        self.assertEqual(original['label'],'시뮬레이션 기반 예측')
        self.assertGreater(original['validation_samples'],300)
        self.assertTrue(all(0<=r['empty']<=64 for r in original['hours']))

    def test_invalid_and_missing_history(self):
        for target in (self.now-timedelta(hours=1),self.now+timedelta(days=8)):
            with self.assertRaises(ValueError):forecast(self.rows,target,self.now)
        with self.assertRaises(ValueError):forecast([],self.now+timedelta(days=1),self.now)

    def test_time_fallback_never_assumes_two_pm(self):
        self.assertEqual(forecast_query.fallback('내일 2시',self.now)['hour'],-1)
        self.assertEqual(forecast_query.fallback('내일 오후 2시 반',self.now)['hour'],14)
        self.assertEqual(forecast_query.fallback('내일 오후 2시 반',self.now)['minute'],30)
        self.assertEqual(forecast_query.fallback('오전 12시',self.now)['hour'],0)

    def test_chat_clarifies_then_returns_api_forecast_not_model_numbers(self):
        with patch.object(engine,'data',adapters.CachedAdapter(adapters.MockAdapter())),patch.dict('os.environ',{'REPORT_OPENROUTER_KEY':'','REPORT_API_BASE':'http://127.0.0.1:8000'}):
            engine.reset_all()
            first=engine.handle('forecast-flow',text='내일 김해공항 2시에 붐빌까?')
            self.assertIn('오전·오후',first['text'])
            result=forecast(simulate('C',datetime(2026,8,1,tzinfo=KST),98),self.now+timedelta(days=1,hours=4),self.now)
            with patch.object(bridge,'read',return_value=result) as api:
                reply=engine.handle('forecast-flow',text='내일 오후 2시')
            api.assert_called_once()
            self.assertEqual(reply['card']['type'],'congestion_forecast')
            self.assertIn('시뮬레이션 기반 예측',reply['text'])
            self.assertIn(str(result['selected']['empty']),reply['text'])

    def test_real_lots_and_failures_do_not_get_synthetic_estimates(self):
        with patch.object(engine,'data',adapters.CachedAdapter(adapters.MockAdapter())),patch.dict('os.environ',{'REPORT_OPENROUTER_KEY':'','REPORT_API_BASE':'http://127.0.0.1:8000'}):
            engine.reset_all()
            with patch.object(bridge,'read',side_effect=TimeoutError()) as api:
                real=engine.handle('forecast-real',text='내일 오후 2시 부산역 혼잡할까?')
                api.assert_not_called()
                failed=engine.handle('forecast-error',text='내일 오후 2시 김해공항 혼잡할까?')
            self.assertIsNone(real['card'])
            self.assertTrue(failed['meta']['unavailable'])
            self.assertIsNone(failed['card'])

    def test_completed_forecast_keeps_lot_date_and_am_pm_for_followups(self):
        def api(path,_):
            params=parse_qs(urlsplit(path).query)
            lot=params['lot_id'][0];target=datetime.fromisoformat(params['at'][0])
            return forecast(simulate(lot,datetime(2026,8,1,tzinfo=KST),98),target,self.now)
        with patch.object(engine,'data',adapters.CachedAdapter(adapters.MockAdapter())),patch.dict('os.environ',{'REPORT_OPENROUTER_KEY':'','REPORT_API_BASE':'http://127.0.0.1:8000'}),patch.object(bridge,'read',side_effect=api),patch.object(bridge,'datetime',wraps=datetime) as clock:
            clock.now.return_value=self.now
            engine.reset_all()
            first=engine.handle('followup-predict',text='내일 오후 2시 김해공항 붐빌까?')
            self.assertEqual(first['card']['data']['lot_id'],'C')
            saturday=engine.handle('followup-predict',text='토요일3시는?')
            self.assertEqual(saturday['card']['data']['lot_id'],'C')
            self.assertEqual(saturday['card']['data']['target'],'2026-11-14T15:00:00+09:00')
            compact=engine.handle('followup-predict',text='토요일오후3시')
            self.assertEqual(compact['card']['data']['target'],'2026-11-14T15:00:00+09:00')
            morning=engine.handle('followup-predict',text='그럼 오전 10시는?')
            self.assertEqual(morning['card']['data']['target'],'2026-11-14T10:00:00+09:00')
            mall=engine.handle('followup-predict',text='그럼 신세계는?')
            self.assertEqual(mall['card']['data']['lot_id'],'D')
            self.assertEqual(mall['card']['data']['target'],'2026-11-14T10:00:00+09:00')

    def test_expired_forecast_context_is_not_used(self):
        with patch.object(engine,'data',adapters.CachedAdapter(adapters.MockAdapter())),patch.dict('os.environ',{'REPORT_OPENROUTER_KEY':''}):
            engine.reset_all()
            s=engine.get_session('expired-forecast')
            s.forecast_question={'slots':{'lot_id':'C','date':'2026-11-09','hour':14,'minute':0}}
            s.forecast_at=0
            with patch.object(bridge,'read') as api:
                response=engine.handle('expired-forecast',text='토요일3시는?')
            api.assert_not_called()
            self.assertIsNone(response['card'])
            self.assertIn('어느 주차장',response['text'])

    def test_weekdays_with_or_without_spaces(self):
        for index,day in enumerate('월화수목금토일'):
            for text in (f'{day}요일오후3시',f'{day}요일 오후 3시'):
                result=forecast_query.fallback(text,self.now)
                expected=(self.now+timedelta(days=(index-self.now.weekday())%7)).date().isoformat()
                self.assertEqual(result['date'],expected)
                self.assertEqual(result['hour'],15)

    def test_passed_bare_weekday_rolls_forward_but_explicit_today_does_not(self):
        at=self.now.replace(hour=18)
        def api(path,_):
            params=parse_qs(urlsplit(path).query)
            return forecast(simulate('C',datetime(2026,8,1,tzinfo=KST),98),datetime.fromisoformat(params['at'][0]),at)
        with patch.object(engine,'data',adapters.CachedAdapter(adapters.MockAdapter())),patch.dict('os.environ',{'REPORT_OPENROUTER_KEY':'','REPORT_API_BASE':'http://127.0.0.1:8000'}),patch.object(bridge,'read',side_effect=api) as read,patch.object(bridge,'datetime',wraps=datetime) as clock:
            clock.now.return_value=at
            engine.reset_all()
            ambiguous=engine.handle('sunday',text='일요일 3시에 김해공항 붐빌까?')
            self.assertIn('오전·오후',ambiguous['text'])
            resolved=engine.handle('sunday',text='일요일 오후 3시')
            self.assertEqual(resolved['card']['data']['target'],'2026-11-15T15:00:00+09:00')
            read.reset_mock()
            past=engine.handle('today',text='오늘 오후 3시 김해공항 붐빌까?')
            read.assert_not_called()
            self.assertIn('이미 지났',past['text'])

if __name__=='__main__':unittest.main()
