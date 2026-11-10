"""Re-estimate board-to-camera homography on every analyzed frame.

The team bundle calls the 16-space board A and the 12-space board B.
Here lot IDs follow the user's drawing/server: A=12, B=16.
"""
import json
import hashlib
import logging
import tempfile
import os
from zipfile import BadZipFile
from itertools import permutations
import cv2
import numpy as np


def read_image(path):
    image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError('도면 기준 사진을 읽지 못했습니다.')
    return image


def expected_ids(lot):
    return [f'{row}-{i:02d}' for row in ('A','B') for i in range(1, (6 if lot=='A' else 8)+1)]


class BoardRegistry:
    def save_start_view(self,camera,ref):
        directory=getattr(self,'landmark_directory',None)
        if not directory or camera not in ('1','2') or not ref or len(ref['landmarks'])<10:
            return
        pose=ref['pose'];lot=pose['lot_id']
        if pose.get('clipped_spaces'):return
        ids=expected_ids(lot)
        polys=pose.get('polygons') or {s['space_id']:cv2.perspectiveTransform(np.float32(s['points'])[None],
                                            pose['inverse'])[0] for s in self.boards[lot]['spaces']}
        temp=None
        try:
            directory.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=directory,delete=False) as f:
                temp=f.name
                np.savez_compressed(f,lot=lot,shape=ref['shape'],polys=np.float32([polys[k] for k in ids]),
                    outline=pose['outline'],matrix=pose['matrix'],inverse=pose['inverse'],area=ref['area'],
                    xy=np.int32([[x,y] for x,y,_ in ref['landmarks']]),patch=np.stack([p for _,_,p in ref['landmarks']]))
            os.replace(temp,directory/f'start-view-{camera}.npz')
            temp=None
        except OSError:
            logging.getLogger(__name__).warning('Could not save startup landmarks for channel %s',camera)
        finally:
            if temp and os.path.exists(temp):os.unlink(temp)

    def load_start_view(self,camera):
        directory=getattr(self,'landmark_directory',None)
        if not directory or camera not in ('1','2'):return None
        path=directory/f'start-view-{camera}.npz'
        try:
            if path.stat().st_size>150000:return None
            with np.load(path,allow_pickle=False) as d:
                lot=str(d['lot']);h,w=map(int,d['shape']);xy=d['xy'];patch=d['patch'];polys=d['polys']
                ids=expected_ids(lot);matrix=d['matrix'];inverse=d['inverse'];outline=d['outline']
                if (lot not in ('A','B') or not 32<=min(w,h)<=max(w,h)<=8192 or polys.shape!=(len(ids),4,2)
                    or matrix.shape!=(3,3) or inverse.shape!=(3,3) or outline.shape!=(4,2)
                    or xy.ndim!=2 or xy.shape[1]!=2 or not 10<=len(xy)<=140 or patch.shape!=(len(xy),15,15)
                    or patch.dtype!=np.uint8 or not all(np.isfinite(v).all() for v in (polys,matrix,inverse,outline))):
                    return None
                scale=min(1.,960/w)
                if np.any(xy<7) or np.any(xy[:,0]>=round(w*scale)-7) or np.any(xy[:,1]>=round(h*scale)-7):return None
                pose=dict(lot_id=lot,polygons=dict(zip(ids,polys)),outline=outline,matrix=matrix,inverse=inverse,
                          inliers=0,residual_px=0.,line_support=0.)
                return dict(shape=(h,w),scale=scale,pose=pose,landmarks=[(int(x),int(y),p) for (x,y),p in zip(xy,patch)],
                            area=float(d['area']),polygons=np.rint(polys*scale).astype(np.int32))
        except (OSError,ValueError,KeyError,TypeError,IndexError,BadZipFile,EOFError):
            return None

    def fixed_view(self, frame, pose):
        """Enroll distinctive fixed landmarks outside all parking bays."""
        h,w=frame.shape[:2];scale=min(1.,960/w)
        gray=cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),None,fx=scale,fy=scale)
        polys=pose.get('polygons') or {s['space_id']:cv2.perspectiveTransform(
            np.float32(s['points'])[None],pose['inverse'])[0] for s in self.boards[pose['lot_id']]['spaces']}
        points=[np.rint(poly*scale).astype(np.int32) for poly in polys.values()]
        hull=cv2.convexHull(np.concatenate(points))
        mask=np.zeros_like(gray)
        cv2.fillConvexPoly(mask,hull,255)
        mask=cv2.dilate(mask,np.ones((45,45),np.uint8))
        # Bay text is hidden when the board fills up. The surveyed outer rim
        # and printed road labels remain useful, even at the two far ends.
        if pose.get('outline') is not None:
            outer=np.rint(pose['outline']*scale).astype(np.int32)
            if cv2.isContourConvex(outer) and all(cv2.pointPolygonTest(
                    outer,tuple(map(float,p[0])),False)>=0 for p in hull):
                mask.fill(0)
                cv2.fillConvexPoly(mask,outer,255)
        for poly in points:cv2.fillConvexPoly(mask,poly,0)
        mask=cv2.erode(mask,np.ones((17,17),np.uint8))
        features=cv2.goodFeaturesToTrack(gray,140,.012,9,mask=mask)
        if features is None:return None
        landmarks=[]
        for x,y in np.rint(features[:,0]).astype(int):
            patch=gray[y-7:y+8,x-7:x+8]
            if patch.shape==(15,15) and patch.std()>=9:
                landmarks.append((x,y,patch.copy()))
        return dict(shape=(h,w),scale=scale,pose=pose,landmarks=landmarks,
                    area=cv2.contourArea(hull),polygons=points)

    def verify_fixed_view(self, frame, reference):
        if not reference or frame.shape[:2]!=reference['shape']:return None
        scale=reference['scale']
        gray=cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),None,fx=scale,fy=scale)
        matches=[]
        for x,y,patch in reference['landmarks']:
            current=gray[y-7:y+8,x-7:x+8]
            if abs(float(current.mean())-float(patch.mean()))>30 or current.std()<7:continue
            if cv2.matchTemplate(current,patch,cv2.TM_CCOEFF_NORMED)[0,0]>=.90:
                matches.append((x,y))
        if len(matches)<max(10,.60*len(reference['landmarks'])):return None
        area=cv2.contourArea(cv2.convexHull(np.float32(matches)))
        if area<.40*reference['area']:return None
        # Recheck actual printed bay lines on the new image. Rays on a clipped
        # bay or outside the frame provide no supporting evidence.
        edges=cv2.distanceTransform(255-cv2.Canny(gray,50,130),cv2.DIST_L2,3)
        supports=[]
        for poly in reference['polygons']:
            for a,b in zip(poly,np.roll(poly,-1,axis=0)):
                q=np.rint(a+(b-a)*np.linspace(.12,.88,12)[:,None]).astype(int)
                q=q[(q[:,0]>=0)&(q[:,0]<gray.shape[1])&(q[:,1]>=0)&(q[:,1]<gray.shape[0])]
                if len(q):supports.extend(edges[q[:,1],q[:,0]]<=3)
        support=float(np.mean(supports)) if supports else 0
        if support<.55:return None
        return dict(reference['pose'],method='reviewed_fixed_landmarks',line_support=support,
                    fixed_landmarks_matched=len(matches))

    def refresh_fixed_exposure(self, frame, reference, detected):
        """Update patches only after an independent full search confirms the original pose."""
        original=reference['pose']
        if (detected['lot_id']!=original['lot_id'] or frame.shape[:2]!=reference['shape']
                or detected.get('clipped_spaces') or original.get('clipped_spaces')):
            return reference
        delta=np.linalg.norm(detected['outline']-original['outline'],axis=1)
        if not np.all(np.isfinite(delta)) or np.max(delta)>3:
            return reference
        scale=reference['scale']
        gray=cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),None,fx=scale,fy=scale)
        matches=[]
        landmarks=[]
        changes=0
        for x,y,patch in reference['landmarks']:
            current=gray[y-7:y+8,x-7:x+8]
            if current.std()>=7 and cv2.matchTemplate(current,patch,cv2.TM_CCOEFF_NORMED)[0,0]>=.95:
                matches.append((x,y))
                if abs(float(current.mean())-float(patch.mean()))>30:
                    patch=current.copy()
                    changes+=1
            landmarks.append((x,y,patch))
        if (not changes or len(matches)<max(10,.60*len(landmarks))
                or cv2.contourArea(cv2.convexHull(np.float32(matches)))<.40*reference['area']):
            return reference
        return dict(reference,landmarks=landmarks)

    def __init__(self, directory, legacy_directory=None, landmark_directory=None):
        self.landmark_directory=landmark_directory
        data = json.loads((directory/'roi.json').read_text(encoding='utf-8-sig'))
        if data['reference_resolution'] != [1920,1080]:
            raise ValueError('도면 기준 해상도가 다릅니다.')
        self.boards = {}
        self.legacy={}
        self.sift = cv2.SIFT_create(nfeatures=3000)
        for actual, source in (('A','B'), ('B','A')):
            spaces = data['lots'][source]
            if sorted(s['space_id'] for s in spaces) != sorted(expected_ids(actual)):
                raise ValueError('A 12칸 / B 16칸 도면 이름을 확인하세요.')
            for slot in spaces:
                points = np.float32(slot['points'])
                if (points.shape != (4,2) or not np.isfinite(points).all() or
                        not cv2.isContourConvex(points) or cv2.contourArea(points) < 100 or
                        np.any(points < 0) or np.any(points > [1920,1080])):
                    raise ValueError('도면 칸 다각형 오류')
            image = read_image(directory/'samples'/f'{source}_rectified_sample.jpg')
            if image.shape[:2] != (1080,1920):
                raise ValueError('도면 기준 사진 해상도가 다릅니다.')
            variants=[]
            static_mask=np.full((1080,1920),255,np.uint8)
            for slot in spaces:
                pts=np.float32(slot['points']);inner=(pts-pts.mean(0))*.90+pts.mean(0)
                cv2.fillConvexPoly(static_mask,inner.astype(np.int32),0)
            ref_edges=cv2.Canny(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),50,130)
            ref_distance=cv2.distanceTransform(255-ref_edges,cv2.DIST_L2,3)
            boundary=[]
            for slot in spaces:
                points=np.float32(slot['points'])
                for a,b in zip(points,np.roll(points,-1,axis=0)):
                    for t in np.linspace(.05,.95,20):
                        point=a+(b-a)*t;x,y=np.rint(point).astype(int)
                        if 0<=x<1920 and 0<=y<1080 and ref_distance[y,x]<=4:boundary.append(point)
            # The team's rectified 12-bay view is stretched sideways. SIFT alone
            # is rotation/scale invariant, not invariant to this anisotropic warp.
            for stretch in (1.,.55,1.7):
                gray=cv2.cvtColor(cv2.resize(image,(round(960*stretch),540)),cv2.COLOR_BGR2GRAY)
                kp,des=self.sift.detectAndCompute(gray,cv2.resize(static_mask,(round(960*stretch),540)))
                variants.append((stretch,kp,des))
            self.boards[actual] = {'spaces':spaces, 'image':image, 'variants':variants,'boundary':np.float32(boundary)}
            if legacy_directory and (legacy_directory/(actual+'.npz')).is_file():
                with np.load(legacy_directory/(actual+'.npz'),allow_pickle=False) as archive:
                    legacy={'xy':archive['xy'].copy(),'des':archive['descriptor'].copy(),'spaces':json.loads(archive['slots'].item())}
                targets={s['space_id']:np.float32(s['points']) for s in spaces}
                if sorted(s['space_id'] for s in legacy['spaces'])!=sorted(targets):
                    raise ValueError('이전 도면 기준의 칸 목록 오류')
                source=np.float32([np.mean(s['polygon'],axis=0) for s in legacy['spaces']])
                target=np.float32([targets[s['space_id']].mean(axis=0) for s in legacy['spaces']])
                canonical,_=cv2.findHomography(source,target,0)
                legacy['canonical']=canonical
                for slot in legacy['spaces']:
                    projected=cv2.perspectiveTransform(np.float32(slot['polygon'])[None],canonical)[0]
                    # Preserve the classifier's training orientation for each bay.
                    slot['order']=min(permutations(range(4)),key=lambda order:float(np.linalg.norm(projected[list(order)]-targets[slot['space_id']],axis=1).sum()))
                self.legacy[actual]=legacy
                if landmark_directory:
                    path=landmark_directory/(actual+'.npz')
                    if path.is_file():
                        try:
                            with np.load(path,allow_pickle=False) as survey:
                                digest=hashlib.sha256((legacy_directory/(actual+'.npz')).read_bytes()).hexdigest()
                                if survey['base_hash'].item()!=digest:continue
                                xy=survey['xy'];des=survey['des']
                                if (xy.ndim==2 and xy.shape[1]==2 and des.shape==(len(xy),128)
                                        and 16<=len(xy)<=2200 and np.isfinite(xy).all() and np.isfinite(des).all()):
                                    legacy['xy']=np.concatenate((legacy['xy'],xy.astype(np.float32)))
                                    legacy['des']=np.concatenate((legacy['des'],des.astype(np.float32)))
                        except (OSError,ValueError,TypeError,KeyError,EOFError,BadZipFile):
                            logging.getLogger(__name__).warning('Optional %s landmark file unreadable',actual)

    def locate(self, frame, lot_hint='auto', preferred=None):
        if lot_hint not in ('auto','A','B'):
            raise ValueError('주차장은 자동/A/B 중 하나여야 합니다.')
        # Reuse only the successful search order, never the old homography.
        # Both boards are still compared using this frame's feature matches.
        enrolled_first=bool(preferred and preferred.get('method')=='v52_enrolled_unmarked_board')
        if enrolled_first:
            fresh=self._locate_legacy(frame,lot_hint,preferred.get('search_scale',1100))
            if fresh is not None:return fresh
        h,w = frame.shape[:2]
        size = (960, round(h*960/w))
        gray = cv2.cvtColor(cv2.resize(frame,size), cv2.COLOR_BGR2GRAY)
        kp, des = self.sift.detectAndCompute(gray,None)
        if des is None:
            return None
        candidates = []
        edge_distance=cv2.distanceTransform(255-cv2.Canny(gray,50,130),cv2.DIST_L2,3)
        for lot, board in self.boards.items():
            if lot_hint != 'auto' and lot_hint != lot:
                continue
            # Choose the variant with most RANSAC support, not concatenated
            # duplicates of the same source feature.
            best=None
            for stretch,ref_kp,ref_des in board['variants']:
                pairs=cv2.BFMatcher().knnMatch(des,ref_des,k=2)
                good=[a for pair in pairs if len(pair)==2 for a,b in [pair] if a.distance<.70*b.distance]
                if len(good)<24:continue
                vp=np.float32([kp[m.queryIdx].pt for m in good])
                vq=np.float32([ref_kp[m.trainIdx].pt for m in good]);vq[:,0]/=stretch
                vt,vm=cv2.findHomography(vp,vq,cv2.RANSAC,2.5)
                if vt is not None and (best is None or vm.sum()>best[0]):best=(int(vm.sum()),vp,vq)
            if best is None:continue
            _,p,q=best
            transform, mask = cv2.findHomography(p,q,cv2.RANSAC,2.5)
            if transform is None or mask.sum()<24 or mask.mean()<.25:
                continue
            hits = q[mask.ravel().astype(bool)]
            cells = {(min(2,int(x/320)),min(2,int(y/180))) for x,y in hits}
            if len(cells)<4 or np.ptp(hits[:,0])<480 or np.ptp(hits[:,1])<250:
                continue
            transform = np.diag([2.,2.,1.]) @ transform @ np.diag([size[0]/w,size[1]/h,1.])
            if not np.isfinite(transform).all() or abs(np.linalg.det(transform))<1e-10:
                continue
            inverse = np.linalg.inv(transform)
            outline = cv2.perspectiveTransform(np.float32([[[0,0],[1919,0],[1919,1079],[0,1079]]]),inverse)[0]
            if not np.isfinite(outline).all() or not cv2.isContourConvex(outline):
                continue
            area = abs(cv2.contourArea(outline))/(w*h)
            if not .10<=area<=3.0 or np.any(outline < [-w*.4,-h*.4]) or np.any(outline > [w*1.4,h*1.4]):
                continue
            projected = cv2.perspectiveTransform(p[None],np.diag([.5,.5,1])@transform@np.diag([w/size[0],h/size[1],1]))[0]
            residual = float(np.median(np.linalg.norm(projected-q,axis=1)[mask.ravel().astype(bool)]))
            if residual>2:
                continue
            # Independently check surveyed printed boundaries against current
            # image edges. Repeated bay text alone must not establish a pose.
            projected_boundary=cv2.perspectiveTransform(board['boundary'][None],inverse)[0]
            small=np.rint(projected_boundary*[size[0]/w,size[1]/h]).astype(int)
            valid=(small[:,0]>=0)&(small[:,0]<size[0])&(small[:,1]>=0)&(small[:,1]<size[1])
            if valid.mean()<.85:continue
            points=small[valid];line_support=float(np.mean(edge_distance[points[:,1],points[:,0]]<=3))
            if line_support<.45:continue
            candidates.append({'lot_id':lot,'matrix':transform,'inverse':inverse,'outline':outline,
                'inliers':int(mask.sum()),'ratio':float(mask.mean()),'residual_px':residual,'line_support':line_support})
        candidates.sort(key=lambda c:c['inliers'],reverse=True)
        if not candidates:
            return None if enrolled_first else self._locate_legacy(frame,lot_hint)
        if len(candidates)>1 and candidates[0]['inliers']<1.5*candidates[1]['inliers']:return None
        return candidates[0]

    def _locate_legacy(self,frame,lot_hint,preferred_scale=None):
        # The rotated A board under a lit indicator sometimes loses matches at
        # both existing sizes. Keep the same line, residual and coverage gates.
        longest=max(frame.shape[:2])
        order=(preferred_scale,1100,820,960,1600) if preferred_scale else (1100,820,960,1600)
        scales=dict.fromkeys(min(longest,side) for side in order)
        if longest<1000:
            scales.update({round(longest*factor):None for factor in (.85,1.5)})
        for max_side in scales:
            result=self._legacy_at_scale(frame,lot_hint,max_side)
            if result is not None:return result
        return None

    def _legacy_at_scale(self,frame,lot_hint,max_side):
        """Use the V52 enrolled unmarked-board descriptors, with fresh geometry."""
        if not self.legacy:return None
        h,w=frame.shape[:2];scale=max_side/max(h,w)
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        small=cv2.resize(gray,None,fx=scale,fy=scale) if scale!=1 else gray
        kp,des=cv2.SIFT_create(nfeatures=2200,contrastThreshold=.022).detectAndCompute(small,None)
        if des is None:return None
        xy=np.float32([p.pt for p in kp])/scale
        distance=cv2.distanceTransform(255-cv2.Canny(small,50,130),cv2.DIST_L2,3)
        candidates=[]
        for lot,base in self.legacy.items():
            if lot_hint not in ('auto',lot):continue
            pairs=cv2.BFMatcher().knnMatch(base['des'],des,k=2)
            matches=sorted((a for pair in pairs if len(pair)==2 for a,b in [pair] if a.distance<.73*b.distance),key=lambda m:m.distance)
            unique={}
            for m in matches:unique.setdefault(m.trainIdx,m)
            good=list(unique.values())
            if len(good)<16:continue
            src=base['xy'][[m.queryIdx for m in good]];dst=xy[[m.trainIdx for m in good]]
            H,mask=cv2.findHomography(src,dst,cv2.RANSAC,max(1.2,max(h,w)*.0015))
            if H is None or mask is None or mask.sum()<16 or mask.mean()<.35:continue
            hits=mask.ravel().astype(bool);corners=np.float32([p for s in base['spaces'] for p in s['polygon']])
            if cv2.contourArea(cv2.convexHull(src[hits]))<.31*cv2.contourArea(cv2.convexHull(corners)):continue
            H,_=cv2.findHomography(src[hits],dst[hits],0)
            if H is None or not np.isfinite(H).all() or abs(np.linalg.det(H))<1e-10:continue
            residual=np.linalg.norm(cv2.perspectiveTransform(src[hits][None],H)[0]-dst[hits],axis=1)
            if residual.max()>max(1.7,max(h,w)*.0025):continue
            polygons={};edge_points=[];valid=True
            clipped = []
            viewport = np.float32([[1,1], [w-1,1], [w-1,h-1], [1,h-1]])
            for slot in base['spaces']:
                points=cv2.perspectiveTransform(np.float32(slot['polygon'])[None],H)[0]
                if not np.isfinite(points).all() or not cv2.isContourConvex(points):
                    valid=False;break
                polygons[slot['space_id']]=points[list(slot['order'])]
                if np.any(points < 1) or np.any(points >= [w-1,h-1]):
                    # Only one lightly clipped bay is allowed. All line evidence
                    # must then come from the other, fully visible bays.
                    area = cv2.contourArea(points)
                    visible_area, _ = cv2.intersectConvexConvex(points, viewport)
                    if clipped or area <= 0 or visible_area < .90 * area:
                        valid = False
                        break
                    clipped.append(slot['space_id'])
                    continue
                for a,b in ((points[3],points[0]),(points[0],points[1]),(points[1],points[2])):
                    edge_points.extend(a+(b-a)*t for t in np.linspace(.1,.9,15))
            if not valid:continue
            q=np.rint(np.float32(edge_points)*scale).astype(int)
            q[:,0]=np.clip(q[:,0],0,distance.shape[1]-1);q[:,1]=np.clip(q[:,1],0,distance.shape[0]-1)
            line_support=float(np.mean(distance[q[:,1],q[:,0]]<=3))
            if line_support<.45:continue
            matrix=base['canonical']@np.linalg.inv(H);inverse=np.linalg.inv(matrix)
            outline=cv2.perspectiveTransform(np.float32([[[0,0],[1919,0],[1919,1079],[0,1079]]]),inverse)[0]
            candidates.append({'lot_id':lot,'matrix':matrix,'inverse':inverse,'outline':outline,'polygons':polygons,
                'clipped_spaces':clipped,
                'inliers':int(mask.sum()),'ratio':float(mask.mean()),'residual_px':float(np.median(residual)),
                'line_support':line_support,'method':'v52_enrolled_unmarked_board','search_scale':max_side})
        candidates.sort(key=lambda c:c['inliers'],reverse=True)
        if not candidates or len(candidates)>1 and candidates[0]['inliers']<1.5*candidates[1]['inliers']:return None
        return candidates[0]


def overlap_slot(box, polygon):
    x1,y1,x2,y2 = box
    if x2<=x1 or y2<=y1:
        return 0.
    area,_ = cv2.intersectConvexConvex(np.float32([[x1,y1],[x2,y1],[x2,y2],[x1,y2]]),np.float32(polygon))
    return float(max(0,area)/((x2-x1)*(y2-y1)))


def dedicated(lot, space):
    if space in ({'A-05','A-06'} if lot=='A' else {'A-07','A-08'}):
        return 'ev'
    if space in ({'B-06'} if lot=='A' else {'B-07','B-08'}):
        return 'accessible'
    return 'general'


def assign_sticker(box, polygons):
    center=((box[0]+box[2])/2,(box[1]+box[3])/2)
    hits=[sid for sid,poly in polygons.items() if cv2.pointPolygonTest(np.float32(poly),center,False)>=0]
    if len(hits)!=1:return None
    selected=hits[0]
    overlaps={sid:overlap_slot(box,poly) for sid,poly in polygons.items()}
    if overlaps[selected]<.5 or any(value>.25 for sid,value in overlaps.items() if sid!=selected):return None
    return selected
