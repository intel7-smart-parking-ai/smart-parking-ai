"""Secondary print comparison using only the ink on the six enrolled stickers."""
from functools import lru_cache
from pathlib import Path
import cv2
import numpy as np


@lru_cache(maxsize=1)
def templates():
    bank=[];keys=[]
    for path in sorted((Path(__file__).resolve().parent/'configs/sticker_print').glob('*.png')):
        raw=cv2.imdecode(np.frombuffer(path.read_bytes(),np.uint8),cv2.IMREAD_GRAYSCALE)
        square=cv2.resize(raw,(40,40),interpolation=cv2.INTER_AREA)
        for scale in (.86,1.,1.14):
            for deg in range(0,360,12):
                matrix=cv2.getRotationMatrix2D((19.5,19.5),deg,scale)
                img=cv2.warpAffine(square,matrix,(40,40))
                img=cv2.GaussianBlur(img,(5,5),1.05)[6:34,6:34].astype('float32')
                img-=img.mean()
                bank.append((img/max(1,np.linalg.norm(img))).ravel())
                keys.append(path.stem)
    return np.asarray(bank),np.asarray(keys)


def compare(image,candidate):
    bank,keys=templates()
    if not len(keys):return None
    cx,cy,ra,rb,angle=candidate['disc']
    u,v=np.meshgrid((np.arange(40,dtype='float32')-19.5)/20,(np.arange(40,dtype='float32')-19.5)/20)
    t=np.deg2rad(angle);cs,sn=np.cos(t),np.sin(t)
    mx=(cx+ra*cs*u-rb*sn*v).astype('float32');my=(cy+ra*sn*u+rb*cs*v).astype('float32')
    patch=cv2.remap(image,mx,my,cv2.INTER_CUBIC)
    g=cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY)
    # Black-hat removes low-frequency lighting gradients while retaining ink.
    bg=cv2.morphologyEx(g,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(15,15)))
    ink=cv2.subtract(bg,g).astype('float32')
    ink[u*u+v*v>.76**2]=0
    crops=np.lib.stride_tricks.sliding_window_view(ink,(28,28))[2:11:2,2:11:2]
    batch=np.ascontiguousarray(crops.reshape(-1,28*28))
    batch-=batch.mean(axis=1,keepdims=True)
    batch/=np.linalg.norm(batch,axis=1,keepdims=True).clip(1)
    scores=cv2.gemm(bank,batch,1,None,0,flags=cv2.GEMM_2_T)
    ranks=sorted(((float(scores[keys==n].max()),str(n)) for n in np.unique(keys)),reverse=True)
    return {'number':ranks[0][1],'score':ranks[0][0],'margin':ranks[0][0]-ranks[1][0],'alternate':ranks[1][1]}
