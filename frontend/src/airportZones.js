import {t} from './serviceLocale.js';
export const airportZoneLabels = {A: 'P1 국내선', B: 'P2 국제선', C: 'P3 여객·화물'};
export const airportZones = Object.fromEntries(Object.entries(airportZoneLabels).map(([zone,label])=>[zone,t(label)]));

export function airportZone(space) {
  // Preserve existing space IDs and sessions when moving the last row into P3.
  return space.zone === 'B' && Number(space.space_id.split('-')[1]) > 24 ? 'C' : space.zone;
}
