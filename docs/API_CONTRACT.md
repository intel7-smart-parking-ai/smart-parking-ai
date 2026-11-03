# API 계약 v0.1 (제안 / 11월 2일 팀 합의 후 확정)

- 기본 경로 `/api/v1`
- JSON UTF-8, 필드 `snake_case`, 날짜 ISO 8601(타임존 포함), 금액 원(KRW) 정수
- `car_id`(실물 미니카 스티커)는 `"10"`~`"99"` 두 자리 숫자 문자열, `00`~`09`는 사용하지 않음, 미인식 `null` (2026-11-03 PM 결정)
- 가상 C/D 차량은 화면에 **4자리 숫자**(예: `9012`, 앞자리 0 보존)로 표시하고 상세 조회 시 번호판(예: `56다9012`)을 보여준다 (2026-11-03 PM 결정). 필드명(`vehicle_number`·`plate_number` 제안)과 검색 API는 박해준과 확정 예정
- `lot_id`: 실물 A/B, 가상 C(/D). `space_id`: 구역명을 포함한 `A-01`, `B-01` 등
- API 계약 변경 시 PM 김한별과 박해준 협의

| 메서드 | 경로 | 담당 |
| --- | --- | --- |
| GET | `/api/v1/health` | 박해준 (초기 골격 구현됨) |
| GET | `/api/v1/parking-lots` | 박해준 |
| GET | `/api/v1/parking-lots/{lot_id}/spaces` | 박해준·우지원 |
| GET | `/api/v1/vehicles/{car_id}/location` | 박해준 |
| GET | `/api/v1/sessions` | 박해준 |
| GET | `/api/v1/sessions/{session_id}/fee` | 윤명수·박해준 |
| POST | `/api/v1/mock-payments` | 윤명수 |
| POST | `/api/v1/mock-payments/{id}/cancel` | 윤명수 |
| GET | `/api/v1/reports/revenue` | 윤명수 |
| GET | `/api/v1/alerts` | 박해준·윤명수·서정범 |
| POST | `/api/v1/internal/vision-events` | 박해준(서버) + 각 모델 담당 |

**2026-11-02 로컬 작업 브랜치 기준:** 기존 `GET /api/v1/health`를 보존하고, 주차장 목록·주차칸 목록·차량 위치 조회 3개를 DB 없는 임시 목업으로 구현했습니다. main 반영·최종 계약 승인·실제 DB 연동은 아직 아닙니다. 합성 데이터와 임시 응답 형식은 [MOCK_API.md](MOCK_API.md)를 참고하세요. 그 외 API는 미구현 초안입니다.

```json
{
  "lot_id": "A",
  "camera_id": "camera_a",
  "space_id": "A-03",
  "status": "occupied",
  "car_id": "12",
  "confidence": 0.94,
  "source": "vision",
  "observed_at": "2026-11-02T10:00:00+09:00"
}
```
