// @ts-nocheck
/**
 * PARK:ON 메인 애플리케이션 진입점 (app.js)
 */
document.addEventListener("DOMContentLoaded", () => {
  const lotSelector = document.getElementById("lot-selector");
  const lotTitle = document.getElementById("lot-title");
  const navTabs = document.querySelectorAll(".nav-tab-btn");
  const filterBtns = document.querySelectorAll(".filter-btn");
  const mapPanel = document.getElementById("map-panel");
  const chatbotScreen = document.getElementById("chatbot-screen");
  const mapContainer = document.getElementById("parking-map-container");

  // 플로팅 위젯 버튼 및 윈도우
  const floatingBtn = document.getElementById("floating-chat-btn");
  const floatingWindow = document.getElementById("floating-chat-window");
  const floatingCloseBtn = document.getElementById("floating-chat-close");

  // 세션 & 도면 & 챗봇 인스턴스 생성
  const session = new ParkOnChatSession();
  const parkingMap = new ParkOnParkingMap(mapContainer, "A");
  parkingMap.render();

  // 메인 화면 챗봇 패널
  const mainChatPanel = new ParkOnChatPanel(
    document.getElementById("main-chatbot-container"),
    session
  );

  // 플로팅 위젯 챗봇 패널
  const floatingChatPanel = new ParkOnChatPanel(
    document.getElementById("floating-chatbot-container"),
    session
  );

  // 주차장별 빈자리 요약 수치 데이터
  const lotSummaries = {
    A: { name: "개발원 주차장", total: "12면", gen: "8면", ev: "2면", acc: "1면" },
    B: { name: "부산역 주차장", total: "16면", gen: "12면", ev: "2면", acc: "2면" },
    C: { name: "김해공항 주차장", total: "64면", gen: "40면", ev: "5면", acc: "15면" },
    D: { name: "신세계 백화점 주차장", total: "64면", gen: "46면", ev: "7면", acc: "7면" }
  };

  function updateSummary(lotId) {
    const s = lotSummaries[lotId] || lotSummaries.A;
    lotTitle.innerText = s.name;
    document.getElementById("stat-total").innerText = s.total;
    document.getElementById("stat-gen").innerText = s.gen;
    document.getElementById("stat-ev").innerText = s.ev;
    document.getElementById("stat-acc").innerText = s.acc;
  }

  // 주차장 변경 및 도면 화면으로 즉시 전환하는 글로벌 함수 (특정 칸 하이라이트 지원)
  window.switchParkingLotAndShowMap = function(lotId, spaceId = null) {
    const targetLot = lotId || "A";
    
    // 1. 셀렉터 변경
    if (lotSelector) {
      lotSelector.value = targetLot;
    }
    updateSummary(targetLot);
    parkingMap.setLot(targetLot, spaceId);

    // 2. 상단 탭 전환: [주차장 현황 (map)] 활성화
    navTabs.forEach(t => t.classList.remove("active"));
    const mapTab = document.querySelector('.nav-tab-btn[data-tab="map"]');
    if (mapTab) mapTab.classList.add("active");

    mapPanel.style.display = "block";
    chatbotScreen.classList.remove("active");

    // 3. 화면 스크롤 부드럽게 이동
    mapPanel.scrollIntoView({ behavior: "smooth", block: "start" });

    // 4. 도면 패널 강조 애니메이션
    mapPanel.style.boxShadow = "0 0 0 4px rgba(37, 99, 235, 0.4)";
    setTimeout(() => {
      mapPanel.style.boxShadow = "";
    }, 1500);

    console.log(`✅ [PARK:ON] 주차장 ${targetLot} 도면으로 성공적으로 전환되었습니다. (선택 칸: ${spaceId || '전체'})`);
  };

  // 주차장 셀렉터 변경 시
  lotSelector.addEventListener("change", (e) => {
    const lotId = e.target.value;
    updateSummary(lotId);
    parkingMap.setLot(lotId);
  });

  // 필터 버튼 클릭 시
  filterBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      filterBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const filter = btn.getAttribute("data-filter");
      parkingMap.setFilter(filter);
    });
  });

  // 상단 네비게이션 탭 클릭 시
  navTabs.forEach(tab => {
    tab.addEventListener("click", () => {
      navTabs.forEach(t => t.classList.remove("active"));
      tab.classList.add("active");

      const tabTarget = tab.getAttribute("data-tab");
      if (tabTarget === "map") {
        mapPanel.style.display = "block";
        chatbotScreen.classList.remove("active");
      } else if (tabTarget === "chatbot") {
        mapPanel.style.display = "none";
        chatbotScreen.classList.add("active");
      } else if (tabTarget === "find_car") {
        mapPanel.style.display = "none";
        chatbotScreen.classList.add("active");
        mainChatPanel.handleChipClick("find_car");
      } else if (tabTarget === "fee") {
        mapPanel.style.display = "none";
        chatbotScreen.classList.add("active");
        mainChatPanel.handleChipClick("fee");
      } else if (tabTarget === "safety") {
        mapPanel.style.display = "none";
        chatbotScreen.classList.add("active");
        mainChatPanel.handleChipClick("evacuate");
      }
    });
  });

  // 플로팅 챗봇 열기/닫기
  if (floatingBtn && floatingWindow) {
    floatingBtn.addEventListener("click", () => {
      const isHidden = floatingWindow.style.display === "none" || !floatingWindow.style.display;
      floatingWindow.style.display = isHidden ? "flex" : "none";
    });

    floatingCloseBtn.addEventListener("click", () => {
      floatingWindow.style.display = "none";
    });
  }

  // 칸 클릭 팝업
  window.onSpaceClick = function(spaceId) {
    let msg = `[${spaceId}] 주차칸 정보를 확인하시겠습니까?`;
    if (confirm(msg + "\n챗봇에게 이 칸과 차량 정보를 질문할까요?")) {
      navTabs.forEach(t => t.classList.remove("active"));
      document.querySelector('[data-tab="chatbot"]').classList.add("active");
      mapPanel.style.display = "none";
      chatbotScreen.classList.add("active");
      mainChatPanel.inputBox.value = `${spaceId}번 주차칸 상태 알려줘`;
      mainChatPanel.handleSendMessage();
    }
  };
});

