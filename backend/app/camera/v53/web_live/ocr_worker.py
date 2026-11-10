"""Optional long-lived EasyOCR subprocess. JSON lines in/out; no camera access."""
import base64
import json
import os
import sys
import re
import contextlib


def read_number(reader, crop):
    import cv2
    import numpy as np
    votes = []
    # The supplied printed stickers are square. Perspective-corrected board
    # samples stretch them horizontally; restore a square before text reading.
    square=cv2.resize(crop,(160,160),interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(square,cv2.COLOR_BGR2HSV)[:,:,2]
    luminance=cv2.cvtColor(square,cv2.COLOR_BGR2GRAY)
    low,high=np.percentile(gray,[8,90])
    normalized=np.clip((gray.astype(float)-low)*255/max(high-low,1),0,255).astype('uint8')
    # Classifier candidates are not fed into OCR: the alphabet is all digits.
    # Try a tighter crop only when the detector crop lacks sufficient evidence.
    for margin in (8,20):
        for k in range(4):
            for method,source in [('value',gray),('normalized',normalized),('luminance',luminance)]:
                pixels=np.ascontiguousarray(np.rot90(source,k))
                hits=reader.recognize(pixels,horizontal_list=[[margin,160-margin,margin,160-margin]],free_list=[],
                    allowlist='0123456789',detail=1,paragraph=False,decoder='greedy')
                # Never concatenate separate characters or text regions.
                if len(hits)!=1:continue
                _,text,score=hits[0]
                if re.fullmatch(r'[1-9][0-9]',text) and score>=.65:
                    votes.append({'number':text,'confidence':float(score),'rotation':k*90,'method':method,'margin':margin})
        supported={v['number'] for v in votes if len({x['method'] for x in votes if x['number']==v['number'] and
                   x['rotation']==v['rotation'] and x['confidence']>=.8})>=2}
        if len(supported)>1:
            return {'number':None,'confidence':0.,'reason':'방향별 번호 충돌','votes':votes}
        if len(supported)==1:
            number=next(iter(supported));best=max((v for v in votes if v['number']==number),key=lambda v:v['confidence'])
            return dict(best,reason='OCR',votes=votes)
    return {'number':None,'confidence':0.,'reason':'OCR 근거 부족','votes':votes}


def main():
    with contextlib.redirect_stdout(sys.stderr):
        import cv2
        import numpy as np
        import torch
        import easyocr
        torch.set_num_threads(2);cv2.setNumThreads(1)
        kwargs = {'gpu':False,'verbose':False,'download_enabled':False}
        if os.environ.get('V53_OCR_MODELS'):
            kwargs['model_storage_directory']=os.environ['V53_OCR_MODELS']
        reader = easyocr.Reader(['ko','en'],**kwargs)
    print(json.dumps({'ready':True}),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line)
            crop=cv2.imdecode(np.frombuffer(base64.b64decode(request['image']),np.uint8),cv2.IMREAD_COLOR)
            if crop is None or max(crop.shape)>1024:
                raise ValueError('crop')
            with contextlib.redirect_stdout(sys.stderr):
                result=read_number(reader,crop)
            print(json.dumps({'id':request['id'],'result':result}),flush=True)
        except Exception:
            print(json.dumps({'error':'OCR 처리 오류'}),flush=True)


if __name__=='__main__':
    main()
