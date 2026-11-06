# V19 코드 안내



- `main.py`: 시작점·설정 검사. `main()`은 새 `Dashboard`를 실행합니다. 기존 `App` 클래스는 이전 버전 참고용 호환 코드이며 새 UI에서 호출하지 않습니다.

- `dashboard.py`: 단일 화면, 프레임 고정, 검수, 작업 큐, 카메라 전환·종료.

- `vision19.py`: 사선 마스킹, 빈 기준 차영상, B안 분류 연결, OCR/스티커 후보, 위치 변화 후보.

- `dataset19.py`: 자산 저장, 예측 이력, 파일 대조 집계, 승인 자료 작업별 내보내기.

- `transport19.py`: 선택형 팀 API 송신. 기본 꺼짐, 최신 큐/재시도/만료 처리.

- `camera_hub.py`, `camera_tools.py`, `quality.py`: 장치 수신·설정·참고 품질 지표.

- `review.py`, `records.py`, `export_images.py`: 사람 정답·원본 무결성·표시사진 내보내기.

- `analysis.py`, `intel_ocr.py`: OCR 및 스티커 내부색 분석.

- `layouts.py`, `contracts.py`, `settings.py`: 주차칸 규칙·D-39 계약·설정 검증.

- `run_tests.py`, `tests/test_v19.py`: V19 전용 회귀시험. 실제 카메라·실제 HTTP 요청 없음.

- `history/`: 이전 버전 문서/검증/시험 보존본. V19의 현재 검증을 의미하지 않음.

