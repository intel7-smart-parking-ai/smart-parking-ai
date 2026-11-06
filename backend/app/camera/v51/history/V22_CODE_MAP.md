# V22 코드 안내



| 파일 | 역할·V22 확인 사항 |

|---|---|

| main.py / run_windows.bat | 실제 기본 GUI는 Dashboard, 기존 환경 재사용, 설정 검사 |

| dashboard.py | 자동 상태/작업 큐, 저장 복구 버튼, 내보내기 직렬화, 입력 변경 취소, 통합 화면 |

| automatic_setup.py / layouts.py | 칸 이름/닫힌 사각형, A/B 카탈로그, 자동 anchor 대조·ROI 검증 |

| camera_hub.py / camera_tools.py | 카메라 캐시·프레임 복사·epoch·설정·출처, V22 모의시험 추가 |

| vision19.py / sticker_ocr.py | 점유 근거·독립 색·투시 보정·OCR 합의·보류, V21 코드 유지 |

| prediction_contract.py / contracts.py | 예측/좌표/근거/현행 D39 계약, V21 코드 유지 |

| dataset19.py | pending→최종 완료 저장, 원본 보존, rebuild_scene_assets, 디스크 집계·승인 학습 export |

| collection_output.py | 칸별 표시사진·한글 JSON, 입력 지문, korean-collection-v2, 검증 복사 |

| pipeline.py / records.py / parking_ai/storage.py | 원본·manifest, 초기 상태, 완료 상태 값 검증, 원자 파일 교체 |

| review.py | 승인 정답·수정 이력·번호 정규화, 갱신 전 pending 및 후속 사진 생성 |

| quality.py / dataset_audit.py | 수치 화질, 승인 정답 대비 일치/보류·커버리지 보고서 |

| transport19.py | 출처/만료/취소/재시도, 실제 서버는 미검증 |

| analysis.py / intel_ocr.py | 기존 모델 로딩·장치 선택 및 과거 분석 경로 |

| tests/test_v19.py / test_v20.py / test_v21.py / test_v21_auto.py | 기존 152개 회귀 |

| tests/test_v22.py | 저장17/UI9/카메라모의4 = 새30개, 합계182개 |

| history/ | 이전 문서·시험 기록, 이번 측정값과 구별 |



vision19/dataset19/transport19 모듈명과 prediction-v21-1 규격명은 제품 버전과 별개입니다. 제품은 V22입니다. main.py의 이전 App와 분석 CLI는 유지되어 있으나 기본 GUI가 사용하는 Dashboard 경로와 구분합니다.



작업 폴더 verification_v22에는 문제 재현, 파일 기록 중단 시험, 합성 UI 준비, 문서/패키지 생성 스크립트와 V21→V22 diff가 있습니다. 시험 데이터·환경은 배포 ZIP에 포함하지 않습니다.

