import {t,translate} from './serviceLocale.js';
import {formatLotName} from './uiState.js';
import {useEffect, useState} from 'react';
import ParkingLayoutMap from './ParkingLayoutMap.jsx';
import AirportPanel from './AirportPanel.jsx';
import CongestionForecast from './CongestionForecast.jsx';
import './styles/charging.css';

const money=value=>value!=null&&value!==''&&Number.isFinite(Number(value))?t("{0}원",Number(value).toLocaleString('ko-KR')):null;
const date=value=>{if(!value)return null;const d=new Date(value);return Number.isNaN(d.getTime())?null:d.toLocaleString('ko-KR',{dateStyle:'short',timeStyle:'short'})};
const label=(key,value)=>value==null||value===''?null:<div className="chat-card-detail" key={key}><span>{key}</span><strong>{value}</strong></div>;
function Zones({zones}){return zones&&<div className="chat-zone-grid">{[['general',t('일반')],['ev',t('전기차')],['accessible',t('장애인')]].map(([key,name])=>zones[key]&&<div key={key}><span>{name}</span><strong>{zones[key].empty??'—'}{t("자리")}</strong></div>)}</div>}
// D-67: 충전 현황 버튼 + 요약 한 줄. 서버가 준 칸 상태·charging 값을 세기만 한다(계산·추정 없음).
function ChargingSummary({lot}){
 const ev=lot?.spaces?.filter(space=>space.space_type==='ev')||[];
 if(!ev.length)return null;
 const mock=ev.filter(space=>space.charging?.is_mock);
 const count=status=>mock.filter(space=>space.charging.status===status).length;
 return <p className="chat-charging-summary" role="status">{mock.length
  ?t("충전 가능 {0} · 충전 중 {1} · 완료 {2}",count('available'),count('charging'),count('completed'))
  :t("빈 전기차 칸 {0} / {1} · 충전기 정보 미연동",ev.filter(space=>space.status==='empty').length,ev.length)}</p>;
}
function VehicleMap({lot,space,alerts}){
 const [filter,setFilter]=useState('all');
 return <ParkingLayoutMap lot={lot} filter={filter} setFilter={setFilter} alerts={alerts} found={{lot_id:lot.lot_id,space_id:space.space_id}} onSpace={()=>{}}/>;
}

function AnswerMap({lot,spaceId,alerts,reservationAvailable,initialFilter='all',route,mapT=t}){
 const [filter,setFilter]=useState(initialFilter);
 return <section className="chat-answer-map" aria-label={mapT('{0} 지도',mapT(formatLotName(lot.name)))}><h3>{mapT(formatLotName(lot.name))}</h3>{route?.origin&&<p>{route.origin} → {route.destination} · {mapT("시연 경로")}</p>}{reservationAvailable&&lot.lot_id==='C'&&<AirportPanel lot={lot} compact/>}<ParkingLayoutMap translate={mapT} hidePreviewControls hideTitle legendNearMap route={route} lot={lot} filter={filter} setFilter={setFilter} alerts={alerts} found={spaceId?{lot_id:lot.lot_id,space_id:spaceId}:null} onSpace={()=>{}}/></section>;
}

function AnswerMaps({ids,lots,alerts,filter='all',spaceId,reservationAvailable,route,mapT=t}){
 return <div className="chat-data-card chat-answer-maps">{ids.map(id=>lots.find(lot=>lot.lot_id===id)).filter(Boolean).map(lot=><AnswerMap mapT={mapT} key={lot.lot_id} lot={lot} spaceId={spaceId} alerts={alerts} initialFilter={filter} reservationAvailable={reservationAvailable} route={route?.lot_id===lot.lot_id?route:null}/>)}</div>;
}

function QuoteCountdown({expiresAt}) {
 const [now, setNow] = useState(Date.now());
 useEffect(() => {
  const timer = setInterval(() => setNow(Date.now()), 1000);
  return () => clearInterval(timer);
 }, []);
 const expiry = Date.parse(expiresAt);
 if (!Number.isFinite(expiry)) return null;
 const seconds = Math.max(0, Math.ceil((expiry - now) / 1000));
 return <p className="chat-confirm-note" role="status">{seconds ? t("견적 남은 시간 {0}분 {1}초",Math.floor(seconds / 60),seconds % 60) : t('견적이 만료되었습니다. 요금을 다시 조회해 주세요.')}</p>;
}

function PaymentSummary({data, lots,mapT=t}) {
 const english=data.language==='en';
 const lot=lots.find(item=>item.lot_id===data.lot_id);
 const observedAt=Date.parse(data.observed_at);
 const checked=Number.isFinite(observedAt)?new Intl.DateTimeFormat(data.language||'ko-KR',{
  timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23',
 }).format(observedAt):null;
 return <section className="chat-data-card chat-payment-summary" aria-label={english?'Parking fee and payment':mapT('요금·결제 조회')}>
  <header><h3>{english?`Vehicle ${data.vehicle_number}`:mapT('차량 {0}',data.vehicle_number)}</h3><span className="chat-payment-status" data-paid={['모의 결제 완료','Demo payment complete'].includes(data.status)}>{mapT(data.status)}</span></header>
  <dl>
   <div><dt>{english?'Current fee':mapT('현재 요금')}</dt><dd className="chat-payment-amount">{english?`₩${data.amount.toLocaleString('en-US')}`:mapT('{0}원',data.amount.toLocaleString('ko-KR'))}</dd></div>
   <div><dt>{english?'Parking location':mapT('주차 위치')}</dt><dd>{mapT(formatLotName(data.lot_name||lot?.name))||data.lot_id} <strong>{data.space_id}</strong></dd></div>
  </dl>
  {data.blocked&&<p className="operation-error" role="alert">{english?'Payment is suspended during the alert.':mapT('경보로 결제가 중단되었습니다.')}</p>}
  {checked&&<small>{english?'Checked at':mapT('조회 기준')} · {checked} KST</small>}
 </section>;
}

