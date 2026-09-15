'use strict';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('push', event => {
    let payload = {};
    try { payload = event.data ? event.data.json() : {}; } catch (e) { payload = { body: event.data && event.data.text() }; }
    const title = payload.title || 'Permit CRM';
    event.waitUntil(self.registration.showNotification(title, {
        body: payload.body || 'You have a CRM update.',
        icon: '/static/crm/icon-192.png',
        badge: '/static/crm/icon-192.png',
        tag: payload.url || title,
        data: { url: payload.url || '/crm/' },
        renotify: false,
    }));
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    const target = new URL((event.notification.data && event.notification.data.url) || '/crm/', self.location.origin).href;
    event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
        for (const client of clients) {
            if (client.url.startsWith(self.location.origin) && 'focus' in client) {
                client.navigate(target);
                return client.focus();
            }
        }
        return self.clients.openWindow ? self.clients.openWindow(target) : undefined;
    }));
});
