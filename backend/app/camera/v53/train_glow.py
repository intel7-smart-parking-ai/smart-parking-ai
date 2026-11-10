"""Fit the luminous-core model from a local manifest with manually checked light centers."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from web_live.glow import proposals,cover_core,reflection_features,tinted_roof_proposals


def vary_background(frame, light):
    """Keep the measured emitter pixels and vary scale and surrounding board paint."""
    x,y,r=light
    extent=int(r*1.3)
    crop=frame[y-extent:y+extent+1,x-extent:x+extent+1]
    samples=[]
    for size in (11,19,29,43):
        lens=cv2.resize(crop,(size,size),interpolation=cv2.INTER_AREA)
        yy,xx=np.ogrid[:size,:size]
        mask=(xx-size//2)**2+(yy-size//2)**2 <= (size/2)**2
        for color in ((25,26,28),(70,70,70),(116,78,38),(100,130,66),(50,100,70),(180,185,184)):
            patch=np.full((95,95,3),color,np.uint8)
            offset=47-size//2
            region=patch[offset:offset+size,offset:offset+size];region[mask]=lens[mask]
            near=[(box,v) for box,v in proposals(patch)
                  if abs((box[0]+box[2])/2-47)<max(2,size*.16) and abs((box[1]+box[3])/2-47)<max(2,size*.16)]
            if near:
                _,feat=max(near,key=lambda p:p[1][5])
                samples.append((feat,1))
    return samples


def fit(manifest,output,base=None):
    rng=np.random.default_rng(18)
    examples=[]
    roof_cores=[]
    roof_negatives=[]
    for row in json.loads(manifest.read_bytes()):
        if row['split']!='train':continue
        frame=cv2.imdecode(np.frombuffer((manifest.parent/row['file']).read_bytes(),np.uint8),1)
        if row.get('roof_emitter'):
            # A model-car pillar can divide one hot core into two visible parts.
            # Train on each remaining part of a labelled emitter, preserving its surroundings.
            for x,y,rad in row['lights']:
                extent=3*rad
                if x<extent or y<extent or x+extent>=frame.shape[1] or y+extent>=frame.shape[0]:continue
                roof=frame[y-extent:y+extent+1,x-extent:x+extent+1]
                for width in {max(1,round(rad*f)) for f in (.12,.22,.30)}:
                    for axis in (0,1):
                        sample=roof.copy()
                        bar=sample if axis==0 else sample.swapaxes(0,1)
                        bar[extent-width//2:extent+(width+1)//2]=[45,47,50]
                        for (a,b,u,v),feature in proposals(sample,clipped=True):
                            if (np.hypot((a+u)/2-extent,(b+v)/2-extent)<rad*.55
                                    and feature[9]>.45 and feature[7]>.75):
                                roof_cores.append(feature)
        positives=negatives=0
        scales=(.5,.65,.8,.94,1.,1.06) if row.get('roof_emitter') else (.5,.8,1.0)
        for scale in scales:
            resized=cv2.resize(frame,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
            for gain in (.88,1.,1.10):
                pixels=np.uint8(np.clip(resized.astype(np.float32)*gain,0,255))
                neg=[]
                for (x,y,u,v),feat in proposals(pixels)+proposals(pixels,clipped=True):
                    cx=(x+u)/2/scale;cy=(y+v)/2/scale
                    ds=[(np.hypot(cx-a,cy-b),rad) for a,b,rad in row['lights']]
                    if any(dist<rad*.55 for dist,rad in ds):
                        examples.append((feat,1));positives+=1
                        if row.get('roof_emitter') and feat[9]>.45 and feat[7]>.75:
                            roof_cores.append(feat)
                    elif all(dist>rad*1.7 for dist,rad in ds):
                        neg.append(feat)
                        if feat[9]>.45 and feat[7]>.75:roof_negatives.append(feat)
                for k in rng.choice(len(neg),min(30,len(neg)),replace=False):
                    examples.append((neg[k],0));negatives+=1
        if row['lights'] and not positives:raise ValueError('No labelled luminous core in '+row['file'])
        if row.get('vary_background'):
            extra=vary_background(frame,row['lights'][0])
            examples.extend(extra);positives+=len(extra)
        print(row['file'],positives,negatives)
    X=np.array([v for v,_ in examples]);y=np.array([t for _,t in examples])
    if set(y)!={0,1}:raise ValueError('Need positive and negative samples')
    output.parent.mkdir(parents=True,exist_ok=True)
    if base:
        from web_live.glow import GlowModel
        original=GlowModel(base)
        w,mu,sd,bias=original.w,original.mu,original.sd,original.bias
        z=(X-mu)/sd
    else:
        mu=X.mean(0);sd=np.maximum(X.std(0),.045);z=(X-mu)/sd
        w=np.zeros(10);bias=0.;bal=np.where(y==1,.5/y.mean(),.5/(1-y.mean()))
        for _ in range(1500):
            err=(1/(1+np.exp(-np.clip(z@w+bias,-40,40)))-y)*bal
            w-=.1*(z.T@err/len(y)+.012*w);bias-=.1*err.mean()
    # Limit each measured roof appearance by its distance to the closest
    # labelled negative. Without a labelled roof sample the linear model suffices.
    refs=(np.asarray(roof_cores).reshape(-1,10)-mu)/sd
    negatives=np.vstack([z[y==0],(np.asarray(roof_negatives).reshape(-1,10)-mu)/sd])
    radii=np.array([min(2.6, np.linalg.norm(negatives-ref,axis=1).min()*.60) for ref in refs])
    keep=radii>.15
    np.savez(output,version=2,weights=w,center=mu,scale=sd,bias=bias,
             roof_refs=refs[keep],roof_radii=radii[keep])
    print('fit',len(y),'positive',int(y.sum()),'negative',int(len(y)-y.sum()))


def fit_covers(manifest,output,base):
    """Extend a released model with measured appearances of the red emitter behind plastic."""
    if not base:raise ValueError('A base LED model is required')
    with np.load(base,allow_pickle=False) as f:bank={k:f[k] for k in f.files}
    positive=[];negative=[];paint=[];emitters=[]
    for item in json.loads(manifest.read_bytes()):
        if item['split']!='train':continue
        frame=cv2.imdecode(np.fromfile(manifest.parent/item['file'],np.uint8),1)
        for scale in (.45,.52,.60,.72,.8,1.):
            small=cv2.resize(frame,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
            for gain in (.91,1.,1.07):
                pixels=cv2.convertScaleAbs(small,alpha=gain)
                candidates=proposals(pixels)+proposals(pixels,clipped=True)
                if item.get('paint_only'):
                    if item['lights'] or not item.get('paint_regions'):
                        raise ValueError('Paint-only samples must specify a reviewed unlit region')
                if item.get('paint_regions'):
                    candidates.extend(tinted_roof_proposals(pixels))
                for box,v in candidates:
                    center=np.mean(np.asarray(box).reshape(2,2),axis=0)/scale
                    distances=[np.linalg.norm(center-[x,y])/radius for x,y,radius in item['lights']]
                    nearest=min(distances,default=np.inf)
                    if nearest<=.65:
                        if cover_core(v):positive.append(v)
                        emitters.append(reflection_features(pixels,box,v))
                    elif nearest>=1.7:
                        if any(x<center[0]<u and y<center[1]<b for x,y,u,b in item.get('paint_regions',())):
                            paint.append(reflection_features(pixels,box,v))
                        else:
                            negative.append(v)
    if not positive or not negative:raise ValueError('Labeled positive and negative candidates required')
    refs=(np.unique(positive,axis=0)-bank['center'])/bank['scale']
    negatives=(np.asarray(negative)-bank['center'])/bank['scale']
    radii=np.array([min(2.5,.60*np.linalg.norm(negatives-ref,axis=1).min()) for ref in refs])
    keep=radii>.15
    if not keep.any():raise ValueError('No red LED examples separated from background')
    paint_refs=np.unique(np.asarray(paint).reshape(-1,14),axis=0)
    positives=np.asarray(emitters)
    paint_radii=np.array([min(.43,.65*np.linalg.norm(positives-ref,axis=1).min()) for ref in paint_refs])
    distinct=paint_radii>.015
    np.savez_compressed(output,**(bank|dict(cover_refs=refs[keep],cover_radii=radii[keep],
        paint_refs=paint_refs[distinct],paint_radii=paint_radii[distinct])))
    print('reflection prototypes',int(distinct.sum()))
    print('covered LED prototypes',int(keep.sum()),'negative candidates',len(negative))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('manifest',type=Path);p.add_argument('model',type=Path)
    p.add_argument('--base',type=Path,help='Preserve the released emitter classifier while fitting roof appearances')
    p.add_argument('--covers',action='store_true',help='Extend a base model with labeled red emitters under a cover')
    args=p.parse_args()
    (fit_covers if args.covers else fit)(args.manifest,args.model,args.base)
