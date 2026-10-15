/* Responsive enhancements preserve the original inputs and their event handlers. */
document.addEventListener('DOMContentLoaded', () => {
    const mobile = window.matchMedia('(max-width: 860px)');
    // Preserve every deal field in a vertical, labeled record on a phone.
    document.querySelectorAll('.crm-deals-table').forEach(table => {
        const labels = [...table.querySelectorAll('thead th')].map(th => th.textContent.trim());
        table.querySelectorAll('tbody tr').forEach(row => {
            [...row.cells].forEach((cell, index) => { cell.dataset.label = labels[index] || ''; });
        });
        table.classList.add('mobile-record-table');
    });
    document.querySelectorAll('[data-mobile-filters]').forEach((panel, index) => {
        const anchor = document.createComment('Desktop filter position');
        panel.before(anchor);
        const toolbar = document.createElement('div');
        toolbar.className = 'mobile-filter-toolbar';
        toolbar.innerHTML = '<p>Refine your results</p><button type="button" class="btn btn-secondary mobile-filter-trigger" aria-haspopup="dialog" aria-expanded="false"><i class="fas fa-sliders" aria-hidden="true"></i> Filters <span class="mobile-filter-count"></span></button>';
        const host = document.querySelector('[data-mobile-filter-toolbar]') || panel.parentElement;
        host.prepend(toolbar);
        const trigger = toolbar.querySelector('button');
        const dialog = document.createElement('dialog');
        dialog.id = `mobileFilters${index}`;
        dialog.className = 'mobile-filter-dialog';
        dialog.setAttribute('aria-labelledby', `${dialog.id}Title`);
        trigger.setAttribute('aria-controls', dialog.id);
        dialog.innerHTML = `<header class="mobile-filter-dialog__head"><h2 id="${dialog.id}Title">Filters</h2><button type="button" class="btn btn-secondary" data-close>Done</button></header><div class="mobile-filter-dialog__body"></div><footer class="mobile-filter-dialog__foot"><button type="button" class="btn btn-primary" data-close>Show results</button></footer>`;
        document.body.append(dialog);
        const body = dialog.querySelector('.mobile-filter-dialog__body');
        const syncCount = () => {
            const count = [...panel.querySelectorAll('input:not([type=hidden]), select')].filter(input => {
                // Enhanced selects contain their own search box and option checkboxes.
                // Count the original select once, not its presentation controls.
                if (input.tagName !== 'SELECT' && input.closest('.ms')) return false;
                if (input.type === 'checkbox' || input.type === 'radio') return input.checked;
                if (input.multiple) return [...input.selectedOptions].some(option => option.value);
                if (input.tagName === 'SELECT') return input.value !== (input.querySelector('option[selected]') || input.options[0])?.value;
                return Boolean(input.value.trim());
            }).length;
            toolbar.querySelector('.mobile-filter-count').textContent = count ? `(${count})` : '';
        };
        const close = () => dialog.close();
        trigger.addEventListener('click', () => {
            syncCount();
            dialog.showModal();
            document.body.classList.add('mobile-dialog-open');
            trigger.setAttribute('aria-expanded', 'true');
        });
        dialog.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', close));
        dialog.addEventListener('click', event => { if (event.target === dialog) close(); });
        dialog.addEventListener('keydown', event => {
            if (event.key === 'Escape' && !event.defaultPrevented) {
                event.preventDefault();
                event.stopPropagation();
                close();
            }
        });
        dialog.addEventListener('close', () => {
            document.body.classList.remove('mobile-dialog-open');
            trigger.setAttribute('aria-expanded', 'false');
            syncCount();
            if (mobile.matches) trigger.focus({ preventScroll: true });
        });
        // Permit filters use an explicit Apply action; closing is safe after its handler runs.
        panel.querySelector('[onclick="applyFilters()"]')?.addEventListener('click', close);
        panel.addEventListener('input', syncCount);
        panel.addEventListener('change', syncCount);
        panel.addEventListener('click', () => queueMicrotask(syncCount));
        const adapt = () => {
            if (mobile.matches) body.append(panel);
            else {
                if (dialog.open) close();
                anchor.after(panel);
            }
        };
        mobile.addEventListener('change', adapt);
        adapt();
        syncCount();
    });

    const mapToggle = document.querySelector('.mobile-map-toggle');
    if (mapToggle) {
        const map = document.getElementById('constructionMap');
        document.body.classList.add('mobile-map-ready');
        const resizeMap = () => requestAnimationFrame(() => {
            // construction.js owns Leaflet; resizing it does not refetch data.
            if (typeof AppState !== 'undefined' && AppState.map) AppState.map.invalidateSize();
        });
        mapToggle.addEventListener('click', () => {
            const open = map.classList.toggle('is-mobile-visible');
            mapToggle.setAttribute('aria-expanded', String(open));
            mapToggle.textContent = open ? 'Hide permit map' : 'Show permit map';
            if (open) resizeMap();
        });
        mobile.addEventListener('change', resizeMap);
    }
});
