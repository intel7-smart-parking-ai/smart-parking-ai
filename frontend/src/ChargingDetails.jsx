import {useState} from 'react';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import {welcomeText} from './welcomeLocales.js';
import './styles/charging.css';

const labels = {
 ko: ['충전 현황','즉시 이용 가능','충전 중','충전 완료','연결 대기','미연결','확인 불가','주차 가능 · 충전기 미확인','출력','남은 시간','분','지도에서 위치 보기','시연 충전 데이터','충전기 데이터 미연동','전기차 전용칸 정보 없음','완충 · 출차 대기','정격'],
 en: ['EV charging status','Available now','Charging','Charged','Connecting','Not connected','Unknown','Space empty · charger unverified','Output','Remaining','min','Show on map','Simulated charging data','Charger telemetry unavailable','No EV bays reported','Fully charged · awaiting departure','Rated'],
 'zh-CN': ['充电状态','可立即使用','充电中','充电完成','等待连接','未连接','状态未知','车位空闲 · 充电设备未确认','功率','剩余时间','分钟','在地图上查看','模拟充电数据','充电设备数据未连接','无电动车专用车位信息','已充满 · 等待离场','额定'],
 ja: ['充電状況','すぐ利用可能','充電中','充電完了','接続待ち','未接続','確認不可','空き枠 · 充電器未確認','出力','残り時間','分','地図で場所を確認','シミュレーションデータ','充電器データ未連携','EV専用枠情報なし','充電完了 · 出庫待ち','定格'],
};
const statusLabel = {available:1, charging:2, completed:3, connected:4, not_connected:5, unknown:6};
const airportZone = id => id.startsWith('A-') ? 'P1' : /^B-(0[1-9]|1\d|2[0-4])$/.test(id) ? 'P2' : 'P3';

function ChargerMap({lot, space, alerts}) {
 const [filter,setFilter] = useState('all');
 return <ParkingLayoutMap lot={lot} filter={filter} setFilter={setFilter} alerts={alerts}
   found={{lot_id:lot.lot_id,space_id:space.space_id}} onSpace={()=>{}}/>;
}

export default function ChargingDetails({lots=[], language='ko', alerts=[], allowMap=true}) {
 const c=labels[language]||labels.ko;
 const entries=lots.flatMap(lot=>lot.spaces.filter(space=>space.space_type==='ev').map(space=>({lot,space})));
 if(!entries.length) return <p>{c[14]}</p>;
 const counts=['available','charging','completed','unknown'].map(status=>entries.filter(({space})=>(space.charging?.status||'unknown')===status).length);
 return <section className="charging-details" lang={language} aria-label={c[0]}>
  <div className="charging-counts">{counts.map((count,index)=><span key={index}>{c[[1,2,3,6][index]]} <b>{count}</b></span>)}</div>
  <div className="charging-grid">{entries.map(({lot,space})=>{
   const d=space.charging, status=d?.status||'unknown';
   const name=welcomeText[language]?.lots['ABCD'.indexOf(lot.lot_id)]||lot.name;
   return <article className={'charger-card charger-'+status} key={lot.lot_id+space.space_id}>
    <div><b>{name} {lot.lot_id==='C'&&airportZone(space.space_id)} [{space.space_id}]</b>{d?.rated_kw!=null&&<small>{c[16]} {d.rated_kw} kW</small>}</div>
    <strong>{status==='unknown'&&space.status==='empty'?c[7]:c[statusLabel[status]??6]}</strong>
    {d?.is_mock&&space.status==='occupied'&&space.plate_number&&<small>{space.plate_number}</small>}
    {d?.percent!=null&&<div className="charger-progress"><progress aria-label={c[2]} value={d.percent} max="100"/><b>{d.percent}%</b></div>}
    {status==='charging'&&d.power_kw!=null&&<small>{c[8]} {d.power_kw} kW</small>}
    {d?.estimated_remaining_seconds>0&&<small>{c[9]} {Math.ceil(d.estimated_remaining_seconds/60)} {c[10]}</small>}
    {status==='completed'&&<small>{c[15]}</small>}
    <small>{d?.is_mock?c[12]:c[13]}</small>
    {allowMap&&<details><summary>{c[11]}</summary><ChargerMap lot={lot} space={space} alerts={alerts}/></details>}
   </article>;
  })}</div>
 </section>;
}
