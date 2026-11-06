"""An operator-confirmed pair of empty views, bound to this camera connection."""
import hashlib
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from dataset_lock import dataset_writer
from layouts import layout
from parking_ai.storage import atomic_json, safe_path, publish_directory
from quality_selection import digest, measure, policy_for
from records import validate_capture
from vision19 import context_key, make_reference

def disc_evidence(image,roi):
    """Look for printed sticker ink before the operator's empty view becomes a baseline."""
    from board_profile import match_aisle_light
    from round_sticker import identify
    from contracts import FIELD_SET,number_config_for_lot
    from static_markings import unchanged_regions
    ignored=unchanged_regions(image,roi)
    working,_=match_aisle_light(image,roi)
    config=number_config_for_lot(roi['lot_id'],{'physical_sticker_set':FIELD_SET})
    found=[];weak=[]
    for slot in roi['spaces']:
        sid=slot['space_id']
        disc,result=identify(working,slot,None,config,ignored.get(sid))
        estimate=result.get('estimate')
        if disc and (result['status'] in ('candidate','sticker_number_unread')
                     or (estimate and estimate['score']>=.80 and estimate['margin']>=.03)):
            found.append(sid)
        elif (disc and estimate) or result['status'] in ('multiple_stickers','multiple_sticker_candidates'):
            weak.append(sid)
    return found,tuple(weak)

def numbered_discs(image,roi):
    return disc_evidence(image,roi)[0]

@dataclass
class EmptyView:
    packet: object
    roi: dict
    suspect_bays: tuple = ()


@dataset_writer
def store_empty_pair(root, views, config=None, max_skew_ms=100):
    """The caller has shown and confirmed these exact two frozen frames."""
    from dataset19 import write_png
    from dual_capture import checked_skew_ms
    from static_markings import compare_markings
    from contracts import FIELD_SET
    if set(views) != {'A', 'B'} or views['A'].packet.serial == views['B'].packet.serial:
        raise ValueError('서로 다른 두 카메라의 빈 기준이 필요합니다.')
    # Compare the two frozen frames, independent of how long the operator took to confirm them.
    received={k:round(v.packet.received_monotonic*1e9) for k,v in views.items()}
    skew=checked_skew_ms(received['A'],received['B'],max_skew_ms)
    timing={'PC수신_ns':received,'허용차_ms':max_skew_ms,'수신차_ms':skew}
    identifier = uuid.uuid4().hex
    folder = safe_path(root, f'references/dual/{identifier}')
    policy = policy_for(config or {})
    manifest = {'자료규격':'듀얼빈기준-v1', '빈기준ID':identifier, '확인방법':'두 고정 장면의 전체 빈칸을 촬영자가 확인', '장면':{}}
    for channel, view in views.items():
        p, roi = view.packet, view.roi
        validate_capture(p, roi)
        if roi.get('registration_method')=='virtual_sticker_guide_v1':
            raise ValueError(f'빈 기준 보류 · 카메라 {channel}의 실물 주차칸을 먼저 등록하세요.')
        ids = {slot['space_id'] for slot in layout(roi['lot_id'])['spaces']}
        if (p.camera_id != channel or p.source_kind != 'camera' or not p.serial or
            {slot['space_id'] for slot in roi['spaces']} != ids):
            raise ValueError('빈 기준의 실물 입력 또는 전체 주차칸 정보가 맞지 않습니다.')
        covered=[sid for sid,_,score,contrast in compare_markings(p.image,roi)
                 if contrast>=10 and score<.35]
        if covered:
            raise ValueError(f"빈 기준 보류 · 카메라 {channel} {', '.join(covered)}의 기준 글씨를 확인할 수 없습니다. 차량·가림·구도를 확인하세요.")
        if (config or {}).get('physical_sticker_set')==FIELD_SET and roi['lot_id'] in ('A','B'):
            occupied,weak=disc_evidence(p.image,roi)
            if occupied:
                raise ValueError(f"빈 기준 보류 · 카메라 {channel} {', '.join(occupied)}에서 원형 숫자 스티커가 보입니다. 차량을 치우고 다시 등록하세요.")
            if weak!=view.suspect_bays:
                raise ValueError(f"빈 기준 보류 · 카메라 {channel}의 약한 스티커 후보를 고정 화면에서 다시 확인하세요: {', '.join(weak)}")
        for slot in roi['spaces']:
            if slot.get('name_from_adjacent'):
                raise ValueError(f"빈 기준 보류 · 카메라 {channel} {slot['space_id']}의 칸 이름이 가려졌습니다. 차량을 치우고 다시 확인하세요.")
            metrics = measure(p.image,slot['polygon'])
            if (metrics['짧은변_px'] < policy['min_bay_short_side'] or
                metrics['어두운픽셀비율'] > policy['max_dark_ratio'] or
                metrics['밝은픽셀비율'] > policy['max_bright_ratio']):
                raise ValueError(f"빈 기준 보류 · 카메라 {channel} {slot['space_id']}의 해상도·노출을 확인하세요.")
    references = {}
    folder.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder.parent,prefix='.pending-') as tmp:
        staging=Path(tmp)/identifier
        for channel, view in views.items():
            p, roi = view.packet, view.roi
            image_path = staging/(channel+'.png')
            image_hash = write_png(image_path, p.image)
            payload=image_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != image_hash:
                raise OSError('빈 기준 원본 저장 검증 실패 · '+channel)
            from collection_output import verify_display_png
            rgb=cv2.cvtColor(p.image,cv2.COLOR_BGR2RGB)
            pixel_hash=hashlib.sha256(rgb.tobytes()).hexdigest()
            verify_display_png(payload,roi['image_size'],pixel_hash)
            evidence = {'빈기준_수신시각':timing,'기준RGB_SHA256':pixel_hash,'빈기준ID':identifier, '카메라ID':channel, '시리얼':p.serial, '주차장':roi['lot_id'],
                        '설치구도':roi['pose'], '연결구간':p.stream_epoch, '프레임':p.sequence,
                        '촬영시각_UTC':p.timestamp_utc, '기준원본_SHA256':image_hash, '기준ROI_SHA256':digest(roi),
                        '재확인칸_SHA256':digest(list(view.suspect_bays))}
            manifest['장면'][channel] = {'근거':evidence, '주차칸등록':roi, '원본파일':channel+'.png'}
            if view.suspect_bays:manifest['장면'][channel]['촬영자_재확인칸']=list(view.suspect_bays)
            references[channel] = (roi, {**make_reference(p, roi), 'empty_board':evidence})
        atomic_json(staging/'reference.json', manifest)
        publish_directory(staging,folder)
    return references


