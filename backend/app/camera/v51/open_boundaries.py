"""Three painted sides define an open parking bay; the entrance is virtual."""
import math
import cv2
import numpy as np


def cross2(a,b):return float(a[0]*b[1]-a[1]*b[0])


def line_intersection(a,b):
    p,q=np.asarray(a,float);r,s=np.asarray(b,float);u=q-p;v=s-r
    determinant=cross2(u,v)
    if abs(determinant)<1e-6:return None
    return p+u*cross2(r-p,v)/determinant


def white_lines(image,gap=6,merge_gap=8):
    scale=min(1.,1800/max(image.shape[:2]));small=cv2.resize(image,None,fx=scale,fy=scale) if scale<1 else image
    hsv=cv2.cvtColor(small,cv2.COLOR_BGR2HSV)
    # Paint is bright and low saturation. Colored bay floors and yellow stops
    # must not supply the three white boundaries.
    local=cv2.medianBlur(hsv[:,:,2],31)
    mask=((hsv[:,:,1]<65)&(hsv[:,:,2]>130)&(hsv[:,:,2].astype(np.int16)-local.astype(np.int16)>22)).astype(np.uint8)*255
    raw=cv2.HoughLinesP(mask,1,np.pi/720,threshold=35,minLineLength=max(32,min(small.shape[:2])*.035),maxLineGap=gap)
    groups=[]
    if raw is None:return [],mask,scale
    for row in sorted(raw.reshape(-1,4),key=lambda r:np.linalg.norm(r[2:]-r[:2]),reverse=True):
        p=np.float64(row[:2]);q=np.float64(row[2:]);direction=(q-p)/np.linalg.norm(q-p)
        if direction[0]<0 or (abs(direction[0])<1e-8 and direction[1]<0):p,q=q,p;direction=-direction
        found=None
        for group in groups:
            if abs(np.dot(direction,group['direction']))<math.cos(math.radians(2.5)):continue
            normal=np.array([-group['direction'][1],group['direction'][0]])
            if max(abs(np.dot(p-group['origin'],normal)),abs(np.dot(q-group['origin'],normal)))>5.:continue
            points=np.asarray(group['points']);proj=points@group['direction'];new=np.array([p,q])@group['direction']
            if min(new)>max(proj)+merge_gap or max(new)<min(proj)-merge_gap:continue
            found=group;break
        if found is None:groups.append({'origin':(p+q)/2,'direction':direction,'points':[p,q]})
        else:found['points'].extend([p,q])
    lines=[]
    for group in groups:
        points=np.float32(group['points']);vx,vy,x,y=cv2.fitLine(points,cv2.DIST_L2,0,.01,.01).reshape(-1)
        direction=np.array([vx,vy],float);origin=np.array([x,y],float)
        projection=(points-origin)@direction;p=origin+direction*projection.min();q=origin+direction*projection.max()
        if np.linalg.norm(q-p)>=max(35,min(small.shape[:2])*.04):lines.append(np.array([p,q]))
    return lines,mask,scale


def support(mask,a,b):
    length=np.linalg.norm(b-a);n=max(15,int(length));values=[]
    for t in np.linspace(.07,.93,n):
        x,y=np.rint(a+(b-a)*t).astype(int)
        values.append(bool(mask[max(0,y-3):y+4,max(0,x-3):x+4].any()))
    return float(np.mean(values))


def extract_open_quads(image):
    # Keep short-gap endpoints as well as the more tolerant candidates used
    # for tape/seam interruptions. A long bridge cannot erase a real entrance.
    return _extract_open_quads(image,6,8)+_extract_open_quads(image,15,24)


def painted_closed_quads(image,polygons):
    """Identify actual four-painted-side legacy bays without assuming closure."""
    _,mask,scale=white_lines(image)
    result=[]
    for polygon in polygons:
        points=np.float64(polygon)*scale
        scores=[support(mask,a,b) for a,b in zip(points,np.roll(points,-1,axis=0))]
        candidate = {'polygon':polygon, '_painted_closed':min(scores)>=.72}
        gap = int(np.argmin(scores))
        if scores[gap] < .35 and sum(score >= .72 for score in scores) == 3:
            # Canonical order: the last two vertices bound the unpainted entrance.
            ordered = [polygon[(gap+i)%4] for i in (2,3,0,1)]
            candidate['_three_painted'] = {'polygon':ordered, 'boundary_kind':'open_u',
                                           'line_support':[scores[(gap+i)%4] for i in (1,2,3)]}
        result.append(candidate)
    return result


