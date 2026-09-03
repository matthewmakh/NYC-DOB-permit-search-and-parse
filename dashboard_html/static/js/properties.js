// ==========================================
// Properties Page JavaScript
// ==========================================

// The API answers in snake_case and does not echo the page size the way the
// renderer wants it. Without this, totalPages/hasNext/hasPrev come back
// undefined — which left every pagination button disabled with no page
// numbers — and perPage was dropped, so the next request silently fell back
// to the server default.
function normalizePagination(raw) {
    const p = raw || {};
    return {
        page: p.page || 1,
        perPage: p.per_page || state.pagination.perPage,
        totalCount: p.total_count || 0,
        total_count: p.total_count || 0,
        totalPages: p.total_pages || 0,
        hasNext: p.has_next === true,
        hasPrev: p.has_prev === true,
    };
}

// Multi-select filters go on the wire as repeated params
// (?permit_type=PL&permit_type=EW); the API ORs them together.
function appendMulti(params, name, values) {
    (values || []).forEach(value => params.append(name, value));
}

// State Management
const state = {
    properties: [],
    allStats: {},
    filters: {
        search: '',
        owner: '',
        minSalePrice: null,
        maxSalePrice: null,
        saleDateFrom: null,
        saleDateTo: null,
        cashOnly: false,
        withPermits: false,
        minPermits: null,
        recentSaleDays: null,
        financingMin: null,
        financingMax: null,
        smartFilter: null,
        ownerKinds: [],      // sidebar "Owner type": person / llc / lender ... (OR'd)
        play: null
    },
    // Borough, property type, building class, units, value, kind of work,
    // recency and violations live here — the same set, read the same way, as
    // on the contractors page. See static/js/filters_common.js.
    shared: {},
    plays: [],
    playFamily: 'property_intel',
    playsHealth: null,
    sort: {
        by: [],          // Sort keys in pick order; empty means the API default
        order: 'desc'
    },
    pagination: {
        page: 1,
        perPage: 50,
        totalCount: 0,
        totalPages: 0
    }
};

const PROPERTY_LIST_SNAPSHOT_KEY = 'properties:list-navigation:v1';
const ALLOWED_SORT_KEYS = new Set([
    'sale_date', 'value', 'sale_price', 'address', 'owner', 'permits', 'units',
    'unused_far', 'co_date', 'recent_permits'
]);
let initialPlaysSettled = false;
let initialPropertiesSettled = false;

if ('scrollRestoration' in window.history) {
    window.history.scrollRestoration = 'manual';
}

function repeatedParamValues(params, name) {
    const values = [];
    params.getAll(name).forEach(raw => {
        String(raw).split(',').forEach(value => {
            const clean = value.trim();
            if (clean && !values.includes(clean)) values.push(clean);
        });
    });
    return values;
}

function finiteParam(params, name) {
    const raw = params.get(name);
    if (raw === null || raw === '') return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
}

function positiveIntParam(params, name, fallback) {
    const value = Number(params.get(name));
    return Number.isInteger(value) && value > 0 ? value : fallback;
}

function trueParam(params, name) {
    return ['1', 'true', 'yes'].includes(
        String(params.get(name) || '').toLowerCase());
}

function restoreStateFromUrl(params) {
    state.filters.search = params.get('search') || params.get('q') || '';
    state.filters.owner = params.get('owner') || '';
    state.filters.minSalePrice = finiteParam(params, 'min_sale_price');
    state.filters.maxSalePrice = finiteParam(params, 'max_sale_price');
    state.filters.saleDateFrom = params.get('sale_date_from') || null;
    state.filters.saleDateTo = params.get('sale_date_to') || null;
    state.filters.cashOnly = trueParam(params, 'cash_only');
    state.filters.withPermits = trueParam(params, 'with_permits');
    state.filters.minPermits = finiteParam(params, 'min_permits');
    state.filters.recentSaleDays = finiteParam(params, 'recent_sale_days');
    // Financing values stay as the percentage displayed in the controls.
    // The server converts them to the stored 0-1 ratio exactly once.
    state.filters.financingMin = finiteParam(params, 'financing_min');
    state.filters.financingMax = finiteParam(params, 'financing_max');
    state.filters.ownerKinds = repeatedParamValues(params, 'owner_kind')
        .filter(kind => OWNER_KIND_SLUGS.has(kind));
    // The retired "Has enrichable owner" checkbox meant people; honour old links.
    if (trueParam(params, 'has_enrichable_owner') && !state.filters.ownerKinds.includes('person')) {
        state.filters.ownerKinds.push('person');
    }
    state.filters.play = params.get('play') || null;
    state.shared = SharedFilters.fromParams(params);

    state.sort.by = repeatedParamValues(params, 'sort_by')
        .filter(key => ALLOWED_SORT_KEYS.has(key));
    state.sort.order = params.get('sort_order') === 'asc' ? 'asc' : 'desc';
    state.pagination.page = positiveIntParam(params, 'page', 1);
    const perPage = positiveIntParam(params, 'per_page', 50);
    state.pagination.perPage = [25, 50, 100, 200].includes(perPage)
        ? perPage
        : 50;
}

function applyStateToControls() {
    const setValue = (id, value) => {
        const node = document.getElementById(id);
        if (node) node.value = value === null || value === undefined ? '' : value;
    };
    setValue('universalSearch', state.filters.search);
    setValue('ownerSearch', state.filters.owner);
    setValue('minSalePrice', state.filters.minSalePrice);
    setValue('maxSalePrice', state.filters.maxSalePrice);
    setValue('saleDateFrom', state.filters.saleDateFrom);
    setValue('saleDateTo', state.filters.saleDateTo);
    setValue('minPermits', state.filters.minPermits);
    setValue('financingMin', state.filters.financingMin);
    setValue('financingMax', state.filters.financingMax);

    document.getElementById('cashOnly').checked = state.filters.cashOnly;
    document.getElementById('withPermits').checked = state.filters.withPermits;
    MultiSelect.set('ownerKind', state.filters.ownerKinds, { silent: true });

    state.sort.by.forEach(key => {
        const sortSelect = document.getElementById('sortBy');
        if (Array.from(sortSelect.options).some(option => option.value === key)) return;
        if (!EXTRA_SORT_LABELS[key]) return;
        const option = document.createElement('option');
        option.value = key;
        option.textContent = EXTRA_SORT_LABELS[key];
        sortSelect.appendChild(option);
    });
    MultiSelect.refresh('sortBy');
    MultiSelect.set('sortBy', state.sort.by, { silent: true });
    MultiSelect.set('sortOrder', [state.sort.order], { silent: true });
    MultiSelect.set('perPage', [String(state.pagination.perPage)], { silent: true });
}

function buildPropertiesParams() {
    const params = new URLSearchParams();
    const f = state.filters;
    if (f.search) params.append('search', f.search);
    if (f.owner) params.append('owner', f.owner);
    SharedFilters.toParams(params, state.shared);
    if (f.minSalePrice !== null) params.append('min_sale_price', f.minSalePrice);
    if (f.maxSalePrice !== null) params.append('max_sale_price', f.maxSalePrice);
    if (f.saleDateFrom) params.append('sale_date_from', f.saleDateFrom);
    if (f.saleDateTo) params.append('sale_date_to', f.saleDateTo);
    if (f.cashOnly) params.append('cash_only', 'true');
    if (f.withPermits) params.append('with_permits', 'true');
    if (f.minPermits !== null) params.append('min_permits', f.minPermits);
    if (f.recentSaleDays) params.append('recent_sale_days', f.recentSaleDays);
    if (f.financingMin !== null) params.append('financing_min', f.financingMin);
    if (f.financingMax !== null) params.append('financing_max', f.financingMax);
    appendMulti(params, 'owner_kind', f.ownerKinds);
    if (f.play) params.append('play', f.play);
    appendMulti(params, 'sort_by', state.sort.by);
    params.append('sort_order', state.sort.order);
    params.append('page', state.pagination.page);
    params.append('per_page', state.pagination.perPage);
    return params;
}

function currentPropertiesUrl() {
    return `${window.location.pathname}${window.location.search}`;
}

function syncPropertiesUrl(params) {
    const query = params.toString();
    const next = `${window.location.pathname}${query ? `?${query}` : ''}`;
    window.history.replaceState({ propertiesList: true }, '', next);
}

function saveListNavigationState(bbl) {
    const card = document.querySelector(
        `#propertiesContainer [data-property-bbl="${bbl}"]`);
    const snapshot = {
        url: currentPropertiesUrl(),
        scrollY: window.scrollY,
        cardOffset: card ? card.getBoundingClientRect().top : null,
        bbl: String(bbl),
        savedAt: Date.now(),
    };
    try {
        window.sessionStorage.setItem(
            PROPERTY_LIST_SNAPSHOT_KEY, JSON.stringify(snapshot));
    } catch (_error) {
        // URL state still restores every filter/page if storage is disabled.
    }
}

function restoreListPositionIfReady(force) {
    if (!force && (!initialPlaysSettled || !initialPropertiesSettled)) return;
    let snapshot;
    try {
        snapshot = JSON.parse(
            window.sessionStorage.getItem(PROPERTY_LIST_SNAPSHOT_KEY) || 'null');
    } catch (_error) {
        snapshot = null;
    }
    if (!snapshot || snapshot.url !== currentPropertiesUrl()
            || Date.now() - snapshot.savedAt > 4 * 60 * 60 * 1000) {
        return;
    }
    try {
        window.sessionStorage.removeItem(PROPERTY_LIST_SNAPSHOT_KEY);
    } catch (_error) {
        // Nothing else depends on clearing this best-effort snapshot.
    }
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => {
        let target = Number(snapshot.scrollY) || 0;
        const card = document.querySelector(
            `#propertiesContainer [data-property-bbl="${snapshot.bbl}"]`);
        if (card && Number.isFinite(snapshot.cardOffset)) {
            target = window.scrollY + card.getBoundingClientRect().top
                - snapshot.cardOffset;
        }
        window.scrollTo({ top: Math.max(0, target), behavior: 'auto' });
    }));
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    MultiSelect.init();
    const initialParams = new URLSearchParams(window.location.search);
    restoreStateFromUrl(initialParams);
    SharedFilters.apply(state.shared);
    applyStateToControls();
    SharedFilters.loadFacets().then(() => SharedFilters.apply(state.shared));
    initializeEventListeners();
    loadPlays();
    loadStats();
    loadProperties();
    checkResumableBulkEnrichJob();
});

window.addEventListener('pageshow', event => {
    if (event.persisted) restoreListPositionIfReady(true);
});

// ==========================================
// PREBUILT PLAYS
// ==========================================

async function loadPlays() {
    const status = document.getElementById('playsStatus');
    const health = document.getElementById('playsHealth');
    status.className = 'plays-status';
    status.textContent = 'Loading prebuilt filters…';
    status.style.display = 'block';
    health.textContent = '';
    try {
        const res = await fetch('/api/properties/plays');
        const data = await res.json();
        if (!res.ok || !data.success) {
            throw new Error(data.error || `Request failed (${res.status})`);
        }
        if (!data.plays || !data.plays.length) {
            status.className = 'plays-status plays-status-warning';
            status.textContent = 'No prebuilt filters are available. The signal migrations may not have run yet.';
            return;
        }
        state.plays = data.plays;
        state.playsHealth = data.health || null;
        renderPlayCards();
        renderPlaysHealth();
        status.style.display = 'none';
    } catch (e) {
        console.warn('Plays unavailable:', e);
        status.className = 'plays-status plays-status-error';
        status.textContent = 'Prebuilt filters could not load. Property search is still available; retry by refreshing the page.';
        health.textContent = '';
    } finally {
        initialPlaysSettled = true;
        restoreListPositionIfReady(false);
    }
}

function renderPlayCards() {
    const row = document.getElementById('playsRow');
    const groups = [
        {id: 'property_intel', label: 'Property intelligence', plays: state.plays.filter(p => p.family !== 'smart_installers')},
        {id: 'smart_installers', label: 'Smart Installers sales plays', plays: state.plays.filter(p => p.family === 'smart_installers')}
    ].filter(group => group.plays.length);
    if (!groups.some(group => group.id === state.playFamily)) {
        state.playFamily = groups[0]?.id || 'property_intel';
    }
    const activePlay = state.plays.find(play => play.id === state.filters.play);
    if (activePlay) {
        state.playFamily = activePlay.family === 'smart_installers'
            ? 'smart_installers'
            : 'property_intel';
    }
    const activeGroup = groups.find(group => group.id === state.playFamily) || groups[0];

    const coverageText = play => {
        const coverage = play.coverage;
        if (!coverage) return '';
        if (coverage.status === 'unavailable') return 'Coverage unavailable';
        if (coverage.status === 'not_started') {
            return coverage.kind === 'pipeline'
                ? `${coverage.label}: refresh not run`
                : `${coverage.label}: none loaded`;
        }
        if (coverage.kind === 'pipeline') {
            return `${coverage.label}: ${coverage.percent}%`;
        }
        return `${coverage.label}: ${formatNumber(coverage.count)}`;
    };
    const card = play => {
        const countUnavailable = play.count_status === 'error' || play.count === null;
        const noMatches = play.count === 0;
        const pipelineNotReady = play.coverage?.kind === 'pipeline'
            && ['not_started', 'unavailable'].includes(play.coverage?.status)
            && noMatches;
        const pipelinePartial = play.coverage?.kind === 'pipeline'
            && play.coverage?.status === 'partial';
        const disabled = countUnavailable || pipelineNotReady || noMatches;
        let countLabel;
        if (countUnavailable) countLabel = 'Count unavailable';
        else if (pipelineNotReady) countLabel = 'Not ready';
        else if (noMatches && pipelinePartial) countLabel = 'No matches yet';
        else if (noMatches) countLabel = 'No matches';
        else countLabel = `${formatNumber(play.count)} match${play.count === 1 ? '' : 'es'}`;
        const source = coverageText(play);
        return `
        <button type="button"
                class="play-card ${state.filters.play === play.id ? 'active' : ''} ${disabled ? 'play-card-disabled' : ''}"
                aria-pressed="${state.filters.play === play.id}"
                ${disabled ? 'disabled' : ''}
                onclick="togglePlay('${play.id}')">
            <div class="play-card-top">
                <span class="play-name">${escapeHtml(play.name)}</span>
                <span class="play-count">${escapeHtml(countLabel)}</span>
            </div>
            <div class="play-desc">${escapeHtml(play.description)}</div>
            ${source ? `<span class="play-source ${['partial', 'not_started', 'unavailable'].includes(play.coverage?.status) ? 'play-source-warning' : ''}">${escapeHtml(source)}</span>` : ''}
            <span class="play-audience play-audience-${play.audience}">${
                play.audience === 'both' ? 'investors + contractors' : play.audience}</span>
        </button>
    `;
    };
    row.innerHTML = `
        <div class="play-family-tabs" role="tablist" aria-label="Prebuilt filter groups">
            ${groups.map(group => `
                <button type="button" class="play-family-tab ${group.id === state.playFamily ? 'active' : ''}"
                        role="tab" aria-selected="${group.id === state.playFamily}"
                        onclick="setPlayFamily('${group.id}')">
                    ${escapeHtml(group.label)}
                    <span>${group.plays.length}</span>
                </button>
            `).join('')}
        </div>
        <div class="play-family play-family-${activeGroup.id}" role="tabpanel">
            <div class="play-family-grid">${activeGroup.plays.map(card).join('')}</div>
        </div>
    `;
}

