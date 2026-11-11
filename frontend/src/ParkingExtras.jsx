import {t} from './serviceLocale.js';
import {useEffect, useRef, useState} from 'react';
import {api} from './api.js';
import './styles/parking-extras.css';
import {ChatbotIcon, time} from './components.jsx';

export function ParkingExtras({lot, syncStatus}) {
  return <section className="panel parking-availability">
    <div className="parking-availability-summary">
    <h3>{t("구역별 빈자리")}</h3>
    <dl>
    <div><dt>{t("전체")}</dt><dd>{lot.spaces.length}{t("면")}</dd></div>
    {[['general', t('일반')], ['ev', t('전기차')], ['accessible', t('장애인')]].map(([type, name]) =>
      <div key={type}><dt>{name}</dt><dd>{lot.spaces.filter(space => space.space_type === type && space.status === 'empty').length}{t("면")}</dd></div>)}
    </dl>
    {syncStatus}
    </div>
  </section>;
}

const completedCallKey = 'parkon-admin-completed-calls';
const callKey = item => JSON.stringify([item.id,item.at]);

export function AdminCalls({showList, onOpen, onSound, onPendingCount}) {
  const [completed,setCompleted] = useState(()=>{
    try {const saved=JSON.parse(localStorage.getItem(completedCallKey));return Array.isArray(saved)?saved:[];}
    catch {return [];}
  });
  function complete(item) {
    const key=callKey(item);
    const next=[...completed.filter(value=>value!==key),key].slice(-500);
    setCompleted(next);
    try {localStorage.setItem(completedCallKey,JSON.stringify(next));} catch {}
  }

  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  const [pending, setPending] = useState([]);
  const [focusList, setFocusList] = useState(false);
  const list = useRef(null);
  useEffect(() => {
    let active = true, timer;
    let seen;
    async function refresh() {
      try {
        const result = await api('/chat/admin/calls');
        if (!active) return;
        const next = Array.isArray(result.items) ? result.items : [];
        const fresh = seen ? next.filter(item => item.id != null && !seen.has(item.id) && item.status === '접수') : [];
        seen ??= new Set();
        next.forEach(item => seen.add(item.id));
        if (fresh.length) setPending(previous => [...previous, ...fresh]);
        setItems(next);
        setError('');
      } catch (failure) {
        if (active) setError(failure.status === 403 ? '챗봇 관리자 권한 연결이 필요합니다.' : failure.message);
      } finally {
        if (active) timer = setTimeout(refresh, 15000);
      }
    }
    refresh();
    return () => {active = false; clearTimeout(timer);};
  }, []);
  useEffect(() => {
    if (!focusList || !list.current) return;
    list.current.focus();
    list.current.scrollIntoView({block:'start'});
    setFocusList(false);
  }, [focusList, showList]);
  const isDone = item => completed.includes(callKey(item)) || ['완료','해결','처리 완료'].includes(item.status);
  const pendingCount = items.filter(item => item.status === '접수' && !isDone(item)).length;
  useEffect(() => onPendingCount?.(pendingCount), [onPendingCount, pendingCount]);
  const notice = pending[0];
  const noticeId = notice?.id;
  useEffect(() => {
    if (noticeId != null) onSound('inquiry');
  }, [noticeId, onSound]);
  const dismiss = () => setPending(previous => previous.slice(1));
  return <>
  {notice && <aside className="admin-call-notice" aria-label={`새 고객 문의 · 미처리 ${pendingCount}건`}>
    <div className="admin-call-brand"><b>PARK:ON</b><span>주차장 관리자</span></div>
    <div className="admin-call-message" role="status">
      <span className="admin-call-icon" aria-hidden="true"><ChatbotIcon/>{pendingCount>0&&<b className="notification-count" data-notification="calls">{pendingCount>99?'99+':pendingCount}</b>}</span>
      <div><strong>고객 호출 · 문의</strong><p>{notice.summary || '새 문의가 접수됐습니다.'}</p>
      <small>#{notice.id} · {time(notice.at)}</small></div>
    </div>
    <div className="admin-call-actions">
      <button type="button" onClick={()=>{dismiss();setFocusList(true);onOpen();}}>문의 목록 보기</button>
      <button type="button" onClick={dismiss}>닫기</button>
    </div>
  </aside>}
  {showList && <section className="panel admin-call-list" ref={list} tabIndex={-1} aria-label="고객 호출 · 문의">
    <header className="admin-call-list-heading admin-card-heading"><ChatbotIcon/><h3>고객 호출 · 문의</h3><span>{items.length}건</span></header>
    {error && <p role="status">{error}</p>}
    {!error && !items.length && <p>접수된 문의가 없습니다.</p>}
    {items.length>0&&<ul>{items.map(item => {
      const done = isDone(item);
      return <li key={item.id}>
      <span className="admin-call-list-icon"><ChatbotIcon/></span>
      <div className="admin-call-list-copy">
        <strong>#{item.id}</strong>
        <p>{item.summary}</p>
        {item.car_id&&<small>차량 {item.car_id}번</small>}
      </div>
      <div className="admin-call-list-meta">
        <span className="admin-call-list-status" data-new={!done&&item.status==='접수'||undefined} data-completed={done||undefined}>{done?'처리 완료':item.status}</span>
        <time dateTime={item.at}>{time(item.at)}</time>
        {!done&&<button type="button" className="button white small" aria-label={`문의 ${item.id}번 처리 완료`} onClick={()=>complete(item)}>처리 완료</button>}
      </div>
    </li>;})}</ul>}
  </section>}
  </>;
}
