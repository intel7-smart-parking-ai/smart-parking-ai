import unittest
from types import SimpleNamespace
import numpy as np
from quality import image_quality,apply_sensor
class Sensor:
    def __init__(self):self.values={k:1 for k in ['exposure','gain','white_balance','enable_auto_exposure','enable_auto_white_balance','sharpness','power_line_frequency']};self.calls=[]
    def supports(self,o):return o in self.values
    def get_option(self,o):return self.values[o]
    def get_option_range(self,o):return SimpleNamespace(min=0,max=100,step=1)
    def is_option_read_only(self,o):return False
    def set_option(self,o,v):self.calls.append(o);self.values[o]=v
class Quality(unittest.TestCase):
    def rs(self):return SimpleNamespace(option=SimpleNamespace(**{k:k for k in Sensor().values}))
    def test_black_and_white_warnings(self):
        self.assertGreater(image_quality(np.zeros((50,50,3),np.uint8))['dark_ratio'],.9)
        self.assertGreater(image_quality(np.full((50,50,3),255,np.uint8))['bright_ratio'],.9)
    def test_texture_sharper_than_flat(self):
        image=np.zeros((100,100,3),np.uint8);image[::2]=255
        self.assertGreater(image_quality(image)['laplacian_variance'],image_quality(np.zeros_like(image))['laplacian_variance'])
    def test_preflight_prevents_partial_invalid_updates(self):
        sensor=Sensor()
        with self.assertRaises(ValueError):apply_sensor(sensor,self.rs(),{'enable_auto_exposure':0,'exposure':101})
        self.assertEqual(sensor.calls,[])
    def test_auto_disabled_before_manual_and_readback(self):
        sensor=Sensor();actual=apply_sensor(sensor,self.rs(),{'exposure':20,'enable_auto_exposure':0})
        self.assertEqual(sensor.calls,['enable_auto_exposure','exposure']);self.assertEqual(actual['exposure']['value'],20)
    def test_nan_and_step_rejected(self):
        for value in [float('nan'),1.2]:
            with self.assertRaises(ValueError):apply_sensor(Sensor(),self.rs(),{'gain':value})
    def test_read_only_query_does_not_write(self):
        sensor=Sensor();self.assertTrue(apply_sensor(sensor,self.rs(),{})['gain']['supported']);self.assertEqual(sensor.calls,[])

    def test_camera_settings_execute_on_camera_worker(self):
        import threading,time
        from camera_hub import CameraHub,CameraSpec
        class Source:
            def open(self):pass
            def read(self):time.sleep(.005);return np.zeros((10,10,3),np.uint8)
            def close(self):pass
            def apply_quality(self,changes):return {'worker_thread':threading.get_ident(),'requested':changes}
        hub=CameraHub([CameraSpec('A')],source_factory=lambda spec:Source())
        hub.start()
        try:
            deadline=time.monotonic()+2
            while hub.get_latest_frame('A') is None and time.monotonic()<deadline:time.sleep(.01)
            result=hub.request_quality('A',{'gain':2}).get(timeout=2)
            self.assertTrue(result['ok']);self.assertNotEqual(result['actual']['worker_thread'],threading.get_ident())
            self.assertEqual(result['actual']['requested'],{'gain':2})
        finally:hub.stop()