function setPlayFamily(familyId) {
    if (!['property_intel', 'smart_installers'].includes(familyId)) return;
    state.playFamily = familyId;
    renderPlayCards();
}

function renderPlaysHealth() {
    const node = document.getElementById('playsHealth');
    if (['error', 'partial'].includes(state.playsHealth?.counts)) {
        node.className = state.playsHealth.counts === 'partial'
            ? 'plays-health plays-health-warning'
            : 'plays-health plays-health-error';
        node.textContent = state.playsHealth.message || 'Counts unavailable';
        return;
    }
    const incomplete = state.plays.filter(play =>
        play.coverage?.kind === 'pipeline'
        && ['not_started', 'partial', 'unavailable'].includes(play.coverage.status)
    );
    if (incomplete.length) {
        node.className = 'plays-health plays-health-warning';
        node.textContent = 'Some counts are incomplete while source data refreshes.';
    } else {
        node.className = 'plays-health plays-health-ready';
        node.textContent = 'Counts ready';
    }
}

function togglePlay(playId) {
    if (state.filters.play === playId) {
        state.filters.play = null;
    } else {
        state.filters.play = playId;
        const play = state.plays.find(p => p.id === playId);
        if (play && play.recommended_sort) {
            applyRecommendedSort(play.recommended_sort);
        }
    }
    state.pagination.page = 1;
    renderPlayCards();
    renderPlayGuide();
    loadProperties();
}

// Signal sorts (unused FAR, CO date) only exist once a play recommends
// them — add the <option> on demand so the select stays clean otherwise.
const EXTRA_SORT_LABELS = {
    unused_far: 'Unused FAR',
    co_date: 'CO date',
    recent_permits: 'Recent permit activity',
};

function applyRecommendedSort(rec) {
    const sortSelect = document.getElementById('sortBy');
    if (!Array.from(sortSelect.options).some(o => o.value === rec.by)) {
        if (!EXTRA_SORT_LABELS[rec.by]) return;
        const opt = document.createElement('option');
        opt.value = rec.by;
        opt.textContent = EXTRA_SORT_LABELS[rec.by];
        sortSelect.appendChild(opt);
        // The enhanced control read its options at init; let it see the new one.
        MultiSelect.refresh('sortBy');
    }
    // A play's recommendation replaces the sort rather than adding to it.
    MultiSelect.set('sortBy', [rec.by], { silent: true });
    state.sort.by = [rec.by];
    MultiSelect.set('sortOrder', [rec.order], { silent: true });
    state.sort.order = rec.order;
}

function renderPlayGuide() {
    const guide = document.getElementById('playGuide');
    const play = state.plays.find(p => p.id === state.filters.play);
    if (!play) {
        guide.style.display = 'none';
        guide.innerHTML = '';
        return;
    }

    let html = `
        <div class="play-guide-head">
            <div>
                <h3>${escapeHtml(play.name)}</h3>
                <p>${escapeHtml(play.description)}</p>
            </div>
            <button class="btn btn-secondary btn-sm" onclick="togglePlay('${play.id}')">Clear play</button>
        </div>
    `;

    if (play.playbook) {
        const pb = play.playbook;
        html += `
            <div class="playbook">
                <p class="playbook-what">${escapeHtml(pb.what)}</p>
                <div class="playbook-ways">
                    ${pb.ways.map(w => `
                        <div class="playbook-way">
                            <h4>${escapeHtml(w.title)}</h4>
                            <p>${escapeHtml(w.body)}</p>
                        </div>
                    `).join('')}
                </div>
                <h4 class="playbook-steps-title">How to run it</h4>
                <ol class="playbook-steps">
                    ${pb.steps.map(s => `<li>${escapeHtml(s)}</li>`).join('')}
                </ol>
                <div class="playbook-caution">${escapeHtml(pb.caution)}</div>
            </div>
        `;
    } else if (play.how_to_use && play.how_to_use.length) {
        html += `
            <ol class="playbook-steps">
                ${play.how_to_use.map(s => `<li>${escapeHtml(s)}</li>`).join('')}
            </ol>
        `;
    }

    guide.innerHTML = html;
    guide.style.display = 'block';
}

async function checkResumableBulkEnrichJob() {
    try {
        const res = await fetch('/api/enrichment/bulk-jobs');
        const data = await res.json();
        if (!data.success || !data.jobs || !data.jobs.length) return;
        const active = data.jobs.find(j => ['pending', 'running', 'cancel_requested'].includes(j.status));
        if (!active) return;
        // Auto-open progress modal so the user can watch it / cancel.
        const modal = document.createElement('div');
        modal.className = 'modal';
        modal.id = 'bulk-enrich-modal';
        modal.style.display = 'block';
        modal.innerHTML = `<div class="modal-content bulk-enrich-modal-content"></div>`;
        document.body.appendChild(modal);
        renderBulkEnrichProgress(active.id, {
            total_owners_planned: active.total_owners_planned,
            total_properties: active.total_properties,
            estimated_max_cost: active.estimated_max_cost,
            customer_max_cost: active.estimated_max_cost,
            owner_strategy: active.owner_strategy,
            provider: active.provider,
            // Only set for admins — flips the admin cost row on
            provider_cost_per_lookup: active.provider_cost_per_lookup,
        });
        beginBulkEnrichPolling(active.id);
    } catch (e) {
        console.warn('Could not check resumable job:', e);
    }
}

// ==========================================
// DATA LOADING
// ==========================================

