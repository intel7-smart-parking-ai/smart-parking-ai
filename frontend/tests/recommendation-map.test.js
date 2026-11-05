import test from 'node:test';
import assert from 'node:assert/strict';
import {chatRecommendations,validCoordinates,readLotCoordinates} from '../src/recommendationMapData.js';
const make=()=>({card:{type:'empty_spots',data:{lots:{A:{name:'만차',display_empty:0,status_reason:'만차'},B:{name:'추천',display_empty:4,status_reason:null},C:{name:'경보',display_empty:6,status_reason:'경보'},D:{name:'차단',display_empty:3}},blocked_lots:['D']}},chips:[{id:'go:A'},{id:'go:B'},{id:'go:B:A'},{id:'go:C'},{id:'go:D'},{id:'go:unknown'}]});
test('only server recommended available lots appear; full and blocked lots are excluded',()=>assert.deepEqual(chatRecommendations(make()).map(l=>l.lot_id),['B']));
test('text mentioning a lot never becomes a fabricated recommendation',()=>assert.deepEqual(chatRecommendations({text:'B 추천',chips:[{id:'go:B'}]}),[]));
test('negative, unknown and non-numeric counts cannot become recommendations',()=>{for(const value of [-1,null,'4',NaN]){const r=make();r.card.data.lots.B.display_empty=value;assert.deepEqual(chatRecommendations(r),[])}});
test('valid coordinates accept zero and reject blanks, strings and out of range values',()=>{assert.ok(validCoordinates([0,0]));for(const value of [null,[],['129',35],[181,35],[129,91],[NaN,35]])assert.equal(validCoordinates(value),false)});
test('registered server location takes priority over browser overrides',()=>{assert.deepEqual(readLotCoordinates({lot_id:'B',longitude:129,latitude:35},{B:[128,34]}),[129,35]);assert.equal(readLotCoordinates({lot_id:'B'},{}),null);assert.deepEqual(readLotCoordinates({lot_id:'B'},{B:[128,34]}),[128,34])});
