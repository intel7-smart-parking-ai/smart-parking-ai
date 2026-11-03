// Protected deployment: do not serve authenticated pages from an offline cache.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('parkon-shell-')).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  if (new URL(event.request.url).origin === self.location.origin) {
    event.respondWith(fetch(event.request, {cache: 'no-store'}));
  }
});
