import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeLot,formatKST,currentKSTMonth,sourceLabel,evacuationRequired} from '../src/uiState.js';
import {submitMockPayment} from '../src/payments.js';
test('four-zone map uses space prefix independently from lot ID and keeps uncertain',()=>{
 const lot=normalizeLot({lot_id:'D'},[{space_id:'A-16',status:'empty'},{space_id:'B-01',status:'occupied'},{space_id:'C-13',status:'uncertain'},{space_id:'D-08',status:'unavailable'}]);
 assert.equal(lot.layout_type,'mall');assert.deepEqual(lot.spaces.map(s=>s.zone),['A','B','C','D']);assert.equal(lot.spaces[2].status,'uncertain');assert.equal(lot.spaces.filter(s=>s.status==='empty').length,1);
});
test('UTC and Korean timestamps display identical KST even on a non-Korean host',()=>{
 assert.equal(formatKST('2026-11-04T00:30:00Z'),formatKST('2026-11-04T09:30:00+09:00'));
 assert.match(formatKST('2026-11-04T00:30:00Z'),/09:30.*KST/);
 assert.equal(currentKSTMonth(new Date('2026-10-31T16:00:00Z')),'2026-11');
});
test('simulation/manual evidence remains visibly mock while unobserved stays pending',()=>{
 assert.equal(sourceLabel({source:'simulation'}),'모의 이벤트');assert.equal(sourceLabel({source:'manual'}),'모의 이벤트');assert.equal(sourceLabel({source:'vision',is_mock:false}),'카메라 관측');assert.equal(sourceLabel({}),'관측 대기');
});
test('only unresolved level2 alerts in the invoice lot stop payment',()=>{
 assert.equal(evacuationRequired([{lot_id:'A',status:'acknowledged',level:2}],'A'),true);
 assert.equal(evacuationRequired([{lot_id:'B',status:'active',level:2}],'A'),false);
 assert.equal(evacuationRequired([{lot_id:'A',status:'resolved',level:2}],'A'),false);
});
test('late evacuation event prevents payment POST after invoice was displayed',async()=>{
 const calls=[];const send=async(path,options)=>{calls.push([path,options]);return path==='/alerts'?[{lot_id:'A',status:'active',level:2}]:{amount_krw:1000,quote_id:'q'}};
 await assert.rejects(submitMockPayment({lot_id:'A',session_id:'s',fee:{amount_krw:1000}},'MOCK_CARD',false,{},send),e=>e.code==='EVACUATION_ACTIVE');
 assert.equal(calls.some(([,options])=>options?.method==='POST'),false);
});

test('safety API failure never proceeds to payment POST',async()=>{
 let posted=false;const send=async(path,options)=>{if(options?.method==='POST')posted=true;if(path==='/alerts')throw new Error('안전 상태 조회 실패');return {amount_krw:1000,quote_id:'q'}};
 await assert.rejects(submitMockPayment({lot_id:'A',session_id:'s',fee:{amount_krw:1000}},'MOCK_CARD',false,{},send),/안전 상태/);assert.equal(posted,false);
});
