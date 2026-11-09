// @ts-nocheck
/**
 * PARK:ON 챗봇 패널 컴포넌트 (chatPanel.js)
 * 7종 풍부한 카드 및 스마트 추천 칩 렌더러
 */
class ChatPanel {
  constructor(containerElement, session) {
    this.container = containerElement;
    this.session = session || new ParkOnChatSession();
    this.messages = [];
    this.renderSkeleton();
    this.bindEvents();
    
    // 초기 환영 메시지 로드
    this.sendInitialGreeting();
  }

  renderSkeleton() {
    this.container.innerHTML = `
      <div class="chat-panel-header">
        <div class="chat-bot-info">
          <div class="chat-avatar">🤖</div>
          <div>
            <div class="chat-bot-name">PARK:ON 챗봇</div>
            <div class="chat-bot-status"><span class="status-indicator"></span> 실시간 안내 중</div>
          </div>
        </div>
        <div class="chat-actions">
          <button class="chat-icon-btn" id="chat-clear-btn" title="대화 지우기">지우기</button>
          <button class="chat-icon-btn danger" id="chat-fire-demo-btn" title="화재 대피 시연">🚨 화재 경보</button>
          <button class="chat-icon-btn safe" id="chat-fire-clear-btn" title="화재 상황 해결 및 경보 해제">🧯 경보 해제</button>
        </div>
      </div>
      <div class="chat-messages-area" id="chat-messages-area"></div>
      <div class="chat-chips-container" id="chat-chips-area"></div>
      <div class="chat-input-container">
        <input type="text" class="chat-input-box" id="chat-input" placeholder="질문을 입력하세요 (예: 23번 차 요금 얼마야?)" />
        <button class="chat-icon-btn" id="chat-mic-btn" title="음성 입력">🎤</button>
        <button class="chat-send-btn" id="chat-send-btn">전송</button>
      </div>
    `;

    this.messagesArea = this.container.querySelector("#chat-messages-area");
    this.chipsArea = this.container.querySelector("#chat-chips-area");
    this.inputBox = this.container.querySelector("#chat-input");
    this.sendBtn = this.container.querySelector("#chat-send-btn");
    this.micBtn = this.container.querySelector("#chat-mic-btn");
  }

