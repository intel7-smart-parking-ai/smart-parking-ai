import test from 'node:test';
import assert from 'node:assert/strict';
import {createChatSession} from '../src/chatSession.js';

function setup() {
  let id = 0;
  const calls = [];
  const chat = createChatSession({makeId: () => `id-${++id}`, request: (path, options) => new Promise((resolve, reject) => calls.push({path, options, resolve, reject}))});
  return {chat, calls};
}
const response = (version, extra={}) => ({runtime_id:'runtime-a',state_version:version,alert_version:0,text:'안내',chips:[],banner:null,safety:null,...extra});
async function ask(ctx, text, result) {ctx.chat.edit(text);const work=ctx.chat.send();ctx.calls.at(-1).resolve(result);await work;}

test('late response preserves a new draft, including a deliberately emptied draft', async () => {
  for(const draft of ['새 질문','']) {
    const {chat,calls}=setup();chat.edit('11번 위치');const work=chat.send();
    chat.edit('새 질문');chat.edit(draft);calls[0].resolve(response(1));await work;
    assert.equal(chat.getSnapshot().draft,draft);
  }
});
test('ambiguous payment retry reuses exactly the original envelope, token and ID',async()=>{
  const ctx=setup();await ask(ctx,'요금',response(1,{flow:'confirm_action',chips:[{id:'confirm_yes',confirmation_token:'token-1'}]}));
  const chip=ctx.chat.getSnapshot().view.chips[0];
  const work=ctx.chat.choose(chip,ctx.chat.getSnapshot().actionEpoch);
  const first=ctx.calls.at(-1).options.body;ctx.calls.at(-1).reject(new Error('timeout'));await work;
  const retry=ctx.chat.retry();assert.deepEqual(ctx.calls.at(-1).options.body,first);
  assert.equal(first.confirmation_token,'token-1');
  ctx.calls.at(-1).resolve(response(2,{card:{type:'payment',data:{amount:2500,payment_id:'receipt-1',paid_at:'2026-11-06T12:00:00+09:00'}}}));await retry;
  assert.equal(ctx.chat.getSnapshot().rows.filter(r=>r.who==='user').length,2);
  await ask(ctx,'새 업무',response(3));assert.notEqual(ctx.calls.at(-1).options.body.request_id,first.request_id);
  assert.equal(ctx.calls.at(-1).options.body.session_id,first.session_id);
});
test('Lv2 invalidates old confirmations and ignores an in-flight old reply in its entirety',async()=>{
  const ctx=setup();await ask(ctx,'요금',response(1,{chips:[{id:'confirm_yes',confirmation_token:'token'}]}));
  const epoch=ctx.chat.getSnapshot().actionEpoch;
  const work=ctx.chat.choose({id:'confirm_yes',confirmation_token:'token'},epoch);
  ctx.chat.invalidateAlerts([{alert_id:'alarm',level:2,status:'active'}]);
  ctx.calls.at(-1).resolve(response(2,{chips:[{id:'confirm_yes'}],card:{type:'fee'},safety:null,banner:null}));await work;
  assert.equal(ctx.chat.getSnapshot().view.banner.level,2);assert.ok(ctx.chat.getSnapshot().view.safety);
  assert.equal(ctx.chat.getSnapshot().view.card,undefined);
  const before=ctx.calls.length;await ctx.chat.choose({id:'confirm_yes',confirmation_token:'token'},epoch);
  assert.equal(ctx.calls.length,before);
});
test('a stale poll cannot overwrite newer card, chips, safety or banner',async()=>{
  const ctx=setup();await ask(ctx,'위치',response(1));
  const poll=ctx.chat.poll(),old=ctx.calls.at(-1);
  const receipt={type:'payment',data:{amount:1000,payment_id:'p-1',paid_at:'2026-11-06T12:00:00+09:00'}};
  await ask(ctx,'결제',response(3,{card:receipt,chips:[{id:'receipt'}],banner:{level:0,text:'차단'},safety:{title:'안내'}}));
  old.resolve({...response(2),messages:[{event_id:'old',text:'old',chips:[{id:'confirm_yes'}],card:{type:'fee'}}]});await poll;
  const view=ctx.chat.getSnapshot().view;
  assert.deepEqual(view.card,receipt);assert.equal(view.chips[0].id,'receipt');assert.equal(view.banner.text,'차단');assert.ok(view.safety);
});
test('state_version gates all response fields even on a newer transport request',async()=>{
  const ctx=setup();await ask(ctx,'조회',response(9,{card:{type:'payment'},chips:[{id:'new'}]}));
  const poll=ctx.chat.poll();ctx.calls.at(-1).resolve({...response(8),messages:[{event_id:'old',text:'old',card:{type:'fee'}}]});await poll;
  assert.equal(ctx.chat.getSnapshot().view.card.type,'payment');assert.equal(ctx.chat.getSnapshot().rows.length,2);
});
test('payment-block evidence keeps safety and banner level 0; unknown alert poll cannot erase it',async()=>{
  const ctx=setup();await ask(ctx,'결제',response(1,{safety:{title:'결제 중단',guidance:'안전 확인'},banner:{level:0,text:'결제가 차단됐어요'}}));
  const poll=ctx.chat.poll();ctx.calls.at(-1).resolve({...response(2),messages:[],chips:null});await poll;
  assert.equal(ctx.chat.getSnapshot().view.banner.level,0);assert.ok(ctx.chat.getSnapshot().view.safety);
});
test('polling does not remove pending confirmation when chips is null; expiry removes it',async()=>{
  const ctx=setup();await ask(ctx,'요금',response(1,{chips:[{id:'confirm_yes',confirmation_token:'token'}]}));
  let poll=ctx.chat.poll();ctx.calls.at(-1).resolve({...response(2),messages:[],chips:null});await poll;
  assert.equal(ctx.chat.getSnapshot().view.chips[0].confirmation_token,'token');
  poll=ctx.chat.poll();ctx.calls.at(-1).resolve({...response(3),messages:[],chips:[]});await poll;
  assert.deepEqual(ctx.chat.getSnapshot().view.chips,[]);
});
test('runtime restart invalidates tokens/retry without replaying payment on the new server',async()=>{
  const ctx=setup();await ask(ctx,'요금',response(1,{chips:[{id:'confirm_yes',confirmation_token:'token'}]}));
  const poll=ctx.chat.poll();ctx.calls.at(-1).resolve({...response(1),runtime_id:'runtime-b',messages:[]});await poll;
  assert.equal(ctx.chat.getSnapshot().restartRequired,true);
  const before=ctx.calls.length;await ctx.chat.send();assert.equal(ctx.calls.length,before);
});
test('200+ character voice/text draft remains whole and is not submitted',async()=>{
  const {chat,calls}=setup();const text='가'.repeat(201);chat.edit(text);await chat.send();
  assert.equal(chat.getSnapshot().draft,text);assert.equal(calls.length,0);assert.match(chat.getSnapshot().error,/200/);
});
test('double click creates one request and reset discards the old session response',async()=>{
  const {chat,calls}=setup();chat.edit('위치');const work=chat.send();chat.edit('다음');await chat.send();assert.equal(calls.length,1);
  chat.reset();calls[0].resolve(response(1));await work;assert.equal(chat.getSnapshot().rows.length,0);assert.equal(chat.getSnapshot().view,null);
});
test('HTTP 200 busy fallback retains the original request for retry',async()=>{
  const ctx=setup();await ask(ctx,'11번 위치',response(1,{meta:{busy:true,unavailable:true}}));
  const first=ctx.calls[0].options.body;
  const retry=ctx.chat.retry();assert.deepEqual(ctx.calls.at(-1).options.body,first);
  ctx.calls.at(-1).resolve(response(2));await retry;
  assert.equal(ctx.chat.getSnapshot().retry,null);
});

test('reply language follows a detected foreign question through chips and retries',async()=>{
 let id=0;const calls=[];
 const chat=createChatSession({language:'ko',makeId:()=>String(++id),request:(path,options)=>new Promise((resolve,reject)=>calls.push({path,options,resolve,reject}))});
 chat.edit('Where can I park?');const sent=chat.send();assert.equal(calls[0].options.body.language,'ko');calls[0].resolve(response(1,{language:'en',chips:[{id:'help',label:'Help'}]}));await sent;
 const chosen=chat.choose({id:'help',label:'Help'},chat.getSnapshot().actionEpoch);assert.equal(calls[1].options.body.language,'en');const body=calls[1].options.body;calls[1].reject(new Error('network'));await chosen;
 const retry=chat.retry();assert.deepEqual(calls[2].options.body,body);calls[2].resolve(response(2,{language:'en'}));await retry;
 chat.edit('한국어로 질문');const korean=chat.send();assert.equal(calls[3].options.body.language,'ko');calls[3].resolve(response(3,{language:'ko'}));await korean;
});
