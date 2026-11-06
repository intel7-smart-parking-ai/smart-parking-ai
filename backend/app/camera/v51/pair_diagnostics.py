"""Keep both exact inputs for a held analysis, outside measurement and export indexes."""
import hashlib
import tempfile
import uuid
from pathlib import Path

from automatic_setup import draw_setup_candidates, missing_stages
from dataset19 import write_png
from dataset_lock import dataset_writer
from dual_capture import CHANNELS, RegistrationHold, checked_skew_ms
from layouts import layout
from partial_bays import partial_label
from parking_ai.storage import atomic_json, safe_path, publish_directory


@dataset_writer
def save_held_pair(root,frames,outcomes,profile,session):
    if not any(isinstance(value,Exception) for value in outcomes.values()):
        raise ValueError('보류 사유가 필요합니다.')
    received={k:round(frames[k].received_monotonic*1e9) for k in CHANNELS}
    skew=checked_skew_ms(received['A'],received['B'],profile['max_skew_ms'])
    parent=safe_path(root,'diagnostics/held_pairs')
    parent.mkdir(parents=True,exist_ok=True)
    pair_id=uuid.uuid4().hex
    target=parent/pair_id
    report={'진단ID':pair_id,'세션ID':session,'상태':'측정 보류','두프레임_수신차_ms':skew,
            '허용수신차_ms':profile['max_skew_ms'],'카메라':{}}
    with tempfile.TemporaryDirectory(dir=parent,prefix='.pending-') as temp:
        folder=Path(temp)/pair_id
        for k in CHANNELS:
            packet,outcome=frames[k],outcomes[k]
            setting=profile['channels'][k]
            if packet.camera_id!=k or packet.serial!=setting['serial']:
                raise ValueError('진단 프레임과 카메라 설정이 다릅니다.')
            reason=str(outcome) if isinstance(outcome,Exception) else None
            setup=outcome.setup if isinstance(outcome,RegistrationHold) else {}
            spaces=setup.get('spaces') if reason is not None else outcome.roi['spaces']
            partial=setup.get('partial_spaces',[])
            evidence = (setup if reason is not None else outcome.roi.get('auto_validation', {})).get('text_evidence')
            raw=k+'_원본.png'
            overlay=k+'_등록표시.png'
            raw_sha=write_png(folder/raw,packet.image)
            annotated_sha=write_png(folder/overlay,draw_setup_candidates(packet.image,(spaces or [])+partial))
            for name,digest in ((raw,raw_sha),(overlay,annotated_sha)):
                if hashlib.sha256((folder/name).read_bytes()).hexdigest()!=digest:
                    raise OSError('보류 진단 사진 저장 검증 실패 · '+name)
            report['카메라'][k]={
                '시리얼':packet.serial,'주차장':setting['lot'],'촬영시점':setting['view'],
                '프레임번호':packet.sequence,'연결회차':packet.stream_epoch,'촬영시각_UTC':packet.timestamp_utc,
                'PC수신시각_ns':received[k],'해상도':list(packet.image.shape[1::-1]),
                '보류사유':reason,'필요칸수':layout(setting['lot'])['space_count'],
                '찾은칸수':len(spaces) if spaces is not None else None,
                '위치등록칸수':len(spaces)+len(partial) if spaces is not None else None,'부분등록칸':[partial_label(s) for s in partial],
                '못찾은칸':setup.get('missing',[] if reason is None else None),
                '미등록단계':missing_stages(setup) if reason is not None else {},
                '등록기준_판독칸명':None if evidence is None else sorted(r['space_id'] for r in evidence),
                '등록기준_재판독칸명':None if evidence is None else sorted(r['space_id'] for r in evidence if r.get('localized_retry')),
                '중복이름':setup.get('duplicate_names',[]),'등록오류':setup.get('errors',[]),
                '후보칸':None if spaces is None else [
                    {'칸명':s['space_id'],'꼭짓점_px':s['polygon'],'경계형태':s.get('boundary_kind','closed'),
                     '칸명확인에_사용한_양옆칸':s.get('name_from_adjacent')}
                    for s in spaces],
                '원본사진':raw,'원본_SHA256':raw_sha,'등록표시사진':overlay,'등록표시_SHA256':annotated_sha}
            if setup.get('dark_frame'):
                report['카메라'][k]['저조도측정']=setup['dark_frame']
        atomic_json(folder/'진단.json',report)
        publish_directory(folder,target)
    return target
