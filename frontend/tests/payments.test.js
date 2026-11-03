import test from 'node:test';
import assert from 'node:assert/strict';
import {submitMockPayment} from '../src/payments.js';
const invoice = {session_id: 's-22'};
const quote = {quote_id: 'quote-s-22', payment: null};

test('changing method after a cancelled attempt creates a new key', async () => {
  const attempts = {'s-22': {key: 'cancelled-key', method: 'kakao_pay'}};
  const send = async (path, options) => {
    if (!options) return quote;
    assert.notEqual(options.body.idempotency_key, 'cancelled-key');
    assert.equal(options.body.payment_method, 'credit_card');
    return {status: 'completed', payment_id: 'new-payment'};
  };
  assert.equal((await submitMockPayment(invoice, 'credit_card', false, attempts, send)).payment_id, 'new-payment');
});

test('same-method cancelled key retries once with a fresh key', async () => {
  const attempts = {'s-22': {key: 'cancelled-key', method: 'kakao_pay'}};
  const keys = [];
  const send = async (path, options) => {
    if (!options) return quote;
    keys.push(options.body.idempotency_key);
    return keys.length === 1 ? {status: 'cancelled'} : {status: 'completed'};
  };
  assert.equal((await submitMockPayment(invoice, 'kakao_pay', false, attempts, send)).status, 'completed');
  assert.equal(keys.length, 2);
  assert.equal(keys[0], 'cancelled-key');
  assert.notEqual(keys[1], keys[0]);
});

test('network failure retains a key for the retry', async () => {
  const attempts = {};
  const keys = [];
  const send = async (path, options) => {
    if (!options) return quote;
    keys.push(options.body.idempotency_key);
    if (keys.length === 1) throw new Error('offline');
    return {status: 'completed'};
  };
  await assert.rejects(submitMockPayment(invoice, 'naver_pay', false, attempts, send), /offline/);
  await submitMockPayment(invoice, 'naver_pay', false, attempts, send);
  assert.equal(keys[0], keys[1]);
});

test('an already completed server payment is shown without posting again', async () => {
  const send = async (path, options) => {
    assert.equal(options, undefined);
    return {...quote, payment: {status: 'completed', payment_id: 'existing'}};
  };
  assert.equal((await submitMockPayment(invoice, 'credit_card', false, {}, send)).payment_id, 'existing');
});

test('a simulated decline remains a visible error', async () => {
  const send = async (path, options) => {
    if (!options) return quote;
    assert.equal(options.body.simulate_failure, true);
    throw Object.assign(new Error('모의 결제가 거절되었습니다.'), {code: 'PAYMENT_DECLINED'});
  };
  await assert.rejects(submitMockPayment(invoice, 'credit_card', true, {}, send), /거절/);
});
