import {api} from './api.js';

// Preserve a key for uncertain network retries, but never reuse a cancelled payment.
export async function submitMockPayment(invoice, method, simulateFailure, attempts, send = api) {
  const sid = invoice.session_id;
  const latest = await send(`/sessions/${sid}/fee`);
  if (latest.payment?.status === 'completed') return latest.payment;
  let attempt = attempts[sid];
  if (!attempt || attempt.method !== method) {
    attempt = attempts[sid] = {key: crypto.randomUUID(), method};
  }
  for (let retry = 0; retry < 2; retry++) {
    let payment;
    try {
      payment = await send('/mock-payments', {method: 'POST', body: {
        session_id: sid, quote_id: latest.quote_id, idempotency_key: attempt.key,
        payment_method: method, simulate_failure: simulateFailure,
      }});
    } catch (error) {
      if (['ALREADY_PAID', 'PAYMENT_METHOD_CONFLICT'].includes(error.code)) {
        const confirmed = await send(`/sessions/${sid}/fee`);
        if (confirmed.payment?.status === 'completed') return confirmed.payment;
        delete attempts[sid];
      }
      throw error;
    }
    if (payment.status === 'completed') return payment;
    if (payment.status !== 'cancelled') throw new Error('결제 완료 상태를 확인하지 못했습니다. 다시 조회해주세요.');
    attempt = attempts[sid] = {key: crypto.randomUUID(), method};
  }
  throw new Error('취소된 결제의 재시도를 처리하지 못했습니다. 다시 조회해주세요.');
}
