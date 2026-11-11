import test from 'node:test';
import assert from 'node:assert/strict';
import {chatSafetyScope} from '../src/uiState.js';

const fireA = [{lot_id: 'A', level: 2, status: 'active'}];
const lv2View = {banner: {level: 2, text: '개발원주차장 모의 화재'}, safety: {title: '대피'}};

test('fire in another lot only adds a notice for the selected lot', () => {
  const scope = chatSafetyScope(lv2View, fireA, 'C');
  assert.equal(scope.unsafe, false);
  assert.equal(scope.relevant, false);
  assert.equal(scope.otherLotNotice, true);
  assert.deepEqual(scope.otherLots, ['A']);
});

test('fire in the selected lot keeps the evacuation screen', () => {
  const scope = chatSafetyScope(lv2View, fireA, 'A');
  assert.equal(scope.unsafe, true);
  assert.equal(scope.otherLotNotice, false);
});

test('unknown selection or a stale alert list follows the server (safe side)', () => {
  assert.equal(chatSafetyScope(lv2View, fireA, undefined).unsafe, true);
  assert.equal(chatSafetyScope(lv2View, [], 'C').unsafe, true);
  assert.equal(chatSafetyScope(lv2View, [{lot_id: 'A', level: 2, status: 'resolved'}], 'C').unsafe, true);
});

test('level 1 or no warning never turns on the evacuation screen', () => {
  assert.equal(chatSafetyScope({banner: {level: 1, text: '확인'}}, fireA, 'A').unsafe, false);
  assert.equal(chatSafetyScope({}, fireA, 'C').otherLotNotice, false);
});

test('every chat screen passes the selected lot to ChatPanel (regression: 6142e8e dropped it)', async () => {
  const {readFile} = await import('node:fs/promises');
  for (const file of ['../src/App.jsx', '../src/MobileParkingApp.jsx']) {
    const source = await readFile(new URL(file, import.meta.url), 'utf8');
    const uses = source.match(/<ChatPanel\b[^>]*>/g) || [];
    assert.ok(uses.length > 0, file);
    for (const use of uses) assert.match(use, /\blotId=\{lotId\}/, `${file}: ${use}`);
  }
});
