"""Read Geti metadata and return sticker detections in decoded-image pixels."""
from pathlib import Path
from datetime import datetime, timezone
import base64
import hashlib
import io
import math
import threading
import time
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from model_api.models import Model
from .image_input import MAX_BYTES, MAX_PIXELS, decode_image

LABELS = {'sticker_blue': ('blue', 'accessible'), 'sticker_green': ('green', 'ev'), 'sticker_white': ('white', 'general')}
COLORS = {'sticker_blue':'#2563eb', 'sticker_green':'#16a34a', 'sticker_white':'#64748b'}
KOREAN = {'sticker_blue':'파란색', 'sticker_green':'초록색', 'sticker_white':'흰색'}


def thresholded_nms(original, floor):
    """Run the SSD adapter's suppression only on scores it can return."""
    def suppress(boxes, scores, labels, include_boundaries=False):
        keep=np.flatnonzero(scores>floor)
        if not len(keep):return []
        # NumPy's ordering of equal scores depends on the input length. Keep
        # the adapter's original tie-breaking in that uncommon case.
        if len(np.unique(scores[keep]))!=len(keep) or not np.isfinite(scores).all():
            return original(boxes,scores,labels,include_boundaries)
        selected=original(boxes[keep],scores[keep],labels[keep],include_boundaries)
        return keep[np.asarray(selected,dtype=np.intp)].tolist()
    return suppress


def roi_pixels(roi, width, height):
    if roi is None:
        return 0, 0, width, height
    if (not isinstance(roi, (list, tuple)) or len(roi) != 4 or
            any(type(x) not in (int, float) or not math.isfinite(x) for x in roi)):
        raise ValueError('주차장 범위는 0~1 좌표 네 개여야 합니다.')
    x1,y1,x2,y2 = roi
    if not 0 <= x1 < x2 <= 1 or not 0 <= y1 < y2 <= 1:
        raise ValueError('주차장 범위가 올바르지 않습니다.')
    box = round(x1*width), round(y1*height), round(x2*width), round(y2*height)
    if box[2]-box[0] < 32 or box[3]-box[1] < 32:
        raise ValueError('주차장 범위가 너무 작습니다.')
    return box


