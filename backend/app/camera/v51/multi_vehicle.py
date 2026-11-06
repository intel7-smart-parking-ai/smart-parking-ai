"""Frame-level OCR overview. Text candidates are NOT vehicle detections."""
FRAME_REVISION='multi-slot-v1'

def summarize(rows):
    instances=[]
    for row in rows:
        for index,candidate in enumerate(row.get('candidates',[])):
            instances.append({'candidate_id':row['space_id']+':'+str(index),'space_id':row['space_id'],
                'number_candidate':candidate.get('number_candidate'),'text':candidate['text'],
                'text_bbox':candidate['text_bbox'],'score':candidate.get('score'),
                'candidate_status':candidate['status'],'slot_status':row['status'],
                'review_status':'pending','object_kind':'ocr_text_candidate'})
    return {'registered_spaces':len(rows),'number_candidate_spaces':sum(r.get('number_candidate') is not None for r in rows),
        'missing_number_spaces':sum(r.get('number_candidate') is None for r in rows),
        'needs_review_spaces':sum(r.get('review_status')!='approved' for r in rows),
        'text_candidate_count':len(instances),'instances':instances,'vehicle_count':None}

def group_reviewed(source,target):
    """Group approved slot labels from the existing JSONL into full-image samples."""
    import json
    from pathlib import Path
    frames={}
    for line in Path(source).read_text(encoding='utf-8').splitlines():
        row=json.loads(line);identifier=row['image_id']
        frame=frames.setdefault(identifier,{'image_id':identifier,'raw_path':row['raw_path'],
            'image_size':row['image_size'],'lot_id':row['lot_id'],'batch_id':row['batch_id'],
            'source':row['source'],'annotations':[],'all_objects_in_image_annotated':False,
            'annotation_scope':'human_reviewed_parking_spaces_not_vehicle_boxes'})
        frame['annotations'].append(row)
        if 'raw_sha256' in row:frame['raw_sha256']=row['raw_sha256']
    temp=Path(str(target)+'.tmp')
    try:
        with temp.open('w',encoding='utf-8') as f:
            for frame in frames.values():f.write(json.dumps(frame,ensure_ascii=False)+'\n')
        temp.replace(target)
    finally:temp.unlink(missing_ok=True)
    return len(frames)
