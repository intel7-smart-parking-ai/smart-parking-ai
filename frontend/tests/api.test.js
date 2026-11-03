import test, {afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {request, loadLots, dataSource, lookupVehicle} from '../src/api.js';

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });
const ok = data => ({ok: true, json: async () => data});

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

test('team mutations are rejected before a network call', async () => {
  globalThis.fetch = () => { throw new Error('must not send'); };
  await assert.rejects(request('/mock-payments', {method: 'POST'}, 'team'), /조회만/);
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
  const result = await lookupVehicle('0022');
  assert.equal(result.vehicle_number, '0022');
  assert.equal(result.car_id, '22');
});

test('invalid number never triggers a request', async () => {
  globalThis.fetch = () => { throw new Error('must not send'); };
  for (const value of ['12', '12345', 'abcd', '']) await assert.rejects(lookupVehicle(value), /4자리/);
});

test('physical sticker lookup supports two-digit numbers through 99', async () => {
  globalThis.fetch = async path => {
    assert.equal(path, '/demo-api/v1/sticker-search?car_id=99');
    return ok({car_id: '99', lookup_type: 'sticker'});
  };
  assert.equal((await lookupVehicle('99','sticker')).car_id, '99');
});

test('physical sticker lookup rejects plate-sized and out-of-range inputs', async () => {
  globalThis.fetch = () => { throw new Error('must not send'); };
  for (const value of ['1234', '100', '']) await assert.rejects(lookupVehicle(value,'sticker'), /10~99/);
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

test('single-digit and zero-prefixed stickers are not found without a request', async () => {
 globalThis.fetch = () => {throw new Error('must not send');};
 for (let n=0;n<10;n++) for (const value of [String(n),String(n).padStart(2,'0')]) await assert.rejects(lookupVehicle(value,'sticker'), /차량이 없습니다/);
});
