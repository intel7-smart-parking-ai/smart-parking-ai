import {useState} from 'react';
import {useSessionFees} from './sessionFees.js';
import {time,money} from './components.jsx';
import './styles/virtual-vehicles.css';

export default function VirtualVehicleDashboard({lot,onSpace,invoice,compact=false}){
 const [selected,setSelected]=useState(invoice?.space_id||''),[query,setQuery]=useState('');
 const vehicles=lot.spaces.filter(space=>space.status==='occupied');
 const sessions=vehicles.map(space=>space.session_id).filter(Boolean).sort().join(',');
 const fees=useSessionFees(sessions);
 const filtered=vehicles.filter(space=>(!compact||space.space_id===invoice?.space_id)&&(space.plate_number||space.vehicle_number||'').includes(query));
 const vehicle=filtered.find(space=>space.space_id===selected)||filtered[0];
 const fee=vehicle&&fees[vehicle.session_id];
 return <section className={"virtual-vehicles"+(compact?" detail-only":"")}>{!compact&&<div className="panel vehicle-list"><h2>차량 · 입출차 현황</h2><p className="fee-note">신세계 백화점 주차장 · 가상 차량</p>
 <input aria-label="차량번호 검색" placeholder="차량번호 검색" value={query} onChange={event=>setQuery(event.target.value)}/>
 <div className="vehicle-summary"><b>주차 중 {vehicles.length}대</b><span>주차 가능 {lot.spaces.filter(space=>space.status==='empty').length}면</span></div>
 <div className="table-scroll"><table><thead><tr><th>차량번호</th><th>주차칸</th><th>입차 시간</th><th>요금</th></tr></thead><tbody>{filtered.map(space=><tr key={space.space_id} className={vehicle?.space_id===space.space_id?'selected':''}><td><button onClick={()=>setSelected(space.space_id)}>{space.plate_number||space.vehicle_number||'번호 미제공'}</button></td><td>{space.space_id}</td><td>{time(space.entry_at)}</td><td>{fees[space.session_id]?money(fees[space.session_id].amount_krw):'조회 대기'}</td></tr>)}</tbody></table></div>
 {!filtered.length&&<p className="empty-state">현재 조건에 맞는 주차 차량이 없습니다. 가상 입차 후 차량 정보가 표시됩니다.</p>}</div>}
 <aside className="panel vehicle-detail">{vehicle?<><div className="vehicle-photo"><img src="/lot-photos/demo-entry-vehicle.optimized.jpg" alt="차両 입차 장면 시연용 이미지"/><span>입차 사진 예시 · 시연용 합성 이미지</span></div><h2>{vehicle.plate_number||vehicle.vehicle_number||'번호 미제공'}</h2><dl><div><dt>주차 위치</dt><dd>{vehicle.space_id}</dd></div><div><dt>입차 시간</dt><dd>{time(vehicle.entry_at)}</dd></div><div><dt>이용 시간</dt><dd>{fee?.duration_minutes!=null?fee.duration_minutes+'분':'조회 대기'}</dd></div><div><dt>현재 요금</dt><dd className="vehicle-fee">{fee?money(fee.amount_krw):'조회 대기'}</dd></div></dl><button className="button primary full" onClick={()=>onSpace(vehicle)}>주차칸 상세 보기</button></>:<div className="empty-state">차량이 입차하면 사진과 상세 정보가 표시됩니다.</div>}</aside></section>;
}
