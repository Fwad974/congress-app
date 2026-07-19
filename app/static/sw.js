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
