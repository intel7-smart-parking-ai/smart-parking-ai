# API 계약 v0.1 (제안 / 11월 2일 팀 합의 후 확정)

- 기본 경로 `/api/v1`
- JSON UTF-8, 필드 `snake_case`, 날짜 ISO 8601(타임존 포함), 금액 원(KRW) 정수
- `car_id`는 `"01"`부터 `"24"`까지 문자열이며 선행 0 유지, 미인식 `null`
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

**위 경로 중 `GET /api/v1/health`만 실행 가능한 예시로 구현되어 있습니다.** 나머지는 계약 초안이며 개발해야 합니다.

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
