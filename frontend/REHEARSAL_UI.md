# 리허설 UI 통합 (D-25~D-42)

- 현재 입차 `status=active`, 별도 출차 이력 필터.
- 점유 `uncertain`은 확인 필요로 표시하고 빈자리에서 제외.
- 주차칸 상세의 입차 시각·마지막 관측·모의 이벤트 출처, 관리자 관측 출처 필터.
- D 64면은 `space_id` 접두어 A/B/C/D에 따라 4구역 배치. 전용 칸 정보는 서버 응답 그대로 사용.
- 레벨 2 미해제 경보는 대피 안내·결제 중단. 웹·모바일 버튼 비활성, 결제 POST 직전 경보 재조회. 경보 조회 실패 시 결제하지 않음. 최종 권한·안전 검사는 서버가 수행.
- 모바일 모의 결제와 영수증, 견적 화면 2분 초기화. 금액은 서버 응답만 표시.
- 모든 관측·입출차·결제 시각은 명시적 Asia/Seoul(KST). 매출 날짜·CSV는 서버의 KST 일자 사용.
- 팀 모드에서도 가상 차량 조회와 서버가 simulation_enabled/capabilities.simulation으로 제공한 C/D 시뮬레이션 사용. 조회 API 부재 시 오류를 표시하며 데모로 전환하지 않음.
- ChatPanel.jsx 변경 없음. 챗봇 연결은 담당자 구현을 유지.

## 실행

```powershell
cd frontend
npm ci
$env:VITE_DATA_SOURCE="team"
$env:API_PROXY_TARGET="http://127.0.0.1:8002"
npm run dev
```

Vite 기본 관리자 프록시 8090, 보관용 데모 프록시 8003 (D-25). 백엔드·Docker 변경은 별도 PR.

## 검증

`npm test`: 47 passed. `VITE_DATA_SOURCE=team npm run build`: 성공.
기존 테스트와 함께 4구역 구역 매칭·uncertain 보존, UTC/KST 동일 표시와 월 경계, 모의 출처, 레벨2 해제 여부·주차장 범위, 결제 직전 경보 생성 시 POST 차단을 검사.
