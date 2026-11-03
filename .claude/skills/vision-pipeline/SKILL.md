---
name: vision-pipeline
description: "미니카 차량 탐지·번호 스티커(두 자리 10~99) OCR·빨간 LED 모의 화재 인식의 데이터 수집→선별→라벨링→분할→학습→평가→어댑터 연동 가이드. capture_dataset.py 사용법, 모델별 수량 목표, 누수 없는 분할, 평가 리포트 형식, 공통 이벤트 변환 규칙을 제공한다. 데이터셋 수집, 라벨링, 학습, 성능 평가, 오탐 분석, 추론 모듈 작성 시 사용한다."
---

# Vision Pipeline

원문: `AI데이터셋_수집계획.md`. vision-engineer가 주로 사용하며 camera-roi-engineer가 참고한다.

## 1. 수집

도구: `smart_parking_dataset_capture/capture_dataset.py` (이미지 저장 전용, 자동 라벨링 아님)

```powershell
python smart_parking_dataset_capture/capture_dataset.py --source A:webcam:0 --mode vehicle --interval 2 --output "D:\project\parking_dataset"
python smart_parking_dataset_capture/capture_dataset.py --source A:realsense --source B:webcam:1 --mode led_on --interval 2 --min-change 0 --output "D:\project\parking_dataset"
```
- 모드: `vehicle`, `ocr`, `led_on`, `led_off`, `background` / 키: `s` 저장, `p` 일시정지, `q` 종료
- 저장 경로는 **저장소 밖**. Git에는 코드·설정·문서만
- 촬영 조건(카메라 위치, 높이, 조명, 배치, 세션 ID)을 CSV/메모로 기록

## 2. 목표 수량

| 모델 | 1차 | 최종 권장 | 단위 | 라벨 |
| --- | --- | --- | --- | --- |
| 차량 탐지 | 300~500 | 800~1,200 | 전체 프레임 | bbox (`car`) |
| 번호 OCR | 240~480 (번호당 10~20) | 600~1,000 (번호당 25~40) | 숫자 크롭 | 배정 두 자리 번호 |
| LED | 300~500 | 600~1,000 | 장면 프레임 | 위치 + on/off |

필수 조건: 차량 1~5대/6대+/방향 변경/빈 주차장·그림자·가림 · OCR 번호 균형 · LED 켜짐/꺼짐/**빨간 미니카·반사광 오탐 케이스**.
같은 프레임 반복 수집 금지 — 실패 장면 위주로 보강.

## 3. 라벨링·분할

- 도구 예: Label Studio, CVAT, Roboflow (YOLO txt 포맷 export)
- **세션 단위 분할** (train/val/test = 70/15/15): 같은 촬영 세션·배치가 여러 분할에 섞이지 않게 → 평가 누수 방지
- test 세트는 고정하고 버전명 부여 (`vehicle_test_v1`). 개선 전후는 같은 test로 비교
- 최종 카메라 위치가 확정되면 그 위치 데이터로 반드시 재검증

## 4. 모델별 권장 접근

| 모델 | 1단계 (빠른 기준선) | 2단계 (부족 시) |
| --- | --- | --- |
| 차량 | YOLO n/s 사전학습 파인튜닝, imgsz 640, 단일 클래스 | 해상도↑, 가림 데이터 보강 |
| 번호 | 차량 크롭 → 스티커 영역 → 24클래스 분류(CNN 소형) 또는 OCR + 배정 번호 화이트리스트(두 자리, `00`~`09` 제외) | 고대비 라벨 / ArUco 마커 전환 (PM 결정) |
| LED | HSV 빨강 범위 + 고휘도 + 면적·원형도 필터, N프레임 연속 확인 | 후보 영역 소형 분류기 |

## 5. 평가 리포트 `_workspace/vision_<model>_eval.md`

```
- 데이터셋 버전 / 분할 기준 / 이미지 수(train/val/test)
- 모델·하이퍼파라미터 / 가중치 저장 위치 + SHA256
- 지표: 차량 mAP50·precision·recall / 번호 accuracy·null 비율·오인식률 / LED precision·recall·오탐 사례 수
- 추론 속도 (ms/frame, 장비)
- 실패 사례 Top 5 (이미지 경로 + 원인 추정) → 다음 보강 조건
```
목표 수치는 11/06 중간 점검에서 실제 데이터로 합의한다. 그 전엔 수치 기록이 목표.

## 6. 추론 모듈·어댑터

```python
# backend/app/vision/<model>/infer.py
class Detector:
    def __init__(self, weights_path: str, conf: float = 0.5): ...
    def predict(self, frame) -> list[dict]:  # 원시 관측: bbox_xyxy, confidence, (car_id | led_on)
        ...

# backend/app/vision/<model>/adapter.py
def to_vision_events(raw, lot_id, camera_id, observed_at, frame_id) -> list[dict]:
    """api-contract의 vision-event 형식으로 변환. space_id는 roi 매칭 후 채운다."""
```
- 입력 프레임은 Frame Hub 또는 이미지 파일. **카메라 직접 열기 금지**
- 불확실 OCR → `car_id=None`, confidence 함께 전달
- 가중치 경로는 `.env`/`configs`로 주입 (하드코딩 금지)
- 테스트: 샘플 이미지 1~3장으로 출력 스키마·타입 검증 (가중치 없으면 skip)
