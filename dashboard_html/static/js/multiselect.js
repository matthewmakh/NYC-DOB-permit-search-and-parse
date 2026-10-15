// ==========================================
// Multi-select filter component
// ------------------------------------------
// Progressive enhancement over a native <select>. The select stays in the
// DOM and remains the source of truth, so anything that already reads or
// writes it (clearFilters, saved views, a no-JS fallback) keeps working.
//
//   <select multiple data-multiselect data-placeholder="All types">
//
// A select carrying data-multiselect WITHOUT the `multiple` attribute is
// enhanced as a single-choice control with the same look — used for the
// recent-permits window, where the options are nested date ranges and a
// union would just mean the widest one.
// ==========================================

(function (global) {
    'use strict';

    const SEARCH_THRESHOLD = 12;
    const instances = new Map();

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function readOptions(select) {
        // Flatten optgroups into a list, remembering which group each option
        // came from so the panel can print the group headings.
        const items = [];
        Array.from(select.children).forEach(child => {
            if (child.tagName === 'OPTGROUP') {
                Array.from(child.children).forEach(opt => {
                    items.push({ value: opt.value, label: opt.textContent.trim(), group: child.label });
                });
            } else if (child.tagName === 'OPTION') {
                items.push({ value: child.value, label: child.textContent.trim(), group: null });
            }
        });
        return items;
    }

    function buildPanel(instance) {
        const { options, multiple, id } = instance;
        const parts = [];
        let lastGroup = null;

        options.forEach((option, index) => {
            if (option.group && option.group !== lastGroup) {
                parts.push(`<div class="ms-group-label">${escapeHtml(option.group)}</div>`);
                lastGroup = option.group;
            }
            const inputType = multiple ? 'checkbox' : 'radio';
            parts.push(
                `<label class="ms-option" data-index="${index}" data-value="${escapeHtml(option.value)}">` +
                    `<input type="${inputType}" name="${escapeHtml(id)}-opt" value="${escapeHtml(option.value)}">` +
                    `<span>${escapeHtml(option.label)}</span>` +
                `</label>`
            );
        });

        parts.push('<div class="ms-empty" hidden>No matches</div>');
        return parts.join('');
    }

    function render(instance) {
        const selected = instance.getValues();
        const selectedSet = new Set(selected);

        const labelFor = new Map(instance.options.map(o => [o.value, o.label]));
        const labels = selected.map(value => labelFor.get(value)).filter(Boolean);

        const valueEl = instance.root.querySelector('.ms-value');

        // One pick reads better by name; past that the chips below carry the
        // names, so the trigger just counts them. Compact controls have no
        // room for chips, so they always join the labels instead.
        if (!labels.length) {
            valueEl.textContent = instance.placeholder;
            valueEl.classList.add('is-placeholder');
        } else if (labels.length === 1 || instance.compact) {
            valueEl.textContent = labels.join(', ');
            valueEl.classList.remove('is-placeholder');
        } else {
            valueEl.textContent = `${labels.length} selected`;
            valueEl.classList.remove('is-placeholder');
        }

        instance.root.querySelectorAll('.ms-option').forEach(optionEl => {
            const isSelected = selectedSet.has(optionEl.dataset.value);
            optionEl.classList.toggle('is-selected', isSelected);
            const input = optionEl.querySelector('input');
            if (input) input.checked = isSelected;
        });

        const footerCount = instance.root.querySelector('.ms-footer-count');
        if (footerCount) {
            footerCount.textContent = labels.length
                ? `${labels.length} selected`
                : instance.placeholder;
        }
        const clearBtn = instance.root.querySelector('.ms-clear');
        if (clearBtn) clearBtn.disabled = !labels.length;

        renderChips(instance, selected, labels);
    }

    function renderChips(instance, selected, labels) {
        const chipsEl = instance.root.querySelector('.ms-chips');
        if (!chipsEl) return;
        // A single pick already reads on the trigger; chips earn their space
        // only once the selection is too wide to show there.
        if (!instance.multiple || instance.compact || labels.length < 2) {
            chipsEl.innerHTML = '';
            return;
        }
        chipsEl.innerHTML = selected.map((value, i) =>
            `<span class="ms-chip"><span>${escapeHtml(labels[i])}</span>` +
            `<button type="button" data-value="${escapeHtml(value)}" ` +
            `aria-label="Remove ${escapeHtml(labels[i])}">&times;</button></span>`
        ).join('');
    }

    function filterOptions(instance, query) {
        const needle = query.trim().toLowerCase();
        let visible = 0;
        let lastVisibleGroup = null;

        instance.root.querySelectorAll('.ms-group-label').forEach(el => { el.hidden = true; });

        instance.root.querySelectorAll('.ms-option').forEach(optionEl => {
            const option = instance.options[Number(optionEl.dataset.index)];
            const match = !needle || option.label.toLowerCase().includes(needle) ||
                option.value.toLowerCase().includes(needle);
            optionEl.hidden = !match;
            optionEl.classList.remove('is-active');
            if (match) {
                visible += 1;
                if (option.group && option.group !== lastVisibleGroup) {
                    lastVisibleGroup = option.group;
                    // Un-hide the heading that precedes this option.
                    let prev = optionEl.previousElementSibling;
                    while (prev && !prev.classList.contains('ms-group-label')) {
                        prev = prev.previousElementSibling;
                    }
                    if (prev) prev.hidden = false;
                }
            }
        });

        const emptyEl = instance.root.querySelector('.ms-empty');
        if (emptyEl) emptyEl.hidden = visible > 0;
    }

    function open(instance) {
        closeAll(instance);
        instance.root.classList.add('is-open');
        instance.panel.hidden = false;
        instance.trigger.setAttribute('aria-expanded', 'true');
        const search = instance.root.querySelector('.ms-search');
        if (search) {
            search.value = '';
            filterOptions(instance, '');
            search.focus();
        }
        keepInView(instance);
    }

    function close(instance) {
        if (!instance.root.classList.contains('is-open')) return;
        instance.root.classList.remove('is-open');
        instance.panel.hidden = true;
        instance.trigger.setAttribute('aria-expanded', 'false');
        instance.root.querySelectorAll('.ms-option.is-active')
            .forEach(el => el.classList.remove('is-active'));
    }

    function closeAll(except) {
        instances.forEach(instance => {
            if (instance !== except) close(instance);
        });
    }

    function keepInView(instance) {
        // The sidebar scrolls; a panel opened near its bottom edge would be
        // clipped, so flip it above the trigger when there is more room there.
        const rect = instance.trigger.getBoundingClientRect();
        const spaceBelow = window.innerHeight - rect.bottom;
        const spaceAbove = rect.top;
        if (spaceBelow < 240 && spaceAbove > spaceBelow) {
            instance.panel.style.top = 'auto';
            instance.panel.style.bottom = 'calc(100% + 4px)';
        } else {
            instance.panel.style.top = '';
            instance.panel.style.bottom = '';
        }
    }

    function setValues(instance, values, options) {
        const ordered = (values || []).map(String);
        const wanted = new Set(ordered);
        instance.order = ordered;
        Array.from(instance.select.options).forEach(opt => {
            opt.selected = wanted.has(opt.value);
        });
        render(instance);
        if (!options || options.silent !== true) {
            instance.select.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }

    // Apply an explicit on/off for one option — used by the panel's change
    // handler, where the input already carries the intended state.
    function applyOption(instance, value, selected) {
        if (!instance.multiple) {
            setValues(instance, value ? [value] : []);
            close(instance);
            instance.trigger.focus();
            return;
        }
        const current = instance.getValues();
        const next = selected
            ? (current.includes(value) ? current : current.concat([value]))
            : current.filter(v => v !== value);
        setValues(instance, next);
    }

    // Flip whichever way the option currently sits — used by the keyboard
    // path and the chip remove buttons.
    function toggleValue(instance, value) {
        if (!instance.multiple) {
            applyOption(instance, value, true);
            return;
        }
        applyOption(instance, value, !instance.getValues().includes(value));
    }

    function moveActive(instance, delta) {
        const visible = Array.from(instance.root.querySelectorAll('.ms-option'))
            .filter(el => !el.hidden);
        if (!visible.length) return;
        const currentIndex = visible.findIndex(el => el.classList.contains('is-active'));
        let nextIndex = currentIndex + delta;
        if (nextIndex < 0) nextIndex = visible.length - 1;
        if (nextIndex >= visible.length) nextIndex = 0;
        visible.forEach(el => el.classList.remove('is-active'));
        const target = visible[nextIndex];
        target.classList.add('is-active');
        target.scrollIntoView({ block: 'nearest' });
    }

    function enhance(select) {
        if (!select || select.dataset.msReady === '1') return null;

        const multiple = select.multiple;
        const compact = select.dataset.compact !== undefined;
        const placeholder = select.dataset.placeholder || 'Any';
        // A single-choice control needs its empty option as a real choice;
        // a multi-select expresses "none" by having nothing selected.
        const options = readOptions(select).filter(o => multiple ? o.value !== '' : true);

        const root = document.createElement('div');
        root.className = 'ms';
        root.dataset.msFor = select.id || '';
        if (compact) root.dataset.msCompact = select.dataset.compact || '';
        select.parentNode.insertBefore(root, select);
        root.appendChild(select);
        select.dataset.msReady = '1';

        const needsSearch = options.length > SEARCH_THRESHOLD;
        const searchPlaceholder = select.dataset.searchPlaceholder || 'Search…';

        // The panel is anchored to .ms-control rather than .ms so that the
        // chips, which sit below, don't push it away from the trigger.
        root.insertAdjacentHTML('beforeend', `
            <div class="ms-control">
                <button type="button" class="ms-trigger" aria-haspopup="listbox" aria-expanded="false">
                    <span class="ms-value is-placeholder">${escapeHtml(placeholder)}</span>
                    <svg class="ms-caret" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                        <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.6"
                            stroke-linecap="round" stroke-linejoin="round"/>
                    </svg>
                </button>
                <div class="ms-panel" role="listbox" ${multiple ? 'aria-multiselectable="true"' : ''} hidden>
                    ${needsSearch ? `<input type="text" class="ms-search" placeholder="${escapeHtml(searchPlaceholder)}">` : ''}
                    <div class="ms-options"></div>
                    ${multiple ? `<div class="ms-footer">
                        <span class="ms-footer-count"></span>
                        <button type="button" class="ms-clear">Clear</button>
                    </div>` : ''}
                </div>
            </div>
            <div class="ms-chips"></div>
        `);

        const instance = {
            select,
            root,
            options,
            multiple,
            compact,
            placeholder,
            id: select.id || `ms-${instances.size}`,
            trigger: root.querySelector('.ms-trigger'),
            panel: root.querySelector('.ms-panel'),
            // The select is the source of truth for WHICH options are on;
            // `order` remembers the sequence they were picked in. That matters
            // for the Sort control, where key order decides the tiebreakers —
            // selectedOptions alone would always come back in DOM order.
            order: [],
            getValues() {
                const selected = Array.from(select.selectedOptions)
                    .map(o => o.value)
                    .filter(v => v !== '');
                const order = instance.order;
                // Stable sort, so anything not in `order` keeps its DOM order
                // at the end (e.g. values set programmatically).
                return selected.slice().sort((a, b) => {
                    const ia = order.indexOf(a);
                    const ib = order.indexOf(b);
                    if (ia === -1 && ib === -1) return 0;
                    if (ia === -1) return 1;
                    if (ib === -1) return -1;
                    return ia - ib;
                });
            },
        };

        root.querySelector('.ms-options').innerHTML = buildPanel(instance);

        instance.trigger.addEventListener('click', () => {
            if (root.classList.contains('is-open')) {
                close(instance);
            } else {
                open(instance);
            }
        });

        // Listen on `change`, not `click`: clicking the label text forwards a
        // second synthetic click to the input, which would toggle twice.
        instance.panel.addEventListener('change', event => {
            const input = event.target.closest('.ms-option input');
            if (!input) return;
            applyOption(instance, input.value, input.checked);
        });

        instance.panel.addEventListener('click', event => {
            if (event.target.closest('.ms-clear')) setValues(instance, []);
        });

        const searchEl = root.querySelector('.ms-search');
        if (searchEl) {
            searchEl.addEventListener('input', () => filterOptions(instance, searchEl.value));
        }

        root.querySelector('.ms-chips').addEventListener('click', event => {
            const button = event.target.closest('button[data-value]');
            if (!button) return;
            toggleValue(instance, button.dataset.value);
        });

        root.addEventListener('keydown', event => {
            if (event.key === 'Escape') {
                if (root.classList.contains('is-open')) {
                    event.stopPropagation();
                    close(instance);
                    instance.trigger.focus();
                }
                return;
            }
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                if (!root.classList.contains('is-open')) {
                    open(instance);
                    return;
                }
                moveActive(instance, event.key === 'ArrowDown' ? 1 : -1);
                return;
            }
            // The trigger is a <button>, so Enter/Space already opens it via a
            // native click. Only the active-option case needs handling here.
            if (event.key === 'Enter' || (event.key === ' ' && event.target !== searchEl)) {
                const active = root.querySelector('.ms-option.is-active');
                if (root.classList.contains('is-open') && active) {
                    event.preventDefault();
                    toggleValue(instance, active.dataset.value);
                }
            }
        });

        instances.set(instance.id, instance);
        render(instance);
        return instance;
    }

    // Re-read the underlying select. Callers that add an <option> after
    // enhancement (the play-recommended sort keys) need this to see it.
    function refresh(instance) {
        if (!instance) return;
        instance.options = readOptions(instance.select)
            .filter(o => instance.multiple ? o.value !== '' : true);
        instance.root.querySelector('.ms-options').innerHTML = buildPanel(instance);
        render(instance);
    }

    function initAll(scope) {
        (scope || document).querySelectorAll('select[data-multiselect]').forEach(enhance);
    }

    function get(id) {
        return instances.get(id) || null;
    }

    document.addEventListener('click', event => {
        if (!event.target.closest('.ms')) closeAll(null);
    });

    window.addEventListener('resize', () => closeAll(null));

    global.MultiSelect = {
        init: initAll,
        enhance,
        get,
        /** Selected values for an enhanced select, by element id. */
        values(id) {
            const instance = instances.get(id);
            return instance ? instance.getValues() : [];
        },
        /** Replace the selection. Pass {silent: true} to skip the change event. */
        set(id, values, options) {
            const instance = instances.get(id);
            if (instance) setValues(instance, values, options);
        },
        /** Re-read the select after its <option> list changed. */
        refresh(id) {
            refresh(instances.get(id));
        },
        /** Clear without firing change — for bulk resets that reload once. */
        clear(id) {
            const instance = instances.get(id);
            if (instance) setValues(instance, [], { silent: true });
        },
        /**
         * Clear every enhanced control, or only those inside `scope`.
         * Scope it when clearing filters, so the toolbar's sort and per-page
         * controls keep their defaults.
         */
        clearAll(scope) {
            instances.forEach(instance => {
                if (scope && !scope.contains(instance.select)) return;
                setValues(instance, [], { silent: true });
            });
        },
    };
})(window);
