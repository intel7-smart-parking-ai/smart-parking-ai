import {useEffect,useRef,useState} from 'react';
import {loadLots,pollInterval} from './api.js';
import {Icon} from './components.jsx';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import {welcomeText} from './welcomeLocales.js';
import {t} from './serviceLocale.js';
import {airportZone, airportZoneLabels} from './airportZones.js';
import './styles/charging.css';
const copy={
 ko:['충전 현황','실시간 충전 현황','빈 전용칸','전체','주차 가능','주차 중','확인 필요','이용 불가','충전 상태 정보 미연동','현재 주차칸 점유 기준입니다. 충전기 연결·충전 여부와 출력은 아직 제공되지 않습니다.','새로고침','닫기','불러오는 중…','정보를 불러오지 못했습니다. 다시 조회해 주세요.','전기차 전용칸이 없습니다.','조회 시각'],
 en:['EV charging','Live charging status','Empty EV spaces','Total','Parking available','Occupied','Check required','Unavailable','Charger status not connected','Availability is based on parking occupancy. Charger connection, charging state and power are not yet available.','Refresh','Close','Loading…','Unable to load data. Please retry.','No EV spaces at this lot.','Updated'],
 'zh-CN':['充电状态','实时充电状态','空余专用车位','总计','可停车','已占用','需确认','不可用','未连接充电设备数据','此处显示车位占用情况，暂不提供充电连接、充电状态及功率。','刷新','关闭','加载中…','加载失败，请重试。','此停车场没有实时充电状态。','更新时间'],
 ja:['充電状況','リアルタイム充電状況','空きEV枠','合計','駐車可能','駐車中','確認が必要','利用不可','充電器情報は未接続','駐車枠の使用状況に基づきます。充電器の接続・充電状態・出力情報は未提供です。','更新','閉じる','読み込み中…','取得できませんでした。再試行してください。','EV専用枠はありません。','更新時刻']
};
const simulatedCopy = {
 ko: ['시뮬레이션 충전 상태', '충전 가능', '연결 대기', '충전 중', '충전 완료', '미연결', '확인 필요', '마지막 사용', '충전 시작 후', '충전 완료 후'],
 en: ['Simulated charger status', 'Available', 'Connecting', 'Charging', 'Complete', 'Not connected', 'Unknown', 'Last used', 'Charging for', 'Completed'],
 'zh-CN': ['模拟充电状态', '可充电', '等待连接', '充电中', '充电完成', '未连接', '待确认', '上次使用', '已充电', '完成后'],
 ja: ['シミュレーション充電状況', '充電可能', '接続待ち', '充電中', '充電完了', '未接続', '確認中', '最終利用', '充電開始から', '充電完了から']
};
const detailCopy = {
 ko: ['배터리', '출력', '완료까지', '주차칸 위치 보기', '목록으로', '충전 중', '충전 완료', '남은 충전시간 정보 없음', '가상 충전 시연 · 입차 후 15초 연결 → 120초 충전 → 완료. 실제 충전기 측정값이 아닙니다.'],
 en: ['Battery', 'Output', 'Time remaining', 'Show space on map', 'Back to list', 'Charging', 'Complete', 'Remaining time unavailable', 'Virtual charger demo: connect 15s → charge 120s → complete. Not physical charger measurements.'],
 'zh-CN': ['电量', '功率', '剩余时间', '查看车位位置', '返回列表', '充电中', '充电完成', '暂无剩余时间', '虚拟充电演示：连接15秒 → 充电120秒 → 完成。非实际设备测量。'],
 ja: ['電池残量', '出力', '残り時間', '区画位置を表示', '一覧に戻る', '充電中', '充電完了', '残り時間不明', '仮想充電デモ：接続15秒 → 充電120秒 → 完了。実機の測定値ではありません。']
};
const chargerStatus = {available:1,connected:2,charging:3,completed:4,not_connected:5,unknown:6};
const parkingStatus = {empty:4,occupied:5,uncertain:6,unavailable:7};
const koreanLotLabels = [['개발원','주차장'],['부산역','주차장'],['김해공항','주차장'],['신세계','백화점','주차장']];

function elapsedTime(from, to, language) {
 const seconds = Math.floor((Date.parse(to)-Date.parse(from))/1000);
 if (!Number.isFinite(seconds) || seconds<0) return '';
 return formatDuration(seconds,language);
}

function formatDuration(seconds,language) {
 const unit = (value, name) => new Intl.NumberFormat(language,{style:'unit',unit:name,unitDisplay:'short'}).format(value);
 const hours = Math.floor(seconds/3600);
 const minutes = Math.floor(seconds%3600/60);
 return hours ? [unit(hours,'hour'),minutes?unit(minutes,'minute'):''].filter(Boolean).join(' ') :
   minutes ? unit(minutes,'minute') : unit(seconds,'second');
}

