/*
 * Self-uninstall service worker — temporary disable (2026-04-29).
 *
 * iOS Safari was silently dropping session cookies on the redirect-
 * after-login path when an SW was caching navigation responses.
 * Login bouncing was the symptom on jo-pip's tailnet HTTP deployment.
 *
 * Until the auth path is confirmed stable on every target device,
 * this SW does nothing except unregister itself and clear all
 * caches. Browsers that have it installed will fetch this on their
 * next 24-hour update check (or on any forced reload), see the
 * uninstall logic run, and stop intercepting future requests.
 *
 * Re-replace with the full SW once auth is verified.
 */

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
      const regs = await self.registration.unregister();
      // Tell controlled clients to navigate fresh so the page
      // reloads without an SW intercepting any subsequent fetches.
      const clients = await self.clients.matchAll({ type: 'window' });
      clients.forEach((c) => {
        try {
          c.navigate(c.url);
        } catch (_) {
          // navigate may be blocked on some browsers; ignored.
        }
      });
    })()
  );
});

// Pass everything through untouched. No caching, no interception.
self.addEventListener('fetch', () => {});
