"""Bay-named annotated photos and Korean sidecars, with verified copy receipts."""
from datetime import datetime
import hashlib,io,json,os,uuid
from pathlib import Path
from dataset_lock import dataset_writer
import cv2,numpy as np
from PIL import Image,ImageDraw,ImageFont
from parking_ai.storage import atomic_json,safe_path,replace_written_file
from layouts import TYPE_NAMES
from capture_views import VIEW_NAMES,view_code,view_folder
from quality_selection import evaluate,load_selection,row_summary,validate_label_selection,require_current_selection

COLORS={'unknown':'미확인','white':'흰색','green':'초록','blue':'파랑'}
OCC={'unknown':'관측 보류','occupied':'차량 있음','vacant':'빈칸'}
KINDS={'annotated_space':'annotated_spaces','korean_label':'korean_labels','annotated_scene':'annotated_scenes','scene_label':'scene_labels'}
COLLECTION_SCHEMA='korean-collection-v2'
SOURCE_NAMES={'camera':'실제 카메라','video':'녹화 재생','import':'가져온 사진','synthetic':'합성 시험'}
CORE_LABEL_VALUES=('점유상태','차량번호','차량번호표기','스티커색','차량종류','번호영역_xyxy_px',
                   '스티커꼭짓점_px','사람검수완료','라벨출처','자동정보완성')
OPTIONAL_LABEL_VALUES=('번호판독상태','점유측정값','점유측정종류','번호판독점수','영상품질',
    '점유판단근거','전용칸종류비교','위치검사','확인필요사유','칸등록방법')


def input_fingerprints(root,identifier):
    result={}
    for folder in ('predictions','reviews'):
        path=Path(root)/folder/(identifier+'.json')
        result[folder]=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    return result


def korean_font(size):
    for path in (Path('C:/Windows/Fonts/malgun.ttf'),Path('/usr/share/fonts/truetype/nanum/NanumGothic.ttf')):
        if path.is_file():return ImageFont.truetype(str(path),size=size)
    raise OSError('한글 표시사진용 글꼴(맑은 고딕 또는 나눔고딕)이 필요합니다.')


