import io
import json
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from web_live.tests import test_round29
from web_live.transport import EventTransport
from web_live.pipeline import ParkingPipeline
from web_live.registration import BoardRegistry
from unittest.mock import Mock
import numpy as np


class ObstructedBoardTests(unittest.TestCase):
    def test_enrolled_board_uses_next_scale_once_before_full_search(self):
        registry=BoardRegistry.__new__(BoardRegistry)
        registry._legacy_at_scale=Mock(side_effect=[None,{'lot_id':'B'}])
        frame=np.zeros((800,1600,3),np.uint8)
        found=registry.locate(frame,'auto',{'method':'v52_enrolled_unmarked_board','search_scale':1100})
        self.assertEqual(found['lot_id'],'B')
        self.assertEqual([call.args[2] for call in registry._legacy_at_scale.call_args_list],[1100,820])

    def test_only_consecutive_current_landmarks_restore_reviewed_geometry(self):
        image=np.zeros((50,60,3),np.uint8)
        approval=(7,'A',np.zeros((4,2)),image.shape)
        model=ParkingPipeline.__new__(ParkingPipeline)
        model.fixed_views={};model.confirmed={};model.baselines={}
        model.registry=Mock()
        ref={'original':True}
        def begin():
            model.interrupted_views={'1':(approval,ref,time.monotonic()+12,None)}
        begin()
        pose={'lot_id':'A'}
        model.registry.verify_fixed_view.side_effect=[pose,None,pose,pose,pose]
        for seq in (2,3,4,4):
            model._recheck_interrupted(image,'1',7,seq,'auto',True)
            self.assertFalse(model.confirmed)
        self.assertIs(model._recheck_interrupted(image,'1',7,5,'auto',True),pose)
        self.assertEqual(model.fixed_views['1'],(7,ref))
        self.assertEqual(model.baselines,{})
        for epoch,lot,lag in [(8,'A',0),(7,'B',0),(7,'A',15)]:
            begin();model.confirmed.clear()
            with patch('web_live.pipeline.time.monotonic',return_value=time.monotonic()+lag):
                self.assertIsNone(model._recheck_interrupted(image,'1',epoch,6,lot,True))
            self.assertFalse(model.interrupted_views)
            self.assertFalse(model.confirmed)


class HeartbeatTests(unittest.TestCase):
    setUp=test_round29.ChangedBayTests.setUp
    frame=test_round29.ChangedBayTests.frame
    offer=test_round29.ChangedBayTests.offer

    def test_timer_waits_for_new_frame_but_changes_bypass_heartbeat(self):
        self.frame(1)
        self.offer()
        self.frame(2)
        t=time.monotonic()
        self.live.results['1'].update(_ready_mono=t,analysis_fps=2)
        self.assertEqual(self.offer(),[])
        with patch('web_live.live.time.monotonic',return_value=t+1.05):
            self.assertEqual(self.offer(),[])
            self.frame(3,occupied=[0])
            self.live.results['1'].update(_ready_mono=t+1.05,analysis_fps=2)
            sent=self.offer()
            self.assertEqual(len(sent),12)
            self.assertEqual(sent[0]['event_type'],'vehicle_observed')


class ReceiptTests(unittest.TestCase):
    def test_error_body_is_classified_without_recording_secrets(self):
        sender=EventTransport()
        self.addCleanup(sender.close)
        ev={'event_id':'e','confidence':.9,'observed_at':datetime.now(timezone.utc).isoformat()}
        base='http://127.0.0.1'
        body=json.dumps({'error':{'code':'EVENT_ID_CONFLICT','message':'private-info'}}).encode()
        error=HTTPError(base,409,'Conflict',{},io.BytesIO(body))
        with patch('urllib.request.OpenerDirector.open',side_effect=error):
            outcome,info=sender._deliver_traced(ev,lambda _:True,base,'private-info',sender.generation)
        sender._record(outcome,ev,info)
        self.assertEqual(outcome,'HTTP_409')
        self.assertNotIn('private-info',str(sender.recent_outcomes()))
        self.assertEqual(sender.snapshot()['recent_reasons'],{'HTTP_409/EVENT_ID_CONFLICT':1})
        with patch('web_live.transport.time.monotonic',return_value=time.monotonic()+61):
            self.assertEqual(sender.snapshot()['recent_counts'],{})
            self.assertEqual(sender.snapshot()['counts'],{'HTTP_409':1})
