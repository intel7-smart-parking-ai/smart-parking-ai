"""Validate editable configuration before starting workers."""
import math
from pathlib import Path
from layouts import COLOR_TYPES


def validate_config(cfg,project):
    if not isinstance(cfg,dict):raise ValueError('config.json은 객체여야 합니다.')
    try:
        if cfg['device'] not in ('auto','cpu','cuda','xpu'):raise ValueError('device는 auto/cpu/cuda/xpu입니다.')
        for key in ('save_interval','warmup_seconds','ocr_min_score'):
            value=cfg[key]
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(key+'는 유한한 숫자여야 합니다.')
        if cfg['save_interval']<=0 or cfg['warmup_seconds']<0 or not 0<=cfg['ocr_min_score']<=1:raise ValueError('저장 간격/준비 시간/OCR 점수 범위를 확인하세요.')
        defaults={'analysis_interval':1.,'difference_threshold':35,'empty_ratio':.025,'occupied_ratio':.12,'event_max_age_seconds':2.}
        for key,default in defaults.items():
            value=cfg.get(key,default)
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(key+'는 유한한 숫자여야 합니다.')
        if cfg.get('analysis_interval',1.)<=0 or not 0<cfg.get('difference_threshold',35)<255:raise ValueError('분석 간격/차영상 임계값 범위 오류')
        if not 0<=cfg.get('empty_ratio',.025)<cfg.get('occupied_ratio',.12)<=1:raise ValueError('empty_ratio < occupied_ratio (0~1)이어야 합니다.')
        if not 0<cfg.get('event_max_age_seconds',2.)<=30:raise ValueError('전송 유효 시간은 0 초과 30초 이하입니다.')
        roi_score=cfg.get('auto_roi_min_score',.7)
        if type(roi_score) not in (int,float) or not math.isfinite(roi_score) or not 0<=roi_score<=1:raise ValueError('auto_roi_min_score는 0~1의 유한한 숫자여야 합니다.')
        if cfg['sticker_region_mode']!='per_image_polygon' or cfg['color_to_type']!=COLOR_TYPES:raise ValueError('스티커 영역/색상 계약 불일치')
        if type(cfg['download_models']) is not bool:raise ValueError('download_models는 true/false여야 합니다.')
        if cfg.get('occupancy_model') is not None:
            from vision19 import validate_model_settings
            validate_model_settings(cfg['occupancy_model'])
        languages=cfg['ocr_languages']
        if not isinstance(languages,list) or not languages or any(not isinstance(s,str) or not s.strip() for s in languages):raise ValueError('ocr_languages는 언어 코드 목록이어야 합니다.')
        allowed=cfg.get('allowed_numbers')
        from contracts import sticker_colors
        sticker_colors(cfg)
        if allowed is not None and (not isinstance(allowed,list) or any(not isinstance(s,str) for s in allowed)):raise ValueError('allowed_numbers는 null 또는 문자열 목록이어야 합니다.')
        for c in ('A','B'):
            if cfg['camera_lots'][c] not in ('A','B'):raise ValueError('실물 카메라는 가/나 주차장에 연결하세요.')
            if not isinstance(cfg['camera_pose'][c],str) or not cfg['camera_pose'][c].strip():raise ValueError('camera_pose 설치 이름이 필요합니다.')
        directory=cfg['dataset_directory']
        if not isinstance(directory,str) or not directory.strip():raise ValueError('dataset_directory가 필요합니다.')
    except (KeyError,TypeError) as exc:raise ValueError('config.json 필수 항목/형식을 확인하세요: '+str(exc)) from exc
    from capture_views import view_code
    view_code(cfg)
    from quality_selection import policy_for
    policy_for(cfg)
    root=Path(directory).expanduser()
    return cfg,root.resolve() if root.is_absolute() else (Path(project)/root).resolve()
