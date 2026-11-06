"""Two physical channels, one host-timed observation and one completion receipt."""
import copy, hashlib, re, threading, time, uuid
from dataclasses import dataclass
from pathlib import Path
from camera_hub import CameraSpec
from capture_views import VIEW_NAMES
from dataset_lock import dataset_writer
from parking_ai.storage import atomic_json,safe_path
from pipeline import read_json

CHANNELS=('A','B')
@dataset_writer
def active_batches(root, renew=False):
    path=Path(root)/'dual_batches.json'
    if path.exists() and not renew:
        batches=read_json(path)
        if (not isinstance(batches,dict) or set(batches)!=set(CHANNELS) or
                not all(isinstance(value,str) and re.fullmatch(r'batch-[a-f0-9]{12}',value) for value in batches.values())):
            raise ValueError('기존 배치 ID 오류 · 실제 배치를 확인한 뒤 새 차량 배치를 선택하세요.')
        return batches
    batches={lot:'batch-'+uuid.uuid4().hex[:12] for lot in CHANNELS}
    atomic_json(path,batches)
    return batches

class PairHold(ValueError):pass

class RegistrationHold(PairHold):
    def __init__(self,packet,lot,setup):
        from automatic_setup import missing_stages
        count = setup['detected_count']
        partial=setup.get('partial_spaces',[])
        if setup.get('dark_frame'):
            hint = f"영상이 너무 어둡습니다 · 평균 밝기 {setup['dark_frame']['평균밝기_0_255']}/255 · 조명·렌즈·노출을 확인하세요."
        elif partial:
            registered=setup['registered_count']
            ids=', '.join(s['space_id'] for s in partial)
            additional=sorted(set(setup['missing'])-{s['space_id'] for s in partial})
            hint=f'위치 등록 {registered}/{setup["expected_count"]}칸 · 일부 촬영 {ids} · 끝 주차선까지 여백을 확보하세요.'
            if additional:
                hint+=' 위치 미등록 '+', '.join(additional)
        elif setup.get('text_evidence') == []:
            hint = '칸 이름 판독 0개 · 보드 네 모서리가 화면에 들어오는지와 글씨 초점을 확인하세요.'
        elif setup.get('missing'):
            missing = setup['missing']
            stages = missing_stages(setup) or {}
            shown = ', '.join(f'{sid}({stages[sid]})' if sid in stages else sid for sid in missing[:4])
            if len(missing) > 4:
                shown += f' 외 {len(missing)-4}칸'
            hint = f'미등록 {shown} · 해당 글씨와 주차선을 확인하세요.'
        else:
            hint = '주차선/마커 확인'
        super().__init__(f"{packet.camera_id} 카메라 · {lot}주차장 {count}/{setup['expected_count']}칸 · {hint}")
        self.setup=setup

def profile_specs(profile,devices=None):
    """Unassigned slots can use the one remaining device; never remap a known serial."""
    channels=profile['channels']
    if set(channels)!=set(CHANNELS):raise ValueError('카메라 1·2 설정이 모두 필요합니다.')
    if any(type(profile[k]) is not int or profile[k]<=0 for k in ('width','height','fps')):raise ValueError('촬영 해상도/FPS 오류')
    if type(profile['max_skew_ms']) is not int or not 1<=profile['max_skew_ms']<=1000:raise ValueError('수신 시각 차이는 1~1000ms로 설정하세요.')
    for c in channels.values():
        if not isinstance(c['serial'],str) or c['lot'] not in CHANNELS or c['view'] not in VIEW_NAMES or c['view']=='unspecified':raise ValueError('시리얼·주차장·시점 설정 오류')
    serials={k:channels[k]['serial'].strip() for k in CHANNELS};assigned=[s for s in serials.values() if s]
    if len(set(assigned))!=len(assigned):raise ValueError('한 RealSense 시리얼을 두 채널에 배정할 수 없습니다.')
    if devices is not None:
        found={d['serial'] for d in devices if d['serial'] and d['serial']!='unknown'}
        if len(found)<2:raise PairHold(f'RealSense {len(found)}/2대 감지 · 두 장치를 연결한 뒤 다시 검색하세요.')
        if set(assigned)-found:raise PairHold('선택한 시리얼이 연결되어 있지 않습니다: '+', '.join(sorted(set(assigned)-found)))
        for k in CHANNELS:
            if not serials[k]:
                free=found-set(serials.values())
                if len(free)!=1:raise PairHold('장치 목록에서 카메라 1·2의 시리얼을 직접 선택하세요.')
                serials[k]=free.pop()
    return [CameraSpec(k,'realsense',serials[k],width=profile['width'],height=profile['height'],fps=profile['fps']) for k in CHANNELS]

