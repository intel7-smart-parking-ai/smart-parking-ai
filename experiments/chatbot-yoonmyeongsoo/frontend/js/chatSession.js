// @ts-nocheck
/**
 * PARK:ON 챗봇 세션 관리자 (chatSession.js)
 * 서정범님 챗봇 스펙 및 QnA 문서의 7대 원칙 반영
 */
class ChatSession {
  constructor() {
    this.sessionId = localStorage.getItem("parkon_chat_session") || "sess_" + Math.random().toString(36).substring(2, 10);
    localStorage.setItem("parkon_chat_session", this.sessionId);
    
    this.apiBaseUrl = window.PARKON_API_URL || "";
    this.runtimeId = null;
    this.stateVersion = 0;
    this.lastRequestId = null;
    this.vehicleContext = { car_id: null, state: "unselected" };
    this.pendingToken = null;
    this.isAlertActive = false;
  }

  async send({ text, chipId, retry = false }) {
    const requestId = retry && this.lastRequestId ? this.lastRequestId : "req_" + Date.now() + "_" + Math.random().toString(36).substring(2, 6);
    if (!retry) this.lastRequestId = requestId;

    const payload = {
      session_id: this.sessionId,
      text: text || null,
      chip_id: chipId || null,
      request_id: requestId,
      vehicle_context: this.vehicleContext
    };

    try {
      // 1. 서버 API 호출 시도
      const endpoints = [
        `${this.apiBaseUrl}/api/v1/chat`
      ];
      
      let res = null;
      for (const url of endpoints) {
        try {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 4000);
          const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: controller.signal
          });
          clearTimeout(timeoutId);
          if (response.ok) {
            res = await response.json();
            break;
          }
        } catch (e) {
          // 다음 엔드포인트 시도
        }
      }

      if (res) {
        this.updateState(res);
        return res;
      }
    } catch (err) {
      console.warn("서버 연결 실패, 내장 로컬 스마트 엔진으로 대체합니다.", err);
    }

    // 2. 서버 연결이 불가능할 경우 내장 로컬 스마트 엔진으로 즉시 응답 생성
    const fallbackResponse = this.generateLocalResponse(payload);
    this.updateState(fallbackResponse);
    return fallbackResponse;
  }

  updateState(res) {
    if (res.runtime_id) this.runtimeId = res.runtime_id;
    if (res.state_version) this.stateVersion = res.state_version;
    if (res.vehicle_context && res.vehicle_context.car_id) {
      this.vehicleContext = res.vehicle_context;
    }
    if (res.confirmation_token) {
      this.pendingToken = res.confirmation_token;
    }
    if (res.card && res.card.type === "payment") {
      this.pendingToken = null;
    }
    if (res.safety && res.safety.level >= 2) {
      this.isAlertActive = true;
    }
  }

  // 브라우저 단독 실행 및 오프라인 환경을 위한 내장 로직
  generateLocalResponse({ text, chip_id }) {
    const raw = (text || chip_id || "").toLowerCase();
    
    // [최초 진입 및 인사말 웰컴 메시지]
    if (!raw || raw === "시작" || raw === "start" || raw === "처음" || raw.includes("안녕") || raw.includes("하이") || raw.includes("반가워")) {
      return {
        text: "안녕하세요! 스마트 주차 도우미 **PARK:ON 챗봇**입니다. 🚗✨\n\n실시간 빈자리 확인부터 내 차 위치 찾기, 주차 요금 조회 및 사전 정산, 전기차 충전 현황까지 편리하게 도와드릴게요!\n\n궁금하신 점을 편하게 말씀해 주시거나 아래 추천 버튼을 눌러보세요.",
        card: null,
        chips: [
          { id: "empty_all", label: "🅿️ 전 주차장 빈자리 확인" },
          { id: "ev_status", label: "⚡ 전기차 충전 현황" },
          { id: "find_car", label: "🚗 내 차 어디 있어?" },
          { id: "fee_rule", label: "💳 주차 요금 안내" },
          { id: "call_admin", label: "📞 관리자 호출" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // [도면 확인 칩 / 버튼]
    if (raw.startsWith("view_map:") || raw.includes("도면")) {
      const lotId = raw.startsWith("view_map:") ? raw.split(":")[1].toUpperCase() : "A";
      const lotNames = { A: "개발원 주차장", B: "부산역 주차장", C: "김해공항 주차장", D: "신세계백화점 주차장" };
      const name = lotNames[lotId] || "개발원 주차장";
      return {
        text: `🗺️ **${name} 주차 도면**으로 화면을 전환했습니다! 상단에서 실시간 주차 배치를 확인하실 수 있습니다.`,
        card: {
          type: "view_map_trigger",
          data: { lot_id: lotId, lot_name: name }
        },
        chips: [
          { id: "empty_all", label: "전체 빈자리 현황" },
          { id: "ev_status", label: "전기차 충전 현황" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // [전기차 충전 현황]: 몇 퍼센트 충전 중인지, 충전시간이 얼마나 남았는지 상세 안내
    if (raw.includes("충전") && (raw.includes("현황") || raw.includes("상태") || raw.includes("시간") || raw.includes("얼마") || raw.includes("퍼센트") || raw.includes("남은") || raw.includes("완충"))) {
      const chargers = [
        {
          charger_id: "EV-A05", lot_id: "A", lot_name: "개발원 주차장", space_id: "A-05",
          charger_type: "급속 100kW", status: "charging", status_text: "충전 중",
          car_id: "23", plate_number: "12가 3423", battery_percent: 78, remaining_minutes: 14, current_speed_kw: 84.5
        },
        {
          charger_id: "EV-A06", lot_id: "A", lot_name: "개발원 주차장", space_id: "A-06",
          charger_type: "완속 7kW", status: "available", status_text: "충전 가능 (빈자리)",
          car_id: null, plate_number: null, battery_percent: 0, remaining_minutes: 0, current_speed_kw: 0
        },
        {
          charger_id: "EV-B07", lot_id: "B", lot_name: "부산역 주차장", space_id: "A-07",
          charger_type: "급속 100kW", status: "charging", status_text: "충전 완료 임박",
          car_id: "58", plate_number: "78라 9058", battery_percent: 92, remaining_minutes: 6, current_speed_kw: 25.0
        },
        {
          charger_id: "EV-C11", lot_id: "C", lot_name: "김해공항 (P1)", space_id: "A-11",
          charger_type: "급속 100kW", status: "charging", status_text: "충전 중",
          car_id: "57", plate_number: "89마 5757", battery_percent: 45, remaining_minutes: 28, current_speed_kw: 92.0
        },
        {
          charger_id: "EV-C31", lot_id: "C", lot_name: "김해공항 (P3)", space_id: "B-31",
          charger_type: "완속 7kW", status: "completed", status_text: "완충 완료 (출차 대기)",
          car_id: "42", plate_number: "42오 4242", battery_percent: 100, remaining_minutes: 0, current_speed_kw: 0
        },
        {
          charger_id: "EV-D05", lot_id: "D", lot_name: "신세계백화점", space_id: "C-05",
          charger_type: "완속 7kW", status: "charging", status_text: "충전 중",
          car_id: "11", plate_number: "11구 1111", battery_percent: 70, remaining_minutes: 45, current_speed_kw: 6.8
        }
      ];

      return {
        text: "**전기차 충전 구역 실시간 충전 현황** ⚡\n\n• **개발원 A-05**: 12가 3423 (23번) — **78% 충전 중** (완충까지 **약 14분 남음** / 100kW 급속)\n• **부산역 A-07**: 78라 9058 (58번) — **92% 충전 중** (완충까지 **약 6분 남음**)\n• **김해공항 A-11**: 89마 5757 (57번) — **45% 충전 중** (완충까지 **약 28분 남음**)\n• **김해공항 B-31**: 42오 4242 (42번) — **100% 완충 완료** (출차 대기)\n• **신세계 C-05**: 11구 1111 (11번) — **70% 충전 중** (완충까지 **약 45분 남음**)\n\n※ 나머지 구역(개발원 A-06, 부산역 A-08, 공항 A-12 등)은 즉시 충전 가능한 빈자리입니다.",
        card: {
          type: "ev_charging",
          data: { chargers }
        },
        chips: [
          { id: "view_map:A", label: "🗺️ 개발원 충전소 위치" },
          { id: "view_map:C", label: "🗺️ 김해공항 충전소 위치" },
          { id: "empty_all", label: "전체 빈자리 현황" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // [전 주차장 빈자리 확인]: 4개 주차장 전체 빈자리 현황 띄우기
    if (raw.includes("전 주차장") || raw.includes("전체 주차장") || raw.includes("모든 주차장") || raw.includes("empty") || raw.includes("자리") || raw.includes("빈자리")) {
      return {
        text: "**전체 4개 주차장 실시간 빈자리 현황** (총 122면 여유) 🅿️\n\n• **개발원 주차장**: 빈자리 **10면** (일반 7 · ⚡전기 2 · ♿장애인 1)\n• **부산역 주차장**: 빈자리 **14면** (일반 10 · ⚡전기 2 · ♿장애인 2)\n• **김해공항 주차장**: 빈자리 **52면** (일반 34 · ⚡전기 4 · ♿장애인 14)\n• **신세계백화점 주차장**: 빈자리 **46면** (일반 35 · ⚡전기 5 · ♿장애인 6)\n\n원하시는 주차장의 **[🗺️ 도면 확인]** 버튼을 누르시면 해당 주차장 도면으로 즉시 이동합니다.",
        card: {
          type: "empty_spots",
          data: {
            lots: [
              { lot_id: "A", name: "개발원 주차장", total_empty: 10, total_capacity: 12, general_empty: 7, ev_empty: 2, accessible_empty: 1 },
              { lot_id: "B", name: "부산역 주차장", total_empty: 14, total_capacity: 16, general_empty: 10, ev_empty: 2, accessible_empty: 2 },
              { lot_id: "C", name: "김해공항 주차장", total_empty: 52, total_capacity: 64, general_empty: 34, ev_empty: 4, accessible_empty: 14 },
              { lot_id: "D", name: "신세계백화점 주차장", total_empty: 46, total_capacity: 64, general_empty: 35, ev_empty: 5, accessible_empty: 6 }
            ],
            show_map_buttons: true
          }
        },
        chips: [
          { id: "view_map:A", label: "🗺️ 개발원 도면 확인" },
          { id: "view_map:B", label: "🗺️ 부산역 도면 확인" },
          { id: "view_map:C", label: "🗺️ 김해공항 도면 확인" },
          { id: "view_map:D", label: "🗺️ 신세계 도면 확인" },
          { id: "ev_status", label: "⚡ 전기차 충전 현황" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // [개선 2] 목적지 청사 기준 가장 가까운 주차구역 안내 (김해공항 국제선/국내선)
    if ((raw.includes("국제선") || raw.includes("국내선") || raw.includes("청사")) && (raw.includes("가까운") || raw.includes("가는길") || raw.includes("어디"))) {
      if (raw.includes("국제선")) {
        return {
          text: "**김해공항 주차장 → 국제선 여객청사 최적 구역 안내** ✈️\n\n국제선 청사로 가실 때 가장 가까운 주차구역은 **[P2 국제선 구역 (B-01 ~ B-24)]**입니다!\n\n• **도보 이동**: P2 구역 북측 출구를 통과하면 청사 순환 셔틀 승하차로와 바로 연결되며, **도보 약 1~2분** 만에 국제선 청사 출입구에 도착할 수 있어요.\n• **팁**: 전기차 충전이 필요하신 경우 B-11, B-12칸에 충전기가 위치해 있습니다.",
          card: {
            type: "empty_spots",
            data: {
              lot: { lot_id: "C", name: "김해공항 주차장", total_empty: 52, total_capacity: 64, general_empty: 34, ev_empty: 4, accessible_empty: 14 }
            }
          },
          chips: [
            { id: "lot:C", label: "김해공항 P2 구역 빈자리" },
            { id: "fee_lot:C", label: "김해공항 요금표" },
            { id: "empty_all", label: "주차장 현황 보기" }
          ],
          runtime_id: "local_runtime",
          state_version: this.stateVersion + 1
        };
      }
    }

    // [개선 3] 신세계백화점 엘리베이터/에스컬레이터 시설물 문의
    if ((raw.includes("엘리베이터") || raw.includes("승강기") || raw.includes("에스컬레이터")) && (raw.includes("몇") || raw.includes("어디") || raw.includes("위치") || raw.includes("신세계"))) {
      return {
        text: "**신세계백화점 주차장 편의시설 안내** 🛗\n\n도면 기준으로 주차장 내 엘리베이터는 **총 2개소**에 위치해 있습니다:\n\n1. **몰 연결 엘리베이터**: 몰(Mall) A / C구역 상단 (영화관 방면 연결)\n2. **백화점 연결 엘리베이터**: 백화점 B / D구역 상단 (백화점 매장 바로 연결)\n\n💡 **추가 편의시설**:\n• **에스컬레이터**: 하단 몰 연결 출입구(좌측) 및 백화점 연결 출입구(우측)에 각 1개소씩 위치\n• **비상구 유도등**: 하단 중앙 연결 통로에 위치",
        card: null,
        chips: [
          { id: "lot:D", label: "신세계 주차 도면 확인" },
          { id: "empty_all", label: "주차장 현황 보기" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // [개선 4] 비행기 출발 시간 및 공항 주차장 혼잡도 문의
    if (raw.includes("비행기") || (raw.includes("출발") && (raw.includes("혼잡") || raw.includes("도착") || raw.includes("시간전") || raw.includes("시간 전에")))) {
      return {
        text: "**김해공항 항공편 탑승 & 주차 혼잡도 안내** ✈️\n\n결론부터 말씀드리면, **비행기 출발 3시간 전 도착 시 주차와 탑승 수속 모두 여유 있게 진행하실 수 있습니다!** 전혀 늦지 않고 매우 안전한 일정이에요.\n\n• **공항 권장 도착 시간**: 국제선 탑승 시 발권, 수하물 위탁, 보안검색 및 출국심사를 고려해 통상 **출발 2시간 30분 ~ 3시간 전** 도착을 가장 권장합니다.\n• **현재 김해공항 주차 현황**: 현재 총 64면 중 **52면의 빈자리**가 있어 만차 걱정 없이 주차가 가능합니다.\n• **추천 팁**: 국제선 탑승 고객은 **[P2 국제선 구역]**에 주차하시면 국제선 청사 출입구까지 도보로 1~2분 만에 가장 빠르게 이동하실 수 있습니다.",
        card: {
          type: "empty_spots",
          data: {
            lot: { lot_id: "C", name: "김해공항 주차장", total_empty: 52, total_capacity: 64, general_empty: 34, ev_empty: 4, accessible_empty: 14 }
          }
        },
        chips: [
          { id: "lot:C", label: "김해공항 실시간 빈자리" },
          { id: "fee_lot:C", label: "김해공항 주차 요금표" },
          { id: "call_admin", label: "관리실 문의" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }
    
    // 빈자리 질문
    if (raw.includes("empty") || raw.includes("자리") || raw.includes("빈자리")) {
      return {
        text: "현재 전체 주차장에 총 **156면** 중 **122면**의 빈자리가 있습니다.\n\n• **개발원 주차장**: 빈자리 10면 (일반 7 · 전기 2 · 장애인 1)\n• **부산역 주차장**: 빈자리 14면 (일반 10 · 전기 2 · 장애인 2)\n• **김해공항 주차장**: 빈자리 52면 (일반 34 · 전기 4 · 장애인 14)\n• **신세계백화점 주차장**: 빈자리 46면 (일반 35 · 전기 5 · 장애인 6)",
        card: {
          type: "empty_spots",
          data: {
            lots: [
              { lot_id: "A", name: "개발원 주차장", total_empty: 10, total_capacity: 12, general_empty: 7, ev_empty: 2, accessible_empty: 1 },
              { lot_id: "B", name: "부산역 주차장", total_empty: 14, total_capacity: 16, general_empty: 10, ev_empty: 2, accessible_empty: 2 },
              { lot_id: "C", name: "김해공항 주차장", total_empty: 52, total_capacity: 64, general_empty: 34, ev_empty: 4, accessible_empty: 14 },
              { lot_id: "D", name: "신세계백화점 주차장", total_empty: 46, total_capacity: 64, general_empty: 35, ev_empty: 5, accessible_empty: 6 }
            ]
          }
        },
        chips: [
          { id: "find_car", label: "내 차 어디 있어?" },
          { id: "fee_rule", label: "주차 요금표 보기" },
          { id: "call_admin", label: "관리자 호출" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 내 차 찾기
    if (raw.includes("차") || raw.includes("위치") || raw.includes("find_car") || raw.includes("23")) {
      return {
        text: "**12가 3423 (23번)** 차량은 **개발원 주차장 [A-05]**에 주차되어 있어요.\n• 입차 시각: 45분 전\n• 상태: 전기차 충전 구역 정상 주차",
        card: {
          type: "car_location",
          data: {
            car_id: "23",
            plate_number: "12가 3423",
            lot_name: "개발원 주차장",
            space_id: "A-05",
            entry_time: "45분 전 입차",
            parked_minutes: 45
          }
        },
        chips: [
          { id: "fee_car:23", label: "23번 요금 조회하기" },
          { id: "pay_car:23", label: "사전 정산하기" },
          { id: "exit_help", label: "출구 경로 안내" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // [각 주차장 요금 안내 / 주차장별 요금표]: 4개 주차장 전체 요금 기준 안내
    if (
      raw.includes("fee_rule") ||
      raw.includes("각 주차장") ||
      raw.includes("주차장별") ||
      raw.includes("전 주차장 요금") ||
      raw.includes("전체 주차장 요금") ||
      raw.includes("모든 주차장 요금") ||
      raw.includes("4개 주차장") ||
      raw.includes("주차장 4곳") ||
      raw.includes("요금 기준표") ||
      raw.includes("요금표") ||
      (raw.includes("주차장") && raw.includes("요금") && !raw.includes("23") && !raw.includes("차") && !raw.includes("내 차")) ||
      (raw.includes("요금") && (raw.includes("각") || raw.includes("전체") || raw.includes("모든") || raw.includes("기준") || raw.includes("표") || raw.includes("규정")))
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
          { id: "fee_car:23", label: "23번 내 차 요금 조회" },
          { id: "empty_all", label: "🅿️ 주차장 빈자리 확인" },
          { id: "ev_status", label: "⚡ 전기차 충전 현황" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 차량별 요금 조회
    if (raw.includes("요금") || raw.includes("fee") || raw.includes("얼마")) {
      return {
        text: "**12가 3423 (23번)** 차량의 주차 요금은 **1,000원**입니다.\n(45분 이용 / 전기차 50% 감면 혜택 적용됨)",
        card: {
          type: "fee",
          data: {
            car_id: "23",
            plate_number: "12가 3423",
            lot_name: "개발원 주차장",
            space_id: "A-05",
            total_fee: 1000,
            parked_minutes: 45,
            entry_time: "45분 전",
            discount_reason: "전기차 50% 감면"
          }
        },
        chips: [
          { id: "pay_car:23", label: "1,000원 결제하기" },
          { id: "fee_rule", label: "요금 기준표" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 결제 요청
    if (raw.includes("pay") || raw.includes("결제")) {
      if (this.isAlertActive) {
        return {
          text: "🚨 **[결제 불가 안내]** 비상 대피 경보가 발령 중이므로 고객 안전을 위해 결제 및 정산이 전면 차단되었습니다. 출차 차단기는 자동 비상 개방되어 있으니 결제하지 마시고 즉시 비상구로 신속히 대피하세요!",
          card: null,
          chips: [{ id: "evacuate", label: "🏃 비상 대피로 안내" }],
          runtime_id: "local_runtime",
          state_version: this.stateVersion + 1
        };
      }
      return {
        text: "**12가 3423** 차량 요금 **1,000원** 모의 결제를 진행하시겠습니까?",
        card: {
          type: "fee",
          data: {
            car_id: "23",
            plate_number: "12가 3423",
            total_fee: 1000
          }
        },
        confirmation_token: "tok_" + Date.now(),
        chips: [
          { id: "confirm_pay:tok_demo", label: "✅ 결제 승인" },
          { id: "cancel_action", label: "❌ 취소" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 결제 승인 확인
    if (raw.includes("confirm_pay")) {
      if (this.isAlertActive) {
        return {
          text: "🚨 **[결제 불가 안내]** 비상 대피 경보가 발령 중이므로 결제가 차단되었습니다. 즉시 안전한 곳으로 대피하세요!",
          card: null,
          chips: [{ id: "evacuate", label: "🏃 비상 대피로 안내" }],
          runtime_id: "local_runtime",
          state_version: this.stateVersion + 1
        };
      }
      return {
        text: "**주차 요금 정산이 정상 완료되었습니다!** 🎉\n• 승인 번호: PAY-" + Date.now().toString().slice(-8) + "\n• 결제 금액: 1,000원\n\n💡 **출차 안내**: 정산 후 15분 이내에 출차하시면 추가 요금이 부과되지 않습니다.",
        card: {
          type: "payment",
          data: {
            payment_id: "PAY-20261107-" + Math.floor(Math.random()*9000+1000),
            amount: 1000,
            payment_time: new Date().toLocaleTimeString(),
            lot_name: "개발원 주차장",
            exit_deadline: "15분 이내"
          }
        },
        chips: [
          { id: "exit_help", label: "출구 경로 안내" },
          { id: "empty_all", label: "주차장 현황으로" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 관리자 질문 (Q7 버그 방지)
    if (raw.includes("부르면") || raw.includes("돈") || raw.includes("비용")) {
      return {
        text: "**관리자 호출 서비스는 전액 무료입니다.** 💁\n주차 안내, 장애인 편의 지원, 결제 오류 등 도움이 필요하시면 언제든지 부담 없이 호출해 주세요.",
        card: null,
        chips: [
          { id: "call_admin", label: "📞 관리자 호출하기" },
          { id: "empty_all", label: "빈자리 찾기" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 관리자 호출 접수
    if (raw.includes("call_admin") || raw.includes("호출")) {
      return {
        text: "**현장 관리자 호출이 접수되었습니다.** 📞\n접수번호: ADM-" + Date.now().toString().slice(-6) + "\n담당 직원이 3분 이내로 차량 위치로 이동합니다. 잠시만 대기해 주세요.",
        card: {
          type: "admin_call",
          data: {
            call_id: "ADM-" + Date.now().toString().slice(-6),
            lot_name: "개발원 주차장",
            space_id: "A구역",
            status: "배정 완료 (이동 중)"
          }
        },
        chips: [
          { id: "empty_all", label: "주차장 현황 보기" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 🧯 화재 비상 경보 해제 및 상황 종료 (초록색 안전 카드 & 정상 복구 안내)
    if (raw.includes("fire_clear") || raw.includes("clear_alert") || raw.includes("해제") || raw.includes("상황 종료") || raw.includes("상황종료") || raw.includes("진압") || raw.includes("불 꺼") || raw.includes("화재 해결") || raw.includes("안전 확인")) {
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
          { id: "zone:ev", label: "⚡ 전기차 충전 구역" },
          { id: "fee_rule", label: "주차 요금 안내" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 🚨 화재 경보 및 비상 대피 (시연용 모의 화재 LED 경보 & 4대 대피 요령)
    if (!raw.includes("해제") && !raw.includes("종료") && !raw.includes("해결") && !raw.includes("진압") && (raw.includes("evacuate") || raw.includes("화재") || raw.includes("불") || raw.includes("대피") || raw.includes("경보") || raw.includes("연기"))) {
      const nowStr = new Date().toLocaleTimeString();
      return {
        text: "🚨 **[시연용 모의 화재(LED) 비상 경보 발령]** 🚨\n\n현재 시스템상에 감지된 신호는 실제 화재가 아닌 **시연용 모의 화재(LED) 경보** 상태입니다. (발화 모의 위치: **개발원 주차장 [A-05구역]**)\n• 감지 시각: " + nowStr + "\n\n실제 화재는 아니지만 안전 시연 수칙에 따라 안내해 드리오니, 침착하게 양측 비상 유도등을 따라 안전한 대피로로 이동해 주시기 바랍니다.\n(고객 안전을 위해 출차 차단기는 비상 자동 개방되었습니다.)",
        card: {
          type: "alert",
          data: {
            alert_id: "ALT-" + Date.now().toString().slice(-6),
            lot_id: "A",
            lot_name: "개발원 주차장",
            space_id: "A-05구역",
            level: 2,
            message: "시연용 모의 화재(LED) 연기 감지",
            detected_at: nowStr,
            status: "active",
            evacuation_steps: [
              { step: "1단계", title: "비상 유도등 확인", desc: "초록색 비상 유도등(달리는 사람)을 따라 양측 비상구로 신속히 이동하세요." },
              { step: "2단계", title: "호흡기 보호", desc: "젖은 수건이나 옷으로 코와 입을 감싸고, 자세를 최대한 낮추어 이동하세요." },
              { step: "3단계", title: "승강기 탑승 금지", desc: "정전 및 질식 위험이 있으니 엘리베이터는 절대 금지, 비상 계단을 이용하세요." },
              { step: "4단계", title: "차단기 자동 개방", desc: "출차 차단기가 자동 비상 개방되었습니다. 차량 탑승을 멈추고 신속히 보행 탈출하세요." }
            ]
          }
        },
        chips: [
          { id: "view_map:A", label: "🗺️ 화재 위치 도면 확인" },
          { id: "fire_clear", label: "🧯 화재 진압 및 경보 해제" },
          { id: "call_admin", label: "📞 상황실/119 긴급 신고" },
          { id: "empty_all", label: "주차장 현황 보기" }
        ],
        runtime_id: "local_runtime",
        state_version: this.stateVersion + 1
      };
    }

    // 기본 도움말
    return {
      text: "안녕하세요! 스마트 주차 안내 챗봇 **PARK:ON BOT**입니다. 무엇을 도와드릴까요?\n\n아래 추천 버튼을 누르시거나 자유롭게 질문해주세요.",
      card: null,
      chips: [
        { id: "empty_all", label: "빈자리 몇 개 있어?" },
        { id: "zone:ev", label: "전기차 충전 구역" },
        { id: "zone:accessible", label: "장애인 구역" },
        { id: "find_car_23", label: "23번 내 차 찾기" },
        { id: "fee_rule", label: "주차 요금 안내" },
        { id: "call_admin", label: "관리자 호출" }
      ],
      runtime_id: "local_runtime",
      state_version: this.stateVersion + 1
    };
  }
}

window.ParkOnChatSession = ChatSession;

