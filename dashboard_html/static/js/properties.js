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
        permitType: null,  // Permit type filter
        propertyType: null,  // Residential/Commercial filter
        borough: [],
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
    checkResumableBulkEnrichJob();
});

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
            owner_strategy: active.owner_strategy,
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
        if (state.filters.permitType) params.append('permit_type', state.filters.permitType);
        if (state.filters.propertyType) params.append('property_type', state.filters.propertyType);
        if (state.filters.borough && state.filters.borough.length) params.append('borough', state.filters.borough.join(','));
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
        const contractorName = property.contractor_name || null;
        const contractorPhone = property.contractor_phone || null;
        
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
                
                ${contractorName || contractorPhone ? `
                    <div class="property-contractor">
                        <div class="contractor-label">📋 Permit Contact</div>
                        ${contractorName ? `<div class="contractor-name">${escapeHtml(contractorName)}</div>` : ''}
                        ${contractorPhone ? `<a href="tel:${contractorPhone}" class="contractor-phone" onclick="event.stopPropagation();">📞 ${contractorPhone}</a>` : ''}
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
    
    // Borough filter (multi-select via checkboxes)
    document.querySelectorAll('.borough-cb').forEach(cb => {
        cb.addEventListener('change', () => {
            const checked = Array.from(document.querySelectorAll('.borough-cb:checked'))
                .map(el => el.value);
            state.filters.borough = checked;
            state.pagination.page = 1;
            loadProperties();
        });
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
    
    // Permit type filter
    document.getElementById('permitType').addEventListener('change', (e) => {
        state.filters.permitType = e.target.value || null;
        state.pagination.page = 1;
        loadProperties();
    });
    
    // Property type filter (residential/commercial/mixed)
    document.getElementById('propertyType').addEventListener('change', (e) => {
        state.filters.propertyType = e.target.value || null;
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
    
    // Check if enrichment is requested
    const enrichContactsCheckbox = document.getElementById('exportEnrichContacts');
    const enrichContacts = enrichContactsCheckbox && enrichContactsCheckbox.checked;
    
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
    if (state.filters.borough && state.filters.borough.length) params.append('borough', state.filters.borough.join(','));
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
        const exportBtn = document.querySelector('.modal-footer .btn-primary');
        const originalText = exportBtn.textContent;
        exportBtn.textContent = 'Enriching & Exporting...';
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
            exportBtn.textContent = originalText;
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
        permitType: null,
        propertyType: null,
        borough: [],
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
    document.querySelectorAll('.borough-cb').forEach(cb => { cb.checked = false; });
    const propertyTypeEl = document.getElementById('propertyType');
    if (propertyTypeEl) propertyTypeEl.value = '';
    document.getElementById('buildingClass').value = '';
    document.getElementById('minUnits').value = '';
    document.getElementById('maxUnits').value = '';
    document.getElementById('minPermits').value = '';
    document.getElementById('permitType').value = '';
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
    if (f.minValue) payload.min_value = f.minValue;
    if (f.maxValue) payload.max_value = f.maxValue;
    if (f.minSalePrice) payload.min_sale_price = f.minSalePrice;
    if (f.maxSalePrice) payload.max_sale_price = f.maxSalePrice;
    if (f.saleDateFrom) payload.sale_date_from = f.saleDateFrom;
    if (f.saleDateTo) payload.sale_date_to = f.saleDateTo;
    if (f.cashOnly) payload.cash_only = true;
    if (f.withPermits) payload.with_permits = true;
    if (f.minPermits) payload.min_permits = f.minPermits;
    if (f.recentPermitDays) payload.recent_permit_days = f.recentPermitDays;
    if (f.borough && f.borough.length) payload.borough = f.borough;
    if (f.buildingClass) payload.building_class = f.buildingClass;
    if (f.minUnits) payload.min_units = f.minUnits;
    if (f.maxUnits) payload.max_units = f.maxUnits;
    if (f.hasViolations !== null && f.hasViolations !== undefined && f.hasViolations !== '') {
        payload.has_violations = f.hasViolations;
    }
    if (f.recentSaleDays) payload.recent_sale_days = f.recentSaleDays;
    if (f.financingMin) payload.financing_min = f.financingMin;
    if (f.financingMax) payload.financing_max = f.financingMax;
    if (f.hasEnrichableOwner) payload.has_enrichable_owner = true;
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
            <span class="modal-close" onclick="closeBulkEnrichModal()">&times;</span>
            <h2>📞 Bulk Owner Enrichment</h2>
            <p class="modal-subtitle">Enriches every property matching your current filters
                (<strong>${formatNumber(totalFiltered)}</strong> total, not just this page).</p>

            <div class="be-strategy">
                <h4>👥 Which owners to enrich per property</h4>
                <label>
                    <input type="radio" name="be-strategy" value="recommended" checked>
                    <strong>Recommended</strong> &mdash; 1 owner per property
                    (SOS principal if available, otherwise the primary listed owner)
                </label>
                <label>
                    <input type="radio" name="be-strategy" value="all">
                    <strong>All available owners</strong> &mdash; enriches every distinct
                    owner name found (SOS + PLUTO + RPAD + HPD). Higher cost.
                </label>
            </div>

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

    // Re-estimate whenever the strategy changes
    document.querySelectorAll('input[name="be-strategy"]').forEach(r => {
        r.addEventListener('change', refreshBulkEnrichEstimate);
    });
    refreshBulkEnrichEstimate();
}

async function refreshBulkEnrichEstimate() {
    const block = document.getElementById('be-estimate-block');
    const startBtn = document.getElementById('be-start-btn');
    if (!block || !startBtn) return;

    const strategy = (document.querySelector('input[name="be-strategy"]:checked') || {}).value || 'recommended';
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
            }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Failed to get estimate');

        window._beEstimate = data;

        const costDisplay = data.is_admin ? 'FREE (Admin)' : `$${data.max_cost.toFixed(2)}`;
        const costPerLookup = data.is_admin ? 'FREE' : `$${data.cost_per_lookup.toFixed(2)}`;

        block.innerHTML = `
            <div class="bulk-enrich-summary">
                <div class="summary-row">
                    <span class="summary-label">Properties matching filters:</span>
                    <span class="summary-value">${formatNumber(data.total_properties)}</span>
                </div>
                <div class="summary-row">
                    <span class="summary-label">Properties with enrichable owners:</span>
                    <span class="summary-value">${formatNumber(data.properties_with_owners)}</span>
                </div>
                <div class="summary-row highlight">
                    <span class="summary-label">Owners to enrich (${strategy}):</span>
                    <span class="summary-value">${formatNumber(data.total_owners)}</span>
                </div>
            </div>

            <div class="cost-breakdown">
                <h4>💰 Cost</h4>
                <div class="math-display">
                    <p>${formatNumber(data.total_owners)} owners × ${costPerLookup} = <strong>${costDisplay}</strong></p>
                </div>
                <p class="cost-note"><small>
                    You're only charged for lookups that return data. Batch pricing: $0.35 per lookup.
                    ${!data.is_admin && data.total_owners > 0 && data.total_owners < 2 ? 'Minimum charge of $0.50 applies.' : ''}
                </small></p>
            </div>

            ${data.requires_typed_confirmation ? `
                <div class="be-confirm-block">
                    ⚠️ This run will charge up to <strong>${costDisplay}</strong>.
                    Type <code>CONFIRM</code> to authorize.
                    <input type="text" id="be-confirm-input" class="be-confirm-input"
                           placeholder="Type CONFIRM" autocomplete="off">
                </div>
            ` : ''}
        `;

        if (data.total_owners === 0) {
            startBtn.textContent = 'No owners to enrich';
            startBtn.disabled = true;
            return;
        }

        startBtn.disabled = false;
        startBtn.textContent = `🚀 Enrich ${formatNumber(data.total_owners)} owners (up to ${costDisplay})`;
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
    const maxCost = initialData.estimated_max_cost || 0;
    const strategy = initialData.owner_strategy || 'recommended';

    content.innerHTML = `
        <span class="modal-close" onclick="closeBulkEnrichModal()">&times;</span>
        <h2>📞 Bulk Enrichment Running</h2>
        <p class="modal-subtitle">
            Job #${jobId} &middot; ${formatNumber(props)} properties &middot;
            ${formatNumber(planned)} owners (${strategy}) &middot;
            up to <strong>$${maxCost.toFixed(2)}</strong>
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
}

