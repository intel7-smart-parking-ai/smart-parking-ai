"""Train the optional V53 indicator classifier from attributed LED photographs."""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from web_live.fire_led import features, LensModel


# Reserve the Toshiba part and the 2009 unplugged part as separate test sources.
SOURCES = [('5_mm_Red_LED_(on).jpg', 1, 'train'), ('5_mm_Red_LED.jpg', 0, 'train'),
           ('5_mm_Tinted_Red_LED_(on).jpg', 1, 'train'), ('5_mm_Tinted_Red_LED.jpg', 0, 'train'),
           ('Stanley_BR5064X_Illuminated_at_20mA..jpg', 1, 'train'),
           ('TLUR123_3mm_Illuminated_at_20mA.jpg', 1, 'test'), ('5mm_Red_LED.jpg', 0, 'test')]


def lens_image(path):
    image = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    h,w = image.shape[:2]
    thumb = cv2.resize(image, (round(w*300/max(h,w)), round(h*300/max(h,w))), interpolation=cv2.INTER_AREA)
    b,g,r = cv2.split(thumb.astype(np.int16))
    mask = np.uint8((r-np.maximum(b,g) > 42) & (r >= 155))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    x,y,w,h = cv2.boundingRect(max(contours,key=cv2.contourArea))
    return thumb[y:y+h,x:x+w]


def train(folder, output):
    X, y, records = [], [], []
    for name, label, split in SOURCES:
        path = folder/name
        lens = lens_image(path)
        records.append(dict(file=name, label=label, split=split,
                            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            author='Antony Evans (Wizzle Labs)' if '20mA' in name else 'oomlout',
                            license='CC BY-SA 4.0' if '20mA' in name else 'CC BY-SA 2.0',
                            source='https://commons.wikimedia.org/wiki/File:'+name))
        if split != 'train':
            continue
        for size in (5, 7, 9, 13, 18, 24):
            patch = cv2.resize(lens,(size,size), interpolation=cv2.INTER_AREA).astype(np.float32)
            for gain in (.82,.94,1,1.08,1.15):
                for offset in (-7, 0, 8):
                    X.append(features(np.clip(patch*gain+offset,0,255).astype('uint8')))
                    y.append(label)
    X, y = np.array(X), np.array(y)
    center, scale = X.mean(0), np.maximum(X.std(0), .035)
    data = (X-center)/scale
    sample_weight=np.where(y==1, .5/np.mean(y==1), .5/np.mean(y==0))
    weights, bias = np.zeros(data.shape[1]), 0.
    for _ in range(1600):
        error = (1/(1+np.exp(-np.clip(data@weights+bias,-40,40))) - y)*sample_weight
        weights -= .1*(data.T@error/len(y) + .013*weights)
        bias -= .1*error.mean()
    output.parent.mkdir(parents=True,exist_ok=True)
    np.savez(output, weights=weights, bias=bias, center=center, scale=scale, version=1)
    model = LensModel.load(output)
    for entry in records:
        patch = cv2.resize(lens_image(folder/entry['file']), (12,12), interpolation=cv2.INTER_AREA)
        entry['score_12px'] = round(model.score(patch),5)
        entry['correct_at_080'] = int(entry['score_12px'] >= .80) == entry['label']
    return dict(augmentation_count=len(y), records=records)


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('photos', type=Path)
    p.add_argument('output', type=Path)
    args=p.parse_args()
    print(json.dumps(train(args.photos,args.output),indent=2))
