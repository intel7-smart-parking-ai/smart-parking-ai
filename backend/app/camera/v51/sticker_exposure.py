"""Describe actual raw/analysis pixels; clipping alone never proves a bad number."""
import math
import cv2
import numpy as np


def _measure(image,polygon):
    points=np.asarray(polygon,np.int32)
    x,y,w,h=cv2.boundingRect(points)
    mask=np.zeros((h,w),np.uint8)
    cv2.fillConvexPoly(mask,points-[x,y],255)
    pixels=image[y:y+h,x:x+w][mask>0]
    gray=cv2.cvtColor(pixels.reshape(-1,1,3),cv2.COLOR_BGR2GRAY).ravel()
    return {'pixels':int(len(pixels)),
            'white_clip_ratio':round(float(np.mean(np.all(pixels>=250,axis=1))),6),
            'channel_clip_ratio':round(float(np.mean(np.any(pixels>=250,axis=1))),6),
            'contrast_p90_p10':round(float(np.percentile(gray,90)-np.percentile(gray,10)),3)}


def diagnose(raw,analysis,slot,disc=None):
    polygon=disc['polygon'] if disc else slot['polygon']
    first=_measure(raw,polygon);second=_measure(analysis,polygon)
    return {'scope':'sticker_polygon' if disc else 'bay_polygon','raw':first,'analysis':second,'status':status_for(first,second)}


def status_for(first,second):
    raw_lost=first['white_clip_ratio']>=.98 and first['contrast_p90_p10']<8
    processed_lost=second['white_clip_ratio']>=.98 and second['contrast_p90_p10']<8
    return ('raw_saturated_without_detail' if raw_lost else
            'correction_lost_detail' if processed_lost and first['contrast_p90_p10']>=8 else
            'highlight_present' if max(first['white_clip_ratio'],second['white_clip_ratio'])>.05 else 'no_clip_evidence')


def validate(value):
    if not isinstance(value,dict) or set(value)!={'scope','raw','analysis','status'}:
        raise ValueError('번호 영상 진단 형식 오류')
    if value['scope'] not in ('sticker_polygon','bay_polygon') or value['status'] not in (
            'raw_saturated_without_detail','correction_lost_detail','highlight_present','no_clip_evidence'):
        raise ValueError('번호 영상 진단 상태 오류')
    for key in ('raw','analysis'):
        row=value[key]
        if not isinstance(row,dict) or set(row)!={'pixels','white_clip_ratio','channel_clip_ratio','contrast_p90_p10'}:
            raise ValueError('번호 영상 진단 수치 형식 오류')
        if type(row['pixels']) is not int or row['pixels']<=0:raise ValueError('번호 영상 진단 픽셀 수 오류')
        for name,high in [('white_clip_ratio',1),('channel_clip_ratio',1),('contrast_p90_p10',255)]:
            v=row[name]
            if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=high:
                raise ValueError('번호 영상 진단 수치 범위 오류')
        if row['white_clip_ratio']>row['channel_clip_ratio']:raise ValueError('번호 영상 진단 포화 비율 오류')
    if value['raw']['pixels']!=value['analysis']['pixels']:raise ValueError('번호 영상 진단 영역 불일치')
    if value['status']!=status_for(value['raw'],value['analysis']):
        raise ValueError('번호 영상 진단 상태와 원본·보정 영상 수치가 다릅니다.')
    return value
