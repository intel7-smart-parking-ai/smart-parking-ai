import test from 'node:test';
import assert from 'node:assert/strict';
import {submitMockPayment} from '../src/payments.js';
const invoice = {session_id: 's-22'};
const quote = {quote_id: 'quote-s-22', payment: null};

test('changing method after a canceled attempt creates a new key', async () => {
  const attempts = {'s-22': {key: 'canceled-key', method: 'MOCK_EASY_PAY'}};
  const send = async (path, options) => {
    if (!options) return quote;
    assert.notEqual(options.body.idempotency_key, 'canceled-key');
    assert.equal(options.body.payment_method, 'MOCK_CARD');
    return {status: 'completed', payment_id: 'new-payment'};
  };
  assert.equal((await submitMockPayment(invoice, 'credit_card', false, attempts, send)).payment_id, 'new-payment');
});

test('same-method canceled key retries once with a fresh key', async () => {
  const attempts = {'s-22': {key: 'canceled-key', method: 'MOCK_EASY_PAY'}};
  const keys = [];
  const send = async (path, options) => {
    if (!options) return quote;
    keys.push(options.body.idempotency_key);
    return keys.length === 1 ? {status: 'canceled'} : {status: 'completed'};
  };
  assert.equal((await submitMockPayment(invoice, 'kakao_pay', false, attempts, send)).status, 'completed');
  assert.equal(keys.length, 2);
  assert.equal(keys[0], 'canceled-key');
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

test('failure simulation never sends unsupported fields or creates a payment',async()=>{
 let calls=0;await assert.rejects(submitMockPayment(invoice,'MOCK_CARD',true,{},async()=>{calls++}),/서버에는 결제를 요청하지/);assert.equal(calls,0);
});

test('official payment body contains only the three contract fields',async()=>{
 await submitMockPayment(invoice,'MOCK_ACCOUNT',false,{},async(path,options)=>{
 if(!options)return {payment_status:'unpaid',amount_krw:1000};
 assert.deepEqual(Object.keys(options.body).sort(),['idempotency_key','payment_method','session_id']);assert.equal(options.body.payment_method,'MOCK_ACCOUNT');return {status:'completed'};
 });
});
test('completed payment_status prevents another payment POST',async()=>{
 let calls=0;const result=await submitMockPayment(invoice,'MOCK_CARD',false,{},async()=>{calls++;return {payment_status:'completed',amount_krw:8900}});assert.equal(calls,1);assert.equal(result.status,'completed');assert.equal(result.amount_krw,8900);
});
for(const code of ['IDEMPOTENCY_CONFLICT','PAYMENT_CONFLICT','ALREADY_PAID'])test(code+' reconciles server state without duplicating payment',async()=>{
 let gets=0;const result=await submitMockPayment(invoice,'MOCK_CARD',false,{},async(path,options)=>{if(!options)return {payment_status:++gets===1?'unpaid':'completed',amount_krw:1200};throw Object.assign(new Error('conflict'),{code})});assert.equal(result.status,'completed');assert.equal(gets,2);
});

for(const code of ['PAYMENT_NOT_FOUND','INVALID_PAYMENT_STATE'])test(code+' clears a rejected idempotency attempt',async()=>{
 const attempts={};await assert.rejects(submitMockPayment(invoice,'MOCK_CARD',false,attempts,async(path,options)=>{if(!options)return {payment_status:'unpaid'};throw Object.assign(new Error(code),{code})}),error=>error.code===code);assert.equal(attempts[invoice.session_id],undefined);
});
