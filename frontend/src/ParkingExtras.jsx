import {useEffect, useState} from 'react';
import {api} from './api.js';
import './styles/parking-extras.css';
import {evacuationRequired} from './uiState.js';

export function ParkingExtras({lot, lots, alerts, onSelect, syncStatus}) {
  const full = lot.spaces.length > 0 && lot.spaces.every(space => space.status !== 'empty');
  const alternatives = lots.filter(item => item.lot_id !== lot.lot_id &&
    !evacuationRequired(alerts, item.lot_id) && item.spaces.some(space => space.status === 'empty'));
  return <section className="panel parking-availability">
    <div className="parking-availability-summary">
    <h3>구역별 빈자리</h3>
    <dl>
    <div><dt>전체</dt><dd>{lot.spaces.length}면</dd></div>
    {[['general', '일반'], ['ev', '전기차'], ['accessible', '장애인']].map(([type, name]) =>
      <div key={type}><dt>{name}</dt><dd>{lot.spaces.filter(space => space.space_type === type && space.status === 'empty').length}면</dd></div>)}
    </dl>
    {syncStatus}
    </div>
    {full && <><h3>다른 주차장 확인</h3>{alternatives.map(item =>
      <button className="button white full" key={item.lot_id} onClick={() => onSelect(item.lot_id)}>
        {item.name} · {item.spaces.filter(space => space.status === 'empty').length}면
      </button>)}{!alternatives.length && <p>현재 안내 가능한 빈자리가 없습니다.</p>}</>}
  </section>;
}

export function AdminCalls() {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    async function refresh() {
      try {
        const result = await api('/chat/admin/calls');
        if (active) {setItems(Array.isArray(result.items) ? result.items : []); setError('');}
      } catch (failure) {
        if (active) setError(failure.status === 403 ? '챗봇 관리자 권한 연결이 필요합니다.' : failure.message);
      }
    }
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => {active = false; clearInterval(timer);};
  }, []);
  return <section className="panel policy-panel" style={{marginBottom:20}}><h3>고객 호출 · 문의</h3>
    {error && <p role="status">{error}</p>}
    {!error && !items.length && <p>접수된 문의가 없습니다.</p>}
    {items.map(item => <div className="policy-row" key={item.id}><strong>#{item.id} · {item.status}</strong>
      <span>{item.car_id && `차량 ${item.car_id}번 · `}{item.summary}<br/>{item.at}</span></div>)}
  </section>;
}