def alignment_root(root,channel):
    # The first camera can reuse V33 markers/anchors; the second never shares their active path.
    if channel not in CHANNELS:raise ValueError('카메라 ID 오류')
    return Path(root) if channel=='A' else Path(root)/'camera_alignment'/'B'

def checked_skew_ms(first_ns, second_ns, limit):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('수신 차이 설정 오류')
    if any(type(n) is not int or n < 0 for n in (first_ns, second_ns)):
        raise ValueError('두 카메라의 수신 시각 오류')
    delta = abs(first_ns - second_ns)
    if delta > limit * 1_000_000:
        raise PairHold(f'수신 시각 차이 {delta/1e6:.3f}ms > {limit}ms')
    return delta / 1e6

def synchronized(frames,specs,limit,last=None,now=None,not_before=None):
    now=time.monotonic() if now is None else now;keys={}
    if type(limit) is not int or not 1<=limit<=1000:raise ValueError('수신 차이 설정 오류')
    if set(specs)!=set(CHANNELS) or len({s.serial for s in specs.values() if s.serial})!=2:raise PairHold('서로 다른 두 카메라 시리얼이 필요합니다.')
    for k in CHANNELS:
        p=frames.get(k)
        if p is None:raise PairHold(f'카메라 {k}의 새 영상 대기')
        if p.camera_id!=k or p.serial!=specs[k].serial or p.source_kind!='camera':raise PairHold(f'카메라 {k} 입력/시리얼 불일치')
        if not 0<=now-p.received_monotonic<=1:raise PairHold(f'카메라 {k} 프레임 만료')
        if not_before is not None and p.received_monotonic<=not_before:raise PairHold(f'카메라 {k}의 측정 시작 이후 프레임 대기')
        keys[k]=(p.serial,p.stream_epoch,p.sequence)
        previous = (last or {}).get(k)
        if previous and previous[0] == p.serial and keys[k][1:] <= previous[1:]:
            raise PairHold(f'카메라 {k}의 다음 프레임 대기')
    return keys,checked_skew_ms(round(frames['A'].received_monotonic*1e9), round(frames['B'].received_monotonic*1e9), limit)


def continuous_pair(hub,frames,specs,limit,last,not_before,now=None):
    now=time.monotonic() if now is None else now
    candidates={}
    for k in CHANNELS:
        latest=frames.get(k)
        if latest is None:raise PairHold(f'카메라 {k}의 새 영상 대기')
        previous=(last or {}).get(k)
        minimum=previous[2] if previous and previous[:2]==(latest.serial,latest.stream_epoch) else -1
        times=dict(hub.recent_times(k,latest.stream_epoch))
        candidates[k]=[(seq,t) for seq,t in times.items() if minimum<seq<=latest.sequence
                       and 0<=now-t<=1 and (not_before is None or t>not_before)]
        if not candidates[k]:raise PairHold(f'카메라 {k}의 현재 연결에서 측정할 새 영상 대기')
    def rank(pair):
        a,b=(round(t*1e9) for seq,t in pair)
        return abs(a-b),-min(a,b)
    best=min(((a,b) for a in candidates['A'] for b in candidates['B']),key=rank)
    checked_skew_ms(round(best[0][1]*1e9),round(best[1][1]*1e9),limit)
    pair=frames.copy()
    for k,(seq,t) in zip(CHANNELS,best):
        if seq!=frames[k].sequence:
            frame=hub.get_frame_near(k,frames[k].stream_epoch,t,seq-1,not_before)
            if frame is None or frame.sequence!=seq:
                raise PairHold(f'카메라 {k}의 프레임 갱신 · 다음 짝 대기')
            pair[k]=frame
    keys,delta=synchronized(pair,specs,limit,last,now,not_before)
    return pair,keys,delta

