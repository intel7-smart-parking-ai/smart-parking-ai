import {useEffect,useState} from 'react';
import {api} from './api.js';
import {time} from './components.jsx';
import {TablePagination,useTablePage} from './TablePagination.jsx';
export default function SessionHistoryPanel({lots}){
 const [status,setStatus]=useState('closed'),[lot,setLot]=useState(''),[rows,setRows]=useState([]),[error,setError]=useState('');
 const history=useTablePage(rows);
 useEffect(()=>{
  let active=true, timer;
  async function load(){
   try {
    if(typeof document!=='undefined'&&document.hidden)return;
    const query=new URLSearchParams({status,limit:'100',...(lot?{lot_id:lot}:{})});
    const data=await api('/sessions?'+query);
    if(active){setRows(data.slice(0,100));setError('')}
   } catch(e){if(active)setError(e.message)}
   // 출차 이력 조회는 무겁다(연속 시연 이력 22만 건) → 30초마다, 탭이 안 보이면 쉬기 (11/10 부하 점검)
   finally {if(active)timer=setTimeout(load,30000)}
  }
  setRows([]);
  load();
  return()=>{active=false;clearTimeout(timer)};
 },[status,lot]);
 return <section className="panel table-panel"><div className="panel-header admin-section-heading session-history-heading"><div className="session-history-title"><h2>최근 입출차 이력 <span>최대 100건</span></h2></div><div className="session-history-tools"><label><span>상태</span><select aria-label="입출차 이력 상태" value={status} onChange={e=>{history.setPage(1);setStatus(e.target.value)}}><option value="closed">출차 완료</option><option value="active">현재 입차</option></select></label><label><span>주차장</span><select aria-label="입출차 이력 주차장" value={lot} onChange={e=>{history.setPage(1);setLot(e.target.value)}}><option value="">전체</option>{lots.map(l=><option key={l.lot_id} value={l.lot_id}>{l.name}</option>)}</select></label><TablePagination label="입출차 이력" pagination={history}/></div></div>{error&&<p role="alert">{error}</p>}<div className="table-scroll"><table><thead><tr><th>차량</th><th>주차장 / 칸</th><th>입차</th><th>출차</th><th>상태</th></tr></thead><tbody>{history.rows.map(r=><tr key={r.session_id}><td>{r.plate_number||r.car_id||'미식별'}</td><td>{r.lot_id} / {r.space_id||'—'}</td><td>{time(r.entry_at)}</td><td>{r.exit_at?time(r.exit_at):'—'}</td><td>{r.status==='active'?'주차 중':'출차 완료'}</td></tr>)}{!rows.length&&<tr><td colSpan="5">해당 이력이 없습니다.</td></tr>}</tbody></table></div></section>
}
