"""Compare one saved dual measurement with a separately transcribed field layout."""
import hashlib
import json
from pathlib import Path
import uuid

from export_integrity import unique_report_fields

from dataset_lock import dataset_writer
from dual_capture import pair_path, read_complete_measurement
from layouts import COLOR_TYPES, TYPE_NAMES, LOT_NAMES, layout, zone_rule
from parking_ai.storage import atomic_json, safe_path
from records import load_prediction

FORM='현장정답표-v1'
COLORS={'흰색':'white','초록':'green','파랑':'blue'}
BOUNDARY={'정상':None,'선침범':'주차선 침범 변화 후보','두칸걸침':'여러 칸 걸침 변화 후보',
          '입구돌출':'주차칸 입구 돌출 변화 후보'}


def form_for(identifier, members):
    rows=[]
    images={}
    for channel,(record,_) in members.items():
        images[channel]={'사진ID':record['image_id'],'원본_SHA256':record['raw_sha256']}
        if record['roi'].get('registration_method')=='virtual_sticker_guide_v1':
            images[channel]['위치근거']='가상 안내선 · 스티커 시험'
        for slot in record['roi']['spaces']:
            rows.append({'카메라':channel,'주차장':record['lot_id'],'칸':slot['space_id'],'주차칸종류':TYPE_NAMES[slot['space_type']],
                         '점유':'미입력','번호':None,'스티커색':None,'스티커방향_도':None,'차량식별표':None,'주차선':'미평가'})
    return {'자료규격':FORM,'측정ID':identifier,'원본':images,'조명조건':'미입력','학습_manifest_상대경로':'',
            '작성안내':'실제 배치를 보고 입력하세요. 조명조건은 기본·어두움·반사 등으로 기록합니다. '
                       '점유는 차량 또는 빈칸, 번호는 10~99 정수, 스티커색은 흰색·초록·파랑입니다. '
                       '같은 실물 차량은 두 시점에서 같은 차량식별표를 쓰세요. 빈칸의 번호·색·식별표는 null입니다. '
                       '스티커방향_도는 화면에서 숫자가 똑바로 선 방향을 0으로 한 시계 방향 각도(0~359)이며 모르면 null입니다. '
                       '주차선은 미평가·정상·선침범·두칸걸침·입구돌출 중 하나 또는 위반 종류의 JSON 배열입니다.',
            '주차칸':rows}