@dataclass
class Result:
    packet:object
    roi:dict
    prediction:dict
    cfg:dict

class SharedReader:
    """Both channels share one model; serialize only model loading/readtext calls."""
    def __init__(self,project,cfg):
        self.project=Path(project);self.cfg=copy.deepcopy(cfg);self.lock=threading.Lock()
        self.reader=None;self.retry_at=0.;self.error=''
    def _get_reader(self):
        from analysis import make_reader
        if self.reader is None:
            if time.monotonic()<self.retry_at:raise RuntimeError(self.error)
            try:self.reader,_=make_reader(self.project,self.cfg)
            except Exception as exc:
                self.error=str(exc);self.retry_at=time.monotonic()+15.
                raise RuntimeError(self.error) from exc
        return self.reader

    def readtext(self,*args,**kwargs):
        with self.lock:
            return self._get_reader().readtext(*args,**kwargs)

    def recognize(self,*args,**kwargs):
        with self.lock:
            return self._get_reader().recognize(*args,**kwargs)

class ChannelAnalyzer:
    def __init__(self,reader=None):
        from vision19 import VisionEngine
        self.engine=VisionEngine();self.engine.reader=reader
    def register_roi(self,root,p,setting,cfg):
        from automatic_setup import prepare_auto_scene
        from analysis import make_reader
        from records import validate_capture
        from layouts import layout
        from quality import dark_registration_frame
        darkness = dark_registration_frame(p.image)
        if darkness is not None:
            ids = [slot['space_id'] for slot in layout(setting['lot'])['spaces']]
            raise RegistrationHold(p,setting['lot'],{'expected_count':len(ids),'detected_count':0,
                    'missing':ids,'spaces':[],'dark_frame':darkness,'errors':['저조도 · 주차칸 등록 보류']})
        pose=cfg['camera_pose'][p.camera_id]+'__view-'+setting['view']
        def reader():
            if self.engine.reader is None:self.engine.reader,_=make_reader(Path(cfg['project']),cfg)
            return self.engine.reader
        setup=prepare_auto_scene(alignment_root(root,p.camera_id),p,setting['lot'],pose,reader,cfg.get('auto_roi_min_score',.7))
        if not setup['ready']:raise RegistrationHold(p,setting['lot'],setup)
        roi=setup['roi'];validate_capture(p,roi)
        if len(roi['spaces'])!=layout(setting['lot'])['space_count']:raise PairHold('미등록 주차칸이 있습니다.')
        return roi

    def analyze(self,root,p,setting,cfg,empty=None):
        from empty_pair import reference_for
        from prediction_contract import validate_prediction
        cfg=copy.deepcopy(cfg)
        cfg.update(capture_view=setting['view'],enable_ocr=True,capture_source={'kind':'camera','captured_time_kind':'host_receive_utc'})
        if cfg.get('auto_sticker_guide') and empty is None:
            from sticker_guide import physical_or_virtual
            from quality import dark_registration_frame
            if dark_registration_frame(p.image) is not None:
                raise PairHold('영상이 너무 어둡습니다. 렌즈와 조명을 확인하세요.')
            pose=cfg['camera_pose'][p.camera_id]+'__view-'+setting['view']
            roi=physical_or_virtual(alignment_root(root,p.camera_id),p,setting['lot'],pose)
        elif empty is not None and not empty[0].get('marker_evidence'):
            from automatic_setup import anchor_alignment
            from records import validate_capture
            from quality import dark_registration_frame
            roi,baseline=empty
            pose=cfg['camera_pose'][p.camera_id]+'__view-'+setting['view']
            if roi['lot_id']!=setting['lot'] or roi['pose']!=pose or baseline['epoch']!=p.stream_epoch:
                raise PairHold('주차장·시점·연결 변경 · 두 빈 주차장 기준을 다시 등록하세요.')
            validate_capture(p,roi)
            if dark_registration_frame(p.image) is not None:
                raise PairHold('영상이 너무 어둡습니다. 조명·렌즈·노출을 확인하세요.')
            alignment=anchor_alignment(baseline['image'],p.image,roi)
            translated=alignment.get('consistent_translation_px',0)>1.5
            if translated or not alignment['passed']:
                from board_profile import METHOD,locate
                found=locate(p,roi['lot_id'],pose) if roi.get('registration_method')==METHOD else None
                if not found:
                    if translated:raise PairHold('빈 기준과 주차선 위치 이동 · 구도를 확인하고 두 빈 기준을 다시 등록하세요.')
                    raise PairHold('등록 구도 확인 보류 · '+alignment['reason']+' · 보드와 카메라 고정을 확인하세요.')
                roi=found['roi']
        else:
            roi=self.register_roi(root,p,setting,cfg)
        reference=reference_for(empty,p,roi) if empty is not None else None
        mode='classifier' if cfg.get('occupancy_model') else 'sticker'
        from sticker_guide import is_guide
        if is_guide(roi):mode='sticker'
        if reference is not None:
            cfg['empty_board']=reference['empty_board']
            mode='reference'
        pred=self.engine.run(p,roi,cfg,reference,mode)
        validate_prediction(pred,{'roi':roi,'frame_id':p.sequence,'captured_at_utc':p.timestamp_utc,'lot_id':setting['lot'],'stream_epoch':p.stream_epoch,'image_size':roi['image_size']})
        return Result(p,roi,pred,cfg)