def reference_for(stored, packet, roi):
    old, reference = stored
    if any(view.get('registration_method')=='virtual_sticker_guide_v1' for view in (old,roi)):
        raise ValueError('가상 안내선에는 실물 빈 기준을 사용할 수 없습니다.')
    if (old['calibration_key'] != roi['calibration_key'] or reference['epoch'] != packet.stream_epoch or
        reference['frame'] >= packet.sequence):
        raise ValueError('카메라 연결·촬영 시점 변경 · 두 빈 주차장 기준을 다시 등록하세요.')
    old_marker = old.get('marker_evidence')
    new_marker = roi.get('marker_evidence')
    if bool(old_marker) != bool(new_marker) or (old_marker and old_marker['template']['sha256'] != new_marker['template']['sha256']):
        raise ValueError('마커 기준 변경 · 두 빈 주차장 기준을 다시 등록하세요.')
    if old_marker:
        # The same printed board may produce subpixel marker jitter. Align only tiny
        # planar changes. A genuine camera move needs a new empty reference.
        transform = np.asarray(new_marker['homography']) @ np.linalg.inv(old_marker['homography'])
        w, h = roi['image_size']
        corners = np.float32([[[0,0],[w-1,0],[w-1,h-1],[0,h-1]]])
        moved = cv2.perspectiveTransform(corners,transform)
        if not np.isfinite(moved).all() or np.max(np.linalg.norm(moved-corners,axis=2)) > 3:
            raise ValueError('빈 기준과 구도가 달라졌습니다. 빈 보드에서 다시 등록하세요.')
        if not np.allclose(moved, corners, atol=.01, rtol=0):
            image = cv2.warpPerspective(reference['image'], transform, (w,h), borderMode=cv2.BORDER_REPLICATE)
            return {**reference, 'key':context_key(roi), 'image':image}
    elif reference['key'] != context_key(roi):
        from board_profile import METHOD,align_printed_reference
        if old.get('registration_method')==roi.get('registration_method')==METHOD:
            if reference['key']!=context_key(old):raise ValueError('저장한 빈 기준의 좌표 근거 불일치')
            aligned,proof=align_printed_reference(old,roi,reference['image'],packet.image)
            return {**reference,'key':context_key(roi),'image':aligned,'printed_board_warp':proof}
        raise ValueError('주차칸 좌표 변경 · 빈 보드에서 다시 등록하세요.')
    return {**reference, 'key':context_key(roi)}


