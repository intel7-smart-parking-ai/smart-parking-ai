"""EasyOCR 1.7.2 single Intel GPU adapter; no global library patching."""
import logging

def select_device(torch,requested):
    if requested not in ('auto','cpu','cuda','xpu'):raise ValueError('Unknown device')
    xpu=hasattr(torch,'xpu') and torch.xpu.is_available()
    cuda=torch.cuda.is_available()
    if requested=='auto':return 'xpu' if xpu else 'cuda' if cuda else 'cpu'
    if requested=='xpu' and not xpu:raise RuntimeError('Intel GPU unavailable. Run install_intel_gpu.bat and check the Intel graphics driver.')
    if requested=='cuda' and not cuda:raise RuntimeError('CUDA unavailable')
    return requested

def build_reader(project,config):
    import torch
    import easyocr
    requested=config['device'];device=select_device(torch,requested)
    info={'engine':'easyocr','version':easyocr.__version__,'torch':torch.__version__,
          'languages':config.get('ocr_languages',['en']),'requested_device':requested,
          'device':device,'gpu':None,'fallback_reason':None}
    from pathlib import Path
    import os
    model_dir=Path(config.get('model_directory') or os.environ.get('SMART_PARKING_MODEL_DIR') or project/'models').expanduser()
    if not model_dir.is_absolute():model_dir=project/model_dir
    kwargs=dict(model_storage_directory=str(model_dir),user_network_directory=str(project/'models'/'user_network'),
                download_enabled=config['download_models'])
    def cpu():return easyocr.Reader(info['languages'],gpu=False,**kwargs)
    if device=='xpu':
        try:
            if easyocr.__version__!='1.7.2':raise RuntimeError('Intel adapter requires easyocr==1.7.2')
            class IntelReader(easyocr.Reader):
                def initDetector(self,path):
                    # 1.7.2 stores self.quantize as a tuple; explicitly disable it.
                    return self.get_detector(path,device='cpu',quantize=False,cudnn_benchmark=False)
            reader=IntelReader(info['languages'],gpu=False,quantize=False,**kwargs)
            reader.detector=reader.detector.to('xpu').eval()
            reader.recognizer=reader.recognizer.to('xpu').eval()
            reader.device='xpu'
            # Exercise detection AND recognition, even if the detector finds no text.
            import numpy as np
            sample=np.full((64,192,3),255,np.uint8)
            reader.detect(sample)
            reader.recognize(sample[:,:,0],horizontal_list=[[0,192,0,64]],free_list=[],workers=0)
            torch.xpu.synchronize()
            info['gpu']=torch.xpu.get_device_name(0)
        except Exception as exc:
            if requested!='auto':raise RuntimeError('Intel OCR initialization/inference failed: '+str(exc)) from exc
            logging.exception('Intel OCR startup failed; falling back to CPU')
            info.update(device='cpu',gpu=None,fallback_reason=str(exc))
            reader=cpu()
    elif device=='cuda':
        reader=easyocr.Reader(info['languages'],gpu=True,**kwargs)
        info['gpu']=torch.cuda.get_device_name(0)
    else:
        reader=cpu()
        if requested=='auto':info['fallback_reason']='No available XPU/CUDA device in this PyTorch installation'
    return reader,info

def device_report():
    import torch
    result={'torch':torch.__version__,'xpu_available':hasattr(torch,'xpu') and torch.xpu.is_available(),'cuda_available':torch.cuda.is_available()}
    if result['xpu_available']:result['intel_gpu']=torch.xpu.get_device_name(0)
    return result
