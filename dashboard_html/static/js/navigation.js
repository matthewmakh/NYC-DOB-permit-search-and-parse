document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.site-nav').forEach(nav => {
        const toggle = nav.querySelector('.site-nav__toggle');
        if (!toggle) return;

        const setOpen = open => {
            nav.classList.toggle('is-open', open);
            toggle.setAttribute('aria-expanded', String(open));
            toggle.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
        };

        toggle.addEventListener('click', () => setOpen(!nav.classList.contains('is-open')));
        nav.querySelectorAll('.site-nav__link').forEach(link => {
            link.addEventListener('click', () => setOpen(false));
        });
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && nav.classList.contains('is-open')) {
                setOpen(false);
                toggle.focus();
            }
        });
        window.addEventListener('resize', () => {
            if (window.innerWidth > 1180) setOpen(false);
        });
    });
});


// ---------- Appearance (Auto / Light / Dark) ----------
// Shared with the CRM through one localStorage key. `auto` follows the OS and
// re-applies live when the system flips at sunset.
(function () {
    const ICONS = { auto: 'fa-circle-half-stroke', light: 'fa-sun', dark: 'fa-moon' };
    const media = window.matchMedia('(prefers-color-scheme: dark)');

    function readPref() {
        try { return localStorage.getItem('theme') || localStorage.getItem('crm-theme') || 'auto'; }
        catch (e) { return 'auto'; }
    }

    function applyTheme(pref) {
        const dark = pref === 'dark' || (pref === 'auto' && media.matches);
        const root = document.documentElement;
        root.setAttribute('data-theme', dark ? 'dark' : 'light');
        root.setAttribute('data-theme-pref', pref);
        if (dark) root.setAttribute('data-crm-theme', 'dark');
        else root.removeAttribute('data-crm-theme');
        document.querySelectorAll('[data-theme-icon]').forEach(icon => {
            icon.className = `fas ${ICONS[pref] || ICONS.auto}`;
        });
        document.querySelectorAll('[data-set-theme]').forEach(btn => {
            btn.setAttribute('aria-checked', String(btn.dataset.setTheme === pref));
        });
        document.dispatchEvent(new CustomEvent('themechange', { detail: { pref, dark } }));
    }

    function setPref(pref) {
        try { localStorage.setItem('theme', pref); localStorage.setItem('crm-theme', pref); } catch (e) { /* */ }
        applyTheme(pref);
    }

    media.addEventListener('change', () => { if (readPref() === 'auto') applyTheme('auto'); });
    window.addEventListener('storage', e => { if (e.key === 'theme' || e.key === 'crm-theme') applyTheme(readPref()); });

    document.addEventListener('DOMContentLoaded', () => {
        applyTheme(readPref());
        const wrap = document.getElementById('themeSwitch');
        if (!wrap) return;
        const btn = wrap.querySelector('.theme-switch__btn');
        const menu = wrap.querySelector('.theme-switch__menu');
        const open = state => { menu.hidden = !state; btn.setAttribute('aria-expanded', String(state)); };
        btn.addEventListener('click', e => { e.stopPropagation(); open(menu.hidden); });
        menu.addEventListener('click', e => {
            const choice = e.target.closest('[data-set-theme]');
            if (!choice) return;
            setPref(choice.dataset.setTheme);
            open(false);
        });
        document.addEventListener('click', e => { if (!wrap.contains(e.target)) open(false); });
        document.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) { open(false); btn.focus(); } });
    });

    window.AppTheme = { apply: applyTheme, set: setPref, read: readPref };
})();
