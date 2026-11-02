/**
 * AI 스마트 주차장 — 김채린 님 모바일 웹 & 관리자 관제 UI 연동 클라이언트 (JavaScript)
 * 작성자: 윤명수 (요금·모의결제·매출 담당)
 * 연동 대상: 김채린 님 모바일 웹 (PWA) 및 관리자 통합관제 화면
 * 
 * [사용 방법]:
 *   const billing = new ParkingBillingClient("http://<윤명수PC_IP>:8080");
 *   const fee = await billing.getFee("ses_0001");
 *   const pay = await billing.pay("ses_0001", "MOCK_CARD");
 *   await billing.cancel(pay.payment_id, "고객 모의 환불");
 *   const rev = await billing.getRevenue("2026-11");
 */
class ParkingBillingClient {
  constructor(baseUrl = "http://127.0.0.1:8080") {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async getFee(sessionId, simulatedNow = null) {
    let url = `${this.baseUrl}/api/v1/sessions/${encodeURIComponent(sessionId)}/fee`;
    if (simulatedNow) url += `?now=${encodeURIComponent(simulatedNow)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`[Billing API Error ${res.status}] 요금 조회 실패`);
    return await res.json();
  }

  async pay(sessionId, paymentMethod = "MOCK_CARD", idempotencyKey = null) {
    const key = idempotencyKey || `idem_${sessionId}_${Date.now()}`;
    const res = await fetch(`${this.baseUrl}/api/v1/mock-payments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        idempotency_key: key,
        payment_method: paymentMethod
      })
    });
    const data = await res.json();
    if (!res.ok && res.status !== 409) {
      throw new Error(`[Billing API Error ${res.status}] ${data.message || "결제 실패"}`);
    }
    return data;
  }

  async cancel(paymentId, reason = "관리자 모의 환불") {
    const res = await fetch(`${this.baseUrl}/api/v1/mock-payments/${encodeURIComponent(paymentId)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refund_reason: reason })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(`[Billing API Error ${res.status}] ${data.message || "환불 실패"}`);
    return data;
  }

  async getRevenue(month = "ALL") {
    const url = `${this.baseUrl}/api/v1/reports/revenue?month=${encodeURIComponent(month)}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`[Billing API Error ${res.status}] 매출 조회 실패`);
    return await res.json();
  }
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { ParkingBillingClient };
} else if (typeof window !== "undefined") {
  window.ParkingBillingClient = ParkingBillingClient;
}
