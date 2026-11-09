import {useEffect,useRef,useState} from 'react';
import {loadLots,pollInterval} from './api.js';
import {Icon} from './components.jsx';
import {welcomeText} from './welcomeLocales.js';
import './styles/charging.css';
import {chargingText,remainingText} from './chargingView.js';
const copy={
 ko:['충전 현황','실시간 충전 현황','빈 전용칸','전체','주차 가능','주차 중','확인 필요','이용 불가','충전 상태 정보 미연동','현재 주차칸 점유 기준입니다. 충전기 연결·충전 여부와 출력은 아직 제공되지 않습니다.','새로고침','닫기','불러오는 중…','정보를 불러오지 못했습니다. 다시 조회해 주세요.','전기차 전용칸이 없습니다.','조회 시각'],
 en:['EV charging','Live charging status','Empty EV spaces','Total','Parking available','Occupied','Check required','Unavailable','Charger status not connected','Availability is based on parking occupancy. Charger connection, charging state and power are not yet available.','Refresh','Close','Loading…','Unable to load data. Please retry.','No EV spaces at this lot.','Updated'],
 'zh-CN':['充电状态','实时充电状态','空余专用车位','总计','可停车','已占用','需确认','不可用','未连接充电设备数据','此处显示车位占用情况，暂不提供充电连接、充电状态及功率。','刷新','关闭','加载中…','加载失败，请重试。','此停车场没有实时充电状态。','更新时间'],
 ja:['充電状況','リアルタイム充電状況','空きEV枠','合計','駐車可能','駐車中','確認が必要','利用不可','充電器情報は未接続','駐車枠の使用状況に基づきます。充電器の接続・充電状態・出力情報は未提供です。','更新','閉じる','読み込み中…','取得できませんでした。再試行してください。','EV専用枠はありません。','更新時刻']
};
export function ChargingIcon(){return <svg className="charging-status-icon" width="45" height="43" viewBox="0 0 45 43" aria-hidden="true"><circle cx="22.5" cy="21.5" r="16" fill="#1796ff"/><g transform="translate(13 12) scale(.79)" fill="#caff27"><path d="m10 2-6 11h5l-2 9 10-13h-6l3-7z"/><path d="m18 3-3 6h3l-2 6 6-8h-3l2-4z"/></g></svg>}
export default function ChargingStatus({language='ko',initialLot='A',onClose}){
 const dialog=useRef(null),[lots,setLots]=useState([]),[selected,setSelected]=useState(initialLot),[busy,setBusy]=useState(true),[error,setError]=useState(false),[stamp,setStamp]=useState(null),[reload,setReload]=useState(0);
 const c=copy[language]||copy.ko;
 useEffect(()=>{dialog.current.showModal();const el=dialog.current;const close=()=>onClose();el.addEventListener('close',close);return()=>el.removeEventListener('close',close)},[onClose]);
 useEffect(()=>{let active=true,timer;async function update(){try{const result=await loadLots();if(active){setLots(result);setStamp(new Date());setError(false)}}catch{if(active)setError(true)}finally{if(active){setBusy(false);timer=setTimeout(update,pollInterval)}}}setBusy(true);update();return()=>{active=false;clearTimeout(timer)}},[reload]);
 const lot=lots.find(l=>l.lot_id===selected),spaces=lot?.spaces.filter(s=>s.space_type==='ev')||[],available=spaces.filter(s=>s.status==='empty').length;
 return <dialog ref={dialog} className="charging-dialog" aria-labelledby="charging-title" lang={language}>
  <header><div><span className="charging-eyebrow">PARK : ON · EV</span><h2 id="charging-title">{c[0]}</h2></div><button className="charging-close" aria-label={c[11]} onClick={()=>dialog.current.close()}><Icon name="close"/></button></header>
  <nav className="charging-lots" aria-label={welcomeText[language].lot}>{['A','B','C','D'].map((id,i)=><button key={id} aria-pressed={id===selected} onClick={()=>setSelected(id)}>{welcomeText[language].lots[i]}</button>)}</nav>
  <div className="charging-section-title"><h3>{c[1]}</h3><button aria-label={c[10]} disabled={busy} onClick={()=>setReload(v=>v+1)}><Icon name="refresh"/></button></div>
  {['C','D'].includes(selected)&&<p className="charging-updated">{({ko:'가상 충전 시연 · 입차 후 15초 연결 대기 → 120초 충전 → 완료. 실제 충전기 측정값이 아닙니다.',en:'Virtual charger demo: connect 15s → charge 120s → complete. Not physical charger measurements.','zh-CN':'虚拟充电演示：连接15秒 → 充电120秒 → 完成。非实际设备测量。',ja:'仮想充電デモ：接続15秒 → 充電120秒 → 完了。実機の測定値ではありません。'})[language]}</p>}
  {error?<div role="alert" className="charging-error">{c[13]}</div>:busy&&!lots.length?<p role="status">{c[12]}</p>:<><div className="charging-summary"><ChargingIcon/><span>{c[2]} <b>{available}</b><small> / {spaces.length}</small></span><span>{c[3]} {spaces.length}</span></div><div className="charging-grid">{spaces.map(s=><article key={s.space_id} className={'charger-card '+s.status}><div><span>{s.space_id}</span><ChargingIcon/></div><strong>{c[({empty:4,occupied:5,uncertain:6,unavailable:7})[s.status]||6]}</strong><small>{s.charging?chargingText(s.charging,language):c[8]}</small><small>{remainingText(s.charging,language)}</small></article>)}</div>{!spaces.length&&<p>{c[14]}</p>}</>}
  {stamp&&<p className="charging-updated">{c[15]} · {stamp.toLocaleTimeString(language==='ko'?'ko-KR':language,{hour:'2-digit',minute:'2-digit',second:'2-digit'})}{error?' · —':''}</p>}
 </dialog>
}
