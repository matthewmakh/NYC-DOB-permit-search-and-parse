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
        hasEnrichableOwner: false,
        play: null
    },
    // Borough, property type, building class, units, value, kind of work,
    // recency and violations live here — the same set, read the same way, as
    // on the contractors page. See static/js/filters_common.js.
    shared: {},
    plays: [],
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

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    MultiSelect.init();
    state.shared = SharedFilters.read();
    const initialParams = new URLSearchParams(window.location.search);
    const initialOwner = initialParams.get('owner') || '';
    const initialSearch = initialParams.get('q') || '';
    if (initialOwner) {
        state.filters.owner = initialOwner;
        document.getElementById('ownerSearch').value = initialOwner;
    }
    if (initialSearch) {
        state.filters.search = initialSearch;
        document.getElementById('universalSearch').value = initialSearch;
    }
    SharedFilters.loadFacets();
    initializeEventListeners();
    loadPlays();
    loadStats();
    loadProperties();
    checkResumableBulkEnrichJob();
});

// ==========================================
// PREBUILT PLAYS
// ==========================================

async function loadPlays() {
    try {
        const res = await fetch('/api/properties/plays');
        const data = await res.json();
        if (!data.success || !data.plays || !data.plays.length) return;
        state.plays = data.plays;
        renderPlayCards();
        document.getElementById('playsSection').style.display = 'block';
    } catch (e) {
        console.warn('Plays unavailable:', e);
    }
}

function renderPlayCards() {
    const row = document.getElementById('playsRow');
    const groups = [
        {id: 'property_intel', label: 'Property intelligence', plays: state.plays.filter(p => p.family !== 'smart_installers')},
        {id: 'smart_installers', label: 'Smart Installers sales plays', plays: state.plays.filter(p => p.family === 'smart_installers')}
    ].filter(group => group.plays.length);
    const card = play => `
        <button class="play-card ${state.filters.play === play.id ? 'active' : ''}"
                onclick="togglePlay('${play.id}')">
            <div class="play-card-top">
                <span class="play-name">${escapeHtml(play.name)}</span>
                <span class="play-count">${formatNumber(play.count)}</span>
            </div>
            <div class="play-desc">${escapeHtml(play.description)}</div>
            <span class="play-audience play-audience-${play.audience}">${
                play.audience === 'both' ? 'investors + contractors' : play.audience}</span>
        </button>
    `;
    row.innerHTML = groups.map(group => `
        <div class="play-family play-family-${group.id}">
            <div class="play-family-title">${escapeHtml(group.label)}</div>
            <div class="play-family-grid">${group.plays.map(card).join('')}</div>
        </div>
    `).join('');
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
const EXTRA_SORT_LABELS = { unused_far: 'Unused FAR', co_date: 'CO date' };

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
        // Build query string from filters
        const params = new URLSearchParams();
        
        // Add all active filters
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
        if (state.filters.hasEnrichableOwner) params.append('has_enrichable_owner', 'true');
        if (state.filters.play) params.append('play', state.filters.play);

        // Add sort and pagination
        appendMulti(params, 'sort_by', state.sort.by);
        params.append('sort_order', state.sort.order);
        params.append('page', state.pagination.page);
        params.append('per_page', state.pagination.perPage);
        
        const response = await fetch(`/api/properties?${params}`);
        const data = await response.json();
        
        if (data.success) {
            state.properties = data.properties;
            state.pagination = normalizePagination(data.pagination);
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
    }
}

