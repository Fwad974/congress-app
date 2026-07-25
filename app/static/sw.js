/* Service worker for Web Push.
 *
 * Receives push messages (delivered even when no app tab is open) and shows a
 * system notification. Clicking it focuses an existing tab on the target URL or
 * opens a new one. Registered from notifications.js once the user has granted
 * notification permission and the server reports push is configured.
 */
'use strict';

self.addEventListener('install', function(){
  self.skipWaiting();
});

self.addEventListener('activate', function(e){
  e.waitUntil(self.clients.claim());
});

self.addEventListener('push', function(e){
  let data = {};
  try { data = e.data ? e.data.json() : {}; }
  catch (_) { data = { title: 'Notification', body: e.data ? e.data.text() : '' }; }

  const title = data.title || 'Notification';
  const options = {
    body: data.body || '',
    icon: '/static/favicon.png',
    badge: '/static/favicon.png',
    tag: data.tag || undefined,
    data: { url: data.url || '/schedule' },
  };
  e.waitUntil(self.registration.showNotification(title, options));
});

// The browser can rotate a push subscription (key change / expiry). Without a
// handler the old subscription silently dies and notifications stop. Re-fetch
// the server key, re-subscribe, and register the new subscription.
self.addEventListener('pushsubscriptionchange', function(e){
  e.waitUntil((async function(){
    try {
      const res = await fetch('/api/notifications/push/key', { credentials: 'same-origin' });
      if (!res.ok) return;
      const cfg = await res.json();
      if (!cfg.enabled || !cfg.public_key) return;
      const key = Uint8Array.from(
        atob((cfg.public_key + '='.repeat((4 - cfg.public_key.length % 4) % 4))
          .replace(/-/g, '+').replace(/_/g, '/')),
        function(c){ return c.charCodeAt(0); });
      const sub = await self.registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: key,
      });
      await fetch('/api/notifications/push/subscribe', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sub),
      });
    } catch (_) { /* best effort — in-app feed still works */ }
  })());
});

self.addEventListener('notificationclick', function(e){
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/schedule';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function(list){
      for (const c of list) {
        if (c.url.indexOf(url) !== -1 && 'focus' in c) return c.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
