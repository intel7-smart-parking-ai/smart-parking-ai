"""Read two separate ink marks on a localized colored sticker in their own orientation."""
import cv2
import numpy as np

from analysis import color_candidate, number


METHODS=('ink_pair_contrast','ink_pair_binary')


def digit_views(image, polygon):
    color=color_candidate(image,polygon)['name']
    if color not in ('green','blue'):
        return
    q=np.int32(polygon)
    x,y,w,h=cv2.boundingRect(q)
    if min(w,h)<12 or max(w,h)>160:
        return
    crop=image[y:y+h,x:x+w]
    hue,saturation,value=cv2.split(cv2.cvtColor(crop,cv2.COLOR_BGR2HSV))
    low,high=(90,136) if color=='blue' else (40,90)
    mask=np.zeros((h,w),np.uint8)
    cv2.fillConvexPoly(mask,q-[x,y],255)
    mask[(hue<low)|(hue>=high)|(saturation<=25)|(value<=50)]=0
    contours=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
    if not contours:
        return
    mask.fill(0)
    cv2.drawContours(mask,[max(contours,key=cv2.contourArea)],-1,255,-1)
    # Zero padding also defines the silhouette at the crop's outermost pixel.
    dist=cv2.distanceTransform(np.pad(mask,1),cv2.DIST_L2,5)[1:-1,1:-1]
    core=dist>=max(2.,dist.max()*.28)
    if core.sum()<50:
        return
    scale=4
    gray=cv2.resize(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
    core=cv2.resize(core.astype('uint8'),gray.shape[::-1],interpolation=cv2.INTER_NEAREST)>0
    radius=max(3,min(24,round(min(w,h)*scale*.075)))
    kernel=cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*radius+1,2*radius+1))
    contrast=cv2.morphologyEx(gray,cv2.MORPH_BLACKHAT,kernel)
    peak=np.percentile(contrast[core],99)
    if peak<12:
        return
    for fraction in (.35,.45,.55,.65):
        threshold=max(5.,peak*fraction)
        mask=(core&(contrast>=threshold)).astype('uint8')
        _,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
        glyphs=[i for i,row in enumerate(stats[1:],1) if row[-1]>=.008*core.sum()
                and not np.any(cv2.dilate((labels==i).astype('uint8'),None)[~core])]
        if len(glyphs)!=2:
            continue
        first,second=glyphs
        sizes=stats[glyphs,cv2.CC_STAT_AREA]
        if min(sizes)<.4*max(sizes) or sizes.sum()>.44*core.sum():
            continue
        a,b=centers[glyphs]
        delta=b-a
        if np.linalg.norm(delta)<scale*2:
            continue
        stroke=np.isin(labels,glyphs).astype('uint8')
        left,top,width,height=cv2.boundingRect(stroke)
        box=[x+left//scale,y+top//scale,x+(left+width+scale-1)//scale,y+(top+height+scale-1)//scale]
        from sticker_ocr import overlap_fraction
        if overlap_fraction(box,polygon)<.6:
            continue
        gray_text=(255-np.clip(contrast.astype(float)*255/(threshold*1.6),0,255)).astype('uint8')
        gray_text[~core]=255
        angle=np.degrees(np.arctan2(delta[1],delta[0]))
        for degrees in (angle,angle+180):
            transform=cv2.getRotationMatrix2D(tuple((a+b)/2),degrees,1.)
            extent=cv2.warpAffine(stroke,transform,gray.shape[::-1],flags=cv2.INTER_NEAREST)
            xx,yy,ww,hh=cv2.boundingRect(extent)
            if not ww or not hh:
                continue
            for method,raw in zip(METHODS,(gray_text,255-255*stroke)):
                rotated=cv2.warpAffine(raw,transform,gray.shape[::-1],borderValue=255)
                margin=max(3,round(hh*(.19 if method=='ink_pair_binary' else .14)))
                pixels=cv2.copyMakeBorder(rotated[yy:yy+hh,xx:xx+ww],margin,margin,margin,margin,cv2.BORDER_CONSTANT,value=255)
                yield method,pixels,box,float(degrees%360)
        return


def recognize_digits(image,polygon,reader,cfg):
    recognize=getattr(reader,'recognize',None)
    candidates=[]
    if not callable(recognize):
        return candidates
    for method,pixels,box,angle in digit_views(image,polygon):
        h,w=pixels.shape
        hits=recognize(pixels,horizontal_list=[[0,w,0,h]],free_list=[],allowlist='0123456789',detail=1,paragraph=False)
        if not isinstance(hits,(list,tuple)) or len(hits)!=1:
            continue
        _,text,score=hits[0]
        if not isinstance(text,str) or not np.isfinite(score) or not 0<=score<=1:
            continue
        value=number(text,cfg)
        status='invalid_number' if value is None else 'low_score' if score<cfg['ocr_min_score'] else 'candidate'
        candidates.append({'text':text,'number_candidate':value,'score':float(score),'text_bbox':box,
            'status':status,'method':method,'rotation_degrees':angle,'selected_in_view':False})
    return candidates
