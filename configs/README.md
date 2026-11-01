# 주차장 설정

- `parking_a.example.json`, `parking_b.example.json`: 구조 예시이며 카메라 번호/ROI **미확정**.
- 실제 설정 파일은 `parking_a.json`, `parking_b.json`으로 복사하여 사용하고 Git에 커밋하지 않습니다.
- 카메라 설치 후보는 중앙 상단 또는 모서리 상단 대각선 촬영이며 비교 테스트 후 결정합니다.
- `lot_id`는 주차장(A/B/C) 식별자, `space_id`는 주차장 내부 구역(A-01/B-01 등) 식별자로 각각 관리합니다.
- DB에서는 `(lot_id, space_id)`를 복합 고유키로 취급합니다.
- 주차면 개수·모형 크기는 미니카 실측 후 확정합니다.
