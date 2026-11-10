import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from web_live.number_reader import NumberReader,signature
from web_live.policy import to_events
from web_live.transport import EventTransport
from web_live.tests.test_contract import fixture


class P1Regressions(unittest.TestCase):
    def test_slow_bay_does_not_block_other_camera(self):
        started=threading.Event();release=threading.Event();other=threading.Event()
        def deliver(event,*args):
            if event['lot_id']=='A':started.set();release.wait(3)
            else:other.set()
            return 'accepted'
        with patch('web_live.transport.destination',return_value=('http://127.0.0.1:1','test-only')),patch.object(EventTransport,'_deliver',side_effect=deliver):
            transport=EventTransport()
            try:
                transport.offer([{'lot_id':'A','space_id':'A-01'}],lambda _:True)
                self.assertTrue(started.wait(2))
                transport.offer([{'lot_id':'B','space_id':'A-01'}],lambda _:True)
                self.assertTrue(other.wait(2),'other camera waited for slow A request')
            finally:release.set();transport.close()
            self.assertFalse(transport.worker.is_alive())

    def test_one_bay_preserves_order_and_keeps_only_latest_waiter(self):
        started=threading.Event();release=threading.Event();last=threading.Event();order=[]
        def deliver(event,*args):
            order.append(event['sequence'])
            if event['sequence']==0:started.set();release.wait(3)
            if event['sequence']==2:last.set()
            return 'accepted'
        with patch('web_live.transport.destination',return_value=('http://127.0.0.1:1','test-only')),patch.object(EventTransport,'_deliver',side_effect=deliver):
            transport=EventTransport()
            try:
                def offer(i):transport.offer([{'lot_id':'A','space_id':'A-01','sequence':i}],lambda _:True)
                offer(0);self.assertTrue(started.wait(2));offer(1);offer(2)
                deadline=time.monotonic()+2
                while transport.snapshot()['counts'].get('queue_replaced',0)<1 and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(order,[0])
                release.set();self.assertTrue(last.wait(2))
                self.assertEqual(order,[0,2])
                self.assertEqual(transport.snapshot()['counts']['queue_replaced'],1)
            finally:release.set();transport.close()

    def test_one_cached_read_never_becomes_two_reads(self):
        r=NumberReader();r.worker=SimpleNamespace(is_alive=lambda:True)
        key=('1',0,'A','A-01','green');crop=np.full((40,40,3),128,np.uint8);sig=signature(crop)
        with patch('web_live.number_reader.time.monotonic',return_value=100.):
            reading=r._record_reading(key,sig,{'number':'11','confidence':.95},'0:1')
            r.cache[key]=(100.,sig,reading)
        for tick in [100.,101.,102.,103.]:
            with patch('web_live.number_reader.time.monotonic',return_value=tick):
                self.assertEqual(r.query(key,crop,frame_id=f'0:{int(tick)}')['independent_reads'],1)
        with patch('web_live.number_reader.time.monotonic',return_value=103.):
            self.assertEqual(r._record_reading(key,sig,reading,'0:1')['independent_reads'],1)
            self.assertEqual(r._record_reading(key,sig,reading,'0:2')['independent_reads'],2)
            self.assertEqual(r._record_reading(key,sig,{'number':'42','confidence':.95},'0:3')['independent_reads'],1)
        r.invalidate('1');self.assertEqual(r.read_history,{})

    def test_changed_pixels_reset_agreement(self):
        r=NumberReader();key=('1',);a=signature(np.full((40,40,3),128,np.uint8))
        b=signature(np.zeros((40,40,3),np.uint8))
        r._record_reading(key,a,{'number':'11','confidence':.9},'1')
        self.assertEqual(r._record_reading(key,b,{'number':'11','confidence':.9},'2')['independent_reads'],1)

    def test_low_score_does_not_contribute_to_independent_agreement(self):
        r=NumberReader();key=('1',);sig=signature(np.full((40,40,3),128,np.uint8))
        self.assertEqual(r._record_reading(key,sig,{'number':'11','confidence':.79},'1')['independent_reads'],0)
        self.assertEqual(r._record_reading(key,sig,{'number':'11','confidence':.9},'2')['independent_reads'],1)

    def test_sender_holds_both_conflicting_identity_and_type(self):
        result,packet=fixture('A')
        result['spaces'][0].update(observation='occupied',sendable=True,car_id='87',vehicle_type='ev',confidence=.9)
        sent=to_events(result,packet,'camera_a')
        row=next(e for e in sent if e['space_id']==result['spaces'][0]['space_id'])
        self.assertIsNone(row['car_id']);self.assertIsNone(row['vehicle_type'])

    def test_health_does_not_claim_ocr_ready_without_worker(self):
        from web_live import server
        state=server.app.state
        with patch.object(state,'live',object(),create=True),patch.object(state,'start_error',None,create=True),patch.object(state,'pipeline',SimpleNamespace(numbers=NumberReader(),metadata={}),create=True):
            result=server.health()
            self.assertTrue(result['models_ready']);self.assertFalse(result['ocr_ready']);self.assertFalse(result['ready'])
