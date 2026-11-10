import threading
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, patch
from PIL import Image
import numpy as np

from web_live.evaluate import evaluate
from web_live.camera_hub import CameraHub
from web_live.pipeline import recover_rotated_print, rotated_proposals
from web_live.live import LiveSession
from web_live.tests import test_evaluation_cohorts as cohorts
from web_live.policy import to_events
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport, eventually
from web_live.transport import EventTransport


class CaptureBindingTests(unittest.TestCase):
    def test_lost_ack_then_auth_rejection_is_still_unconfirmed(self):
        sender=EventTransport()
        self.addCleanup(sender.close)
        result,packet=fixture()
        event=to_events(result,packet,'camera_a')[0]
        base='http://127.0.0.1'
        with patch('web_live.transport.urllib.request.build_opener') as opener:
            post=opener.return_value.open
            post.side_effect=[URLError('lost ACK'),HTTPError(base,401,'unauthorized',{},None)]
            self.assertEqual(sender.send_one(event,lambda _:True,base,'test',sender.generation),'delivery_unconfirmed')
            self.assertEqual(post.call_count,2)
            self.assertEqual(post.call_args_list[0].args[0].data,post.call_args_list[1].args[0].data)

    def test_new_counter_after_long_sdk_gap_requires_new_epoch(self):
        hub=CameraHub([{}])
        sdk=Mock()
        first,late=Mock(),Mock()
        for frames,seq in ((first,8),(late,9)):
            color=frames.get_color_frame.return_value
            color.get_frame_number.return_value=seq
            color.get_data.return_value=np.zeros((16,16,3),np.uint8)
        def end():
            hub.stop_event.set()
            return False,None
        reads=iter([lambda:(True,first),lambda:(True,late),end])
        sdk.try_wait_for_frames.side_effect=lambda timeout:next(reads)()
        with patch('web_live.camera_hub.time.monotonic',side_effect=[1.0,1.1,243.1,243.2]):
            with self.assertRaises(RuntimeError):
                hub._receive('1',7,sdk)
        self.assertEqual(hub.frames['1']['sequence'],8)
        late.get_color_frame.return_value.get_data.assert_not_called()

    def test_retry_cannot_use_other_channel_with_same_lot_and_epoch(self):
        live = LiveSession(Pipeline(), discover_fn=lambda: [{}, {}], hub_factory=Hub, transport_factory=Transport)
        self.addCleanup(live.close)
        live.start()
        live.hub.feed('1', epoch=1, seq=9)
        live.sending = True
        self.assertTrue(eventually(lambda: live.transport.offers))
        _, events, validate = live.transport.offers[0]
        self.assertTrue(validate(events[0]))
        live.hub.packets.pop('1')
        live.hub.feed('2', epoch=1, seq=1)
        self.assertTrue(eventually(lambda: '1' not in live.results and '2' in live.results))
        self.assertFalse(validate(events[0]), 'A retry used the new physical channel as evidence')
        self.assertTrue(eventually(lambda: len(live.transport.offers) == 2))
        _, new_events, new_validate = live.transport.offers[-1]
        self.assertTrue(new_validate(new_events[0]))

    def test_queued_bays_retain_their_own_validator_after_coalescing(self):
        release = threading.Event()
        original = EventTransport._run
        results = {}
        def paused(sender):
            release.wait(3)
            original(sender)
        def deliver(event, validator, *args):
            results[event['lot_id']] = validator(event)
            return 'accepted'
        with patch.object(EventTransport, '_run', paused), patch.object(EventTransport, '_deliver', side_effect=deliver), \
                patch('web_live.transport.destination', return_value=('http://127.0.0.1','test')):
            sender = EventTransport()
            try:
                sender.offer([dict(lot_id='A', space_id='A-01')], lambda _: False)
                sender.offer([dict(lot_id='B', space_id='A-01')], lambda _: True)
                release.set()
                self.assertTrue(eventually(lambda: len(results)==2))
                self.assertEqual(results, {'A':False,'B':True})
            finally:
                release.set()
                sender.close()


class RotationTests(unittest.TestCase):
    def setUp(self):
        self.frame=np.zeros((80,140,3),np.uint8)
        self.polys={'A-01':np.float32([[0,0],[139,0],[139,79],[0,79]])}
        self.box=dict(bbox_xyxy=[10,10,30,30],sticker_color='green',confidence=.8)
        self.bank=Mock()
        self.bank.read_box.return_value={'number':'23','score':.90,'margin':.05}

    def recover(self,proposals,peers):
        return recover_rotated_print(self.frame,'A-01',self.polys,proposals,peers,self.bank)

    def test_rotated_box_maps_to_native_pixels_on_nonsquare_frame(self):
        model=Mock()
        model.infer_bgr.return_value={'detections':[self.box],'inference_ms':23}
        found,ms=rotated_proposals(model,self.frame)
        self.assertEqual(found[0]['bbox_xyxy'],[110,50,130,70])
        self.assertEqual(self.box['bbox_xyxy'],[10,10,30,30])
        self.assertEqual(ms,23)

    def test_weak_box_requires_print_and_corroboration_at_same_place_and_color(self):
        self.assertEqual(self.recover([self.box],[]),[])
        self.assertEqual(self.recover([self.box],[dict(self.box,sticker_color='white')]),[])
        self.assertEqual(len(self.recover([self.box],[self.box])),1)
        self.bank.read_box.return_value=None
        self.assertEqual(self.recover([dict(self.box,confidence=.99)],[self.box]),[])

    def test_conflicting_box_or_color_vetoes_single_sticker(self):
        for extra in (dict(self.box,bbox_xyxy=[80,10,100,30],confidence=.92),
                      dict(self.box,sticker_color='white',confidence=.94)):
            self.assertEqual(self.recover([self.box],[self.box,extra]),[])


class ImageEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=cohorts.CohortTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def test_identical_decoded_pixels_in_distinct_pngs_rejected(self):
        t=self.fixture
        pixels=Image.new('RGB',(40,35),'red')
        for name,level in [('first',0),('second',9)]:
            pixels.save(t.base/name,format='PNG',compress_level=level)
        self.assertNotEqual((t.base/'first').read_bytes(),(t.base/'second').read_bytes())
        train=dict(t.sample,split='train',session_id='other',sample_id='training',image='second')
        with self.assertRaises(ValueError):evaluate({'samples':[t.sample,train]},t.base)

    def test_text_is_not_photo_evidence(self):
        t=self.fixture
        (t.base/'first').write_text('training evidence','utf8')
        with self.assertRaises(ValueError):evaluate({'samples':[t.sample]},t.base)
