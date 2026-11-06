# V23 코드 구성과 데이터 흐름



| 파일 | 역할과 이번 검수 포인트 |

|---|---|

| main.py / run_windows.bat | 환경 재사용, 설정 검사, 일반 실행과 --demo 분기 |

| dashboard.py | A/B 선택·카드·사진 자동수집, 빠른/느린/I/O 작업자, 세대 토큰, 수량/검수/복구/내보내기 |

| demo.py / run_demo.bat | A12/B16 합성 예제와 실제 수집 분리 |

| saved_scenes.py | 읽기 전 원본·예측·검수 검증, 주차장별 최신 정상 장면과 실패 안내 |

| camera_hub.py / camera_tools.py | 단일 카메라 프레임, 시리얼·epoch·만료 및 품질 설정 |

| automatic_setup.py | 회전 이름 OCR→원본 좌표→경계 귀속→전칸검증→기준/진단 저장·ORB 재사용 |

| open_boundaries.py | 흰 선분→ㄷ자 세 면→이름 간격 대조→가상 입구→계산 ROI |

| static_markings.py | 등록 바닥 이름의 외관/해시와 현재 영상 대조, 스티커 오인 억제 |

| layouts.py / configs | 주차장/칸 ID·종류, 다각형 검증, 자동 ROI v2/v3 계약 |

| vision19.py | 점유·스티커색 빠른 분석, 번호 enrich, 동일 프레임/구도 검증 |

| analysis.py / sticker_ocr.py | 기존 색/숫자 후보, 스티커 보정과 복수 전처리 일치 |

| contracts.py / prediction_contract.py | D39 운영번호/색 계약, 추론 근거와 타입 검증 |

| records.py / dataset19.py | 원본·manifest·크롭·예측 저장, 지문 검사·집계·복구 |

| collection_output.py | 한글 라벨·ㄷ자 표시사진·bundle, 입력 지문·완료 상태·검증 복사 |

| review.py / widgets.py | 사람 정답·수동 ROI·번호/스티커 영역·저장 충돌 검증 |

| dataset_audit.py | 사람 승인 정답과 예측 비교, 보류/커버리지/손상 자료 구분 |

| transport19.py | 실제 카메라 결과만 전송하는 제한, 만료/취소·재시도 |

| tests/test_v19~v23*.py | 누적 회귀215개. V23 추가는 test_v23와 test_v23_open_u |



카메라/사진 → FramePacket → ROI 전칸 등록/대조 → 점유·색 → 동일 프레임 번호 보강 → persist_scene → 무표시 원본/크롭 + 표시사진/한글 JSON → 실제 파일 집계 → 검증된 내보내기.



main.py에는 예전 2카메라 App 클래스가 보존돼 있으나 일반 실행 진입점은 Dashboard를 생성합니다. 이 구형 클래스의 문구·가상 주차장 정보는 현재 UI가 아닙니다. --analyze-only 역시 구형 analysis 후보 생성용 보조 경로이며, 현재 통합 수집은 일반 실행의 Dashboard 흐름을 사용합니다. 이번 변경에서 구형 기능을 제거하거나 새 자동 수집과 동일하다고 간주하지 않았습니다.



V19/V21로 남은 모듈명·데이터 계약은 제품 버전과 다릅니다. 이전 report는 history, 현재 근거는 TEST_RESULTS_V23·PHYSICAL_BOARD_VALIDATION_V23·PREVIOUS_DATA_REUSE_V23·VALIDATION입니다.

