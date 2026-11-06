"""Photo-derived placement guides. Their coordinates describe the screen, not the floor."""
from functools import lru_cache
import numpy as np

from board_profile import load
from layouts import snapshot

METHOD='virtual_sticker_guide_v1'


def is_guide(roi):
    return roi.get('registration_method')==METHOD


def waiting_for_sticker(roi, prediction):
    if not is_guide(roi) or prediction['errors']:return False
    return not any(row.get('sticker_polygon') or row.get('ocr_status') in ('multiple_stickers','multiple_sticker_candidates')
                   for row in prediction['spaces'])


@lru_cache(maxsize=24)
def guide_spaces(lot,width,height):
    if lot not in ('A','B') or min(width,height)<64:
        raise ValueError('가상 도면용 A/B 주차장과 영상 크기를 확인하세요.')
    _,_,source,_=load(lot)
    points=np.array([s['polygon'] for s in source],float)
    lower=points.min(axis=(0,1));upper=points.max(axis=(0,1))
    frame=np.array([width-1,height-1])
    scale=.92*min(frame/(upper-lower))
    projected=np.rint((points-(upper+lower)/2)*scale+frame/2).astype(int)
    return [dict(space_id=s['space_id'],space_type=s['space_type'],polygon=q.tolist(),boundary_kind='virtual_open_u')
            for s,q in zip(source,projected)]


def virtual_roi(packet,lot,pose):
    width,height=packet.image.shape[1::-1]
    roi=snapshot(lot,packet.camera_id,packet.serial,[width,height],pose,guide_spaces(lot,width,height))
    roi.update(registration_method=METHOD,operator_confirmed=False,guide_sha256=load(lot)[3])
    return roi


def validate(roi):
    if (roi.get('operator_confirmed') is not False or roi.get('unregistered_spaces')!=[]
            or roi.get('guide_sha256')!=load(roi['lot_id'])[3]
            or roi['spaces']!=guide_spaces(roi['lot_id'],*roi['image_size'])):
        raise ValueError('가상 안내선과 원본 도면의 좌표가 다릅니다.')


def physical_or_virtual(root,packet,lot,pose):
    from board_profile import locate as locate_print
    from marker_setup import load_template,locate as locate_markers
    template=load_template(root,lot)
    if template:
        try:
            found=locate_markers(template,packet,pose)
        except ValueError:
            found=None
        if found and found.get('ready'):return found['roi']
    found=locate_print(packet,lot,pose)
    return found['roi'] if found else virtual_roi(packet,lot,pose)