def compare_layout(form, template, members, predictions):
    condition=form.get('조명조건')
    if not isinstance(condition,str) or not condition.strip() or condition.strip()=='미입력':
        raise ValueError('실제로 시험한 조명조건을 입력하세요.')
    if form.get('원본')!=template['원본'] or form.get('측정ID')!=template['측정ID']:
        raise ValueError('정답표의 사진과 저장된 두 원본 식별 정보가 다릅니다.')
    expected={(r['카메라'],r['주차장'],r['칸']) for r in template['주차칸']}
    seen=set()
    rows=form.get('주차칸')
    if not isinstance(rows,list) or len(rows)!=len(expected):
        raise ValueError('두 주차장의 모든 칸을 한 번씩 입력하세요.')
    output=[]
    counts=dict(차량칸=0,직접일치=0,추정일치=0,오답=0,판독보류=0,빈칸=0,빈칸정상=0,빈칸오점유=0,빈칸점유보류=0,빈칸오번호=0,가상빈배치_스티커관측=0,가상빈배치_스티커미관측=0)
    boundary=dict(일치=0,오경보=0,놓침또는종류오류=0,판정보류=0)
    per_type={kind:dict(적중=0,오경보=0,놓침=0,정상=0,판정보류=0) for kind in BOUNDARY if kind!='정상'}
    occupancy=dict(비교칸=0,일치=0,오점유=0,놓침=0,보류=0)
    scope={kind:dict(차량관측=0,직접일치=0,추정일치=0,오답=0,판독보류=0) for kind in ('실물칸','가상배치')}
    stickers={kind:dict(차량관측=0,직접일치=0,추정일치=0,오답=0,판독보류=0) for kind in scope}
    separate={kind:{'번호':dict(직접일치=0,추정일치=0,직접오답=0,추정오답=0,보류=0),
                    '스티커색':dict(일치=0,오답=0,보류=0)} for kind in scope}
    by_angle={kind:{} for kind in scope}
    vehicle_ids={};numbers=set();missing_ids=0
    zone=dict(일치=0,오경보=0,놓침=0,판정보류=0)
    for row in rows:
        if not isinstance(row,dict):raise ValueError('정답표 칸 형식 오류')
        key=(row.get('카메라'),row.get('주차장'),row.get('칸'))
        if key not in expected or key in seen:raise ValueError('중복 또는 다른 주차칸: '+str(key))
        seen.add(key)
        channel,lot,sid=key
        slot=next(s for s in members[channel][0]['roi']['spaces'] if s['space_id']==sid)
        zone_name=TYPE_NAMES[slot['space_type']]
        if '주차칸종류' in row and row['주차칸종류']!=zone_name:
            raise ValueError(f'{lot}/{sid} 주차칸종류가 등록된 도면과 다릅니다.')
        state=row.get('점유');n=row.get('번호');color=row.get('스티커색');line=row.get('주차선')
        lines=line if isinstance(line,list) else [line]
        if (state not in ('차량','빈칸') or not lines or any(not isinstance(v,str) or v not in ('미평가',*BOUNDARY) for v in lines)
            or len(set(lines))!=len(lines) or (isinstance(line,list) and any(v in ('정상','미평가') for v in lines))):
            raise ValueError(f'{lot}/{sid}의 점유 또는 주차선 정답을 입력하세요.')
        if state=='차량' and (type(n) is not int or not 10<=n<=99 or color not in COLORS):
            raise ValueError(f'{lot}/{sid}의 스티커 번호와 배경색을 확인하세요.')
        if state=='빈칸' and (n is not None or color is not None):
            raise ValueError(f'{lot}/{sid} 빈칸의 번호와 색을 비우세요.')
        angle=row.get('스티커방향_도')
        if angle is not None and (state!='차량' or type(angle) is not int or not 0<=angle<360):
            raise ValueError(f'{lot}/{sid} 차량의 스티커 방향을 0~359도 또는 null로 입력하세요.')
        vehicle_id=row.get('차량식별표')
        if vehicle_id is not None and (state!='차량' or not isinstance(vehicle_id,str) or not vehicle_id.strip()):
            raise ValueError(f'{lot}/{sid} 차량식별표를 확인하세요.')
        pred=predictions[channel]
        virtual='위치근거' in template['원본'][channel]
        if virtual and line!='미평가':raise ValueError(f'{lot}/{sid} 가상 안내선에서는 주차선을 미평가로 입력하세요.')
        item=next(r for r in pred['spaces'] if r['space_id']==sid)
        guess=item['number'] or (item.get('number_estimate') or {}).get('number')
        issues=[g['reason'] for g in pred['geometry'] if sid in g['spaces']]
        if not virtual:
            actual_state=item['occupancy']
            expected_state='occupied' if state=='차량' else 'vacant'
            occupancy['비교칸']+=1
            occupancy['보류' if actual_state=='unknown' else '일치' if actual_state==expected_state else '오점유' if state=='빈칸' else '놓침']+=1
        if state=='빈칸':
            counts['빈칸']+=1
            grade={'vacant':'빈칸정상','occupied':'빈칸오점유','unknown':'빈칸점유보류'}[item['occupancy']]
            if virtual:
                observed=(item.get('sticker_polygon') is not None or guess is not None or
                          item.get('ocr_status') in ('multiple_stickers','multiple_sticker_candidates'))
                grade='가상빈배치_스티커관측' if observed else '가상빈배치_스티커미관측'
            counts['빈칸오번호']+=int(guess is not None)
        else:
            counts['차량칸']+=1
            numbers.add(n)
            if vehicle_id is None:missing_ids+=1
            elif vehicle_ids.setdefault(vehicle_id.strip(),(n,color))!=(n,color):
                raise ValueError(f'{lot}/{sid} 같은 차량식별표의 번호 또는 색이 다릅니다.')
            source='가상배치' if virtual else '실물칸'
            number_grade='보류'
            if guess:
                method='직접' if item['number'] else '추정'
                number_grade=method+('일치' if guess==str(n) else '오답')
            color_grade='보류' if item['color']=='unknown' else '일치' if item['color']==COLORS[color] else '오답'
            separate[source]['번호'][number_grade]+=1
            separate[source]['스티커색'][color_grade]+=1
            bucket=by_angle[source].setdefault(str(angle) if angle is not None else '미기록',dict.fromkeys(separate[source]['번호'],0))
            bucket[number_grade]+=1
            if (guess and guess!=str(n)) or item['color'] not in (COLORS[color],'unknown'):
                sticker_grade='오답'
            elif guess is None or item['color']=='unknown':
                sticker_grade='판독보류'
            else:
                sticker_grade='직접일치' if item['number'] else '추정일치'
            stickers[source]['차량관측']+=1;stickers[source][sticker_grade]+=1
            if virtual:
                grade=sticker_grade
            elif item['occupancy']=='vacant' or sticker_grade=='오답':
                grade='오답'
            elif item['occupancy']=='unknown':
                grade='판독보류'
            else:
                grade=sticker_grade
            actual=item['zone_rule'];truth=zone_rule(slot['space_type'],COLOR_TYPES[COLORS[color]])
            if not virtual:zone['판정보류' if actual=='unknown' else '일치' if actual==truth else '오경보' if truth=='allowed' else '놓침']+=1
            source_counts=scope[source]
            source_counts['차량관측']+=1;source_counts[grade]+=1
        counts[grade]+=1
        line_grade=None
        if line!='미평가':
            expected_warnings={BOUNDARY[name] for name in lines if name!='정상'}
            actual_warnings=set(issues)
            held=not pred.get('reference') or pred.get('reference_error') or pred['mode']!='reference'
            for kind,metric in per_type.items():
                expected_kind=kind in lines
                detected=BOUNDARY[kind] in actual_warnings
                metric['판정보류' if held else '적중' if expected_kind and detected else '놓침' if expected_kind else '오경보' if detected else '정상']+=1
            if held:
                line_grade='판정보류'
            elif expected_warnings-actual_warnings:
                line_grade='놓침또는종류오류'
            else:
                line_grade='오경보' if actual_warnings-expected_warnings else '일치'
            boundary[line_grade]+=1
        output.append({**row,'주차칸종류':zone_name,'화면관측':item['occupancy'],'실물칸확인':not virtual,
                       '자동점유':None if virtual else item['occupancy'],'판독번호':int(guess) if guess else None,
                       '판독방식':'직접' if item['number'] else '추정' if guess else '보류',
                       '판독스티커색':next((k for k,v in COLORS.items() if v==item['color']),'미확인'),
                       '대조':grade,'위치경고':issues,'주차선대조':line_grade,'자동전용칸판정':None if virtual else item['zone_rule']})
    physical=scope['실물칸']
    matched=physical['직접일치']+physical['추정일치']
    return {'자료규격':'동시측정현장대조-v3','측정ID':form['측정ID'],'원본':template['원본'],'조명조건':condition.strip(),
            '결과':counts,'차량칸_번호색점유_일치율':matched/physical['차량관측'] if physical['차량관측'] else None,
            '번호색점유_실물차량칸수':physical['차량관측'],
            '위치근거별_차량관측대조':scope,'스티커번호색대조':stickers,'번호와색_개별대조':separate,'실물점유대조':occupancy,
            '방향별_번호대조':by_angle,
            '차량식별대조':{'차량관측칸':counts['차량칸'],'서로다른번호수':len(numbers),'식별표누락관측':missing_ids,
                           '정답표상_고유차량수':None if missing_ids else len(vehicle_ids)},
            '주차선대조':boundary,'주차선유형별대조':per_type,'전용칸대조':zone,'칸별결과':output}