class StickerModel:
    def __init__(self, path, device='CPU'):
        self.path = Path(path).resolve()
        tree = ET.parse(self.path)
        info = {node.tag:node.get('value') for node in tree.findall('./rt_info/model_info/*')}
        labels = info.get('labels','').split()
        if info.get('task_type') != 'detection' or len(labels)!=len(LABELS) or set(labels) != set(LABELS):
            raise ValueError('이 서버에는 파랑·초록·흰색 스티커 Detection 모델이 필요합니다.')
        threshold = float(info['confidence_threshold'])
        if not .05<=threshold<=.99:raise ValueError('스티커 모델 표시 기준이 지원 범위와 다릅니다.')
        self.metadata = {'name':info.get('model_name'), 'task':'detection', 'target':'number_sticker',
            'labels':labels, 'input_size':[640,640], 'input_layout':'NCHW', 'export_precision':'FP16',
            'device':device, 'default_threshold':round(threshold,6), 'adapter_threshold_floor':.05,
            'bbox_format':'[x1,y1,x2,y2]', 'bbox_units':'pixels of EXIF-oriented decoded image',
            'adapter_input_color':'BGR', 'model_reverse_input_channels':info.get('reverse_input_channels')=='True',
            'xml_sha256':hashlib.sha256(self.path.read_bytes()).hexdigest(),
            'weights_sha256':hashlib.sha256(self.path.with_suffix('.bin').read_bytes()).hexdigest(),
            'number_recognition':False, 'vehicle_body_detection':False, 'parking_state_confirmation':False}
        started = time.perf_counter()
        # The same PC runs OCR, two-camera registration, and HTTP delivery.
        # Bound CPU inference threads instead of competing for every core.
        self.model = Model.create_model(str(self.path), device=device, nthreads=2,
                                       configuration={'confidence_threshold':.05})
        if getattr(self.model,'__model__',None)=='SSD':
            self.model._calculate_nms=thresholded_nms(self.model._calculate_nms,.05)
        self.metadata['cpu_inference_threads']=2
        self.metadata['load_ms'] = round((time.perf_counter()-started)*1000,2)
        self.lock = threading.Lock()

    def infer(self, data, threshold=None):
        started = time.perf_counter()
        photo = decode_image(data)
        result = self.infer_bgr(np.ascontiguousarray(np.asarray(photo)[:,:,::-1]), threshold)
        result['total_ms'] = round((time.perf_counter()-started)*1000,2)
        return result

    def infer_bgr(self, bgr, threshold=None, *, roi=None, observed_at=None, preview_width=None,render=True):
        started = time.perf_counter()
        threshold = self.metadata['default_threshold'] if threshold is None else threshold
        if type(threshold) not in (float,int) or not math.isfinite(threshold) or not .05 <= threshold <= .99:
            raise ValueError('표시 기준은 0.05~0.99 숫자여야 합니다.')
        if (not isinstance(bgr,np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3 or
                bgr.shape[2] != 3 or bgr.shape[0]*bgr.shape[1] > MAX_PIXELS):
            raise ValueError('uint8 BGR 영상이 필요합니다.')
        height,width = bgr.shape[:2]
        ox,oy,ex,ey = roi_pixels(roi,width,height)
        crop = np.ascontiguousarray(bgr[oy:ey,ox:ex])
        if not self.lock.acquire(timeout=10):
            raise TimeoutError('다른 사진 분석이 진행 중입니다. 잠시 후 다시 시도하세요.')
        try:
            infer_started = time.perf_counter()
            # Model API embeds BGR->RGB when reverse_input_channels=True.
            # Feeding PIL RGB here would apply the channel conversion twice.
            result = self.model(crop)
            inference_ms = (time.perf_counter()-infer_started)*1000
            # Copy before releasing the reusable inference adapter to another request.
            boxes = result.bboxes.copy(); scores = result.scores.copy(); ids = result.labels.copy()
        finally:
            self.lock.release()
        detections = []
        for box, score, label_id in zip(boxes,scores,ids):
            score = float(score); label_id = int(label_id)
            if not math.isfinite(score) or not 0 <= score <= 1 or score < threshold:continue
            if not 0 <= label_id < len(self.metadata['labels']):raise ValueError('모델 라벨 인덱스 오류')
            if len(box)!=4 or any(not math.isfinite(float(x)) for x in box):continue
            coords = [int(x) for x in box]
            x1,y1,x2,y2 = coords
            if not 0<=x1<x2<=ex-ox or not 0<=y1<y2<=ey-oy:continue
            coords = [x1+ox,y1+oy,x2+ox,y2+oy]
            label = self.metadata['labels'][label_id]
            color,vehicle_type = LABELS[label]
            detections.append({'label':label,'confidence':score,'bbox_xyxy':coords,
                'bbox_target':'sticker','sticker_color':color,'color_name':KOREAN[label],
                'vehicle_type_from_sticker':vehicle_type,'car_id':None})
        detections.sort(key=lambda row:row['confidence'],reverse=True)
        if not render:
            return {'detections':detections,'inference_ms':round(inference_ms,2)}
        photo = Image.fromarray(bgr[:,:,::-1])
        drawing = ImageDraw.Draw(photo)
        font_size = max(16,round(width/65))
        try:
            font = ImageFont.truetype('malgun.ttf',font_size)
            korean_font = True
        except OSError:
            font = ImageFont.load_default(size=font_size)
            korean_font = False
        if roi is not None:
            drawing.rectangle([ox,oy,ex-1,ey-1],outline='#f59e0b',width=max(2,round(width/500)))
        for row in detections:
            box = row['bbox_xyxy']; label = row['label']; color = COLORS[label]
            drawing.rectangle(box,outline=color,width=max(2,round(width/500)))
            caption = f"{KOREAN[label] if korean_font else label} {row['confidence']*100:.0f}%"
            bounds = drawing.textbbox((0,0),caption,font=font)
            text_width = bounds[2]-bounds[0]+10
            text_height = bounds[3]-bounds[1]+10
            x,y = max(0,min(box[0],width-text_width)), max(0,box[1]-text_height)
            drawing.rectangle([x,y,x+text_width,y+text_height],fill=color)
            drawing.text((x+5,y+5-bounds[1]),caption,font=font,fill='white')
        if preview_width and width>preview_width:
            photo.thumbnail((preview_width,round(preview_width*height/width)))
        encoded = io.BytesIO(); photo.save(encoded,format='JPEG',quality=85 if preview_width else 92)
        return {'schema':'geti-sticker-test-v1', 'source':'live_camera' if observed_at else 'uploaded_image',
            'observed_at':observed_at or datetime.now(timezone.utc).isoformat(), 'roi_normalized':roi,
            'image_size':[width,height], 'coordinate_system':'decoded_image_pixels_xyxy', 'threshold':threshold,
            'model':self.metadata, 'detections':detections, 'count':len(detections),
            'inference_ms':round(inference_ms,2), 'total_ms':round((time.perf_counter()-started)*1000,2),
            'car_id_policy':'null: this model does not recognize digits', 'vision_event_sent':False,
            'annotated_image':'data:image/jpeg;base64,'+base64.b64encode(encoded.getvalue()).decode('ascii')}