def pair_path(root,pid):
    if not isinstance(pid,str) or not re.fullmatch('[0-9a-f]{32}',pid):raise ValueError('동시측정 ID 오류')
    return safe_path(root,f'dual_measurements/{pid}.json')

def info_for(r):return r['analysis_config'].get('dual_measurement')

def require_same_empty_pair(config_a, config_b):
    evidence = [cfg.get('empty_board') for cfg in (config_a, config_b)]
    if evidence == [None, None]:
        return
    if (not all(isinstance(e, dict) and e.get('빈기준ID') and e.get('카메라ID') == channel
                for channel, e in zip(CHANNELS, evidence))
            or evidence[0]['빈기준ID'] != evidence[1]['빈기준ID']):
        raise ValueError('두 장면의 빈 기준 등록 쌍이 다릅니다. 두 빈 주차장 기준을 다시 등록하세요.')

def label_fields(record):
    info=info_for(record)
    fields={} if info is None else {'동시측정ID':info['id'],'카메라ID':record['camera_id'],'카메라시리얼':record['serial'],'두프레임_수신차_ms':info['skew_ms']}
    from sticker_guide import is_guide
    virtual=is_guide(record.get('roi',{}))
    if virtual or 'auto_sticker_guide' in record['analysis_config']:
        fields.update(위치근거='가상 안내선' if virtual else '실물 주차판',실물칸확인=not virtual,
                      수집대상='스티커 번호·배경색' if virtual else '주차칸·스티커 번호·배경색')
    return fields

def _members(root,info):
    from layouts import layout
    from records import validate_record,validate_sensor_frame
    if not isinstance(info,dict) or info.get('clock') != 'host_monotonic_receive_ns':
        raise ValueError('동시측정 시계 정보 오류')
    members=info['members']
    if set(members)!=set(CHANNELS) or members['A']['serial']==members['B']['serial']:raise ValueError('두 카메라 연결 정보 오류')
    delta=checked_skew_ms(members['A']['received_ns'],members['B']['received_ns'],info['max_skew_ms'])
    if delta!=info['skew_ms']:raise ValueError('두 카메라 수신 차이 오류')
    records={}
    for k,m in members.items():
        ident=m['image_id']
        if not re.fullmatch('[0-9a-f]{32}',ident):raise ValueError('동시측정 사진 ID 오류')
        r=validate_record(read_json(safe_path(root,f'manifests/{ident}.json')),root,ident)
        expected={'serial':r['serial'],'frame_id':r['frame_id'],'timestamp':r['captured_at_utc'],'epoch':r['stream_epoch'],'lot':r['lot_id'],'view':r['capture_view'],'batch':r['batch_id']}
        validate_sensor_frame(m.get('sensor_frame'),r['source']['kind'])
        if m.get('sensor_frame')!=r.get('sensor_frame'):raise ValueError('측정 쌍과 원본 RGB 프레임 근거 불일치: '+k)
        if (r['camera_id']!=k or r['source']['kind']!='camera' or r['session_id']!=info['session'] or info_for(r)!=info or
            any(m[f]!=v for f,v in expected.items()) or r.get('asset_status')!='complete' or r.get('collection_status')!='complete'):
            raise ValueError('동시측정 멤버/완료 상태 불일치: '+k)
        if not safe_path(root,r['raw_path']).is_file():raise ValueError('동시측정 원본 누락: '+k)
        expected_slots = {s['space_id'] for s in layout(r['lot_id'])['spaces']}
        if not r.get('raw_sha256') or {s['space_id'] for s in r['roi']['spaces']} != expected_slots:
            raise ValueError('동시측정 원본 해시 또는 전체 주차칸 목록 오류: '+k)
        records[k]=r
    require_same_empty_pair(records['A']['analysis_config'], records['B']['analysis_config'])
    return records