async function loadStats() {
    try {
        const response = await fetch('/api/properties/stats');
        const data = await response.json();
        
        if (data.success) {
            state.allStats = data.stats;
            updateStatsDisplay(data.stats);
        }
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

async function loadProperties() {
    showLoading(true);
    
    try {
        const params = buildPropertiesParams();
        // The address bar is the durable source of truth. The property-detail
        // navigation creates the next history entry, so browser Back lands on
        // this exact filter/sort/page URL even after a full reload.
        syncPropertiesUrl(params);
        
        const response = await fetch(`/api/properties?${params}`);
        const data = await response.json();
        
        if (data.success) {
            state.properties = data.properties;
            state.pagination = normalizePagination(data.pagination);
            if (peek.open && peekIndex(peek.bbl) < 0) closePeek();
            renderProperties();
            renderPagination();
            updateResultsCount();
        } else {
            showError('Failed to load properties');
        }
    } catch (error) {
        console.error('Error loading properties:', error);
        showError('Failed to load properties');
    } finally {
        showLoading(false);
        initialPropertiesSettled = true;
        restoreListPositionIfReady(false);
        // Whatever moved the filters — a control, a play, Back — the toolbar
        // says which saved search this is, or that it has drifted from one.
        if (typeof ssSyncLabel === 'function') ssSyncLabel();
    }
}

// ==========================================
// RENDERING
// ==========================================

function escapeAttr(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function profileHref(bbl) {
    const returnTo = encodeURIComponent(currentPropertiesUrl());
    return `/property/${encodeURIComponent(bbl)}?return_to=${returnTo}`;
}

// How long the current owner has held the lot, as a rep reads it: a fresh
// buyer is a different conversation from someone twenty years in.
function heldFor(saleDate) {
    if (!saleDate) return null;
    const then = new Date(saleDate);
    if (Number.isNaN(then.getTime())) return null;
    const months = Math.max(0, Math.round((Date.now() - then.getTime()) / (30.44 * 86400000)));
    if (months < 1) return 'New';
    if (months < 12) return `${months} mo`;
    const years = Math.round(months / 12);
    return `${years} yr${years === 1 ? '' : 's'}`;
}

const OWNER_KIND_CLASS = { Person: 'is-person', Public: 'is-public', Lender: 'is-lender' };

// The owner block: who owns it, what kind of owner that is, and the one
// human worth calling if the record names one. Everything else about the
// owner waits for Quick Look so the card stays scannable.
function ownerBlockHtml(p) {
    const owner = p.owner_display || 'Owner unknown';
    const kind = p.owner_kind;
    const reach = p.owner_reach;
    const kindTag = kind
        ? `<span class="owner-kind ${OWNER_KIND_CLASS[kind] || ''}">${escapeHtml(kind)}</span>` : '';
    let reachLine = '';
    if (reach) {
        reachLine = `
            <div class="owner-reach" title="${escapeAttr(reach.source)}">
                <i class="fas fa-user" aria-hidden="true"></i>
                <span class="owner-reach__name">${escapeHtml(reach.name)}</span>
                <span class="owner-reach__title">${escapeHtml(reach.title)}</span>
            </div>`;
    } else if (p.owner_is_person) {
        reachLine = '<div class="owner-hint">Individual owner — contacts can be unlocked</div>';
    }
    return `
        <div class="owner-block">
            <div class="owner-head"><span class="owner-label">Owner</span>${kindTag}</div>
            <button type="button" class="owner-name-btn" data-act="portfolio" data-owner="${escapeAttr(owner)}"
                    title="See everything this owner holds">${escapeHtml(owner)}</button>
            ${reachLine}
            <span class="owner-unlocked js-unlocked" data-bbl="${escapeAttr(p.bbl)}" hidden>
                <i class="fas fa-phone" aria-hidden="true"></i> Contacts unlocked
            </span>
        </div>`;
}

function renderProperties() {
    const container = document.getElementById('propertiesContainer');
    const noResults = document.getElementById('noResults');
    
    if (state.properties.length === 0) {
        container.innerHTML = '';
        noResults.style.display = 'block';
        return;
    }
    
    noResults.style.display = 'none';
    
    container.innerHTML = state.properties.map(property => {
        const assessedValue = property.assessed_total_value || 0;
        const salePrice = property.sale_price || 0;
        const permitCount = property.permit_count || 0;
        const totalUnits = property.total_units ?? property.units ?? property.residential_units;
        const violationCount = property.hpd_violations_count || property.hpd_open_violations || 0;
        const held = heldFor(property.sale_date);
        const href = profileHref(property.bbl);
        
        return `
            <article class="property-card" data-property-bbl="${escapeAttr(property.bbl)}" tabindex="0"
                     aria-label="${escapeAttr(property.address || 'Property')} — press Enter for a quick look">
                <div class="property-header">
                    ${property.bbl ? `
                        <label class="property-select" title="Select for bulk add to CRM">
                            <input type="checkbox" class="js-select-bbl" data-bbl="${escapeAttr(property.bbl)}"
                                   ${crmBulk.selected.has(String(property.bbl)) ? 'checked' : ''}>
                        </label>
                    ` : ''}
                    <div class="property-title-block">
                        <div class="property-address">
                            <a href="${href}" data-nav title="Open the full profile (⌘-click for a new tab)">${escapeHtml(property.address || 'Address N/A')}</a>
                        </div>
                        <div class="property-bbl">${formatBBL(property.bbl)}</div>
                    </div>
                    ${assessedValue > 0 ? `
                        <div class="property-value">
                            <div class="property-value-amount">$${formatNumber(assessedValue)}</div>
                            <div class="property-value-label">Assessed</div>
                        </div>
                    ` : ''}
                </div>

                ${ownerBlockHtml(property)}

                <div class="property-details">
                    ${totalUnits !== null && totalUnits !== undefined ? `
                        <div class="detail-item">
                            <div class="detail-label">Units</div>
                            <div class="detail-value">${formatNumber(totalUnits)}</div>
                        </div>
                    ` : ''}
                    ${property.year_built ? `
                        <div class="detail-item">
                            <div class="detail-label">Built</div>
                            <div class="detail-value">${property.year_built}</div>
                        </div>
                    ` : ''}
                    ${salePrice > 0 ? `
                        <div class="detail-item">
                            <div class="detail-label">Last sale</div>
                            <div class="detail-value">$${formatNumber(salePrice)}</div>
                        </div>
                    ` : ''}
                    ${held ? `
                        <div class="detail-item" title="Sold ${escapeAttr(formatDate(property.sale_date))}">
                            <div class="detail-label">Held</div>
                            <div class="detail-value">${held}</div>
                        </div>
                    ` : ''}
                    <div class="detail-item">
                        <div class="detail-label">Last permit</div>
                        <div class="detail-value">${property.last_permit_date
                            ? formatDate(property.last_permit_date)
                            : 'No history'}</div>
                    </div>
                </div>

                <div class="property-badges">
                    ${property.is_cash_purchase ? '<span class="badge badge-cash">Cash purchase</span>' : ''}
                    ${property.acris_total_transactions > 0 ? '<span class="badge badge-acris">ACRIS</span>' : ''}
                    ${permitCount > 0 ? `<span class="badge badge-permits">${permitCount} permit${permitCount > 1 ? 's' : ''}</span>` : ''}
                    ${violationCount > 0 ? `<span class="badge badge-violations">${violationCount} open violation${violationCount > 1 ? 's' : ''}</span>` : ''}
                    ${signalBadges(property)}
                </div>

                <div class="property-actions">
                    <button type="button" class="btn-view" data-act="peek" title="Quick look (Enter)">
                        Quick look
                    </button>
                    <a class="btn-view is-link" href="${href}" data-nav title="Open the full profile (⌘-click for a new tab)">
                        <i class="fas fa-arrow-up-right-from-square" aria-hidden="true"></i> Open
                    </a>
                    <button type="button" class="btn-portfolio js-crm-btn" data-bbl="${escapeAttr(property.bbl)}" data-act="crm" title="Add to CRM">
                        <i class="fas fa-address-book"></i>
                    </button>
                </div>
            </article>
        `;
    }).join('');

    refreshCrmStatus();
    refreshUnlockedStatus();
    updateBulkBar();
}

// Green "Contacts unlocked" chip on cards whose owner this user already paid
// to look up. Asked separately from /api/properties, which is cached across
// users and so cannot carry anything personal.
async function refreshUnlockedStatus() {
    const bbls = state.properties.map(p => p.bbl).filter(Boolean);
    if (!bbls.length) return;
    try {
        const res = await fetch(`/api/enrichment/unlocked-status?bbls=${encodeURIComponent(bbls.join(','))}`);
        const data = await res.json();
        const unlocked = (data && data.unlocked) || {};
        document.querySelectorAll('#propertiesContainer .js-unlocked').forEach(chip => {
            const isUnlocked = Boolean(unlocked[chip.dataset.bbl]);
            chip.hidden = !isUnlocked;
            // "contacts can be unlocked" is moot once they are.
            const hint = chip.parentElement.querySelector('.owner-hint');
            if (hint) hint.hidden = isUnlocked;
        });
    } catch (e) { /* decoration only */ }
}

// One listener for the whole grid. Links stay links (⌘-click, middle-click
// and "open in new tab" all just work); everything else on the card body is
// a Quick Look, and ⌘-clicking the body opens the profile in a new tab too.
function wireGridInteractions() {
    const container = document.getElementById('propertiesContainer');
    if (!container) return;

    container.addEventListener('click', (e) => {
        const card = e.target.closest('.property-card');
        if (!card) return;
        const bbl = card.dataset.propertyBbl;
        if (e.target.closest('a[data-nav]')) {
            saveListNavigationState(bbl);
            return;                       // the browser handles the navigation
        }
        if (e.target.closest('.property-select')) return;
        const act = e.target.closest('[data-act]');
        if (act) {
            e.preventDefault();
            if (act.dataset.act === 'peek') openPeek(bbl, card);
            else if (act.dataset.act === 'crm') crmOpen(bbl);
            else if (act.dataset.act === 'portfolio') viewOwnerPortfolio(act.dataset.owner);
            return;
        }
        if (e.metaKey || e.ctrlKey) {
            saveListNavigationState(bbl);
            window.open(profileHref(bbl), '_blank', 'noopener');
            return;
        }
        openPeek(bbl, card);
    });

    container.addEventListener('keydown', (e) => {
        if (!e.target.classList || !e.target.classList.contains('property-card')) return;
        if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            openPeek(e.target.dataset.propertyBbl, e.target);
        }
    });

    // Warm the Quick Look while the pointer rests on a card.
    let hoverTimer = null;
    container.addEventListener('mouseover', (e) => {
        const card = e.target.closest('.property-card');
        if (!card) return;
        clearTimeout(hoverTimer);
        hoverTimer = setTimeout(() => fetchPeek(card.dataset.propertyBbl).catch(() => {}), 220);
    });
    container.addEventListener('mouseleave', () => clearTimeout(hoverTimer));
}

// ==========================================
// QUICK LOOK
// ------------------------------------------
// A slide-over for one building: enough to decide whether to call, add to
// the CRM, or open the full profile — without leaving the grid or losing
// the scroll position. ←/→ walk the current page of results.
// ==========================================

const peek = { open: false, bbl: null, cache: new Map(), returnFocus: null, seq: 0 };

async function fetchPeek(bbl) {
    if (peek.cache.has(bbl)) return peek.cache.get(bbl);
    const res = await fetch(`/api/property/${encodeURIComponent(bbl)}/peek`);
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || 'Could not load this building');
    peek.cache.set(bbl, data);
    if (peek.cache.size > 40) peek.cache.delete(peek.cache.keys().next().value);
    return data;
}

function peekIndex(bbl) {
    return state.properties.findIndex(p => String(p.bbl) === String(bbl));
}

function openPeek(bbl, fromCard) {
    if (!bbl) return;
    const panel = document.getElementById('peek');
    const scrim = document.getElementById('peekScrim');
    const body = document.getElementById('peekBody');
    if (!panel || !body) return;
    if (!peek.open) peek.returnFocus = fromCard || document.activeElement;
    peek.open = true;
    peek.bbl = String(bbl);
    panel.hidden = false;
    scrim.hidden = false;

    const idx = peekIndex(bbl);
    const total = state.properties.length;
    document.getElementById('peekPos').textContent = idx >= 0 ? `${idx + 1} of ${total}` : '';
    panel.querySelector('[data-peek="prev"]').disabled = idx <= 0;
    panel.querySelector('[data-peek="next"]').disabled = idx < 0 || idx >= total - 1;
    document.getElementById('peekOpen').href = profileHref(bbl);

    body.innerHTML = `<div class="peek__loading">
        <div class="skeleton-line" style="width:60%;height:22px"></div>
        <div class="skeleton-line" style="width:40%"></div>
        <div class="skeleton-line" style="width:100%;height:64px;margin-top:8px"></div>
        <div class="skeleton-line" style="width:90%"></div>
        <div class="skeleton-line" style="width:70%"></div>
    </div>`;
    body.scrollTop = 0;
    const seq = ++peek.seq;
    fetchPeek(bbl).then(data => {
        if (seq !== peek.seq || !peek.open) return;
        renderPeek(data);
    }).catch(err => {
        if (seq !== peek.seq || !peek.open) return;
        body.innerHTML = `<div class="peek__error"><i class="fas fa-triangle-exclamation"></i><p>${escapeHtml(err.message || 'Could not load this building')}</p></div>`;
    });
    if (!fromCard || document.activeElement !== panel) panel.focus({ preventScroll: true });
}

function closePeek() {
    if (!peek.open) return;
    peek.open = false;
    document.getElementById('peek').hidden = true;
    document.getElementById('peekScrim').hidden = true;
    const target = peek.returnFocus;
    peek.returnFocus = null;
    if (target && typeof target.focus === 'function' && document.contains(target)) {
        target.focus({ preventScroll: true });
    }
}

function peekStep(delta) {
    const idx = peekIndex(peek.bbl);
    const next = state.properties[idx + delta];
    if (!next) return;
    const card = document.querySelector(`#propertiesContainer [data-property-bbl="${next.bbl}"]`);
    if (card) card.scrollIntoView({ block: 'nearest' });
    openPeek(next.bbl, card);
}

function peekMoney(value) {
    const n = Number(value);
    return Number.isFinite(n) && n > 0 ? `$${formatNumber(Math.round(n))}` : null;
}

function peekStat(label, value, cls) {
    return `<div class="peek-stat">
        <div class="peek-stat__label">${label}</div>
        <div class="peek-stat__value ${value == null ? 'is-muted' : (cls || '')}">${value == null ? '—' : value}</div>
    </div>`;
}

function renderPeek(data) {
    const b = data.building || {};
    const owner = data.owner || {};
    const permits = data.permits || { total: 0, recent: [] };
    const unlocked = (data.contacts && data.contacts.unlocked) || [];
    const bbl = b.bbl;
    const href = profileHref(bbl);
    const inCrm = crmStatus.inCrm[String(bbl)];
    const violations = Number(b.hpd_open_violations) || 0;
    const kindTag = owner.kind
        ? `<span class="owner-kind ${OWNER_KIND_CLASS[owner.kind] || ''}">${escapeHtml(owner.kind)}</span>` : '';
    const held = heldFor(b.sale_date);
    const portfolioCount = data.portfolio && data.portfolio.count;

    const ownerMeta = [
        portfolioCount > 1 ? `Owns <b>${formatNumber(portfolioCount)} properties</b>` : null,
        held ? `Held <b>${held}</b>${b.sale_date ? ` (since ${formatDate(b.sale_date)})` : ''}` : null,
        b.sos_entity_name ? `Registered as <b>${escapeHtml(b.sos_entity_name)}</b>${b.sos_entity_status ? ` · ${escapeHtml(b.sos_entity_status)}` : ''}` : null,
    ].filter(Boolean).join(' · ');

    const reach = owner.reach ? `
        <div class="peek-reach">
            <i class="fas fa-user-check" aria-hidden="true"></i>
            <div>
                <div class="peek-reach__name">${escapeHtml(owner.reach.name)}</div>
                <div class="peek-reach__title">${escapeHtml(owner.reach.title)} · ${escapeHtml(owner.reach.source)}</div>
            </div>
        </div>` : '';

    const people = [];
    (owner.others || []).forEach(o => people.push(
        `<div class="peek-person"><span class="peek-person__name">${escapeHtml(o.name)}</span><span class="peek-person__src">${escapeHtml(o.kind || '')} · ${escapeHtml(o.source)}</span></div>`));
    if (b.hpd_agent_name && !(owner.reach && owner.reach.name === b.hpd_agent_name)) {
        people.push(`<div class="peek-person"><span class="peek-person__name">${escapeHtml(b.hpd_agent_name)}</span><span class="peek-person__src">Managing agent · HPD</span></div>`);
    }
    if (b.hpd_site_manager_name && !(owner.reach && owner.reach.name === b.hpd_site_manager_name)) {
        people.push(`<div class="peek-person"><span class="peek-person__name">${escapeHtml(b.hpd_site_manager_name)}</span><span class="peek-person__src">Site manager · HPD</span></div>`);
    }

    const contacts = unlocked.length ? `
        <div class="peek-contacts">
            ${unlocked.map(c => `
                <div class="peek-contact">
                    <div class="peek-contact__who"><i class="fas fa-lock-open"></i> ${escapeHtml(c.owner_name || 'Owner')} · unlocked</div>
                    <div class="peek-contact__line">
                        ${(c.phones || []).slice(0, 3).map(ph => `<a href="tel:${escapeAttr(String(ph).replace(/[^\d+]/g, ''))}"><i class="fas fa-phone"></i> ${escapeHtml(ph)}</a>`).join('')}
                        ${(c.emails || []).slice(0, 2).map(em => `<a href="mailto:${escapeAttr(em)}"><i class="fas fa-envelope"></i> ${escapeHtml(em)}</a>`).join('')}
                    </div>
                </div>`).join('')}
        </div>` : ((owner.is_person || (owner.reach && owner.reach.role === 'principal')) ? `
        <div class="peek-unlock">Phone and email for this owner aren't unlocked yet — <a href="${href}" data-nav>unlock them on the full profile</a>.</div>` : '');

    const sale = [
        ['Last sale', peekMoney(b.sale_price)],
        ['Sold', b.sale_date ? formatDate(b.sale_date) : null],
        ['Buyer', b.sale_buyer_primary || null],
        ['Seller', b.sale_seller_primary || null],
        ['Mortgage', b.mortgage_amount ? `${peekMoney(b.mortgage_amount)}${b.mortgage_lender_primary ? ` · ${escapeHtml(b.mortgage_lender_primary)}` : ''}` : (b.is_cash_purchase ? 'None — cash purchase' : null)],
        ['Financing', b.financing_ratio != null && b.financing_ratio !== '' ? `${Math.round(Number(b.financing_ratio) * 100)}% financed` : null],
    ].filter(([, v]) => v);

    const permitRows = (permits.recent || []).map(pm => {
        const type = [pm.job_type, pm.work_type, pm.permit_type].filter(Boolean).join(' · ') || 'Permit';
        return `<div class="peek-permit">
            <div class="peek-permit__date">${pm.date ? formatDate(pm.date) : '—'}</div>
            <div class="peek-permit__what">
                <span class="peek-permit__type">${escapeHtml(type)}</span>
                ${pm.permit_status ? `<span class="peek-permit__status">${escapeHtml(pm.permit_status)}</span>` : ''}
                ${pm.work_description ? `<div class="peek-permit__desc" title="${escapeAttr(pm.work_description)}">${escapeHtml(pm.work_description)}</div>` : ''}
            </div>
        </div>`;
    }).join('');

    const sv = data.streetview || {};
    document.getElementById('peekBody').innerHTML = `
        <div class="peek__title">
            <h2 id="peekTitle"><a href="${href}" data-nav>${escapeHtml(b.address || 'Address N/A')}</a></h2>
            <div class="peek__sub">
                ${b.borough ? `<span>${escapeHtml(b.borough)}${b.zip_code ? ` ${escapeHtml(b.zip_code)}` : ''}</span>` : ''}
                <span class="mono">BBL ${formatBBL(bbl)}</span>
                ${b.bin ? `<span class="mono">BIN ${escapeHtml(b.bin)}</span>` : ''}
                ${b.building_class ? `<span>Class ${escapeHtml(b.building_class)}</span>` : ''}
            </div>
        </div>

        <div class="peek__stats">
            ${peekStat('Assessed', peekMoney(b.assessed_total_value))}
            ${peekStat('Units', b.total_units != null ? formatNumber(b.total_units) : null)}
            ${peekStat('Built', b.year_built || null)}
            ${peekStat('Floors', b.num_floors || null)}
            ${peekStat('Last sale', peekMoney(b.sale_price))}
            ${peekStat('Open violations', violations > 0 ? formatNumber(violations) : (b.hpd_open_violations === 0 ? '0' : null), violations > 0 ? 'is-warn' : '')}
        </div>

        <section class="peek__section">
            <div class="peek__section-head"><h3>Owner</h3>${kindTag}</div>
            <div class="peek-owner__primary">
                <div>
                    <div class="peek-owner__name">${escapeHtml(owner.display || 'Owner unknown')}</div>
                    ${ownerMeta ? `<div class="peek-owner__meta">${ownerMeta}</div>` : ''}
                </div>
            </div>
            ${reach}
            ${people.length ? `<div class="peek-people">${people.join('')}</div>` : ''}
            ${contacts}
        </section>

        ${sale.length ? `
        <section class="peek__section">
            <div class="peek__section-head"><h3>Sale &amp; financing</h3>${b.is_cash_purchase ? '<span class="badge badge-cash">Cash purchase</span>' : ''}</div>
            <dl class="peek-kv">${sale.map(([k, v]) => `<div><dt>${k}</dt><dd title="${escapeAttr(String(v).replace(/<[^>]+>/g, ''))}">${v}</dd></div>`).join('')}</dl>
        </section>` : ''}

        <section class="peek__section">
            <div class="peek__section-head">
                <h3>Permits${permits.total ? ` <span>${formatNumber(permits.total)}</span>` : ''}</h3>
                ${permits.total > (permits.recent || []).length ? `<a href="${href}" data-nav>All ${formatNumber(permits.total)} →</a>` : ''}
            </div>
            ${permitRows ? `<div class="peek-permits">${permitRows}</div>` : '<div class="peek__empty">No permits on file for this lot.</div>'}
        </section>

        <section class="peek__section peek-crm" data-bbl="${escapeAttr(bbl)}">
            <div class="peek__section-head"><h3>CRM</h3><span class="peek-crm__status js-peek-crm-status"></span></div>
            <div class="peek-crm__row">
                <button type="button" class="btn btn-primary btn-sm js-peek-log"><i class="fas fa-phone"></i> Log a contact</button>
                <button type="button" class="btn btn-secondary btn-sm js-peek-crm ${inCrm ? 'is-in-crm' : ''}" data-bbl="${escapeAttr(bbl)}">
                    <i class="fas ${inCrm ? 'fa-check' : 'fa-address-book'}"></i> ${inCrm ? 'Open in CRM' : 'Add to CRM'}
                </button>
            </div>
            ${peekLogFormHtml()}
        </section>

        <div class="peek__actions">
            ${sv.open_url ? `<a class="btn btn-secondary btn-sm" href="${escapeAttr(sv.open_url)}" target="_blank" rel="noopener"><i class="fas fa-${sv.open_kind === 'streetview' ? 'street-view' : 'map'}"></i> ${sv.open_kind === 'streetview' ? 'Street View' : 'Map'}</a>` : ''}
            <button type="button" class="btn btn-secondary btn-sm js-peek-portfolio" data-owner="${escapeAttr(owner.display || '')}" ${owner.display ? '' : 'disabled'}>
                <i class="fas fa-building"></i> Portfolio${portfolioCount > 1 ? ` (${formatNumber(portfolioCount)})` : ''}
            </button>
        </div>`;
    renderPeekCrmStatus(bbl);
}

// ---------- Quick Look → CRM logging ----------
// A rep can log the call right here and move to the next card. The building
// is added to the CRM on first use; the CRM's own /api/contacted does the
// rest (append-only activity, rollups, the prospect -> contacted stage bump).

const PEEK_METHODS = [['call', 'Call'], ['text', 'Text'], ['email', 'Email'], ['in_person', 'In person'], ['other', 'Other']];
const PEEK_OUTCOMES = [
    ['spoke', 'Spoke'], ['voicemail', 'Voicemail'], ['no_answer', 'No answer'],
    ['callback_requested', 'Callback requested'], ['meeting_set', 'Meeting set'],
    ['wrong_number', 'Wrong number'], ['not_interested', 'Not interested'],
];
const PEEK_FOLLOWUPS = [['', 'None'], ['1', 'Tomorrow'], ['3', '3 days'], ['7', 'Next week'], ['14', '2 weeks']];

function peekChips(role, options, selected) {
    return `<div class="peek-chips" data-role="${role}">${options.map(([value, label]) =>
        `<button type="button" class="peek-chip${value === selected ? ' is-on' : ''}" data-value="${escapeAttr(value)}">${label}</button>`
    ).join('')}</div>`;
}

function peekLogFormHtml() {
    return `
        <div class="peek-log" hidden>
            <div class="peek-log__label">How</div>
            ${peekChips('method', PEEK_METHODS, 'call')}
            <div class="peek-log__label">Outcome</div>
            ${peekChips('outcome', PEEK_OUTCOMES, null)}
            <textarea class="peek-log__note" data-role="note" rows="2" placeholder="What happened? Optional, but the team reads it."></textarea>
            <div class="peek-log__label">Follow up</div>
            ${peekChips('followup', PEEK_FOLLOWUPS, '')}
            <div class="peek-log__actions">
                <button type="button" class="btn btn-secondary btn-sm" data-role="cancel">Cancel</button>
                <button type="button" class="btn btn-primary btn-sm" data-role="save"><i class="fas fa-check"></i> Save contact</button>
            </div>
        </div>`;
}

function renderPeekCrmStatus(bbl) {
    const node = document.querySelector('#peekBody .js-peek-crm-status');
    if (!node || String(bbl) !== String(peek.bbl)) return;
    const st = crmStatus.status && crmStatus.status[String(bbl)];
    if (!st) { node.textContent = 'Not in the CRM yet'; return; }
    const stage = st.stage ? st.stage.charAt(0).toUpperCase() + st.stage.slice(1) : 'In CRM';
    const touched = st.last_contacted_at
        ? `contacted ${ssTimeAgo(st.last_contacted_at)}${st.contact_count > 1 ? ` · ${st.contact_count}×` : ''}`
        : 'no contact yet';
    node.textContent = `${stage} · ${touched}`;
    const crmBtn = document.querySelector('#peekBody .js-peek-crm');
    if (crmBtn) {
        crmBtn.classList.add('is-in-crm');
        crmBtn.innerHTML = '<i class="fas fa-check"></i> Open in CRM';
    }
}

function peekChipValue(form, role) {
    const on = form.querySelector(`[data-role="${role}"] .peek-chip.is-on`);
    return on ? on.dataset.value : null;
}

async function ensureCrmBuilding(bbl) {
    const known = crmStatus.inCrm[String(bbl)];
    if (known) return known;
    const res = await fetch('/crm/api/bulk-add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ bbls: [String(bbl)], with_contacts: true }),
    });
    const data = await res.json();
    const id = data && data.in_crm && data.in_crm[String(bbl)];
    if (!id) throw new Error(data.error || 'Could not add this building to the CRM');
    crmStatus.inCrm[String(bbl)] = id;
    return id;
}

