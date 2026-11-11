import test from 'node:test';
import assert from 'node:assert/strict';
import {parkingViolation} from '../src/uiState.js';

const bay = (space_type, vehicle_type, status = 'occupied') => ({space_id: 'A-05', space_type, vehicle_type, status});

test('reserved bays with a different vehicle type are violations (camera 15:35 cases)', () => {
  assert.equal(parkingViolation(bay('ev', 'general')), true);        // 개발원 A-05 58번
  assert.equal(parkingViolation(bay('ev', 'accessible')), true);     // 부산역 A-07 49번, 개발원 A-06 32번
  assert.equal(parkingViolation(bay('accessible', 'general')), true); // 개발원 B-06 40번
  assert.equal(parkingViolation(bay('accessible', 'ev')), true);
});

test('matching or general bays are never violations', () => {
  assert.equal(parkingViolation(bay('ev', 'ev')), false);
  assert.equal(parkingViolation(bay('accessible', 'accessible')), false);
  assert.equal(parkingViolation(bay('general', 'ev')), false);
  assert.equal(parkingViolation(bay('general', 'accessible')), false);
});

test('unknown vehicle type, empty or uncertain bays are not flagged', () => {
  assert.equal(parkingViolation(bay('ev', null)), false);
  assert.equal(parkingViolation(bay('ev', 'general', 'empty')), false);
  assert.equal(parkingViolation(bay('ev', 'general', 'uncertain')), false);
  assert.equal(parkingViolation(null), false);
});
