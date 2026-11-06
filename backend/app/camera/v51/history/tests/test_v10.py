import unittest
from types import SimpleNamespace
from intel_ocr import select_device
class DeviceSelection(unittest.TestCase):
    def torch(self,xpu,cuda):return SimpleNamespace(xpu=SimpleNamespace(is_available=lambda:xpu),cuda=SimpleNamespace(is_available=lambda:cuda))
    def test_intel_priority(self):self.assertEqual(select_device(self.torch(True,True),'auto'),'xpu')
    def test_cpu_fallback(self):self.assertEqual(select_device(self.torch(False,False),'auto'),'cpu')
    def test_cuda_retained(self):self.assertEqual(select_device(self.torch(False,True),'auto'),'cuda')
    def test_explicit_intel_failure(self):
        with self.assertRaises(RuntimeError):select_device(self.torch(False,False),'xpu')
    def test_explicit_cpu(self):self.assertEqual(select_device(self.torch(True,True),'cpu'),'cpu')

class AdapterTests(unittest.TestCase):
    def run_adapter(self,requested,fail=False):
        from unittest.mock import patch,MagicMock
        from pathlib import Path
        from intel_ocr import build_reader
        detector=MagicMock();recognizer=MagicMock()
        detector.to.return_value=detector;detector.eval.return_value=detector
        recognizer.to.return_value=recognizer;recognizer.eval.return_value=recognizer
        calls=[]
        class Reader:
            def __init__(self,languages,**kwargs):
                calls.append(kwargs);self.detector=detector;self.recognizer=recognizer
            def detect(self,image):
                if fail:raise RuntimeError('unsupported GPU operator')
            def recognize(self,*args,**kwargs):return []
        torch=SimpleNamespace(__version__='test',xpu=SimpleNamespace(is_available=lambda:True,synchronize=lambda:None,get_device_name=lambda n:'Intel test'),cuda=SimpleNamespace(is_available=lambda:False))
        easyocr=SimpleNamespace(__version__='1.7.2',Reader=Reader)
        with patch.dict('sys.modules',torch=torch,easyocr=easyocr),patch('intel_ocr.logging.exception'):
            result=build_reader(Path('.'),{'device':requested,'download_models':False})
        return result,calls,detector,recognizer
    def test_both_models_moved_and_quantization_disabled(self):
        (reader,info),calls,d,r=self.run_adapter('xpu')
        d.to.assert_called_once_with('xpu');r.to.assert_called_once_with('xpu')
        self.assertFalse(calls[0]['quantize']);self.assertEqual(info['device'],'xpu');self.assertEqual(reader.device,'xpu')
    def test_auto_fallback_is_recorded(self):
        (_,info),calls,_,_=self.run_adapter('auto',True)
        self.assertEqual(info['device'],'cpu');self.assertIn('unsupported',info['fallback_reason']);self.assertEqual(len(calls),2)
    def test_explicit_xpu_never_silently_falls_back(self):
        with self.assertRaises(RuntimeError):self.run_adapter('xpu',True)
