import threading
import time
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
from types import SimpleNamespace
import numpy as np
from web_live.live import LiveSession
from web_live.registration import assign_sticker
from web_live.number_reader import same_sticker,signature
from web_live.tests.test_contract import fixture


class Hub:
    def __init__(self,devices):self.packets={};self.active=False
    def start(self):self.active=True
    def snapshot(self):return {'1':'ready','2':'ready'}
    def latest(self,camera):
        p=self.packets.get(camera)
        return p if p and time.monotonic()-p['received']<=1 else None
    def stop(self):self.active=False;return True
    def stopped(self):return not self.active
    def feed(self,camera='1',epoch=1,seq=1):
        self.packets[camera]={'image':np.zeros((20,20,3),np.uint8),'epoch':epoch,'sequence':seq,
            'received':time.monotonic(),'observed_at':datetime.now(timezone.utc).isoformat()}


class Pipeline:
    def __init__(self):self.numbers=SimpleNamespace(status='ready');self.invalidations=[];self.pause=None;self.lock=threading.RLock()
    def invalidate(self,camera=None,*,disconnected=False):self.invalidations.append(camera)
    def analyze(self,image,**kwargs):
        if self.pause:self.pause.wait(3)
        r,p=fixture();r.update(camera_id=kwargs['camera'],stream_epoch=kwargs['epoch'],frame_sequence=kwargs['sequence'],
            observed_at=kwargs['observed_at'],registration_ok=True)
        return r
    def approve_roi(self,*args):pass
    def register_empty(self,*args):pass
    def close(self):pass


class Transport:
    def __init__(self):self.offers=[];self.cancellations=0
    def offer(self,events,validator):self.offers.append((time.monotonic(),events,validator))
    def cancel(self):self.cancellations+=1
    def snapshot(self):return {'counts':{},'last_status':'test'}
    def close(self):pass


def eventually(check,timeout=2):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if check():return True
        time.sleep(.01)
    return False


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.pipeline=Pipeline();self.live=LiveSession(self.pipeline,discover_fn=lambda:[{},{}],hub_factory=Hub,transport_factory=Transport)
    def tearDown(self):
        if self.pipeline.pause:self.pipeline.pause.set()
        self.live.close()

    def test_start_idempotent_and_stop_clears(self):
        self.live.start();hub=self.live.hub;self.live.start();self.assertIs(self.live.hub,hub)
        hub.feed();self.assertTrue(eventually(lambda:bool(self.live.snapshot()['results'])))
        self.live.stop();self.assertFalse(self.live.snapshot()['running']);self.assertEqual(self.live.snapshot()['results'],{})

    def test_late_inference_after_config_not_published(self):
        self.pipeline.pause=threading.Event();self.live.start();self.live.hub.feed()
        time.sleep(.08);self.live.configure(.9,{'1':'B','2':'auto'});self.live.hub.packets.clear()
        self.pipeline.pause.set();time.sleep(.1)
        self.assertEqual(self.live.snapshot()['results'],{})

    def test_old_connection_result_discarded(self):
        self.pipeline.pause=threading.Event();self.live.start();self.live.hub.feed(epoch=1)
        time.sleep(.08);self.live.hub.feed(epoch=2,seq=2);self.pipeline.pause.set()
        self.assertTrue(eventually(lambda:self.live.results.get('1',{}).get('stream_epoch')==2))
        self.assertEqual(self.live.results['1']['frame_sequence'],2)

    def test_stale_frames_hidden(self):
        self.live.start();self.live.hub.feed();self.assertTrue(eventually(lambda:bool(self.live.results)))
        self.live.hub.packets['1']['received']-=3
        self.assertEqual(self.live.snapshot()['results'],{})
        self.assertTrue(eventually(lambda:not self.live.results))

    def test_send_requires_simulator_confirmation(self):
        with patch('web_live.live.destination',return_value=('http://127.0.0.1:8000','test')):
            self.assertRaises(ValueError,self.live.enable_send,True,False)
            self.assertRaises(ValueError,self.live.enable_send,True,True)

    def test_one_camera_disconnect_preserves_other_camera_delivery(self):
        self.live.start();self.live.hub.feed('1');self.live.hub.feed('2')
        self.assertTrue(eventually(lambda:len(self.live.results)==2))
        cancellations=self.live.transport.cancellations
        self.live.hub.packets.pop('1')
        self.assertTrue(eventually(lambda:'1' in self.pipeline.invalidations))
        self.assertIn('2',self.live.results)
        self.assertEqual(self.live.transport.cancellations,cancellations)
        self.assertNotIn('2',self.pipeline.invalidations)

    def test_same_lot_from_two_cameras_not_sent(self):
        self.live.start();self.live.hub.feed('1');self.live.hub.feed('2')
        self.assertTrue(eventually(lambda:len(self.live.results)==2))
        self.live.sending=True
        self.assertFalse(self.live._valid_event({'lot_id':'A','space_id':'A-01','event_type':'space_empty_observed'}))

    def test_current_unknown_invalidates_queued_empty(self):
        self.live.start();self.live.hub.feed();self.assertTrue(eventually(lambda:bool(self.live.results)))
        self.live.sending=True;event={'lot_id':'A','space_id':'A-01','event_type':'space_empty_observed','frame_id':'camera_a:1:1:test'}
        self.assertTrue(self.live._valid_event(event))
        self.live.results['1']['spaces'][0]['sendable']=False
        self.assertFalse(self.live._valid_event(event))

    def test_no_repeat_events_for_frozen_frame(self):
        self.live.start();self.live.hub.feed();self.assertTrue(eventually(lambda:bool(self.live.results)))
        self.live.sending=True
        time.sleep(1.15)
        self.assertLessEqual(len(self.live.transport.offers),1)
        self.live.hub.packets.clear();time.sleep(1.05)
        self.assertLessEqual(len(self.live.transport.offers),1)

    def test_ocr_identity_change_not_reused(self):
        a=np.full((64,64,3),160,np.uint8);b=a.copy();b[20:45,25:35]=0
        self.assertTrue(same_sticker(signature(a),signature(a)))
        self.assertFalse(same_sticker(signature(a),signature(b)))

    def test_center_boundary_is_not_assigned_twice(self):
        polygons={'A-01':[[0,0],[100,0],[100,100],[0,100]],'A-02':[[0,100],[100,100],[100,200],[0,200]]}
        self.assertEqual(assign_sticker([20,20,50,50],polygons),'A-01')
        self.assertIsNone(assign_sticker([20,80,50,120],polygons))
        self.assertIsNone(assign_sticker([200,20,240,60],polygons))
        self.assertEqual(assign_sticker([20,-5,50,25],polygons),'A-01')


if __name__=='__main__':unittest.main()
