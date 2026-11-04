export const KST='Asia/Seoul';
export const layoutForLot={A:'basic',B:'angled',C:'loop',D:'mall'};
export function normalizeSpace(space,lotId){return {...space,lot_id:lotId,zone:space.space_id?.split('-')[0]||space.zone||'A',space_type:space.space_type||'unknown'}}
export function normalizeLot(lot,spaces,asOf){return {...lot,layout_type:lot.layout_type||layoutForLot[lot.lot_id],updated_at:asOf||lot.updated_at,spaces:spaces.map(s=>normalizeSpace(s,lot.lot_id))}}
export function sourceLabel(space){return space?.is_mock||['simulation','manual'].includes(space?.source)?'모의 이벤트':space?.source==='vision'?'카메라 관측':'관측 대기'}
export function evacuationRequired(alerts,lotId){return Array.isArray(alerts)&&alerts.some(a=>a.lot_id===lotId&&Number(a.level)>=2&&a.status!=='resolved')}
export function formatKST(value,options={}){if(!value)return '—';const date=new Date(value);return Number.isNaN(date.getTime())?'—':date.toLocaleString('ko-KR',{timeZone:KST,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false,...options})+' KST'}
export function currentKSTMonth(now=new Date()){const parts=new Intl.DateTimeFormat('en-CA',{timeZone:KST,year:'numeric',month:'2-digit'}).formatToParts(now);return parts.find(p=>p.type==='year').value+'-'+parts.find(p=>p.type==='month').value}
