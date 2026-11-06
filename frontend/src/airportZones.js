import {t} from './serviceLocale.js';
export const airportZones = {A: t('P1 국내선'), B: t('P2 국제선'), C: t('P3 여객·화물')};

export function airportZone(space) {
  // Preserve existing space IDs and sessions when moving the last row into P3.
  return space.zone === 'B' && Number(space.space_id.split('-')[1]) > 24 ? 'C' : space.zone;
}
