# V20 코드 안내



| 파일 | 역할과 V20 변경 |

|---|---|

| main.py | 실제 시작점, V20 설정 검사. 과거 App 클래스는 기본 실행되지 않음 |

| dashboard.py | 통합 UI, 점유/번호/I/O Worker, 결과 세대·장면 그룹·내보내기 범위·취소 연결 |

| vision19.py | 점유 A안/B안 연결, 독립 색 후보, 내부 흰 윤곽, OCR enrich, 모델 캐시 |

| prediction_contract.py | 예측 형식·장면·점수·영역·종류·번호 규칙 검사 |

| dataset19.py | 원본/자산/예측 저장, 유실 검사, 촬영 시각 정렬, 작업별 크롭 가능 수, 범위 export |

| transport19.py | source_kind 제한, 전송 세대 취소, 재시도/만료·최신 큐 |

| camera_hub.py | 장치 프레임과 camera/video 출처 전달 |

| pipeline.py | 원본 저장, 초기 asset pending 및 입력 출처 기록 |

| review.py | 사람 검수·수정 이력·범위별 원본/표시사진 export |

| records.py, export_images.py | 원본 검증과 표시사진 작성, 기존 동작 유지 |

| analysis.py, intel_ocr.py | 기존 OCR·모델 로딩, V20에서는 상위 처리 흐름 분리 |

| layouts.py, contracts.py, settings.py | A12/B16·D39 데이터 계약·설정, 규칙 변경 없음 |

| run_tests.py, tests/test_v19.py, tests/test_v20.py | 총 83개 회귀, 실제 HTTP/카메라 없음 |

| history/ | 과거 버전 문서·시험, 현재 통과 수에 합산하지 않음 |



`vision19`, `dataset19`, `transport19` 모듈명은 import 호환을 위해 유지한다. 프로그램 폴더, 제목, README, VALIDATION, ZIP의 제품 버전은 V20이다. 실행 환경·모델·촬영 데이터는 소스 배포에서 제외한다.