def verified_scene_bundle(root, record):
    """Read the bytes promised by one dual-camera scene before counting it."""
    from capture_views import view_folder
    from collection_output import read_collection
    from records import load_original, load_prediction
    from vision19 import crop_space
    import cv2
    import numpy as np

    from capture_recovery import require_finished_capture
    require_finished_capture(root, record)
    image = load_original(root, record)
    bundle = read_collection(root, record, image=image)
    if not bundle or bundle['status'] != 'complete' or not bundle.get('quality_selection_sha256'):
        raise ValueError('표시사진·한글 JSON 또는 품질 판정 기록 미완료')
    identifier = record['image_id']
    assets = read_json(safe_path(root, f'assets/{identifier}.json'))
    if not isinstance(assets, dict) or assets.get('image_id') != identifier or assets.get('status') != 'complete' or assets.get('errors') != []:
        raise ValueError('칸·스티커 크롭 저장 기록 오류')
    view = view_folder(record)
    if record['raw_path'] != f"raw/{record['camera_id']}/{view}{identifier}.png":
        raise ValueError('동시측정 원본 경로와 카메라·시점·사진 ID 불일치')
    expected = {('space', s['space_id']): f"spaces/{record['lot_id']}/{view}{s['space_id']}/{identifier}.png"
                for s in record['roi']['spaces']}
    polygons = {('space', s['space_id']): s['polygon'] for s in record['roi']['spaces']}
    prediction = load_prediction(root, record)
    if prediction is None:
        raise ValueError('동시측정 예측 기록 누락')
    from empty_pair import verify_empty_evidence
    verify_empty_evidence(root,record,prediction)
    for row in prediction['spaces']:
        sid = row['space_id']
        if row.get('sticker_polygon'):
            expected['sticker', sid] = f'stickers/{view}{identifier}_{sid}.png'
            polygons['sticker', sid] = row['sticker_polygon']
    for item in assets['items']:
        path = expected.pop((item['kind'], item['space_id']), None)
        if path is None or item['path'] != path or item.get('status') != 'complete':
            raise ValueError('칸·스티커 크롭 연결 오류')
        payload = safe_path(root, path).read_bytes()
        if hashlib.sha256(payload).hexdigest() != item['sha256']:
            raise ValueError('크롭 파일 해시 불일치: '+path)
        polygon = polygons[item['kind'],item['space_id']]
        original_crop, _, origin = crop_space(image, polygon)
        try:
            decoded = cv2.imdecode(np.frombuffer(payload,np.uint8), cv2.IMREAD_UNCHANGED) if payload else None
        except cv2.error as exc:
            raise ValueError('크롭 사진을 열 수 없습니다: '+path) from exc
        if not np.array_equal(original_crop, decoded):
            raise ValueError('원본 좌표와 크롭 사진 픽셀 불일치: '+path)
        if item['kind']=='space' and (item.get('origin')!=list(origin) or item.get('polygon')!=polygon
                or item.get('mask_outside')!='white'):
            raise ValueError('원본 좌표와 칸 크롭 기록 불일치: '+path)
    if expected:
        raise ValueError('필수 칸·스티커 크롭 누락')
    return bundle


def _verified_members(root, info):
    verified = {}
    for channel, record in _members(root, info).items():
        try:
            verified[channel] = (record, verified_scene_bundle(root, record))
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise ValueError(f"카메라 {channel} · {record['image_id']} · {exc}") from exc
    return verified