def read_reference_source(root, evidence):
    from pipeline import read_json
    identifier = evidence['빈기준ID']
    if not isinstance(identifier,str) or not re.fullmatch('[0-9a-f]{32}',identifier):
        raise ValueError('빈 기준 ID 오류')
    channel = evidence['카메라ID']
    if channel not in ('A','B'):
        raise ValueError('빈 기준 채널 오류')
    folder = f'references/dual/{identifier}/'
    manifest = read_json(safe_path(root,folder+'reference.json'))
    member = manifest['장면'][channel]
    if (manifest.get('자료규격') != '듀얼빈기준-v1' or manifest.get('빈기준ID') != identifier or
        member['근거'] != evidence or member['원본파일'] != channel+'.png' or
        digest(member['주차칸등록']) != evidence['기준ROI_SHA256']):
        raise ValueError('빈 기준 등록 기록 불일치')
    if '재확인칸_SHA256' in evidence and digest(member.get('촬영자_재확인칸',[]))!=evidence['재확인칸_SHA256']:
        raise ValueError('빈 기준 촬영자 재확인 칸 기록 불일치 · '+channel)
    payload=safe_path(root,folder+channel+'.png').read_bytes()
    if hashlib.sha256(payload).hexdigest() != evidence['기준원본_SHA256']:
        raise ValueError('빈 기준 원본 해시 불일치 · '+channel)
    timing=evidence.get('빈기준_수신시각')
    if any('빈기준_수신시각' in x['근거'] for x in manifest['장면'].values()):
        from dual_capture import checked_skew_ms
        if not isinstance(timing,dict) or set(manifest['장면'])!={'A','B'} or any(
                digest(x['근거'].get('빈기준_수신시각'))!=digest(timing) for x in manifest['장면'].values()):
            raise ValueError('빈 기준 두 장면의 PC 수신 시각 기록 불일치')
        received=timing.get('PC수신_ns')
        if not isinstance(received,dict) or set(received)!={'A','B'} or type(timing.get('수신차_ms')) not in (int,float):
            raise ValueError('빈 기준 PC 수신 시각 형식 오류')
        if checked_skew_ms(received['A'],received['B'],timing.get('허용차_ms'))!=timing['수신차_ms']:
            raise ValueError('빈 기준 PC 수신차 계산 불일치')
    from collection_output import verify_display_png
    verify_display_png(payload,member['주차칸등록']['image_size'],evidence.get('기준RGB_SHA256'))
    return member,payload


def verify_empty_evidence(root, record, prediction):
    evidence = record['analysis_config'].get('empty_board')
    reference=(prediction or {}).get('reference') or {}
    reported=reference.get('empty_board')
    proof=reference.get('printed_board_warp')
    if evidence is None and reported is None:
        if proof is not None:raise ValueError('재정합에 사용한 빈 기준 기록이 없습니다.')
        return
    if not isinstance(evidence, dict) or reported != evidence or (prediction or {}).get('mode') != 'reference':
        raise ValueError('빈 기준과 저장된 판독 근거 불일치')
    if ((record['camera_id'],record['serial'],record['lot_id'],record['roi']['pose'],record['stream_epoch']) !=
        tuple(evidence[k] for k in ('카메라ID','시리얼','주차장','설치구도','연결구간')) or
        record['frame_id'] <= evidence['프레임']):
        raise ValueError('빈 기준의 장치·구도·프레임 연결 오류')
    member,payload=read_reference_source(root,evidence)
    old=member['주차칸등록'];roi=record['roi']
    from board_profile import METHOD,align_printed_reference
    if (old.get('registration_method')==roi.get('registration_method')==METHOD
            and context_key(old)!=context_key(roi)):
        from records import load_original
        empty=cv2.imdecode(np.frombuffer(payload,np.uint8),cv2.IMREAD_COLOR)
        from layouts import validate_snapshot
        validate_snapshot(old)
        _,expected=align_printed_reference(old,roi,empty,load_original(root,record))
        if not isinstance(proof,dict) or set(proof)!=set(expected):
            raise ValueError('저장된 빈 기준 도면 정합 근거 누락')
        matrix=np.asarray(proof['빈기준에서현재로'])
        if (matrix.shape!=(3,3) or matrix.dtype.kind not in 'fiu' or not np.isfinite(matrix).all()
                or not np.allclose(matrix,expected['빈기준에서현재로'],atol=1e-10,rtol=1e-9)
                or proof['최대칸이동_px']!=expected['최대칸이동_px'] or proof['일치한주차선']!=expected['일치한주차선']):
            raise ValueError('저장된 빈 기준 도면 정합 근거 불일치')
    elif proof is not None:raise ValueError('빈 기준 도면 정합의 위치 근거 오류')


def copy_references(root, target, used, transfer):
    """Export only the cameras in scope, once per confirmed reference."""
    from collection_output import atomic_bytes
    for identifier, channels in used.items():
        folder=f'references/dual/{identifier}/'
        members={}
        for channel,evidence in channels.items():
            member,payload=read_reference_source(root,evidence)
            relative=folder+channel+'.png'
            atomic_bytes(safe_path(target,relative),payload)
            transfer.verify(relative,evidence['기준원본_SHA256'])
            members[channel]=member
        relative=folder+'reference.json'
        report={'자료규격':'내보낸빈기준-v1','빈기준ID':identifier,'장면':members}
        atomic_json(safe_path(target,relative),report)
        transfer.report(relative,report)
    return {'images':sum(map(len,used.values())),'json_files':len(used)}
