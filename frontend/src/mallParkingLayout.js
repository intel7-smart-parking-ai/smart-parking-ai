export const mallZones = [['A',119,134],['B',822,134],['C',119,570],['D',822,570]];
export const mallRestrooms = {shopping:295, department:1180};

export function mallBayPosition(left, top, index) {
 const row = Math.floor(index / 8);
 return {x:left + index % 8 * 75, y:top + row * (top === 134 ? 191 : 182), h:row ? 120 : 132};
}

export function nearestMallRestroomSpace(lot, question, alerts) {
 if (!/(신세계|백화점)/.test(question) || !/화장실/.test(question) || !/빈\s*자리|빈\s*칸|빈\s*공간/.test(question)) return null;
 if (lot.layout_type !== 'mall' || alerts.some(alert => alert.lot_id === lot.lot_id && alert.status !== 'resolved')) return null;
 const type = /장애인/.test(question) ? 'accessible' : /전기차|충전/.test(question) ? 'ev' : 'general';
 const shoppingOnly = /(?:몰|[AC]\s*구역)(?:\s*(?:쪽|의))?\s*화장실/i.test(question);
 const departmentOnly = /(?:백화점\s*쪽|[BD]\s*구역)(?:\s*(?:쪽|의))?\s*화장실/i.test(question);
 const markers = Object.entries(mallRestrooms).filter(([wing]) =>
  (!shoppingOnly || wing === 'shopping') && (!departmentOnly || wing === 'department'));
 let best = null;
 for (const [zone,left,top] of mallZones) {
  const wing = zone === 'A' || zone === 'C' ? 'shopping' : 'department';
  const marker = markers.find(([key]) => key === wing);
  if (!marker) continue;
  const restroomX = marker[1] + 78 * .65 / 2;
  const restroomY = 10 + 86 * .65 / 2;
  lot.spaces.filter(space => space.zone === zone).forEach((space,index) => {
   if (space.status !== 'empty' || space.space_type !== type) return;
   const {x,y,h} = mallBayPosition(left,top,index);
   const distance = Math.hypot(x + 37.5 - restroomX, y + h / 2 - restroomY);
   if (!best || distance < best.distance) best = {space,distance};
  });
 }
 return best?.space ?? null;
}
