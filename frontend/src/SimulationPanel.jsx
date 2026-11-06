import {useEffect,useRef,useState} from 'react';
import {api} from './api.js';
import {formatKST} from './uiState.js';
import './styles/simulation.css';
export default function SimulationPanel({lot,onRefresh}){
 const [state,setState]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[interval,setIntervalValue]=useState(8),[notice,setNotice]=useState('');
 const refreshRef=useRef(onRefresh),sequence=useRef(null),lock=useRef(false);refreshRef.current=onRefresh;
 useEffect(()=>{let active=true,fetching=false;sequence.current=null;
  const read=async()=>{if(fetching||document.hidden)return;fetching=true;try{const result=await api('/simulation/'+lot.lot_id);if(!active)return;setState(result);setError('');if(sequence.current===null||result.running)setIntervalValue(result.interval_seconds);const latest=result.events[0]?.event_id;if(sequence.current!==null&&latest!==sequence.current)await refreshRef.current();sequence.current=latest??0}catch(e){if(active)setError(e.message)}finally{fetching=false}};
  read();const timer=window.setInterval(read,2000);return()=>{active=false;window.clearInterval(timer)};
 },[lot.lot_id]);
 async function perform(action, body = {}) {
  if (lock.current) return;
  lock.current = true;
  setBusy(true);
  setError('');
  try {
   await api(`/simulation/${lot.lot_id}/${action}`, {method: 'POST', body});
   setState(await api('/simulation/' + lot.lot_id));
   await refreshRef.current();
   setNotice(action === 'start' ? '자동 시뮬레이션을 시작했습니다.' : '자동 시뮬레이션을 일시정지했습니다.');
  } catch (failure) {
   setError(failure.message);
  } finally {
   lock.current = false;
   setBusy(false);
  }
 }
 const empty=lot.spaces.filter(s=>s.status==='empty'),occupied=lot.spaces.filter(s=>s.status==='occupied');
 return <section className="panel simulation-panel" aria-label="가상 주차장 입출차 시뮬레이션"><div className="simulation-heading"><div><span className="simulation-kicker">VIRTUAL PARKING</span><h2>{lot.name} 입출차 시뮬레이션</h2></div><span className={`badge ${state?.running?'real':'neutral'}`}>{state?.running?'자동 실행 중':'일시정지'}</span></div><p className="simulation-note">일반차·전기차·장애인 주차 자격 차량이 각각 맞는 칸에 입차합니다. 자동 실행은 세 종류의 점유율을 고르게 채웁니다. 출차는 미결제 상태에서도 가능하며 자동 결제는 하지 않습니다.</p>
 <div className="simulation-auto"><label>자동 입출차 간격<select aria-label="시뮬레이션 간격" disabled={busy||state?.running} value={interval} onChange={e=>setIntervalValue(Number(e.target.value))}>{[3,5,8,15,30].map(n=><option key={n} value={n}>{n}초</option>)}</select></label><button className="button primary" disabled={busy||!state||state.running} onClick={()=>perform('start',{interval_seconds:interval})}>자동 실행</button><button className="button white" disabled={busy||!state?.running} onClick={()=>perform('stop')}>일시정지</button><span>빈자리 <b>{empty.length}</b> · 주차 중 <b>{occupied.length}</b>{state?.running&&` · ${state.interval_seconds}초마다 입출차`}</span></div>
 {notice&&<p className="simulation-notice" role="status">{notice}</p>}{(error||state?.last_error)&&<p className="operation-error" role="alert">{error||state.last_error}</p>}
 <details open><summary>최근 입출차 이력</summary><div className="table-scroll"><table><thead><tr><th>시각</th><th>구분</th><th>차량</th><th>주차칸</th></tr></thead><tbody>{state?.events.map(e=><tr key={e.event_id}><td>{formatKST(e.occurred_at,{second:'2-digit'})}</td><td><span className={`badge ${e.action==='entry'?'real':'neutral'}`}>{e.action==='entry'?'입차':'출차'}</span></td><td>{e.vehicle_number||e.car_id}</td><td>{e.space_id}</td></tr>)}{!state?.events.length&&<tr><td colSpan="4">자동 실행을 시작하면 입출차 이력이 표시됩니다.</td></tr>}</tbody></table></div></details></section>
}
