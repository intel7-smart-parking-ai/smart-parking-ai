"""Find a luminous core surrounded by red light anywhere in the RGB frame."""
from itertools import chain
import cv2
import numpy as np
from .registration import assign_sticker, overlap_slot


def proposals(frame, *, clipped=False):
    # Candidate cores include camera-clipped white and yellow emitters.
    hot=cv2.inRange(frame,(160,210,225) if clipped else (35,140,218),(255,255,255))
    contours,_=cv2.findContours(hot,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    # On a white roof the broad core can merge with the bodywork. A second
    # contour isolates the bright center; both use the same trained halo test.
    h,w=hot.shape
    result=[]
    for contour in contours:
        x,y,cw,ch=cv2.boundingRect(contour)
        d=max(cw,ch)
        if not (2<=d<=100 and min(cw,ch)>=2 and min(cw,ch)/d>=.32 and cv2.contourArea(contour)>=1):
            continue
        cx=x+cw/2;cy=y+ch/2
        pad=max(5,round(d*1.45))
        x1,y1=max(0,int(cx)-pad),max(0,int(cy)-pad)
        x2,y2=min(w,int(cx)+pad+1),min(h,int(cy)+pad+1)
        patch=frame[y1:y2,x1:x2]
        if min(patch.shape[:2])<6:continue
        sample=cv2.resize(patch,(25,25),interpolation=cv2.INTER_AREA).astype(np.float32)
        b,g,r=cv2.split(sample)
        red=(r>125)&(r-np.maximum(g,b)>34)
        if red.mean()<.035:continue
        yy,xx=np.ogrid[-12:13,-12:13]
        radius=np.hypot(xx,yy)
        ring=(radius>=4)&(radius<=11)
        core=radius<=3
        outer=radius>=10
        dom=r-np.maximum(g,b)
        values=np.array([red[ring].mean(),np.mean(red & (radius<=10)),
                         r[core].mean()/255,g[core].mean()/255,b[core].mean()/255,
                         (r[core].mean()-r[outer].mean())/255, np.mean(dom[ring])/255,
                         np.mean((r[core]>220)&(g[core]>145)), min(cw,ch)/d,
                         min(red[ring&(xx<=0)].mean(),red[ring&(xx>=0)].mean(),
                             red[ring&(yy<=0)].mean(),red[ring&(yy>=0)].mean())])
        # The feature patch includes background for measuring contrast; report
        # only the emitting lens for slot/boundary assignment.
        lens=max(2,int(np.ceil(d*.62)))
        box=[max(0,int(cx)-lens),max(0,int(cy)-lens),min(w,int(cx)+lens+1),min(h,int(cy)+lens+1)]
        result.append((box,values))
    return result


def tinted_roof_proposals(frame):
    """Recover a dim core behind a cover using the unsaturated local red halo."""
    b,g,r=cv2.split(frame)
    dominance=cv2.subtract(r,cv2.max(g,b))
    red=cv2.bitwise_and(cv2.inRange(dominance,38,255),cv2.inRange(r,95,255))
    red=cv2.morphologyEx(red,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
    contours,_=cv2.findContours(red,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    height,width=r.shape
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if min(w,h)<5 or max(w,h)>140 or min(w,h)/max(w,h)<.30 or cv2.contourArea(contour)<16:
            continue
        # Estimate transmission on the halo itself. Nearby bare white paint
        # can be much brighter than the emitter behind the car window.
        mask=red[y:y+h,x:x+w]>0
        peak=int(np.percentile(r[y:y+h,x:x+w][mask],90))
        if not 137<=peak<=246:continue
        pad=max(w,h)
        left,top=max(0,x-pad),max(0,y-pad)
        patch=frame[top:min(height,y+h+pad),left:min(width,x+w+pad)]
        exposed=cv2.convertScaleAbs(patch,alpha=255/peak)
        for box,feat in proposals(exposed,clipped=True):
            a,b1,c,d=box
            cx,cy=left+(a+c)/2,top+(b1+d)/2
            if x<=cx<x+w and y<=cy<y+h and a>0 and b1>0 and c<patch.shape[1] and d<patch.shape[0]:
                yield [a+left,b1+top,c+left,d+top],feat


def cover_core(v):
    # A bright compact core, measurable local contrast, and red on every
    # side. A painted body, red power wire or streak alone lacks this pattern.
    visible_ring=v[9]>=.06 or (v[9]>=.02 and v[0]>=.15 and v[5]>=.45 and v[7]>=.80)
    return v[0]>=.12 and v[2]>=.92 and v[5]>=.25 and v[7]>=.75 and v[8]>=.48 and visible_ring


def red_halo(frame,box):
    x,y,u,v=box
    crop=frame[y:v,x:u]
    b,g,r=cv2.split(crop.astype(np.int16))
    hue=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)[:,:,0]
    halo=hue[(r>130)&(r-np.maximum(g,b)>35)]
    return len(halo)>=8 and np.mean((halo<=6)|(halo>=165))>=.28


def reflection_features(frame,box,v):
    x,y,u,bottom=box
    cx,cy=(x+u)//2,(y+bottom)//2
    radius=max(4,int((u-x)*1.5))
    patch=frame[max(0,cy-radius):cy+radius+1,max(0,cx-radius):cx+radius+1]
    b,g,r=cv2.split(cv2.resize(patch,(32,32),interpolation=cv2.INTER_AREA).astype(np.int16))
    yy,xx=np.ogrid[-16:16,-16:16]
    outer=np.hypot(xx,yy)>10
    red=(r-np.maximum(b,g)>30)&(r>70)
    return np.r_[v,[red[outer&m].mean() for m in (xx<0,xx>=0,yy<0,yy>=0)]]


class GlowModel:
    def __init__(self,path):
        with np.load(path,allow_pickle=False) as f:
            self.w,self.mu,self.sd=(f[n] for n in ('weights','center','scale'))
            self.bias=float(f['bias'].item())
            version=f['version'].item()
            if (version not in (1,2) or any(v.shape!=(10,) or not np.isfinite(v).all()
                    for v in (self.w,self.mu,self.sd)) or not np.isfinite(self.bias) or (self.sd<=0).any()):
                raise ValueError('LED 발광 모델 형식 오류')
            self.refs=f['roof_refs'] if version==2 else np.empty((0,10))
            self.radii=f['roof_radii'] if version==2 else np.empty(0)
            self.cover_refs=f['cover_refs'] if 'cover_refs' in f else np.empty((0,10))
            self.cover_radii=f['cover_radii'] if 'cover_radii' in f else np.empty(0)
            self.paint_refs=f['paint_refs'] if 'paint_refs' in f else np.empty((0,14))
            self.paint_radii=f['paint_radii'] if 'paint_radii' in f else np.empty(0)
            if (self.paint_refs.shape!=(self.paint_radii.size,14) or self.paint_radii.ndim!=1
                    or not np.isfinite(self.paint_refs).all() or not np.isfinite(self.paint_radii).all()
                    or (self.paint_radii<=0).any()):raise ValueError('반사광 기준 형식 오류')
            if (self.cover_refs.shape!=(self.cover_radii.size,10) or self.cover_radii.ndim!=1
                    or not np.isfinite(self.cover_refs).all() or not np.isfinite(self.cover_radii).all()
                    or (self.cover_radii<=0).any()):raise ValueError('LED 덮개 발광 기준 형식 오류')
            if (self.refs.shape!=(self.radii.size,10) or self.radii.ndim!=1 or
                    not np.isfinite(self.refs).all() or not np.isfinite(self.radii).all() or (self.radii<=0).any()):
                raise ValueError('차량 지붕 LED 기준 형식 오류')

    def detect(self,frame):
        hits=[]
        candidates=((box,v,False) for box,v in proposals(frame))
        recovered=((box,v,True) for box,v in chain(proposals(frame,clipped=True),tinted_roof_proposals(frame))) if self.refs.size else ()
        for box,v,tinted in chain(candidates,recovered):
            z=(v-self.mu)/self.sd
            score=0. if tinted else float(1/(1+np.exp(-np.clip(z@self.w+self.bias,-40,40))))
            if self.refs.size and v[9]>.45 and v[7]>.75:
                distance=(np.linalg.norm(self.refs-z,axis=1)/self.radii).min()
                if distance<1:
                    score=max(score,float(.85+.14*(1-distance)))
            if self.cover_refs.size and cover_core(v):
                distance=(np.linalg.norm(self.cover_refs-z,axis=1)/self.cover_radii).min()
                if distance<1 and red_halo(frame,box):score=max(score,float(.85+.14*(1-distance)))
            # Small specular points on glossy red paint also have a red rim.
            # Require the high-confidence limit through the observed 9px scale.
            tiny=min(box[2]-box[0],box[3]-box[1])<=11
            if score>=(.97 if tiny else .85) and v[0]>=.045 and v[5]>=.08 and v[9]>=.01:
                # An orange body can surround a white specular point on every side.
                # Inspect the saturated halo; the bright core itself loses hue.
                x,y,u,bottom=box
                hsv=cv2.cvtColor(frame[y:bottom,x:u],cv2.COLOR_BGR2HSV)
                hues=hsv[:,:,0][(hsv[:,:,1]>70)&(hsv[:,:,2]>120)]
                if (len(hues)>=8 and np.mean((hues>6)&(hues<28))>=.72
                        and np.mean((hues<=6)|(hues>=165))<.26):
                    continue
                if self.paint_refs.size:
                    signature=reflection_features(frame,box,v)
                    if np.any(np.linalg.norm(self.paint_refs-signature,axis=1)<self.paint_radii):continue
                hits.append(dict(bbox_xyxy=box,confidence=score,state='confirmed',method='red_emission'))
        selected=[]
        for hit in sorted(hits,key=lambda d:-d['confidence']):
            x,y,u,v=hit['bbox_xyxy']
            if not any(a<=(x+u)/2<=b and c<=(y+v)/2<=d for a,c,b,d in (h['bbox_xyxy'] for h in selected)):
                selected.append(hit)
        return selected


def localize(lights, outline, polygons):
    for light in lights:
        box=light['bbox_xyxy']
        light.update(space_id=None, location='unregistered')
        if outline is None:continue
        if overlap_slot(box,outline)<.95:
            light['location']='outside'
            continue
        slot=assign_sticker(box,polygons)
        if slot and overlap_slot(box,polygons[slot])>.85:
            light.update(space_id=slot,location='space')
            x,y,u,v=box
            light['bay_area_fraction']=(u-x)*(v-y)/max(1.,cv2.contourArea(np.float32(polygons[slot])))
        elif any(overlap_slot(box,p)>.02 for p in polygons.values()):
            light['location']='boundary'
        else:light['location']='aisle'
    return lights