function ChargerCard({space,lotId,c,sim,detail,language,onShowMap}) {
 const parking = c[parkingStatus[space.status] ?? 6];
 const charge = space.charging?.is_mock ? space.charging : null;
 const available = charge?.status === 'available';
 const charging = charge?.status === 'charging';
 const completed = charge?.status === 'completed';
 const timestamp = available ? charge.last_used_at : charging ? charge.started_at : completed ? charge.completed_at : null;
 const elapsed = timestamp ? elapsedTime(timestamp,charge.observed_at,language) : '';
 return <article className={'charger-card '+space.status}>
  <div><span>{lotId==='C'&&<small className="charger-zone">{t(airportZoneLabels[airportZone(space)])} </small>}{space.space_id}</span><ChargingIcon/></div>
  <strong>{charge ? sim[chargerStatus[charge.status] ?? 6] : parking}</strong>
  <small>{charge ? parking : c[8]}</small>
  {charge?.rated_power_kw!=null&&<small>{charge.rated_power_kw} kW</small>}
  {Number.isFinite(charge?.battery_percent)&&<div className="charging-progress">
   <label>{detail[0]} <b>{charge.battery_percent}%</b><progress value={charge.battery_percent} max="100"/></label>
   <small>{detail[1]} {charge.power_kw} kW</small>
   {Number.isFinite(charge.remaining_seconds)?<small>{detail[2]} {formatDuration(charge.remaining_seconds,language)}</small>:charging&&<small>{detail[7]}</small>}
  </div>}
  {elapsed&&<small>{sim[available ? 7 : charging ? 8 : 9]} · {elapsed}{language==='ko'?(available?' 전':' 경과'):''}</small>}
  <button type="button" className="button white small charging-map-button" onClick={()=>onShowMap(space.space_id)}>{detail[3]}</button>
 </article>;
}

export function ChargingIcon({className='charging-status-icon',viewBox='0 0 45 43'}={}){return <svg className={className} width="45" height="43" viewBox={viewBox} aria-hidden="true"><circle cx="22.5" cy="21.5" r="16" fill="#1796ff"/><g transform="translate(13 12) scale(.79)" fill="#caff27"><path d="m10 2-6 11h5l-2 9 10-13h-6l3-7z"/><path d="m18 3-3 6h3l-2 6 6-8h-3l2-4z"/></g></svg>}
export default function ChargingStatus({language='ko',initialLot='C',onClose}){
 const dialog=useRef(null),[lots,setLots]=useState([]),[selected,setSelected]=useState(initialLot),[busy,setBusy]=useState(true),[error,setError]=useState(false),[stamp,setStamp]=useState(null),[reload,setReload]=useState(0),[mapSpace,setMapSpace]=useState(null),[mapFilter,setMapFilter]=useState("all");
 const c=copy[language]||copy.ko,sim=simulatedCopy[language]||simulatedCopy.ko,detail=detailCopy[language]||detailCopy.ko;
 useEffect(()=>{dialog.current.showModal();const el=dialog.current;const close=()=>onClose();el.addEventListener('close',close);return()=>el.removeEventListener('close',close)},[onClose]);
 useEffect(()=>{let active=true,timer;async function update(){try{const result=await loadLots();if(active){setLots(result);setStamp(new Date());setError(false)}}catch{if(active)setError(true)}finally{if(active){setBusy(false);timer=setTimeout(update,pollInterval)}}}setBusy(true);update();return()=>{active=false;clearTimeout(timer)}},[reload]);
 const lot=lots.find(l=>l.lot_id===selected),spaces=lot?.spaces.filter(s=>s.space_type==='ev')||[],available=spaces.filter(s=>s.status==='empty').length;
 return <dialog ref={dialog} className="charging-dialog" aria-labelledby="charging-title" lang={language}>
  <header><div><BrandWordmark showCar={false}/><h2 id="charging-title">{c[0]}</h2></div><button className="charging-close" aria-label={c[11]} onClick={()=>dialog.current.close()}><Icon name="close"/></button></header>
  <nav className="charging-lots" aria-label={welcomeText[language].lot}>{['A','B','C','D'].map((id,i)=><button key={id} aria-pressed={id===selected} aria-label={welcomeText[language].lots[i]} onClick={()=>{setSelected(id);setMapSpace(null)}}>{language==='ko'?koreanLotLabels[i].map(line=><span key={line} style={{display:'block',whiteSpace:'nowrap'}}>{line}</span>):welcomeText[language].lots[i]}</button>)}</nav>
  <div className="charging-section-title"><h3>{c[1]}</h3><button aria-label={c[10]} disabled={busy} onClick={()=>setReload(v=>v+1)}><Icon name="refresh"/></button></div>
  {error?<div role="alert" className="charging-error">{c[13]}</div>:busy&&!lots.length?<p role="status">{c[12]}</p>:<><div className="charging-summary"><ChargingIcon/><span>{c[2]} <b>{available}</b><small> / {spaces.length}</small></span><span>{c[3]} {spaces.length}</span></div>{spaces.some(s=>s.charging?.is_mock)&&<p className="charging-updated">{sim[0]}</p>}{spaces.some(s=>s.charging?.is_mock)&&<p className="charging-updated charging-counts">{sim[1]} {spaces.filter(s=>s.charging?.status==='available').length} · {detail[5]} {spaces.filter(s=>s.charging?.status==='charging').length} · {detail[6]} {spaces.filter(s=>s.charging?.status==='completed').length} · {sim[6]} {spaces.filter(s=>s.charging?.status==='unknown').length}</p>}
  {mapSpace&&lot?<div className="charging-map"><button type="button" className="button white small" onClick={()=>setMapSpace(null)}>{detail[4]}</button>
   <ParkingLayoutMap lot={lot} filter={mapFilter} setFilter={setMapFilter} alerts={[]} found={{lot_id:lot.lot_id,space_id:mapSpace}} onSpace={space=>setMapSpace(space.space_id)} hidePreviewControls/>
  </div>:<div className="charging-grid">{spaces.map(s=><ChargerCard key={s.space_id} space={s} lotId={selected} c={c} sim={sim} detail={detail} language={language}
   onShowMap={id=>{setMapFilter('all');setMapSpace(id)}}/>)}</div>}{!spaces.length&&<p>{c[14]}</p>}</>}
  {stamp&&<p className="charging-updated">{c[15]} · {stamp.toLocaleTimeString(language==='ko'?'ko-KR':language,{hour:'2-digit',minute:'2-digit',second:'2-digit'})}{error?' · —':''}</p>}
 </dialog>
}
import BrandWordmark from './BrandWordmark.jsx';
