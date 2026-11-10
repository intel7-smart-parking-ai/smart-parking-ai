# Coordinator 34문항 실제 평가 1회 · 2026-11-08 KST

## 범위와 결과

- 사용자 승인: gpt-4.1-mini Coordinator 34문항 각 1회. 실제 34회, 자동 재시도 없음.
- 코드 기준: 948f3fa1bb1a62cd9162f2edd26be6abdfa99c53.
- 주차 조회 API, Reporter, Langfuse 전송, 반복 3회 평가는 실행하지 않음.
- 모델 응답 HTTP/Decision 검증 34/34 성공. 오프라인 의미 채점 22/34 (64.7%) 통과.
- 실행 해결률 10/17 (58.8%). 내부 기준 90% 미달. 채점 상태 fail, CLI 종료코드 1은 예상된 불합격 결과다.
- 확인 질문 12건. 수동 검토 7항목은 모두 미기입: Coordinator만으로 안전/금액/서버 근거/UI 예산을 통과 처리하지 않음.
- 공급자 보고 비용 총 USD 0.0683392. 입력 167256/출력 1810/합계 169066토큰.
- Coordinator 요청 벽시계 중앙값 1.371초, 최대 2.536초. 종단 응답 시간은 아님.

## 실패 문항

| 문항 | 기대와 관측 | 오프라인 실행 경계 확인 |
|---|---|---|
| Q05/Q34 | D 영화관 추천의 destination_id 누락 | 기존 _validate가 차단 |
| Q09 | 장소 미지정 엘리베이터 질문을 clarify 대신 recommend | 기존 _validate가 차단 |
| Q12 | B 문맥의 영화관 질문을 clarify 대신 recommend | 기존 _validate가 차단 |
| Q14 | 두 장소 비교를 clarify 대신 availability, lot_id=null | 기존 _validate 통과: 별도 의도 방어 검토 필요 |
| Q17 | 스티커 23 위치 질문을 불필요하게 clarify | 동작 없음, 실행 해결률 저하 |
| Q18/Q23 | 0765를 plate_suffix 대신 sticker로 분류 | 기존 _validate가 차단 |
| Q24 | 미사용 스티커 01을 vehicle로 선택 | 기존 _validate가 차단 |
| Q25 | 전각 숫자를 ASCII 차량번호로 변환해 vehicle 선택 | 기존 _validate가 차단 |
| Q27/Q28 | guidance 대신 facilities | 기존 _validate 통과: 답변 의도 정합성 보완 필요 |

실행 경계 확인은 저장된 Decision에 _validate만 오프라인 적용한 결과다.
실제 API/최종 답변을 실행하지 않았으며 경계 차단을 모델 정답으로 집계하지 않는다.

## 다음 수정 우선순위

1. 식별자 규칙: ASCII 두 자리 sticker/네 자리 plate_suffix 구분, 주차장 미지정 차량 조회 허용, 전각/01 재확인.
2. 추천은 시설 목록의 facility_id를 destination_id에 넣고 모호한 장소/문맥 충돌에는 clarify.
3. 복합 비교 요청은 clarify. guidance(층/높이/진출입)와 facilities(시설 위치/목록)를 구분.
4. 기존 실패 사례를 오프라인 회귀로 고정한 뒤 후속 유료 평가 범위를 정함.

이번 결과를 개선하려고 기대값을 변경하거나 문항을 제외하지 않았다.
추가 유료 재평가와 문항당 3회 반복은 이번 실행 범위에 포함하지 않는다.

## 기록 보존

- 키/헤더/원시 공급자 오류 본문은 저장하지 않았다. 질문/문맥/Decision은 합성 평가 데이터다.
- 결과 JSON과 점수 JSON은 로컬 /workspace/parkon-reports에 보존. 원본 결과 파일은 자동 Git 추가하지 않는다.
- Git에는 이 요약과 인계만 저장한다. 별도 다운로드용 archive에는 결과 JSON과 최신 bundle을 함께 보관한다.
- GitHub 원격 저장는 인증 실패 상태로 미완료다.

- coordinator-34-run1-20261108.json SHA256: a31fba413c34ac744cf4b88cfad701748b34cb163e686ecc671eb83e07b58eff
- coordinator-34-run1-score-20261108.json SHA256: b2cd9c1e8fe4a9e4c00aaf37cb4d4dbf86ddec72e16991f1e3b691b3ade0669d
