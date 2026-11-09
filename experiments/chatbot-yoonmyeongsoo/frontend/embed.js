// @ts-nocheck
/**
 * PARK:ON 주차 챗봇 원클릭 임베드 스크립트 (embed.js)
 * 
 * <시연 사이트 주소> 페이지에
 * <script src="https://.../embed.js"></script> 한 줄로 챗봇 기능을 삽입할 수 있습니다.
 */
(function() {
  if (window.__PARKON_CHATBOT_INITIALIZED__) return;
  window.__PARKON_CHATBOT_INITIALIZED__ = true;

  // 1. 스타일 동적 주입
  const style = document.createElement("style");
  style.textContent = `
    #parkon-floating-btn {
      position: fixed;
      bottom: 24px;
      right: 24px;
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: linear-gradient(135deg, #2563eb, #7c3aed);
      color: #ffffff;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 26px;
      cursor: pointer;
      box-shadow: 0 4px 16px rgba(37, 99, 235, 0.4);
      z-index: 999999;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    #parkon-floating-btn:hover {
      transform: scale(1.08);
      box-shadow: 0 6px 20px rgba(37, 99, 235, 0.6);
    }
    #parkon-chat-modal {
      position: fixed;
      bottom: 92px;
      right: 24px;
      width: 420px;
      height: 620px;
      max-width: calc(100vw - 32px);
      max-height: calc(100vh - 120px);
      background: #ffffff;
      border-radius: 20px;
      box-shadow: 0 12px 36px rgba(0,0,0,0.2);
      border: 1px solid #e2e8f0;
      z-index: 999998;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, sans-serif;
      animation: parkonPop 0.25s cubic-bezier(0.16, 1, 0.3, 1);
    }
    @keyframes parkonPop {
      from { opacity: 0; transform: translateY(20px) scale(0.95); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }
    .parkon-modal-header {
      background: #1e293b;
      color: #ffffff;
      padding: 14px 18px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: 700;
      font-size: 15px;
    }
    .parkon-modal-close {
      background: transparent;
      border: none;
      color: #94a3b8;
      font-size: 20px;
      cursor: pointer;
      padding: 0 4px;
    }
    .parkon-modal-close:hover { color: #ffffff; }
    .parkon-chat-body {
      flex: 1;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      background: #f8fafc;
    }
    .parkon-msgs {
      flex: 1;
      padding: 16px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .parkon-msg-row {
      display: flex;
      gap: 8px;
      max-width: 88%;
      font-size: 13px;
      line-height: 1.5;
    }
    .parkon-msg-row.user {
      align-self: flex-end;
      flex-direction: row-reverse;
    }
    .parkon-msg-bubble {
      padding: 10px 14px;
      border-radius: 14px;
      word-break: break-word;
      white-space: pre-line;
    }
    .parkon-msg-row.bot .parkon-msg-bubble {
      background: #ffffff;
      color: #1e293b;
      border: 1px solid #cbd5e1;
      border-top-left-radius: 2px;
    }
    .parkon-msg-row.user .parkon-msg-bubble {
      background: #2563eb;
      color: #ffffff;
      border-top-right-radius: 2px;
    }
    .parkon-chips {
      padding: 8px 12px;
      background: #ffffff;
      border-top: 1px solid #e2e8f0;
      display: flex;
      gap: 6px;
      overflow-x: auto;
      white-space: nowrap;
    }
    .parkon-chip {
      padding: 5px 12px;
      background: #f1f5f9;
      border: 1px solid #cbd5e1;
      border-radius: 16px;
      font-size: 12px;
      font-weight: 600;
      color: #334155;
      cursor: pointer;
      flex-shrink: 0;
    }
    .parkon-chip:hover {
      background: #eff6ff;
      border-color: #93c5fd;
      color: #1d4ed8;
    }
    .parkon-input-bar {
      padding: 12px;
      background: #ffffff;
      border-top: 1px solid #e2e8f0;
      display: flex;
      gap: 8px;
    }
    .parkon-input {
      flex: 1;
      padding: 10px 14px;
      border: 1px solid #cbd5e1;
      border-radius: 20px;
      font-size: 13px;
      outline: none;
    }
    .parkon-input:focus { border-color: #2563eb; }
    .parkon-send-btn {
      padding: 8px 16px;
      background: #2563eb;
      color: #ffffff;
      border: none;
      border-radius: 20px;
      font-weight: 700;
      font-size: 13px;
      cursor: pointer;
    }
    .parkon-card {
      margin-top: 8px;
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-radius: 10px;
      padding: 10px 12px;
      font-size: 12px;
      max-width: 440px;
      box-sizing: border-box;
    }
  `;
  document.head.appendChild(style);

  // 2. DOM 요소 생성
  const btn = document.createElement("div");
  btn.id = "parkon-floating-btn";
  btn.innerHTML = "🤖";
  btn.title = "PARK:ON 주차 챗봇 열기";
  document.body.appendChild(btn);

  const modal = document.createElement("div");
  modal.id = "parkon-chat-modal";
  modal.style.display = "none";
  modal.innerHTML = `
    <div class="parkon-modal-header">
      <span>PARK:ON 스마트 주차 챗봇</span>
      <div style="display:flex; align-items:center; gap:6px;">
        <button id="parkon-fire-btn" style="background:#fee2e2; color:#dc2626; border:1px solid #f87171; border-radius:12px; font-size:11px; font-weight:700; padding:2px 8px; cursor:pointer;" title="화재 대피 시연">🚨 화재 경보</button>
        <button id="parkon-clear-fire-btn" style="background:#ecfdf5; color:#059669; border:1px solid #10b981; border-radius:12px; font-size:11px; font-weight:700; padding:2px 8px; cursor:pointer;" title="화재 상황 해결 및 경보 해제">🧯 경보 해제</button>
        <button class="parkon-modal-close" id="parkon-close-btn">✕</button>
      </div>
    </div>
    <div class="parkon-chat-body">
      <div class="parkon-msgs" id="parkon-msgs"></div>
      <div class="parkon-chips" id="parkon-chips"></div>
      <div class="parkon-input-bar">
        <input type="text" class="parkon-input" id="parkon-input" placeholder="질문을 입력하세요 (예: 23번 차 요금)" />
        <button class="parkon-send-btn" id="parkon-send-btn">전송</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  // 세션 상태
  let sessionId = localStorage.getItem("parkon_embed_session") || "embed_" + Math.random().toString(36).substr(2, 8);
  localStorage.setItem("parkon_embed_session", sessionId);

  const msgsContainer = modal.querySelector("#parkon-msgs");
  const chipsContainer = modal.querySelector("#parkon-chips");
  const inputElem = modal.querySelector("#parkon-input");
  const sendBtn = modal.querySelector("#parkon-send-btn");
  const closeBtn = modal.querySelector("#parkon-close-btn");
  const fireBtn = modal.querySelector("#parkon-fire-btn");
  const clearFireBtn = modal.querySelector("#parkon-clear-fire-btn");

  if (fireBtn) {
    fireBtn.addEventListener("click", () => {
      sendMessage("🚨 [화재 경보 발령] 비상 대피요령 및 화재 위치 안내", "evacuate");
    });
  }
  if (clearFireBtn) {
    clearFireBtn.addEventListener("click", () => {
      sendMessage("🧯 [상황 종료] 화재 진압 완료 및 안전 경보 해제 요청", "fire_clear");
    });
  }

  function toggleModal() {
    const isHidden = modal.style.display === "none";
    modal.style.display = isHidden ? "flex" : "none";
    if (isHidden && msgsContainer.children.length === 0) {
      sendMessage("시작");
    }
  }

  btn.addEventListener("click", toggleModal);
  closeBtn.addEventListener("click", toggleModal);

  // 상단 탭 중 '주차 챗봇' 버튼이 있다면 자동 연결
  setTimeout(() => {
    document.querySelectorAll("button, a, .tab").forEach(el => {
      if (el.innerText && el.innerText.includes("주차 챗봇")) {
        el.addEventListener("click", (e) => {
          e.preventDefault();
          modal.style.display = "flex";
          if (msgsContainer.children.length === 0) sendMessage("시작");
        });
      }
    });
  }, 1000);

  // 첨부 사진 4종 질문 대응 스마트 응답기
  function checkSpecialQueries(query) {
    if (!query) return null;
    const q = query.toLowerCase();

    // 0. 최초 진입 및 인사말
    if (q === "시작" || q === "start" || q === "처음" || q.includes("안녕") || q.includes("하이") || q.includes("반가워")) {
      return {
        text: "안녕하세요! 스마트 주차 도우미 **PARK:ON 챗봇**입니다. 🚗✨\n\n실시간 빈자리 확인부터 내 차 위치 찾기, 주차 요금 조회 및 사전 정산, 전기차 충전 현황까지 편리하게 도와드릴게요!\n\n궁금하신 점을 편하게 말씀해 주시거나 아래 추천 버튼을 눌러보세요.",
        chips: [
          { id: "empty_all", label: "🅿️ 전 주차장 빈자리 확인" },
          { id: "ev_status", label: "⚡ 전기차 충전 현황" },
          { id: "find_car", label: "🚗 내 차 어디 있어?" },
          { id: "fee_rule", label: "💳 주차 요금 안내" },
          { id: "call_admin", label: "📞 관리자 호출" }
        ]
      };
    }

    // 1. 전기차 충전 현황 (몇 퍼센트 충전 중인지, 충전시간이 얼마나 남았는지)
    if (q.includes("충전") && (q.includes("현황") || q.includes("상태") || q.includes("시간") || q.includes("얼마") || q.includes("퍼센트") || q.includes("남은") || q.includes("완충"))) {
      const mockChargers = [
        { charger_id: "EV-A05", lot_id: "A", lot_name: "개발원", space_id: "A-05", charger_type: "급속 100kW", status: "charging", plate_number: "12가 3423", car_id: "23", battery_percent: 78, remaining_minutes: 14, current_speed_kw: 84.5 },
        { charger_id: "EV-A06", lot_id: "A", lot_name: "개발원", space_id: "A-06", charger_type: "완속 7kW", status: "available", plate_number: null, car_id: null, battery_percent: 0, remaining_minutes: 0, current_speed_kw: 0 },
        { charger_id: "EV-B07", lot_id: "B", lot_name: "부산역", space_id: "A-07", charger_type: "급속 100kW", status: "charging", plate_number: "78라 9058", car_id: "58", battery_percent: 92, remaining_minutes: 6, current_speed_kw: 25.0 },
        { charger_id: "EV-B08", lot_id: "B", lot_name: "부산역", space_id: "A-08", charger_type: "완속 7kW", status: "available", plate_number: null, car_id: null, battery_percent: 0, remaining_minutes: 0, current_speed_kw: 0 },
        { charger_id: "EV-C11", lot_id: "C", lot_name: "김해공항", space_id: "A-11", charger_type: "급속 100kW", status: "charging", plate_number: "89마 5757", car_id: "57", battery_percent: 45, remaining_minutes: 28, current_speed_kw: 92.0 },
        { charger_id: "EV-C31", lot_id: "C", lot_name: "김해공항", space_id: "B-31", charger_type: "완속 7kW", status: "completed", plate_number: "42오 4242", car_id: "42", battery_percent: 100, remaining_minutes: 0, current_speed_kw: 0 },
        { charger_id: "EV-D05", lot_id: "D", lot_name: "신세계", space_id: "C-05", charger_type: "완속 7kW", status: "charging", plate_number: "11구 1111", car_id: "11", battery_percent: 70, remaining_minutes: 45, current_speed_kw: 6.8 },
        { charger_id: "EV-D06", lot_id: "D", lot_name: "신세계", space_id: "C-06", charger_type: "완속 7kW", status: "available", plate_number: null, car_id: null, battery_percent: 0, remaining_minutes: 0, current_speed_kw: 0 }
      ];
      return {
        text: "**전기차 충전 구역 실시간 충전 현황** ⚡\n\n• **부산역 A-07**: 78라 9058 — **92% 충전 중 (약 6분 남음)**\n• **개발원 A-05**: 12가 3423 — **78% 충전 중 (약 14분 남음)**\n• **김해공항 A-11**: 89마 5757 — **45% 충전 중 (약 28분 남음)**\n• **신세계 C-05**: 11구 1111 — **70% 충전 중 (약 45분 남음)**\n• **김해공항 B-31**: 42오 4242 — **100% 완충 완료** (출차 대기)\n\n※ 나머지 구역은 즉시 충전 가능한 빈자리입니다. 아래 카드에서 실시간 배터리와 잔여시간을 확인하세요.",
        card: {
          type: "ev_charging",
          data: { chargers: mockChargers }
        },
        chips: [
          { id: "view_map:B", label: "🗺️ 부산역 A-07 도면" },
          { id: "view_map:A", label: "🗺️ 개발원 A-05 도면" },
          { id: "empty_all", label: "전체 빈자리 현황" }
        ]
      };
    }

    // 2. 전 주차장 빈자리 확인 (4개 주차장 전체 현황)
    if (q.includes("전 주차장") || q.includes("전체 주차장") || q.includes("모든 주차장") || (q.includes("전체") && q.includes("빈자리"))) {
      return {
        text: "**전체 4개 주차장 실시간 빈자리 현황** (총 122면 여유) 🅿️\n\n• **개발원 주차장**: 빈자리 **10면** (일반 7 · ⚡전기 2 · ♿장애인 1)\n• **부산역 주차장**: 빈자리 **14면** (일반 10 · ⚡전기 2 · ♿장애인 2)\n• **김해공항 주차장**: 빈자리 **52면** (일반 34 · ⚡전기 4 · ♿장애인 14)\n• **신세계백화점 주차장**: 빈자리 **46면** (일반 35 · ⚡전기 5 · ♿장애인 6)\n\n원하시는 주차장의 **[🗺️ 도면 확인]** 버튼을 누르시면 해당 주차장 도면으로 즉시 이동합니다.",
        chips: [
          { id: "view_map:A", label: "🗺️ 개발원 도면 확인" },
          { id: "view_map:B", label: "🗺️ 부산역 도면 확인" },
          { id: "view_map:C", label: "🗺️ 김해공항 도면 확인" },
          { id: "view_map:D", label: "🗺️ 신세계 도면 확인" }
        ]
      };
    }

    // 2-1. 각 주차장 요금 안내 (4개 주차장 전체 요금표)
    if (
      q.includes("fee_rule") ||
      q.includes("각 주차장") ||
      q.includes("주차장별") ||
      q.includes("전 주차장 요금") ||
      q.includes("전체 주차장 요금") ||
      q.includes("모든 주차장 요금") ||
      q.includes("4개 주차장") ||
      q.includes("주차장 4곳") ||
      (q.includes("주차장") && q.includes("요금") && !q.includes("23") && !q.includes("내 차")) ||
      (q.includes("요금") && (q.includes("각") || q.includes("전체") || q.includes("모든") || q.includes("기준") || q.includes("표") || q.includes("규정")))
    ) {
      return {
        text: "**전체 4개 주차장 요금 안내** 💳\n\n• **개발원 주차장**: 기본 30분 1,000원 / 추가 10분당 500원 (일 최대 15,000원 · 회차 15분 이내 무료)\n• **부산역 주차장**: 기본 30분 1,500원 / 추가 10분당 600원 (일 최대 20,000원 · 회차 15분 이내 무료)\n• **김해공항 주차장**: 기본 30분 3,000원 / 추가 10분당 800원 (일 최대 40,000원 · 회차 20분 이내 무료)\n• **신세계백화점 주차장**: 기본 30분 2,000원 / 추가 10분당 700원 (일 최대 30,000원 · 회차 30분 이내 무료)\n\n※ 친환경 전기차 및 장애인 등록 차량은 50% 자동 감면 혜택이 적용됩니다.",
        card: {
          type: "fee_rule",
          data: {
            lots: [
              { lot_id: "A", name: "개발원 주차장", base_min: 30, base_fee: 1000, unit_min: 10, unit_fee: 500, daily_max: 15000, free_min: 15 },
              { lot_id: "B", name: "부산역 주차장", base_min: 30, base_fee: 1500, unit_min: 10, unit_fee: 600, daily_max: 20000, free_min: 15 },
              { lot_id: "C", name: "김해공항 주차장", base_min: 30, base_fee: 3000, unit_min: 10, unit_fee: 800, daily_max: 40000, free_min: 20 },
              { lot_id: "D", name: "신세계백화점 주차장", base_min: 30, base_fee: 2000, unit_min: 10, unit_fee: 700, daily_max: 30000, free_min: 30 }
            ]
          }
        },
        chips: [
          { id: "fee_car:23", label: "23번 요금 확인" },
          { id: "empty_all", label: "🅿️ 주차장 빈자리 확인" }
        ]
      };
    }

    // 2. [이미지 2] 김해공항 국제선 청사 가장 가까운 주차구역
    if ((q.includes("국제선") || q.includes("국내선") || q.includes("청사")) && (q.includes("가까운") || q.includes("가는길") || q.includes("어디"))) {
      return {
        text: "**김해공항 주차장 → 국제선 여객청사 최적 구역 안내** ✈️\n\n국제선 청사로 가실 때 가장 가까운 주차구역은 **[P2 국제선 구역 (B-01 ~ B-24)]**입니다!\n\n• **도보 이동**: P2 구역 북측 출구를 통과하면 청사 순환 셔틀 승하차로와 바로 연결되며, **도보 약 1~2분** 만에 국제선 청사 출입구에 도착할 수 있어요.\n• **팁**: 전기차 충전이 필요하신 경우 B-11, B-12칸에 충전기가 위치해 있습니다.",
        chips: [
          { id: "lot:C", label: "김해공항 P2 구역 빈자리" },
          { id: "fee_lot:C", label: "김해공항 요금 안내" }
        ]
      };
    }

    // 3. [이미지 3] 신세계백화점 엘리베이터 개수
    if ((q.includes("엘리베이터") || q.includes("승강기") || q.includes("에스컬레이터")) && (q.includes("몇") || q.includes("어디") || q.includes("위치") || q.includes("신세계"))) {
      return {
        text: "**신세계백화점 주차장 편의시설 안내** 🛗\n\n도면 기준으로 주차장 내 엘리베이터는 **총 2개소**에 위치해 있습니다:\n\n1. **몰 연결 엘리베이터**: 몰(Mall) A / C구역 상단 (영화관 방면 연결)\n2. **백화점 연결 엘리베이터**: 백화점 B / D구역 상단 (백화점 매장 바로 연결)\n\n💡 **추가 편의시설**:\n• **에스컬레이터**: 하단 몰 연결 출입구(좌측) 및 백화점 연결 출입구(우측)에 각 1개소씩 위치\n• **비상구 유도등**: 하단 중앙 연결 통로에 위치",
        chips: [
          { id: "lot:D", label: "신세계 주차 도면 확인" },
          { id: "empty_all", label: "주차장 현황 보기" }
        ]
      };
    }

    // 4. [이미지 4] 비행기 출발 3시간 전 도착 혼잡도
    if (q.includes("비행기") || (q.includes("출발") && (q.includes("혼잡") || q.includes("도착") || q.includes("시간전") || q.includes("시간 전에")))) {
      return {
        text: "**김해공항 항공편 탑승 & 주차 혼잡도 안내** ✈️\n\n결론부터 말씀드리면, **비행기 출발 3시간 전 도착 시 주차와 탑승 수속 모두 여유 있게 진행하실 수 있습니다!** 전혀 늦지 않고 매우 안전한 일정이에요.\n\n• **공항 권장 도착 시간**: 국제선 탑승 시 발권, 수하물 위탁, 보안검색 및 출국심사를 고려해 통상 **출발 2시간 30분 ~ 3시간 전** 도착을 가장 권장합니다.\n• **현재 김해공항 주차 현황**: 현재 총 64면 중 **52면의 빈자리**가 있어 만차 걱정 없이 주차가 가능합니다.\n• **추천 팁**: 국제선 탑승 고객은 **[P2 국제선 구역]**에 주차하시면 국제선 청사 출입구까지 도보로 1~2분 만에 가장 빠르게 이동하실 수 있습니다.",
        chips: [
          { id: "lot:C", label: "김해공항 실시간 빈자리" },
          { id: "fee_lot:C", label: "김해공항 요금표" }
        ]
      };
    }

    // 5. 화재 비상 경보 해제 (초록색 안전 카드)
    if (q.includes("fire_clear") || q.includes("clear_alert") || q.includes("해제") || q.includes("상황 종료") || q.includes("진압") || q.includes("불 꺼") || q.includes("화재 해결") || q.includes("안전 확인")) {
      const nowStr = new Date().toLocaleTimeString();
      return {
        text: "✅ **[화재 비상 경보 상황 종료 및 안전 해제]** ✅\n\n• **안전 확인 구역**: **개발원 주차장 [A-05구역]**\n• **해제 시각**: " + nowStr + "\n• **조치 결과**: 소방서 및 자위소방대 발화점 완전 진압 / 연기 및 유독가스 배연 완료\n• **안전 점검**: 소방·전기 시설물 정밀 안전 점검 완료 (이상 없음)\n• **운영 안내**: **주차장 전 구역 정상 운영 재개** (출차 차단기 정상 관제 전환)\n\n시설 이용객 여러분께서는 안심하시고 주차장 및 편의시설을 정상적으로 이용하시기 바랍니다. 긴급 상황에 협조해 주셔서 감사합니다.",
        card: {
          type: "alert_resolved",
          data: {
            resolved_id: "RES-" + Date.now().toString().slice(-6),
            lot_id: "A",
            lot_name: "개발원 주차장",
            space_id: "A-05구역",
            resolved_at: nowStr,
            status: "safe",
            safety_checks: [
              { title: "화재 완전 진압", desc: "소방대 현장 출동 및 발화점 완전 진압 완료" },
              { title: "유독가스 배출 완료", desc: "강제 배연 설비 가동으로 실내 공기질 정상화 확인" },
              { title: "시설물 정밀 안전 점검", desc: "소방·전기 시스템 및 승강기 가동 승인" },
              { title: "정상 관제 복구", desc: "차단기 및 무인 정산 시스템 정상 운영 재개" }
            ]
          }
        },
        chips: [
          { id: "empty_all", label: "🚗 주차장 빈자리 확인" },
          { id: "view_map:A", label: "🗺️ 정상 주차 도면 확인" },
          { id: "zone:ev", label: "⚡ 전기차 충전 구역" }
        ]
      };
    }

    // 6. 화재 경보 및 비상 대피 (빨간색 알림 카드)
    if (!q.includes("해제") && !q.includes("종료") && !q.includes("진압") && (q.includes("화재") || q.includes("불") || q.includes("대피") || q.includes("경보") || q.includes("연기"))) {
      return {
        text: "🚨 **[긴급 화재 비상 대피 경보 발령]** 🚨\n\n• **발화 위치**: **개발원 주차장 [A-05구역]**\n• **상황**: A구역 화재 연기 감지 (Lv.2 비상사태)\n• **조치**: 무인 차단기 자동 비상 개방 완료\n\n⚠️ **지금 즉시 차량 탑승을 중단하고 아래 4대 긴급 대피요령에 따라 비상구로 탈출하세요!**",
        card: {
          type: "alert",
          data: {
            alert_id: "ALT-EMERGENCY",
            lot_id: "A",
            lot_name: "개발원 주차장",
            space_id: "A-05구역",
            level: 2,
            message: "A구역 전기차 충전칸 화재 연기 감지",
            detected_at: new Date().toLocaleTimeString(),
            evacuation_steps: [
              { step: "1단계", title: "비상 유도등 확인", desc: "초록색 비상 유도등(달리는 사람)을 따라 양측 비상구로 신속히 이동하세요." },
              { step: "2단계", title: "호흡기 보호", desc: "젖은 손수건이나 옷으로 코와 입을 감싸고 낮은 자세를 유지하세요." },
              { step: "3단계", title: "승강기 탑승 금지", desc: "엘리베이터는 절대 타지 마시고 비상 계단을 이용하세요." },
              { step: "4단계", title: "차단기 자동 개방", desc: "모든 차단기가 비상 개방되었습니다. 차량을 두고 신속히 보행 탈출하세요." }
            ]
          }
        },
        chips: [
          { id: "view_map:A", label: "🗺️ 화재 위치 도면 확인" },
          { id: "fire_clear", label: "🧯 화재 진압 및 경보 해제" },
          { id: "call_admin", label: "📞 상황실/119 긴급 신고" }
        ]
      };
    }

    return null;
  }

  async function sendMessage(text, chipId = null) {
    if (!text && !chipId) return;

    if (text && text !== "시작") {
      addMsg(text, "user");
    }
    inputElem.value = "";

    const special = checkSpecialQueries(text);
    if (special) {
      setTimeout(() => {
        addMsg(special.text, "bot", special.card);
        renderChips(special.chips || []);
      }, 300);
      return;
    }

    const loadingRow = addMsg("답변을 조회하고 있어요...", "bot");

    try {
      const resp = await fetch("/api/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          text: text || null,
          chip_id: chipId || null,
          request_id: "req_" + Date.now()
        })
      });
      loadingRow.remove();

      if (resp.ok) {
        const data = await resp.json();
        addMsg(data.text, "bot", data.card);
        renderChips(data.chips || []);
      } else {
        addMsg("안내를 가져오지 못했습니다. 다시 시도해주세요.", "bot");
      }
    } catch (e) {
      loadingRow.remove();
      addMsg("현재 오프라인 모드입니다. 빈자리 및 요금 조회가 지원됩니다.", "bot");
    }
  }

  function renderEmbedCard(card) {
    if (!card || !card.data) return "";
    const type = card.type;
    const d = card.data;

    if (type === "ev_charging") {
      const chargers = d.chargers || [];
      return `
        <div style="margin-top:8px; border:1.5px solid #86efac; background:linear-gradient(180deg, #f0fdf4 0%, #f6fef9 100%); border-radius:12px; padding:10px 12px;">
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #bbf7d0; padding-bottom:6px; margin-bottom:8px; font-size:12px; font-weight:800; color:#15803d;">
            <span>⚡ 전기차 충전 현황 관제</span>
            <span style="font-size:10px; background:#dcfce7; color:#166534; padding:1px 6px; border-radius:10px; font-weight:700;">실시간</span>
          </div>
          <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(170px, 1fr)); gap:6px;">
            ${chargers.map(c => {
              const isChg = c.status === "charging";
              const isComp = c.status === "completed";
              const isAvail = c.status === "available";
              const pct = c.battery_percent || 0;
              const barColor = isComp ? "#9333ea" : (pct >= 80 ? "#16a34a" : "#22c55e");
              return `
                <div style="background:#fff; border:1.5px solid #e2e8f0; border-left:4px solid ${isChg ? '#16a34a' : (isComp ? '#9333ea' : '#2563eb')}; border-radius:10px; padding:10px; font-size:11px; display:flex; flex-direction:column; justify-content:space-between; min-height:140px; box-sizing:border-box; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                  <div style="display:flex; justify-content:space-between; font-weight:800; align-items:center; padding-bottom:5px; border-bottom:1px solid #f1f5f9; margin-bottom:6px;">
                    <span style="color:#0f172a; font-size:11px;">📍 ${c.lot_name.replace(" 주차장", "")} [${c.space_id}]</span>
                    <span style="font-size:9px; padding:2px 6px; border-radius:8px; background:${isChg ? '#ecfdf5' : (isComp ? '#f5f3ff' : '#eff6ff')}; color:${isChg ? '#059669' : (isComp ? '#7c3aed' : '#2563eb')}; font-weight:700;">${isChg ? '⚡ 충전중' : (isComp ? '✨ 완충' : '🟢 즉시가능')}</span>
                  </div>
                  <div style="flex:1; display:flex; flex-direction:column; justify-content:center; margin-bottom:6px;">
                    ${isAvail ? `
                      <div style="text-align:center; padding:10px 8px; background:linear-gradient(135deg, #eff6ff 0%, #f0fdf4 100%); border:1px solid #bfdbfe; border-radius:8px; margin:auto 0;">
                        <div style="display:inline-block; padding:2px 8px; background:#fff; border:1px solid #93c5fd; border-radius:12px; color:#1d4ed8; font-weight:800; font-size:10px; margin-bottom:2px;">🟢 즉시 충전 가능</div>
                        <div style="color:#475569; font-size:9px;">대기 없이 바로 충전 가능</div>
                      </div>
                    ` : `
                      <div style="display:flex; justify-content:space-between; margin-bottom:2px; align-items:flex-end;">
                        <div>
                          <div style="color:#0f172a; font-size:11px; font-weight:800;">차량 ${c.plate_number || ''}</div>
                          <div style="color:#64748b; font-size:9px;">실시간 충전 중</div>
                        </div>
                        <strong style="color:${isComp ? '#7c3aed' : '#059669'}; font-size:16px;">${pct}%</strong>
                      </div>
                      <div style="width:100%; height:6px; background:#f1f5f9; border-radius:3px; overflow:hidden; margin:3px 0; border:1px solid #e2e8f0;">
                        <div style="width:${pct}%; height:100%; background:${isComp ? '#9333ea' : '#10b981'};"></div>
                      </div>
                      <div style="display:flex; justify-content:space-between; font-size:9px; color:#64748b; background:#f8fafc; padding:3px 6px; border-radius:4px;">
                        <span>⚡ ${c.current_speed_kw ? c.current_speed_kw + 'kW' : ''}</span>
                        <span style="font-weight:700; color:${c.remaining_minutes <= 10 && !isComp ? '#dc2626' : '#059669'}">${isComp ? '출차 대기' : `약 ${c.remaining_minutes}분`}</span>
                      </div>
                    `}
                  </div>
                  <div>
                    <button style="width:100%; height:32px; display:flex; align-items:center; justify-content:center; gap:4px; font-size:10px; background:#eff6ff; color:#2563eb; border:1.5px solid #93c5fd; border-radius:6px; cursor:pointer; font-weight:700; box-shadow:0 1px 2px rgba(37,99,235,0.08);" onclick="if(window.switchParkingLotAndShowMap) window.switchParkingLotAndShowMap('${c.lot_id}', '${c.space_id}')">🗺️ ${c.space_id} 주차칸 도면 보기</button>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        </div>
      `;
    }

    if (type === "alert") {
      const steps = d.evacuation_steps || [
        { step: "1단계", title: "비상 유도등 확인", desc: "초록색 비상 유도등(달리는 사람)을 따라 양측 비상구로 신속히 이동" },
        { step: "2단계", title: "호흡기 보호", desc: "젖은 수건/옷으로 코와 입을 감싸고 낮은 자세 유지" },
        { step: "3단계", title: "승강기 탑승 금지", desc: "엘리베이터 절대 금지, 비상 계단 이용" },
        { step: "4단계", title: "차단기 자동 개방", desc: "모든 차단기 비상 개방됨, 차량 두고 보행 탈출" }
      ];

      return `
        <div style="margin-top:8px; border:2px solid #ef4444; background:linear-gradient(180deg, #fef2f2 0%, #fff5f5 100%); border-radius:12px; padding:12px 14px; box-shadow:0 4px 12px rgba(239,68,68,0.2);">
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1.5px solid #fca5a5; padding-bottom:6px; margin-bottom:8px;">
            <span style="font-weight:800; font-size:14px; color:#b91c1c;">🚨 긴급 화재 발생 경보</span>
            <span style="font-size:10px; padding:2px 7px; background:#dc2626; color:#fff; border-radius:10px; font-weight:800;">Lv.${d.level || 2} 비상사태</span>
          </div>
          <div style="background:#fee2e2; border:1px solid #f87171; border-radius:8px; padding:8px 10px; margin-bottom:8px;">
            <div style="font-size:10px; color:#b91c1c; font-weight:700;">발화 감지 위치</div>
            <div style="font-size:13px; font-weight:800; color:#991b1b;">📍 ${d.lot_name} [${d.space_id || 'A-05구역'}]</div>
          </div>
          <div style="background:#fff; border:1px solid #fecaca; border-radius:8px; padding:8px 10px; margin-bottom:8px;">
            <div style="font-size:11px; font-weight:800; color:#b91c1c; margin-bottom:6px;">🏃 현장 긴급 비상 대피요령</div>
            <div style="display:flex; flex-direction:column; gap:4px;">
              ${steps.map(s => `
                <div style="display:flex; align-items:flex-start; gap:6px; font-size:11px; line-height:1.4;">
                  <span style="background:#fee2e2; color:#b91c1c; font-weight:800; font-size:9px; padding:1px 4px; border-radius:3px; flex-shrink:0;">${s.step}</span>
                  <div><strong style="color:#991b1b;">${s.title}</strong>: ${s.desc}</div>
                </div>
              `).join("")}
            </div>
          </div>
          <div style="display:flex; gap:6px;">
            <button style="flex:1; height:34px; background:#dc2626; color:#fff; border:none; border-radius:6px; font-weight:800; font-size:11px; cursor:pointer;" onclick="if(window.switchParkingLotAndShowMap) window.switchParkingLotAndShowMap('${d.lot_id || 'A'}', 'A-05');">
              🗺️ 화재 도면 확인
            </button>
            <button style="padding:0 10px; height:34px; background:#ecfdf5; color:#059669; border:1px solid #10b981; border-radius:6px; font-weight:800; font-size:11px; cursor:pointer;" onclick="document.getElementById('parkon-clear-fire-btn')?.click();" title="화재 진압 완료 후 안전 경보 해제">
              🧯 경보 해제
            </button>
          </div>
        </div>
      `;
    }

    if (type === "alert_resolved" || type === "safe") {
      const checks = d.safety_checks || [
        { title: "화재 완전 진압", desc: "소방대 현장 출동 및 발화점 완전 진압 완료" },
        { title: "유독가스 배출 완료", desc: "강제 배연 설비 가동으로 실내 공기질 정상화 확인" },
        { title: "시설물 정밀 안전 점검", desc: "소방·전기 시스템 및 승강기 가동 승인" },
        { title: "정상 주차 관제 복구", desc: "차단기 및 무인 정산 시스템 정상 운영 재개" }
      ];

      return `
        <div style="margin-top:8px; border:2px solid #10b981; background:linear-gradient(180deg, #f0fdf4 0%, #ffffff 100%); border-radius:12px; padding:12px 14px; box-shadow:0 4px 12px rgba(16,185,129,0.15);">
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1.5px solid #a7f3d0; padding-bottom:6px; margin-bottom:8px;">
            <span style="font-weight:800; font-size:14px; color:#065f46;">✅ 화재 진압 및 비상 경보 해제</span>
            <span style="font-size:10px; padding:2px 7px; background:#10b981; color:#fff; border-radius:10px; font-weight:800;">정상 운영 재개</span>
          </div>
          <div style="background:#ecfdf5; border:1px solid #6ee7b7; border-radius:8px; padding:8px 10px; margin-bottom:8px; display:flex; justify-content:space-between; align-items:center;">
            <div>
              <div style="font-size:10px; color:#166534; font-weight:700;">안전 점검 완료 구역</div>
              <div style="font-size:13px; font-weight:800; color:#047857;">📍 ${d.lot_name} [${d.space_id || 'A-05구역'}]</div>
            </div>
            <span style="font-size:10px; color:#059669; font-weight:700;">⏱️ ${d.resolved_at || '방금 전'}</span>
          </div>
          <div style="background:#fff; border:1px solid #d1fae5; border-radius:8px; padding:8px 10px; margin-bottom:8px;">
            <div style="font-size:11px; font-weight:800; color:#065f46; margin-bottom:6px;">🛡️ 현장 안전 점검 및 조치 결과</div>
            <div style="display:flex; flex-direction:column; gap:4px;">
              ${checks.map(c => `
                <div style="display:flex; align-items:flex-start; gap:6px; font-size:11px; line-height:1.4;">
                  <span style="color:#059669; font-weight:800;">✓</span>
                  <div><strong style="color:#14532d;">${c.title}</strong>: <span style="color:#166534;">${c.desc}</span></div>
                </div>
              `).join("")}
            </div>
          </div>
          <div style="display:flex; gap:6px;">
            <button style="flex:1; height:34px; background:#059669; color:#fff; border:none; border-radius:6px; font-weight:800; font-size:11px; cursor:pointer;" onclick="if(window.switchParkingLotAndShowMap) window.switchParkingLotAndShowMap('${d.lot_id || 'A'}');">
              🗺️ 정상 주차장 도면 확인
            </button>
            <button style="padding:0 12px; height:34px; background:#fff; color:#059669; border:1px solid #10b981; border-radius:6px; font-weight:800; font-size:11px; cursor:pointer;" onclick="sendMessage('주차장 빈자리 확인');">
              🚗 빈자리 찾기
            </button>
          </div>
        </div>
      `;
    }

    if (type === "fee_rule") {
      const lots = d.lots || (d.lot ? [d.lot] : []);
      return `
        <div style="margin-top:8px; border:1.5px solid #cbd5e1; background:#ffffff; border-radius:12px; padding:12px 14px; box-shadow:0 2px 8px rgba(0,0,0,0.06);">
          <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #e2e8f0; padding-bottom:6px; margin-bottom:8px; font-weight:800; font-size:13px; color:#1e293b;">
            <span>💳 주차장별 요금 기준표</span>
            <span style="font-size:10px; background:#eff6ff; color:#2563eb; padding:2px 6px; border-radius:10px; font-weight:700;">요금 안내</span>
          </div>
          ${lots.map(l => {
            const rule = l.fee_rule || l;
            return `
              <div style="margin-bottom:8px; padding-bottom:6px; border-bottom:1px dashed #e2e8f0; font-size:11px;">
                <div style="display:flex; justify-content:space-between; font-weight:700; font-size:12px; align-items:center;">
                  <span>${l.name}</span>
                  <span style="color:#2563eb; font-weight:800;">기본 ${rule.base_min}분 ${(rule.base_fee || 0).toLocaleString()}원</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:3px; color:#64748b; font-size:10px;">
                  <span>추가 ${rule.unit_min}분당 ${(rule.unit_fee || 0).toLocaleString()}원 · 일 최대 ${(rule.daily_max || 0).toLocaleString()}원</span>
                  <span style="color:#10b981; font-weight:600;">회차 ${rule.free_min}분 무료</span>
                </div>
              </div>
            `;
          }).join("")}
          <div style="font-size:10px; color:#64748b; margin-top:4px;">
            ※ 친환경 전기차 및 장애인 등록 차량 50% 자동 감면
          </div>
        </div>
      `;
    }
    return "";
  }

  function addMsg(txt, sender, card = null) {
    const row = document.createElement("div");
    row.className = `parkon-msg-row ${sender}`;
    let cardHtml = "";
    if (card) {
      cardHtml = renderEmbedCard(card);
    }
    const isAlert = card && card.type === "alert";
    const isResolved = card && (card.type === "alert_resolved" || card.type === "safe");
    let bubbleStyle = "";
    if (isAlert) {
      bubbleStyle = 'style="background:#fff5f5; border:1.5px solid #f87171; color:#991b1b;"';
    } else if (isResolved) {
      bubbleStyle = 'style="background:#f0fdf4; border:1.5px solid #86efac; color:#166534;"';
    }
    let html = `
      <div class="parkon-msg-bubble" ${bubbleStyle}>
        <div>${txt.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>").replace(/\n/g, "<br>")}</div>
        ${cardHtml}
      </div>
    `;
    row.innerHTML = html;
    msgsContainer.appendChild(row);
    msgsContainer.scrollTop = msgsContainer.scrollHeight;
    return row;
  }

  function renderChips(chips) {
    chipsContainer.innerHTML = "";
    chips.forEach(c => {
      const chip = document.createElement("button");
      chip.className = "parkon-chip";
      chip.innerText = c.label;
      chip.addEventListener("click", () => {
        addMsg(c.label, "user");
        if (c.id && c.id.startsWith("view_map:")) {
          const lotId = c.id.split(":")[1];
          if (typeof window.switchParkingLotAndShowMap === "function") {
            window.switchParkingLotAndShowMap(lotId);
          } else {
            const sel = document.querySelector("select");
            if (sel) {
              sel.value = lotId;
              sel.dispatchEvent(new Event("change"));
            }
          }
        }
        sendMessage(null, c.id);
      });
      chipsContainer.appendChild(chip);
    });
  }

  sendBtn.addEventListener("click", () => sendMessage(inputElem.value.trim()));
  inputElem.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage(inputElem.value.trim());
  });

  console.log("✅ PARK:ON 챗봇 임베드 위젯이 성공적으로 활성화되었습니다.");
})();

