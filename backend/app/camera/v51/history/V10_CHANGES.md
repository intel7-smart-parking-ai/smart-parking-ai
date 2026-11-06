# V9 → V10 수정 내역



- intel_ocr.py: Intel XPU 우선 장치 선택, 양자화하지 않은 EasyOCR 검출/인식 모델 이동, 시작 추론 시험, 실패 이유 기록.

- analysis.py: 새 OCR 생성기로 연결.

- settings.py: xpu 설정 허용.

- pipeline.py: 실제 GPU 이름과 CPU 대체 이유를 화면 상태에 표시. 분석 JSON에도 engine 정보 저장.

- main.py: V10 제목, --check-device 장치 진단, --check-ocr 실제 모델 시작 시험 추가.

- install_intel_gpu.bat: 별도 V10 가상환경에 공식 XPU PyTorch와 의존성 설치.

- requirements.txt: 어댑터와 맞는 EasyOCR 1.7.2 고정.

- tests/test_v10.py: 새 장치 선택/모델 이동/실패 처리 테스트 8개.



총 64개 자동 시험 통과. 실제 Intel B390 추론, 설치 스크립트의 온라인 설치, 속도 향상은 미검증입니다. V9 소스/ZIP은 수정하지 않았습니다. 다음 실제 수정본은 V11입니다.

