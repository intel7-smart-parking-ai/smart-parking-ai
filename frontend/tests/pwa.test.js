import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

async function worker({network = async () => new Response('network')} = {}) {
  const handlers = {};
  const accesses = [];
  const script = (await readFile(new URL('../public/sw-template.js', import.meta.url), 'utf8'))
    .replace('__CACHE_VERSION__','parkon-shell-test').replace('__SHELL_FILES__', JSON.stringify(['/index.html','/assets/app.js']));
  const cache = {match: async key => {accesses.push(key); return new Response('cached-shell');}, addAll: async () => {}};
  vm.runInNewContext(script, {URL, fetch: network, caches: {open: async () => cache}, self: {
    location: {origin:'https://parking.test'}, addEventListener: (name, fn) => {handlers[name] = fn;},
  }});
  return {handlers, accesses};
}
test('PWA manifest launches the mobile app in standalone mode', async () => {
  const manifest = JSON.parse(await readFile(new URL('../public/manifest.webmanifest', import.meta.url)));
  assert.equal(manifest.start_url, '/?app=1');
  assert.equal(manifest.display, 'standalone');
  assert.ok(manifest.icons.some(icon => icon.sizes === '192x192'));
  assert.ok(manifest.icons.some(icon => icon.sizes === '512x512' && icon.purpose === 'maskable'));
});
test('availability always uses the network and never reads shell cache', async () => {
  const {handlers, accesses} = await worker();
  let response;
  handlers.fetch({request:{url:'https://parking.test/demo-api/v1/parking-lots',method:'GET'},respondWith:value => {response=value;}});
  assert.equal(await (await response).text(), 'network');
  assert.equal(accesses.length,0);
});
test('offline API fails instead of returning cached availability', async () => {
  const {handlers, accesses} = await worker({network:async () => {throw new Error('offline');}});
  let response;
  handlers.fetch({request:{url:'https://parking.test/demo-api/v1/parking-lots',method:'GET'},respondWith:value => {response=value;}});
  await assert.rejects(response,/offline/);
  assert.equal(accesses.length,0);
});
test('offline navigation never serves a cached authenticated page', async () => {
  const {handlers, accesses} = await worker({network:async () => {throw new Error('offline');}});
  let response;
  handlers.fetch({request:{url:'https://parking.test/?app=1',method:'GET',mode:'navigate'},respondWith:value => {response=value;}});
  await assert.rejects(response,/offline/);
  assert.equal(accesses.length,0);
});
test('payment posts cannot be cached or queued by the service worker', async () => {
  const {handlers, accesses} = await worker({network:async request => {
    assert.equal(request.method,'POST'); throw new Error('offline');
  }});
  let response;
  handlers.fetch({request:{url:'https://parking.test/demo-api/v1/mock-payments',method:'POST'},respondWith:value => {response=value;}});
  await assert.rejects(response,/offline/);
  assert.equal(accesses.length,0);
});