// ==========================================
// RENDERING
// ==========================================

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
        const owner = property.sale_buyer_primary || property.current_owner_name ||
            property.owner_name_hpd || property.owner_name_rpad || 'Unknown';
        const assessedValue = property.assessed_total_value || 0;
        const salePrice = property.sale_price || 0;
        const permitCount = property.permit_count || 0;
        const violationCount = property.hpd_violations_count || 0;
        const contractorName = property.contractor_name || null;
        const contractorPhone = property.contractor_phone || null;
        
        return `
            <div class="property-card" onclick="viewProperty('${property.bbl}')">
                <div class="property-header">
                    <div>
                        <div class="property-address">${escapeHtml(property.address || 'Address N/A')}</div>
                        <div class="property-bbl">${formatBBL(property.bbl)}</div>
                    </div>
                    ${assessedValue > 0 ? `
                        <div class="property-value">
                            <div class="property-value-amount">$${formatNumber(assessedValue)}</div>
                            <div class="property-value-label">Assessed</div>
                        </div>
                    ` : ''}
                </div>

                <div class="property-owner" onclick="event.stopPropagation(); viewOwnerPortfolio('${escapeHtml(owner)}')">
                    <div class="owner-label">Owner</div>
                    <div class="owner-name">${escapeHtml(owner)}</div>
                </div>

                ${contractorName || contractorPhone ? `
                    <div class="property-contractor">
                        <div class="contractor-label">Permit contact</div>
                        ${contractorName ? `<div class="contractor-name">${escapeHtml(contractorName)}</div>` : ''}
                        ${contractorPhone ? `<a href="tel:${contractorPhone}" class="contractor-phone" onclick="event.stopPropagation();">${contractorPhone}</a>` : ''}
                    </div>
                ` : ''}

                <div class="property-details">
                    ${property.units ? `
                        <div class="detail-item">
                            <div class="detail-label">Units</div>
                            <div class="detail-value">${property.units}</div>
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
                    ${property.sale_date ? `
                        <div class="detail-item">
                            <div class="detail-label">Sale date</div>
                            <div class="detail-value">${formatDate(property.sale_date)}</div>
                        </div>
                    ` : ''}
                </div>

                <div class="property-badges">
                    ${property.is_cash_purchase ? '<span class="badge badge-cash">Cash purchase</span>' : ''}
                    ${property.acris_total_transactions > 0 ? '<span class="badge badge-acris">ACRIS</span>' : ''}
                    ${permitCount > 0 ? `<span class="badge badge-permits">${permitCount} permit${permitCount > 1 ? 's' : ''}</span>` : ''}
                    ${violationCount > 0 ? `<span class="badge badge-violations">${violationCount} violation${violationCount > 1 ? 's' : ''}</span>` : ''}
                    ${signalBadges(property)}
                </div>

                <div class="property-actions">
                    <button class="btn-view" onclick="event.stopPropagation(); viewProperty('${property.bbl}')">
                        View details
                    </button>
                    <button class="btn-portfolio" onclick="event.stopPropagation(); viewOwnerPortfolio('${escapeHtml(owner)}')" title="View owner's portfolio">
                        <i class="fas fa-building"></i>
                    </button>
                </div>
            </div>
        `;
    }).join('');
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
        state.filters.financingMin = e.target.value ? parseFloat(e.target.value) / 100 : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('financingMax').addEventListener('change', (e) => {
        state.filters.financingMax = e.target.value ? parseFloat(e.target.value) / 100 : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Cash only checkbox
    document.getElementById('cashOnly').addEventListener('change', (e) => {
        state.filters.cashOnly = e.target.checked;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Enrichable owner checkbox
    document.getElementById('hasEnrichableOwner').addEventListener('change', (e) => {
        state.filters.hasEnrichableOwner = e.target.checked;
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
    if (state.filters.hasEnrichableOwner) params.append('has_enrichable_owner', 'true');
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
    window.location.href = `/property/${bbl}`;
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
        <div class="property-card" onclick="viewProperty('${prop.bbl}')">
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
        hasEnrichableOwner: false,
        play: null
    };
}

function clearFilters() {
    resetFilters();
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
    document.getElementById('hasEnrichableOwner').checked = false;
    
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
    if (f.hasEnrichableOwner) payload.has_enrichable_owner = true;
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

// Make functions globally available
window.viewProperty = viewProperty;
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
