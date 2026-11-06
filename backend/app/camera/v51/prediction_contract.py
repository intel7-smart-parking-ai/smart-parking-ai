"""Validate persisted/generated predictions before UI, disk, or HTTP use."""
import math
from analysis import number,validate_sticker_polygon
from contracts import number_config_for_lot
from layouts import COLOR_TYPES,zone_rule
from parking_ai.storage import valid_bbox


def finite(value,low=0,high=None):
    return type(value) in (int,float) and math.isfinite(value) and value>=low and (high is None or value<=high)


def validate_prediction(pred,record):
    from vision19 import context_key
    from review import region_matches_slot
    from sticker_ocr import overlap_fraction
    try:
        if not isinstance(pred,dict) or pred.get('schema') not in ('prediction-v19-1','prediction-v20-1','prediction-v21-1'):raise ValueError('예측 schema 오류')
        if pred['context_key']!=context_key(record['roi']) or pred['frame_id']!=record['frame_id'] or pred['captured_at_utc']!=record['captured_at_utc']:raise ValueError('분석과 촬영 장면이 다릅니다.')
        if type(pred['frame_id']) is not int or type(pred['stream_epoch']) is not int:raise ValueError('예측 프레임/연결 구간 형식 오류')
        if pred['lot_id']!=record['lot_id'] or pred['stream_epoch']!=record.get('stream_epoch',0):raise ValueError('분석 주차장/연결 구간 불일치')
        if pred['mode'] not in ('reference','classifier','manual','sticker') or pred.get('stage','complete') not in ('occupancy','complete'):raise ValueError('예측 분석 방식 오류')
        if not finite(pred['elapsed_ms']):raise ValueError('예측 처리 시간 오류')
        slots={s['space_id']:s for s in record['roi']['spaces']};rows=pred['spaces'];size=record['image_size']
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):raise ValueError('예측 칸 목록 형식 오류')
        if len(rows)!=len(slots) or {r['space_id'] for r in rows}!=set(slots):raise ValueError('분석 칸 목록 불일치')
        virtual=record['roi'].get('registration_method')=='virtual_sticker_guide_v1'
        if virtual and (pred['mode'] not in ('sticker','manual') or pred.get('reference') or pred['geometry']):
            raise ValueError('가상 안내선의 실물 기준·점유 모델·경계 판정은 허용하지 않습니다.')
        selected=[]
        parameters=pred.get('parameters',{})
        lighting=pred.get('sticker_lighting')
        tone=pred.get('measurement_lighting')
        revision=parameters.get('measurement_brightness')
        if revision not in (None,'aisle-v1') or (tone is not None and revision!='aisle-v1') or (revision and lighting is not None and lighting!=tone):
            raise ValueError('측정 사진과 스티커 밝기 보정 기록 불일치')
        for value in (lighting,tone):
            if value is None:continue
            if (not isinstance(value,dict) or value.get('source') not in ('printed_aisle','empty_aisle')
                    or not finite(value.get('gain'),.63,1.60) or not finite(value.get('offset'),-20,20)
                    or (value['source']=='printed_aisle' and record['roi'].get('registration_method')!='fixed_printed_board_v1')
                    or (value['source']=='empty_aisle' and (not revision or not (pred.get('reference') or {}).get('empty_board')))):
                raise ValueError('스티커 통로 밝기 보정 근거 오류')
        if 'analysis_config' in record and record['analysis_config'].get('physical_sticker_set')!=parameters.get('physical_sticker_set'):
            raise ValueError('촬영 설정과 판독 스티커 배정표가 다릅니다.')
        number_rules=number_config_for_lot(record['lot_id'],parameters)
        for row in rows:
            if 'sticker_exposure' in row:
                from sticker_exposure import validate
                validate(row['sticker_exposure'])
            state=row['occupancy'];color=row['color'];text=row['number']
            if state not in ('occupied','vacant','unknown') or color not in ('unknown','white','green','blue'):raise ValueError('예측 점유/색 상태 오류')
            if virtual and state=='vacant':raise ValueError('가상 안내선에서 실물 빈칸을 판정할 수 없습니다.')
            if row['review_status']!='pending':raise ValueError('자동 예측은 검수 승인이 아닙니다.')
            if row['occupancy_score'] is not None and not finite(row['occupancy_score'],0,1):raise ValueError('예측 점수 범위 오류')
            if row['score_kind'] not in ('difference_ratio','model_score','sticker_presence'):raise ValueError('예측 점수 의미 오류')
            if text is not None:
                if not isinstance(text,str) or number(text,number_rules)!=text:raise ValueError('예측 번호 규칙 오류')
                selected.append(text)
            if row['vehicle_type']!=COLOR_TYPES.get(color):raise ValueError('예측 색/차량 종류 불일치')
            expected=zone_rule(slots[row['space_id']]['space_type'],COLOR_TYPES.get(color)) if state=='occupied' else 'unknown'
            if slots[row['space_id']].get('boundary_kind')=='virtual_open_u':expected='unknown'
            if row['zone_rule']!=expected:raise ValueError('예측 전용칸 규칙 불일치')
            if row['plate_bbox'] is not None and not valid_bbox(row['plate_bbox'],size):raise ValueError('예측 숫자 박스 오류')
            if text is not None and row['plate_bbox'] is None:raise ValueError('예측 번호 근거 박스 없음')
            if row['sticker_polygon'] is not None:validate_sticker_polygon(row['sticker_polygon'],size)
            estimate=row.get('number_estimate')
            if estimate is not None:
                if (not isinstance(estimate,dict) or text is not None or row['ocr_status']!='estimated'
                    or state=='vacant' or color=='unknown' or row['sticker_polygon'] is None
                    or estimate.get('method') not in ('open_print','reference_print')
                    or not finite(estimate.get('score'),0,1) or not finite(estimate.get('margin'),0,1)
                    or not isinstance(estimate.get('number'),str) or number(estimate['number'],number_rules)!=estimate['number']
                    or not isinstance(estimate.get('alternate'),str) or number(estimate['alternate'],number_rules)!=estimate['alternate']
                    or estimate['alternate']==estimate['number'] or not valid_bbox(estimate.get('text_bbox'),size)
                    or overlap_fraction(estimate['text_bbox'],row['sticker_polygon'])<.6):
                    raise ValueError('추정 번호와 스티커 판독 근거 불일치')
                if 'exposure_agreement' in estimate and (not lighting or estimate['exposure_agreement']!='raw_and_partial_correction'):
                    raise ValueError('추정 번호의 밝기 대조 근거 오류')
            regions=[]
            if row['sticker_polygon'] is not None:regions.append(row['sticker_polygon'])
            if row['plate_bbox'] is not None:
                a,b,c,d=row['plate_bbox'];regions.append([[a,b],[c,b],[c,d],[a,d]])
            if any(not region_matches_slot(region,row['space_id'],record['roi']['spaces']) for region in regions):raise ValueError('예측 근거 영역과 주차칸 귀속 불일치')
            if text is not None:
                if row['ocr_status']!='candidate':raise ValueError('판독 실패/미실행 상태에 확정 번호 후보가 있습니다.')
                if row['sticker_polygon'] is not None and overlap_fraction(row['plate_bbox'],row['sticker_polygon'])<.6:raise ValueError('번호와 스티커 영역 불일치')
            evidence=row.get('ocr_candidates')
            if evidence is not None:
                if not isinstance(evidence,list):raise ValueError('OCR 근거 목록 오류')
                for item in evidence:
                    if (not isinstance(item,dict) or not isinstance(item.get('text'),str) or not isinstance(item.get('status'),str)
                            or not finite(item.get('score'),0,1) or not valid_bbox(item.get('text_bbox'),size)):raise ValueError('OCR 근거 필드 오류')
                    if item.get('number_candidate')!=number(item['text'],number_rules):raise ValueError('OCR 원문/번호 근거 불일치')
                if text is not None and not any(e.get('number_candidate')==text and e['status']=='candidate' and e['text_bbox']==row['plate_bbox'] for e in evidence):raise ValueError('번호와 직접 판독 근거 불일치')
            if pred['schema']=='prediction-v21-1' and text is None:
                if row.get('selected_ocr') is not None or row['ocr_status']=='candidate':
                    raise ValueError('번호가 없는 칸에 선택한 직접 판독 근거가 있습니다.')
                if row['ocr_status']=='estimated' and estimate is None:
                    raise ValueError('추정 상태에 추정 번호 근거가 없습니다.')
            if pred['schema']=='prediction-v21-1' and text is not None:
                selected_ocr=row.get('selected_ocr');threshold=pred['parameters']['ocr_min_score']
                if not finite(threshold,0,1) or not isinstance(evidence,list) or not evidence:raise ValueError('V21 번호 근거/기준 누락')
                if selected_ocr not in evidence or selected_ocr.get('number_candidate')!=text or selected_ocr['text_bbox']!=row['plate_bbox']:raise ValueError('V21 선택 근거 불일치')
                if (selected_ocr['status']!='candidate' or selected_ocr['score']<threshold
                        or selected_ocr.get('selected_in_view',True) is not True):
                    raise ValueError('V21 선택한 직접 판독 근거가 승인 기준에 미달합니다.')
                agreeing=[]
                from sticker_digits import METHODS
                for method in ('sticker_color','sticker_value_normalized','sticker_value_binary',*METHODS):
                    observations=[e for e in evidence if e.get('method')==method and e.get('selected_in_view',True)]
                    if len(observations)==1 and observations[0]['status']=='candidate' and observations[0]['score']>=threshold:
                        if observations[0]['number_candidate']!=text:raise ValueError('V21 변환 영상 간 판독 충돌')
                        if row['sticker_polygon'] is None or overlap_fraction(observations[0]['text_bbox'],row['sticker_polygon'])<.6:
                            raise ValueError('직접 판독에 합의한 영상의 숫자 영역이 같은 스티커에 속하지 않습니다.')
                        agreeing.append(observations[0])
                if len(agreeing)<2:raise ValueError('V21 두 영상 이상 판독 합의 부족')
                if selected_ocr not in agreeing:raise ValueError('V21 선택 근거가 합의한 영상에 없습니다.')
                if row['sticker_polygon'] is None:raise ValueError('V21 번호의 스티커 위치 근거 없음')
            if color!='unknown' and row['sticker_polygon'] is None:raise ValueError('예측 색 근거 영역 없음')
            if state=='vacant' and (text is not None or color!='unknown' or row['plate_bbox'] is not None or row['sticker_polygon'] is not None):raise ValueError('빈칸 예측에 차량 정보가 있습니다.')
            if not isinstance(row['issues'],list) or any(not isinstance(s,str) for s in row['issues']):raise ValueError('예측 사유 목록 오류')
            for key in ('occupancy_reason','geometry_status','ocr_status'):
                if not isinstance(row[key],str):raise ValueError('예측 설명 형식 오류')
            quality=row.get('quality',{})
            if not isinstance(quality,dict) or not isinstance(quality.get('warnings',[]),list) or any(not isinstance(s,str) for s in quality.get('warnings',[])):raise ValueError('예측 품질 설명 오류')
        if len(selected)!=len(set(selected)):raise ValueError('예측 번호 중복')
        if not isinstance(pred['errors'],list) or any(not isinstance(s,str) for s in pred['errors']):raise ValueError('예측 오류 목록 형식 오류')
        if not isinstance(pred['geometry'],list):raise ValueError('예측 경계 목록 형식 오류')
        for item in pred['geometry']:
            if not isinstance(item,dict) or not valid_bbox(item['bbox'],size):raise ValueError('예측 변화 박스 오류')
            if not isinstance(item['spaces'],list) or any(s not in slots for s in item['spaces']):raise ValueError('예측 변화 칸 오류')
            if not isinstance(item['reason'],str) or item['source']!='background_change_not_vehicle_mask':raise ValueError('예측 변화 근거 오류')
    except (KeyError,TypeError,AttributeError,OverflowError) as exc:
        raise ValueError('예측 필수 항목/형식 오류: '+str(exc)) from exc
    return pred
