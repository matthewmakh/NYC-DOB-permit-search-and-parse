// Universal search explorer -------------------------------------------------
// One URL-backed state object drives all four grains, so a user can group or
// paginate without losing the filters that define the lead list.

(function () {
    'use strict';

    const config = JSON.parse(document.getElementById('searchExplorerConfig').textContent);
    const GROUPS = new Set(['property', 'owner', 'job_type', 'permit']);
    const MULTI_CONTROLS = {
        matchField: 'match_field',
        boroughFilter: 'borough',
        propertyType: 'property_type',
        buildingClass: 'building_class',
        jobType: 'job_type',
        workType: 'work_type',
        permitType: 'permit_type',
        licenseType: 'license_type',
        permitStatus: 'permit_status',
        violationsFilter: 'has_violations',
    };
    const NUMBER_CONTROLS = {
        minUnits: 'min_units',
        maxUnits: 'max_units',
        minSqft: 'min_sqft',
        maxSqft: 'max_sqft',
        minValue: 'min_value',
        maxValue: 'max_value',
        minMatchingPermits: 'min_matching_permits',
    };
    const FACET_CONTROLS = ['jobType', 'workType', 'permitType', 'licenseType', 'permitStatus'];
    const SORTS = {
        property: [
            ['latest', 'Latest permit activity'],
            ['matching_permits', 'Matching permits'],
            ['open_permits', 'Current / open permits'],
            ['units', 'Units'],
            ['value', 'Assessed value'],
            ['address', 'Address'],
            ['owner', 'Owner'],
        ],
        owner: [
            ['properties', 'Properties'],
            ['matching_permits', 'Matching permits'],
            ['open_permits', 'Current / open permits'],
            ['units', 'Units'],
            ['latest', 'Latest activity'],
            ['owner', 'Owner name'],
        ],
        job_type: [
            ['permits', 'Permit count'],
            ['properties', 'Properties'],
            ['open_permits', 'Current / open permits'],
            ['latest', 'Latest activity'],
            ['job_type', 'Job type'],
        ],
        permit: [
            ['latest', 'Latest activity'],
            ['expiry', 'Expiration date'],
            ['units', 'Property units'],
            ['address', 'Address'],
            ['job_type', 'Job type'],
        ],
    };

    let state = readUrlState();
    let requestController = null;
    let filterTimer = null;

    function el(id) { return document.getElementById(id); }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function safePath(value) { return encodeURIComponent(String(value == null ? '' : value)); }

    function parsePositive(value, fallback) {
        const number = parseInt(value, 10);
        return Number.isFinite(number) && number > 0 ? number : fallback;
    }

    function readUrlState() {
        const params = new URLSearchParams(window.location.search);
        const group = params.get('group_by');
        const next = {
            q: (params.get('q') || config.query || '').trim(),
            group_by: GROUPS.has(group) ? group : 'property',
            page: parsePositive(params.get('page'), 1),
            per_page: Math.min(100, parsePositive(params.get('per_page'), 25)),
            sort_by: params.get('sort_by') || '',
            sort_order: params.get('sort_order') === 'asc' ? 'asc' : 'desc',
            current_only: params.get('current_only') === 'true',
            recent_permit_days: params.get('recent_permit_days') || '',
            permit_activity_mode: params.get('permit_activity_mode') === 'inactive'
                ? 'inactive'
                : 'within',
        };
        Object.values(MULTI_CONTROLS).forEach(param => { next[param] = params.getAll(param); });
        Object.values(NUMBER_CONTROLS).forEach(param => { next[param] = params.get(param) || ''; });
        return next;
    }

    function toParams(includePaging) {
        const params = new URLSearchParams();
        params.set('q', state.q);
        params.set('group_by', state.group_by);
        Object.values(MULTI_CONTROLS).forEach(param => {
            (state[param] || []).forEach(value => params.append(param, value));
        });
        Object.values(NUMBER_CONTROLS).forEach(param => {
            if (state[param] !== '') params.set(param, state[param]);
        });
        if (state.current_only) params.set('current_only', 'true');
        if (state.recent_permit_days) {
            params.set('recent_permit_days', state.recent_permit_days);
            params.set('permit_activity_mode', state.permit_activity_mode);
        }
        if (state.sort_by) params.set('sort_by', state.sort_by);
        params.set('sort_order', state.sort_order);
        if (includePaging !== false) {
            params.set('page', state.page);
            params.set('per_page', state.per_page);
        }
        return params;
    }

    function writeUrl(push) {
        const url = `${window.location.pathname}?${toParams(true).toString()}`;
        window.history[push ? 'pushState' : 'replaceState']({}, '', url);
    }

    function selectedLabel(id, value) {
        const option = Array.from(el(id)?.options || []).find(item => item.value === String(value));
        return option ? option.textContent.replace(/\s+\([\d,]+\)$/, '').trim() : String(value);
    }

    function syncControls() {
        el('searchInput').value = state.q;
        Object.entries(MULTI_CONTROLS).forEach(([id, param]) => {
            MultiSelect.set(id, state[param] || [], { silent: true });
        });
        Object.entries(NUMBER_CONTROLS).forEach(([id, param]) => {
            if (el(id)) el(id).value = state[param] || '';
        });
        const dayValues = Array.from(el('recentPermitDays').options).map(option => option.value);
        const preset = state.recent_permit_days && dayValues.includes(state.recent_permit_days)
            ? state.recent_permit_days
            : (state.recent_permit_days ? 'custom' : '');
        MultiSelect.set('recentPermitDays', [preset], { silent: true });
        el('recentPermitCustomDays').style.display = preset === 'custom' ? 'block' : 'none';
        el('recentPermitCustomDays').value = preset === 'custom' ? state.recent_permit_days : '';
        el('permitActivityMode').value = state.permit_activity_mode;
        updatePermitActivityHint();
        el('currentOnly').checked = state.current_only;
        el('sortOrder').value = state.sort_order;
        el('perPage').value = String(state.per_page);
        setGroupTabs();
        setSortOptions();
        renderFilterChips();
    }

    function setGroupTabs() {
        el('groupTabs').querySelectorAll('[data-group]').forEach(button => {
            button.setAttribute('aria-selected', String(button.dataset.group === state.group_by));
            button.tabIndex = button.dataset.group === state.group_by ? 0 : -1;
        });
    }

    function setSortOptions() {
        const select = el('sortBy');
        const choices = SORTS[state.group_by];
        if (!choices.some(([value]) => value === state.sort_by)) state.sort_by = choices[0][0];
        select.innerHTML = choices.map(([value, label]) =>
            `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`
        ).join('');
        select.value = state.sort_by;
    }

    function pullControlsIntoState() {
        Object.entries(MULTI_CONTROLS).forEach(([id, param]) => {
            state[param] = MultiSelect.values(id);
        });
        Object.entries(NUMBER_CONTROLS).forEach(([id, param]) => {
            state[param] = el(id)?.value || '';
        });
        state.current_only = el('currentOnly').checked;
        state.permit_activity_mode = el('permitActivityMode').value === 'inactive'
            ? 'inactive'
            : 'within';
        state.recent_permit_days = el('recentPermitDays').value === 'custom'
            ? (el('recentPermitCustomDays').value || '')
            : (el('recentPermitDays').value || '');
    }

    function filterChanged(immediate) {
        pullControlsIntoState();
        updatePermitActivityHint();
        state.page = 1;
        renderFilterChips();
        clearTimeout(filterTimer);
        if (immediate) return loadResults();
        filterTimer = setTimeout(loadResults, 450);
    }

    function updatePermitActivityHint() {
        const inactive = el('permitActivityMode').value === 'inactive';
        if (!state.recent_permit_days) {
            el('permitActivityHint').textContent = 'Choose a time window to apply this timing rule.';
            return;
        }
        el('permitActivityHint').textContent = inactive
            ? 'Excludes a property if any permit was filed or issued inside the window. Buildings with no permit history are included.'
            : 'Requires at least one matching permit filed or issued inside the selected window.';
    }

    function filterCount() {
        let count = Number(state.current_only) + Number(Boolean(state.recent_permit_days));
        Object.values(MULTI_CONTROLS).forEach(param => { count += (state[param] || []).length; });
        Object.values(NUMBER_CONTROLS).forEach(param => { if (state[param] !== '') count += 1; });
        return count;
    }

    function renderFilterChips() {
        const chips = [];
        const labels = {
            matchField: 'Match', boroughFilter: 'Borough', propertyType: 'Type',
            buildingClass: 'Class', jobType: 'Job', workType: 'Work',
            permitType: 'Permit', licenseType: 'License', permitStatus: 'Status',
            violationsFilter: 'HPD',
        };
        Object.entries(MULTI_CONTROLS).forEach(([id, param]) => {
            (state[param] || []).forEach(value => chips.push({
                param, value, text: `${labels[id]}: ${selectedLabel(id, value)}`,
            }));
        });
        const numberLabels = {
            min_units: 'Units ≥', max_units: 'Units ≤', min_sqft: 'Sq ft ≥', max_sqft: 'Sq ft ≤',
            min_value: 'Value ≥ $', max_value: 'Value ≤ $', min_matching_permits: 'Permits ≥',
        };
        Object.values(NUMBER_CONTROLS).forEach(param => {
            if (state[param] !== '') chips.push({ param, value: '', text: `${numberLabels[param]} ${Number(state[param]).toLocaleString()}` });
        });
        if (state.current_only) chips.push({ param: 'current_only', value: '', text: 'Current / open only' });
        if (state.recent_permit_days) chips.push({
            param: 'recent_permit_days', value: '',
            text: state.permit_activity_mode === 'inactive'
                ? `No permits in ${Number(state.recent_permit_days).toLocaleString()} days`
                : `Permits within ${Number(state.recent_permit_days).toLocaleString()} days`,
        });

        el('activeFilters').innerHTML = chips.map(chip =>
            `<span class="sr-filter-chip">${escapeHtml(chip.text)}` +
            `<button type="button" data-remove-param="${escapeHtml(chip.param)}" ` +
            `data-remove-value="${escapeHtml(chip.value)}" aria-label="Remove ${escapeHtml(chip.text)}">×</button></span>`
        ).join('');
        const count = filterCount();
        el('filterSummary').textContent = count ? `${count} active` : 'All results';
        el('mobileFilterCount').textContent = count;
        el('mobileFilterCount').hidden = count === 0;
    }

    function removeFilter(param, value) {
        if (Array.isArray(state[param])) {
            state[param] = state[param].filter(item => item !== value);
        } else if (param === 'current_only') {
            state.current_only = false;
        } else {
            state[param] = '';
            if (param === 'recent_permit_days') state.permit_activity_mode = 'within';
        }
        state.page = 1;
        syncControls();
        loadResults();
    }

    async function loadFacets() {
        try {
            const response = await fetch('/api/permits/facets');
            const data = await response.json();
            if (!response.ok || !data.success) return;
            FACET_CONTROLS.forEach(id => {
                const select = el(id);
                const options = data.facets?.[select.dataset.facet] || [];
                select.innerHTML = options.map(option =>
                    `<option value="${escapeHtml(option.value)}">${escapeHtml(option.label)}` +
                    `${option.count ? ` (${Number(option.count).toLocaleString()})` : ''}</option>`
                ).join('');
                MultiSelect.refresh(id);
                MultiSelect.set(id, state[MULTI_CONTROLS[id]] || [], { silent: true });
            });
            renderFilterChips();
        } catch (error) {
            console.error('Could not load permit filters:', error);
        }
    }

    function setLoading() {
        el('resultsContainer').setAttribute('aria-busy', 'true');
        el('resultsContainer').innerHTML =
            '<div class="sr-loading"><span class="sr-spinner" aria-hidden="true"></span>' +
            '<p>Applying search and filters…</p></div>';
    }

    async function loadResults() {
        clearTimeout(filterTimer);
        writeUrl(false);
        setLoading();
        if (requestController) requestController.abort();
        requestController = new AbortController();

        try {
            const response = await fetch(`/api/search/explore?${toParams(true).toString()}`, {
                signal: requestController.signal,
            });
            const data = await response.json();
            if (!response.ok || !data.success) throw new Error(data.error || 'Search failed');
            state.sort_by = data.sort.by;
            state.sort_order = data.sort.order;
            state.page = data.pagination.page;
            setSortOptions();
            renderSummary(data.summary || {});
            renderResults(data.results || [], data.pagination);
            writeUrl(false);
        } catch (error) {
            if (error.name === 'AbortError') return;
            renderError(error.message);
        } finally {
            el('resultsContainer').setAttribute('aria-busy', 'false');
        }
    }

    function renderSummary(summary) {
        const values = {
            statProperties: summary.properties,
            statPermits: summary.permits,
            statOpen: summary.current_open_permits,
            statUnits: summary.total_units,
            tabPropertyCount: summary.properties,
            tabOwnerCount: summary.owners,
            tabJobCount: summary.job_types,
            tabPermitCount: summary.permits,
        };
        Object.entries(values).forEach(([id, value]) => {
            el(id).textContent = formatNumber(value);
        });
    }

    function renderResults(results, pagination) {
        const total = Number(pagination.total_count || 0);
        const groupLabels = { property: 'properties', owner: 'owners', job_type: 'job types', permit: 'permits' };
        const start = total ? (pagination.page - 1) * pagination.per_page + 1 : 0;
        const end = Math.min(total, pagination.page * pagination.per_page);
        const copy = total
            ? `${formatNumber(start)}–${formatNumber(end)} of ${formatNumber(total)} ${groupLabels[state.group_by]}`
            : `0 ${groupLabels[state.group_by]}`;
        el('resultsCount').textContent = copy;
        el('mobileResultCount').textContent = formatNumber(total) + ' results';

        if (!results.length) {
            el('resultsContainer').innerHTML =
                '<div class="sr-empty"><i class="fas fa-filter-circle-xmark" aria-hidden="true"></i>' +
                '<strong>No results match this combination</strong>' +
                '<p>Remove one or two filters, or broaden where the search term is allowed to match.</p>' +
                '<button type="button" class="btn btn-secondary" data-clear-results>Clear filters</button></div>';
            el('pagination').innerHTML = '';
            return;
        }

        const renderer = {
            property: renderProperty,
            owner: renderOwner,
            job_type: renderJobType,
            permit: renderPermit,
        }[state.group_by];
        el('resultsContainer').innerHTML = results.map(renderer).join('');
        renderPagination(pagination);
    }

    function renderProperty(row) {
        const address = row.address || 'Address unknown';
        const current = Number(row.current_open_permit_count || 0);
        const tags = []
            .concat((row.match_reasons || []).slice(0, 2).map(value => tag(value, 'accent')))
            .concat((row.job_types || []).slice(0, 3).map(value => tag(value)))
            .concat((row.work_types || []).slice(0, 2).map(value => tag(value)));
        return `<article class="sr-result-row">
            <div class="sr-result-primary">
                <a class="sr-result-title" href="/property/${safePath(row.bbl)}">${escapeHtml(address)}</a>
                <div class="sr-result-sub">BBL ${escapeHtml(formatBBL(row.bbl))} · ${escapeHtml(row.building_class || 'Class unknown')}</div>
                <div class="sr-result-owner">Owner: <strong>${escapeHtml(row.owner_display || 'Owner unknown')}</strong></div>
            </div>
            <div class="sr-result-context">
                <span class="sr-kicker">Why it matched</span>
                <div class="sr-chip-list">${tags.join('') || tag('Related property')}</div>
            </div>
            <div class="sr-result-metrics">
                <div class="sr-metric-grid">
                    ${metric('Matching', row.matching_permit_count)}
                    ${metric('Current / open', current, current ? 'open' : '')}
                    ${metric('All permits', row.total_permit_count)}
                    ${metric('Units', row.total_units)}
                    ${metric('Assessed', formatMoney(row.assessed_total_value))}
                    ${metric('Latest', formatDate(row.latest_activity))}
                </div>
            </div>
            <a class="sr-row-arrow" href="/property/${safePath(row.bbl)}" aria-label="Open ${escapeHtml(address)}"><i class="fas fa-chevron-right" aria-hidden="true"></i></a>
        </article>`;
    }

    function renderOwner(row) {
        const owner = row.owner_display || 'Owner unknown';
        const href = `/properties?owner=${encodeURIComponent(owner)}`;
        const addresses = (row.sample_addresses || []).filter(Boolean);
        return `<article class="sr-result-row sr-owner-row">
            <div class="sr-result-primary">
                <span class="sr-kicker">Property owner</span>
                <a class="sr-result-title" href="${escapeHtml(href)}">${escapeHtml(owner)}</a>
                <div class="sr-result-owner">Portfolio value: <strong>${escapeHtml(formatMoney(row.assessed_value))}</strong></div>
            </div>
            <div class="sr-result-context">
                <span class="sr-kicker">Sample properties</span>
                <div class="sr-address-list">${addresses.length ? addresses.map(escapeHtml).join('<span aria-hidden="true"> · </span>') : 'No address available'}</div>
            </div>
            <div class="sr-result-metrics">
                <div class="sr-metric-grid">
                    ${metric('Properties', row.property_count)}
                    ${metric('Matching', row.matching_permit_count)}
                    ${metric('Current / open', row.current_open_permit_count, Number(row.current_open_permit_count) ? 'open' : '')}
                    ${metric('Units', row.total_units)}
                    ${metric('Latest', formatDate(row.latest_activity))}
                </div>
            </div>
            <a class="sr-row-arrow" href="${escapeHtml(href)}" aria-label="Open ${escapeHtml(owner)} portfolio"><i class="fas fa-chevron-right" aria-hidden="true"></i></a>
        </article>`;
    }

    function renderJobType(row) {
        const jobType = row.job_type || 'Unknown';
        const href = explorerHref({ group_by: 'property', job_type: [jobType === 'Unknown' ? '' : jobType], page: 1 });
        return `<article class="sr-result-row sr-job-row">
            <div class="sr-result-primary">
                <span class="sr-kicker">DOB job type</span>
                <a class="sr-result-title" href="${escapeHtml(href)}">${escapeHtml(jobType)}</a>
                <div class="sr-result-owner">Latest activity: <strong>${escapeHtml(formatDate(row.latest_activity))}</strong></div>
            </div>
            <div class="sr-result-context">
                <span class="sr-kicker">Work represented</span>
                <div class="sr-chip-list">${(row.work_types || []).slice(0, 8).map(value => tag(value)).join('') || tag('Not classified')}</div>
            </div>
            <div class="sr-result-metrics">
                <div class="sr-metric-grid">
                    ${metric('Permits', row.permit_count)}
                    ${metric('Properties', row.property_count)}
                    ${metric('Current / open', row.current_open_permit_count, Number(row.current_open_permit_count) ? 'open' : '')}
                </div>
            </div>
            <a class="sr-row-arrow" href="${escapeHtml(href)}" aria-label="Show properties with job type ${escapeHtml(jobType)}"><i class="fas fa-chevron-right" aria-hidden="true"></i></a>
        </article>`;
    }

    function renderPermit(row) {
        const permit = row.permit_no || row.job_number || `Permit ${row.id}`;
        const status = row.permit_status || row.status || row.filing_status || 'Status unknown';
        const address = row.property_address || 'Address unknown';
        return `<article class="sr-result-row sr-permit-row">
            <div class="sr-result-primary">
                <a class="sr-result-title" href="/permit/${safePath(row.id)}">${escapeHtml(permit)}</a>
                <div class="sr-result-sub">${escapeHtml(row.job_type || 'Job type unknown')} · ${escapeHtml(row.work_type || 'Work type unknown')}</div>
                <div class="sr-chip-list" style="margin-top:7px">${tag(status, row.is_current ? 'open' : '')}${tag(row.match_role || 'Related permit', 'accent')}</div>
            </div>
            <div class="sr-result-context">
                <span class="sr-kicker">Property</span>
                <a class="sr-result-title" href="/property/${safePath(row.bbl)}">${escapeHtml(address)}</a>
                <div class="sr-result-owner">Owner: <strong>${escapeHtml(row.owner_display || 'Owner unknown')}</strong></div>
            </div>
            <div class="sr-result-metrics">
                <span class="sr-kicker">Work and timing</span>
                <div class="sr-description">${escapeHtml(row.work_description || 'No work description')}</div>
                <div class="sr-chip-list" style="margin-top:6px">${tag(`Filed ${formatDate(row.filing_date)}`)}${tag(`Expires ${formatDate(row.exp_date)}`)}</div>
            </div>
            <a class="sr-row-arrow" href="/permit/${safePath(row.id)}" aria-label="Open permit ${escapeHtml(permit)}"><i class="fas fa-chevron-right" aria-hidden="true"></i></a>
        </article>`;
    }

    function metric(label, value, tone) {
        const className = tone === 'open' ? ' class="sr-tag-open"' : '';
        return `<div class="sr-metric"><span>${escapeHtml(label)}</span><strong${className}>${escapeHtml(formatMetric(value))}</strong></div>`;
    }

    function tag(value, tone) {
        const modifier = tone ? ` sr-tag-${tone}` : '';
        return `<span class="sr-tag${modifier}">${escapeHtml(value)}</span>`;
    }

    function formatMetric(value) {
        if (value === null || value === undefined || value === '') return '—';
        if (typeof value === 'number' || /^-?[\d.]+$/.test(String(value))) return formatNumber(value);
        return String(value);
    }

    function formatNumber(value) {
        const number = Number(value || 0);
        return Number.isFinite(number) ? Math.round(number).toLocaleString() : '0';
    }

    function formatMoney(value) {
        const number = Number(value || 0);
        if (!Number.isFinite(number) || !number) return '—';
        return new Intl.NumberFormat('en-US', {
            style: 'currency', currency: 'USD', maximumFractionDigits: 0,
            notation: number >= 1000000 ? 'compact' : 'standard',
        }).format(number);
    }

    function formatDate(value) {
        if (!value) return '—';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return String(value);
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
    }

    function formatBBL(value) {
        const bbl = String(value || '').replace(/\D/g, '');
        return bbl.length === 10 ? `${bbl[0]}-${bbl.slice(1, 6)}-${bbl.slice(6)}` : String(value || '');
    }

    function explorerHref(overrides) {
        const snapshot = state;
        state = { ...state, ...overrides };
        const href = `${window.location.pathname}?${toParams(true).toString()}`;
        state = snapshot;
        return href;
    }

    function renderPagination(pagination) {
        if (pagination.total_pages <= 1) {
            el('pagination').innerHTML = '';
            return;
        }
        el('pagination').innerHTML = `
            <button type="button" class="btn-pagination" data-page="${pagination.page - 1}" ${pagination.has_prev ? '' : 'disabled'} aria-label="Previous page">
                <i class="fas fa-chevron-left" aria-hidden="true"></i> Previous
            </button>
            <span class="sr-page-label">Page ${formatNumber(pagination.page)} of ${formatNumber(pagination.total_pages)}</span>
            <button type="button" class="btn-pagination" data-page="${pagination.page + 1}" ${pagination.has_next ? '' : 'disabled'} aria-label="Next page">
                Next <i class="fas fa-chevron-right" aria-hidden="true"></i>
            </button>`;
    }

    function renderError(message) {
        el('resultsCount').textContent = 'Search unavailable';
        el('mobileResultCount').textContent = 'Search unavailable';
        el('resultsContainer').innerHTML =
            `<div class="sr-error"><i class="fas fa-circle-exclamation" aria-hidden="true"></i>` +
            `<strong>We couldn’t load this result set</strong><p>${escapeHtml(message)}</p>` +
            '<button type="button" class="btn btn-primary" data-retry-results>Retry</button></div>';
        el('pagination').innerHTML = '';
    }

    function clearFilters() {
        Object.values(MULTI_CONTROLS).forEach(param => { state[param] = []; });
        Object.values(NUMBER_CONTROLS).forEach(param => { state[param] = ''; });
        state.current_only = false;
        state.recent_permit_days = '';
        state.permit_activity_mode = 'within';
        state.page = 1;
        syncControls();
        loadResults();
    }

    function openFilters() {
        document.body.classList.add('sr-filters-open');
        el('filterBackdrop').hidden = false;
        el('openFiltersBtn').setAttribute('aria-expanded', 'true');
        el('closeFiltersBtn').focus();
    }

    function closeFilters() {
        document.body.classList.remove('sr-filters-open');
        el('filterBackdrop').hidden = true;
        el('openFiltersBtn').setAttribute('aria-expanded', 'false');
    }

    function bindEvents() {
        el('searchForm').addEventListener('submit', event => {
            event.preventDefault();
            const query = el('searchInput').value.trim();
            if (query.length < 2) {
                el('searchInput').focus();
                return;
            }
            state.q = query;
            state.page = 1;
            writeUrl(true);
            loadResults();
        });

        Object.keys(MULTI_CONTROLS).forEach(id => el(id).addEventListener('change', () => filterChanged(true)));
        el('permitActivityMode').addEventListener('change', () => {
            updatePermitActivityHint();
            filterChanged(true);
        });
        el('recentPermitDays').addEventListener('change', event => {
            const custom = el('recentPermitCustomDays');
            if (event.target.value === 'custom') {
                custom.style.display = 'block';
                custom.focus();
                pullControlsIntoState();
                updatePermitActivityHint();
                return;
            }
            custom.style.display = 'none';
            custom.value = '';
            filterChanged(true);
        });
        el('recentPermitCustomDays').addEventListener('input', () => filterChanged(false));
        el('recentPermitCustomDays').addEventListener('change', () => filterChanged(true));
        el('currentOnly').addEventListener('change', () => filterChanged(true));
        Object.keys(NUMBER_CONTROLS).forEach(id => {
            el(id).addEventListener('input', () => filterChanged(false));
            el(id).addEventListener('change', () => filterChanged(true));
        });

        el('groupTabs').addEventListener('click', event => {
            const button = event.target.closest('[data-group]');
            if (!button || button.dataset.group === state.group_by) return;
            state.group_by = button.dataset.group;
            state.page = 1;
            state.sort_by = '';
            setGroupTabs();
            setSortOptions();
            writeUrl(true);
            loadResults();
        });
        el('groupTabs').addEventListener('keydown', event => {
            if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
            event.preventDefault();
            const tabs = Array.from(el('groupTabs').querySelectorAll('[data-group]'));
            const index = tabs.indexOf(document.activeElement);
            const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
            tabs[next].focus();
            tabs[next].click();
        });

        el('sortBy').addEventListener('change', event => {
            state.sort_by = event.target.value;
            state.page = 1;
            loadResults();
        });
        el('sortOrder').addEventListener('change', event => {
            state.sort_order = event.target.value;
            state.page = 1;
            loadResults();
        });
        el('perPage').addEventListener('change', event => {
            state.per_page = parsePositive(event.target.value, 25);
            state.page = 1;
            loadResults();
        });

        el('activeFilters').addEventListener('click', event => {
            const button = event.target.closest('[data-remove-param]');
            if (button) removeFilter(button.dataset.removeParam, button.dataset.removeValue);
        });
        el('pagination').addEventListener('click', event => {
            const button = event.target.closest('[data-page]');
            if (!button || button.disabled) return;
            state.page = Number(button.dataset.page);
            loadResults();
            window.scrollTo({ top: el('groupTabs').getBoundingClientRect().top + window.scrollY - 80, behavior: 'smooth' });
        });
        el('resultsContainer').addEventListener('click', event => {
            if (event.target.closest('[data-clear-results]')) clearFilters();
            if (event.target.closest('[data-retry-results]')) loadResults();
        });
        el('clearFiltersBtn').addEventListener('click', clearFilters);
        el('openFiltersBtn').addEventListener('click', openFilters);
        el('closeFiltersBtn').addEventListener('click', closeFilters);
        el('filterBackdrop').addEventListener('click', closeFilters);
        el('applyFiltersBtn').addEventListener('click', closeFilters);
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && document.body.classList.contains('sr-filters-open')) closeFilters();
        });
        window.addEventListener('popstate', () => {
            state = readUrlState();
            syncControls();
            loadResults();
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        MultiSelect.init(document);
        syncControls();
        bindEvents();
        loadFacets();
        loadResults();
    });
})();