  bindEvents() {
    this.sendBtn.addEventListener("click", () => this.handleSendMessage());
    this.inputBox.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) {
        this.handleSendMessage();
      }
    });

    this.container.querySelector("#chat-clear-btn").addEventListener("click", () => {
      this.messages = [];
      this.messagesArea.innerHTML = "";
      this.sendInitialGreeting();
    });

    this.container.querySelector("#chat-fire-demo-btn").addEventListener("click", async () => {
      this.addUserMessage("🚨 [화재 경보 발령] 비상 대피요령 및 화재 위치 안내");
      const loadingElem = this.addLoadingMessage();
      try {
        const res = await this.session.send({ text: "화재 경보 대피", chipId: "evacuate" });
        loadingElem.remove();
        this.addBotMessage(res);
      } catch (e) {
        loadingElem.remove();
        this.addSystemMessage("화재 경보 시스템 연결 중 오류가 발생했습니다.");
      }
    });

    this.container.querySelector("#chat-fire-clear-btn").addEventListener("click", async () => {
      this.addUserMessage("🧯 [상황 종료] 화재 진압 완료 및 안전 경보 해제 요청");
      const loadingElem = this.addLoadingMessage();
      try {
        const res = await this.session.send({ text: "화재 경보 해제", chipId: "fire_clear" });
        loadingElem.remove();
        this.addBotMessage(res);
      } catch (e) {
        loadingElem.remove();
        this.addSystemMessage("경보 해제 시스템 연결 중 오류가 발생했습니다.");
      }
    });

    // 음성 인식 (STT) 지원
    if ("webkitSpeechRecognition" in window || "SpeechRecognition" in window) {
      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      const recognition = new SpeechRecognition();
      recognition.lang = "ko-KR";
      recognition.interimResults = false;

      this.micBtn.addEventListener("click", () => {
        this.micBtn.style.color = "#ef4444";
        recognition.start();
      });

      recognition.onresult = (event) => {
        const transcript = event.results[0][0].transcript;
        this.inputBox.value = transcript;
        this.micBtn.style.color = "";
        this.handleSendMessage();
      };

      recognition.onerror = () => {
        this.micBtn.style.color = "";
      };

      recognition.onend = () => {
        this.micBtn.style.color = "";
      };
    } else {
      this.micBtn.style.display = "none";
    }
  }

  async sendInitialGreeting() {
    const res = await this.session.send({ text: "시작" });
    this.addBotMessage(res);
  }

  async handleSendMessage() {
    const text = this.inputBox.value.trim();
    if (!text) return;

    this.addUserMessage(text);
    this.inputBox.value = "";
    this.inputBox.focus();

    // 로딩 인디케이터
    const loadingElem = this.addLoadingMessage();

    try {
      const res = await this.session.send({ text });
      loadingElem.remove();
      this.addBotMessage(res);
    } catch (err) {
      loadingElem.remove();
      this.addSystemMessage("응답을 가져오는 중 오류가 발생했습니다. 다시 시도해주세요.");
    }
  }

  async handleChipClick(chipId) {
    let chipText = document.querySelector(`[data-chip-id="${chipId}"]`)?.innerText || chipId;
    if (chipId === "evacuate") chipText = "🚨 화재 비상 대피요령 안내";
    if (chipId === "fire_clear" || chipId === "clear_alert") chipText = "🧯 화재 진압 완료 및 안전 경보 해제";
    this.addUserMessage(chipText);

    // [도면 확인 인터랙션 개선] view_map: 클릭 시 즉시 도면 화면으로 전환 및 동기화
    if (chipId.startsWith("view_map:")) {
      const lotId = chipId.split(":")[1];
      if (typeof window.switchParkingLotAndShowMap === "function") {
        window.switchParkingLotAndShowMap(lotId);
      }
    }

    const loadingElem = this.addLoadingMessage();
    try {
      const res = await this.session.send({ chipId });
      loadingElem.remove();
      this.addBotMessage(res);

      // 응답 카드에 view_map_trigger 가 있다면 다시 한 번 화면 전환 확인
      if (res.card && res.card.type === "view_map_trigger" && res.card.data?.lot_id) {
        if (typeof window.switchParkingLotAndShowMap === "function") {
          window.switchParkingLotAndShowMap(res.card.data.lot_id);
        }
      }
    } catch (err) {
      loadingElem.remove();
      this.addSystemMessage("처리 중 오류가 발생했습니다.");
    }
  }

  addUserMessage(text) {
    const row = document.createElement("div");
    row.className = "chat-msg-row user";
    row.innerHTML = `
      <div class="chat-msg-bubble">${this.escapeHtml(text)}</div>
      <div class="chat-msg-time">${this.getCurrentTime()}</div>
    `;
    this.messagesArea.appendChild(row);
    this.scrollToBottom();
  }

  addBotMessage(res) {
    const row = document.createElement("div");
    row.className = "chat-msg-row bot";

    let cardHtml = "";
    if (res.card) {
      cardHtml = this.renderCard(res.card);
    }

    // 마크다운 볼드(**text**) 변환
    let formattedText = this.escapeHtml(res.text || "");
    formattedText = formattedText.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");

    const isAlert = res.card && res.card.type === "alert";
    const isResolved = res.card && (res.card.type === "alert_resolved" || res.card.type === "safe");
    let bubbleStyle = "";
    let avatarIcon = "🤖";

    if (isAlert) {
      bubbleStyle = 'style="background:#fff5f5; border:1.5px solid #f87171; color:#991b1b;"';
      avatarIcon = "🚨";
    } else if (isResolved) {
      bubbleStyle = 'style="background:#f0fdf4; border:1.5px solid #86efac; color:#166534;"';
      avatarIcon = "🛡️";
    }

    row.innerHTML = `
      <div class="chat-avatar" style="width:32px; height:32px; font-size:16px;">${avatarIcon}</div>
      <div style="flex:1;">
        <div class="chat-msg-bubble" ${bubbleStyle}>
          <div>${formattedText}</div>
          ${cardHtml}
        </div>
        <div class="chat-msg-time">${this.getCurrentTime()}</div>
      </div>
    `;
    this.messagesArea.appendChild(row);
    this.renderChips(res.chips || []);
    this.scrollToBottom();
  }

  renderCard(card) {
    const type = card.type;
    const d = card.data || {};

    if (type === "empty_spots") {
      const lots = d.lots || (d.lot ? [d.lot] : []);
      return `
        <div class="chat-card">
          <div class="card-header">
            <span>주차장별 실시간 잔여석</span>
            <span class="card-badge blue">실시간</span>
          </div>
          ${lots.map(l => `
            <div style="margin-bottom:10px; padding-bottom:8px; border-bottom:1px dashed #e2e8f0;">
              <div style="display:flex; justify-content:space-between; font-weight:700; font-size:13px; align-items:center;">
                <span>${l.name}</span>
                <span style="color:#2563eb; font-weight:800;">빈자리 ${l.total_empty} / ${l.total_capacity}면</span>
              </div>
              <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px;">
                <div style="display:flex; gap:10px; font-size:11px; color:#64748b;">
                  <span>일반 ${l.general_empty}</span>
                  <span>⚡전기 ${l.ev_empty}</span>
                  <span>♿장애인 ${l.accessible_empty}</span>
                </div>
                <button class="card-map-action-btn" onclick="window.switchParkingLotAndShowMap('${l.lot_id}')">🗺️ 도면 확인</button>
              </div>
            </div>
          `).join("")}
        </div>
      `;
    }

    if (type === "ev_charging") {
      const chargers = d.chargers || [];
      const gridId = "ev-grid-" + Math.random().toString(36).substr(2, 6);
      
      const availableCnt = chargers.filter(c => c.status === "available").length;
      const chargingCnt = chargers.filter(c => c.status === "charging").length;
      const completedCnt = chargers.filter(c => c.status === "completed").length;

      return `
        <div class="chat-card ev-card">
          <!-- 헤더: 타이틀 & 현황 요약 칩 -->
          <div class="ev-card-header">
            <div class="ev-header-title">
              <span>⚡ 전기차 충전 현황 관제</span>
            </div>
            <div class="ev-summary-chips">
              <span class="ev-stat-badge available">🟢 빈자리 ${availableCnt}대</span>
              <span class="ev-stat-badge charging">⚡ 충전 중 ${chargingCnt}대</span>
              ${completedCnt > 0 ? `<span class="ev-stat-badge completed">✨ 완충 ${completedCnt}대</span>` : ''}
            </div>
          </div>

          <!-- 주차장별 빠른 퀵 필터 탭 -->
          <div class="ev-filter-tabs">
            <button class="ev-filter-btn active" onclick="window.filterEvChargers('${gridId}', 'ALL', this)">전체보기 (${chargers.length})</button>
            <button class="ev-filter-btn" onclick="window.filterEvChargers('${gridId}', 'A', this)">개발원</button>
            <button class="ev-filter-btn" onclick="window.filterEvChargers('${gridId}', 'B', this)">부산역</button>
            <button class="ev-filter-btn" onclick="window.filterEvChargers('${gridId}', 'C', this)">김해공항</button>
            <button class="ev-filter-btn" onclick="window.filterEvChargers('${gridId}', 'D', this)">신세계</button>
          </div>

          <!-- 충전기 2열/반응형 일목요연 정렬 그리드 -->
          <div class="ev-chargers-grid" id="${gridId}">
            ${chargers.map(c => {
              const isCharging = c.status === "charging";
              const isCompleted = c.status === "completed";
              const isAvailable = c.status === "available";
              const percent = c.battery_percent || 0;
              const barColor = isCompleted ? "#9333ea" : (percent >= 80 ? "#16a34a" : "#22c55e");
              const cardClass = isCharging ? "is-charging" : (isCompleted ? "is-completed" : "is-available");

              return `
                <div class="ev-charger-item ${cardClass}" data-lot="${c.lot_id}">
                  <!-- 카드 상단: 위치 & 충전기 사양 & 상태 태그 -->
                  <div class="ev-item-head">
                    <div class="ev-item-title">
                      <span class="ev-lot-name">📍 ${c.lot_name.replace(" 주차장", "")} [${c.space_id}]</span>
                      <span class="ev-type-tag">${c.charger_type || '급속'}</span>
                    </div>
                    <span class="ev-status-tag ${c.status}">
                      ${isCharging ? '⚡ 충전 중' : (isCompleted ? '✨ 완충 완료' : '🟢 즉시 가능')}
                    </span>
                  </div>

                  <!-- 카드 중앙 본문 영역 -->
                  <div class="ev-item-body">
                    ${isAvailable ? `
                      <!-- [즉시 가능 칸] 세련된 에코 스테이션 디자인 -->
                      <div class="ev-available-box">
                        <div class="ev-available-badge">
                          <span>🟢 즉시 충전 가능</span>
                        </div>
                        <div class="ev-available-desc">
                          대기 차량 없음 · <strong>${c.charger_type || '급속'}</strong> 즉시 이용 가능
                        </div>
                      </div>
                    ` : `
                      <!-- [충전 중 칸] 모던 EV 관제 지표 -->
                      <div class="ev-charging-box">
                        <div class="ev-body-row">
                          <div class="ev-car-info">
                            <span class="ev-car-plate">차량 ${c.plate_number || '미확인'}</span>
                            <span class="ev-car-sub">${c.car_id ? c.car_id + '번 차량' : ''} · 실시간 충전 중</span>
                          </div>
                          <div class="ev-battery-pct" style="color: ${isCompleted ? '#7e22ce' : '#059669'};">
                            ${percent}%
                          </div>
                        </div>
                        <div class="ev-progress-track">
                          <div class="ev-progress-fill ${isCompleted ? 'completed' : ''}" style="width: ${percent}%;"></div>
                        </div>
                        <div class="ev-sub-info">
                          <span>⚡ 출력: <strong>${c.current_speed_kw ? c.current_speed_kw + 'kW' : '관제 중'}</strong></span>
                          <span class="ev-remain-time ${c.remaining_minutes <= 10 && !isCompleted ? 'urgent' : 'normal'}">
                            ${isCompleted ? '✨ 출차 대기 중' : `⏱️ 완충까지 약 ${c.remaining_minutes}분 남음`}
                          </span>
                        </div>
                      </div>
                    `}
                  </div>

                  <!-- [주차자리 도면 보기 칸] 프리미엄 원클릭 CTA 버튼 -->
                  <div class="ev-item-actions">
                    <button class="card-map-action-btn" onclick="window.switchParkingLotAndShowMap('${c.lot_id}', '${c.space_id}')">
                      🗺️ ${c.space_id} 주차칸 위치 도면 보기
                    </button>
                  </div>
                </div>
              `;
            }).join("")}
          </div>
        </div>
      `;
    }

    if (type === "view_map_trigger") {
      return `
        <div class="chat-card" style="border: 2px solid #3b82f6; background:#eff6ff; text-align:center;">
          <div style="font-weight:700; color:#1e40af; font-size:14px; margin-bottom:6px;">
            🗺️ ${d.lot_name} 주차 도면이 열렸습니다
          </div>
          <p style="font-size:12px; color:#3b82f6; margin-bottom:8px;">
            화면 상단의 도면 영역에서 실시간 배치도를 확인하세요.
          </p>
          <button class="card-map-action-btn" style="padding:6px 16px; font-size:13px;" onclick="window.switchParkingLotAndShowMap('${d.lot_id}')">
            도면 영역으로 화면 스크롤
          </button>
        </div>
      `;
    }

    if (type === "car_location") {
      return `
        <div class="chat-card">
          <div class="card-header">
            <span>차량 위치 안내</span>
            <span class="card-badge green">주차 중</span>
          </div>
          <div class="card-content-row">
            <span class="label">차량 번호</span>
            <span class="val">${d.plate_number || d.car_id}</span>
          </div>
          <div class="card-content-row">
            <span class="label">주차 위치</span>
            <span class="val" style="color:#2563eb; font-weight:800;">${d.lot_name} [${d.space_id}]</span>
          </div>
          <div class="card-content-row">
            <span class="label">입차 시각</span>
            <span class="val">${d.entry_time}</span>
          </div>
        </div>
      `;
    }

    if (type === "fee_rule") {
      const lots = d.lots || (d.lot ? [d.lot] : []);
      return `
        <div class="chat-card fee-rule-card">
          <div class="card-header">
            <span>주차장별 요금 기준표</span>
            <span class="card-badge blue">요금 안내</span>
          </div>
          ${lots.map(l => {
            const rule = l.fee_rule || l;
            return `
              <div style="margin-bottom:10px; padding-bottom:8px; border-bottom:1px dashed #e2e8f0;">
                <div style="display:flex; justify-content:space-between; font-weight:700; font-size:13px; align-items:center;">
                  <span>${l.name}</span>
                  <span style="color:#2563eb; font-weight:800;">기본 ${rule.base_min}분 ${(rule.base_fee || 0).toLocaleString()}원</span>
                </div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px; font-size:11px; color:#64748b;">
                  <span>추가 ${rule.unit_min}분당 ${(rule.unit_fee || 0).toLocaleString()}원 · 일 최대 ${(rule.daily_max || 0).toLocaleString()}원</span>
                  <span style="color:#10b981; font-weight:600;">회차 ${rule.free_min}분 무료</span>
                </div>
              </div>
            `;
          }).join("")}
          <div style="font-size:11px; color:#64748b; margin-top:4px;">
            ※ 친환경 전기차 및 장애인 등록 차량 50% 자동 감면
          </div>
        </div>
      `;
    }

    if (type === "fee") {
      const isPaid = d.paid;
      return `
        <div class="chat-card">
          <div class="card-header">
            <span>주차 요금 정산</span>
            <span class="card-badge ${isPaid ? 'green' : 'blue'}">${isPaid ? '정산 완료' : '미정산'}</span>
          </div>
          <div class="card-content-row">
            <span class="label">차량 번호</span>
            <span class="val">${d.plate_number || d.car_id}</span>
          </div>
          <div class="card-content-row">
            <span class="label">주차 시간</span>
            <span class="val">${d.parked_minutes}분</span>
          </div>
          ${d.discount ? `
            <div class="card-content-row" style="color:#16a34a;">
              <span class="label">감면 혜택</span>
              <span class="val">-${d.discount.toLocaleString()}원 (${d.discount_reason})</span>
            </div>
          ` : ""}
          <div class="card-content-row total-fee">
            <span class="label">최종 결제 금액</span>
            <span class="val">${(d.total_fee || 0).toLocaleString()}원</span>
          </div>
        </div>
      `;
    }

    if (type === "payment") {
      return `
        <div class="chat-card" style="border: 2px solid #22c55e; background:#f0fdf4;">
          <div class="card-header">
            <span>모의 결제 영수증</span>
            <span class="card-badge green">정산 완료</span>
          </div>
          <div class="card-content-row">
            <span class="label">승인 번호</span>
            <span class="val" style="font-family:monospace;">${d.payment_id}</span>
          </div>
          <div class="card-content-row">
            <span class="label">결제 금액</span>
            <span class="val" style="font-size:15px; color:#15803d;">${(d.amount || 0).toLocaleString()}원</span>
          </div>
          <div class="card-content-row">
            <span class="label">결제 일시</span>
            <span class="val">${d.payment_time}</span>
          </div>
          <div class="card-content-row" style="margin-top:6px; padding-top:6px; border-top:1px dashed #bbf7d0;">
            <span class="label">무료 출차 기한</span>
            <span class="val" style="color:#b91c1c; font-weight:700;">${d.exit_deadline}까지</span>
          </div>
        </div>
      `;
    }

    if (type === "alert") {
      const steps = d.evacuation_steps || [
        { step: "1단계", title: "비상 유도등 확인", desc: "초록색 비상 유도등(달리는 사람)을 따라 양측 비상구로 신속히 이동하세요." },
        { step: "2단계", title: "호흡기 보호", desc: "젖은 수건이나 옷으로 코와 입을 감싸고 낮은 자세를 유지하세요." },
        { step: "3단계", title: "승강기 탑승 금지", desc: "엘리베이터는 절대 타지 마시고 비상 계단을 이용하세요." },
        { step: "4단계", title: "차단기 자동 개방", desc: "모든 차단기가 비상 개방되었습니다. 차량을 두고 신속히 보행 탈출하세요." }
      ];

      const cleanSpaceId = (d.space_id || 'A-05').replace('구역', '').trim();

      return `
        <div class="chat-card alert-card">
          <!-- 헤더: 긴급 경보 타이틀 & 레벨 뱃지 -->
          <div class="alert-card-header">
            <div class="alert-card-title">
              <span>🚨 긴급 화재 발생 경보</span>
            </div>
            <span class="alert-level-badge">Lv.${d.level || 2} 비상사태</span>
          </div>

          <!-- 화재 발생 위치 강조 붉은색 박스 -->
          <div class="alert-loc-box">
            <div>
              <div style="font-size: 11px; color: #b91c1c; font-weight: 700; margin-bottom: 2px;">발화 감지 위치</div>
              <div class="alert-loc-text">📍 ${d.lot_name} [${d.space_id || 'A-05구역'}]</div>
            </div>
            <div style="text-align: right;">
              <span class="alert-time-text">⏱️ ${d.detected_at || '방금 전'}</span>
              <div style="font-size: 10px; color: #b91c1c; font-weight: 700; margin-top: 2px;">차단기 비상 자동개방됨</div>
            </div>
          </div>

          <!-- 4대 긴급 대피요령 체크리스트 -->
          <div class="alert-evac-box">
            <div class="alert-evac-head">
              <span>🏃 현장 긴급 비상 대피요령 (행동 수칙)</span>
            </div>
            <div class="alert-evac-list">
              ${steps.map(s => `
                <div class="alert-evac-item">
                  <span class="alert-step-tag">${s.step}</span>
                  <div>
                    <strong style="color: #991b1b; font-size: 12px;">${s.title}</strong>:
                    <span>${s.desc}</span>
                  </div>
                </div>
              `).join("")}
            </div>
          </div>

          <!-- 원클릭 도면 이동 & 긴급 전화 & 경보 해제 액션 버튼 -->
          <div class="alert-action-group">
            <button class="alert-map-btn" onclick="window.switchParkingLotAndShowMap('${d.lot_id || 'A'}', '${cleanSpaceId}');">
              🗺️ 화재 위치 & 비상 대피로 도면 확인
            </button>
            <button class="alert-call-btn" onclick="alert('📞 긴급 상황실(051-120) 및 119 소방 종합상황실로 긴급 연결합니다.');">
              📞 119 연결
            </button>
            <button class="alert-clear-btn" onclick="document.getElementById('chat-fire-clear-btn')?.click();" title="화재 진압 완료 후 안전 경보 해제">
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
        <div class="chat-card resolved-card">
          <!-- 헤더: 상황 종료 타이틀 & 안전 뱃지 -->
          <div class="resolved-card-header">
            <div class="resolved-card-title">
              <span>✅ 화재 진압 및 비상 경보 해제</span>
            </div>
            <span class="resolved-badge">정상 운영 재개</span>
          </div>

          <!-- 현장 안전 확인 위치 박스 -->
          <div class="resolved-loc-box">
            <div>
              <div style="font-size: 11px; color: #166534; font-weight: 700; margin-bottom: 2px;">안전 점검 완료 구역</div>
              <div class="resolved-loc-text">📍 ${d.lot_name || '개발원 주차장'} [${d.space_id || 'A-05구역'}]</div>
            </div>
            <div style="text-align: right;">
              <span class="resolved-time-text">⏱️ ${d.resolved_at || '방금 전'}</span>
              <div style="font-size: 10px; color: #15803d; font-weight: 700; margin-top: 2px;">안전 확인 완료</div>
            </div>
          </div>

          <!-- 4대 안전 점검 및 조치 결과 -->
          <div class="resolved-check-box">
            <div class="resolved-check-head">
              <span>🛡️ 현장 안전 점검 및 조치 결과 (정상 가동 승인)</span>
            </div>
            <div class="resolved-check-list">
              ${checks.map(c => `
                <div class="resolved-check-item">
                  <span class="resolved-check-icon">✓</span>
                  <div>
                    <strong style="color: #14532d; font-size: 12px;">${c.title}</strong>:
                    <span style="color: #166534; font-size: 11px;">${c.desc}</span>
                  </div>
                </div>
              `).join("")}
            </div>
          </div>

          <!-- 원클릭 도면 확인 & 빈자리 확인 액션 버튼 -->
          <div class="resolved-action-group">
            <button class="resolved-map-btn" onclick="window.switchParkingLotAndShowMap('${d.lot_id || 'A'}');">
              🗺️ 정상 주차장 도면 확인
            </button>
            <button class="resolved-empty-btn" onclick="document.querySelector('[data-chip-id=\\'empty_all\\']')?.click();">
              🚗 빈자리 확인하기
            </button>
          </div>
        </div>
      `;
    }

    if (type === "admin_call") {
      return `
        <div class="chat-card">
          <div class="card-header">
            <span>관리자 호출 접수</span>
            <span class="card-badge purple">접수 완료</span>
          </div>
          <div class="card-content-row">
            <span class="label">접수 번호</span>
            <span class="val">${d.call_id}</span>
          </div>
          <div class="card-content-row">
            <span class="label">위치</span>
            <span class="val">${d.lot_name} (${d.space_id})</span>
          </div>
          <div class="card-content-row">
            <span class="label">진행 상황</span>
            <span class="val" style="color:#7c3aed;">${d.status}</span>
          </div>
        </div>
      `;
    }

    return "";
  }

  renderChips(chips) {
    this.chipsArea.innerHTML = "";
    chips.forEach(c => {
      const btn = document.createElement("button");
      btn.className = "chip-btn";
      btn.setAttribute("data-chip-id", c.id);
      btn.innerText = c.label;
      btn.addEventListener("click", () => this.handleChipClick(c.id));
      this.chipsArea.appendChild(btn);
    });
  }

  addLoadingMessage() {
    const row = document.createElement("div");
    row.className = "chat-msg-row bot";
    row.innerHTML = `
      <div class="chat-avatar" style="width:32px; height:32px; font-size:16px;">🤖</div>
      <div class="chat-msg-bubble" style="color:#94a3b8; font-style:italic;">답변을 준비하고 있어요...</div>
    `;
    this.messagesArea.appendChild(row);
    this.scrollToBottom();
    return row;
  }

  addSystemMessage(text) {
    const row = document.createElement("div");
    row.className = "chat-msg-row bot";
    row.innerHTML = `<div class="chat-msg-bubble" style="background:#fee2e2; color:#991b1b;">${this.escapeHtml(text)}</div>`;
    this.messagesArea.appendChild(row);
    this.scrollToBottom();
  }

  scrollToBottom() {
    this.messagesArea.scrollTop = this.messagesArea.scrollHeight;
  }

  getCurrentTime() {
    const now = new Date();
    return now.getHours().toString().padStart(2, "0") + ":" + now.getMinutes().toString().padStart(2, "0");
  }

  escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
}

window.ParkOnChatPanel = ChatPanel;

// 전기차 충전기 주차장별 빠른 필터 전역 함수
window.filterEvChargers = function(gridId, targetLot, btnEl) {
  const container = document.getElementById(gridId);
  if (!container) return;
  const items = container.querySelectorAll(".ev-charger-item");
  items.forEach(item => {
    const itemLot = item.getAttribute("data-lot");
    if (targetLot === "ALL" || itemLot === targetLot) {
      item.style.display = "flex";
    } else {
      item.style.display = "none";
    }
  });
  if (btnEl) {
    const parent = btnEl.parentElement;
    parent.querySelectorAll(".ev-filter-btn").forEach(b => b.classList.remove("active"));
    btnEl.classList.add("active");
  }
};

