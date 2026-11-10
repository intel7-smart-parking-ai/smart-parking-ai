import threading
import time
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET

from web_live.live import LiveSession
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport, eventually
from web_live.server import BoundedChunkedPost
from web_live.inference import StickerModel
from web_live.models import Classifier
from web_live.pipeline import ParkingPipeline


class DeliveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.live = LiveSession(Pipeline(), transport_factory=Transport)
        self.live.hub = Hub([])
        self.live.running = self.live.sending = True
        self.addCleanup(self.live.close)

    def put(self, cam, lot):
        result, packet = fixture(lot)
        packet['received'] = time.monotonic()
        row = result['spaces'][0]
        row.update(observation='occupied', car_id='23', vehicle_type='ev')
        self.live.results[cam] = result
        self.live.packets[cam] = dict(packet)
        self.live.hub.packets[cam] = dict(packet)
        return packet

    def test_disconnected_or_expired_peer_cannot_suppress_healthy_camera(self):
        for lot, fault in [('A','disconnect'),('B','new_epoch'),('B','old_analysis')]:
            with self.subTest(lot=lot, fault=fault):
                self.put('1','A')
                self.put('2',lot)
                if fault == 'disconnect':
                    self.live.hub.packets.pop('2')
                elif fault == 'new_epoch':
                    self.live.hub.packets['2']['epoch'] += 1
                else:
                    self.live.packets['2']['received'] -= 3
                self.live.stop_event.clear()
                before = len(self.live.transport.offers)
                worker = threading.Thread(target=self.live._publish)
                worker.start()
                try:
                    self.assertTrue(eventually(lambda:len(self.live.transport.offers)>before, .4))
                    _,events,validate = self.live.transport.offers[-1]
                    event = next(e for e in events if e['space_id']=='A-01')
                    self.assertEqual(event['car_id'],'23')
                    self.assertTrue(validate(event))
                    self.assertEqual({e['lot_id'] for e in events},{'A'})
                finally:
                    self.live.stop_event.set()
                    worker.join(1)

    def test_send_enable_requires_current_approved_result(self):
        self.put('1','A')
        self.live.hub.packets.clear()
        with patch('web_live.live.destination',return_value=('loopback','test')):
            with self.assertRaises(ValueError):
                self.live.enable_send(True,True)


class StartupTests(unittest.TestCase):
    def test_photo_and_camera_cannot_take_same_model_session(self):
        live=LiveSession(Pipeline(),discover_fn=lambda:[{}],hub_factory=Hub,transport_factory=Transport)
        self.addCleanup(live.close)
        with self.assertRaisesRegex(ValueError,'invalid photo'),live.photo_turn():
            with self.assertRaises(ValueError):live.start()
            with self.assertRaises(ValueError),live.photo_turn():pass
            raise ValueError('invalid photo')
        self.assertTrue(live.start()['running'])
        with self.assertRaises(ValueError),live.photo_turn():pass

    def test_partial_hub_failure_releases_channels_and_allows_fresh_start(self):
        class BrokenHub(Hub):
            def start(self):
                self.active=True
                raise RuntimeError('capture thread start failed')
        hubs = [BrokenHub([]),Hub([])]
        live = LiveSession(Pipeline(),discover_fn=lambda:[{}],
            hub_factory=lambda devices:hubs.pop(0),transport_factory=Transport)
        self.addCleanup(live.close)
        with self.assertRaises(RuntimeError):
            live.start()
        self.assertFalse(live.running)
        self.assertTrue(live.hub.stopped())
        self.assertTrue(live.start()['running'])


class ChunkedPostTests(unittest.IsolatedAsyncioTestCase):
    headers=[(b'transfer-encoding',b'chunked')]

    async def test_actual_bytes_are_limited_before_parsing(self):
        app=AsyncMock()
        send=AsyncMock()
        receive=AsyncMock(side_effect=[{'type':'http.request','body':b'x'*65000,'more_body':True},
                                       {'type':'http.request','body':b'x'*600,'more_body':False}])
        with patch('web_live.server.MAX_BYTES',32):
            await BoundedChunkedPost(app)({'type':'http','method':'POST','headers':self.headers},receive,send)
        app.assert_not_awaited()
        self.assertEqual(send.await_args_list[0].args[0]['status'],413)

    async def test_small_stream_delivered_once_and_disconnect_stops_parse(self):
        messages=[]
        async def app(scope,receive,send):
            messages.extend([await receive(),await receive()])
        receive=AsyncMock(side_effect=[{'type':'http.request','body':b'a','more_body':True},
            {'type':'http.request','body':b'b','more_body':False},{'type':'http.disconnect'}])
        scope={'type':'http','method':'POST','headers':self.headers}
        await BoundedChunkedPost(app)(scope,receive,AsyncMock())
        self.assertEqual(messages,[{'type':'http.request','body':b'ab','more_body':False},{'type':'http.disconnect'}])
        downstream=AsyncMock()
        await BoundedChunkedPost(downstream)(scope,AsyncMock(return_value={'type':'http.disconnect'}),AsyncMock())
        downstream.assert_not_awaited()


class ModelStartupTests(unittest.TestCase):
    def test_duplicate_class_index_and_nonfinite_threshold_rejected_before_compilation(self):
        with TemporaryDirectory() as folder:
            path=Path(folder)/'model.xml'
            for role,fields,loader in [
                ('detection',{'labels':'sticker_white sticker_green sticker_blue sticker_blue'},StickerModel),
                ('detection',{'labels':'sticker_white sticker_green sticker_blue','confidence_threshold':'nan'},StickerModel),
                ('classification',{'label_info':'{"label_names":["occupied","empty","occupied"]}'},
                 lambda path:Classifier(path,('empty','occupied')))]:
                root=ET.Element('net')
                parent=ET.SubElement(ET.SubElement(root,'rt_info'),'model_info')
                for key,value in dict(task_type=role,**fields).items():ET.SubElement(parent,key,value=value)
                ET.ElementTree(root).write(path)
                with self.subTest(fields=fields), self.assertRaises(ValueError):loader(path)

    def test_corrupted_print_file_leaves_no_ocr_worker(self):
        with TemporaryDirectory() as folder:
            bank=Path(folder)/'bank.npz';bank.write_bytes(b'bad')
            cfg=dict(detector='',team_directory=Path(folder),printed_numbers=bank)
            with patch('web_live.pipeline.StickerModel'),patch('web_live.pipeline.BoardRegistry'), \
                 patch('web_live.pipeline.Classifier'),patch('web_live.pipeline.NumberReader') as reader:
                with self.assertRaises(ValueError):ParkingPipeline(cfg)
                reader.assert_not_called()