function finalizeBulkEnrichUI(job) {
    const cancelBtn = document.getElementById('be-cancel-btn');
    const closeBtn = document.getElementById('be-close-btn');
    const txt = document.getElementById('be-progress-text');
    const chargeMsg = document.getElementById('be-charge-msg');

    if (cancelBtn) cancelBtn.style.display = 'none';
    if (closeBtn) closeBtn.style.display = 'inline-block';

    let headline;
    if (job.status === 'completed') headline = `✅ Completed. Charged $${(job.total_charged || 0).toFixed(2)}.`;
    else if (job.status === 'cancelled') headline = `⏹ Cancelled. Charged $${(job.total_charged || 0).toFixed(2)} for ${formatNumber(job.owners_successful || 0)} completed lookups.`;
    else headline = `❌ Failed: ${job.error_message || 'unknown error'}`;

    if (txt) txt.textContent = headline;
    if (chargeMsg && job.error_message) {
        chargeMsg.innerHTML = `<small style="color: #ff9800;">${job.error_message}</small>`;
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
    estimateDiv.innerHTML = '<div class="estimate-loading">⏳ Calculating enrichment cost...</div>';
    
    try {
        // Build query params from current filters
        const params = new URLSearchParams();
        
        if (state.filters.search) params.append('search', state.filters.search);
        if (state.filters.owner) params.append('owner', state.filters.owner);
        if (state.filters.minValue) params.append('min_value', state.filters.minValue);
        if (state.filters.maxValue) params.append('max_value', state.filters.maxValue);
        if (state.filters.borough && state.filters.borough.length) params.append('borough', state.filters.borough.join(','));
        if (state.filters.minUnits) params.append('min_units', state.filters.minUnits);
        if (state.filters.maxUnits) params.append('max_units', state.filters.maxUnits);
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
                    <span>Properties to export:</span>
                    <strong>${formatNumber(data.total_properties)}</strong>
                </div>
                <div class="estimate-row">
                    <span>Properties with contacts:</span>
                    <strong>${formatNumber(data.properties_with_contacts)}</strong>
                </div>
                <div class="estimate-row">
                    <span>Total enrichable contacts:</span>
                    <strong>${formatNumber(data.total_contacts)}</strong>
                </div>
                <div class="estimate-row">
                    <span style="color: #27ae60;">✓ Already unlocked (free):</span>
                    <strong>${formatNumber(data.already_unlocked)}</strong>
                </div>
                <div class="estimate-row" style="color: #e67e22;">
                    <span>⚡ New enrichments (charged):</span>
                    <strong>${formatNumber(data.need_enrichment)}</strong>
                </div>
                <div class="estimate-row estimate-total">
                    <span>${isAdmin ? 'Admin - Free:' : 'Total cost:'}:</span>
                    <strong>${isAdmin ? 'FREE' : '$' + cost.toFixed(2)}</strong>
                </div>
                ${!isAdmin && data.need_enrichment > 0 ? `
                    <p class="estimate-note">
                        <small>💡 $${data.cost_per_contact.toFixed(2)} per new contact (${data.need_enrichment} × $${data.cost_per_contact.toFixed(2)} = $${cost.toFixed(2)})</small>
                    </p>
                ` : ''}
                ${isAdmin ? '<p class="estimate-note"><small>🔑 Admin access - all enrichments are free</small></p>' : ''}
            </div>
        `;
    } catch (error) {
        console.error('Enrichment estimate error:', error);
        estimateDiv.innerHTML = '<div class="estimate-error">❌ Failed to calculate estimate: ' + error.message + '</div>';
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
window.showBulkEnrichModal = showBulkEnrichModal;
window.closeBulkEnrichModal = closeBulkEnrichModal;
window.cancelBulkEnrichJob = cancelBulkEnrichJob;
window.startBulkEnrichJob = startBulkEnrichJob;
window.updateEnrichmentEstimate = updateEnrichmentEstimate;

console.log('🏢 Properties Intelligence loaded');
