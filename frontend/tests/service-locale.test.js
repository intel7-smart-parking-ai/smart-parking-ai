import test from 'node:test';
import assert from 'node:assert/strict';
import {getLanguage,t} from '../src/serviceLocale.js';

function languageEnvironment(search, saved, pathname='/') {
  globalThis.window={location:{search,pathname}};
  globalThis.localStorage={getItem:()=>saved};
}

test('service language follows the explicit welcome selection and preserves template values',()=>{
  try {
    languageEnvironment('?lang=en','ja');
    assert.equal(getLanguage(),'en');
    assert.equal(t('내 차 찾기'),'Find my car');
    assert.equal(t('{0}로 {1} 결제','CARD','KRW 1,000'),'Pay KRW 1,000 with CARD');
    languageEnvironment('?lang=zh-CN',null);
    assert.equal(t('요금 결제'),'停车缴费');
    languageEnvironment('?lang=ja',null);
    assert.equal(t('주차지도'),'駐車マップ');
  } finally {delete globalThis.window;delete globalThis.localStorage;}
});

test('saved language works across web and app links while admin stays Korean',()=>{
  try {
    languageEnvironment('?app=1','en');
    assert.equal(t('음성 입력'),'Voice input');
    languageEnvironment('?lang=en','en','/admin/');
    assert.equal(t('입차 시각'),'입차 시각');
  } finally {delete globalThis.window;delete globalThis.localStorage;}
});

test('unknown language and blocked storage fall back to Korean without changing vehicle IDs',()=>{
  try {
    languageEnvironment('?lang=unsupported','unsupported');
    assert.equal(getLanguage(),'ko');
    globalThis.localStorage={getItem:()=>{throw new Error('blocked')}};
    assert.equal(t('주차장 현황'),'주차장 현황');
    languageEnvironment('?lang=en',null);
    assert.equal(t('88가2000'),'88가2000');
  } finally {delete globalThis.window;delete globalThis.localStorage;}
});
