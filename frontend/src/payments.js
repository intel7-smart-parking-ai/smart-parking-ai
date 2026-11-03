import {api} from './api.js';
export const paymentMethodCodes={credit_card:'MOCK_CARD',naver_pay:'MOCK_EASY_PAY',kakao_pay:'MOCK_EASY_PAY',MOCK_CARD:'MOCK_CARD',MOCK_EASY_PAY:'MOCK_EASY_PAY',MOCK_ACCOUNT:'MOCK_ACCOUNT'};
const conflictCodes=['ALREADY_PAID','IDEMPOTENCY_CONFLICT','PAYMENT_CONFLICT'];
export async function submitMockPayment(invoice, method, simulateFailure, attempts, send=api) {
  if (!invoice?.session_id) throw new Error('요금 정보가 있는 주차 세션을 먼저 조회해주세요.');
  if (simulateFailure) throw new Error('실패 체험은 로컬 시뮬레이션입니다. 서버에는 결제를 요청하지 않았습니다.');
  const code=paymentMethodCodes[method];
  if (!code) throw new Error('지원하지 않는 결제 수단입니다.');
  const sid=invoice.session_id;
  const completed=fee=>fee.payment_status==='completed'||fee.payment?.status==='completed';
  const receipt=fee=>fee.payment||{session_id:sid,status:'completed',amount_krw:fee.amount_krw,notice:'서버에서 이미 결제 완료된 세션입니다.'};
  const latest=await send(`/sessions/${encodeURIComponent(sid)}/fee`);
  if (completed(latest)) return receipt(latest);
  let attempt=attempts[sid];
  if (!attempt||attempt.method!==code) attempt=attempts[sid]={key:crypto.randomUUID(),method:code};
  for(let retry=0;retry<2;retry++) {
    let payment;
    try {payment=await send('/mock-payments',{method:'POST',body:{session_id:sid,idempotency_key:attempt.key,payment_method:code}});}
    catch(error) {
      if(conflictCodes.includes(error.code)) {
        const confirmed=await send(`/sessions/${encodeURIComponent(sid)}/fee`);
        if(completed(confirmed)) return receipt(confirmed);
        delete attempts[sid];
      } else if(['PAYMENT_NOT_FOUND','INVALID_PAYMENT_STATE'].includes(error.code)) delete attempts[sid];
      throw error;
    }
    if(payment.status==='completed') return payment;
    if(payment.status!=='canceled') throw new Error('결제가 완료되지 않았습니다. 서버 상태를 확인해주세요.');
    attempt=attempts[sid]={key:crypto.randomUUID(),method:code};
  }
  throw new Error('취소된 결제의 재시도를 처리하지 못했습니다. 다시 조회해주세요.');
}
