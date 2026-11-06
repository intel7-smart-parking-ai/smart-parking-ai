"""Register the visible part of a named end bay separately from complete dataset ROIs."""
import cv2
import numpy as np

from open_boundaries import line_intersection, support, white_lines


def partial_label(slot):
    return {'칸명':slot['space_id'],'등록상태':'일부 촬영 · 위치 등록','화면안_다각형_px':slot['polygon'],
            '전체경계_추정_px':slot['estimated_full_polygon'],'화면안_면적비율':slot['visible_fraction'],
            '대조한_인접칸':slot['neighbours'],'세주차선_지지율':slot['line_support'],
            '읽은칸명_좌표_px':slot['name_bbox'],'칸명판독점수':slot['name_score']}


def inside(pts, width, height):
    return ((pts[:,0]>=0)&(pts[:,0]<=width-1)&(pts[:,1]>=0)&(pts[:,1]<=height-1))


def measured_edge(start, end, lines, mask, scale, tolerance):
    height,width=mask.shape
    visible, a, b=cv2.clipLine((0,0,width,height),tuple(np.rint(start*scale).astype(int)),
                              tuple(np.rint(end*scale).astype(int)))
    if not visible:
        return None
    segment=np.float64([a,b])
    length=np.linalg.norm(segment[1]-segment[0])
    if length < max(25, .65*np.linalg.norm(end-start)*scale):
        return None
    axis=(segment[1]-segment[0])/length
    options=[]
    for line in lines:
        delta=line[1]-line[0]
        direction=delta/np.linalg.norm(delta)
        if abs(np.dot(axis,direction))<.997:
            continue
        normal=np.array([-direction[1],direction[0]])
        distance=np.abs((segment-line[0])@normal).max()
        if distance>tolerance*scale:
            continue
        lo,hi=sorted((line-segment[0])@axis)
        if min(hi,length)-max(lo,0)<.75*length:
            continue
        projected=line[0]+np.outer((segment-line[0])@direction,direction)
        score=support(mask,*projected)
        if score>=.80:
            options.append((distance,line/scale,score))
    if not options:
        return None
    # Closely spaced Hough fragments may describe either side of one white stroke.
    options.sort(key=lambda item:item[0])
    best=options[0]
    for _,line,_ in options[1:]:
        direction=(line[1]-line[0])/np.linalg.norm(line[1]-line[0])
        if np.max(np.abs((best[1]-line[0])@[-direction[1],direction[0]]))>tolerance:
            return None
    return best[1],best[2]


def partial_end_bays(image,lot,catalog,spaces,names,conflicts):
    if lot not in ('A','B') or conflicts:
        return []
    observed={s['space_id']:s for s in spaces}
    height,width=image.shape[:2]
    frame=np.float32([[0,0],[width-1,0],[width-1,height-1],[0,height-1]])
    measurements=None
    partial=[]
    for sid in sorted(names.keys()-observed.keys()):
        row=sid[0]
        index=int(sid[2:])
        last=sum(key[0]==row for key in catalog)
        if index not in (1,last):
            continue
        step=1 if index==1 else -1
        peers=[f'{row}-{index+i*step:02d}' for i in (1,2)]
        if any(key not in names or observed.get(key,{}).get('boundary_kind')!='open_u' for key in peers):
            continue
        centers=[]
        for key in (sid,*peers):
            box=names[key]['bbox']
            centers.append((np.array(box[:2])+box[2:])/2)
        shift=centers[0]-centers[1]
        if np.linalg.norm(shift-(centers[1]-centers[2]))>max(5,.10*np.linalg.norm(shift)):
            continue
        predicted=np.float64(observed[peers[0]]['polygon'])+shift
        if inside(predicted,width,height).sum()!=3:
            continue
        if measurements is None:
            measurements=white_lines(image)
        lines,mask,scale=measurements
        tolerance=max(4.,min(12.,.06*np.sqrt(cv2.contourArea(predicted.astype(np.float32)))))
        measured=[measured_edge(predicted[i],predicted[j],lines,mask,scale,tolerance)
                  for i,j in ((3,0),(0,1),(1,2))]
        if any(edge is None for edge in measured):
            continue
        left,back,right=[m[0] for m in measured]
        # The back corner can be outside the frame. The two entrances must be
        # real line endpoints close to their predicted positions inside it.
        corners=[line_intersection(left,back),line_intersection(back,right),
                 min(right,key=lambda pt:np.linalg.norm(pt-predicted[2])),
                 min(left,key=lambda pt:np.linalg.norm(pt-predicted[3]))]
        if any(point is None for point in corners):
            continue
        quad=np.float32(corners)
        valid=inside(quad,width,height)
        if (valid.sum()!=3 or not valid[2:].all() or not cv2.isContourConvex(quad)
                or np.linalg.norm(quad-predicted,axis=1).max()>2*tolerance):
            continue
        area,clipped=cv2.intersectConvexConvex(quad,frame)
        fraction=area/max(1.,cv2.contourArea(quad))
        if fraction<.90 or clipped is None:
            continue
        polygon=np.rint(clipped.reshape(-1,2)).astype(np.int32)
        if cv2.contourArea(polygon)<1 or not cv2.isContourConvex(polygon):
            continue
        def contains_name(entry):
            x,y,r,b=entry['bbox']
            return all(cv2.pointPolygonTest(polygon,(u,v),False)>=0 for u,v in ((x,y),(r,y),(r,b),(x,b)))
        if [key for key,entry in names.items() if contains_name(entry)]!=[sid]:
            continue
        if any(cv2.intersectConvexConvex(polygon.astype(np.float32),np.float32(s['polygon']))[0]>.025*area for s in spaces+partial):
            continue
        partial.append({'space_id':sid,'space_type':catalog[sid]['space_type'],'boundary_kind':'partial_frame',
                        'polygon':polygon.tolist(),'estimated_full_polygon':np.round(quad.astype(float),2).tolist(),
                        'visible_fraction':float(fraction),'line_support':[float(m[1]) for m in measured],
                        'neighbours':peers,'name_bbox':names[sid]['bbox'],'name_score':names[sid]['score']})
    return partial
