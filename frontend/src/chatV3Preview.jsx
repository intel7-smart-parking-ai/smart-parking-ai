import React, {useEffect, useState, useSyncExternalStore} from 'react';
import {createRoot} from 'react-dom/client';
import {createV3Session} from './chatV3Session.js';
import './styles/chat-v3-preview.css';

function Preview() {
  const [chat] = useState(() => createV3Session());
  const state = useSyncExternalStore(chat.subscribe, chat.getSnapshot);
  const {config, busy, rows, draft, pending, error, resetRequired} = state;
  useEffect(() => {chat.start();}, [chat]);
  const fixture = config?.model_mode === 'fixture';
  function reset() {
    if (pending && !window.confirm('이전 요청은 서버에서 계속 처리될 수 있습니다. 새 대화를 시작할까요?')) return;
    chat.start();
  }
  return <main className="v3-shell">
    <header><a href="/">PARK:ON</a><span>새 챗봇 시험</span></header>
    <section className="v3-intro"><h1>주차장에 궁금한 점을 물어보세요</h1>
      <p>기존 서비스와 분리된 시험 화면입니다. 현재 한국어만 지원하며 결제·예약은 실행하지 않습니다.</p>
      <strong className="v3-mode">{config ? (fixture ? '모의 모델 · 예시 질문 전용' : 'OpenRouter 모델 · 호출 시 과금 가능') : '시험 서버 연결 중'}</strong>
      {fixture && <p>아래 예시 질문으로 화면과 조회 연결을 확인합니다. 자유로운 표현을 이해하는 실제 AI 성능 시험은 아닙니다.</p>}
    </section>
    {Array.isArray(config?.examples) && <section className="v3-examples" aria-label="예시 질문">{config.examples.map((text, i) => <button key={i} disabled={busy || Boolean(pending) || resetRequired} onClick={() => chat.edit(text)}>{text}</button>)}</section>}
    <section className="v3-conversation" role="log" aria-live="polite" aria-label="대화 기록">
      {!rows.length && <p className="v3-empty">예시를 선택하거나 질문을 입력해 시작하세요.</p>}
      {rows.map((row, i) => <article className={row.who === 'user' ? 'v3-user' : 'v3-answer'} key={i}><small>{row.who === 'user' ? '나' : '주차 챗봇'}</small><p>{row.text}</p>
        {row.result?.meta?.source_refs?.length > 0 && <details><summary>답변 근거</summary><ul>{row.result.meta.source_refs.map((ref, j) => <li key={j}>{ref}</li>)}</ul></details>}
      </article>)}
      {busy && <p role="status">{config ? '응답을 확인하고 있습니다…' : '시험 서버에 연결하고 있습니다…'}</p>}
    </section>
    {error && <p className="v3-error" role="alert">{error}</p>}
    {pending && !busy && <div className="v3-pending"><p>이전 요청의 완료 여부를 확인하기 전에는 새 질문을 보내지 않습니다.</p>{!resetRequired && <button onClick={() => chat.retry()}>같은 요청 다시 확인</button>}</div>}
    <form onSubmit={e => {e.preventDefault(); chat.send();}}><label htmlFor="question">질문 <small>{[...draft].length}/200자</small></label><div className="v3-input"><input id="question" value={draft} disabled={busy || Boolean(pending) || resetRequired || !config} onChange={e => chat.edit(e.target.value)} placeholder="예: 부산역 빈자리 있어?" autoComplete="off"/><button disabled={busy || Boolean(pending) || resetRequired || !config || !draft.trim() || [...draft.trim()].length > 200}>보내기</button></div></form>
    <footer><button className="v3-reset" disabled={busy} onClick={reset}>{config ? '새 대화 시작' : '연결 다시 시도'}</button><p>시설 설명은 프로젝트 시나리오이며 실제 시설 안내로 확인된 자료가 아닙니다. 빈자리 조회는 예약이 아닙니다.</p></footer>
  </main>;
}

createRoot(document.getElementById('root')).render(<Preview/>);