def _extract_open_quads(image,gap,merge_gap):
    lines,mask,scale=white_lines(image,gap,merge_gap);candidates=[]
    h,w=mask.shape
    for i,first in enumerate(lines):
        p,q=first;d=q-p;length=np.linalg.norm(d);d=d/length
        for second in lines[i+1:]:
            r,s=second;other=s-r;other_length=np.linalg.norm(other);other=other/other_length
            if abs(np.dot(d,other))<math.cos(math.radians(12)):continue
            gap=abs(cross2(d,(r+s-p-q)/2))
            if not max(22,min(h,w)*.018)<gap<min(h,w)*.40:continue
            if max(length,other_length)>min(length,other_length)*1.6:continue
            if not .6<length/gap<6.5 or not .6<other_length/gap<6.5:continue
            for back in lines:
                bd=back[1]-back[0];back_length=np.linalg.norm(bd);bd=bd/back_length
                if abs(np.dot(d,bd))>math.cos(math.radians(30)):continue
                a=line_intersection(first,back);b=line_intersection(second,back)
                if a is None or b is None:continue
                tolerance=max(8,min(length,other_length)*.08)
                ta=np.dot(a-p,d);tb=np.dot(b-r,other)
                if min(abs(ta),abs(ta-length))>tolerance or min(abs(tb),abs(tb-other_length))>tolerance:continue
                if any(np.dot(pt-back[0],bd)<-tolerance or np.dot(pt-back[0],bd)>back_length+tolerance for pt in (a,b)):continue
                c=s if abs(tb)<abs(tb-other_length) else r
                e=q if abs(ta)<abs(ta-length) else p
                if np.dot(e-a,c-b)<=0:continue
                quad=np.float32([a,b,c,e])
                if (quad<1).any() or (quad[:,0]>=w-1).any() or (quad[:,1]>=h-1).any():continue
                if not cv2.isContourConvex(quad) or not .0015*h*w<cv2.contourArea(quad)<.25*h*w:continue
                supports=[support(mask,e,a),support(mask,a,b),support(mask,b,c)]
                if min(supports)<.72:continue
                # The virtual fourth side is deliberately not required to have paint.
                points=np.rint(quad/scale).astype(int).tolist()
                candidates.append({'polygon':points,'boundary_kind':'open_u',
                    'painted_edges':[[points[3][:],points[0][:]],[points[0][:],points[1][:]],[points[1][:],points[2][:]]],
                    'entrance_edge':[points[2][:],points[3][:]],'line_support':supports})
    return candidates


def matches_name_spacing(candidate,sid,names):
    """Measure a single numbered bay even when intermediate names are occluded."""
    entries=sorted((int(key[2:]),value) for key,value in names.items() if key[:1]==sid[:1])
    if len(entries)<2:return True
    centers=[(np.array(e['bbox'][:2],float)+e['bbox'][2:])/2 for _,e in entries]
    axis=centers[-1]-centers[0];length=np.linalg.norm(axis)
    if length<1:return False
    axis=axis/length
    numbers=[n for n,_ in entries]
    index=numbers.index(int(sid[2:]))
    origin=centers[index]
    steps=np.diff(np.array(centers)@axis)/np.diff(numbers)
    previous=steps[max(0,index-1)]
    following=steps[min(index,len(steps)-1)]
    if min(previous,following)<=0:return False
    p=np.float64(candidate['polygon'])
    if '_painted_closed' in candidate:
        # Closed legacy bays can have off-centre lettering. Guard against an
        # entire hidden neighbour without assuming the order of contour vertices.
        if all(b-a==1 for a,b in zip(numbers,numbers[1:])):return True
        distances=[]
        for start,end in zip(p,np.roll(p,-1,axis=0)):
            intersection=line_intersection([start,end],[origin,origin+axis])
            if intersection is not None and np.dot(intersection-start,intersection-end)<=1e-6:
                distances.append(np.dot(intersection-origin,axis))
        return len(distances)>=2 and min(distances)>=-previous and max(distances)<=following
    a=line_intersection([p[3],p[0]],[origin,origin+axis])
    b=line_intersection([p[1],p[2]],[origin,origin+axis])
    if a is None or b is None:return False
    low,high=sorted((np.dot(a-origin,axis),np.dot(b-origin,axis)))
    return abs(low+previous/2)<=previous*.22 and abs(high-following/2)<=following*.22


def inset_candidate(candidate,pixels=2.):
    result=dict(candidate);poly=np.float64(candidate['polygon']);center=poly.mean(axis=0);edges=[]
    for a,b in zip(poly,np.roll(poly,-1,axis=0)):
        normal=np.array([-(b-a)[1],(b-a)[0]]);normal=normal/np.linalg.norm(normal)
        if np.dot(center-a,normal)<0:normal=-normal
        edges.append([a+normal*pixels,b+normal*pixels])
    points=np.rint([line_intersection(edges[i-1],edges[i]) for i in range(4)]).astype(int).tolist()
    result.update(polygon=points,painted_edges=[[points[3][:],points[0][:]],[points[0][:],points[1][:]],[points[1][:],points[2][:]]],
                  entrance_edge=[points[2][:],points[3][:]],boundary_inset_px=pixels)
    return result
