"""Resolve one hidden interior name from two directly read neighbours and real paint."""
import cv2
import numpy as np
from open_boundaries import inset_candidate


def adjacent_names(sid):
    row,number=sid.split('-')
    return [f'{row}-{int(number)+offset:02}' for offset in (-1,1)]


def gap_between(left,right):
    polygons=[np.float32(slot['polygon']) for slot in (left,right)]
    centers=[p.mean(axis=0) for p in polygons]
    edges=[]
    for index,p in enumerate(polygons):
        ends=np.roll(p,-1,axis=0)
        midpoints=(p+ends)/2
        side=np.argmin(np.linalg.norm(midpoints-centers[1-index],axis=1))
        edges.extend((p[side],ends[side]))
    gap=cv2.convexHull(np.float32(edges)).reshape(-1,2)
    if len(gap)!=4:
        return None
    # Some layouts leave a strip between adjacent strokes. Estimate one bay's
    # footprint from its two neighbours, then check it fits in their actual gap.
    first,second=polygons
    if cv2.contourArea(first,oriented=True)*cv2.contourArea(second,oriented=True)<0:
        second=second[::-1]
    aligned=min((np.roll(second,i,axis=0) for i in range(4)),
                key=lambda p:np.linalg.norm((first-centers[0])-(p-centers[1])))
    footprint=(first+aligned)/2
    area=cv2.contourArea(footprint)
    intersection,_=cv2.intersectConvexConvex(footprint,gap)
    if intersection<.94*area or not all(.65*cv2.contourArea(p)<=area<=1.5*cv2.contourArea(p) for p in polygons):
        return None
    return footprint


def overlap(a,b):
    a,b=np.float32(a),np.float32(b)
    intersection,_=cv2.intersectConvexConvex(a,b)
    return intersection/max(1.,cv2.contourArea(a)+cv2.contourArea(b)-intersection)


def fits_visible_neighbours(candidate,footprint,neighbours):
    # Each of the four measured corners must have a nearby predicted corner.
    p=np.float32(candidate['polygon'])
    tolerance=max(4.,.075*np.sqrt(cv2.contourArea(footprint)))
    distances=np.linalg.norm(p[:,None]-footprint[None,:],axis=2)
    if distances.min(axis=0).max()>tolerance or distances.min(axis=1).max()>tolerance:
        return False
    directions=[np.mean(n['entrance_edge'],axis=0)-np.mean(n['polygon'],axis=0)
                for n in neighbours if n.get('boundary_kind')=='open_u']
    direction=np.mean(candidate['entrance_edge'],axis=0)-p.mean(axis=0)
    return bool(directions) and all(np.dot(d,direction)>.85*np.linalg.norm(d)*np.linalg.norm(direction) for d in directions)


def recover_hidden(lot,catalog,visible,names,candidates):
    if lot not in ('A','B'):
        return []
    anchors={s['space_id']:s for s in visible}
    recovered=[]
    for sid in catalog.keys()-names.keys():
        neighbours=adjacent_names(sid)
        if not all(n in anchors for n in neighbours):
            continue
        visible_neighbours=[anchors[n] for n in neighbours]
        gap=gap_between(*visible_neighbours)
        if gap is None:
            continue
        options=[]
        for candidate in candidates:
            if overlap(gap,candidate['polygon'])<.82:
                continue
            inset=inset_candidate(candidate)
            score=overlap(gap,inset['polygon'])
            if score>=.82 and fits_visible_neighbours(inset,gap,visible_neighbours):
                options.append((score,inset))
        if not options:
            continue
        _,best=max(options,key=lambda entry:entry[0])
        # Short-gap and long-gap line extraction often return the same bay.
        # Disagreeing polygons or opposite entrance interpretations are ambiguous.
        entrance=np.mean(best['entrance_edge'],axis=0)
        tolerance=.12*np.sqrt(cv2.contourArea(gap))
        if any(overlap(best['polygon'],opt['polygon'])<.90 or
               np.linalg.norm(entrance-np.mean(opt['entrance_edge'],axis=0))>tolerance for _,opt in options):
            continue
        recovered.append({**catalog[sid],**best,'name_from_adjacent':neighbours})
    return sorted(recovered,key=lambda s:s['space_id'])
