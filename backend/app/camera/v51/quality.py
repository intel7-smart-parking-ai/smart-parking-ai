"""Advisory image metrics; thresholds are initial heuristics, not OCR accuracy."""
import cv2
import numpy as np

def read_quality_config(path):
    """Validate file syntax/types without claiming hardware range support."""
    import json,math
    from pathlib import Path
    path=Path(path)
    data=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    allowed={'exposure','gain','white_balance','sharpness','contrast','saturation','brightness','power_line_frequency','auto_exposure_priority'}
    if not isinstance(data,dict) or set(data)-{'defaults','A','B'}:raise ValueError('camera_quality.json: defaults/A/B 객체만 허용합니다.')
    for camera,options in data.items():
        if not isinstance(options,dict) or set(options)-allowed:raise ValueError('camera_quality.json: '+camera+'의 설정 항목/형식을 확인하세요.')
        for key,value in options.items():
            if value is None:continue
            if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('camera_quality.json: '+camera+'.'+key+'는 유한한 숫자 또는 null이어야 합니다.')
            if key=='power_line_frequency' and value not in (0,1,2):raise ValueError('power_line_frequency는 0/1/2입니다.')
            if key=='auto_exposure_priority' and value not in (0,1):raise ValueError('auto_exposure_priority는 0/1입니다.')
    return data

def dark_registration_frame(image):
    """Reject only an almost entirely black frame before board detection or OCR."""
    gray = cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    mean = float(np.mean(gray))
    if mean >= 8:
        return None
    fraction = float(np.mean(gray < 24))
    if fraction < .99:
        return None
    return {'평균밝기_0_255':round(mean,3),'밝기24미만_비율':round(fraction,6)}


def image_quality(image):
    if not isinstance(image,np.ndarray) or image.ndim!=3 or image.shape[2]!=3 or image.size==0:raise ValueError('유효한 컬러 영상이 필요합니다.')
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    sharpness=float(cv2.Laplacian(gray,cv2.CV_64F).var())
    dark=float(np.mean(gray<40));bright=float(np.mean(gray>245));warnings=[]
    if sharpness<60:warnings.append('선명도 낮음 가능성')
    if dark>.35:warnings.append('어두운 픽셀 많음')
    if bright>.20:warnings.append('밝은 영역/반사 가능성')
    return {'laplacian_variance':sharpness,'dark_ratio':dark,'bright_ratio':bright,
            'width':image.shape[1],'height':image.shape[0],'warnings':warnings,
            'assessment':'참고용 지표 · 번호 인식 가능 여부를 보장하지 않음'}

def inspect_sensor(sensor,rs):
    values={}
    for name in ('exposure','gain','white_balance','enable_auto_exposure','enable_auto_white_balance','sharpness','power_line_frequency'):
        option=getattr(rs.option,name,None)
        try:
            if option is None or not sensor.supports(option):values[name]={'supported':False};continue
            limits=sensor.get_option_range(option)
            values[name]={'supported':True,'value':sensor.get_option(option),'min':limits.min,'max':limits.max,'step':limits.step,'read_only':sensor.is_option_read_only(option)}
        except Exception as exc:values[name]={'supported':False,'error':str(exc)}
    return values

def apply_sensor(sensor,rs,changes):
    import math
    allowed={'exposure','gain','white_balance','enable_auto_exposure','enable_auto_white_balance','sharpness','power_line_frequency'}
    if not isinstance(changes,dict) or set(changes)-allowed:raise ValueError('지원하지 않는 설정 항목')
    info=inspect_sensor(sensor,rs)
    # Validate ALL requested values before touching the sensor.
    for name,value in changes.items():
        entry=info[name]
        if type(value) not in (int,float) or not math.isfinite(value):raise ValueError(name+': 유한한 숫자가 필요합니다.')
        unlock=(name in ('exposure','gain') and changes.get('enable_auto_exposure')==0) or (name=='white_balance' and changes.get('enable_auto_white_balance')==0)
        if not entry.get('supported') or (entry['read_only'] and not unlock):raise ValueError(name+': 미지원 또는 읽기 전용')
        if not entry['min']<=value<=entry['max']:raise ValueError(name+': 허용 범위 밖')
        step=entry['step']
        if step>0 and abs((value-entry['min'])/step-round((value-entry['min'])/step))>1e-5:raise ValueError(name+': 지원하는 단계값이 아닙니다.')
    for name in ('enable_auto_exposure','enable_auto_white_balance','exposure','gain','white_balance','sharpness','power_line_frequency'):
        if name in changes:sensor.set_option(getattr(rs.option,name),float(changes[name]))
    actual=inspect_sensor(sensor,rs)
    if any(not actual.get(name,{}).get('supported') for name in changes):raise RuntimeError('적용 후 실제값 조회 실패 · 저장하지 않고 다시 조회하세요.')
    mismatches=[name for name,value in changes.items()
                if not math.isclose(actual[name]['value'],value,rel_tol=1e-6,abs_tol=1e-5)]
    if mismatches:
        raise RuntimeError('요청값과 실제값 불일치: '+', '.join(mismatches)+' · 일부 적용 가능, 실제값을 다시 조회하세요.')
    return actual
