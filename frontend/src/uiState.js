import {t,getLanguage} from './serviceLocale.js';
export const KST='Asia/Seoul';
export const layoutForLot={A:'basic',B:'angled',C:'loop',D:'mall'};
export function normalizeSpace(space,lotId){return {...space,lot_id:lotId,zone:space.space_id?.split('-')[0]||space.zone||'A',space_type:space.space_type||'unknown'}}
export function normalizeLot(lot,spaces,asOf){return {...lot,name:t(formatLotName(lot.name)),layout_type:lot.layout_type||layoutForLot[lot.lot_id],updated_at:asOf||lot.updated_at,spaces:spaces.map(s=>normalizeSpace(s,lot.lot_id))}}
export function sourceLabel(space){return space?.is_mock||['simulation','manual'].includes(space?.source)?'이벤트':space?.source==='vision'?'카메라 관측':'관측 대기'}
export function evacuationRequired(alerts,lotId){return Array.isArray(alerts)&&alerts.some(a=>a.lot_id===lotId&&Number(a.level)>=2&&a.status!=='resolved')}
// 오주차(11/11 PM A안): 전기차·장애인 전용 칸에 종류가 다른 차가 있으면 화면에만 표시한다.
// 일반 칸에는 어떤 차든 정상(카메라 policy.parking_rule_status와 같은 규칙). 차 종류를 모르면 표시하지 않는다.
const RESERVED_SPACE_TYPES=new Set(['ev','accessible']);
const VEHICLE_TYPES=new Set(['general','ev','accessible']);
export function parkingViolation(space){
 return Boolean(space)&&space.status==='occupied'&&RESERVED_SPACE_TYPES.has(space.space_type)
  &&VEHICLE_TYPES.has(space.vehicle_type)&&space.vehicle_type!==space.space_type;
}
export const vehicleTypeLabels={general:'일반 차량',ev:'전기차',accessible:'장애인 차량'};
// 챗봇 서버의 대피(레벨 2) 응답을 선택한 주차장 기준으로 적용한다(11/11 PM).
// 다른 주차장 경보라는 것이 경보 목록으로 확인될 때만 대피 화면을 안내 한 줄로 낮춘다.
// 선택 주차장이 없거나, 목록이 아직 갱신되지 않아 활성 레벨 2를 모르면 서버 판단(안전 쪽)을 그대로 따른다.
export function chatSafetyScope(view,alerts,lotId){
 const server=Boolean(view?.safety)||Number(view?.banner?.level)>=2;
 const active=Array.isArray(alerts)?alerts.filter(a=>Number(a.level)>=2&&a.status!=='resolved'):[];
 const relevant=!lotId||active.length===0||evacuationRequired(active,lotId);
 const otherLots=[...new Set(active.filter(a=>a.lot_id!==lotId).map(a=>a.lot_id))];
 return {unsafe:server&&relevant,relevant,otherLotNotice:server&&!relevant,otherLots};
}
export function formatKST(value,options={}){if(!value)return '—';const date=new Date(value);return Number.isNaN(date.getTime())?'—':date.toLocaleString(getLanguage()==='ko'?'ko-KR':getLanguage(),{timeZone:KST,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,...options})+' KST'}
export function currentKSTMonth(now=new Date()){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:KST,year:'numeric',month:'2-digit'}).formatToParts(now);return parts.find(p=>p.type==='year').value+'-'+parts.find(p=>p.type==='month').value}

export function formatLotName(value){return typeof value==='string'?value.replace(/모의\s*/g,'').replaceAll('개발원주차장','개발원 주차장').replaceAll('부산역주차장','부산역 주차장').replace(/북항\s*주차장|김해공항주차장/g,'김해공항 주차장').replaceAll('신세계백화점주차장','신세계 백화점 주차장'):value}

export function reservedParkingStatus(space) {
  if (space.status !== 'occupied' || !['ev', 'accessible'].includes(space.space_type)) return null;
  if (!['general', 'ev', 'accessible'].includes(space.vehicle_type)) return '확인 필요';
  return space.vehicle_type === space.space_type ? null : '전용구역 위반 의심';
}
