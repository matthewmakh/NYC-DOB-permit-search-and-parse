(function () {
    'use strict';
    const $ = (s, r) => (r || document).querySelector(s);
    const $all = (s, r) => Array.from((r || document).querySelectorAll(s));

    function bytes(base64) {
        const padding = '='.repeat((4 - base64.length % 4) % 4);
        const raw = atob((base64 + padding).replace(/-/g, '+').replace(/_/g, '/'));
        return Uint8Array.from(Array.from(raw).map(ch => ch.charCodeAt(0)));
    }
    async function post(path, body) {
        const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
        let data = {}; try { data = await response.json(); } catch (e) { /* ignored */ }
        if (!response.ok && !data.error) data.error = 'Request failed';
        return data;
    }
    async function registration() {
        if (!('serviceWorker' in navigator)) throw new Error('This browser does not support notifications');
        await navigator.serviceWorker.register('/crm/service-worker.js', { scope: '/crm/' });
        return navigator.serviceWorker.ready;
    }
    registration().catch(() => {});

    const inboxBadge = $('[data-notification-count]');
    let lastUnread = inboxBadge ? parseInt(inboxBadge.textContent, 10) || 0 : 0;
    async function refreshInboxCount() {
        try {
            const response = await fetch('/crm/api/notifications', { headers: { 'Accept': 'application/json' } });
            if (!response.ok) return;
            const data = await response.json();
            const unread = data.unread || 0;
            if (inboxBadge) {
                inboxBadge.textContent = unread;
                inboxBadge.hidden = !unread;
            }
            if (unread > lastUnread && window.crmToast) {
                window.crmToast('You have a new CRM notification', 'info');
            }
            lastUnread = unread;
        } catch (err) { /* stay quiet while offline */ }
    }
    window.setInterval(refreshInboxCount, 60000);

    document.addEventListener('click', async event => {
        const enable = event.target.closest('.js-enable-push');
        if (enable) {
            if (!window.CRM_PUSH_KEY) return;
            enable.disabled = true;
            try {
                const permission = await Notification.requestPermission();
                if (permission !== 'granted') throw new Error('Notifications were not allowed');
                const reg = await registration();
                let sub = await reg.pushManager.getSubscription();
                if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes(window.CRM_PUSH_KEY) });
                const data = await post('/crm/api/push/subscribe', { subscription: sub.toJSON() });
                if (!data.success) throw new Error(data.error || 'Could not enable push');
                enable.textContent = 'Push enabled';
                if (window.crmToast) window.crmToast('Notifications enabled on this device', 'success');
            } catch (err) {
                enable.disabled = false;
                if (window.crmToast) window.crmToast(err.message || 'Could not enable notifications', 'error');
            }
            return;
        }
        const markAll = event.target.closest('.js-notifications-read');
        if (markAll) {
            const data = await post('/crm/api/notifications/read');
            if (data.success) {
                $all('.crm-notification.is-unread').forEach(el => el.classList.remove('is-unread'));
                if (inboxBadge) { inboxBadge.textContent = '0'; inboxBadge.hidden = true; }
                lastUnread = 0;
            }
            return;
        }
        const note = event.target.closest('[data-notification-id]');
        if (note) post('/crm/api/notifications/read', { id: note.dataset.notificationId });
    });

    document.addEventListener('change', async event => {
        const input = event.target.closest('[data-pref]');
        if (!input) return;
        const value = input.type === 'checkbox' ? input.checked : input.value;
        const data = await post('/crm/api/notifications/preferences', { [input.dataset.pref]: value });
        if (!data.success && window.crmToast) window.crmToast(data.error || 'Could not save preference', 'error');
    });
})();
