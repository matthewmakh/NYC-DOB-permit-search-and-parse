// ==========================================
// Properties Page JavaScript
// ==========================================

// State Management
const state = {
    properties: [],
    allStats: {},
    filters: {
        search: '',
        owner: '',
        minValue: null,
        maxValue: null,
        minSalePrice: null,
        maxSalePrice: null,
        saleDateFrom: null,
        saleDateTo: null,
        cashOnly: false,
        withPermits: false,
        minPermits: null,
        recentPermitDays: null,
        borough: null,
        buildingClass: '',
        minUnits: null,
        maxUnits: null,
        hasViolations: null,
        recentSaleDays: null,
        financingMin: null,
        financingMax: null,
        smartFilter: null,
        hasEnrichableOwner: false
    },
    sort: {
        by: 'sale_date',
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
    initializeEventListeners();
    loadStats();
    loadProperties();
});

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
        if (state.filters.minValue) params.append('min_value', state.filters.minValue);
        if (state.filters.maxValue) params.append('max_value', state.filters.maxValue);
        if (state.filters.minSalePrice) params.append('min_sale_price', state.filters.minSalePrice);
        if (state.filters.maxSalePrice) params.append('max_sale_price', state.filters.maxSalePrice);
        if (state.filters.saleDateFrom) params.append('sale_date_from', state.filters.saleDateFrom);
        if (state.filters.saleDateTo) params.append('sale_date_to', state.filters.saleDateTo);
        if (state.filters.cashOnly) params.append('cash_only', 'true');
        if (state.filters.withPermits) params.append('with_permits', 'true');
        if (state.filters.minPermits) params.append('min_permits', state.filters.minPermits);
        if (state.filters.recentPermitDays) params.append('recent_permit_days', state.filters.recentPermitDays);
        if (state.filters.borough) params.append('borough', state.filters.borough);
        if (state.filters.buildingClass) params.append('building_class', state.filters.buildingClass);
        if (state.filters.minUnits) params.append('min_units', state.filters.minUnits);
        if (state.filters.maxUnits) params.append('max_units', state.filters.maxUnits);
        if (state.filters.hasViolations !== null) params.append('has_violations', state.filters.hasViolations);
        if (state.filters.recentSaleDays) params.append('recent_sale_days', state.filters.recentSaleDays);
        if (state.filters.financingMin) params.append('financing_min', state.filters.financingMin);
        if (state.filters.financingMax) params.append('financing_max', state.filters.financingMax);
        if (state.filters.hasEnrichableOwner) params.append('has_enrichable_owner', 'true');
        
        // Add sort and pagination
        params.append('sort_by', state.sort.by);
        params.append('sort_order', state.sort.order);
        params.append('page', state.pagination.page);
        params.append('per_page', state.pagination.perPage);
        
        const response = await fetch(`/api/properties?${params}`);
        const data = await response.json();
        
        if (data.success) {
            state.properties = data.properties;
            state.pagination = data.pagination;
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
        const owner = property.current_owner_name || property.owner_name_rpad || property.owner_name_hpd || 'Unknown';
        const assessedValue = property.assessed_total_value || 0;
        const salePrice = property.sale_price || 0;
        const permitCount = property.permit_count || 0;
        const violationCount = property.hpd_violations_count || 0;
        
        return `
            <div class="property-card" onclick="viewProperty('${property.bbl}')">
                <div class="property-header">
                    <div>
                        <div class="property-address">${escapeHtml(property.address || 'Address N/A')}</div>
                        <div class="property-bbl">BBL: ${formatBBL(property.bbl)}</div>
                    </div>
                    ${assessedValue > 0 ? `
                        <div class="property-value-badge">
                            $${formatNumber(assessedValue)}
                        </div>
                    ` : ''}
                </div>
                
                <div class="property-owner" onclick="event.stopPropagation(); viewOwnerPortfolio('${escapeHtml(owner)}')">
                    <div class="owner-label">Owner</div>
                    <div class="owner-name">${escapeHtml(owner)}</div>
                </div>
                
                <div class="property-details">
                    ${property.units ? `
                        <div class="detail-item">
                            <div class="detail-label">Units</div>
                            <div class="detail-value">${property.units}</div>
                        </div>
                    ` : ''}
                    ${property.year_built ? `
                        <div class="detail-item">
                            <div class="detail-label">Year Built</div>
                            <div class="detail-value">${property.year_built}</div>
                        </div>
                    ` : ''}
                    ${salePrice > 0 ? `
                        <div class="detail-item">
                            <div class="detail-label">Last Sale</div>
                            <div class="detail-value">$${formatNumber(salePrice)}</div>
                        </div>
                    ` : ''}
                    ${property.sale_date ? `
                        <div class="detail-item">
                            <div class="detail-label">Sale Date</div>
                            <div class="detail-value">${formatDate(property.sale_date)}</div>
                        </div>
                    ` : ''}
                </div>
                
                <div class="property-badges">
                    ${property.is_cash_purchase ? '<span class="badge badge-cash">💵 Cash Purchase</span>' : ''}
                    ${property.acris_total_transactions > 0 ? '<span class="badge badge-acris">✓ ACRIS Data</span>' : ''}
                    ${permitCount > 0 ? `<span class="badge badge-permits">🏗️ ${permitCount} Permit${permitCount > 1 ? 's' : ''}</span>` : ''}
                    ${violationCount > 0 ? `<span class="badge badge-violations">⚠️ ${violationCount} Violation${violationCount > 1 ? 's' : ''}</span>` : ''}
                </div>
                
                <div class="property-actions">
                    <button class="btn-view" onclick="event.stopPropagation(); viewProperty('${property.bbl}')">
                        <i class="fas fa-eye"></i> View Details
                    </button>
                    <button class="btn-portfolio" onclick="event.stopPropagation(); viewOwnerPortfolio('${escapeHtml(owner)}')" title="View owner's portfolio">
                        <i class="fas fa-building"></i>
                    </button>
                </div>
            </div>
        `;
    }).join('');
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
    document.getElementById('minValue').addEventListener('change', (e) => {
        state.filters.minValue = e.target.value ? parseFloat(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('maxValue').addEventListener('change', (e) => {
        state.filters.maxValue = e.target.value ? parseFloat(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
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
    
    // Borough filter
    document.getElementById('boroughFilter').addEventListener('change', (e) => {
        state.filters.borough = e.target.value ? parseInt(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Building class
    let classTimeout;
    document.getElementById('buildingClass').addEventListener('input', (e) => {
        clearTimeout(classTimeout);
        classTimeout = setTimeout(() => {
            state.filters.buildingClass = e.target.value.trim();
            state.pagination.page = 1;
            loadProperties();
        }, 500);
    });
    
    // Unit range
    document.getElementById('minUnits').addEventListener('change', (e) => {
        state.filters.minUnits = e.target.value ? parseInt(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    document.getElementById('maxUnits').addEventListener('change', (e) => {
        state.filters.maxUnits = e.target.value ? parseInt(e.target.value) : null;
        state.pagination.page = 1;
        loadProperties();
    });
    
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
    
    // Recent permit days filter
    document.getElementById('recentPermitDays').addEventListener('change', (e) => {
        const customInput = document.getElementById('recentPermitCustomDays');
        if (e.target.value === 'custom') {
            customInput.style.display = 'block';
            customInput.focus();
            // Don't trigger search yet - wait for custom input
        } else {
            customInput.style.display = 'none';
            customInput.value = '';
            state.filters.recentPermitDays = e.target.value ? parseInt(e.target.value) : null;
            state.pagination.page = 1;
            loadProperties();
        }
    });
    
    document.getElementById('recentPermitCustomDays').addEventListener('change', (e) => {
        state.filters.recentPermitDays = e.target.value ? parseInt(e.target.value) : null;
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
    
    // Violations filter
    document.getElementById('violationsFilter').addEventListener('change', (e) => {
        const value = e.target.value;
        state.filters.hasViolations = value === '' ? null : value;
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
    document.getElementById('sortBy').addEventListener('change', (e) => {
        state.sort.by = e.target.value;
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
    
    // Build query params from current filters
    const params = new URLSearchParams();
    
    if (state.filters.search) params.append('search', state.filters.search);
    if (state.filters.owner) params.append('owner', state.filters.owner);
    if (state.filters.minValue) params.append('min_value', state.filters.minValue);
    if (state.filters.maxValue) params.append('max_value', state.filters.maxValue);
    if (state.filters.minSalePrice) params.append('min_sale_price', state.filters.minSalePrice);
    if (state.filters.maxSalePrice) params.append('max_sale_price', state.filters.maxSalePrice);
    if (state.filters.saleDateFrom) params.append('sale_date_from', state.filters.saleDateFrom);
    if (state.filters.saleDateTo) params.append('sale_date_to', state.filters.saleDateTo);
    if (state.filters.cashOnly) params.append('cash_only', 'true');
    if (state.filters.withPermits) params.append('with_permits', 'true');
    if (state.filters.minPermits) params.append('min_permits', state.filters.minPermits);
    if (state.filters.recentPermitDays) params.append('recent_permit_days', state.filters.recentPermitDays);
    if (state.filters.borough) params.append('borough', state.filters.borough);
    if (state.filters.buildingClass) params.append('building_class', state.filters.buildingClass);
    if (state.filters.minUnits) params.append('min_units', state.filters.minUnits);
    if (state.filters.maxUnits) params.append('max_units', state.filters.maxUnits);
    if (state.filters.hasViolations !== null) params.append('has_violations', state.filters.hasViolations);
    if (state.filters.recentSaleDays) params.append('recent_sale_days', state.filters.recentSaleDays);
    if (state.filters.financingMin) params.append('financing_min', state.filters.financingMin);
    if (state.filters.financingMax) params.append('financing_max', state.filters.financingMax);
    if (state.filters.hasEnrichableOwner) params.append('has_enrichable_owner', 'true');
    
    // Add sort
    params.append('sort_by', state.sort.by);
    params.append('sort_order', state.sort.order);
    
    // Add selected fields
    params.append('fields', fields.join(','));
    
    // Trigger download
    window.location.href = `/api/properties/export?${params}`;
    closeExportModal();
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
                    <div class="property-bbl">BBL: ${formatBBL(prop.bbl)}</div>
                </div>
                ${prop.assessed_total_value ? `
                    <div class="property-value-badge">
                        $${formatNumber(prop.assessed_total_value)}
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
                        <div class="detail-label">Purchase Price</div>
                        <div class="detail-value">$${formatNumber(prop.sale_price)}</div>
                    </div>
                ` : ''}
                ${prop.sale_date ? `
                    <div class="detail-item">
                        <div class="detail-label">Purchase Date</div>
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
        minValue: null,
        maxValue: null,
        minSalePrice: null,
        maxSalePrice: null,
        saleDateFrom: null,
        saleDateTo: null,
        cashOnly: false,
        withPermits: false,
        minPermits: null,
        recentPermitDays: null,
        borough: null,
        buildingClass: '',
        minUnits: null,
        maxUnits: null,
        hasViolations: null,
        recentSaleDays: null,
        financingMin: null,
        financingMax: null,
        smartFilter: null,
        hasEnrichableOwner: false
    };
}

function clearFilters() {
    resetFilters();
    
    // Clear all form inputs
    document.getElementById('universalSearch').value = '';
    document.getElementById('ownerSearch').value = '';
    document.getElementById('minValue').value = '';
    document.getElementById('maxValue').value = '';
    document.getElementById('minSalePrice').value = '';
    document.getElementById('maxSalePrice').value = '';
    document.getElementById('saleDateFrom').value = '';
    document.getElementById('saleDateTo').value = '';
    document.getElementById('boroughFilter').value = '';
    document.getElementById('buildingClass').value = '';
    document.getElementById('minUnits').value = '';
    document.getElementById('maxUnits').value = '';
    document.getElementById('minPermits').value = '';
    document.getElementById('recentPermitDays').value = '';
    document.getElementById('recentPermitCustomDays').value = '';
    document.getElementById('recentPermitCustomDays').style.display = 'none';
    document.getElementById('financingMin').value = '';
    document.getElementById('financingMax').value = '';
    document.getElementById('cashOnly').checked = false;
    document.getElementById('withPermits').checked = false;
    document.getElementById('violationsFilter').value = '';
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

// Make functions globally available
window.viewProperty = viewProperty;
window.viewOwnerPortfolio = viewOwnerPortfolio;
window.closePortfolioModal = closePortfolioModal;
window.closeExportModal = closeExportModal;
window.downloadExport = downloadExport;
window.goToPage = goToPage;
window.clearFilters = clearFilters;

console.log('🏢 Properties Intelligence loaded');
