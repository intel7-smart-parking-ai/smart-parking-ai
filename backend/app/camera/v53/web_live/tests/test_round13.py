import io
import json
import unittest
from unittest.mock import MagicMock, patch

from web_live.policy import to_events
from web_live.tests.test_contract import fixture
from web_live.transport import EventTransport


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.sender=EventTransport()
        self.addCleanup(self.sender.close)
        result,packet=fixture()
        self.event=to_events(result,packet,'camera_a')[0]

    def send(self,status,body):
        response=MagicMock()
        response.__enter__.return_value.status=status
        response.__enter__.return_value.length=None
        response.__enter__.return_value.read.side_effect=io.BytesIO(body).read
        with patch.object(self.sender.http,'opener',create=True) as opener, \
                patch.object(self.sender.http,'local',True,create=True):
            opener.open.return_value=response
            result=self.sender.send_one(self.event,lambda _:True,'http://127.0.0.1','example',0)
        return result

    def test_success_requires_matching_event_and_disposition(self):
        for status,outcome in ((202,'accepted'),(200,'duplicate')):
            receipt=dict(event_id=self.event['event_id'],disposition=outcome)
            self.assertEqual(self.send(status,json.dumps(receipt).encode()),outcome)
            for bad in (dict(receipt,event_id='another-event'),dict(receipt,disposition='unexpected'),
                        dict(receipt,disposition='duplicate' if status==202 else 'accepted')):
                with self.subTest(status=status,bad=bad):
                    self.assertEqual(self.send(status,json.dumps(bad).encode()),'delivery_unconfirmed')

    def test_invalid_success_body_cannot_confirm_delivery(self):
        for body in (b'',b'<html>login</html>',b'[]',b'null',b'\xff',b'{}',b' '*5000):
            with self.subTest(body=body[:20]):
                self.assertEqual(self.send(200,body),'delivery_unconfirmed')