async function submitPeekLog(section) {
    const bbl = section.dataset.bbl;
    const form = section.querySelector('.peek-log');
    const save = form.querySelector('[data-role="save"]');
    const outcome = peekChipValue(form, 'outcome');
    if (!outcome) {
        crmNotice('Pick an outcome first', 'error');
        form.querySelector('[data-role="outcome"]').classList.add('is-missing');
        return;
    }
    const method = peekChipValue(form, 'method') || 'call';
    const note = form.querySelector('[data-role="note"]').value.trim();
    const followDays = peekChipValue(form, 'followup');
    save.disabled = true;
    save.innerHTML = 'Saving…';
    try {
        const buildingId = await ensureCrmBuilding(bbl);
        const res = await fetch('/crm/api/contacted', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ building_id: buildingId, method, outcome, note: note || null }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Could not log the contact');
        let followText = '';
        if (followDays) {
            const fu = await fetch('/crm/api/followup', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ building_id: buildingId, days: Number(followDays), title: 'Follow up' }),
            });
            const fud = await fu.json();
            if (fud.success) followText = ` · follow-up in ${followDays} day${followDays === '1' ? '' : 's'}`;
        }
        form.hidden = true;
        form.querySelector('[data-role="note"]').value = '';
        form.querySelectorAll('[data-role="outcome"] .peek-chip').forEach(c => c.classList.remove('is-on'));
        form.querySelectorAll('[data-role="followup"] .peek-chip').forEach(c => c.classList.toggle('is-on', c.dataset.value === ''));
        crmNotice(`Logged ${PEEK_OUTCOMES.find(([v]) => v === outcome)[1].toLowerCase()}${followText}.`);
        await refreshCrmStatus();
    } catch (e) {
        crmNotice(e.message || 'Could not log the contact', 'error');
    } finally {
        save.disabled = false;
        save.innerHTML = '<i class="fas fa-check"></i> Save contact';
    }
}

function initQuickLook() {
    const panel = document.getElementById('peek');
    const scrim = document.getElementById('peekScrim');
    if (!panel) return;
    scrim.addEventListener('click', closePeek);
    panel.addEventListener('click', (e) => {
        const ctl = e.target.closest('[data-peek]');
        if (ctl) {
            if (ctl.dataset.peek === 'close') closePeek();
            if (ctl.dataset.peek === 'prev') peekStep(-1);
            if (ctl.dataset.peek === 'next') peekStep(1);
            return;
        }
        if (e.target.closest('a[data-nav]')) { saveListNavigationState(peek.bbl); return; }
        const crm = e.target.closest('.js-peek-crm');
        if (crm) { crmOpen(crm.dataset.bbl); return; }
        const logBtn = e.target.closest('.js-peek-log');
        if (logBtn) {
            const form = logBtn.closest('.peek-crm').querySelector('.peek-log');
            form.hidden = !form.hidden;
            if (!form.hidden) form.querySelector('[data-role="note"]').focus({ preventScroll: true });
            return;
        }
        const chip = e.target.closest('.peek-chip');
        if (chip) {
            const group = chip.parentElement;
            group.classList.remove('is-missing');
            group.querySelectorAll('.peek-chip').forEach(c => c.classList.toggle('is-on', c === chip));
            return;
        }
        const role = e.target.closest('.peek-log [data-role]');
        if (role && role.dataset.role === 'cancel') { role.closest('.peek-log').hidden = true; return; }
        if (role && role.dataset.role === 'save') { submitPeekLog(role.closest('.peek-crm')); return; }
        const pf = e.target.closest('.js-peek-portfolio');
        if (pf && pf.dataset.owner) { closePeek(); viewOwnerPortfolio(pf.dataset.owner); }
    });
    document.addEventListener('keydown', (e) => {
        if (!peek.open) return;
        const typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || '').toUpperCase());
        if (e.key === 'Escape') { e.preventDefault(); closePeek(); return; }
        if (typing) return;
        if (e.key === 'ArrowRight') { e.preventDefault(); peekStep(1); }
        if (e.key === 'ArrowLeft') { e.preventDefault(); peekStep(-1); }
    });
}

// Badges for the signal columns (only present in API responses once the
// signals migration has run — each guard tolerates their absence).
function signalBadges(p) {
    const badges = [];
    if (p.on_speculation_watch_list) {
        badges.push('<span class="badge badge-speculation">Speculation list</span>');
    }
    if (p.unused_far >= 1) {
        const buildable = p.lot_sqft ? ` (~${formatNumber(Math.round(p.unused_far * p.lot_sqft))} sqft)` : '';
        badges.push(`<span class="badge badge-upside">+${Number(p.unused_far).toFixed(1)} FAR${buildable}</span>`);
    }
    if (p.is_free_and_clear && p.acris_total_transactions > 0) {
        badges.push('<span class="badge badge-equity">Free &amp; clear</span>');
    }
    if (p.has_senior_exemption) {
        badges.push('<span class="badge badge-equity">Senior exemption</span>');
    }
    if (p.litigation_open_count > 0) {
        badges.push(`<span class="badge badge-violations">${p.litigation_open_count} open case${p.litigation_open_count > 1 ? 's' : ''}</span>`);
    }
    if (p.eviction_count > 0) {
        badges.push(`<span class="badge badge-violations">${p.eviction_count} eviction${p.eviction_count > 1 ? 's' : ''}</span>`);
    }
    if (p.has_tax_delinquency) {
        badges.push('<span class="badge badge-violations">Lien-sale notice</span>');
    }
    if (p.latest_co_date) {
        const coDate = new Date(p.latest_co_date);
        if (!isNaN(coDate) && (Date.now() - coDate.getTime()) < 200 * 86400000) {
            badges.push(`<span class="badge badge-permits">CO ${formatDate(p.latest_co_date)}</span>`);
        }
    }
    if (p.fisp_status && /^(UNSAFE|SWARMP)/i.test(p.fisp_status)) {
        badges.push(`<span class="badge badge-violations">FISP ${escapeHtml(p.fisp_status.split(' ')[0])}</span>`);
    }
    return badges.join('');
}

function renderPagination() {
    const container = document.getElementById('pagination');
    const { page, totalPages, hasNext, hasPrev } = state.pagination;
    
    if (totalPages <= 1) {
        container.innerHTML = '';
        return;
    }
    
    let html = `
        <button class="page-btn" onclick="goToPage(1)" ${!hasPrev ? 'disabled' : ''}>
            <i class="fas fa-angle-double-left"></i>
        </button>
        <button class="page-btn" onclick="goToPage(${page - 1})" ${!hasPrev ? 'disabled' : ''}>
            <i class="fas fa-angle-left"></i> Prev
        </button>
    `;
    
    // Show page numbers
    const maxButtons = 7;
    let startPage = Math.max(1, page - Math.floor(maxButtons / 2));
    let endPage = Math.min(totalPages, startPage + maxButtons - 1);
    
    if (endPage - startPage < maxButtons - 1) {
        startPage = Math.max(1, endPage - maxButtons + 1);
    }
    
    for (let i = startPage; i <= endPage; i++) {
        html += `
            <button class="page-btn ${i === page ? 'active' : ''}" onclick="goToPage(${i})">
                ${i}
            </button>
        `;
    }
    
    html += `
        <button class="page-btn" onclick="goToPage(${page + 1})" ${!hasNext ? 'disabled' : ''}>
            Next <i class="fas fa-angle-right"></i>
        </button>
        <button class="page-btn" onclick="goToPage(${totalPages})" ${!hasNext ? 'disabled' : ''}>
            <i class="fas fa-angle-double-right"></i>
        </button>
    `;
    
    container.innerHTML = html;
}

function updateStatsDisplay(stats) {
    document.getElementById('statTotal').textContent = formatNumber(stats.total_properties || 0);
    document.getElementById('statValue').textContent = '$' + formatNumber(Math.round((stats.total_assessed_value || 0) / 1000000)) + 'M';
    document.getElementById('statCash').textContent = formatNumber(stats.cash_purchases || 0);
    document.getElementById('statRecent').textContent = formatNumber(stats.recent_sales_90d || 0);
}

function updateResultsCount() {
    const totalCount = state.pagination.total_count || state.pagination.totalCount || 0;
    const text = totalCount === 1 ? '1 property' : `${formatNumber(totalCount)} properties`;
    document.getElementById('resultsCount').textContent = text;
}

// ==========================================
// EVENT LISTENERS
// ==========================================

