// ==========================================
// Shared filter wiring
// ------------------------------------------
// The properties and contractors pages filter the same underlying data —
// permits joined to buildings — so they read the same controls and put the
// same params on the wire. This module owns that common half; each page keeps
// its own state object and decides what to do when a filter changes.
// ==========================================

(function (global) {
    'use strict';

    // id -> query param. Multi-select controls; each selected value goes on
    // the wire as its own repeated param.
    const MULTI_FILTERS = {
        boroughFilter: 'borough',
        propertyType: 'property_type',
        buildingClass: 'building_class',
        workType: 'work_type',
        jobType: 'job_type',
        permitType: 'permit_type',
        licenseType: 'license_type',
        violationsFilter: 'has_violations',
    };

    // id -> query param. Plain number inputs.
    const NUMBER_FILTERS = {
        minUnits: 'min_units',
        maxUnits: 'max_units',
        minValue: 'min_value',
        maxValue: 'max_value',
    };

    // Selects whose options are filled from /api/permits/facets rather than
    // being hardcoded, so they only ever offer codes that match real permits.
    const FACET_SELECTS = ['workType', 'jobType', 'permitType', 'licenseType'];

    function el(id) {
        return document.getElementById(id);
    }

    /** Current value of every shared filter, ready to merge into page state. */
    function read() {
        const out = {
            recentPermitDays: null,
            permitActivityMode: el('permitActivityMode')?.value === 'inactive'
                ? 'inactive'
                : 'within',
        };

        Object.keys(MULTI_FILTERS).forEach(id => {
            out[id] = el(id) ? MultiSelect.values(id) : [];
        });
        Object.keys(NUMBER_FILTERS).forEach(id => {
            const node = el(id);
            const raw = node && node.value !== '' ? Number(node.value) : null;
            out[id] = Number.isFinite(raw) ? raw : null;
        });

        const recent = el('recentPermitDays');
        const custom = el('recentPermitCustomDays');
        if (recent && recent.value && recent.value !== 'custom') {
            out.recentPermitDays = parseInt(recent.value, 10);
        } else if (custom && custom.value) {
            out.recentPermitDays = parseInt(custom.value, 10);
        }
        return out;
    }

    function repeatedValues(params, name) {
        const values = [];
        params.getAll(name).forEach(raw => {
            String(raw).split(',').forEach(value => {
                const clean = value.trim();
                if (clean && !values.includes(clean)) values.push(clean);
            });
        });
        return values;
    }

    /** Rebuild shared filter state from a deep-link query string. */
    function fromParams(params) {
        const out = {
            recentPermitDays: null,
            permitActivityMode: params.get('permit_activity_mode') === 'inactive'
                ? 'inactive'
                : 'within',
        };
        Object.entries(MULTI_FILTERS).forEach(([id, param]) => {
            out[id] = repeatedValues(params, param);
        });
        Object.entries(NUMBER_FILTERS).forEach(([id, param]) => {
            const raw = params.get(param);
            const value = raw !== null && raw !== '' ? Number(raw) : null;
            out[id] = Number.isFinite(value) ? value : null;
        });
        const recent = Number(params.get('recent_permit_days'));
        if (Number.isInteger(recent) && recent > 0 && recent <= 3650) {
            out.recentPermitDays = recent;
        }
        return out;
    }

    /** Apply restored shared state to native/enhanced controls silently. */
    function apply(filters) {
        const restored = filters || {};
        Object.keys(MULTI_FILTERS).forEach(id => {
            if (el(id)) MultiSelect.set(id, restored[id] || [], { silent: true });
        });
        Object.keys(NUMBER_FILTERS).forEach(id => {
            const node = el(id);
            if (!node) return;
            const value = restored[id];
            node.value = value === null || value === undefined ? '' : value;
        });

        const recent = el('recentPermitDays');
        const custom = el('recentPermitCustomDays');
        const days = restored.recentPermitDays;
        if (recent) {
            const standardValues = new Set(
                Array.from(recent.options).map(option => option.value));
            const selected = days && standardValues.has(String(days))
                ? String(days)
                : (days ? 'custom' : '');
            MultiSelect.set('recentPermitDays', [selected], { silent: true });
            if (custom) {
                custom.value = selected === 'custom' ? days : '';
                custom.style.display = selected === 'custom' ? 'block' : 'none';
            }
        }
        const mode = el('permitActivityMode');
        if (mode) {
            mode.value = restored.permitActivityMode === 'inactive'
                ? 'inactive'
                : 'within';
        }
        updateActivityHint();
    }

    /** Append the shared filters to a URLSearchParams. */
    function toParams(params, filters) {
        Object.entries(MULTI_FILTERS).forEach(([id, param]) => {
            (filters[id] || []).forEach(value => params.append(param, value));
        });
        Object.entries(NUMBER_FILTERS).forEach(([id, param]) => {
            const value = filters[id];
            if (value !== null && value !== undefined && value !== '') {
                params.append(param, value);
            }
        });
        if (filters.recentPermitDays) {
            params.append('recent_permit_days', filters.recentPermitDays);
            params.append(
                'permit_activity_mode',
                filters.permitActivityMode === 'inactive' ? 'inactive' : 'within'
            );
        }
        return params;
    }

    /** Same filters as a JSON body, for the POST endpoints. */
    function toPayload(filters) {
        const payload = {};
        Object.entries(MULTI_FILTERS).forEach(([id, param]) => {
            const values = filters[id] || [];
            if (values.length) payload[param] = values;
        });
        Object.entries(NUMBER_FILTERS).forEach(([id, param]) => {
            const value = filters[id];
            if (value !== null && value !== undefined && value !== '') {
                payload[param] = value;
            }
        });
        if (filters.recentPermitDays) {
            payload.recent_permit_days = filters.recentPermitDays;
            payload.permit_activity_mode = filters.permitActivityMode === 'inactive'
                ? 'inactive'
                : 'within';
        }
        return payload;
    }

    /**
     * Wire every shared control to `onChange`. The page supplies the callback
     * so it can reset paging and refetch in whatever way it needs.
     */
    function bind(onChange) {
        Object.keys(MULTI_FILTERS).forEach(id => {
            const node = el(id);
            if (node) node.addEventListener('change', () => onChange());
        });

        let debounce;
        Object.keys(NUMBER_FILTERS).forEach(id => {
            const node = el(id);
            if (!node) return;
            node.addEventListener('change', () => onChange());
            node.addEventListener('input', () => {
                clearTimeout(debounce);
                debounce = setTimeout(() => onChange(), 600);
            });
        });

        const recent = el('recentPermitDays');
        const custom = el('recentPermitCustomDays');
        const mode = el('permitActivityMode');
        if (mode) {
            mode.addEventListener('change', () => {
                updateActivityHint();
                onChange();
            });
        }
        if (recent) {
            recent.addEventListener('change', e => {
                if (!custom) return onChange();
                if (e.target.value === 'custom') {
                    // Wait for a number before refetching.
                    custom.style.display = 'block';
                    custom.focus();
                    updateActivityHint();
                    return;
                }
                custom.style.display = 'none';
                custom.value = '';
                updateActivityHint();
                onChange();
            });
        }
        if (custom) custom.addEventListener('change', () => {
            updateActivityHint();
            onChange();
        });
        updateActivityHint();
    }

    function updateActivityHint() {
        const hint = el('permitActivityHint');
        const mode = el('permitActivityMode');
        if (!hint || !mode) return;
        const recent = el('recentPermitDays');
        const custom = el('recentPermitCustomDays');
        const hasWindow = recent?.value &&
            (recent.value !== 'custom' || Boolean(custom?.value));
        if (!hasWindow) {
            hint.textContent = 'Choose a time window to apply this timing rule.';
            return;
        }
        const context = hint.dataset.context || 'properties';
        if (mode.value === 'inactive') {
            hint.textContent = context === 'contractors'
                ? 'Counts permit records dated before the selected cutoff.'
                : 'Excludes buildings with any recent permit. Buildings with no history remain eligible unless “Only properties with permits” is on.';
        } else {
            hint.textContent = context === 'contractors'
                ? 'Counts permit records filed or issued inside the selected window.'
                : 'Requires at least one permit filed or issued inside the selected window.';
        }
    }

    /** Reset every shared control without firing a fetch per control. */
    function clear() {
        const sidebar = document.querySelector('.sidebar');
        MultiSelect.clearAll(sidebar);
        Object.keys(NUMBER_FILTERS).forEach(id => {
            const node = el(id);
            if (node) node.value = '';
        });
        const custom = el('recentPermitCustomDays');
        if (custom) {
            custom.value = '';
            custom.style.display = 'none';
        }
        const mode = el('permitActivityMode');
        if (mode) mode.value = 'within';
        updateActivityHint();
    }

    /**
     * Fill the work-classification selects from the API. Options arrive after
     * MultiSelect has already enhanced the selects, so each one is refreshed.
     */
    async function loadFacets() {
        const targets = FACET_SELECTS.map(el).filter(Boolean);
        if (!targets.length) return;

        let facets;
        try {
            const res = await fetch('/api/permits/facets');
            const data = await res.json();
            if (!data.success) return;
            facets = data.facets || {};
        } catch (err) {
            // A filter that cannot offer options just stays empty; the rest of
            // the page is unaffected.
            console.error('Could not load permit facets:', err);
            return;
        }

        targets.forEach(select => {
            const options = facets[select.dataset.facet] || [];
            if (!options.length) {
                select.closest('.filter-section')
                    ?.querySelector(`[for="${select.id}"]`)
                    ?.style.setProperty('display', 'none');
                return;
            }
            select.innerHTML = options.map(option =>
                `<option value="${option.value}">${option.label}` +
                `${option.count ? ` (${option.count.toLocaleString()})` : ''}</option>`
            ).join('');
            MultiSelect.refresh(select.id);
        });
    }

    global.SharedFilters = {
        MULTI_FILTERS,
        NUMBER_FILTERS,
        read,
        fromParams,
        apply,
        toParams,
        toPayload,
        bind,
        clear,
        loadFacets,
    };
})(window);
