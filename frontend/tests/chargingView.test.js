import test from 'node:test';
import assert from 'node:assert/strict';
import {remainingText} from '../src/chargingView.js';
test('unknown charging time is never shown as zero',()=>{
 assert.match(remainingText(null),/정보 없음/);
 assert.match(remainingText({estimated_remaining_seconds:null}),/정보 없음/);
});
test('simulation remaining time and completion are distinct',()=>{
 assert.match(remainingText({estimated_remaining_seconds:75}),/1분 15초.*시연/);
 assert.equal(remainingText({estimated_remaining_seconds:0}),'충전 완료');
});