function initializeEventListeners() {
    // Borough, property type, building class, units, value, kind of work,
    // recency and violations are wired by the shared module so this page and
    // the contractors page read them identically.
    SharedFilters.bind(() => {
        state.shared = SharedFilters.read();
        state.pagination.page = 1;
        loadProperties();
    });

    // Universal search
    let searchTimeout;
    document.getElementById('universalSearch').addEventListener('input', (e) => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            state.filters.search = e.target.value.trim();
            state.pagination.page = 1;
            loadProperties();
        }, 500);
    });
    
    // Owner search
    let ownerTimeout;
    document.getElementById('ownerSearch').addEventListener('input', (e) => {
        clearTimeout(ownerTimeout);
        ownerTimeout = setTimeout(() => {
            state.filters.owner = e.target.value.trim();
            state.pagination.page = 1;
            loadProperties();
        }, 500);
    });
    
    // Value range
    // Sale price range
    document.getElementById('minSalePrice').addEventListener('change', (e) => {
        state.filters.minSalePrice = e.target.value ? parseFloat(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('maxSalePrice').addEventListener('change', (e) => {
        state.filters.maxSalePrice = e.target.value ? parseFloat(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Sale date range
    document.getElementById('saleDateFrom').addEventListener('change', (e) => {
        state.filters.saleDateFrom = e.target.value || null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('saleDateTo').addEventListener('change', (e) => {
        state.filters.saleDateTo = e.target.value || null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Borough filter (multi-select via checkboxes)
    // Permit filters
    document.getElementById('withPermits').addEventListener('change', (e) => {
        state.filters.withPermits = e.target.checked;
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('minPermits').addEventListener('change', (e) => {
        state.filters.minPermits = e.target.value ? parseInt(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Financing range
    document.getElementById('financingMin').addEventListener('change', (e) => {
        state.filters.financingMin = e.target.value ? parseFloat(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('financingMax').addEventListener('change', (e) => {
        state.filters.financingMax = e.target.value ? parseFloat(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Cash only checkbox
    document.getElementById('cashOnly').addEventListener('change', (e) => {
        state.filters.cashOnly = e.target.checked;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Owner type (multi-select, OR'd)
    document.getElementById('ownerKind').addEventListener('change', () => {
        state.filters.ownerKinds = MultiSelect.values('ownerKind');
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Sort controls
    // Sort accepts several keys; later ones break ties in earlier ones.
    document.getElementById('sortBy').addEventListener('change', () => {
        state.sort.by = MultiSelect.values('sortBy');
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('sortOrder').addEventListener('change', (e) => {
        state.sort.order = e.target.value;
        loadProperties();
    });
    
    // Per page
    document.getElementById('perPage').addEventListener('change', (e) => {
        state.pagination.perPage = parseInt(e.target.value);
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Clear filters button
    document.getElementById('clearFiltersBtn').addEventListener('click', clearFilters);
    
    // Export button - show modal
    document.getElementById('exportBtn').addEventListener('click', showExportModal);
}

// ==========================================
// EXPORT FUNCTIONALITY
// ==========================================

function showExportModal() {
    const totalCount = state.pagination.total_count || state.pagination.totalCount || 0;
    document.getElementById('exportCount').textContent = formatNumber(Math.min(totalCount, 10000));
    document.getElementById('exportModal').style.display = 'flex';
}

function closeExportModal() {
    document.getElementById('exportModal').style.display = 'none';
}

async function downloadExport() {
    // Get selected fields
    const checkboxes = document.querySelectorAll('input[name="export_field"]:checked');
    const fields = Array.from(checkboxes).map(cb => cb.value);
    
    if (fields.length === 0) {
        alert('Please select at least one field to export');
        return;
    }
    
    // Check if enrichment is requested
    const enrichContactsCheckbox = document.getElementById('exportEnrichContacts');
    const enrichContacts = enrichContactsCheckbox && enrichContactsCheckbox.checked;
    
    // Build query params from current filters
    const params = new URLSearchParams();
    
    if (state.filters.search) params.append('search', state.filters.search);
    if (state.filters.owner) params.append('owner', state.filters.owner);
    SharedFilters.toParams(params, state.shared);
    if (state.filters.minSalePrice) params.append('min_sale_price', state.filters.minSalePrice);
    if (state.filters.maxSalePrice) params.append('max_sale_price', state.filters.maxSalePrice);
    if (state.filters.saleDateFrom) params.append('sale_date_from', state.filters.saleDateFrom);
    if (state.filters.saleDateTo) params.append('sale_date_to', state.filters.saleDateTo);
    if (state.filters.cashOnly) params.append('cash_only', 'true');
    if (state.filters.withPermits) params.append('with_permits', 'true');
    if (state.filters.minPermits) params.append('min_permits', state.filters.minPermits);
    if (state.filters.recentSaleDays) params.append('recent_sale_days', state.filters.recentSaleDays);
    if (state.filters.financingMin) params.append('financing_min', state.filters.financingMin);
    if (state.filters.financingMax) params.append('financing_max', state.filters.financingMax);
    appendMulti(params, 'owner_kind', state.filters.ownerKinds);
    if (state.filters.play) params.append('play', state.filters.play);

    // Add sort
    appendMulti(params, 'sort_by', state.sort.by);
    params.append('sort_order', state.sort.order);

    // Add selected fields
    params.append('fields', fields.join(','));
    
    // If enrichment is requested, we need to do a POST request with confirmation
    if (enrichContacts) {
        const totalCount = Math.min(state.pagination.total_count || 0, 10000);
        const estimatedEnrichable = Math.round(totalCount * 0.6);
        const estimatedCost = Math.max(estimatedEnrichable * 0.35, 0.50).toFixed(2);
        
        if (!confirm(`This export will include contact enrichment.\n\nEstimated max cost: $${estimatedCost}\n\nYou'll only be charged for new enrichments - previously unlocked contacts are free.\n\nProceed with enrichment?`)) {
            return;
        }
        
        params.append('enrich_contacts', 'true');

        // Show loading indicator
        const exportBtn = document.getElementById('exportDownloadBtn');
        const originalText = exportBtn.innerHTML;
        exportBtn.textContent = 'Enriching & exporting…';
        exportBtn.disabled = true;
        
        try {
            // POST request for enrichment export (since it can take time and charges money)
            const response = await fetch(`/api/properties/export-with-enrichment?${params}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'Export failed');
            }
            
            // Download the response as a file
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `properties_export_enriched_${new Date().toISOString().slice(0,10)}.csv`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            a.remove();
            
            closeExportModal();
        } catch (error) {
            alert('Export failed: ' + error.message);
        } finally {
            exportBtn.innerHTML = originalText;
            exportBtn.disabled = false;
        }
    } else {
        // Standard export without enrichment - direct download
        window.location.href = `/api/properties/export?${params}`;
        closeExportModal();
    }
}

// ==========================================
// NAVIGATION
// ==========================================

function viewProperty(bbl) {
    saveListNavigationState(bbl);
    const returnTo = encodeURIComponent(currentPropertiesUrl());
    window.location.href = `/property/${encodeURIComponent(bbl)}?return_to=${returnTo}`;
}

async function viewOwnerPortfolio(ownerName) {
    try {
        const response = await fetch(`/api/owner/${encodeURIComponent(ownerName)}/portfolio`);
        const data = await response.json();
        
        if (data.success) {
            showPortfolioModal(data);
        } else {
            alert('Failed to load owner portfolio');
        }
    } catch (error) {
        console.error('Error loading portfolio:', error);
        alert('Failed to load owner portfolio');
    }
}

function showPortfolioModal(data) {
    document.getElementById('portfolioOwnerName').textContent = data.owner_name;
    document.getElementById('portfolioCount').textContent = formatNumber(data.stats.total_properties);
    document.getElementById('portfolioValue').textContent = '$' + formatNumber(data.stats.total_assessed_value);
    document.getElementById('portfolioUnits').textContent = formatNumber(data.stats.total_units);
    document.getElementById('portfolioCash').textContent = formatNumber(data.stats.cash_purchases);
    
    const propertiesList = document.getElementById('portfolioProperties');
    propertiesList.innerHTML = data.properties.map(prop => `
        <div class="property-card" data-property-bbl="${prop.bbl}"
             onclick="viewProperty('${prop.bbl}')">
            <div class="property-header">
                <div>
                    <div class="property-address">${escapeHtml(prop.address || 'Address N/A')}</div>
                    <div class="property-bbl">${formatBBL(prop.bbl)}</div>
                </div>
                ${prop.assessed_total_value ? `
                    <div class="property-value">
                        <div class="property-value-amount">$${formatNumber(prop.assessed_total_value)}</div>
                        <div class="property-value-label">Assessed</div>
                    </div>
                ` : ''}
            </div>
            <div class="property-details">
                ${prop.units ? `
                    <div class="detail-item">
                        <div class="detail-label">Units</div>
                        <div class="detail-value">${prop.units}</div>
                    </div>
                ` : ''}
                ${prop.sale_price ? `
                    <div class="detail-item">
                        <div class="detail-label">Purchase price</div>
                        <div class="detail-value">$${formatNumber(prop.sale_price)}</div>
                    </div>
                ` : ''}
                ${prop.sale_date ? `
                    <div class="detail-item">
                        <div class="detail-label">Purchase date</div>
                        <div class="detail-value">${formatDate(prop.sale_date)}</div>
                    </div>
                ` : ''}
                ${prop.permit_count > 0 ? `
                    <div class="detail-item">
                        <div class="detail-label">Permits</div>
                        <div class="detail-value">${prop.permit_count}</div>
                    </div>
                ` : ''}
            </div>
        </div>
    `).join('');
    
    document.getElementById('portfolioModal').style.display = 'flex';
}

function closePortfolioModal() {
    document.getElementById('portfolioModal').style.display = 'none';
}

function goToPage(page) {
    state.pagination.page = page;
    loadProperties();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ==========================================
// UTILITIES
// ==========================================

function resetFilters() {
    state.filters = {
        search: '',
        owner: '',
        minSalePrice: null,
        maxSalePrice: null,
        saleDateFrom: null,
        saleDateTo: null,
        cashOnly: false,
        withPermits: false,
        minPermits: null,
        recentSaleDays: null,
        financingMin: null,
        financingMax: null,
        smartFilter: null,
        ownerKinds: [],
        play: null
    };
}

function clearFilters() {
    resetFilters();
    savedSearch.openedId = null;
    renderPlayCards();
    renderPlayGuide();
    
    // Clear all form inputs
    document.getElementById('universalSearch').value = '';
    document.getElementById('ownerSearch').value = '';
    document.getElementById('minSalePrice').value = '';
    document.getElementById('maxSalePrice').value = '';
    document.getElementById('saleDateFrom').value = '';
    document.getElementById('saleDateTo').value = '';
    // Silent so the single reload below is the only fetch. Scoped to the
    // sidebar so the toolbar's sort and per-page controls keep their values.
    SharedFilters.clear();
    state.shared = SharedFilters.read();
    document.getElementById('minPermits').value = '';
    document.getElementById('financingMin').value = '';
    document.getElementById('financingMax').value = '';
    document.getElementById('cashOnly').checked = false;
    document.getElementById('withPermits').checked = false;
    
    state.pagination.page = 1;
    loadProperties();
}

function showLoading(show) {
    document.getElementById('loadingState').style.display = show ? 'block' : 'none';
    document.getElementById('propertiesContainer').style.opacity = show ? '0.5' : '1';
}

function showError(message) {
    alert(message);
}

function formatNumber(num) {
    if (!num) return '0';
    return new Intl.NumberFormat('en-US').format(num);
}

function formatDate(dateStr) {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatBBL(bbl) {
    if (!bbl) return 'N/A';
    if (bbl.length === 10) {
        return `${bbl[0]}-${bbl.slice(1, 6)}-${bbl.slice(6)}`;
    }
    return bbl;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ==========================================
// BULK ENRICHMENT
// ==========================================
//
// Flow:
//   1. POST /api/enrichment/bulk-estimate  with {filters, owner_strategy}
//      -> returns total_owners + estimated cost
//   2. User confirms (and types CONFIRM if cost > $500)
//   3. POST /api/enrichment/bulk-job/start -> returns job_id
//   4. Poll GET /api/enrichment/bulk-job/<id> every few seconds
//   5. Show progress bar / counters / cancel button until status terminal.

// Build a {filters: {...}} payload that mirrors the params the /api/properties
// query is using. This is what gets sent to the bulk endpoints so the server
// enriches the EXACT set of properties the user is viewing.
function buildBulkEnrichFiltersPayload() {
    const f = state.filters;
    const payload = {};
    if (f.search) payload.search = f.search;
    if (f.owner) payload.owner = f.owner;
    Object.assign(payload, SharedFilters.toPayload(state.shared));
    if (f.minSalePrice) payload.min_sale_price = f.minSalePrice;
    if (f.maxSalePrice) payload.max_sale_price = f.maxSalePrice;
    if (f.saleDateFrom) payload.sale_date_from = f.saleDateFrom;
    if (f.saleDateTo) payload.sale_date_to = f.saleDateTo;
    if (f.cashOnly) payload.cash_only = true;
    if (f.withPermits) payload.with_permits = true;
    if (f.minPermits) payload.min_permits = f.minPermits;
    if (f.recentSaleDays) payload.recent_sale_days = f.recentSaleDays;
    if (f.financingMin) payload.financing_min = f.financingMin;
    if (f.financingMax) payload.financing_max = f.financingMax;
    if (f.ownerKinds.length) payload.owner_kind = f.ownerKinds;
    if (f.play) payload.play = f.play;
    return payload;
}

let _bulkEnrichPollTimer = null;

async function showBulkEnrichModal() {
    const totalFiltered = state.pagination.total_count || state.pagination.totalCount || 0;
    if (totalFiltered === 0) {
        alert('No properties match the current filters. Adjust filters first.');
        return;
    }

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.id = 'bulk-enrich-modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content bulk-enrich-modal-content">
            <button type="button" class="modal-close" onclick="closeBulkEnrichModal()"
                    aria-label="Close bulk enrichment">&times;</button>
            <h2>Bulk people enrichment</h2>
            <p class="modal-subtitle">Finds contact information only for confident human
                candidates on every property matching your current filters
                (<strong>${formatNumber(totalFiltered)}</strong> total, not just this page).
                Companies, banks, trusts, and registered agents are excluded.</p>

            <div class="be-strategy">
                <h4>Which people to enrich per property</h4>
                <label>
                    <input type="radio" name="be-strategy" value="recommended" checked>
                    <strong>Best human candidate</strong> &mdash; at most 1 person per property
                    (matched SOS principal if available, otherwise the highest-priority person)
                </label>
                <label>
                    <input type="radio" name="be-strategy" value="all">
                    <strong>All human candidates</strong> &mdash; enriches every distinct
                    person found across matched SOS, ACRIS deeds, PLUTO, HPD, and RPAD. Higher cost.
                </label>
            </div>

            <!-- Admin-only provider selector. Populated by refreshBulkEnrichEstimate
                 once we know whether the user is_admin. -->
            <div id="be-provider-block" style="display:none;"></div>

            <div id="be-estimate-block" class="loading-spinner">
                <p>Calculating cost for ${formatNumber(totalFiltered)} properties...</p>
            </div>

            <div class="bulk-enrich-footer" id="be-footer">
                <button class="btn btn-secondary" onclick="closeBulkEnrichModal()">Cancel</button>
                <button class="btn btn-primary" id="be-start-btn" disabled>Calculating…</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    // Stash for use by other handlers
    window._beFilters = buildBulkEnrichFiltersPayload();
    window._beProvider = 'enformion_fallback';

    // Re-estimate whenever the strategy changes
    document.querySelectorAll('input[name="be-strategy"]').forEach(r => {
        r.addEventListener('change', refreshBulkEnrichEstimate);
    });
    refreshBulkEnrichEstimate();
}

function renderProviderSelectorIfAdmin(data) {
    // Once the first estimate comes back we know is_admin. Inject the
    // Enformion/Apify selector for admins; leave it hidden for everyone else.
    const block = document.getElementById('be-provider-block');
    if (!block) return;
    if (!data.is_admin) {
        block.style.display = 'none';
        block.innerHTML = '';
        return;
    }
    if (block.dataset.rendered === '1') return;  // already built; don't clobber selection

    const current = window._beProvider || 'enformion_fallback';
    block.style.display = '';
    block.dataset.rendered = '1';
    block.innerHTML = `
        <div class="be-strategy">
            <h4>Enrichment provider <span class="be-admin-tag">admin only</span></h4>
            <label>
                <input type="radio" name="be-provider" value="enformion_fallback" ${current === 'enformion_fallback' ? 'checked' : ''}>
                <strong>Enformion → Apify fallback</strong> (default)
            </label>
            <label>
                <input type="radio" name="be-provider" value="enformion" ${current === 'enformion' ? 'checked' : ''}>
                <strong>Enformion only</strong>
            </label>
            <label>
                <input type="radio" name="be-provider" value="apify" ${current === 'apify' ? 'checked' : ''}>
                <strong>Apify TruePeopleSearch only</strong>
            </label>
        </div>
    `;
    block.querySelectorAll('input[name="be-provider"]').forEach(r => {
        r.addEventListener('change', e => {
            window._beProvider = e.target.value;
            refreshBulkEnrichEstimate();
        });
    });
}

async function refreshBulkEnrichEstimate() {
    const block = document.getElementById('be-estimate-block');
    const startBtn = document.getElementById('be-start-btn');
    if (!block || !startBtn) return;

    const strategy = (document.querySelector('input[name="be-strategy"]:checked') || {}).value || 'recommended';
    const provider = window._beProvider || 'enformion_fallback';
    startBtn.disabled = true;
    startBtn.textContent = 'Calculating…';
    block.innerHTML = `<div class="loading-spinner"><p>Calculating cost…</p></div>`;

    try {
        const res = await fetch('/api/enrichment/bulk-estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filters: window._beFilters,
                owner_strategy: strategy,
                provider: provider,
            }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to get estimate');

        window._beEstimate = data;
        renderProviderSelectorIfAdmin(data);

        // Customer rate is what regular users would owe. Admin sees the same
        // number so margin is transparent, but is not actually charged.
        const customerPerLookup = data.customer_cost_per_lookup ?? data.cost_per_lookup ?? 0.35;
        const customerMax = data.customer_max_cost ?? data.max_cost ?? 0;
        const customerMaxStr = `$${customerMax.toFixed(2)}`;
        const customerPerStr = `$${customerPerLookup.toFixed(2)}`;
        const startBtnCostStr = data.is_admin ? 'FREE for admin' : `up to ${customerMaxStr}`;

        // Admin-only: real upstream cost to the developer.
        // Wrapped in <details> so it stays collapsed by default — the admin
        // can click to reveal it, but on customer screen-shares the actual
        // vendor cost stays hidden behind a neutral "Admin info" chip.
        let adminCostHtml = '';
        if (data.is_admin && data.provider_cost_per_lookup !== undefined) {
            const providerLabel = ({
                'enformion': 'Enformion',
                'apify': 'Apify TruePeopleSearch',
                'enformion_fallback': 'Enformion → Apify fallback',
                'apify_fallback': 'Apify → Enformion fallback',
            })[data.provider] || data.provider;
            const realUnit = data.provider_cost_per_lookup;
            const realMax = data.provider_max_cost || 0;
            adminCostHtml = `
                <details class="be-admin-cost">
                    <summary>Admin info (click to reveal)</summary>
                    <div class="be-admin-cost-row">
                        <span>Real provider cost (${providerLabel}):</span>
                        <strong>$${realMax.toFixed(2)}</strong>
                    </div>
                    <div class="be-admin-cost-row sub">
                        <span>Per lookup:</span>
                        <span>$${realUnit.toFixed(4)} × ${formatNumber(data.total_owners)}</span>
                    </div>
                    <small>You (admin) are not charged. This is what the upstream vendor bills.</small>
                </details>
            `;
        }

        block.innerHTML = `
            <div class="bulk-enrich-summary">
                <div class="summary-row">
                    <span class="summary-label">Properties matching filters:</span>
                    <span class="summary-value">${formatNumber(data.total_properties)}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-label">Properties with human candidates:</span>
                    <span class="summary-value">${formatNumber(data.properties_with_owners)}</span>
                </div>
                <div class="summary-row highlight">
                    <span class="summary-label">People to enrich (${strategy}):</span>
                    <span class="summary-value">${formatNumber(data.total_owners)}</span>
                </div>
            </div>

            <div class="cost-breakdown">
                <h4>Cost ${data.is_admin ? '<small style="font-weight:normal;">(customer rate — admin is not charged)</small>' : ''}</h4>
                <div class="math-display">
                    <p>${formatNumber(data.total_owners)} people × ${customerPerStr} = <strong>${customerMaxStr}</strong></p>
                </div>
                <p class="cost-note"><small>
                    Only lookups that return data are charged. Batch pricing: ${customerPerStr} per lookup.
                    ${data.total_owners > 0 && data.total_owners < 2 ? 'Minimum charge of $0.50 applies.' : ''}
                </small></p>
            </div>

            ${adminCostHtml}

            ${data.requires_typed_confirmation ? `
                <div class="be-confirm-block">
                    This run will charge up to <strong>${customerMaxStr}</strong>.
                    Type <code>CONFIRM</code> to authorize.
                    <input type="text" id="be-confirm-input" class="be-confirm-input"
                           placeholder="Type CONFIRM" autocomplete="off">
                </div>
            ` : ''}
        `;

        if (data.total_owners === 0) {
            startBtn.textContent = 'No people to enrich';
            startBtn.disabled = true;
            return;
        }

        startBtn.disabled = false;
        startBtn.textContent = `Enrich ${formatNumber(data.total_owners)} people (${startBtnCostStr})`;
        startBtn.onclick = startBulkEnrichJob;

        if (data.requires_typed_confirmation) {
            // Disable until they type CONFIRM
            startBtn.disabled = true;
            const ci = document.getElementById('be-confirm-input');
            ci.addEventListener('input', () => {
                startBtn.disabled = ci.value.trim().toUpperCase() !== 'CONFIRM';
            });
        }
    } catch (err) {
        block.innerHTML = `<p class="error-message">Error: ${err.message}</p>`;
        startBtn.textContent = 'Retry';
        startBtn.disabled = false;
        startBtn.onclick = refreshBulkEnrichEstimate;
    }
}

async function startBulkEnrichJob() {
    const startBtn = document.getElementById('be-start-btn');
    if (!startBtn || startBtn.disabled) return;
    const strategy = (document.querySelector('input[name="be-strategy"]:checked') || {}).value || 'recommended';
    const provider = window._beProvider || 'enformion_fallback';
    const confirmEl = document.getElementById('be-confirm-input');
    const confirmText = confirmEl ? confirmEl.value.trim() : '';

    startBtn.disabled = true;
    startBtn.textContent = 'Starting…';

    try {
        const res = await fetch('/api/enrichment/bulk-job/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filters: window._beFilters,
                owner_strategy: strategy,
                provider: provider,
                confirm_typed: confirmText,
            }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to start job');

        renderBulkEnrichProgress(data.job_id, data);
        beginBulkEnrichPolling(data.job_id);
    } catch (err) {
        alert('Could not start bulk enrichment: ' + err.message);
        startBtn.disabled = false;
        startBtn.textContent = 'Try again';
    }
}

function renderBulkEnrichProgress(jobId, initialData) {
    const modal = document.getElementById('bulk-enrich-modal');
    if (!modal) return;
    const content = modal.querySelector('.modal-content');
    const planned = initialData.total_owners_planned || 0;
    const props = initialData.total_properties || 0;
    const customerMax = initialData.customer_max_cost ?? initialData.estimated_max_cost ?? 0;
    const strategy = initialData.owner_strategy || 'recommended';
    const provider = initialData.provider || 'enformion_fallback';
    const providerLabel = ({
        'enformion': 'Enformion',
        'apify': 'Apify',
        'enformion_fallback': 'Enformion→Apify',
    })[provider] || provider;
    const isAdmin = !!initialData.provider_cost_per_lookup; // server only sends this for admin
    const adminUnitCost = initialData.provider_cost_per_lookup || 0;

    content.innerHTML = `
        <button type="button" class="modal-close" onclick="closeBulkEnrichModal()"
                aria-label="Close bulk enrichment">&times;</button>
        <h2>Bulk enrichment running</h2>
        <p class="modal-subtitle">
            Job #${jobId} &middot; ${formatNumber(props)} properties &middot;
            ${formatNumber(planned)} people (${strategy}) &middot;
            provider: <strong>${providerLabel}</strong> &middot;
            customer rate: up to <strong>$${customerMax.toFixed(2)}</strong>
        </p>

        <div class="be-progress-wrap">
            <div class="be-progress-bar"><div class="be-progress-fill" id="be-pf"></div></div>
            <div class="be-progress-meta">
                <span id="be-progress-text">Starting…</span>
                <span id="be-progress-pct">0%</span>
            </div>
        </div>

        <div class="be-counters">
            <span class="be-counter success">Successful: <strong id="be-c-success">0</strong></span>
            <span class="be-counter failed">No data: <strong id="be-c-failed">0</strong></span>
            <span class="be-counter skipped">Skipped: <strong id="be-c-skipped">0</strong></span>
            <span class="be-counter">Properties: <strong id="be-c-props">0</strong> / ${formatNumber(props)}</span>
        </div>

        ${isAdmin ? `
            <details class="be-admin-cost" id="be-admin-cost-live">
                <summary>Admin info (click to reveal)</summary>
                <div class="be-admin-cost-row">
                    <span>Real provider cost so far (${providerLabel}, $${adminUnitCost.toFixed(4)}/lookup):</span>
                    <strong id="be-admin-cost-val">$0.00</strong>
                </div>
                <small>You (admin) are not charged. This is the actual vendor cost as enrichments complete.</small>
            </details>
        ` : ''}

        <p id="be-charge-msg" style="margin: 0.5rem 0;"></p>

        <div class="bulk-enrich-footer">
            <button class="btn btn-secondary" id="be-cancel-btn" onclick="cancelBulkEnrichJob(${jobId})">
                Stop after current property
            </button>
            <button class="btn btn-primary" id="be-close-btn" style="display:none;" onclick="closeBulkEnrichModal(); loadProperties();">
                Done — refresh results
            </button>
        </div>
    `;
}

function beginBulkEnrichPolling(jobId) {
    if (_bulkEnrichPollTimer) clearInterval(_bulkEnrichPollTimer);
    const tick = async () => {
        try {
            const res = await fetch(`/api/enrichment/bulk-job/${jobId}`);
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Poll failed');
            updateBulkEnrichProgressUI(data.job);
            if (['completed', 'failed', 'cancelled'].includes(data.job.status)) {
                clearInterval(_bulkEnrichPollTimer);
                _bulkEnrichPollTimer = null;
                finalizeBulkEnrichUI(data.job);
            }
        } catch (e) {
            // Keep polling; surface intermittent errors quietly
            console.warn('Poll error:', e);
        }
    };
    tick();
    _bulkEnrichPollTimer = setInterval(tick, 3000);
}

function updateBulkEnrichProgressUI(job) {
    const planned = job.total_owners_planned || 1;
    const attempted = job.owners_attempted || 0;
    const pct = Math.min(100, Math.round((attempted / planned) * 100));
    const pf = document.getElementById('be-pf');
    if (pf) pf.style.width = `${pct}%`;
    const pctEl = document.getElementById('be-progress-pct');
    if (pctEl) pctEl.textContent = `${pct}%`;
    const txt = document.getElementById('be-progress-text');
    if (txt) txt.textContent = `${formatNumber(attempted)} / ${formatNumber(planned)} owners attempted — status: ${job.status}`;
    const elS = document.getElementById('be-c-success');
    if (elS) elS.textContent = formatNumber(job.owners_successful || 0);
    const elF = document.getElementById('be-c-failed');
    if (elF) elF.textContent = formatNumber(job.owners_failed || 0);
    const elSk = document.getElementById('be-c-skipped');
    if (elSk) elSk.textContent = formatNumber(job.owners_skipped || 0);
    const elP = document.getElementById('be-c-props');
    if (elP) elP.textContent = formatNumber(job.properties_processed || 0);
    // Admin only: server includes provider_actual_cost on each poll
    if (typeof job.provider_actual_cost === 'number') {
        const elA = document.getElementById('be-admin-cost-val');
        if (elA) elA.textContent = `$${job.provider_actual_cost.toFixed(2)}`;
    }
}

function finalizeBulkEnrichUI(job) {
    const cancelBtn = document.getElementById('be-cancel-btn');
    const closeBtn = document.getElementById('be-close-btn');
    const txt = document.getElementById('be-progress-text');
    const chargeMsg = document.getElementById('be-charge-msg');

    if (cancelBtn) cancelBtn.style.display = 'none';
    if (closeBtn) closeBtn.style.display = 'inline-block';

    let headline;
    if (job.status === 'completed') headline = `Completed. Charged $${(job.total_charged || 0).toFixed(2)}.`;
    else if (job.status === 'cancelled') headline = `Cancelled. Charged $${(job.total_charged || 0).toFixed(2)} for ${formatNumber(job.owners_successful || 0)} completed lookups.`;
    else headline = `Failed: ${job.error_message || 'unknown error'}`;

    if (txt) txt.textContent = headline;
    if (chargeMsg && job.error_message) {
        chargeMsg.innerHTML = `<small style="color: var(--amber, #925e04);">${job.error_message}</small>`;
    }
}

async function cancelBulkEnrichJob(jobId) {
    const btn = document.getElementById('be-cancel-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Stopping…'; }
    try {
        await fetch(`/api/enrichment/bulk-job/${jobId}/cancel`, { method: 'POST' });
    } catch (e) {
        console.warn('Cancel failed:', e);
    }
}

function closeBulkEnrichModal() {
    if (_bulkEnrichPollTimer) {
        clearInterval(_bulkEnrichPollTimer);
        _bulkEnrichPollTimer = null;
    }
    const modal = document.getElementById('bulk-enrich-modal');
    if (modal) modal.remove();
}

// ==========================================
// EXPORT ENRICHMENT FUNCTIONS
// ==========================================

/**
 * Update enrichment cost estimate when checkbox is toggled
 */
async function updateEnrichmentEstimate() {
    const checkbox = document.getElementById('exportEnrichContacts');
    const estimateDiv = document.getElementById('enrichmentEstimate');
    
    if (!checkbox.checked) {
        estimateDiv.style.display = 'none';
        return;
    }
    
    estimateDiv.style.display = 'block';
    estimateDiv.innerHTML = '<div class="estimate-loading">Calculating enrichment cost…</div>';
    
    try {
        // Build query params from current filters
        const params = new URLSearchParams();
        
        if (state.filters.search) params.append('search', state.filters.search);
        if (state.filters.owner) params.append('owner', state.filters.owner);
        SharedFilters.toParams(params, state.shared);
        if (state.filters.withPermits) params.append('with_permits', 'true');
        
        // Call API to get accurate estimate
        const response = await fetch(`/api/properties/export/enrichment-estimate?${params}`);
        const data = await response.json();
        
        if (!data.success) {
            throw new Error(data.error || 'Failed to calculate estimate');
        }
        
        const isAdmin = data.is_admin;
        const cost = isAdmin ? 0 : data.estimated_cost;
        
        estimateDiv.innerHTML = `
            <div class="estimate-result">
                <div class="estimate-row">
                    <span>Properties to export</span>
                    <strong>${formatNumber(data.total_properties)}</strong>
                </div>
                <div class="estimate-row">
                    <span>Properties with contacts</span>
                    <strong>${formatNumber(data.properties_with_contacts)}</strong>
                </div>
                <div class="estimate-row">
                    <span>Total enrichable contacts</span>
                    <strong>${formatNumber(data.total_contacts)}</strong>
                </div>
                <div class="estimate-row unlocked">
                    <span>Already unlocked (free)</span>
                    <strong>${formatNumber(data.already_unlocked)}</strong>
                </div>
                <div class="estimate-row charged">
                    <span>New enrichments (charged)</span>
                    <strong>${formatNumber(data.need_enrichment)}</strong>
                </div>
                <div class="estimate-row estimate-total">
                    <span>${isAdmin ? 'Admin — free' : 'Total cost'}</span>
                    <strong>${isAdmin ? 'FREE' : '$' + cost.toFixed(2)}</strong>
                </div>
                ${!isAdmin && data.need_enrichment > 0 ? `
                    <p class="estimate-note">
                        <small>$${data.cost_per_contact.toFixed(2)} per new contact (${data.need_enrichment} × $${data.cost_per_contact.toFixed(2)} = $${cost.toFixed(2)})</small>
                    </p>
                ` : ''}
                ${isAdmin ? '<p class="estimate-note"><small>Admin access — all enrichments are free</small></p>' : ''}
            </div>
        `;
    } catch (error) {
        console.error('Enrichment estimate error:', error);
        estimateDiv.innerHTML = '<div class="estimate-error">Failed to calculate estimate: ' + escapeHtml(error.message) + '</div>';
    }
}

// ==========================================
// CRM INTEGRATION
// ==========================================

// BBL -> CRM building id for everything rendered so far this visit.
const crmStatus = { inCrm: {}, status: {} };

function crmOpen(bbl) {
    const crmId = crmStatus.inCrm[bbl];
    window.location.href = crmId
        ? `/crm/buildings/${crmId}`
        : `/crm/buildings/add?bbl=${encodeURIComponent(bbl)}`;
}

async function refreshCrmStatus() {
    const bbls = state.properties.map(p => p.bbl).filter(Boolean);
    if (!bbls.length) return;
    try {
        const res = await fetch('/crm/api/bbl-status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ bbls })
        });
        const data = await res.json();
        if (!data.success) return;
        Object.assign(crmStatus.inCrm, data.in_crm || {});
        Object.assign(crmStatus.status, data.status || {});
        if (peek.open) renderPeekCrmStatus(peek.bbl);
        document.querySelectorAll('.js-crm-btn').forEach(btn => {
            if (crmStatus.inCrm[btn.dataset.bbl]) {
                btn.innerHTML = '<i class="fas fa-check"></i>';
                btn.title = 'In CRM — open it';
                btn.classList.add('is-in-crm');
            }
        });
    } catch (e) { /* the CRM chip is decoration; never break the grid */ }
}

function crmNotice(message, kind) {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
        stack = document.createElement('div');
        stack.className = 'toast-stack';
        document.body.appendChild(stack);
    }
    const el = document.createElement('div');
    el.className = `toast toast-${kind === 'error' ? 'error' : 'success'}`;
    el.innerHTML = kind === 'error'
        ? '<i class="fas fa-circle-exclamation"></i><span></span>'
        : '<i class="fas fa-check-circle"></i><span></span>';
    el.querySelector('span').textContent = message;
    stack.appendChild(el);
    setTimeout(() => { el.classList.add('leaving'); setTimeout(() => el.remove(), 220); }, 3500);
}

// ==========================================
// SAVED SEARCHES
// ------------------------------------------
// A saved search is this page's whole view — every sidebar filter, the play,
// the sort and the page size — under a name. Saving stores the querystring,
// so a search re-runs live: the filters are evaluated again on every open and
// pick up permits and sales that landed since. Team searches double as the
// CRM's live lead lists, which is why they share one store.
// ==========================================

const savedSearch = {
    items: [],
    loaded: false,
    activeId: null,     // the saved search the current filters match
    openedId: null,     // the one the person opened, even after they tweak it
    editingId: null,    // row being renamed/updated in the dialog
    repoint: false,     // dialog: replace the stored filters with what's on screen
    find: '',
};

// param -> the control whose <option> labels name its values. Read from the
// DOM so codes filled in from /api/permits/facets describe themselves too.
const OWNER_KIND_SLUGS = new Set([
    'person', 'llc', 'corporation', 'partnership', 'trust', 'coop',
    'nonprofit', 'lender', 'public', 'multiple', 'other',
]);

const SS_OPTION_SOURCES = {
    owner_kind: 'ownerKind',
    borough: 'boroughFilter',
    property_type: 'propertyType',
    building_class: 'buildingClass',
    work_type: 'workType',
    job_type: 'jobType',
    permit_type: 'permitType',
    license_type: 'licenseType',
    has_violations: 'violationsFilter',
};

const SS_FLAGS = {
    cash_only: 'Cash purchases',
    with_permits: 'Has permits',
};

// `unit` trails the whole range ("5-20 units"); a unit with no leading space
// binds to each number instead ("10-50%").
const SS_RANGES = [
    { min: 'min_units', max: 'max_units', unit: ' units' },
    { min: 'min_value', max: 'max_value', label: 'Assessed', money: true },
    { min: 'min_sale_price', max: 'max_sale_price', label: 'Sale', money: true },
    { min: 'sale_date_from', max: 'sale_date_to', label: 'Sold', date: true },
    { min: 'financing_min', max: 'financing_max', label: 'Financed', unit: '%' },
];

const SS_SORT_LABELS = {
    sale_date: 'sale date', value: 'assessed value', sale_price: 'sale price',
    address: 'address', owner: 'owner', permits: 'permit count', units: 'units',
    unused_far: 'unused FAR', co_date: 'CO date', recent_permits: 'recent permits',
};

function ssMoney(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return String(value);
    if (n >= 1e9) return `$${(n / 1e9).toFixed(n % 1e9 ? 1 : 0)}B`;
    if (n >= 1e6) return `$${(n / 1e6).toFixed(n % 1e6 ? 1 : 0)}M`;
    if (n >= 1e3) return `$${Math.round(n / 1e3)}k`;
    return `$${n}`;
}

function ssOptionLabel(param, value) {
    const select = document.getElementById(SS_OPTION_SOURCES[param] || '');
    const option = select && Array.from(select.options)
        .find(o => o.value === String(value));
    // Facet options carry their own counts — "Plumbing (1,204)" — which are
    // noise in a one-line summary of what a search asks for.
    return option ? option.textContent.replace(/\s*\(\d[\d,]*\)\s*$/, '').trim() : String(value);
}

function ssRangeChip(params, range) {
    const lo = params.get(range.min);
    const hi = params.get(range.max);
    if (!lo && !hi) return null;
    const unit = range.unit || '';
    const trails = unit.startsWith(' ');   // " units" reads after the range
    const one = v => {
        if (range.money) return ssMoney(v);
        if (range.date) return formatDate(v);
        return trails ? String(v) : `${v}${unit}`;
    };
    const tail = trails ? unit : '';
    let body;
    if (lo && hi) body = `${one(lo)}–${one(hi)}${tail}`;
    else if (lo) body = range.date ? `since ${one(lo)}` : (trails ? `${one(lo)}+${tail}` : `${one(lo)}+`);
    else body = range.date ? `before ${one(hi)}` : `up to ${one(hi)}${tail}`;
    return range.label ? `${range.label} ${body}` : body;
}

/** The current filters in plain English, one chip per idea. */
function describeSearch(querystring) {
    const params = new URLSearchParams(querystring || '');
    const chips = [];
    const add = text => { if (text) chips.push(text); };

    if (params.get('search')) add(`“${params.get('search')}”`);
    if (params.get('owner')) add(`Owner “${params.get('owner')}”`);

    Object.keys(SS_OPTION_SOURCES).forEach(param => {
        const values = repeatedParamValues(params, param);
        if (!values.length) return;
        const labels = values.map(v => ssOptionLabel(param, v));
        add(labels.length > 3 ? `${labels.slice(0, 2).join(', ')} +${labels.length - 2}` : labels.join(', '));
    });

    SS_RANGES.forEach(range => add(ssRangeChip(params, range)));
    Object.entries(SS_FLAGS).forEach(([param, label]) => {
        if (trueParam(params, param)) add(label);
    });
    if (trueParam(params, 'has_enrichable_owner') && !params.getAll('owner_kind').length) add('Person');

    if (params.get('min_permits')) add(`${params.get('min_permits')}+ permits`);
    if (params.get('recent_sale_days')) add(`Sold in ${params.get('recent_sale_days')}d`);
    const permitDays = params.get('recent_permit_days');
    if (permitDays) {
        add(params.get('permit_activity_mode') === 'inactive'
            ? `No permit in ${permitDays}d`
            : `Permit in ${permitDays}d`);
    }
    const play = state.plays.find(p => p.id === params.get('play'));
    if (play) add(`Play: ${play.name}`);
    else if (params.get('play')) add(`Play: ${params.get('play')}`);

    const sortKeys = repeatedParamValues(params, 'sort_by')
        .map(key => SS_SORT_LABELS[key] || key);
    if (sortKeys.length) {
        add(`Sorted by ${sortKeys.join(', ')}` +
            (params.get('sort_order') === 'asc' ? ' (low to high)' : ''));
    }
    return chips;
}

/**
 * A comparable form of a querystring.
 *
 * Page number is dropped (where someone was scrolled to is not part of what
 * they saved) and the rest is sorted, so filters picked in a different order
 * still recognise their saved search.
 */
function ssNormalize(querystring) {
    const params = new URLSearchParams(querystring || '');
    params.delete('page');
    const pairs = [];
    params.forEach((value, key) => { if (value !== '') pairs.push(`${key}=${value}`); });
    return pairs.sort().join('&');
}

function ssCurrentQuery() {
    const params = buildPropertiesParams();
    params.delete('page');
    return params.toString();
}

function ssTimeAgo(iso) {
    if (!iso) return '';
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return '';
    const mins = Math.round((Date.now() - then.getTime()) / 60000);
    if (mins < 2) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    if (days < 30) return `${days}d ago`;
    return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

async function ssLoad(force) {
    if (savedSearch.loaded && !force) return;
    try {
        const res = await fetch('/crm/api/saved-filters?page=properties');
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'load failed');
        savedSearch.items = data.searches || [];
        savedSearch.loaded = true;
    } catch (e) {
        savedSearch.items = [];
        savedSearch.loaded = false;
    }
    ssRender();
    ssSyncLabel();
}

/** Reflect on the toolbar which saved search (if any) is on screen. */
function ssSyncLabel() {
    const current = ssNormalize(ssCurrentQuery());
    const match = savedSearch.items.find(item => ssNormalize(item.querystring) === current);
    savedSearch.activeId = match ? match.id : null;
    if (match) savedSearch.openedId = match.id;

    const label = document.getElementById('savedSearchLabel');
    const dirty = document.getElementById('savedSearchDirty');
    const button = document.getElementById('savedSearchBtn');
    if (!label || !button) return;
    label.textContent = match ? match.name : 'Saved searches';
    button.classList.toggle('is-active', Boolean(match));
    // A dot marks filters that have moved on from the search that was opened.
    const opened = savedSearch.items.find(item => item.id === savedSearch.openedId);
    if (dirty) dirty.hidden = !(opened && !match);
    if (!match && opened) label.textContent = `${opened.name} (edited)`;
    ssRenderFoot();
    document.querySelectorAll('#savedSearchList .ss-row').forEach(row => {
        row.classList.toggle('is-active', Number(row.dataset.id) === savedSearch.activeId);
    });
}

function ssRender() {
    const list = document.getElementById('savedSearchList');
    const count = document.getElementById('savedSearchCount');
    const findWrap = document.getElementById('savedSearchFindWrap');
    if (!list) return;

    if (count) count.textContent = savedSearch.items.length
        ? `${savedSearch.items.length}` : '';
    if (findWrap) findWrap.hidden = savedSearch.items.length < 6;

    const needle = savedSearch.find.trim().toLowerCase();
    const items = needle
        ? savedSearch.items.filter(item => item.name.toLowerCase().includes(needle))
        : savedSearch.items;

    if (!savedSearch.items.length) {
        list.innerHTML = `
            <div class="saved-search__empty">
                <i class="fas fa-bookmark" aria-hidden="true"></i>
                <p>No saved searches yet.</p>
                <span>Set up the filters you use every week, then save them here to run them in one click.</span>
            </div>`;
        ssRenderFoot();
        return;
    }
    if (!items.length) {
        list.innerHTML = '<div class="saved-search__empty"><p>Nothing matches that.</p></div>';
        return;
    }

    list.innerHTML = items.map(item => {
        const chips = describeSearch(item.querystring);
        const summary = chips.length ? chips.join(' · ') : 'No filters — every property';
        const meta = [
            item.is_mine ? null : escapeHtml(item.owner_name || 'a teammate'),
            item.last_used_at
                ? `run ${ssTimeAgo(item.last_used_at)}${item.use_count > 1 ? ` · ${item.use_count}×` : ''}`
                : `saved ${ssTimeAgo(item.created_at)}`,
        ].filter(Boolean).join(' · ');
        return `
        <div class="ss-row${item.id === savedSearch.activeId ? ' is-active' : ''}" data-id="${item.id}" role="listitem">
            <button type="button" class="ss-row__main" data-act="apply" title="Run this search">
                <span class="ss-row__name">
                    ${item.is_pinned ? '<i class="fas fa-thumbtack ss-row__pin" aria-hidden="true"></i>' : ''}
                    ${escapeHtml(item.name)}
                    ${item.visibility === 'private' ? '<i class="fas fa-lock ss-row__lock" title="Only you can see this"></i>' : ''}
                </span>
                <span class="ss-row__summary">${escapeHtml(summary)}</span>
                <span class="ss-row__meta">${meta}</span>
            </button>
            <div class="ss-row__acts">${item.can_edit ? `
                <button type="button" data-act="pin" title="${item.is_pinned ? 'Unpin' : 'Pin to the top'}"
                        class="${item.is_pinned ? 'is-on' : ''}"><i class="fas fa-thumbtack"></i></button>
                <button type="button" data-act="edit" title="Rename or re-save"><i class="fas fa-pen"></i></button>
                <button type="button" data-act="delete" title="Delete"><i class="fas fa-trash-can"></i></button>` : ''}
            </div>
        </div>`;
    }).join('');

    document.querySelectorAll('#savedSearchList .ss-row').forEach(row => {
        const id = Number(row.dataset.id);
        row.querySelectorAll('[data-act]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                ssAction(btn.dataset.act, id);
            });
        });
    });
}

/** The menu's footer: save what is on screen, or push it onto the open search. */
function ssRenderFoot() {
    const foot = document.getElementById('savedSearchFoot');
    if (!foot) return;
    const opened = savedSearch.items.find(item => item.id === savedSearch.openedId);
    const drifted = opened && savedSearch.activeId !== opened.id && opened.can_edit;
    foot.innerHTML = `
        ${drifted ? `<button type="button" class="btn btn-secondary btn-sm" data-foot="update">
            <i class="fas fa-arrows-rotate"></i> Update “${escapeHtml(opened.name)}”
        </button>` : ''}
        <button type="button" class="btn btn-primary btn-sm" data-foot="new">
            <i class="fas fa-plus"></i> Save current search
        </button>`;
    foot.querySelectorAll('[data-foot]').forEach(btn => {
        btn.addEventListener('click', () => {
            if (btn.dataset.foot === 'update') ssUpdateQuery(savedSearch.openedId);
            else ssOpenDialog(null);
        });
    });
}

function ssAction(action, id) {
    const item = savedSearch.items.find(s => s.id === id);
    if (!item) return;
    if (action === 'apply') return ssApply(item);
    if (action === 'edit') return ssOpenDialog(item);
    if (action === 'pin') return ssPatch(id, { is_pinned: !item.is_pinned });
    if (action === 'delete') return ssDelete(item);
}

/**
 * Run a saved search without a reload: rebuild state from its querystring,
 * push it back into every control, and refetch. loadProperties() rewrites the
 * address bar, so the view stays linkable and Back still works.
 */
function ssApply(item) {
    const params = new URLSearchParams(item.querystring || '');
    restoreStateFromUrl(params);
    state.pagination.page = 1;
    SharedFilters.apply(state.shared);
    applyStateToControls();
    renderPlayCards();
    renderPlayGuide();
    clearBulkSelection();
    savedSearch.openedId = item.id;
    ssCloseMenu();
    loadProperties();
    ssSyncLabel();
    fetch(`/crm/api/saved-filter/${item.id}/used`, { method: 'POST' }).catch(() => {});
    item.last_used_at = new Date().toISOString();
}

async function ssPatch(id, body, quiet) {
    try {
        const res = await fetch(`/crm/api/saved-filter/${id}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Could not save that change');
        if (data.search) {
            const index = savedSearch.items.findIndex(item => item.id === id);
            if (index >= 0) savedSearch.items[index] = data.search;
        }
        await ssLoad(true);
        if (!quiet) crmNotice('Saved search updated.');
        return true;
    } catch (e) {
        crmNotice(e.message || 'Could not save that change', 'error');
        return false;
    }
}

function ssUpdateQuery(id) {
    ssPatch(id, { querystring: ssCurrentQuery() }, true).then(ok => {
        if (ok) crmNotice('Saved search now points at these filters.');
    });
}

async function ssDelete(item) {
    if (!confirm(`Delete the saved search “${item.name}”?`)) return;
    try {
        await fetch(`/crm/api/saved-filter/${item.id}/delete`, { method: 'POST' });
        if (savedSearch.openedId === item.id) savedSearch.openedId = null;
        await ssLoad(true);
        crmNotice('Saved search deleted.');
    } catch (e) {
        crmNotice('Could not delete that search', 'error');
    }
}

// ---------- the save / rename dialog ----------

function ssVisibility() {
    const on = document.querySelector('#savedSearchVisibility .seg__btn.is-on');
    return on ? on.dataset.value : 'team';
}

function ssSetVisibility(value) {
    document.querySelectorAll('#savedSearchVisibility .seg__btn').forEach(btn => {
        const on = btn.dataset.value === value;
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-checked', on ? 'true' : 'false');
    });
}

function ssOpenDialog(item) {
    savedSearch.editingId = item ? item.id : null;
    const modal = document.getElementById('savedSearchModal');
    const name = document.getElementById('savedSearchName');
    const chips = document.getElementById('savedSearchChips');
    const submit = document.getElementById('savedSearchSubmit');
    document.getElementById('savedSearchModalTitle').textContent =
        item ? 'Edit saved search' : 'Save this search';
    submit.textContent = item ? 'Save changes' : 'Save search';
    name.value = item ? item.name : '';
    ssSetVisibility(item ? item.visibility : 'team');

    // Editing keeps the search's own filters unless the button below is used,
    // so the summary has to show the filters that will actually be stored.
    const query = item ? item.querystring : ssCurrentQuery();
    const parts = describeSearch(query);
    chips.innerHTML = parts.length
        ? parts.map(part => `<span class="ss-chip">${escapeHtml(part)}</span>`).join('')
        : '<span class="ss-chip is-empty">No filters — every property</span>';

    const drifted = item && ssNormalize(query) !== ssNormalize(ssCurrentQuery());
    let repoint = document.getElementById('savedSearchRepoint');
    if (repoint) repoint.remove();
    if (drifted) {
        repoint = document.createElement('button');
        repoint.id = 'savedSearchRepoint';
        repoint.type = 'button';
        repoint.className = 'btn-clear ss-repoint';
        repoint.textContent = 'Replace with the filters on screen';
        repoint.addEventListener('click', () => {
            savedSearch.repoint = true;
            const now = describeSearch(ssCurrentQuery());
            chips.innerHTML = now.length
                ? now.map(part => `<span class="ss-chip">${escapeHtml(part)}</span>`).join('')
                : '<span class="ss-chip is-empty">No filters — every property</span>';
            repoint.remove();
        });
        chips.parentElement.appendChild(repoint);
    }
    savedSearch.repoint = !item;

    ssCloseMenu();
    modal.style.display = 'block';
    name.focus();
    name.select();
}

function ssCloseDialog() {
    document.getElementById('savedSearchModal').style.display = 'none';
    savedSearch.editingId = null;
    savedSearch.repoint = false;
}

async function ssSubmitDialog() {
    const nameNode = document.getElementById('savedSearchName');
    const submit = document.getElementById('savedSearchSubmit');
    const name = nameNode.value.trim();
    if (!name) {
        nameNode.focus();
        crmNotice('Give the search a name first', 'error');
        return;
    }
    submit.disabled = true;
    try {
        if (savedSearch.editingId) {
            const body = { name, visibility: ssVisibility() };
            if (savedSearch.repoint) body.querystring = ssCurrentQuery();
            const ok = await ssPatch(savedSearch.editingId, body, true);
            if (!ok) return;
            crmNotice('Saved search updated.');
        } else {
            const res = await fetch('/crm/api/saved-filter', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name,
                    querystring: ssCurrentQuery(),
                    page: 'properties',
                    visibility: ssVisibility(),
                }),
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Could not save the search');
            savedSearch.openedId = data.filter_id;
            await ssLoad(true);
            crmNotice('Search saved — open it any time from Saved searches.');
        }
        ssCloseDialog();
        ssSyncLabel();
    } catch (e) {
        crmNotice(e.message || 'Could not save the search', 'error');
    } finally {
        submit.disabled = false;
    }
}

// ---------- menu open/close ----------

function ssOpenMenu() {
    const menu = document.getElementById('savedSearchMenu');
    const button = document.getElementById('savedSearchBtn');
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    ssLoad(true);
    const find = document.getElementById('savedSearchFind');
    if (find && !document.getElementById('savedSearchFindWrap').hidden) find.focus();
}

function ssCloseMenu() {
    const menu = document.getElementById('savedSearchMenu');
    const button = document.getElementById('savedSearchBtn');
    if (!menu) return;
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
}

function ssToggleMenu() {
    const menu = document.getElementById('savedSearchMenu');
    if (menu.hidden) ssOpenMenu(); else ssCloseMenu();
}

function initSavedSearches() {
    const button = document.getElementById('savedSearchBtn');
    if (!button) return;
    button.addEventListener('click', (e) => { e.stopPropagation(); ssToggleMenu(); });

    const find = document.getElementById('savedSearchFind');
    if (find) find.addEventListener('input', () => {
        savedSearch.find = find.value;
        ssRender();
    });

    document.addEventListener('click', (e) => {
        const wrap = document.getElementById('savedSearch');
        if (wrap && !wrap.contains(e.target)) ssCloseMenu();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Escape') return;
        if (document.getElementById('savedSearchModal').style.display === 'block') ssCloseDialog();
        else ssCloseMenu();
    });

    document.getElementById('savedSearchModalClose').addEventListener('click', ssCloseDialog);
    document.getElementById('savedSearchCancel').addEventListener('click', ssCloseDialog);
    document.getElementById('savedSearchSubmit').addEventListener('click', ssSubmitDialog);
    document.getElementById('savedSearchName').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') ssSubmitDialog();
    });
    document.querySelectorAll('#savedSearchVisibility .seg__btn').forEach(btn => {
        btn.addEventListener('click', () => ssSetVisibility(btn.dataset.value));
    });
    document.getElementById('savedSearchModal').addEventListener('click', (e) => {
        if (e.target.id === 'savedSearchModal') ssCloseDialog();
    });

    ssLoad(true);
}

// ---------- bulk multi-select add ----------

// Selected BBLs survive re-renders and page changes within the visit.
const crmBulk = { selected: new Set(), submitting: false };

function updateBulkBar() {
    const bar = document.getElementById('crmBulkBar');
    if (!bar) return;
    const n = crmBulk.selected.size;
    bar.hidden = n === 0;
    const count = document.getElementById('crmBulkCount');
    if (count) count.textContent = `${n} selected`;
}

function clearBulkSelection() {
    crmBulk.selected.clear();
    document.querySelectorAll('.js-select-bbl').forEach(cb => { cb.checked = false; });
    updateBulkBar();
}

async function openCrmBulkModal() {
    if (!crmBulk.selected.size) return;
    const modal = document.getElementById('crmBulkModal');
    document.getElementById('crmBulkTitle').textContent =
        `Add ${crmBulk.selected.size} building${crmBulk.selected.size > 1 ? 's' : ''} to CRM`;
    document.getElementById('crmBulkProgress').hidden = true;
    document.getElementById('crmBulkNewList').value = '';
    const go = document.getElementById('crmBulkGo');
    go.disabled = false;
    go.innerHTML = 'Add buildings';
    modal.style.display = 'block';
    // Fill the list picker with the team's current lists.
    try {
        const res = await fetch('/crm/api/lists');
        const data = await res.json();
        if (data.success) {
            const select = document.getElementById('crmBulkList');
            select.innerHTML = '<option value="">No list</option>' + data.lists.map(l =>
                `<option value="${l.id}">${escapeHtml(l.name)}</option>`).join('');
        }
    } catch (e) { /* picker just stays at "No list" */ }
}

function closeCrmBulkModal() {
    if (crmBulk.submitting) return;
    document.getElementById('crmBulkModal').style.display = 'none';
}

async function submitCrmBulkAdd() {
    if (crmBulk.submitting) return;
    const bbls = Array.from(crmBulk.selected);
    if (!bbls.length) return;
    const withContacts = document.getElementById('crmBulkContacts').checked;
    const listId = document.getElementById('crmBulkList').value || null;
    const newListName = document.getElementById('crmBulkNewList').value.trim() || null;
    const go = document.getElementById('crmBulkGo');
    const progress = document.getElementById('crmBulkProgress');
    crmBulk.submitting = true;
    go.disabled = true;
    progress.hidden = false;

    // Chunked so a big selection can't hit a request timeout; the first
    // chunk may create the new list, later chunks reuse its id.
    const CHUNK = 25;
    let added = 0, existing = 0, failed = 0, done = 0;
    let effectiveListId = listId;
    let effectiveNewList = newListName;
    try {
        for (let i = 0; i < bbls.length; i += CHUNK) {
            const chunk = bbls.slice(i, i + CHUNK);
            progress.textContent = `Adding ${Math.min(i + chunk.length, bbls.length)} of ${bbls.length}…`;
            const res = await fetch('/crm/api/bulk-add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    bbls: chunk,
                    with_contacts: withContacts,
                    list_id: effectiveListId,
                    new_list_name: effectiveNewList
                })
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.error || 'Bulk add failed');
            added += data.added; existing += data.existing; failed += data.failed;
            done += chunk.length;
            Object.assign(crmStatus.inCrm, data.in_crm || {});
            if (data.list_id) { effectiveListId = data.list_id; effectiveNewList = null; }
        }
        const parts = [`${added} added`];
        if (existing) parts.push(`${existing} already in CRM`);
        if (failed) parts.push(`${failed} failed`);
        crmNotice(parts.join(' · '));
        clearBulkSelection();
        document.getElementById('crmBulkModal').style.display = 'none';
        refreshCrmStatus();
    } catch (e) {
        progress.textContent = (e && e.message) || 'Something went wrong — try again.';
        if (done) refreshCrmStatus();
    } finally {
        crmBulk.submitting = false;
        go.disabled = false;
        go.innerHTML = 'Add buildings';
    }
}

(function wireCrmButtons() {
    const bind = () => {
        initSavedSearches();
        wireGridInteractions();
        initQuickLook();
        const addBtn = document.getElementById('crmBulkAddBtn');
        if (addBtn) addBtn.addEventListener('click', openCrmBulkModal);
        const clearBtn = document.getElementById('crmBulkClearBtn');
        if (clearBtn) clearBtn.addEventListener('click', clearBulkSelection);
        const goBtn = document.getElementById('crmBulkGo');
        if (goBtn) goBtn.addEventListener('click', submitCrmBulkAdd);
        const closeBtn = document.getElementById('crmBulkClose');
        if (closeBtn) closeBtn.addEventListener('click', closeCrmBulkModal);
        const cancelBtn = document.getElementById('crmBulkCancel');
        if (cancelBtn) cancelBtn.addEventListener('click', closeCrmBulkModal);
        document.addEventListener('change', (e) => {
            if (!e.target.classList || !e.target.classList.contains('js-select-bbl')) return;
            const bbl = String(e.target.dataset.bbl);
            if (e.target.checked) crmBulk.selected.add(bbl);
            else crmBulk.selected.delete(bbl);
            updateBulkBar();
        });
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bind);
    } else {
        bind();
    }
})();

// Make functions globally available
window.viewProperty = viewProperty;
window.openPeek = openPeek;
window.closePeek = closePeek;
window.crmOpen = crmOpen;
window.viewOwnerPortfolio = viewOwnerPortfolio;
window.closePortfolioModal = closePortfolioModal;
window.closeExportModal = closeExportModal;
window.downloadExport = downloadExport;
window.goToPage = goToPage;
window.clearFilters = clearFilters;
window.togglePlay = togglePlay;
window.showBulkEnrichModal = showBulkEnrichModal;
window.closeBulkEnrichModal = closeBulkEnrichModal;
window.cancelBulkEnrichJob = cancelBulkEnrichJob;
window.startBulkEnrichJob = startBulkEnrichJob;
window.updateEnrichmentEstimate = updateEnrichmentEstimate;
