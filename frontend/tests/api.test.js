import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {request, loadLots, dataSource, lookupVehicle} from '../src/api.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const ok = data => ({ok: true, json: async () => data});

test('vehicle search explains generic receiver errors without hiding specific errors', async () => {
  for (const [status, text] of [[422, '뒤 4자리'], [404, '찾지 못했습니다'], [503, '잠시 후']]) {
    globalThis.fetch = async () => ({ok: false, status, json: async () => ({
      error: {code: 'HTTP_ERROR', message: '요청이 허용되지 않았습니다.'},
    })});
    await assert.rejects(request('/vehicle-search?vehicle_number=1234', {}, 'team'),
      error => error.status === status && error.code === 'HTTP_ERROR' && error.message.includes(text));
  }
  globalThis.fetch = async () => ({ok: false, status: 404, json: async () => ({
    error: {code: 'VEHICLE_NOT_FOUND', message: '서버의 구체적 안내'},
  })});
  await assert.rejects(request('/vehicle-search?vehicle_number=1234', {}, 'team'),
    error => error.message === '서버의 구체적 안내');
});

test('default data source is the clearly labeled demo', () => assert.equal(dataSource, 'demo'));

test('team response keeps IDs, states and missing dedicated bay types', async () => {
  const calls = [];
  globalThis.fetch = async path => {
    calls.push(path);
    return path.endsWith('/spaces') ? ok({items: [
      {space_id: 'A-01', status: 'occupied', car_id: '01'},
      {space_id: 'A-02', status: 'uncertain', car_id: null},
      {space_id: 'A-03', status: 'unavailable', car_id: null},
    ], as_of: '2026-11-02T10:00:00+09:00'}) : ok({items: [{lot_id: 'A', type: 'real'}]});
  };
  const lots = await loadLots('team');
  assert.deepEqual(calls, ['/api/v1/parking-lots', '/api/v1/parking-lots/A/spaces']);
  assert.equal(lots[0].spaces[0].car_id, '01');
  assert.equal(lots[0].spaces[1].status, 'uncertain');
  assert.equal(lots[0].spaces[2].status, 'unavailable');
  assert.equal(lots[0].spaces[0].space_type, 'unknown');
  assert.equal(lots[0].updated_at, '2026-11-02T10:00:00+09:00');
});

test('team payment mutations reach the official route',async()=>{
 globalThis.fetch=async(path,options)=>{assert.equal(path,'/api/v1/mock-payments');assert.equal(options.method,'POST');return ok({status:'completed'})};
 assert.equal((await request('/mock-payments',{method:'POST',body:{session_id:'ses_01'}},'team')).status,'completed');
});

test('demo payment requests use the isolated proxy', async () => {
  globalThis.fetch = async (path, options) => {
    assert.equal(path, '/demo-api/v1/mock-payments');
    assert.equal(options.method, 'POST');
    assert.equal(JSON.parse(options.body).session_id, 'session-01');
    return ok({payment_id: 'demo-payment'});
  };
  assert.equal((await request('/mock-payments', {method: 'POST', body: {session_id: 'session-01'}}, 'demo')).payment_id, 'demo-payment');
});

test('nested team errors preserve the server message and code', async () => {
  globalThis.fetch = async () => ({ok: false, json: async () => ({error: {
    code: 'VEHICLE_NOT_FOUND', message: '목업 차량이 없습니다.', details: [],
  }})});
  await assert.rejects(request('/vehicles/24/location', {}, 'team'), error =>
    error.code === 'VEHICLE_NOT_FOUND' && error.message === '목업 차량이 없습니다.');
});

test('team network failures do not silently substitute demo data', async () => {
  const calls = [];
  globalThis.fetch = async path => { calls.push(path); throw new Error('offline'); };
  await assert.rejects(loadLots('team'), /offline/);
  assert.deepEqual(calls, ['/api/v1/parking-lots']);
});

test('partial paginated data is rejected instead of displayed as complete', async () => {
  globalThis.fetch = async () => ok({items: [], next_cursor: 'page-2'});
  await assert.rejects(loadLots('team'), /응답 형식/);
});

test('four-digit lookup preserves leading zeroes and uses demo search API', async () => {
  globalThis.fetch = async path => {
    assert.equal(path, '/demo-api/v1/vehicle-search?vehicle_number=0022');
    return ok({vehicle_number: '0022', car_id: '22', fee: {amount_krw: 3200}});
  };
  const result = await lookupVehicle('0022','plate');
  assert.equal(result.vehicle_number, '0022');
  assert.equal(result.car_id, '22');
});

test('invalid number never triggers a request', async () => {
  globalThis.fetch = () => { throw new Error('must not send'); };
  for (const value of ['12', '12345', 'abcd', '']) await assert.rejects(lookupVehicle(value,'plate'), /4자리/);
});

