import {formatLotName} from './uiState.js';
export function validCoordinates(value){return Array.isArray(value)&&value.length===2&&value.every(n=>typeof n==='number'&&Number.isFinite(n))&&Math.abs(value[0])<=180&&Math.abs(value[1])<=90}
export function chatRecommendations(result){
 const card=result?.card;
 if(card?.type!=='empty_spots'||!card.data?.lots)return [];
 const ids=[...new Set((result.chips||[]).map(c=>c?.id?.match(/^go:([^:]+)(?::[^:]+)?$/)?.[1]).filter(Boolean))];
 return ids.flatMap(id=>{const lot=card.data.lots[id];const empty=lot?.display_empty??lot?.empty;return lot&&!lot.status_reason&&!card.data.blocked_lots?.includes(id)&&Number.isInteger(empty)&&empty>0?[{...lot,lot_id:id,name:formatLotName(lot.name)||id,empty_spaces:empty}]:[]});
}
export function readLotCoordinates(lot,overrides){const supplied=lot.coordinates??lot.location?.coordinates??(typeof lot.longitude==='number'&&typeof lot.latitude==='number'?[lot.longitude,lot.latitude]:null);return validCoordinates(supplied)?supplied:validCoordinates(overrides?.[lot.lot_id])?overrides[lot.lot_id]:null}
