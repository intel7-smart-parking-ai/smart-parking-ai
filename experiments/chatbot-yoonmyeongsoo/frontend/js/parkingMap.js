// @ts-nocheck
/**
 * PARK:ON 주차장 도면 렌더러 (parkingMap.js)
 * 첨부 사진 4종 (개발원, 부산역, 신세계백화점, 김해공항) 완벽 반영
 */
class ParkingMap {
  constructor(containerElement, initialLot = "A") {
    this.container = containerElement;
    this.currentLot = initialLot;
    this.currentFilter = "all"; // all, empty, ev, accessible
    this.occupancyState = this.getInitialOccupancy();
  }

  getInitialOccupancy() {
    return {
      "A-01": "empty", "A-02": "empty", "A-03": "empty", "A-04": "occupied",
      "A-05": "ev_occupied", "A-06": "ev_empty",
      "B-01": "occupied", "B-02": "occupied", "B-03": "empty", "B-04": "occupied", "B-05": "empty",
      "B-06": "acc_occupied"
    };
  }

  setLot(lotId, highlightSpaceId = null) {
    this.currentLot = lotId;
    this.render();
    if (highlightSpaceId) {
      setTimeout(() => this.highlightSpace(highlightSpaceId), 150);
    }
  }

  highlightSpace(spaceId) {
    if (!spaceId) return;
    const target = this.container.querySelector(`[data-space-id="${spaceId}"]`);
    if (target) {
      target.classList.add("space-highlight-pulse");
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => {
        target.classList.remove("space-highlight-pulse");
      }, 3500);
    }
  }

  setFilter(filter) {
    this.currentFilter = filter;
    this.render();
  }

  render() {
    if (this.currentLot === "A") {
      this.renderDevCenter();
    } else if (this.currentLot === "B") {
      this.renderBusanStation();
    } else if (this.currentLot === "C") {
      this.renderGimhaeAirport();
    } else if (this.currentLot === "D") {
      this.renderShinsegae();
    }
  }

  // 1. 개발원 주차장 도면
  renderDevCenter() {
    this.container.innerHTML = `
      <div class="dev-lot-container">
        <!-- A구역 -->
        <div class="dev-zone-col">
          <div class="zone-badge badge-a">A구역</div>
          ${[1, 2, 3, 4, 5, 6].map(i => {
            const sid = `A-0${i}`;
            const isEv = i >= 5;
            const isEmpty = i <= 3 || i === 6;
            const isMatch = this.matchesFilter(isEmpty, isEv, false);
            const opacity = isMatch ? "1" : "0.3";
            
            let bgClass = "space-item";
            if (isEv) bgClass += " ev-spot";
            else if (isEmpty) bgClass += " empty-spot";
            else bgClass += " occupied";

            return `
              <div class="${bgClass}" style="opacity:${opacity};" data-space-id="${sid}" onclick="window.onSpaceClick('${sid}')">
                <span class="space-side-bar left"></span>
                <span>${sid}</span>
                ${isEv ? '<div class="space-icon-label">⚡ 전기차 전용</div>' : ''}
              </div>
            `;
          }).join("")}
        </div>

        <!-- 중앙 통로 도로 -->
        <div class="center-corridor">
          <div class="road-arrow">↑</div>
          <div class="road-divider"></div>
          <div class="road-arrow">↓</div>
          <div class="road-divider"></div>
          <div class="road-arrow">↑</div>
          <div class="road-divider"></div>
          <div class="road-arrow">↓</div>
        </div>

        <!-- B구역 -->
        <div class="dev-zone-col">
          <div class="zone-badge badge-b">B구역</div>
          ${[1, 2, 3, 4, 5, 6].map(i => {
            const sid = `B-0${i}`;
            const isAcc = i === 6;
            const isEmpty = i === 3 || i === 5;
            const isMatch = this.matchesFilter(isEmpty, false, isAcc);
            const opacity = isMatch ? "1" : "0.3";

            let bgClass = "space-item";
            if (isAcc) bgClass += " acc-spot";
            else if (isEmpty) bgClass += " empty-spot";
            else bgClass += " occupied";

            return `
              <div class="${bgClass}" style="opacity:${opacity};" data-space-id="${sid}" onclick="window.onSpaceClick('${sid}')">
                <span class="space-side-bar right"></span>
                <span>${sid}</span>
                ${isAcc ? '<div class="space-icon-label">♿ 장애인 전용</div>' : ''}
              </div>
            `;
          }).join("")}
        </div>
      </div>
    `;
  }

  // 2. 부산역 주차장 도면 (사선형)
  renderBusanStation() {
    this.container.innerHTML = `
      <div class="busan-lot-container">
        <div style="display:flex; justify-content:space-between; align-items:center;">
          <span style="font-size:24px;" title="비상구">🏃</span>
          <div class="zone-badge badge-a" style="width:120px; margin:0 auto;">A구역</div>
        </div>

        <!-- A행 (사선형) -->
        <div class="busan-row">
          ${[1, 2, 3, 4, 5, 6, 7, 8].map(i => {
            const sid = `A-0${i}`;
            const isEv = i >= 7;
            const isEmpty = i % 2 === 1;
            const isMatch = this.matchesFilter(isEmpty, isEv, false);
            const opacity = isMatch ? "1" : "0.3";
            let bgClass = "space-item";
            if (isEv) bgClass += " ev-spot";
            else if (isEmpty) bgClass += " empty-spot";

            return `
              <div class="${bgClass}" style="opacity:${opacity};" data-space-id="${sid}" onclick="window.onSpaceClick('${sid}')">
                <span>${sid}</span>
                <span class="space-side-bar left"></span>
                ${isEv ? '<div class="space-icon-label" style="font-size:9px;">⚡전기차</div>' : ''}
              </div>
            `;
          }).join("")}
        </div>

        <!-- 중앙 일방 도로 -->
        <div class="road-strip-horizontal">
          <div class="gate-badge">입구</div>
          <span style="font-size:20px; color:#64748b; font-weight:800;">→&nbsp;&nbsp;&nbsp;&nbsp;— — —&nbsp;&nbsp;&nbsp;&nbsp;→</span>
          <div class="gate-badge">출구</div>
        </div>

        <!-- B행 (사선형) -->
        <div class="busan-row">
          ${[1, 2, 3, 4, 5, 6, 7, 8].map(i => {
            const sid = `B-0${i}`;
            const isAcc = i >= 7;
            const isEmpty = i === 2 || i === 4 || i === 6;
            const isMatch = this.matchesFilter(isEmpty, false, isAcc);
            const opacity = isMatch ? "1" : "0.3";
            let bgClass = "space-item";
            if (isAcc) bgClass += " acc-spot";
            else if (isEmpty) bgClass += " empty-spot";

            return `
              <div class="${bgClass}" style="opacity:${opacity};" data-space-id="${sid}" onclick="window.onSpaceClick('${sid}')">
                <span>${sid}</span>
                <span class="space-side-bar left"></span>
                ${isAcc ? '<div class="space-icon-label" style="font-size:9px;">♿장애인</div>' : ''}
              </div>
            `;
          }).join("")}
        </div>

        <div class="zone-badge badge-b" style="width:120px; margin:0 auto;">B구역</div>
      </div>
    `;
  }

  // 3. 신세계백화점 주차장 (4분면)
  renderShinsegae() {
    this.container.innerHTML = `
      <div class="shinsegae-container">
        <div class="shinsegae-top-bar">
          <span>몰 · A / C</span>
          <span>영화관 ↑</span>
          <span>엘리베이터 ‡</span>
          <span>백화점 · B / D</span>
          <span>엘리베이터 ‡</span>
        </div>

        <div class="quadrant-grid">
          <!-- A구역 -->
          <div class="quad-col">
            <div class="zone-badge badge-a">A구역</div>
            <div class="quad-spaces-grid">
              ${Array.from({length: 16}, (_, k) => k + 1).map(i => {
                const sid = `A-${i < 10 ? '0' + i : i}`;
                const isAcc = i >= 5 && i <= 8;
                const isEmpty = i === 1 || i === 2 || i === 9 || i === 12;
                const isMatch = this.matchesFilter(isEmpty, false, isAcc);
                let bgClass = "space-item";
                if (isAcc) bgClass += " acc-spot";
                else if (isEmpty) bgClass += " empty-spot";
                return `<div class="${bgClass}" style="opacity:${isMatch ? 1 : 0.3}; height:50px; font-size:12px;" onclick="window.onSpaceClick('${sid}')">${sid}</div>`;
              }).join("")}
            </div>
          </div>

          <!-- B구역 -->
          <div class="quad-col">
            <div class="zone-badge badge-b">B구역</div>
            <div class="quad-spaces-grid">
              ${Array.from({length: 16}, (_, k) => k + 1).map(i => {
                const sid = `B-${i < 10 ? '0' + i : i}`;
                const isAcc = i >= 5 && i <= 8;
                const isEmpty = i % 3 === 0;
                const isMatch = this.matchesFilter(isEmpty, false, isAcc);
                let bgClass = "space-item";
                if (isAcc) bgClass += " acc-spot";
                else if (isEmpty) bgClass += " empty-spot";
                return `<div class="${bgClass}" style="opacity:${isMatch ? 1 : 0.3}; height:50px; font-size:12px;" onclick="window.onSpaceClick('${sid}')">${sid}</div>`;
              }).join("")}
            </div>
          </div>

          <!-- C구역 -->
          <div class="quad-col">
            <div class="zone-badge badge-c">C구역</div>
            <div class="quad-spaces-grid">
              ${Array.from({length: 16}, (_, k) => k + 1).map(i => {
                const sid = `C-${i < 10 ? '0' + i : i}`;
                const isEv = i >= 5 && i <= 8;
                const isEmpty = i === 3 || i === 4 || i === 11 || i === 15;
                const isMatch = this.matchesFilter(isEmpty, isEv, false);
                let bgClass = "space-item";
                if (isEv) bgClass += " ev-spot";
                else if (isEmpty) bgClass += " empty-spot";
                return `<div class="${bgClass}" style="opacity:${isMatch ? 1 : 0.3}; height:50px; font-size:12px;" onclick="window.onSpaceClick('${sid}')">${sid}</div>`;
              }).join("")}
            </div>
          </div>

          <!-- D구역 -->
          <div class="quad-col">
            <div class="zone-badge badge-d">D구역</div>
            <div class="quad-spaces-grid">
              ${Array.from({length: 16}, (_, k) => k + 1).map(i => {
                const sid = `D-${i < 10 ? '0' + i : i}`;
                const isEv = i >= 5 && i <= 8;
                const isEmpty = i === 1 || i === 8 || i === 14;
                const isMatch = this.matchesFilter(isEmpty, isEv, false);
                let bgClass = "space-item";
                if (isEv) bgClass += " ev-spot";
                else if (isEmpty) bgClass += " empty-spot";
                return `<div class="${bgClass}" style="opacity:${isMatch ? 1 : 0.3}; height:50px; font-size:12px;" onclick="window.onSpaceClick('${sid}')">${sid}</div>`;
              }).join("")}
            </div>
          </div>
        </div>

        <div class="shinsegae-bottom-bar">
          <div class="gate-badge">입구</div>
          <span>몰 연결 출입구</span>
          <span>에스컬레이터 ↑</span>
          <span>🏃 비상구</span>
          <span>백화점 연결 출입구</span>
          <span>에스컬레이터 ↑</span>
          <div class="gate-badge">출구</div>
        </div>
      </div>
    `;
  }

  // 4. 김해공항 주차장 도면
  renderGimhaeAirport() {
    this.container.innerHTML = `
      <div class="airport-container">
        <!-- 상단 여객 청사 3개 -->
        <div class="terminals-row">
          <div class="terminal-box">국내선 여객청사</div>
          <div class="terminal-box">국제선 여객청사</div>
          <div class="terminal-box">국제화물청사</div>
        </div>

        <!-- 셔틀 승하차로 -->
        <div class="shuttle-lane">
          🚍 청사 순환 셔틀 · 승하차로 (도보 2~3분)
        </div>

        <!-- 주차 구역 3분할 -->
        <div class="airport-sections">
          <!-- P1 국내선 24면 -->
          <div class="airport-sec-col">
            <h4>P1 국내선 · 24면</h4>
            <div class="airport-grid">
              ${Array.from({length: 24}, (_, k) => k + 1).map(i => {
                const sid = `A-${i < 10 ? '0' + i : i}`;
                const isAcc = i <= 6;
                const isEv = i === 11 || i === 12;
                const isEmpty = i > 12 || i === 7 || i === 8;
                const isMatch = this.matchesFilter(isEmpty, isEv, isAcc);
                let bgClass = "space-item";
                if (isAcc) bgClass += " acc-spot";
                else if (isEv) bgClass += " ev-spot";
                else if (isEmpty) bgClass += " empty-spot";
                return `<div class="${bgClass}" style="opacity:${isMatch ? 1 : 0.3}; height:44px; font-size:11px;" onclick="window.onSpaceClick('${sid}')">${sid}</div>`;
              }).join("")}
            </div>
          </div>

          <!-- P2 국제선 24면 -->
          <div class="airport-sec-col">
            <h4>P2 국제선 · 24면</h4>
            <div class="airport-grid">
              ${Array.from({length: 24}, (_, k) => k + 1).map(i => {
                const sid = `B-${i < 10 ? '0' + i : i}`;
                const isAcc = i <= 6;
                const isEv = i === 11 || i === 12;
                const isEmpty = i > 12 || i === 9 || i === 10;
                const isMatch = this.matchesFilter(isEmpty, isEv, isAcc);
                let bgClass = "space-item";
                if (isAcc) bgClass += " acc-spot";
                else if (isEv) bgClass += " ev-spot";
                else if (isEmpty) bgClass += " empty-spot";
                return `<div class="${bgClass}" style="opacity:${isMatch ? 1 : 0.3}; height:44px; font-size:11px;" onclick="window.onSpaceClick('${sid}')">${sid}</div>`;
              }).join("")}
            </div>
          </div>

          <!-- P3 여객/화물 16면 -->
          <div class="airport-sec-col">
            <h4>P3 여객·화물 · 16면</h4>
            <div class="airport-grid">
              ${Array.from({length: 16}, (_, k) => k + 25).map(i => {
                const sid = i <= 36 ? `B-${i}` : `C-0${i - 36}`;
                const isAcc = i <= 28;
                const isEv = i === 31 || i === 32;
                const isEmpty = i > 32 || i === 29;
                const isMatch = this.matchesFilter(isEmpty, isEv, isAcc);
                let bgClass = "space-item";
                if (isAcc) bgClass += " acc-spot";
                else if (isEv) bgClass += " ev-spot";
                else if (isEmpty) bgClass += " empty-spot";
                return `<div class="${bgClass}" style="opacity:${isMatch ? 1 : 0.3}; height:44px; font-size:11px;" onclick="window.onSpaceClick('${sid}')">${sid}</div>`;
              }).join("")}
            </div>
          </div>
        </div>

        <!-- 하단 출입구 -->
        <div class="road-strip-horizontal" style="margin-top:10px;">
          <div class="gate-badge">입구</div>
          <span style="font-size:20px; color:#64748b; font-weight:800;">→&nbsp;&nbsp;&nbsp;&nbsp;— — —&nbsp;&nbsp;&nbsp;&nbsp;→</span>
          <div class="gate-badge">출구</div>
        </div>
      </div>
    `;
  }

  matchesFilter(isEmpty, isEv, isAcc) {
    if (this.currentFilter === "all") return true;
    if (this.currentFilter === "empty") return isEmpty;
    if (this.currentFilter === "ev") return isEv;
    if (this.currentFilter === "accessible") return isAcc;
    return true;
  }
}

window.ParkOnParkingMap = ParkingMap;

