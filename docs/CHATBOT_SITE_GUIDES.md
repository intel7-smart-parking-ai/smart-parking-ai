# 로컬 사이트 기반 챗봇 안내 등록

2026-11-08 추가 승인으로 가상 운영 시간·할인·화장실·높이 제한을 등록했습니다. 아래는 초기 화면 기반 등록 기록이며, 최신 적용 정책과 가상 충전 동작은 `VIRTUAL_OPERATING_POLICY.md`를 따릅니다.

확인일: 2026-11-08. 기준 화면: http://127.0.0.1:18380/?app=1&lang=ko

등록 위치: `backend/app/chatbot/parking-chatbot/chatbot/lots.json`의 각 주차장 `guide`.
현재 웹 앱과 가상 도면의 설명이며 실제 부산역·공항·백화점 운영 정책을 인증한 자료가 아닙니다.

| 주차장 | 등록 키 | 근거 |
| --- | --- | --- |
| 개발원 A | entrance | ParkingLayoutMap.jsx의 basic 입·출구 표시 |
| 부산역 B | entrance | ParkingLayoutMap.jsx의 angled 입·출구 표시 |
| 김해공항 C | terminal | airportZones.js와 ParkingLayoutMap.jsx의 airport 도면 |
| 김해공항 C | reservation | MobileParkingApp.jsx의 AirportPanel 연결, AirportPanel.jsx의 localStorage 저장·취소 |
| 김해공항 C | overnight | AirportPanel.jsx의 P1 24시간 이하·P3 24시간 이상 제한 |
| 김해공항 C | shuttle | ParkingLayoutMap.jsx의 청사 순환 셔틀·승하차로 표기. 실제 운행 자료 없음 |
| 신세계 D | elevator, stairs, entrance, amenity | MallParkingMap.jsx의 가상 B1 시설·출입구·영화관 표기 |

등록하지 않은 정보: 네 주차장 공통 실제 운영 시간, 할인·감면, 환불 규정, 화장실 위치, 높이 제한. A·B의 건물 시설, C의 엘리베이터·계단·편의시설, D의 예약·야간 정책도 근거가 없어 비워 둡니다. 빈 항목은 확인 불가로 안내합니다.

중요한 구분:
- P1/P3의 24시간은 시연 예약의 이용 기간 제한이며 운영 시간을 뜻하지 않습니다.
- 공항 예약은 브라우저에만 저장됩니다. 챗봇은 예약 실행·조회·취소를 처리하지 않습니다.
- 공항 예약·장기 주차 안내 답변의 ‘예약하기’ 버튼으로 기존 예약 창을 열 수 있습니다. 구역과 입·출차 시각을 지정한 뒤 ‘예약 확정’을 눌러야 저장되며 버튼 클릭만으로는 저장하지 않습니다.
- 셔틀 표기만으로 실제 운행을 보장하지 않습니다.
- 지도는 화면 기준 상대 위치입니다. 실제 현장 경로·비상 대피 경로로 사용하지 않습니다.
- 시작 화면의 실제 청구 없음, 차량 검색 방식 등 일반 이용 설명은 기존 화면에 있습니다. 시설이나 할인 항목에 섞어 저장하지 않습니다.

확인된 현장 자료가 생기면 해당 guide 문장을 교체하고 출처·확인일을 이 문서에 갱신합니다. 로컬 API 이미지를 다시 빌드하면 적용됩니다.
