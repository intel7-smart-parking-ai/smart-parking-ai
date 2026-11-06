"""Orientation-independent printed-number candidates for circular physical labels."""
from functools import lru_cache
import cv2
import numpy as np
from PIL import Image,ImageDraw,ImageFont


def feature(mask):
    x,y,w,h=cv2.boundingRect(mask)
    if min(w,h)<2:
        return None
    factor=min(34/w,28/h)
    q=cv2.resize(mask[y:y+h,x:x+w],(max(1,round(w*factor)),max(1,round(h*factor))),interpolation=cv2.INTER_AREA)
    frame=np.zeros((32,38),np.uint8)
    j,i=(32-q.shape[0])//2,(38-q.shape[1])//2
    frame[j:j+q.shape[0],i:i+q.shape[1]]=q
    vector=cv2.GaussianBlur(frame,(3,3),.8).astype('float32').ravel()
    return vector/max(1.,np.linalg.norm(vector))


@lru_cache(maxsize=1)
def alphabet():
    fonts=[]
    for name in ('arialbd.ttf','segoeuib.ttf','arial.ttf','DejaVuSans-Bold.ttf'):
        try:fonts.append(ImageFont.truetype(name,48))
        except OSError:continue
    if not fonts:fonts.append(ImageFont.load_default())
    features=[]
    for font in fonts:
        for n in range(10,100):
            im=Image.new('L',(120,82));ImageDraw.Draw(im).text((4,0),str(n),fill=255,font=font)
            features.append(feature(np.asarray(im)))
    return np.stack(features)


def examine(image,candidate):
    cx,cy,ra,rb,angle=candidate['disc']
    # Normalize the ellipse and the illumination gradient before testing orientations.
    a=np.linspace(-1,1,49,dtype='float32');u,v=np.meshgrid(a,a)
    t=np.deg2rad(angle);cs,sn=np.cos(t),np.sin(t)
    mx=(cx+ra*cs*u-rb*sn*v).astype('float32')
    my=(cy+ra*sn*u+rb*cs*v).astype('float32')
    bgr=cv2.remap(image,mx,my,cv2.INTER_CUBIC)
    gray=cv2.cvtColor(bgr,cv2.COLOR_BGR2GRAY)
    dist=np.hypot(u,v);inside=dist<.76
    background=cv2.morphologyEx(gray,cv2.MORPH_CLOSE,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(15,15)))
    ink=cv2.subtract(background,gray)
    peak=np.percentile(ink[inside],98)
    if peak<11:return None
    level=max(7.,cv2.threshold(ink[inside],0,255,cv2.THRESH_OTSU)[0])
    mask=((ink>level)&inside).astype('uint8')
    _,lab,stats,_=cv2.connectedComponentsWithStats(mask)
    pieces=[]
    for i,(_,_,w,h,area) in enumerate(stats[1:],1):
        if 8<=area<=400 and np.mean(dist[lab==i]>.69)<.22:
            pieces.append(i)
    if not 1<=len(pieces)<=3:return None
    mask=(np.isin(lab,pieces).astype('uint8'))*255
    x,y,w,h=cv2.boundingRect(mask)
    if not 10<=max(w,h)<=38 or min(w,h)<5 or mask.sum()/255<32:return None
    contrast=(255-np.clip(ink.astype(float)*255/max(level*1.6,1),0,255)).astype('uint8')
    contrast[~inside]=255
    source=np.column_stack((mx[mask>0],my[mask>0]))
    lower=np.maximum(0,np.floor(source.min(axis=0))).astype(int)
    upper=np.minimum(image.shape[1::-1],np.ceil(source.max(axis=0))+1).astype(int)
    box=lower.tolist()+upper.tolist()
    variations=[];features=[]
    for degrees in range(0,360,12):
        transform=cv2.getRotationMatrix2D((24,24),degrees,1.)
        rotated=cv2.warpAffine(mask,transform,(49,49))
        binary=(rotated>115).astype('uint8')*255
        descr=feature(binary)
        if descr is not None:
            features.append(descr);variations.append((degrees,binary,transform))
    if not features:return None
    scores=cv2.gemm(np.stack(features),alphabet(),1,None,0,flags=cv2.GEMM_2_T).reshape(len(features),-1,90).max(axis=1)
    index=np.argmax(scores.max(axis=0))
    by_digit=scores.max(axis=0);order=np.argsort(by_digit)
    orientation=variations[int(np.argmax(scores[:,index]))]
    degrees,binary,transform=orientation
    x,y,w,h=cv2.boundingRect(binary)
    views=[]
    for bg in (cv2.warpAffine(contrast,transform,(49,49),borderValue=255),255-binary):
        scaled=cv2.resize(bg[y:y+h,x:x+w],None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
        margin=max(3,round(.14*scaled.shape[0]))
        views.append(cv2.copyMakeBorder(scaled,margin,margin,margin,margin,cv2.BORDER_CONSTANT,value=255))
    return {'number':str(index+10),'score':float(by_digit[index]),'margin':float(by_digit[index]-by_digit[order[-2]]),
            'alternate':str(order[-2]+10),'box':box,'angle':degrees,'views':views}