def read_complete_measurement(root,identifier):
    receipt=read_json(pair_path(root,identifier))
    if not isinstance(receipt,dict) or receipt.get('schema')!='dual-measurement-v1' or receipt.get('status')!='complete':
        raise ValueError('동시측정 미완료 · 미완료 측정 확인이 필요합니다.')
    if receipt['info']['id']!=identifier:
        raise ValueError('측정 파일 ID 불일치')
    return _verified_members(root,receipt['info'])


def verified_pair(root, record, cache=None):
    """Cache belongs to one scan/export, never to a later verification."""
    info = info_for(record)
    if info is None:
        from capture_recovery import require_finished_capture
        require_finished_capture(root, record)
        return None
    if not isinstance(info, dict):
        raise ValueError('동시측정 연결 정보 형식 오류')
    path = pair_path(root, info.get('id'))
    checked = cache.get(path) if cache is not None else None
    if checked is None:
        try:
            checked = read_complete_measurement(root,path.stem)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            checked = str(exc)
        if cache is not None:
            if len(cache) >= 64:
                cache.pop(next(iter(cache)))
            cache[path] = checked
    if isinstance(checked, str):
        raise ValueError(checked)
    member = checked[record['camera_id']][0]
    if member['image_id'] != record['image_id'] or info_for(member) != info:
        raise ValueError('다른 동시측정 짝입니다.')
    return checked


def require_complete_pair(root, record, cache=None):
    checked = verified_pair(root, record, cache)
    if checked is not None:
        return {k: item[0] for k, item in checked.items()}

@dataset_writer
def save_pair(root,results,batches,session,limit):
    from dataset19 import capture_id,persist_scene
    from pipeline import save_capture
    from records import validate_capture
    from layouts import layout
    from prediction_contract import validate_prediction
    require_same_empty_pair(results['A'].cfg, results['B'].cfg)
    pid=uuid.uuid4().hex;members={};root=Path(root)
    for k in CHANNELS:
        result=results[k];p,roi=result.packet,result.roi;validate_capture(p,roi)
        if p.camera_id!=k or p.source_kind!='camera' or not p.serial or len(roi['spaces'])!=layout(roi['lot_id'])['space_count']:raise ValueError('동시측정 카메라/칸 오류')
        validate_prediction(result.prediction,{'roi':roi,'frame_id':p.sequence,'captured_at_utc':p.timestamp_utc,'lot_id':roi['lot_id'],'stream_epoch':p.stream_epoch,'image_size':roi['image_size']})
        image_id=capture_id(p,roi,batches[k],session,result.cfg)
        if (root/'manifests'/(image_id+'.json')).exists():raise ValueError('이미 저장된 프레임을 새 동시측정에 사용할 수 없습니다.')
        members[k]={'image_id':image_id,'serial':p.serial,'frame_id':p.sequence,'timestamp':p.timestamp_utc,'epoch':p.stream_epoch,
                    'received_ns':round(p.received_monotonic*1e9),'lot':roi['lot_id'],'view':result.cfg['capture_view'],'batch':batches[k]}
        if p.sensor_frame is not None:members[k]['sensor_frame']=copy.deepcopy(p.sensor_frame)
    if members['A']['serial']==members['B']['serial']:raise ValueError('두 카메라의 시리얼이 같습니다.')
    delta=checked_skew_ms(members['A']['received_ns'],members['B']['received_ns'],limit)
    info={'id':pid,'session':session,'members':members,'skew_ms':delta,'max_skew_ms':limit,'clock':'host_monotonic_receive_ns'}
    path=pair_path(root,pid);receipt={'schema':'dual-measurement-v1','info':info,'status':'pending'}
    atomic_json(path,receipt)
    quality={}
    bay_holds={}
    try:
        # Secure both raw frames and prediction journals before slower per-bay work.
        # The existing recovery path can then reconstruct either interrupted scene.
        for k in CHANNELS:
            r=results[k]
            save_capture(root,r.packet,r.roi,batches[k],session,{**r.cfg,'dual_measurement':info},
                         scene_assets=True,identifier=members[k]['image_id'],
                         prediction_expected=True,prediction_snapshot=r.prediction)
        for k in CHANNELS:
            r=results[k];cfg={**r.cfg,'dual_measurement':info}
            rec,assets=persist_scene(root,r.packet,r.roi,batches[k],session,cfg,r.prediction)
            if assets['status']!='complete' or rec['image_id']!=members[k]['image_id']:raise OSError('동시측정 일부 저장 실패: '+k+str(assets['errors']))
            selection=read_json(root/'selection'/(rec['image_id']+'.json'))
            quality[k]=selection['상태']
            bay_holds[k]={row['주차칸명']:row['사유'] for row in selection['칸별결과'] if row['사유']}
        _verified_members(root,info)
        atomic_json(path,{**receipt,'status':'complete'})
    except Exception as e:
        try:atomic_json(path,{**receipt,'status':'partial','error':str(e)})
        except OSError:pass
        raise
    return {**info,'quality':quality,'bay_holds':bay_holds}

