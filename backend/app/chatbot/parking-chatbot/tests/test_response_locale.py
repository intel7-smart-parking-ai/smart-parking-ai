from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatbot import response_locale as locale, http_router

class ResponseLocaleTests(unittest.TestCase):
    def test_input_detection_and_explicit_selection(self):
        for text, expected in [('Where can I park?', 'en'), ('有空位吗', 'zh-CN'), ('空きはありますか', 'ja'), ('A 주차장', 'ko'), ('12', 'ko')]:
            self.assertEqual(locale.resolve_language('ko',text),expected)
        self.assertEqual(locale.resolve_language('ja','Where can I park?'),'ja')

    def test_numeric_facts_plate_ids_and_lot_names_are_protected(self):
        masked, replacements = locale._mask('김해공항 주차장 A-11 88가2000 4,500원 30분', 'en')
        restored = locale._restore(masked,masked,replacements)
        self.assertEqual(restored,'Gimhae Airport Parking A-11 88가2000 KRW 4,500 30분')
        for changed in (masked.replace('__SPACE1__',''), masked+' 999', masked+' __NAME0__'):
            with self.assertRaises(ValueError):locale._restore(changed,masked,replacements)

    def test_space_counts_are_not_translated_as_bay_numbers(self):
        masked, replacements = locale._mask('김해공항 주차장 16자리 · 카메라 확인 중 3칸', 'en')
        result = locale._restore(masked, masked, replacements)
        self.assertEqual(result, 'Gimhae Airport Parking 16 spaces · 카메라 확인 중 3 spaces')

    def test_translation_preserves_state_and_action_tokens_without_mutating_engine_reply(self):
        original={'text':'안내', 'request_id':'request-1', 'state_version':8,'banner':{'level':0,'text':'안내'},
                  'safety':{'title':'안전','guidance':'대피','vehicle_help':'이동 금지'},
                  'chips':[{'id':'confirm_yes','label':'확인','confirmation_token':'private-token'}],
                  'card':{'type':'payment','data':{'amount':4500,'payment_id':'receipt-1'}}}
        before=deepcopy(original)
        with patch.object(locale,'_translate',side_effect=lambda strings,language:tuple('EN '+text for text in strings)):
            result=locale.localize_response(original,'en')
        self.assertEqual(original,before)
        self.assertEqual(result['text'],'EN 안내')
        self.assertEqual(result['chips'][0]['confirmation_token'],'private-token')
        self.assertEqual(result['card'],before['card'])
        self.assertEqual(result['state_version'],8)
        self.assertEqual(result['banner']['level'],0)

    def test_translation_failure_retains_safety_and_receipt(self):
        original={'text':'결제 완료','safety':{'title':'안전','guidance':'대피'},'card':{'type':'payment','data':{'amount':4500}}}
        with patch.object(locale,'_translate',side_effect=TimeoutError):
            result=locale.localize_response(original,'ja')
        self.assertEqual(result['translation_status'],'unavailable')
        self.assertEqual(result['safety'],original['safety'])
        self.assertEqual(result['card'],original['card'])
        self.assertTrue(result['text'].startswith('翻訳'))

    def test_updates_and_chip_labels_are_localized(self):
        response={'messages':[{'text':'안내','event_id':'e1','chips':[{'id':'help','label':'안내'}]}],'chips':None}
        with patch.object(locale,'_translate',return_value=('Help',)):
            result=locale.localize_response(response,'en')
        self.assertEqual(result['messages'][0]['text'],'Help')
        self.assertEqual(result['messages'][0]['chips'][0]['label'],'Help')
        self.assertIsNone(result['chips'])

    def test_korean_and_unchanged_poll_do_not_call_provider(self):
        with patch.object(locale,'_translate') as translate:
            self.assertEqual(locale.localize_response({'text':'안내'},'ko'),{'text':'안내'})
            locale.localize_response({'messages':[],'chips':None},'en')
            translate.assert_not_called()

    def test_http_uses_language_and_keeps_legacy_shape(self):
        app=FastAPI();app.include_router(http_router.router)
        response={'text':'안내','chips':[{'id':'help','label':'안내'}],'banner':None,'safety':None}
        with patch.object(http_router.config,'EMBEDDED',False),patch.object(http_router.engine,'handle',return_value=response),patch.object(locale,'_translate',return_value=('Help',)):
            with TestClient(app) as client:
                self.assertEqual(client.post('/api/v1/chat',json={'session_id':'s','text':'안녕'}).json(),response)
                result=client.post('/api/v1/chat',json={'session_id':'s','text':'Where can I park?','language':'ko'}).json()
                self.assertEqual(result['text'],'Help');self.assertEqual(result['language'],'en')
                self.assertEqual(client.post('/api/v1/chat',json={'session_id':'s','language':'invalid'}).status_code,422)
