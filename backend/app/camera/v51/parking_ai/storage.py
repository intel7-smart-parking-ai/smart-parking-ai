"""경로·메타데이터 검증과 원자적 JSON 교체. 카메라를 열지 않는 공통 함수입니다."""
import json
import math
import os
import time
import re
import uuid
from pathlib import Path


def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('데이터셋 밖의 경로입니다.')
    return path


def replace_written_file(temp,path):
    """Retry only publishing the completed file while a Windows handle is briefly held."""
    for delay in (.02,.04,.08,.16,None):
        try:
            os.replace(temp,path)
            return
        except PermissionError:
            if delay is None:
                raise
            time.sleep(delay)


def atomic_json(path, data):
    """완성된 파일만 보이게 합니다. 기존 검수 파일을 바꿀 때도 중간 JSON이 노출되지 않습니다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        replace_written_file(temp,path)
    finally:
        temp.unlink(missing_ok=True)


def publish_directory(staging, destination):
    """Publish a fully written UUID directory. Short Windows file locks can outlast close()."""
    for delay in (.03,.06,.12,.24,.48,None):
        if destination.exists():
            raise FileExistsError(destination)
        try:
            staging.rename(destination)
            return
        except PermissionError:
            if delay is None:raise
            time.sleep(delay)


def valid_bbox(bbox, size):
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    if not all(type(v) in (int, float) and math.isfinite(v) for v in bbox):
        return False
    x1, y1, x2, y2 = bbox
    return 0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]


def read_manifest(path, root):
    record = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(record, dict):
        raise ValueError('manifest는 JSON 객체여야 합니다.')
    image_id = record.get('image_id')
    if not isinstance(image_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', image_id):
        raise ValueError('잘못된 image_id')
    if Path(path).stem != image_id:
        raise ValueError('파일명과 image_id가 다릅니다.')
    size = record.get('image_size')
    if not isinstance(size, list) or len(size) != 2 or not all(type(n) is int and n > 0 for n in size):
        raise ValueError('잘못된 원본 크기')
    for key in ('batch_id', 'camera_id', 'raw_path'):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(f'{key}가 없습니다.')
    if record['camera_id'] not in ('A', 'B'):
        raise ValueError('카메라 ID는 A/B여야 합니다.')
    if not isinstance(record.get('observations'), list):
        raise ValueError('observations 목록이 없습니다.')
    if not isinstance(record.get('collection_context', {}), dict):
        raise ValueError('잘못된 collection_context')
    if not isinstance(record.get('captured_at_utc', ''), str):
        raise ValueError('잘못된 촬영 시각')
    if not isinstance(record.get('collection_context', {}).get('scenario', 'legacy'), str):
        raise ValueError('촬영 종류는 문자열이어야 합니다.')
    for key in ('installation', 'image_quality'):
        if not isinstance(record.get(key, {}), dict):
            raise ValueError(f'{key}는 JSON 객체여야 합니다.')
    # 주차칸 내보내기/검수 화면에서 잘못된 좌표를 학습 라벨로 쓰지 않도록 검사합니다.
    import cv2
    import numpy as np
    spaces = record.get('roi_spaces', [])
    if not isinstance(spaces, list):
        raise ValueError('roi_spaces는 목록이어야 합니다.')
    names = set()
    for space in spaces:
        if not isinstance(space, dict) or not isinstance(space.get('space_id'), str):
            raise ValueError('잘못된 주차칸 ID')
        name = space['space_id']
        if not re.fullmatch(re.escape(record['camera_id'])+r'-[0-9]{2,}', name) or name in names:
            raise ValueError('카메라와 불일치하거나 중복된 주차칸 ID')
        names.add(name)
        polygon = space.get('polygon')
        if not isinstance(polygon, list) or len(polygon) < 3:
            raise ValueError('잘못된 주차칸 다각형')
        for point in polygon:
            if (not isinstance(point, list) or len(point)!=2
                    or not all(type(n) is int for n in point)
                    or not 0<=point[0]<size[0] or not 0<=point[1]<size[1]):
                raise ValueError('잘못된 주차칸 좌표')
        points = np.array(polygon,dtype=np.int32)
        if not cv2.isContourConvex(points) or cv2.contourArea(points)<=10:
            raise ValueError('주차칸은 유효한 볼록 다각형이어야 합니다.')
    proposals = record.get('sticker_observations', [])
    if not isinstance(proposals,list):
        raise ValueError('잘못된 번호 후보 목록')
    for proposal in proposals:
        if not isinstance(proposal,dict) or not valid_bbox(proposal.get('sticker_bbox'),size):
            raise ValueError('잘못된 번호 후보 박스')
    for item in record['observations']:
        if not isinstance(item, dict) or not valid_bbox(item.get('vehicle_bbox'), size):
            raise ValueError('잘못된 차량 박스')
        for key in ('car_id', 'space_id'):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise ValueError(f'{key}는 문자열 또는 null이어야 합니다.')
    if not safe_path(root, record['raw_path']).is_file():
        raise ValueError('원본 이미지가 없습니다.')
    return record


def scan_manifests(root):
    """손상된 한 파일 때문에 전체 수집/검수가 멈추지 않도록 오류 목록을 함께 반환합니다."""
    records, errors = [], []
    for path in sorted((Path(root) / 'manifests').glob('*.json')):
        try:
            records.append(read_manifest(path, root))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append({'file': path.name, 'reason': str(exc)})
    return records, errors
