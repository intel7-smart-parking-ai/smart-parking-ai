"""Supervised printed-digit prototypes for the D-54 sticker sheet."""
import argparse
import os
from pathlib import Path
import tempfile
import zipfile

import cv2
import numpy as np

from .policy import CATALOG
from .sticker_geometry import rectify_sticker


def same_box(a,b):
    x,y,u,v=a;left,top,right,bottom=b
    overlap=max(0,min(u,right)-max(x,left))*max(0,min(v,bottom)-max(y,top))
    return overlap>=.60*max((u-x)*(v-y),(right-left)*(bottom-top))


def same_print_area(a, b):
    """A paper contour and an inner ink proposal can enclose the same two glyphs."""
    if same_box(a,b):
        return True
    outer,inner=sorted((a,b),key=lambda box:(box[2]-box[0])*(box[3]-box[1]),reverse=True)
    x,y,u,v=outer
    l,t,r,d=inner
    return (x<=l<r<=u and y<=t<d<=v and (r-l)*(d-t)>=.28*(u-x)*(v-y)
            and abs((x+u)-(l+r))<=.32*(u-x) and abs((y+v)-(t+d))<=.32*(v-y))


def paper_color_matches(crop, color):
    if crop.size == 0 or min(crop.shape[:2]) < 14:
        return False
    hsv = cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
    core = hsv[hsv.shape[0]//6:hsv.shape[0]*5//6,hsv.shape[1]//6:hsv.shape[1]*5//6]
    paper = core[(core[:,:,2] >= np.percentile(core[:,:,2],50)) & (core[:,:,2]>45)]
    if len(paper)<16:
        return False
    hue,sat,_ = paper.T
    if color=='white':
        consistent = sat < 65
    elif color=='green':
        consistent = (hue>=32)&(hue<=96)&(sat>=28)
    else:
        consistent = (hue>=90)&(hue<=140)&(sat>=35)
    return consistent.mean() >= .62


def ink_pair(crop):
    """Find the two ink components, ignoring dark surround and the printed border."""
    if crop.size == 0 or min(crop.shape[:2]) < 14:
        return None
    small = cv2.resize(crop, (80, 80), interpolation=cv2.INTER_LINEAR)
    value = small.max(axis=2)
    core = value[8:72, 8:72]
    low, high = np.percentile(core, (10, 85))
    if int(high) - int(low) < 25:
        return None
    threshold, _ = cv2.threshold(core, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = np.uint8(value <= threshold)
    _, labels, stats, centers = cv2.connectedComponentsWithStats(mask)
    components = []
    for i, (x, y, w, h, area) in enumerate(stats[1:], 1):
        if (80 <= area <= 1600 and 7 <= min(w, h) and max(w, h) <= 67
                and x > 0 and y > 0 and x+w < 80 and y+h < 80):
            components.append(i)
    if len(components) != 2:
        return None
    a, b = components
    delta = centers[b] - centers[a]
    if not 12 < np.linalg.norm(delta) < 48:
        return None
    center = (centers[a] + centers[b]) / 2
    if np.linalg.norm(center - [39.5, 39.5]) > 14:
        return None
    angle = float(np.degrees(np.arctan2(delta[1],delta[0])))
    # Canonical left-to-right ordering for the upright enrollment images.
    if angle > 90 or angle < -90:
        a, b = b, a
        angle = (angle + 360) % 360 - 180
    matrix = cv2.getRotationMatrix2D((39.5,39.5), angle, 1)
    glyphs = []
    for index in (a,b):
        rotated = cv2.warpAffine(np.uint8(labels == index), matrix, (80,80),
                                 flags=cv2.INTER_NEAREST)
        x,y,w,h = cv2.boundingRect(rotated)
        if not 10 <= h or not .17 <= w/h <= 1.20:
            return None
        glyph = cv2.resize(rotated[y:y+h,x:x+w].astype('float32'), (24,32),
                           interpolation=cv2.INTER_AREA)
        glyphs.append(cv2.GaussianBlur(glyph,(3,3),.7))
    return np.stack(glyphs)


class PrintedNumbers:
    def __init__(self, labels, glyphs):
        self.labels = list(labels)
        if set(self.labels) != set(CATALOG) or len(self.labels) != len(CATALOG):
            raise ValueError('인쇄 번호와 D-54 목록이 다릅니다.')
        glyphs = np.asarray(glyphs, dtype=np.float32)
        if glyphs.shape != (len(self.labels),2,32,24) or not np.isfinite(glyphs).all():
            raise ValueError('인쇄 번호 기준 형식 오류')
        self.digits = np.array(list(''.join(self.labels)))
        raw = glyphs.reshape(-1, 32*24)
        self.bank = raw / np.maximum(np.linalg.norm(raw,axis=1,keepdims=True),1e-6)
        self.digit_columns = [(int(digit), np.flatnonzero(self.digits == digit))
                              for digit in np.unique(self.digits)]
        self.six_bank = np.ascontiguousarray(
            self.bank[self.digits == '9'].reshape(-1,32,24)[:,::-1,::-1]).reshape(-1,768)

    @classmethod
    def load(cls, path):
        with np.load(path, allow_pickle=False) as data:
            return cls(data['labels'].tolist(), data['glyphs'])

    def read(self, crop, kind):
        glyphs = ink_pair(crop)
        if glyphs is None:
            return None
        both = np.stack([glyphs, glyphs[::-1,::-1,::-1]]).reshape(4,-1)
        both /= np.maximum(np.linalg.norm(both,axis=1,keepdims=True),1e-6)
        similarities = cv2.gemm(both,self.bank,1,None,0,flags=cv2.GEMM_2_T)
        digit_scores = np.zeros((4,10),np.float32)
        for digit, columns in self.digit_columns:
            digit_scores[:,digit] = similarities[:,columns].max(axis=1)
        # The supplied catalog contains no 6. A rotated 9 supplies an unknown
        # six-shaped competitor; do not force an unregistered pair into a known ID.
        digit_scores[:,6] = cv2.gemm(both,self.six_bank,1,None,0,flags=cv2.GEMM_2_T).max(axis=1)
        scores = {}
        for left,right in digit_scores.reshape(2,2,10):
            pair_scores = (left[:,None]+right[None,:])*.5
            for idx in np.argsort(pair_scores.ravel())[-2:]:
                i,j = divmod(int(idx),10)
                number = f'{i}{j}'
                score = float(pair_scores[i,j])
                if number not in scores or score > scores[number][0]:
                    scores[number] = (score, float(min(left[i],right[j])))
        ranked = sorted(scores, key=lambda n:scores[n][0],reverse=True)
        number = ranked[0]
        score, worst = scores[number]
        margin = score - scores[ranked[1]][0]
        if score < .88 or worst < .82 or margin < .022 or CATALOG.get(number) != kind:
            return None
        return {'number':number,'score':round(score,4),'margin':round(margin,4)}

    def read_box(self, frame, detection):
        x,y,u,v = detection['bbox_xyxy']
        if min(u-x,v-y)<14:
            return None
        kind = detection['vehicle_type_from_sticker']
        color = detection['sticker_color']
        crop = frame[y:v,x:u]
        if not paper_color_matches(crop, color):
            return None
        reads = []
        for pad in (0, -.07, .07):
            dx,dy = round((u-x)*pad),round((v-y)*pad)
            patch = frame[max(0,y-dy):v+dy,max(0,x-dx):u+dx]
            rect,changed = rectify_sticker(patch,color)
            for view in ((rect,patch) if changed else (patch,)):
                hit = self.read(view,kind)
                if hit:
                    reads.append(hit)
        if not reads or len({r['number'] for r in reads})!=1:
            return None
        return max(reads,key=lambda r:r['score'])

    def read_beside_light(self, frame, box, lens):
        """Read current ink and paper on the side facing away from a nearby LED."""
        x,y,u,v=box
        w,h=u-x,v-y
        if min(w,h)<20 or not .65<w/h<1.55:return None
        a,b,c,d=lens
        dx,dy=(a+c-x-u)/2,(b+d-y-v)/2
        if not .42<np.hypot(dx/w,dy/h)<1.9:return None
        crop=frame[y:v,x:u]
        hue,sat,val=cv2.split(cv2.cvtColor(crop,cv2.COLOR_BGR2HSV))
        yy,xx=np.ogrid[:h,:w]
        far=((xx-w/2)*dx+(yy-h/2)*dy<0)&(xx>w*.16)&(xx<w*.84)&(yy>h*.16)&(yy<h*.84)
        colored=far&(sat>=18)&(val>=55)
        usable=colored&(hue>26)&(hue<145)
        count=np.count_nonzero(usable)
        if count<max(30,.28*np.count_nonzero(far)):return None
        green=np.count_nonzero(usable&(hue>=36)&(hue<=90))/count
        blue=np.count_nonzero(usable&(hue>=98)&(hue<=135))/count
        if max(green,blue)<.80:return None
        color,kind=('green','ev') if green>blue else ('blue','accessible')
        reads=[]
        # Red-channel clipping can join ink strokes to a bright roof. Both
        # views retain the original two glyphs and use the same print bank.
        for sx,sy in ((0,0),(0,-1),(0,1),(-1,0),(1,0)):
            i,j=round(w*.065)*sx,round(h*.065)*sy
            if min(x+i,y+j)<0 or u+i>frame.shape[1] or v+j>frame.shape[0]:continue
            patch=frame[y+j:v+j,x+i:u+i]
            for view in (patch,cv2.cvtColor(patch[:,:,1],cv2.COLOR_GRAY2BGR)):
                hit=self.read(view,kind)
                if hit and hit['score']>=.94 and hit['margin']>=.04:reads.append(hit)
        if len(reads)<2 or len({r['number'] for r in reads})!=1:return None
        hit=max(reads,key=lambda r:r['score'])
        return dict(label='printed_'+color,sticker_color=color,vehicle_type_from_sticker=kind,
            color_name='초록색' if color=='green' else '파란색',bbox_xyxy=list(box),bbox_target='sticker',
            confidence=hit['score'],confidence_method='print_shape_similarity',print_read=hit,
            localization_source='led_adjacent_paper_and_print',car_id=None)

    def papers_beside_light(self, frame, polygon, lens):
        """Find a colored sheet partly illuminated by the LED, using current pixels."""
        x,y,w,h = cv2.boundingRect(polygon.astype(np.float32))
        x,y = max(0,x),max(0,y)
        tile = frame[y:y+h,x:x+w]
        if not tile.size:
            return []
        hue,sat,val = cv2.split(cv2.cvtColor(tile, cv2.COLOR_BGR2HSV))
        area = cv2.contourArea(polygon)
        found = []
        for color, hue_range in (('green',(32,96)), ('blue',(98,135))):
            mask = (hue>=hue_range[0]) & (hue<=hue_range[1]) & (sat>=18) & (val>=45)
            contours = cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
            for contour in contours:
                size = cv2.contourArea(contour)
                if not .012*area <= size <= .34*area:
                    continue
                a,b,c,d = cv2.boundingRect(contour)
                if not .65<c/d<1.55 or size<.34*c*d:
                    continue
                box = [x+a,y+b,x+a+c,y+b+d]
                read = self.read_beside_light(frame,box,lens)
                if read and read['sticker_color']==color and read['print_read']['score']>=.96:
                    found.append(read)
        return found

    def secondary_paper(self, frame, polygon, primary, excluded_box=None):
        """Find a separate paper-sized rectangle; this evidence may only veto a bay."""
        px,py,pu,pv=primary['bbox_xyxy']
        area=(pu-px)*(pv-py)
        x,y,w,h=cv2.boundingRect(polygon.astype(np.float32))
        tile=frame[y:y+h,x:x+w]
        if tile.size==0 or min(w,h)<24:return None
        hsv=cv2.cvtColor(tile,cv2.COLOR_BGR2HSV)
        value=hsv[:,:,2]
        # Level sets and saturation boundaries recover a label even when Geti
        # gives it the wrong color or the ink has blurred into the paper.
        for mask in (value>95,value>120,value>150,hsv[:,:,1]>48):
            contours=cv2.findContours(mask.astype(np.uint8),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
            for c in contours:
                size=cv2.contourArea(c)
                if not .53*area<=size<=1.55*area:continue
                quad=cv2.approxPolyDP(c,.035*cv2.arcLength(c,True),True)
                if len(quad)!=4 or not cv2.isContourConvex(quad):continue
                (_, _),(rw,rh),_=cv2.minAreaRect(c)
                if min(rw,rh)<15 or min(rw,rh)<.59*max(rw,rh) or size<.90*rw*rh:continue
                bx,by,bw,bh=cv2.boundingRect(c)
                box=[x+bx,y+by,x+bx+bw,y+by+bh]
                if (min(pu,box[2])>max(px,box[0]) and min(pv,box[3])>max(py,box[1])):continue
                if excluded_box and same_box(box,excluded_box):continue
                if any(cv2.pointPolygonTest(polygon,(float(x+a),float(y+b)),False)<0 for a,b in quad[:,0]):continue
                # Verify a bright rectangular sheet against its immediate surround
                # on all four sides. The dark marks must extend into its interior.
                points=quad[:,0].astype(np.float32)
                center=points.mean(axis=0)
                expanded=center+1.17*(points-center)
                matrix=cv2.getPerspectiveTransform(expanded,np.float32([[0,0],[63,0],[63,63],[0,63]]))
                paper=cv2.warpPerspective(hsv,matrix,(64,64))
                light=paper[:,:,2].astype(np.float32)
                if any(np.median(side[7:11,14:50])-np.median(side[:3,14:50])<25
                       for side in (np.rot90(light,i) for i in range(4))):continue
                core=light[14:50,14:50]
                if np.percentile(core,90)-np.percentile(core,10)<18:continue
                hue,sat,_=np.median(paper[7:56,7:56].reshape(-1,3),axis=0)
                color='white' if sat<65 else 'green' if 32<=hue<=96 else 'blue' if 90<=hue<=140 else None
                if color is None:continue
                name,kind={'white':('흰색','general'),'green':('초록색','ev'),'blue':('파란색','accessible')}[color]
                candidate=dict(label='possible_paper',bbox_xyxy=box,bbox_target='sticker',
                               sticker_color=color,color_name=name,vehicle_type_from_sticker=kind,
                               localization_source='secondary_paper_candidate',car_id=None,
                               confidence=None,confidence_method='hold_only')
                candidate['print_read']=self.read_box(frame,candidate)
                return candidate
        return None

    def locate_in_bay(self, frame, polygon):
        """Search rectangular paper patches in a bay classified as occupied."""
        left,top,w,h=cv2.boundingRect(polygon.astype(np.float32))
        left=max(0,left);top=max(0,top)
        tile=frame[top:top+h,left:left+w]
        if min(tile.shape[:2])<16:return []
        hsv=cv2.cvtColor(tile,cv2.COLOR_BGR2HSV)
        hue,sat,value=cv2.split(hsv)
        masks={'white':(sat<65)&(value>65),'green':(hue>=32)&(hue<=96)&(sat>28)&(value>45),
               'blue':(hue>=90)&(hue<=140)&(sat>35)&(value>45)}
        area=cv2.contourArea(polygon)
        recognized=[]
        # A colored roof can touch the printed paper. Use the paper's straight
        # luminance/saturation edges as a second source of quadrilaterals.
        edges=cv2.bitwise_or(cv2.Canny(value,18,56),cv2.Canny(sat,28,76))
        edges=cv2.morphologyEx(edges,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        smooth=cv2.GaussianBlur(tile,(3,3),0)
        gradient=cv2.morphologyEx(smooth,cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8)).max(axis=2)
        regions=[*masks.items(),(None,edges),(None,gradient<22),(None,gradient<32)]
        # Cut apart paper and similarly colored paint using the two ink strokes
        # as a geometric proposal. The same full print/color checks follow.
        diameter=max(7,min(31,round(np.sqrt(area)*.11)))|1
        contrast=cv2.morphologyEx(value,cv2.MORPH_BLACKHAT,
                                  cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(diameter,diameter)))
        for threshold in (22,45):
            contours=cv2.findContours(np.uint8(contrast>=threshold),cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
            glyphs=[]
            for contour in contours:
                a=cv2.contourArea(contour);x,y,gw,gh=cv2.boundingRect(contour)
                if (.0012*area<a<.035*area and max(gw,gh)<.40*np.sqrt(area)
                        and cv2.pointPolygonTest(polygon,(left+x+gw/2,top+y+gh/2),False)>=0):
                    glyphs.append((contour,a,np.float32([x+gw/2,y+gh/2])))
            if len(glyphs)>48:continue
            for i,(first,a,center) in enumerate(glyphs):
                for second,b,other in glyphs[i+1:]:
                    delta=other-center;dist=float(np.linalg.norm(delta))
                    if not .32<=a/b<=3.1 or not .07*np.sqrt(area)<dist<.28*np.sqrt(area):continue
                    axis=delta/dist;normal=np.array([-axis[1],axis[0]])
                    points=np.concatenate([first[:,0],second[:,0]])
                    rx=points@axis;ry=points@normal
                    width=float(np.ptp(rx));height=float(np.ptp(ry))
                    if not 1.0<width/max(1,height)<2.2 or not .36<dist/width<.8:continue
                    mid=axis*((rx.max()+rx.min())/2)+normal*((ry.max()+ry.min())/2)
                    dx=axis*width*.64;dy=normal*height*.77
                    window=np.rint([mid-dx-dy,mid+dx-dy,mid+dx+dy,mid-dx+dy]).astype(np.int32)
                    # Keep candidate rectangles separate even when they overlap.
                    regions.append((None,window.reshape(-1,1,2)))
        for color,mask in regions:
            if mask.ndim==3:
                contours=[mask]
            else:
                contours=cv2.findContours(mask.astype(np.uint8),cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)[0]
            for contour in contours:
                size=cv2.contourArea(contour)
                if not .012*area<=size<=.34*area:continue
                (cx,cy),(rw,rh),angle=cv2.minAreaRect(contour)
                if min(rw,rh)<12 or not .50<=rw/max(1,rh)<=2 or size<.76*rw*rh:continue
                if cv2.pointPolygonTest(polygon,(left+cx,top+cy),False)<0:continue
                rect=cv2.boxPoints(((cx,cy),(rw+1,rh+1),angle))
                quad=cv2.approxPolyDP(contour,.035*cv2.arcLength(contour,True),True)
                if len(quad)==4 and cv2.isContourConvex(quad):
                    rect=quad[:,0].astype(np.float32)
                    if cv2.contourArea(rect,oriented=True)<0:rect=rect[::-1].copy()
                rect=np.roll(rect,-int(np.argmin(rect.sum(axis=1))),axis=0)
                transform=cv2.getPerspectiveTransform(rect,np.float32([[0,0],[79,0],[79,79],[0,79]]))
                paper=cv2.warpPerspective(tile,transform,(80,80))
                outline=np.zeros(tile.shape[:2],np.uint8)
                cv2.fillConvexPoly(outline,rect.astype(int),1)
                support={k:np.count_nonzero(m & (outline!=0))/max(1,cv2.countNonZero(outline)) for k,m in masks.items()}
                paper_color=max(support,key=support.get)
                if support[paper_color]<(.65 if mask.ndim==3 else .50) or color and paper_color!=color:continue
                kind={'white':'general','green':'ev','blue':'accessible'}[paper_color]
                hit=self.read(paper,kind)
                if not hit or hit['score']<.93:continue
                if hit['margin']<.035:
                    inset=self.read(paper[3:-3,3:-3],kind)
                    if hit['score']<.96 or not inset or inset['number']!=hit['number']:continue
                x,y,bw,bh=cv2.boundingRect(contour)
                box=[left+x,top+y,left+x+bw,top+y+bh]
                if any(same_print_area(box,other['bbox_xyxy']) and paper_color==other['sticker_color']
                       and hit['number']==other['print_read']['number'] for other in recognized):
                    continue
                recognized.append(dict(label='printed_'+paper_color,sticker_color=paper_color,
                    color_name={'white':'흰색','green':'초록색','blue':'파란색'}[paper_color],
                    vehicle_type_from_sticker=kind,bbox_target='sticker',
                    bbox_xyxy=box,print_read=hit,
                    confidence=hit['score'],confidence_method='print_shape_similarity',
                    localization_source='paper_contour_and_print',car_id=None))
        return recognized


def enroll(archive, output):
    labels, glyphs = [], []
    with zipfile.ZipFile(archive) as source:
        entries = [p for p in source.infolist() if not p.is_dir() and p.filename.lower().endswith('.png')]
        for entry in sorted(entries,key=lambda p:p.filename):
            if entry.file_size > 2_000_000:
                raise ValueError('스티커 파일 크기 초과')
            number = Path(entry.filename).stem
            crop = cv2.imdecode(np.frombuffer(source.read(entry),np.uint8),cv2.IMREAD_COLOR)
            pair = ink_pair(crop) if crop is not None else None
            if pair is None:
                raise ValueError(f'{number} 기준에서 숫자 두 자리를 찾지 못했습니다.')
            labels.append(number)
            glyphs.append(pair)
    bank = PrintedNumbers(labels,glyphs)
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent,delete=False) as temp:
        tmp = Path(temp.name)
    try:
        with tmp.open('wb') as stream:
            np.savez_compressed(stream,labels=labels,glyphs=glyphs)
        os.replace(tmp,output)
    finally:
        tmp.unlink(missing_ok=True)
    return len(bank.labels)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('archive',type=Path)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'models'/'d54_print.npz')
    args = parser.parse_args()
    print('Registered printed numbers:',enroll(args.archive,args.output))
