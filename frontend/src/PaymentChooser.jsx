import {useState} from 'react';
import {money} from './components.jsx';

export const paymentMethods = {
  MOCK_CARD: '신용카드',
  MOCK_EASY_PAY: '부캉페이' ,
  MOCK_ACCOUNT: '계좌이체',
};

export default function PaymentChooser({amount, busy, error, onPay, onBack, blocked=false}) {
  const [method, setMethod] = useState('');
  const [fail, setFail] = useState(false);
  return <section className="payment-chooser" aria-label="결제 수단 선택">
    <div className="payment-chooser-heading"><span>PAYMENT</span><h3>결제 수단을 선택해주세요</h3><p>선택한 수단으로 모의 결제를 진행합니다.</p></div>
    {blocked&&<p className="operation-error" role="alert">대피 안내 중 · 결제 중단</p>}
    <fieldset disabled={busy||blocked} className="payment-methods">
      <legend>결제 수단</legend>
      {Object.entries(paymentMethods).map(([id, label]) => <label key={id} className={`payment-method ${id} ${method === id ? 'selected' : ''}`}>
        <input type="radio" name="payment_method" value={id} checked={method === id} onChange={() => setMethod(id)}/>
        <span className="payment-brand" aria-hidden="true">{id === 'MOCK_CARD' ? 'CARD' : id === 'MOCK_EASY_PAY' ? <img src="/icons/bukang-pay.png" alt="" style={{width:76,height:56,objectFit:"contain",borderRadius:8}}/> : 'BANK'}</span>
        <strong>{label}</strong><span className="payment-method-check" aria-hidden="true">{method === id ? '✓' : ''}</span>
      </label>)}
    </fieldset>
    <div className="payment-choice-total"><span>결제 금액</span><strong>{money(amount)}</strong></div>
    <p className="payment-demo-note">실제 청구 없이 진행되는 시연입니다. 카드번호 입력이나 외부 앱 연결은 필요하지 않습니다.</p>
    <label className="failure-toggle"><input type="checkbox" checked={fail} disabled={busy||blocked} onChange={event => setFail(event.target.checked)}/>실패 상황 체험 (서버 요청 없음)</label>
    {error && <div className="payment-inline-error" role="alert">{error}</div>}
    <button className="button primary full" disabled={busy || blocked || !method} onClick={() => onPay(fail, method)}>{busy ? '모의 결제 처리 중…' : method ? `${paymentMethods[method]}로 ${money(amount)} 모의 결제` : '결제 수단을 선택해주세요'}</button>
    <button className="button white full payment-back" disabled={busy} onClick={onBack}>이전으로</button>
  </section>;
}