def atomic_bytes(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
        replace_written_file(temp,path)
    finally:temp.unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()


def png_bytes(image):
    buffer=io.BytesIO();image.save(buffer,format='PNG');return buffer.getvalue()

def verify_display_png(payload, expected_size=None, expected_digest=None, *, identify_pixels=False):
    """Bind PNG pixels to the canvas before encoding; legacy receipts have only file hashes."""
    try:
        # Pillow can decode all scanlines even when the final IEND chunk is truncated.
        if not payload.endswith(bytes.fromhex('0000000049454e44ae426082')):
            raise ValueError('PNG 종료 블록 누락 또는 손상')
        with Image.open(io.BytesIO(payload)) as container:
            if container.format!='PNG':raise ValueError('PNG 형식이 아닙니다.')
            container.verify()
        with Image.open(io.BytesIO(payload)) as photo:
            if photo.format!='PNG' or photo.mode!='RGB' or getattr(photo,'n_frames',1)!=1:
                raise ValueError('단일 RGB PNG 형식이 아닙니다.')
            if expected_size is not None and list(photo.size)!=expected_size:
                raise ValueError('생성한 표시사진 크기와 다릅니다.')
            photo.load()
            if identify_pixels or expected_digest is not None:
                digest=hashlib.sha256(photo.tobytes()).hexdigest()
                if expected_digest is not None and digest!=expected_digest:
                    raise ValueError('생성한 표시사진 픽셀과 다릅니다.')
                if identify_pixels:return photo.size,digest
    except (OSError,ValueError,SyntaxError,Image.DecompressionBombError) as exc:
        raise ValueError('표시사진 PNG 검증 실패 · '+str(exc)) from exc


def labels_for_scene(record,prediction=None,review=None):
    preds={r['space_id']:r for r in (prediction or {}).get('spaces',[])}
    reviews={r['space_id']:r for r in (review or {}).get('spaces',[])};labels=[]
    for slot in record['roi']['spaces']:
        sid=slot['space_id'];p=preds.get(sid,{});r=reviews.get(sid,{})
        approved=r.get('review_status')=='approved';state=r.get('occupancy') if approved else p.get('occupancy','unknown')
        text=(r.get('display_number') or None) if approved else p.get('number');color=r.get('color') if approved else p.get('color','unknown')
        box=r.get('plate_bbox') if approved else p.get('plate_bbox');poly=r.get('sticker_polygon') if approved else p.get('sticker_polygon')
        q=p.get('quality',{})
        label={'자료규격':'주차칸-한글-v1','사진ID':record['image_id'],'주차장':record['lot_id'],'주차장이름':record['lot_name'],
            '주차칸명':sid,'주차칸종류':TYPE_NAMES[slot['space_type']],'촬영시각_UTC':record['captured_at_utc'],
            '프레임번호':record['frame_id'],'연결구간':record.get('stream_epoch',0),'촬영배치':record['batch_id'],'촬영세션':record['session_id'],
            '촬영시점':VIEW_NAMES[view_code(record)],'촬영시점코드':view_code(record),
            '촬영시점근거':'촬영자가 선택한 방향. 측정된 카메라 각도 값이 아닙니다.',
            '입력출처':SOURCE_NAMES.get(record['source']['kind'],record['source']['kind']),
            '좌표기준':'변경하지 않은 전체 원본의 좌상단 (0,0), 픽셀','원본너비_px':record['image_size'][0],'원본높이_px':record['image_size'][1],
            '주차칸꼭짓점_px':slot['polygon'],'점유상태':OCC[state],'차량번호':int(text) if text and text.isdecimal() else None,
            '차량번호표기':text,'스티커색':COLORS[color],'차량종류':TYPE_NAMES.get(r.get('vehicle_type') if approved else p.get('vehicle_type'),'미확인'),
            '번호영역_xyxy_px':box,'스티커꼭짓점_px':poly,'사람검수완료':approved,'라벨출처':'사람 승인' if approved else '자동 생성 후보 · 미검수',
            '번호판독상태':'사람 확인' if approved and text else '미확인' if not text else '복수 전처리 판독 일치',
            '점유측정값':p.get('occupancy_score'),'점유측정종류':{'difference_ratio':'빈 기준 대비 변화 비율','model_score':'점유 분류 모델 점수','sticker_presence':'스티커 검출 근거'}.get(p.get('score_kind'),'미측정'),
            '번호판독점수':(p.get('selected_ocr') or {}).get('score') if not approved else None,
            '영상품질':{'선명도_라플라시안분산':q.get('laplacian_variance'),'어두운픽셀비율':q.get('dark_ratio'),
                '밝은픽셀비율':q.get('bright_ratio'),'주의사항':q.get('warnings',[]),'설명':'참고 지표이며 실제 정답률을 뜻하지 않습니다.'},
            '자동정보완성':state=='vacant' or (state=='occupied' and bool(text) and color!='unknown'),
            '점유판단근거':p.get('occupancy_reason','자동 점유 근거 없음'),
            '전용칸종류비교':{'allowed':'종류 일치','not_allowed':'종류 불일치 의심','unknown':'확인 불가'}.get(r.get('zone_rule') if approved else p.get('zone_rule'),'확인 불가'),
            '위치검사':p.get('geometry_status','위치 자동검사 불가'),'확인필요사유':p.get('issues',[]),
            '원본사진파일':record['raw_path'],'원본_SHA256':record.get('raw_sha256'),
            '칸등록방법':record['roi'].get('registration_method','사람이 등록한 ROI'),
            '학습사용안내':'글자가 그려진 표시사진은 검토용입니다. 학습에는 원본/무표시 크롭과 라벨 좌표를 사용하세요.'}
        estimate=p.get('number_estimate') if not approved else None
        if 'sticker_exposure' in p:label['번호판독영상진단']=p['sticker_exposure']
        lighting=(prediction or {}).get('sticker_lighting')
        if lighting:
            label['번호판독밝기보정']={'기준':'등록한 빈 주차장 통로' if lighting['source']=='empty_aisle' else '인쇄 도면 통로','배율':lighting['gain'],'밝기이동':lighting['offset']}
        if (prediction or {}).get('parameters',{}).get('measurement_brightness')=='aisle-v1':
            tone=prediction.get('measurement_lighting')
            label['측정사진밝기']={'처리':'원본 밝기','배율':1.,'밝기이동':0.} if not tone else {
                '처리':'통로 기준 보정','기준':'등록한 빈 주차장' if tone['source']=='empty_aisle' else '인쇄 도면',
                '배율':tone['gain'],'밝기이동':tone['offset']}
        if estimate:
            label.update(번호판독상태='추정 · 검토 필요',추정차량번호=int(estimate['number']),
                         추정근거='참고 사진 숫자 비교' if estimate['method']=='reference_print' else '10~99 인쇄 숫자 비교',
                         추정유사도=estimate['score'],차순위번호=int(estimate['alternate']),추정점수차=estimate['margin'],
                         추정번호영역_xyxy_px=estimate['text_bbox'])
            if estimate.get('exposure_agreement'):
                label['추정밝기대조']='원본·부분보정에서 같은 위치의 번호·스티커색 일치'
        from marker_setup import korean_evidence
        evidence=korean_evidence(record['roi'])
        if evidence is not None:label['마커등록근거']=evidence
        from dual_capture import label_fields
        label.update(label_fields(record));labels.append(label)
        if slot.get('boundary_kind')=='virtual_open_u':
            label.update(가상배치칸=sid,실물주차칸=None,주차선형태='가상 안내선 세 면',입구가상경계_px=slot['polygon'][2:4],전용칸종류비교='실물 위치 미측정',위치검사='실물 위치 미측정')
        if record['analysis_config'].get('empty_board'):
            label['빈주차장기준']=record['analysis_config']['empty_board']
            warp=((prediction or {}).get('reference') or {}).get('printed_board_warp')
            if warp is not None:label['빈기준도면재정합']=warp
        if 'name_from_adjacent' in slot:
            label['칸명확인에_사용한_양옆칸']=slot['name_from_adjacent']
        if slot.get('boundary_kind')=='board_open_u':
            label.update(주차선형태='사진 도면 ㄷ자 세 면',실제주차선_3면_px=slot['painted_edges'],입구가상경계_px=slot['entrance_edge'])
        if slot.get('boundary_kind')=='open_u':
            label.update(주차선형태='ㄷ자 세 면 · 열린 입구는 가상 경계',실제주차선_3면_px=slot['painted_edges'],
                         입구가상경계_px=slot['entrance_edge'],주차선지지비율=slot['line_support'])
            if evidence is not None:label['주차선지지근거']='최초 기준 장면의 측정값. 현재 좌표는 마커 평면 변환으로 갱신.'
    return labels


def expected_scene_labels(root,record,prediction,image=None):
    """Derive sidecar values from validated evidence, not from the sidecar itself."""
    review=None;path=Path(root)/'reviews'/(record['image_id']+'.json')
    if path.exists():
        from review import rows_for_edit,validate_review
        from records import load_original
        review=json.loads(path.read_text(encoding='utf-8-sig'))
        rows=rows_for_edit(record,review)
        clean=validate_review(record,rows,review['reviewer'],load_original(root,record) if image is None else image)
        review={**review,'spaces':clean}
    return {r['주차칸명']:r for r in labels_for_scene(record,prediction,review)}


def display_number(label):
    if label.get('추정차량번호') is not None:return f"추정 {label['추정차량번호']}"
    return label.get('차량번호표기') or '미확인'

def annotate(image,labels,origin=(0,0),single=False):
    h,w=image.shape[:2];font=korean_font(18 if single else max(16,min(26,w//65)))
    line=int(font.size*1.6);width=max(w,680 if single else 1100)
    lines=[]
    for label in labels:
        lines.append(f"{label['주차장']} / {label['주차칸명']} {label.get('주차칸종류','')} / {label.get('촬영시점','미지정')} · {label['점유상태']} · 번호 {display_number(label)} · 스티커 {label['스티커색']}")
        if label.get('품질선별'):lines.append('품질 선별: '+label['품질선별']['상태']+' / 장면 '+label['품질선별']['장면상태']+' (정답 승인 아님)')
        if single:lines.extend([label['라벨출처'],f"촬영 UTC {label['촬영시각_UTC']}",label['전용칸종류비교']+' · '+label['위치검사']])
    header='ROI·번호·색 표시사진 / 원본 별도 보존 / 자동 후보는 검수 전 정답 아님'
    if labels and labels[0].get('실물칸확인') is False:header='가상 안내선 · 스티커 번호·색 측정 / 실물 주차 위치 미측정'
    measure=ImageDraw.Draw(Image.new('RGB',(1,1)))
    width=max(width,max(measure.textbbox((0,0),text,font=font)[2] for text in [header]+lines)+24)
    canvas=Image.new('RGB',(width,h+line*(len(lines)+2)),(22,32,43));canvas.paste(Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)),(0,0));draw=ImageDraw.Draw(canvas)
    ox,oy=origin
    if not single and labels:
        for marker in labels[0].get('마커등록근거',{}).get('검출모서리_px',[]):
            pts=[tuple(p) for p in marker['좌표']];draw.line(pts+[pts[0]],fill='#f3a0ff',width=3)
            draw.text((pts[0][0],max(0,pts[0][1]-font.size-2)),'ID '+str(marker['ID']),font=font,fill='#d020dd')
    for label in labels:
        points=[(x-ox,y-oy) for x,y in label['주차칸꼭짓점_px']]
        if label.get('입구가상경계_px'):
            draw.line([points[3],points[0],points[1],points[2]],fill='#74c8ff' if label.get('실물칸확인') is False else '#58ef8d',width=3)
            a,b=np.float64(points[2]),np.float64(points[3]);distance=float(np.linalg.norm(b-a))
            for offset in range(0,max(1,int(distance)),16):
                start=a+(b-a)*offset/max(distance,1);end=a+(b-a)*min(offset+9,distance)/max(distance,1)
                draw.line([tuple(start),tuple(end)],fill='#ffc56a',width=3)
        else:draw.line(points+[points[0]],fill='#58ef8d',width=3)
        if label['스티커꼭짓점_px']:
            pts=[(x-ox,y-oy) for x,y in label['스티커꼭짓점_px']];draw.line(pts+[pts[0]],fill='#ffb74d',width=3)
        if label['번호영역_xyxy_px']:
            a,b,c,d=label['번호영역_xyxy_px'];draw.rectangle((a-ox,b-oy,c-ox,d-oy),outline='#5de9ff',width=2)
        elif label.get('추정번호영역_xyxy_px'):
            a,b,c,d=label['추정번호영역_xyxy_px'];draw.rectangle((a-ox,b-oy,c-ox,d-oy),outline='#ffc56a',width=2)
        text=f"{label['주차칸명']} | {display_number(label)} | {label['스티커색']}"
        x=max(0,min(p[0] for p in points));y=max(0,min(p[1] for p in points))
        draw.rectangle(draw.textbbox((x,y),text,font=font),fill='#16202b');draw.text((x,y),text,font=font,fill='white')
    draw.text((10,h+2),header,font=font,fill='#ffe18a')
    for i,text in enumerate(lines,1):draw.text((10,h+line*i),text,font=font,fill='white')
    return canvas


@dataset_writer
def render_collection(root,record,image,prediction=None,review=None):
    root=Path(root);identifier=record['image_id'];labels=labels_for_scene(record,prediction,review)
    inputs=input_fingerprints(root,identifier)
    selection=evaluate(record,image,labels,inputs)
    from board_profile import apply_lighting
    display=apply_lighting(image,(prediction or {}).get('measurement_lighting'))
    selection_path=root/'selection'/(identifier+'.json')
    atomic_json(selection_path,selection)
    selection_hash=hashlib.sha256(selection_path.read_bytes()).hexdigest()
    for label,row in zip(labels,selection['칸별결과']):label['품질선별']=row_summary(selection,row)
    stamp=datetime.fromisoformat(record['captured_at_utc']).strftime('%Y%m%dT%H%M%S%f')
    items=[];errors=[]
    def write(relative,payload,kind,sid=None,**evidence):
        digest=atomic_bytes(safe_path(root,relative),payload)
        items.append({'path':relative,'sha256':digest,'kind':kind,'space_id':sid,**evidence});return digest
    def write_photo(relative,canvas,kind,sid=None):
        size=list(canvas.size)
        pixels=hashlib.sha256(canvas.tobytes()).hexdigest()
        payload=png_bytes(canvas)
        verify_display_png(payload,size,pixels)
        return write(relative,payload,kind,sid,render_size=size,render_rgb_sha256=pixels)
    from sticker_guide import is_guide
    prefix='sticker_only/' if is_guide(record['roi']) else ''
    for label in labels:
        sid=label['주차칸명'];stem=f"{prefix}collected/{record['lot_id']}/{view_folder(record)}{sid}/{sid}__{stamp}__{identifier}"
        try:
            points=np.asarray(label['주차칸꼭짓점_px'],np.int32);x,y,w,h=cv2.boundingRect(points)
            label.update(표시사진파일=stem+'.png',한글라벨파일=stem+'.json',칸크롭_원본시작점_px=[x,y],칸크롭_너비_px=w,칸크롭_높이_px=h)
            label['표시사진_SHA256']=write_photo(stem+'.png',annotate(display[y:y+h,x:x+w], [label],(x,y),True),'annotated_space',sid)
            write(stem+'.json',json.dumps(label,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8'),'korean_label',sid)
        except Exception as exc:errors.append({'space_id':sid,'error':str(exc)})
    stem=f"{prefix}scenes/{record['lot_id']}/{view_folder(record)}{record['lot_id']}주차장__{stamp}__{identifier}"
    try:
        digest=write_photo(stem+'.png',annotate(display,labels),'annotated_scene')
        from dual_capture import label_fields
        scene={**label_fields(record),'자료규격':'주차장면-한글-v1','사진ID':identifier,'주차장':record['lot_id'],'촬영시각_UTC':record['captured_at_utc'],
            '원본사진파일':record['raw_path'],'표시사진파일':stem+'.png','표시사진_SHA256':digest,'등록칸수':len(labels),'촬영시점':VIEW_NAMES[view_code(record)],'촬영시점코드':view_code(record),
            '주차칸별결과':labels,'자동라벨주의':'추정차량번호는 검토할 후보입니다. 차량번호와 사람검수완료를 함께 확인하세요.'}
        write(stem+'.json',json.dumps(scene,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8'),'scene_label')
    except Exception as exc:errors.append({'scene':identifier,'error':str(exc)})
    if inputs!=input_fingerprints(root,identifier):errors.append({'error':'표시자료 생성 중 예측/검수 파일 변경'})
    receipt={'schema':COLLECTION_SCHEMA,'image_id':identifier,'items':items,'errors':errors,'status':'partial' if errors else 'complete',
        'input_fingerprints':inputs,'quality_selection_sha256':selection_hash,'quality_selection_status':selection['상태']}
    atomic_json(root/'bundles'/(identifier+'.json'),receipt)
    return receipt


def validate_collection_label(label,record,item,items,expected_labels=None):
    """A valid digest alone does not bind a sidecar to its capture and photo."""
    try:
        if not isinstance(label,dict):raise ValueError('한글 JSON은 객체여야 합니다.')
        from dual_capture import label_fields
        expected={**label_fields(record),'사진ID':record['image_id'],'주차장':record['lot_id'],
                  '촬영시각_UTC':record['captured_at_utc'],'원본사진파일':record['raw_path']}
        if item.get('kind')=='korean_label' and record['analysis_config'].get('empty_board') is not None:
            expected['빈주차장기준']=record['analysis_config']['empty_board']
        if 'capture_view' in record:expected.update(촬영시점=VIEW_NAMES[view_code(record)],촬영시점코드=view_code(record))
        sid=item.get('space_id');kind=item['kind']
        photo_kind='annotated_space' if kind=='korean_label' else 'annotated_scene'
        photo=next((p for p in items if p['kind']==photo_kind and p.get('space_id')==sid),None)
        if photo is None:raise ValueError('한글 JSON에 연결할 표시사진 기록이 없습니다.')
        expected.update(표시사진파일=photo['path'],표시사진_SHA256=photo['sha256'])
        if kind=='korean_label':
            slot=next(s for s in record['roi']['spaces'] if s['space_id']==sid)
            if slot.get('boundary_kind')=='virtual_open_u':
                expected.update(가상배치칸=sid,실물주차칸=None,위치검사='실물 위치 미측정',전용칸종류비교='실물 위치 미측정')
            x,y,width,height=cv2.boundingRect(np.asarray(slot['polygon'],np.int32))
            expected.update(칸크롭_원본시작점_px=[x,y],칸크롭_너비_px=width,칸크롭_높이_px=height)
            if 'name_from_adjacent' in slot:
                expected['칸명확인에_사용한_양옆칸']=slot['name_from_adjacent']
            elif '칸명확인에_사용한_양옆칸' in label:raise ValueError('칸 이름 복원 근거 불일치')
            expected.update(자료규격='주차칸-한글-v1',주차칸명=sid,한글라벨파일=item['path'],
                주차장이름=record['lot_name'],주차칸종류=TYPE_NAMES[slot['space_type']],
                주차칸꼭짓점_px=slot['polygon'],원본_SHA256=record.get('raw_sha256'),
                원본너비_px=record['image_size'][0],원본높이_px=record['image_size'][1],
                프레임번호=record['frame_id'],연결구간=record.get('stream_epoch',0),
                촬영배치=record['batch_id'],촬영세션=record['session_id'],
                입력출처=SOURCE_NAMES.get(record['source']['kind'],record['source']['kind']))
            if expected_labels is not None:
                source=expected_labels[sid]
                for key in ('추정차량번호','추정근거','추정유사도','차순위번호','추정점수차','추정번호영역_xyxy_px','번호판독밝기보정','추정밝기대조','측정사진밝기','번호판독영상진단','빈기준도면재정합'):
                    if key in source:expected[key]=source[key]
                    elif key in label:raise ValueError('원본 판독에 없는 추정 번호 또는 보정 근거')
                if '추정차량번호' in source:expected['번호판독상태']=source['번호판독상태']
                expected.update({k:source[k] for k in CORE_LABEL_VALUES})
                # Older valid sidecars may predate advisory fields. When present,
                # they must describe the current stored evidence, including types.
                expected.update({k:source[k] for k in OPTIONAL_LABEL_VALUES if k in label})
            if record['roi'].get('registration_method')=='aruco_board_v1':
                from marker_setup import korean_evidence
                expected.update(칸등록방법='aruco_board_v1',마커등록근거=korean_evidence(record['roi']))
            if slot.get('boundary_kind')=='board_open_u':
                expected.update(실제주차선_3면_px=slot['painted_edges'],입구가상경계_px=slot['entrance_edge'])
            if slot.get('boundary_kind')=='open_u':
                expected.update(실제주차선_3면_px=slot['painted_edges'],입구가상경계_px=slot['entrance_edge'],주차선지지비율=slot['line_support'])
                if record['roi'].get('registration_method')=='aruco_board_v1':expected['주차선지지근거']='최초 기준 장면의 측정값. 현재 좌표는 마커 평면 변환으로 갱신.'
        elif kind=='scene_label':
            expected.update(자료규격='주차장면-한글-v1',등록칸수=len(record['roi']['spaces']))
            rows=label.get('주차칸별결과');slots={s['space_id'] for s in record['roi']['spaces']}
            if (not isinstance(rows,list) or len(rows)!=len(slots) or any(not isinstance(r,dict) for r in rows)
                    or {r.get('주차칸명') for r in rows}!=slots):raise ValueError('전체 한글 JSON 칸 목록 오류')
            for row in rows:
                child=next((p for p in items if p['kind']=='korean_label' and p['space_id']==row['주차칸명']),None)
                if child is None:raise ValueError('전체 장면에 연결할 칸별 JSON이 없습니다.')
                validate_collection_label(row,record,child,items,expected_labels)
        else:raise ValueError('한글 JSON 산출물 종류 오류')
        for field,value in expected.items():
            if field not in label or json.dumps(label[field],ensure_ascii=False,sort_keys=True,allow_nan=False)!=json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False):
                raise ValueError('한글 JSON과 현재 촬영/사진·예측·검수 근거 불일치: '+field)
    except (KeyError,TypeError,AttributeError,StopIteration) as exc:
        raise ValueError('한글 JSON 연결 정보 형식 오류') from exc
    return label


def read_collection(root,record,verify=True,*,image=None):
    if record.get('collection_status') in ('pending','partial'):
        raise ValueError('표시사진/한글 JSON 갱신 미완료: 선택 장면 저장 복구가 필요합니다.')
    if record.get('prediction_status')=='pending':raise ValueError('예측 저장 미완료: 선택 장면 저장 복구가 필요합니다.')
    path=Path(root)/'bundles'/(record['image_id']+'.json')
    if not path.exists():
        if record.get('collection_schema'):raise ValueError('칸별 표시사진/한글 JSON 묶음 기록 유실')
        return None
    receipt=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(receipt,dict):raise ValueError('수집 묶음은 객체여야 합니다.')
    if receipt.get('schema')=='korean-collection-v1':raise ValueError('V21 이전 표시자료: 선택 장면 저장 복구로 최신 근거와 재연결하세요.')
    if receipt.get('schema')!=COLLECTION_SCHEMA or receipt.get('image_id')!=record['image_id']:raise ValueError('수집 묶음 ID/규격 오류')
    if receipt.get('input_fingerprints')!=input_fingerprints(root,record['image_id']):raise ValueError('표시사진/JSON과 최신 예측·검수 내용이 다릅니다. 저장 복구가 필요합니다.')
    # A matching fingerprint proves byte identity, not valid prediction content.
    # V23 could produce a complete bundle after silently dropping bad evidence.
    from records import load_prediction
    prediction=load_prediction(root,record)
    expected_labels=expected_scene_labels(root,record,prediction,image) if verify else None
    if receipt.get('status') not in ('complete','partial') or not isinstance(receipt.get('errors'),list) or not isinstance(receipt.get('items'),list):raise ValueError('수집 묶음 형식 오류')
    if receipt['status']=='complete' and receipt['errors']:raise ValueError('오류가 남은 수집 묶음은 완료일 수 없습니다.')
    selection=load_selection(root,record,receipt,input_fingerprints(root,record['image_id']))
    expected={s['space_id'] for s in record['roi']['spaces']};seen=set();pairs={k:set() for k in KINDS};labels={};scene_label=None
    for item in receipt['items']:
        if (not isinstance(item,dict) or not isinstance(item.get('kind'),str) or not isinstance(item.get('path'),str)
                or not isinstance(item.get('sha256'),str) or len(item['sha256'])!=64 or set(item['sha256'])-set('0123456789abcdef')):
            raise ValueError('수집 묶음 파일 기록 형식 오류')
        kind=item['kind'];sid=item.get('space_id');p=safe_path(root,item['path'])
        if sid is not None and not isinstance(sid,str):raise ValueError('수집 묶음 칸 이름 형식 오류')
        if kind not in KINDS or item['path'] in seen:raise ValueError('수집 묶음 종류/중복 오류')
        if kind in ('annotated_space','korean_label') and sid not in expected:raise ValueError('수집 묶음 칸 이름 오류')
        if kind in ('annotated_scene','scene_label') and sid is not None:raise ValueError('전체 장면 산출물에 칸 ID를 지정할 수 없습니다.')
        if sid in pairs[kind]:raise ValueError('같은 칸/장면 산출물 중복')
        seen.add(item['path']);pairs[kind].add(sid)
        if verify:
            payload=p.read_bytes()
            if hashlib.sha256(payload).hexdigest()!=item['sha256']:raise ValueError('표시사진/한글 JSON 해시 불일치: '+item['path'])
            if kind in ('annotated_space','annotated_scene'):
                if 'render_size' in item or 'render_rgb_sha256' in item:
                    size=item.get('render_size');digest=item.get('render_rgb_sha256')
                    if (not isinstance(size,list) or len(size)!=2 or not all(type(n) is int and n>0 for n in size)
                        or not isinstance(digest,str) or len(digest)!=64 or set(digest)-set('0123456789abcdef')):
                        raise ValueError('표시사진 크기/픽셀 기록 오류')
                verify_display_png(payload,item.get('render_size'),item.get('render_rgb_sha256'))
            if kind in ('korean_label','scene_label'):
                label=validate_collection_label(json.loads(payload),record,item,receipt['items'],expected_labels)
                if kind=='korean_label':
                    validate_label_selection(label,selection);labels[sid]=label
                else:scene_label=label
    if receipt['status']=='complete' and (pairs['annotated_space']!=expected or pairs['korean_label']!=expected or pairs['annotated_scene']!={None} or pairs['scene_label']!={None}):raise ValueError('수집 묶음 사진/JSON 짝 누락')
    if verify and scene_label is not None and {row['주차칸명']:row for row in scene_label['주차칸별결과']}!=labels:
        raise ValueError('전체 장면 JSON과 칸별 JSON 결과가 다릅니다.')
    return receipt


@dataset_writer
def export_collection(root,lot=None,session=None,selected_only=False,paired_only=False):
    from dual_capture import CHANNELS, verified_pair, info_for, label_fields, pair_inventory, inspect_pairs
    if paired_only and lot is not None:raise ValueError('동시측정은 두 카메라를 함께 내보내세요.')
    from records import validate_record,load_original
    from pipeline import read_json
    from export_integrity import ExportTransaction
    root=Path(root);target=root/'exports'/(('selected-' if selected_only else 'automatic-')+uuid.uuid4().hex);target.mkdir(parents=True)
    with ExportTransaction(target) as transfer:
        counts={'scenes':0,'space_pairs':0,'images':0,'json_files':0,'originals':0};skipped=[];manifest=[]
        raw_files=set();image_files=set();json_files=set();measurements={}
        used_references={}
        source_counts = {'complete': 0, 'held': 0, 'bay_pairs': dict.fromkeys(CHANNELS, 0)}
        def verify_input(record,receipt=None):
            if record.get('asset_status') not in (None,'complete'):raise ValueError('장면 자산 저장 미완료: 복구 후 내보내세요.')
            if receipt is None:
                image=load_original(root,record)
                receipt=read_collection(root,record,image=image)
            if not receipt:raise ValueError('이전 자료: 표시사진/한글 JSON을 다시 저장해 생성하세요.')
            if receipt['status']!='complete':raise ValueError('부분 실패 묶음은 내보내지 않습니다.')
            selection=load_selection(root,record,receipt,input_fingerprints(root,record['image_id']))
            if selected_only:
                if selection is None:raise ValueError('품질 미판정: 선택 장면 저장 복구로 판정하세요.')
                if selection['상태']!='통과':raise ValueError('품질 보류: '+', '.join(selection['보류사유']))
                require_current_selection(selection)
            return receipt,selection
        def export_inputs():
            if paired_only:
                receipts, missing, unidentified = pair_inventory(root)
                skipped.extend(unidentified)
                for _, members, _, errors in inspect_pairs(root, receipts, missing, session=session):
                    if errors:
                        source_counts['held'] += 1
                        skipped.extend(errors)
                        continue
                    source_counts['complete'] += 1
                    for channel, (record, _) in members.items():
                        source_counts['bay_pairs'][channel] += len(record['roi']['spaces'])
                    try:
                        both = [(r, *verify_input(r, bundle)) for r, bundle in members.values()]
                    except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
                        skipped.extend({'file': r['image_id']+'.json', 'reason': '두 장면 함께 제외 · '+str(exc)}
                                       for r, _ in members.values())
                        continue
                    yield from both
                return
            pair_cache = {}
            for path in sorted((root/'manifests').glob('*.json')):
                try:
                    record = validate_record(read_json(path), root, path.stem)
                    if lot and record['lot_id'] != lot or session and record['session_id'] != session:
                        continue
                    members = verified_pair(root, record, pair_cache)
                    bundle = members[record['camera_id']][1] if members else None
                    receipt, selection = verify_input(record, bundle)
                except (ValueError, KeyError, TypeError, FileNotFoundError) as exc:
                    skipped.append({'file': path.name, 'reason': str(exc)})
                    continue
                yield record, receipt, selection

        for record, receipt, selection in export_inputs():
            copying=False
            try:
                info=info_for(record)
                # Read and verify all bytes before copying a scene. A failed export is marked unusable.
                payloads=[]
                for item in receipt['items']:
                    payload=safe_path(root,item['path']).read_bytes()
                    if hashlib.sha256(payload).hexdigest()!=item['sha256']:raise ValueError('내보내기 중 자료 변경')
                    payloads.append((item,payload))
                raw=safe_path(root,record['raw_path']).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=record['raw_sha256']:raise ValueError('내보내기 중 원본 변경')
                if receipt['input_fingerprints']!=input_fingerprints(root,record['image_id']):raise ValueError('내보내기 준비 중 예측/검수 변경')
                # Include selection in the last source consistency check.
                if selection!=load_selection(root,record,receipt,input_fingerprints(root,record['image_id'])):raise ValueError('내보내기 준비 중 품질 선별 변경')
                copying=True
                for item,payload in payloads:
                    atomic_bytes(safe_path(target,item['path']),payload)
                    transfer.verify(item['path'],item['sha256'])
                atomic_bytes(safe_path(target,record['raw_path']),raw)
                transfer.verify(record['raw_path'],record['raw_sha256'])
                counts['scenes']+=1;counts['space_pairs']+=len(record['roi']['spaces'])
                # Counts are destination files, not repeated writes of a shared
                # original. Path equality follows the platform's case semantics.
                raw_files.add(safe_path(target,record['raw_path']))
                for item,payload in payloads:
                    group=image_files if item['kind'] in ('annotated_space','annotated_scene') else json_files
                    group.add(safe_path(target,item['path']))
                counts.update(originals=len(raw_files),images=len(image_files|raw_files),json_files=len(json_files))
                manifest.append({**label_fields(record),'image_id':record['image_id'],'lot_id':record['lot_id'],'items':receipt['items'],'raw_path':record['raw_path'],'raw_sha256':record['raw_sha256'],'quality_selection':selection,'capture_view':view_code(record),'촬영시점':VIEW_NAMES[view_code(record)]})
                if info is not None:measurements[info['id']]=info
                evidence=record['analysis_config'].get('empty_board')
                if evidence:
                    used_references.setdefault(evidence['빈기준ID'],{})[record['camera_id']]=evidence
            except (ValueError,KeyError,TypeError,FileNotFoundError) as exc:
                if copying or paired_only:
                    atomic_json(target/'FAILED.json',{'사유':'복사 도중 실패. 이 묶음은 완료되지 않았습니다.'});raise
                skipped.append({'file':record['image_id']+'.json','reason':str(exc)})
            except Exception:
                atomic_json(target/'FAILED.json',{'사유':'복사 중 저장 오류. 완료되지 않은 묶음입니다.'});raise
        result={'schema':'automatic-export-v1','selected_only':selected_only,'scope':{'lot':lot,'session':session},'counts':counts,'skipped':skipped,'scenes':manifest,
            '설명':'원본은 유지하고 복사한 결과입니다. 자동 라벨 후보와 사람 승인 상태를 각 JSON에서 확인하세요. 표시사진을 학습 원본으로 쓰지 마세요.'}
        if paired_only:
            ids={r['image_id'] for r in manifest}
            if any(not {m['image_id'] for m in info['members'].values()}<=ids for info in measurements.values()):raise ValueError('동시측정의 한쪽만 전달될 수 없습니다. 제외 사유와 두 장면을 확인하세요: '+str(skipped[:3]))
            result['동시측정목록']=measurements;counts['measurements']=len(measurements)
            result['source_measurements']=source_counts
        if used_references:
            from empty_pair import copy_references
            basis=copy_references(root,target,used_references,transfer)
            counts['images']+=basis['images']
            counts['json_files']+=basis['json_files']
            result['빈기준파일수']=basis
        atomic_json(target/'내보내기_결과.json',result)
        transfer.report('내보내기_결과.json',result);transfer.finish()
        return str(target),result
