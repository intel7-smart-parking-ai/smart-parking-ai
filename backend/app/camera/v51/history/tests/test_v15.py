import unittest
from types import SimpleNamespace
from quality import apply_sensor
import test_v14 as fixture
class Fixes(unittest.TestCase):
    def test_automatic_read_only_unlocked_before_manual(self):
        class Dynamic(fixture.Sensor):
            def is_option_read_only(self,o):return o in ('exposure','gain') and self.values['enable_auto_exposure']==1
        sensor=Dynamic();actual=apply_sensor(sensor,fixture.Quality().rs(),{'enable_auto_exposure':0,'exposure':20,'gain':2})
        self.assertEqual(actual['exposure']['value'],20);self.assertEqual(sensor.calls[0],'enable_auto_exposure')
    def test_failed_readback_cannot_report_success(self):
        class BadRead(fixture.Sensor):
            broken=False
            def set_option(self,o,v):super().set_option(o,v);self.broken=True
            def get_option(self,o):
                if self.broken:raise RuntimeError('readback failure')
                return super().get_option(o)
        with self.assertRaises(RuntimeError):apply_sensor(BadRead(),fixture.Quality().rs(),{'gain':2})

    def worker_case(self,disconnect):
        import threading,time
        import numpy as np
        from camera_hub import CameraHub,CameraSpec
        entered=threading.Event();release=threading.Event();calls=[]
        class Source:
            count=0
            def open(self):pass
            def close(self):pass
            def read(self):
                self.count+=1
                if self.count==2:
                    entered.set();release.wait(2)
                    if disconnect:raise RuntimeError('disconnect')
                time.sleep(.002);return np.zeros((10,10,3),np.uint8)
            def apply_quality(self,changes):calls.append(changes);return {}
        source=Source();hub=CameraHub([CameraSpec('A')],source_factory=lambda spec:source)
        hub.start()
        try:
            self.assertTrue(entered.wait(2));receipt=hub.request_quality('A',{'gain':2})
            if not disconnect:receipt.cancelled=True
            release.set();result=receipt.get(timeout=2)
            self.assertFalse(result['ok']);self.assertEqual(calls,[])
        finally:release.set();hub.stop()
    def test_worker_skips_cancelled_settings(self):self.worker_case(False)
    def test_disconnect_discards_pending_settings(self):self.worker_case(True)

    def test_atomic_json_retries_temporary_windows_lock(self):
        import tempfile,os,json
        from pathlib import Path
        from unittest.mock import patch
        from parking_ai.storage import atomic_json
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'data.json';target.write_text('{}',encoding='utf-8');original=os.replace;calls=[]
            def replace(a,b):
                calls.append(1)
                if len(calls)<3:raise PermissionError('temporary sharing lock')
                return original(a,b)
            with patch('parking_ai.storage.os.replace',side_effect=replace),patch('parking_ai.storage.time.sleep'):atomic_json(target,{'saved':True})
            self.assertEqual(len(calls),3);self.assertEqual(json.loads(target.read_text()),{'saved':True})
