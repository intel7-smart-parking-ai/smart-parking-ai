# V21 코드 안내



| 파일 | 역할 |

|---|---|

| main.py / run_windows.bat | 통합 Dashboard 시작, 설정 검사, 기존 실행 환경 재사용 |

| dashboard.py | 원클릭 자동 상태·작업 큐·통합 화면·범위별 집계/내보내기·중단 |

| automatic_setup.py | 칸 이름/선 사각형 등록, 전칸/귀속 검사, anchor 저장/대조 |

| layouts.py | A/B 카탈로그·사각형 검사·수동/자동 ROI 근거 검증 |

| vision19.py | 점유/독립 색 후보, 원형 지원, 스티커 근거, OCR 장애/중복 처리 |

| sticker_ocr.py | 스티커 투시 보정·확대·3개 처리 영상·회전별 OCR·합의·좌표 복원 |

| prediction_contract.py | 원문/근거/박스/점수/번호/칸 귀속·장면 규약 |

| collection_output.py | 칸명 표시사진·한글 JSON·전체 사진/결과·bundle·해시·복사 export |

| dataset19.py | 장면 저장·파일 해시/짝/정보완성 집계·승인 학습 export |

| dataset_audit.py | 범위별 승인 정답 비교·보류·커버리지·Markdown/JSON 보고서 |

| pipeline.py / records.py | 원본·촬영 기록·pending 표식·시각/epoch/해시 검증 |

| review.py | 사람 정답/수정 이력, 표시사진/한글 JSON 갱신 |

| quality.py / camera_tools.py | 수치 화질 지표, 센서 설정/실제값 조회 |

| camera_hub.py | 카메라 프레임 수신·연결 구간·카메라/녹화 출처 |

| transport19.py | 전송 출처/만료/취소/재시도, 실제 서버는 검증하지 않음 |

| analysis.py / intel_ocr.py | 기존 로컬 OCR 모델 로딩·장치 선택 |

| contracts.py / settings.py | D39 계약 및 설정 범위 검증 |

| tests/test_v19.py / test_v20.py / test_v21.py / test_v21_auto.py | 52+31+33+36=152개 회귀 |

| history/ | 과거 문서와 시험, 현재 시험 수로 중복 합산하지 않음 |



vision19/dataset19/transport19라는 모듈명은 import 호환 때문에 유지했습니다. 제품 버전은 V21입니다. 파일명만 바꾼 배포가 아니며 실제 차이는 verification_v21/CODE_CHANGES_V20_TO_V21.diff에서 확인합니다.