export default function ChatResponseCard({card,lots=[],alerts=[],allowMap,onOpenCharging,language}){
 if(!card?.data||typeof card.data!=='object')return null;
 const d=card.data;
 const mapT=language?translate.bind(null,language):t;
 switch(card.type){
  case 'congestion_forecast': return allowMap?<CongestionForecast data={d}/>:null;
  case 'charging_status': return allowMap?<div className="chat-charging"><ChargingSummary lot={lots.find(lot=>lot.lot_id===(d.lot_id||'A'))}/><button type="button" className="button primary" onClick={()=>onOpenCharging?.(d.lot_id||'A')}>{t('충전 현황')}</button></div>:null;
  case 'parking_map': return allowMap&&Array.isArray(d.lot_ids)?<>
   {d.payment_summary&&<PaymentSummary mapT={mapT} data={d.payment_summary} lots={lots}/>}
   <AnswerMaps mapT={mapT} ids={d.lot_ids} lots={lots} alerts={alerts} filter={d.filter||'all'} spaceId={d.space_id} reservationAvailable={d.reservation_available} route={d.route}/>
  </>:null;
  case 'empty_spots': return <><div className="chat-data-card"><h3>{t("주차장별 빈자리")}</h3><div className="chat-lot-list">{Object.entries(d.lots||{}).map(([id,lot])=><div className="chat-lot-row" key={id}><div><strong>{formatLotName(lot.name)||t("{0} 주차장",id)}</strong><span>{lot.status_reason||t("빈자리 {0}면",lot.display_empty??lot.empty??'—')}</span></div><Zones zones={lot.zones}/></div>)}</div></div>{allowMap&&<AnswerMaps mapT={mapT} ids={d.focus?[d.focus]:Object.keys(d.lots||{})} lots={lots} alerts={alerts} filter={d.zone||'empty'}/>}</>;
  case 'car_location': {
   const lot=lots.find(item=>item.lot_id===d.lot_id);
   const space=lot?.spaces.find(item=>item.space_id===d.space_id);
   return <div className="chat-data-card"><h3>{t("차량 위치")}</h3><strong className="chat-card-hero">{d.car_id??'차량'}{t("번 ·")} {lot?.name||d.lot_id} {d.space_id||d.spot_no}</strong>{label(t('입차'),date(d.entry_at))}{allowMap&&space&&<AnswerMap mapT={mapT} lot={lot} spaceId={space.space_id} alerts={alerts}/>}</div>;
  }
  case 'fee': return <div className="chat-data-card"><h3>{t("이용요금 견적")}</h3><strong className="chat-card-hero">{money(d.fee)||t('요금 확인 중')}</strong>{label('차량',d.car_id!=null?t("{0}번",d.car_id):null)}{label(t('이용 시간'),Number.isFinite(d.minutes)?t("{0}분",d.minutes):null)}{label(t('견적 유효'),date(d.quote_expires_at))}<QuoteCountdown expiresAt={d.quote_expires_at}/></div>;
  case 'payment': return <div className="chat-data-card chat-receipt"><h3>{d.verification_pending ? t("결제 결과 확인 필요") : t("결제 영수증")}</h3><strong className="chat-card-hero">{money(d.amount??d.fee??d.paid_amount)||t('결제 결과 확인')}</strong>{label('차량',d.car_id!=null?t("{0}번",d.car_id):null)}{label(t('결제 시각'),date(d.paid_at??d.created_at))}{label(t('영수증 번호'),d.receipt_id??d.payment_id)}<small>{t("실제 금액은 청구되지 않습니다.")}</small></div>;
  case 'alert': return <div className="chat-data-card chat-alert-card"><h3>{t("경보")}</h3>{label(t('위치'),[d.lot_id,d.space_id||d.spot_no].filter(v=>v!=null).join(' · '))}{label(t('발생 시각'),date(d.started_at??d.observed_at))}<small>{t("현장 안전 안내를 우선 따라 주세요.")}</small></div>;
  case 'admin_call': return <div className="chat-data-card"><h3>{t("관리자 호출 접수")}</h3>{label(t('접수 번호'),d.id!=null?`#${d.id}`:null)}{label(t('상태'),d.status)}{label(t('접수 시각'),date(d.at))}</div>;
  case 'unrecognized_candidates': return <div className="chat-data-card"><h3>{t("번호 미인식 차량 후보")}</h3>{Array.isArray(d.cars)&&d.cars.map((car,i)=><div className="chat-card-detail" key={i}><span>{t("후보")} {i+1}</span><strong>{car.space_id||[car.lot_id,car.spot_no].filter(v=>v!=null).join(' · ')}</strong></div>)}{d.notice&&<small>{d.notice}</small>}</div>;
  default:return null;
 }
}
