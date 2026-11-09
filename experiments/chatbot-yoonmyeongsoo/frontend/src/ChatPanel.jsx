// @ts-nocheck
import React, { useState, useEffect, useRef } from "react";
import { ChatSession } from "../js/chatSession";

/**
 * PARK:ON React 챗봇 컴포넌트 (ChatPanel.jsx)
 * 운영 웹사이트 frontend/src/ 에 바로 탑재 가능한 React 컴포넌트
 */
export default function ChatPanel({ onClose, initialPrompt = null }) {
  const [session] = useState(() => new ChatSession());
  const [messages, setMessages] = useState([]);
  const [chips, setChips] = useState([]);
  const [inputText, setInputText] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    // 최초 인사말 요청
    const initChat = async () => {
      setIsLoading(true);
      const res = await session.send({ text: initialPrompt || "시작" });
      setIsLoading(false);
      handleBotResponse(res);
    };
    initChat();
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isLoading]);

  const handleBotResponse = (res) => {
    setMessages((prev) => [
      ...prev,
      {
        sender: "bot",
        text: res.text,
        card: res.card,
        time: getCurrentTime(),
      },
    ]);
    if (res.chips) {
      setChips(res.chips);
    }
  };

  const handleSend = async (textToSend = null, chipId = null) => {
    const text = textToSend || inputText.trim();
    if (!text && !chipId) return;

    // 도면 확인 액션 인터랙션 감지
    if (chipId && chipId.startsWith("view_map:")) {
      const lotId = chipId.split(":")[1];
      if (typeof window.switchParkingLotAndShowMap === "function") {
        window.switchParkingLotAndShowMap(lotId);
      }
    }

    if (text) {
      setMessages((prev) => [
        ...prev,
        { sender: "user", text, time: getCurrentTime() },
      ]);
      setInputText("");
    }

    setIsLoading(true);
    try {
      const res = await session.send({ text: chipId ? null : text, chipId });
      handleBotResponse(res);
      if (res.card && res.card.type === "view_map_trigger" && res.card.data?.lot_id) {
        if (typeof window.switchParkingLotAndShowMap === "function") {
          window.switchParkingLotAndShowMap(res.card.data.lot_id);
        }
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          sender: "bot",
          text: "안내를 불러오는 중 오류가 발생했습니다. 다시 시도해 주세요.",
          time: getCurrentTime(),
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  const getCurrentTime = () => {
    const d = new Date();
    return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
  };

  return (
    <div className="chat-panel-root" style={styles.container}>
      {/* 헤더 */}
      <div style={styles.header}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={styles.avatar}>🤖</div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 15 }}>PARK:ON 챗봇</div>
            <div style={{ fontSize: 11, color: "#10b981" }}>● 실시간 스마트 안내</div>
          </div>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <button
            onClick={() => handleSend("화재 경보 대피", "evacuate")}
            style={{ padding: "4px 8px", background: "#fee2e2", color: "#dc2626", border: "1px solid #f87171", borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer" }}
            title="화재 경보 시연"
          >
            🚨 화재 경보
          </button>
          <button
            onClick={() => handleSend("화재 경보 해제", "fire_clear")}
            style={{ padding: "4px 8px", background: "#ecfdf5", color: "#059669", border: "1px solid #10b981", borderRadius: 6, fontSize: 11, fontWeight: 700, cursor: "pointer" }}
            title="화재 상황 해결 및 경보 해제"
          >
            🧯 경보 해제
          </button>
          {onClose && (
            <button onClick={onClose} style={styles.closeBtn}>
              ✕
            </button>
          )}
        </div>
      </div>

      {/* 대화 스크롤 영역 */}
      <div style={styles.messagesArea}>
        {messages.map((m, idx) => (
          <div
            key={idx}
            style={{
              ...styles.msgRow,
              alignSelf: m.sender === "user" ? "flex-end" : "flex-start",
              flexDirection: m.sender === "user" ? "row-reverse" : "row",
            }}
          >
            {m.sender === "bot" && <div style={styles.botAvatarSmall}>🤖</div>}
            <div style={{ maxWidth: "82%" }}>
              <div
                style={{
                  ...styles.bubble,
                  background: m.sender === "user" ? "#2563eb" : "#ffffff",
                  color: m.sender === "user" ? "#ffffff" : "#1e293b",
                  border: m.sender === "user" ? "none" : "1px solid #e2e8f0",
                }}
              >
                <div style={{ whiteSpace: "pre-line", wordBreak: "break-word" }}>
                  {m.text}
                </div>
                {m.card && renderCard(m.card, handleSend)}
              </div>
              <div style={styles.msgTime}>{m.time}</div>
            </div>
          </div>
        ))}

        {isLoading && (
          <div style={{ ...styles.msgRow, alignSelf: "flex-start" }}>
            <div style={styles.botAvatarSmall}>🤖</div>
            <div style={{ ...styles.bubble, background: "#ffffff", color: "#94a3b8", fontStyle: "italic" }}>
              답변을 준비하고 있어요...
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* 칩 추천 영역 */}
      <div style={styles.chipsArea}>
        {chips.map((c) => (
          <button
            key={c.id}
            onClick={() => handleSend(c.label, c.id)}
            style={styles.chipBtn}
          >
            {c.label}
          </button>
        ))}
      </div>

      {/* 입력 바 */}
      <div style={styles.inputBar}>
        <input
          type="text"
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && handleSend()}
          placeholder="질문을 입력하세요 (예: 23번 요금 얼마야?)"
          style={styles.inputBox}
        />
        <button onClick={() => handleSend()} style={styles.sendBtn}>
          전송
        </button>
      </div>
    </div>
  );
}

// 7종 카드 컴포넌트 렌더러
function renderCard(card, onAction) {
  const { type, data } = card;
  if (!data) return null;

  if (type === "empty_spots") {
    const lots = data.lots || (data.lot ? [data.lot] : []);
    return (
      <div style={styles.cardBox}>
        <div style={styles.cardTitle}>주차장 잔여석 현황</div>
        {lots.map((l) => (
          <div key={l.lot_id} style={styles.cardItem}>
            <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 700, alignItems: "center" }}>
              <span>{l.name}</span>
              <span style={{ color: "#2563eb" }}>{l.total_empty} / {l.total_capacity}면</span>
            </div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
              <div style={{ fontSize: 11, color: "#64748b" }}>
                일반 {l.general_empty} · ⚡전기 {l.ev_empty} · ♿장애인 {l.accessible_empty}
              </div>
              <button
                onClick={() => {
                  if (typeof window.switchParkingLotAndShowMap === "function") {
                    window.switchParkingLotAndShowMap(l.lot_id);
                  }
                }}
                style={{ padding: "3px 8px", fontSize: 11, background: "#eff6ff", color: "#2563eb", border: "1px solid #bfdbfe", borderRadius: 4, cursor: "pointer", fontWeight: 700 }}
              >
                🗺️ 도면 확인
              </button>
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (type === "ev_charging") {
    const chargers = data.chargers || [];
    const availableCnt = chargers.filter(c => c.status === "available").length;
    const chargingCnt = chargers.filter(c => c.status === "charging").length;
    const completedCnt = chargers.filter(c => c.status === "completed").length;

    return (
      <div style={{ ...styles.cardBox, maxWidth: 760, border: "1.5px solid #86efac", background: "linear-gradient(180deg, #f0fdf4 0%, #f6fef9 100%)", padding: "12px 14px", borderRadius: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid #bbf7d0", paddingBottom: 6, marginBottom: 8 }}>
          <div style={{ fontWeight: 800, color: "#15803d", fontSize: 13 }}>⚡ 전기차 충전 현황 관제</div>
          <div style={{ display: "flex", gap: 4 }}>
            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 10, background: "#dcfce7", color: "#166534", fontWeight: 700 }}>빈자리 {availableCnt}</span>
            <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 10, background: "#fef3c7", color: "#92400e", fontWeight: 700 }}>충전 중 {chargingCnt}</span>
            {completedCnt > 0 && <span style={{ fontSize: 10, padding: "2px 6px", borderRadius: 10, background: "#f3e8ff", color: "#6b21a8", fontWeight: 700 }}>완충 {completedCnt}</span>}
          </div>
        </div>

        {/* 2열 반응형 그리드 정렬 */}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 8 }}>
          {chargers.map((c) => {
            const isCharging = c.status === "charging";
            const isCompleted = c.status === "completed";
            const isAvailable = c.status === "available";
            const percent = c.battery_percent || 0;
            const barColor = isCompleted ? "#9333ea" : (percent >= 80 ? "#16a34a" : "#22c55e");

            return (
              <div
                key={c.charger_id}
                style={{
                  background: "#ffffff",
                  border: "1.5px solid #e2e8f0",
                  borderLeft: isCharging ? "5px solid #16a34a" : (isCompleted ? "5px solid #9333ea" : "5px solid #2563eb"),
                  borderRadius: 14,
                  padding: "12px 14px",
                  display: "flex",
                  flexDirection: "column",
                  justifyContent: "space-between",
                  minHeight: 168,
                  boxSizing: "border-box",
                  boxShadow: "0 2px 8px rgba(15, 23, 42, 0.05)"
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: 6, borderBottom: "1px solid #f1f5f9", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ fontWeight: 800, fontSize: 13, color: "#0f172a" }}>📍 {c.lot_name.replace(" 주차장", "")} [{c.space_id}]</span>
                    <span style={{ fontSize: 10, padding: "2px 6px", background: "#f1f5f9", color: "#475569", borderRadius: 4, fontWeight: 600 }}>{c.charger_type || '급속'}</span>
                  </div>
                  <span style={{ fontSize: 11, padding: "3px 8px", borderRadius: 12, background: isCharging ? "#ecfdf5" : (isCompleted ? "#f5f3ff" : "#eff6ff"), color: isCharging ? "#059669" : (isCompleted ? "#7c3aed" : "#2563eb"), border: isCharging ? "1px solid #a7f3d0" : (isCompleted ? "1px solid #ddd6fe" : "1px solid #bfdbfe"), fontWeight: 700 }}>
                    {isCharging ? "⚡ 충전 중" : (isCompleted ? "✨ 완충" : "🟢 즉시 가능")}
                  </span>
                </div>

                {/* 중앙 본문 영역 */}
                <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", marginBottom: 8 }}>
                  {isAvailable ? (
                    <div style={{ textAlign: "center", padding: "12px 10px", background: "linear-gradient(135deg, #eff6ff 0%, #f0fdf4 100%)", border: "1px solid #bfdbfe", borderRadius: 10, margin: "auto 0" }}>
                      <div style={{ display: "inline-block", padding: "3px 10px", background: "#ffffff", border: "1px solid #93c5fd", borderRadius: 20, fontSize: 12, fontWeight: 800, color: "#1d4ed8", marginBottom: 4 }}>
                        🟢 즉시 충전 가능
                      </div>
                      <div style={{ fontSize: 11, color: "#475569", fontWeight: 500 }}>
                        대기 차량 없음 · <strong>{c.charger_type || '급속'}</strong> 즉시 이용
                      </div>
                    </div>
                  ) : (
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", marginBottom: 2 }}>
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 800, color: "#0f172a" }}>차량 {c.plate_number}</div>
                          <div style={{ fontSize: 10, color: "#64748b" }}>{c.car_id ? `${c.car_id}번 차량` : ''} · 실시간 충전 중</div>
                        </div>
                        <strong style={{ fontSize: 22, color: isCompleted ? "#7e22ce" : "#059669", letterSpacing: -0.5 }}>{percent}%</strong>
                      </div>
                      <div style={{ width: "100%", height: 8, background: "#f1f5f9", borderRadius: 4, margin: "4px 0", overflow: "hidden", border: "1px solid #e2e8f0" }}>
                        <div style={{ width: `${percent}%`, height: "100%", background: isCompleted ? "linear-gradient(90deg, #c084fc, #9333ea)" : "linear-gradient(90deg, #34d399, #059669)", borderRadius: 4, transition: "width 0.5s" }} />
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#64748b", background: "#f8fafc", padding: "4px 8px", borderRadius: 6, border: "1px solid #f1f5f9" }}>
                        <span>⚡ 출력: <strong>{c.current_speed_kw ? `${c.current_speed_kw}kW` : "관제 중"}</strong></span>
                        <span style={{ fontWeight: 700, color: c.remaining_minutes <= 10 && !isCompleted ? "#dc2626" : "#059669" }}>
                          {isCompleted ? "✨ 출차 대기 중" : `⏱️ 완충까지 약 ${c.remaining_minutes}분 남음`}
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                {/* 프리미엄 도면 보기 CTA 버튼 */}
                <div style={{ width: "100%" }}>
                  <button
                    onClick={() => {
                      if (typeof window.switchParkingLotAndShowMap === "function") {
                        window.switchParkingLotAndShowMap(c.lot_id, c.space_id);
                      }
                    }}
                    style={{ width: "100%", height: 36, display: "flex", alignItems: "center", justifyContent: "center", gap: 6, background: "#eff6ff", color: "#2563eb", border: "1.5px solid #93c5fd", borderRadius: 8, cursor: "pointer", fontWeight: 700, fontSize: 12, boxShadow: "0 1px 3px rgba(37, 99, 235, 0.08)" }}
                  >
                    🗺️ {c.space_id} 주차칸 위치 도면 보기
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (type === "car_location") {
    return (
      <div style={styles.cardBox}>
        <div style={styles.cardTitle}>차량 위치 안내</div>
        <div style={styles.cardRow}>
          <span>차량번호</span>
          <strong>{data.plate_number || data.car_id}</strong>
        </div>
        <div style={styles.cardRow}>
          <span>주차위치</span>
          <strong style={{ color: "#2563eb" }}>{data.lot_name} [{data.space_id}]</strong>
        </div>
        <div style={styles.cardRow}>
          <span>주차시간</span>
          <span>{data.parked_minutes}분째 주차 중</span>
        </div>
      </div>
    );
  }

  if (type === "fee_rule") {
    const lots = data.lots || (data.lot ? [data.lot] : []);
    return (
      <div style={styles.cardBox}>
        <div style={styles.cardTitle}>주차장별 요금 기준표</div>
        {lots.map((l) => {
          const rule = l.fee_rule || l;
          return (
            <div key={l.lot_id} style={styles.cardItem}>
              <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 700, alignItems: "center" }}>
                <span>{l.name}</span>
                <span style={{ color: "#2563eb", fontWeight: 800 }}>기본 {rule.base_min}분 {(rule.base_fee || 0).toLocaleString()}원</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4, fontSize: 11, color: "#64748b" }}>
                <span>추가 {rule.unit_min}분당 {(rule.unit_fee || 0).toLocaleString()}원 · 일 최대 {(rule.daily_max || 0).toLocaleString()}원</span>
                <span style={{ color: "#10b981", fontWeight: 600 }}>회차 {rule.free_min}분 무료</span>
              </div>
            </div>
          );
        })}
        <div style={{ fontSize: 10, color: "#64748b", marginTop: 4 }}>
          ※ 친환경 전기차 및 장애인 등록 차량 50% 자동 감면
        </div>
      </div>
    );
  }

  if (type === "fee") {
    return (
      <div style={styles.cardBox}>
        <div style={styles.cardTitle}>주차 요금 안내</div>
        <div style={styles.cardRow}>
          <span>차량</span>
          <strong>{data.plate_number || data.car_id}</strong>
        </div>
        <div style={styles.cardRow}>
          <span>이용시간</span>
          <span>{data.parked_minutes}분</span>
        </div>
        {data.discount > 0 && (
          <div style={{ ...styles.cardRow, color: "#16a34a" }}>
            <span>감면 혜택</span>
            <span>-{data.discount?.toLocaleString()}원 ({data.discount_reason})</span>
          </div>
        )}
        <div style={{ ...styles.cardRow, borderTop: "1px dashed #cbd5e1", paddingTop: 6, marginTop: 6, fontSize: 15, color: "#2563eb", fontWeight: 800 }}>
          <span>최종 요금</span>
          <span>{(data.total_fee || 0).toLocaleString()}원</span>
        </div>
      </div>
    );
  }

  if (type === "payment") {
    return (
      <div style={{ ...styles.cardBox, borderColor: "#22c55e", background: "#f0fdf4" }}>
        <div style={{ ...styles.cardTitle, color: "#15803d" }}>모의 결제 완료 영수증</div>
        <div style={styles.cardRow}>
          <span>승인번호</span>
          <code style={{ fontSize: 11 }}>{data.payment_id}</code>
        </div>
        <div style={styles.cardRow}>
          <span>결제금액</span>
          <strong style={{ color: "#15803d" }}>{(data.amount || 0).toLocaleString()}원</strong>
        </div>
        <div style={{ ...styles.cardRow, marginTop: 4, paddingTop: 4, borderTop: "1px dashed #bbf7d0", color: "#b91c1c", fontWeight: 700 }}>
          <span>출차 기한</span>
          <span>{data.exit_deadline} (15분 이내)</span>
        </div>
      </div>
    );
  }

  if (type === "alert") {
    const steps = data.evacuation_steps || [
      { step: "1단계", title: "비상 유도등 확인", desc: "초록색 비상 유도등(달리는 사람)을 따라 양측 비상구로 신속히 이동하세요." },
      { step: "2단계", title: "호흡기 보호", desc: "젖은 수건이나 옷으로 코와 입을 감싸고 낮은 자세를 유지하세요." },
      { step: "3단계", title: "승강기 탑승 금지", desc: "엘리베이터는 절대 타지 마시고 비상 계단을 이용하세요." },
      { step: "4단계", title: "차단기 자동 개방", desc: "모든 차단기가 비상 개방되었습니다. 차량을 두고 신속히 보행 탈출하세요." }
    ];

    const cleanSpaceId = (data.space_id || 'A-05').replace('구역', '').trim();

    return (
      <div style={{ ...styles.cardBox, border: "2.5px solid #ef4444", background: "linear-gradient(180deg, #fef2f2 0%, #fff5f5 100%)", borderRadius: 14, padding: "14px 16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1.5px solid #fca5a5", paddingBottom: 6, marginBottom: 10 }}>
          <div style={{ fontWeight: 800, fontSize: 15, color: "#b91c1c" }}>🚨 긴급 화재 발생 경보</div>
          <span style={{ fontSize: 10, padding: "2px 8px", background: "#dc2626", color: "#ffffff", borderRadius: 12, fontWeight: 800 }}>Lv.{data.level || 2} 비상사태</span>
        </div>

        {/* 발화 위치 붉은색 박스 */}
        <div style={{ background: "#fee2e2", border: "1.5px solid #f87171", borderRadius: 8, padding: "8px 12px", marginBottom: 10, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 10, color: "#b91c1c", fontWeight: 700 }}>발화 감지 위치</div>
            <div style={{ fontSize: 13, fontWeight: 800, color: "#991b1b" }}>📍 {data.lot_name} [{data.space_id || 'A-05구역'}]</div>
          </div>
          <span style={{ fontSize: 10, color: "#b91c1c", fontWeight: 700 }}>차단기 비상 자동개방</span>
        </div>

        {/* 4대 긴급 대피요령 */}
        <div style={{ background: "#ffffff", border: "1px solid #fecaca", borderRadius: 10, padding: "10px 12px", marginBottom: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 800, color: "#b91c1c", marginBottom: 6, borderBottom: "1px dashed #fca5a5", paddingBottom: 4 }}>
            🏃 현장 긴급 비상 대피요령 (행동 수칙)
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {steps.map((s, idx) => (
              <div key={idx} style={{ display: "flex", alignItems: "flex-start", gap: 6, fontSize: 11, lineHeight: 1.4 }}>
                <span style={{ background: "#fee2e2", color: "#b91c1c", fontWeight: 800, fontSize: 9, padding: "1px 5px", borderRadius: 4, flexShrink: 0, marginTop: 1 }}>{s.step}</span>
                <div>
                  <strong style={{ color: "#991b1b" }}>{s.title}</strong>: {s.desc}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* 원클릭 도면 이동 & 경보 해제 버튼 */}
        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => {
              if (typeof window.switchParkingLotAndShowMap === "function") {
                window.switchParkingLotAndShowMap(data.lot_id || 'A', cleanSpaceId);
              }
            }}
            style={{ flex: 1, height: 36, background: "#dc2626", color: "#ffffff", border: "none", borderRadius: 8, fontWeight: 800, fontSize: 12, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: 4, boxShadow: "0 2px 6px rgba(220, 38, 38, 0.3)" }}
          >
            🗺️ 화재 도면 확인
          </button>
          <button
            onClick={() => onAction("화재 경보 해제", "fire_clear")}
            style={{ padding: "0 10px", height: 36, background: "#ecfdf5", color: "#059669", border: "1.5px solid #10b981", borderRadius: 8, fontWeight: 800, fontSize: 12, cursor: "pointer" }}
            title="화재 진압 완료 후 안전 경보 해제"
          >
            🧯 경보 해제
          </button>
        </div>
      </div>
    );
  }

  if (type === "alert_resolved" || type === "safe") {
    const checks = data.safety_checks || [
      { title: "화재 완전 진압", desc: "소방대 현장 출동 및 발화점 완전 진압 완료" },
      { title: "유독가스 배출 완료", desc: "강제 배연 설비 가동으로 실내 공기질 정상화 확인" },
      { title: "시설물 정밀 안전 점검", desc: "소방·전기 시스템 및 승강기 가동 승인" },
      { title: "정상 주차 관제 복구", desc: "차단기 및 무인 정산 시스템 정상 운영 재개" }
    ];

    return (
      <div style={{ marginTop: 8, border: "2px solid #10b981", background: "linear-gradient(180deg, #f0fdf4 0%, #ffffff 100%)", borderRadius: 14, padding: "14px 16px", boxShadow: "0 4px 12px rgba(16, 185, 129, 0.15)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "2px solid #a7f3d0", paddingBottom: 6, marginBottom: 10 }}>
          <span style={{ fontWeight: 800, fontSize: 14, color: "#065f46" }}>✅ 화재 진압 및 비상 경보 해제</span>
          <span style={{ fontSize: 11, padding: "2px 8px", background: "#10b981", color: "#fff", borderRadius: 12, fontWeight: 800 }}>정상 운영 재개</span>
        </div>

        <div style={{ background: "#ecfdf5", border: "1.5px solid #6ee7b7", borderRadius: 8, padding: "8px 12px", marginBottom: 10, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 10, color: "#166534", fontWeight: 700 }}>안전 점검 완료 구역</div>
            <div style={{ fontSize: 13, fontWeight: 800, color: "#047857" }}>📍 {data.lot_name || "개발원 주차장"} [{data.space_id || "A-05구역"}]</div>
          </div>
          <span style={{ fontSize: 11, color: "#059669", fontWeight: 700 }}>⏱️ {data.resolved_at || "방금 전"}</span>
        </div>

        <div style={{ background: "#ffffff", border: "1px solid #d1fae5", borderRadius: 10, padding: "10px 12px", marginBottom: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 800, color: "#065f46", marginBottom: 6 }}>🛡️ 현장 안전 점검 및 조치 결과</div>
          {checks.map((c, i) => (
            <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: 6, fontSize: 11, marginBottom: 4 }}>
              <span style={{ color: "#059669", fontWeight: 800 }}>✓</span>
              <div>
                <strong style={{ color: "#14532d" }}>{c.title}</strong>: <span style={{ color: "#166534" }}>{c.desc}</span>
              </div>
            </div>
          ))}
        </div>

        <div style={{ display: "flex", gap: 6 }}>
          <button
            onClick={() => {
              if (typeof window.switchParkingLotAndShowMap === "function") {
                window.switchParkingLotAndShowMap(data.lot_id || "A");
              }
            }}
            style={{ flex: 1, height: 34, background: "#059669", color: "#ffffff", border: "none", borderRadius: 8, fontWeight: 800, fontSize: 12, cursor: "pointer" }}
          >
            🗺️ 정상 주차장 도면 확인
          </button>
          <button
            onClick={() => onAction("주차장 빈자리 확인", "empty_all")}
            style={{ padding: "0 12px", height: 34, background: "#ffffff", color: "#059669", border: "1.5px solid #10b981", borderRadius: 8, fontWeight: 800, fontSize: 12, cursor: "pointer" }}
          >
            🚗 빈자리 찾기
          </button>
        </div>
      </div>
    );
  }

  if (type === "admin_call") {
    return (
      <div style={styles.cardBox}>
        <div style={{ ...styles.cardTitle, color: "#7c3aed" }}>관리자 호출 접수</div>
        <div style={styles.cardRow}>
          <span>접수번호</span>
          <strong>{data.call_id}</strong>
        </div>
        <div style={styles.cardRow}>
          <span>상태</span>
          <strong style={{ color: "#7c3aed" }}>{data.status}</strong>
        </div>
      </div>
    );
  }

  return null;
}

const styles = {
  container: {
    display: "flex",
    flexDirection: "column",
    width: "100%",
    height: "100%",
    background: "#f8fafc",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Pretendard', sans-serif",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "12px 16px",
    background: "#ffffff",
    borderBottom: "1px solid #e2e8f0",
  },
  avatar: {
    width: 34,
    height: 34,
    borderRadius: "50%",
    background: "linear-gradient(135deg, #2563eb, #7c3aed)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "#fff",
    fontSize: 18,
  },
  botAvatarSmall: {
    width: 28,
    height: 28,
    borderRadius: "50%",
    background: "#e2e8f0",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: 14,
    marginRight: 6,
    flexShrink: 0,
  },
  closeBtn: {
    background: "transparent",
    border: "none",
    fontSize: 18,
    cursor: "pointer",
    color: "#64748b",
  },
  messagesArea: {
    flex: 1,
    padding: 16,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  msgRow: {
    display: "flex",
    alignItems: "flex-end",
    gap: 6,
  },
  bubble: {
    padding: "10px 14px",
    borderRadius: 16,
    fontSize: 13,
    lineHeight: 1.5,
  },
  msgTime: {
    fontSize: 10,
    color: "#94a3b8",
    marginTop: 2,
    textAlign: "right",
  },
  chipsArea: {
    display: "flex",
    gap: 6,
    padding: "8px 12px",
    background: "#ffffff",
    borderTop: "1px solid #e2e8f0",
    overflowX: "auto",
    whiteSpace: "nowrap",
  },
  chipBtn: {
    padding: "5px 12px",
    borderRadius: 16,
    background: "#f1f5f9",
    border: "1px solid #cbd5e1",
    fontSize: 12,
    fontWeight: 600,
    color: "#334155",
    cursor: "pointer",
    flexShrink: 0,
  },
  inputBar: {
    display: "flex",
    gap: 8,
    padding: 12,
    background: "#ffffff",
    borderTop: "1px solid #e2e8f0",
  },
  inputBox: {
    flex: 1,
    padding: "10px 14px",
    borderRadius: 20,
    border: "1px solid #cbd5e1",
    fontSize: 13,
    outline: "none",
  },
  sendBtn: {
    padding: "8px 16px",
    borderRadius: 20,
    background: "#2563eb",
    color: "#ffffff",
    border: "none",
    fontWeight: 700,
    fontSize: 13,
    cursor: "pointer",
  },
  cardBox: {
    marginTop: 8,
    padding: 10,
    background: "#ffffff",
    border: "1px solid #cbd5e1",
    borderRadius: 10,
    fontSize: 12,
    maxWidth: 480,
    width: "100%",
    boxSizing: "border-box",
  },
  cardTitle: {
    fontWeight: 700,
    marginBottom: 6,
    color: "#1e293b",
  },
  cardRow: {
    display: "flex",
    justifyContent: "space-between",
    marginBottom: 3,
  },
  cardItem: {
    marginBottom: 6,
    paddingBottom: 4,
    borderBottom: "1px dashed #e2e8f0",
  },
};