def inspect_training_folder(folder):
    """Read every delivered crop and its receipt, including crops from other measurements."""
    from collection_output import verify_display_png
    from dataset19 import training_split
    path=safe_path(folder,'manifest.json')
    receipt=json.loads(safe_path(path.parent,'EXPORT_STATUS.json').read_bytes(),object_pairs_hook=unique_report_fields)
    entries=receipt.get('파일목록',[])
    if not isinstance(entries,list) or any(not isinstance(r,dict) or not isinstance(r.get('파일'),str)
            or not isinstance(r.get('SHA256'),str) or type(r.get('바이트')) is not int or r['바이트']<0 for r in entries):
        raise ValueError('학습 내보내기 파일 목록 형식 오류')
    files={r['파일']:r for r in entries}
    if type(receipt.get('검증파일수')) is not int or len(entries)!=len(files) or receipt['검증파일수']!=len(files):
        raise ValueError('학습 내보내기 완료 기록의 파일 수 또는 중복 경로 오류')
    payload=path.read_bytes()
    manifest_entry=files.get(path.name,{})
    if (receipt.get('상태')!='완료' or manifest_entry.get('바이트')!=len(payload)
            or manifest_entry.get('SHA256')!=hashlib.sha256(payload).hexdigest()):
        raise ValueError('학습 내보내기 완료 기록과 manifest.json을 확인하세요.')
    manifest=json.loads(payload,object_pairs_hook=unique_report_fields)
    samples=manifest['samples']
    groups={};coverage={};listed={path.name}
    if not isinstance(samples,list):raise ValueError('학습 전달 사진 목록 형식 오류')
    observations=set();crop_labels={}
    zones={(lot,slot['space_id']):TYPE_NAMES[slot['space_type']] for lot in LOT_NAMES for slot in layout(lot)['spaces']}
    if '주차칸종류포함' in manifest and manifest['주차칸종류포함'] is not True:
        raise ValueError('학습 전달 도면 구역 표시 오류')
    actual={p.relative_to(path.parent).as_posix() for p in path.parent.rglob('*') if p.is_file()}
    if actual!=set(files)|{'EXPORT_STATUS.json'}:raise ValueError('학습 전달 목록과 실제 파일 목록 불일치')
    for sample in samples:
        if not isinstance(sample,dict) or any(not isinstance(sample.get(k),str) or not sample[k]
                for k in ('task','split','label','group','image_id','space_id','path','sha256')):
            raise ValueError('학습 전달 사진 필드 오류')
        task,split,label,ident,sid=(sample[k] for k in ('task','split','label','image_id','space_id'))
        if (task not in ('number','occupancy','color') or any(part in ('.','..') or '/' in part or chr(92) in part or ':' in part
                for part in (label,ident,sid)) or sample['path']!=f'{task}/{split}/{label}/{ident}_{sid}.png'):
            raise ValueError('학습 전달 사진의 분할·라벨·칸 파일 경로 불일치')
        if manifest.get('주차칸종류포함') or '주차칸종류' in sample:
            lot=sample.get('lot_id')
            if not isinstance(lot,str) or (lot,sid) not in zones or sample.get('주차칸종류')!=zones[(lot,sid)]:
                raise ValueError(f'{lot}/{sid} 학습 자료의 주차칸종류가 도면과 다릅니다.')
        observation=(task,ident,sid)
        if observation in observations:raise ValueError('같은 장면·칸의 학습 과제 중복')
        observations.add(observation)
        group=sample['group'];name=sample['path']
        if name in listed:raise ValueError('학습 전달 사진의 중복 파일 경로')
        listed.add(name)
        if split!=training_split(group) or files.get(name,{}).get('SHA256')!=sample.get('sha256'):
            raise ValueError('학습 전달 사진의 배치·분할 또는 파일 목록 불일치')
        crop_path=safe_path(path.parent,name)
        crop=crop_path.read_bytes()
        if len(crop)!=files[name]['바이트'] or hashlib.sha256(crop).hexdigest()!=sample['sha256']:
            raise ValueError('학습 전달 사진 해시 또는 바이트 불일치 · '+name)
        pixels=verify_display_png(crop,identify_pixels=True)
        prior=crop_labels.setdefault((task,pixels),(split,label))
        if prior!=(split,label):raise ValueError('같은 학습 크롭의 분할 또는 정답 충돌 · '+name)
        groups[group]=split
        totals=coverage.setdefault(sample['task'],{}).setdefault(sample['label'],dict(train=0,validation=0,test=0))
        totals[split]+=1
    if listed!=set(files) or groups!=manifest.get('groups'):
        raise ValueError('학습 전달 목록의 파일 또는 배치별 분할 집계 불일치')
    if ('과제별_라벨_분할수' in manifest and
            json.dumps(coverage,sort_keys=True)!=json.dumps(manifest['과제별_라벨_분할수'],sort_keys=True)):
        raise ValueError('학습 전달 목록의 과제·라벨별 분할 집계 불일치')
    return manifest,files


