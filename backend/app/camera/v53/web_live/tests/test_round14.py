"""Regression checks for the camera-free workflow and live control transitions."""
import io
import json
import threading
import unittest
from http.client import HTTPResponse
from unittest.mock import Mock, patch

from web_live.live import LiveSession
from web_live.policy import to_events
from web_live.tests.test_contract import fixture
from web_live.tests.test_lifecycle import Hub, Pipeline, Transport, eventually
from web_live.transport import EventTransport


class ControlTests(unittest.TestCase):
    def test_configuration_hides_old_roi_while_invalidation_is_waiting(self):
        entered,release=threading.Event(),threading.Event()
        class Controlled(Pipeline):
            approved=False
            def invalidate(self,camera=None,*,disconnected=False):
                if self.approved:
                    entered.set()
                    if not release.wait(5):raise RuntimeError('fixture timed out')
                with self.lock:self.approved=False
            def analyze(self,image,**kwargs):
                with self.lock:
                    row=super().analyze(image,**kwargs)
                    row['roi_confirmed']=self.approved
                    return row
        pipeline=Controlled()
        live=LiveSession(pipeline,discover_fn=lambda:[{}],hub_factory=Hub,transport_factory=Transport)
        change=None
        try:
            live.start()
            pipeline.approved=True
            change=threading.Thread(target=live.configure,args=(.92,{'1':'A','2':'B'}))
            change.start()
            self.assertTrue(entered.wait(3))
            live.hub.feed()
            def stale_published():
                with live.lock:
                    return live.results.get('1',{}).get('roi_confirmed')
            self.assertFalse(eventually(stale_published,.25))
            release.set()
            change.join(3)
            self.assertFalse(change.is_alive())
            live.hub.feed(seq=2)
            self.assertTrue(eventually(lambda:live.results.get('1',{}).get('frame_sequence')==2))
            self.assertFalse(live.results['1']['roi_confirmed'])
        finally:
            release.set()
            if change:change.join(3)
            live.close()

    def test_transport_refuses_offers_after_shutdown(self):
        sender=EventTransport()
        sender.close()
        with patch.dict('os.environ',API_BASE_URL='http://127.0.0.1',VISION_TOKEN='test'):
            with self.assertRaises(ValueError):sender.offer([],lambda _:True)
        self.assertTrue(sender.jobs.empty())

    def test_truncated_http_receipt_is_not_confirmed(self):
        sender=EventTransport()
        self.addCleanup(sender.close)
        result,packet=fixture()
        event=to_events(result,packet,'camera_a')[0]
        body=json.dumps({'event_id':event['event_id'],'disposition':'accepted'}).encode()
        raw=b'HTTP/1.1 202 Accepted\r\nContent-Length: '+str(len(body)+2).encode()+b'\r\n\r\n'+body
        def interrupted_reply(*args,**kwargs):
            response=HTTPResponse(Mock(makefile=lambda *a:io.BytesIO(raw)))
            response.begin()
            return response
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=interrupted_reply
            self.assertEqual(sender.send_one(event,lambda _:True,'http://127.0.0.1','test',0),
                             'delivery_unconfirmed')
