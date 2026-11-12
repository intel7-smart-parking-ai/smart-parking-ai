import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeSpeechResults} from '../src/speechTranscript.js';

const results = (...pieces) => pieces.map(text => [{transcript: text}]);

test('PC 크롬: 새 조각만 오면 이어 붙인다', () => {
  assert.equal(mergeSpeechResults(results('주차장', '빈자리')), '주차장 빈자리');
});

test('안드로이드 크롬: 누적 조각은 중복 없이 마지막 것으로 교체한다', () => {
  assert.equal(mergeSpeechResults(results('주차장', '주차장 빈자리')), '주차장 빈자리');
  assert.equal(mergeSpeechResults(results('주', '주 차장')), '주 차장');
  assert.equal(mergeSpeechResults(results('주차장', '주차장 빈자리', '주차장 빈자리 있어')), '주차장 빈자리 있어');
});

test('같은 조각이 두 번 오거나 빈 조각이 섞여도 한 번만 들어간다', () => {
  assert.equal(mergeSpeechResults(results('주차장 빈자리', ' ', '주차장 빈자리')), '주차장 빈자리');
  assert.equal(mergeSpeechResults(results('A 주차장', '빈자리', '빈자리')), 'A 주차장 빈자리');
  assert.equal(mergeSpeechResults([]), '');
  assert.equal(mergeSpeechResults(undefined), '');
});
