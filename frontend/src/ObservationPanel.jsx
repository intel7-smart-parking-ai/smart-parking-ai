import {time} from './components.jsx';
import {sourceLabel} from './uiState.js';
export default function ObservationPanel({lot,filter,setFilter}){
 const rows=lot.spaces.filter(s=>!filter.startsWith('source:')||s.source===filter.slice(7));
 const latest=lot.spaces.map(s=>s.observed_at).filter(Boolean).sort((a,b)=>new Date(b)-new Date(a))[0];
 const displayedSource = space => sourceLabel(space) === '관측 대기' ? '' : sourceLabel(space);
 return <section className="panel observation-panel"><div className="panel-header"><div><h3>주차면 상태 모니터링</h3><p>마지막 관측 {time(latest)} · {lot.type==='virtual'?'가상 관측':lot.camera_status||'카메라 상태 미제공'}</p></div><label>관측 출처 <select aria-label="관측 출처 필터" value={filter.startsWith('source:')?filter:'all'} onChange={e=>setFilter(e.target.value)}><option value="all">전체 출처</option><option value="source:vision">카메라 관측</option><option value="source:simulation">이벤트</option><option value="source:manual">수동 관측</option></select></label></div><p className="fee-note">10초 이상 관측이 없으면 확인 필요로 표시합니다. 관측 중단은 출차 확정이 아닙니다. 출처 필터는 표시만 바꾸며 전체 빈자리 수는 유지합니다.</p><div className="observation-list">{rows.map(s=><span key={s.space_id} className={`observation-item ${s.status}`}><b>{s.space_id}</b> {s.status==='uncertain'?'확인 필요 · ':''}{displayedSource(s)} <small>{time(s.observed_at)}</small></span>)}</div></section>
}