def pair_inventory(root):
    root = Path(root).resolve()
    # A receipt can vanish independently of the two scene manifests.
    receipts = set((root/'dual_measurements').glob('*.json'))
    missing = {}
    unidentified = []
    for manifest in (root/'manifests').glob('*.json'):
        try:
            info = info_for(read_json(manifest))
            if info is not None:
                path = pair_path(root, info['id'])
                if path not in receipts:
                    missing.setdefault(path, {})[manifest.stem] = info
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            unidentified.append({'file': manifest.name, 'reason': str(exc)})
    return receipts, missing, unidentified


def inspect_pairs(root, receipts, missing, *, restore=False, session=None):
    """Yield verified measurements one pair at a time under the caller's writer lock."""
    for path in sorted(receipts | missing.keys()):
        files = [path.name]
        if path in missing:
            files = [i + '.json' for i in missing[path]]
        try:
            if path in missing:
                claims = missing[path]
                info = next(iter(claims.values()))
                if session and info['session'] != session:
                    continue
                if not restore:
                    raise ValueError('동시측정 완료 기록 유실 · 미완료 측정 확인이 필요합니다.')
                if any(value != info for value in claims.values()) or set(claims) != {m['image_id'] for m in info['members'].values()}:
                    raise ValueError('완료 기록 유실 · 양쪽 장면의 측정 정보가 일치해야 복구할 수 있습니다.')
                receipt = {'schema': 'dual-measurement-v1', 'status': 'pending', 'info': info}
            else:
                receipt = read_json(path)
                info = receipt['info']
                if session and info['session'] != session:
                    continue
            if path!=pair_path(root,info['id']) or receipt.get('schema')!='dual-measurement-v1':raise ValueError('측정 파일 ID/규격 불일치')
            files = [pair_member['image_id'] + '.json' for pair_member in info['members'].values()]
            if receipt['status'] not in ('pending','partial','complete'):raise ValueError('측정 상태 오류')
            if receipt['status'] != 'complete' and not restore:
                raise ValueError('동시측정 미완료 · 미완료 측정 확인이 필요합니다.')
            members = _verified_members(root, info)
            restored = receipt['status'] != 'complete'
            if restored:
                atomic_json(path,{'schema':receipt['schema'],'info':info,'status':'complete'})
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            yield path.stem, None, False, [{'file': name, 'reason': str(exc)} for name in files or [path.name]]
        else:
            yield path.stem, members, restored, []


@dataset_writer
def recover_pairs(root,cfg):
    from capture_recovery import recover_captures
    raw = recover_captures(root, cfg)
    receipts, missing, unidentified = pair_inventory(root)
    summary = {'complete': 0, 'held': 0, 'restored_receipts': 0, 'errors': [],
               'unidentified_manifests': unidentified, 'bay_pairs': dict.fromkeys(CHANNELS, 0),
               'capture_recovery': raw}
    for pid, members, restored, errors in inspect_pairs(root, receipts, missing, restore=True):
        if errors:
            summary['held'] += 1
            summary['errors'].append(pid + ' · ' + errors[0]['reason'])
            continue
        summary['restored_receipts'] += int(restored)
        summary['complete'] += 1
        for channel, (record, _) in members.items():
            summary['bay_pairs'][channel] += len(record['roi']['spaces'])
    return summary
