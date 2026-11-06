"""Operator-selected viewpoints, not measured camera rotation angles."""
VIEW_NAMES={'ceiling':'천장','right':'오른쪽','left':'왼쪽','upper':'위쪽','lower':'아래쪽','unspecified':'미지정'}


def view_code(value):
    code=value.get('capture_view','unspecified')
    if not isinstance(code,str) or code not in VIEW_NAMES:raise ValueError('촬영 시점은 오른쪽/왼쪽/위쪽/아래쪽/천장/미지정 중 하나입니다.')
    return code


def view_folder(value):
    code=view_code(value)
    return '' if code=='unspecified' else VIEW_NAMES[code]+'/'


def capture_pose(cfg):
    code=view_code(cfg);pose=cfg['camera_pose']['A']
    return pose if code=='unspecified' else pose+'__view-'+code


def view_counts(entries):
    result={v:{'장면수':0,'통과':0,'보류':0,'미판정':0,'저장오류':0} for v in VIEW_NAMES}
    for entry in entries:
        counts=result[entry.get('capture_view','unspecified')];counts['장면수']+=1
        counts[entry.get('quality_selection','미판정')]+=1
        counts['저장오류']+=int(bool(entry.get('errors')))
    return result
