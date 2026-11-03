export function registerPwa() {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  navigator.serviceWorker.register('/sw.js', {updateViaCache: 'none'}).then(registration => {
    const announce = () => window.dispatchEvent(new CustomEvent('parkon-update', {detail: registration}));
    if (registration.waiting) announce();
    registration.addEventListener('updatefound', () => {
      registration.installing?.addEventListener('statechange', event => {
        if (event.target.state === 'installed' && navigator.serviceWorker.controller) announce();
      });
    });
  }).catch(() => window.dispatchEvent(new Event('parkon-install-unavailable')));
}