def check_training(root, relative, members, report, predictions):
    """Audit actual exported number crops against independent truth and the approval at export time."""
    from review import validate_review,rows_for_edit
    from dual_capture import label_fields
    from records import load_original
    from dataset19 import training_split,training_pixels_hash
    import cv2
    import numpy as np
    path=safe_path(root,relative)
    if path.name!='manifest.json' or path.parent.parent!=safe_path(root,'exports') or not path.parent.name.startswith('training-'):
        raise ValueError('학습 내보내기 폴더의 manifest.json 상대 경로를 입력하세요.')
    manifest,files=inspect_training_folder(path.parent)
    manifest_entry=files[path.name]
    truth={(r['카메라'],r['칸']):r for r in report['칸별결과']}
    records={r['image_id']:(k,r) for k,(r,_) in members.items()}
    rows_by_revision={};originals={};pixel_hashes={};delivered=set();verified=set()
    counts=dict(번호사진=0,현장정답불일치=0,승인근거미확인=0,승인좌표픽셀불일치=0)
    samples=manifest['samples']
    for sample in samples:
        ident=sample['image_id']
        if ident not in records or sample['task']!='number':continue
        channel,record=records[ident]
        sid=sample['space_id'];observation=(channel,sid)
        if observation not in truth:raise ValueError('학습 번호 사진의 측정 칸 불일치')
        if observation in delivered:raise ValueError('동일 측정 칸의 학습 번호 사진 중복')
        if (sample.get('lot_id')!=record['lot_id'] or sample.get('group')!=record['batch_id']
                or sample.get('split')!=training_split(record['batch_id'])
                or sample.get('raw_sha256')!=record.get('raw_sha256')):
            raise ValueError('학습 번호 사진의 촬영 원본·배치·분할 불일치')
        if sample['path']!=f"number/{sample['split']}/{sample['label']}/{ident}_{sid}.png":
            raise ValueError('학습 번호 사진의 분할·라벨·칸 파일 경로 불일치')
        basis_keys=('동시측정ID','카메라ID','카메라시리얼','두프레임_수신차_ms','위치근거','실물칸확인','수집대상')
        basis={k:sample[k] for k in basis_keys if k in sample}
        if json.dumps(basis,sort_keys=True)!=json.dumps(label_fields(record),sort_keys=True):
            raise ValueError('학습 번호 사진의 카메라·측정 또는 실물/가상 위치 근거 불일치')
        expected=truth[observation];counts['번호사진']+=1;delivered.add(observation)
        truth_matches=expected['점유']=='차량' and str(expected['번호'])==sample['label']
        counts['현장정답불일치']+=int(not truth_matches)
        crop=safe_path(path.parent,sample['path']).read_bytes()
        if files[sample['path']]['바이트']!=len(crop) or hashlib.sha256(crop).hexdigest()!=sample['sha256']:
            raise ValueError('학습용 숫자 사진 해시 불일치: '+sample['path'])
        revision=sample.get('검수파일_SHA256');key=(ident,revision)
        if ident not in originals:
            originals[ident]=load_original(root,record)
            pixel_hashes[ident]=training_pixels_hash(originals[ident])
        if 'raw_pixels_sha256' in sample and sample['raw_pixels_sha256']!=pixel_hashes[ident]:
            raise ValueError('학습 번호 사진의 디코딩 원본 픽셀 해시 불일치')
        if key not in rows_by_revision:
            rows_by_revision[key]={}
            reviews=Path(root)/'reviews'
            for item in [reviews/(ident+'.json'),*(reviews/'history').glob(ident+'-*.json')]:
                if not item.is_file():continue
                raw=item.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=revision:continue
                saved=json.loads(raw)
                clean=validate_review(record,rows_for_edit(record,saved),saved['reviewer'],originals[ident])
                rows_by_revision[key]={r['space_id']:r for r in clean if r['review_status']=='approved'}
                break
        approved=rows_by_revision[key].get(sid)
        if (sample.get('사람검수완료') is not True or not approved or not approved['plate_bbox']
                or approved.get('display_number')!=sample['label']):
            counts['승인근거미확인']+=1
            continue
        x,y,right,bottom=map(int,approved['plate_bbox'])
        pixels=cv2.imdecode(np.frombuffer(crop,np.uint8),cv2.IMREAD_UNCHANGED)
        from board_profile import apply_lighting
        source=originals[ident][y:bottom,x:right]
        tone=sample.get('적용한측정밝기보정')
        if tone is not None and tone!=predictions[channel].get('measurement_lighting'):
            counts['승인좌표픽셀불일치']+=1
        else:
            pixels_match=np.array_equal(pixels,apply_lighting(source,tone))
            counts['승인좌표픽셀불일치']+=int(not pixels_match)
            if truth_matches and pixels_match:verified.add(observation)
    expected_numbers={key for key,row in truth.items() if row['점유']=='차량'}
    return {**counts,'정답번호관측수':len(expected_numbers),'미전달정답번호관측수':len(expected_numbers-delivered),
            '검증통과번호관측수':len(verified),'미검증정답번호관측수':len(expected_numbers-verified),
            '검사한_manifest':relative,'manifest_SHA256':manifest_entry['SHA256']}


