"""Metadata-specific color conversion; raw multi-label scores are not softmax."""
import json
import threading
import xml.etree.ElementTree as ET
import cv2
import numpy as np
from model_api.models import Model


class Classifier:
    def __init__(self, path, expected):
        info = {n.tag: n.get('value') for n in ET.parse(path).findall('./rt_info/model_info/*')}
        self.labels = json.loads(info['label_info'])['label_names']
        if info.get('task_type') != 'classification' or len(self.labels)!=len(expected) or set(self.labels) != set(expected):
            raise ValueError('분류 모델 라벨과 작업 종류가 설정과 다릅니다.')
        self.reverse = info.get('reverse_input_channels') == 'True'
        self.model = Model.create_model(str(path), device='CPU', nthreads=2)
        self.lock = threading.Lock()

    def scores(self, bgr):
        pixels = bgr if self.reverse else cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        with self.lock:
            values = np.asarray(self.model(pixels).raw_scores).reshape(-1).copy()
        if len(values) != len(self.labels) or not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
            raise ValueError('분류 모델 출력 범위 오류')
        return dict(zip(self.labels, map(float, values)))


NUMBERS = ('11', '28', '34', '49', '77', '87')
STICKER_LABELS = ['color_'+c for c in ('blue', 'green', 'white')] + ['number_'+n for n in NUMBERS]


def unique_choice(scores, threshold=.8, competing=.2):
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked or ranked[0][1] < threshold or len(ranked)>1 and ranked[1][1] > competing:
        return None
    return ranked[0][0]