for(const number of ['10','99'])test('two-digit sticker '+number+' is sent unchanged',async()=>{
 globalThis.fetch=async path=>{assert.equal(path,'/api/v1/vehicles/'+number+'/location');return ok({car_id:number,session_id:null})};
 const result=await lookupVehicle(number,'sticker','team');assert.equal(result.car_id,number);assert.equal(result.fee,null);
});
test('single digit sticker is rejected with a two-digit instruction, never padded',async()=>{
 globalThis.fetch=()=>{throw new Error('must not send')};
 for(const value of ['1','7','9'])await assert.rejects(lookupVehicle(value,'sticker'),/두 자리로 입력/);
});
test('reserved and malformed sticker numbers never reach the network',async()=>{
 globalThis.fetch=()=>{throw new Error('must not send')};
 for(const value of ['00','07','09','100','1234',''])await assert.rejects(lookupVehicle(value,'sticker'),/10~99/);
});
test('sticker lookup gets server fee without calculating a price',async()=>{
 const calls=[];globalThis.fetch=async path=>{calls.push(path);return ok(path.includes('/location')?{car_id:'99',session_id:'ses_99'}:{session_id:'ses_99',amount_krw:7531,payment_status:'completed',fee_breakdown:{base_fee_krw:1000,extra_units:2,extra_fee_krw:6531}})};
 const result=await lookupVehicle('99','sticker','team');assert.equal(result.fee.amount_krw,7531);assert.equal(result.fee.payment_status,'completed');assert.deepEqual(calls,['/api/v1/vehicles/99/location','/api/v1/sessions/ses_99/fee']);
});
test('team virtual lookup uses same server and fetches its fee',async()=>{
 const calls=[];globalThis.fetch=async path=>{calls.push(path);return ok(path.includes('vehicle-search')?{session_id:'virtual-session',vehicle_number:'1234',car_id:null}:{amount_krw:3000,quote_id:'quote'})};
 const found=await lookupVehicle('1234','plate','team');assert.equal(found.car_id,null);assert.equal(found.fee.amount_krw,3000);assert.deepEqual(calls,['/api/v1/vehicle-search?vehicle_number=1234','/api/v1/sessions/virtual-session/fee']);
});
test('demo lot collection unwraps the common list envelope',async()=>{
 globalThis.fetch=async()=>ok({items:[{lot_id:'A',spaces:[]}],next_cursor:null,as_of:'2026-11-03T00:00:00Z'});
 assert.equal((await loadLots('demo'))[0].lot_id,'A');
});

test('legacy team URL still opens the final four-lot UI', async () => {
  const previous = globalThis.location;
  globalThis.location = {search: '?source=team'};
  try {
    const module = await import('../src/api.js?legacy-team-link-test');
    assert.equal(module.dataSource, 'demo');
  } finally {
    if (previous === undefined) delete globalThis.location;
    else globalThis.location = previous;
  }
});

test('fee API not implemented preserves successful location',async()=>{
 globalThis.fetch=async path=>path.includes('/location')?ok({car_id:'12',session_id:'ses_12'}):{ok:false,status:404,json:async()=>({detail:'Not Found'})};
 const result=await lookupVehicle('12','sticker','team');assert.equal(result.car_id,'12');assert.equal(result.fee,null);assert.match(result.fee_notice,/연결 대기/);
});

test('focus lot is refreshed every time, other lots reuse spaces for 30 seconds', async () => {
  const {clearLotCache} = await import('../src/api.js');
  clearLotCache();
  const calls = [];
  globalThis.fetch = async path => {
    calls.push(path);
    return path.endsWith('/spaces') ? ok({items: [{space_id: 'A-01', status: 'empty'}], as_of: '2026-11-10T13:00:00+09:00'})
      : ok({items: ['A', 'B', 'C', 'D'].map(lot_id => ({lot_id, type: 'real'}))});
  };
  await loadLots('team', {focusLotId: 'C'});
  assert.equal(calls.filter(p => p.endsWith('/spaces')).length, 4);  // first load: every lot
  calls.length = 0;
  const lots = await loadLots('team', {focusLotId: 'C'});
  assert.deepEqual(calls, ['/api/v1/parking-lots', '/api/v1/parking-lots/C/spaces']);
  assert.equal(lots.length, 4);
  assert.ok(lots.every(lot => lot.spaces.length === 1));  // cached lots still have their bays
  calls.length = 0;
  await loadLots('team');  // no focus → all lots (unchanged behaviour)
  assert.equal(calls.filter(p => p.endsWith('/spaces')).length, 4);
  clearLotCache();
});