@dataset_writer
def field_check(root, selected):
    path=Path(selected)
    payload=path.read_bytes()
    data=json.loads(payload,object_pairs_hook=unique_report_fields)
    if not isinstance(data,dict):raise ValueError('측정 기록 또는 현장 정답표 JSON을 선택하세요.')
    make_form=data.get('schema')=='dual-measurement-v1'
    if make_form:
        identifier=(data.get('info') or {}).get('id')
        if path.resolve()!=pair_path(root,identifier).resolve():raise ValueError('현재 데이터 폴더의 동시측정 기록을 선택하세요.')
    elif data.get('자료규격')==FORM:
        identifier=data.get('측정ID')
    else:raise ValueError('dual_measurements 폴더의 측정 기록 또는 작성한 현장 정답표를 선택하세요.')
    members=read_complete_measurement(root,identifier)
    template=form_for(identifier,members)
    folder=safe_path(root,'field_checks/'+identifier)
    if make_form:
        target=folder/'현장정답표.json'
        if not target.exists():atomic_json(target,template)
        return target,'실제 배치와 조명조건을 입력한 뒤 현장 검증에서 이 정답표를 선택하세요.'
    predictions={k:load_prediction(root,r) for k,(r,_) in members.items()}
    if not all(predictions.values()):raise ValueError('동시측정의 저장된 자동 예측을 확인하세요.')
    report=compare_layout(data,template,members,predictions)
    manifest=data.get('학습_manifest_상대경로','')
    report['학습번호전달검사']=check_training(root,manifest,members,report,predictions) if manifest else None
    report['정답표_SHA256']=hashlib.sha256(payload).hexdigest()
    target=folder/('대조-'+uuid.uuid4().hex+'.json')
    atomic_json(target,report)
    c=report['결과']
    unique=report['차량식별대조']['정답표상_고유차량수']
    message=f"차량 관측 {c['차량칸']}칸 · 식별표상 고유 차량 {str(unique)+'대' if unique is not None else '미확인'} · 실물 차량 {report['번호색점유_실물차량칸수']}칸 · 위치근거별 대조 합계 직접 일치 {c['직접일치']} · 추정 일치 {c['추정일치']} · 오답 {c['오답']} · 보류 {c['판독보류']} · 빈칸 오번호 {c['빈칸오번호']}"
    audit=report['학습번호전달검사']
    if audit:message+=f" · 전달 번호 {audit['번호사진']}장 · 정답·승인·픽셀 검증 통과 {audit['검증통과번호관측수']}/{audit['정답번호관측수']}칸 · 정답 불일치 {audit['현장정답불일치']} / 승인 미확인 {audit['승인근거미확인']} / 픽셀 불일치 {audit['승인좌표픽셀불일치']}"
    return target,message
