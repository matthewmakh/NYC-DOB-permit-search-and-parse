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
