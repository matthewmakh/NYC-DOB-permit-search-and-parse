// Global state
const state = {
    currentPage: 0,
    resultsPerPage: 100,
    permits: [],
    filteredPermits: [],
    buildings: [],
    stats: {},
    filters: {
        dateFilter: 'all',
        dateFrom: null,
        dateTo: null,
        smartFilter: 'all',
        hasContacts: true,
        mobileOnly: false,
        minContacts: 0,
        minScore: 0,
        permitStatus: ['Active', 'Expired', 'Expiring Soon'],
        permitType: 'all',
        unitsRange: 500,
        storiesRange: 100,
        contactSearch: '',
        globalSearch: ''
    },
    charts: {},
    mapInstance: null
};

// API Base URL
const API_BASE = '/api';

// Initialize app
document.addEventListener('DOMContentLoaded', () => {
    initializeEventListeners();
    loadInitialData();
    updateLastUpdated();
});

// Event Listeners
function initializeEventListeners() {
    // Date filter
    document.getElementById('dateFilter').addEventListener('change', handleDateFilterChange);

    // Smart filters
    document.querySelectorAll('.smart-filter-btn').forEach(btn => {
        btn.addEventListener('click', (e) => handleSmartFilter(e.target.dataset.filter));
    });

    // Contact search
    const contactSearch = document.getElementById('contactSearch');
    contactSearch.addEventListener('input', debounce(handleContactSearch, 500));

    // Sliders
    document.getElementById('minContacts').addEventListener('input', (e) => {
        document.getElementById('minContactsValue').textContent = e.target.value;
        state.filters.minContacts = parseInt(e.target.value);
        applyFilters();
    });

    document.getElementById('minScore').addEventListener('input', (e) => {
        document.getElementById('minScoreValue').textContent = e.target.value;
        state.filters.minScore = parseInt(e.target.value);
        applyFilters();
    });

    document.getElementById('unitsRange').addEventListener('input', (e) => {
        const value = e.target.value;
        document.getElementById('unitsRangeValue').textContent = value == 500 ? 'All' : `0-${value}`;
        state.filters.unitsRange = parseInt(value);
        applyFilters();
    });

    document.getElementById('storiesRange').addEventListener('input', (e) => {
        const value = e.target.value;
        document.getElementById('storiesRangeValue').textContent = value == 100 ? 'All' : `0-${value}`;
        state.filters.storiesRange = parseInt(value);
        applyFilters();
    });

    // Checkboxes
    document.getElementById('hasContacts').addEventListener('change', (e) => {
        state.filters.hasContacts = e.target.checked;
        applyFilters();
    });

    document.getElementById('mobileOnly').addEventListener('change', (e) => {
        state.filters.mobileOnly = e.target.checked;
        applyFilters();
    });

    // Permit status checkboxes
    document.querySelectorAll('input[name="permitStatus"]').forEach(cb => {
        cb.addEventListener('change', () => {
            state.filters.permitStatus = Array.from(
                document.querySelectorAll('input[name="permitStatus"]:checked')
            ).map(el => el.value);
            applyFilters();
        });
    });

    // Permit type
    document.getElementById('permitType').addEventListener('change', (e) => {
        state.filters.permitType = e.target.value;
        applyFilters();
    });

    // Results per page
    document.getElementById('resultsPerPage').addEventListener('change', (e) => {
        state.resultsPerPage = parseInt(e.target.value);
        state.currentPage = 0;
        renderLeads();
    });

    // Global search
    document.getElementById('globalSearch').addEventListener('input', debounce((e) => {
        state.filters.globalSearch = e.target.value.toLowerCase();
        applyFilters();
    }, 300));

    // Action buttons
    document.getElementById('refreshBtn').addEventListener('click', () => {
        loadInitialData();
    });

    document.getElementById('clearBtn').addEventListener('click', () => {
        clearFilters();
    });

    // Tabs
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const tab = e.target.dataset.tab;
            switchTab(tab);
        });
    });

    // Map load button
    document.getElementById('loadMapBtn')?.addEventListener('click', loadMap);
}

// Load initial data
async function loadInitialData() {
    showLoading(true);
    try {
        const response = await fetch(`${API_BASE}/permits`);
        const data = await response.json();
        
        state.permits = data.permits || [];
        state.filteredPermits = [...state.permits];
        
        // Load permit types for dropdown
        const types = [...new Set(state.permits.map(p => p.job_type))].sort();
        const permitTypeSelect = document.getElementById('permitType');
        permitTypeSelect.innerHTML = '<option value="all">All</option>';
        types.forEach(type => {
            const option = document.createElement('option');
            option.value = type;
            option.textContent = type;
            permitTypeSelect.appendChild(option);
        });
        
        applyFilters();
        updateStats();
        showLoading(false);
    } catch (error) {
        console.error('Error loading data:', error);
        showLoading(false);
        alert('Error loading permit data. Please try again.');
    }
}

// Apply all filters
function applyFilters() {
    let filtered = [...state.permits];
    
    // Smart filter - Apply first as it's a primary filter
    filtered = applySmartFilter(filtered, state.filters.smartFilter);
    
    // Date filter
    if (state.filters.dateFrom && state.filters.dateTo) {
        filtered = filtered.filter(p => {
            const issueDate = new Date(p.issue_date);
            return issueDate >= state.filters.dateFrom && issueDate <= state.filters.dateTo;
        });
    }
    
    // Has contacts
    if (state.filters.hasContacts) {
        filtered = filtered.filter(p => p.contact_count > 0);
    }
    
    // Mobile only
    if (state.filters.mobileOnly) {
        filtered = filtered.filter(p => p.has_mobile);
    }
    
    // Min contacts
    if (state.filters.minContacts > 0) {
        filtered = filtered.filter(p => p.contact_count >= state.filters.minContacts);
    }
    
    // Min score
    if (state.filters.minScore > 0) {
        filtered = filtered.filter(p => p.lead_score >= state.filters.minScore);
    }
    
    // Permit status
    if (state.filters.permitStatus.length > 0) {
        filtered = filtered.filter(p => {
            const status = getPermitStatus(p.exp_date);
            return state.filters.permitStatus.includes(status);
        });
    }
    
    // Permit type
    if (state.filters.permitType !== 'all') {
        filtered = filtered.filter(p => p.job_type === state.filters.permitType);
    }
    
    // Units range
    if (state.filters.unitsRange < 500) {
        filtered = filtered.filter(p => {
            const units = parseInt(p.total_units) || 0;
            return units <= state.filters.unitsRange;
        });
    }
    
    // Stories range
    if (state.filters.storiesRange < 100) {
        filtered = filtered.filter(p => {
            const stories = parseInt(p.stories) || 0;
            return stories <= state.filters.storiesRange;
        });
    }
    
    // Global search
    if (state.filters.globalSearch) {
        const search = state.filters.globalSearch.replace(/\s+/g, ' ');
        filtered = filtered.filter(p => {
            const address = p.address?.toLowerCase().replace(/\s+/g, ' ') || '';
            const applicant = p.applicant?.toLowerCase().replace(/\s+/g, ' ') || '';
            const permitNo = p.permit_no?.toLowerCase().replace(/\s+/g, ' ') || '';
            
            return address.includes(search) || 
                   applicant.includes(search) || 
                   permitNo.includes(search);
        });
    }
    
    state.filteredPermits = filtered;
    state.currentPage = 0;
    renderLeads();
    updateStats();
}

// Render leads
function renderLeads() {
    const container = document.getElementById('leadsContainer');
    const start = state.currentPage * state.resultsPerPage;
    const end = start + state.resultsPerPage;
    const pagePermits = state.filteredPermits.slice(start, end);
    
    if (pagePermits.length === 0) {
        container.innerHTML = '<div class="no-results"><h2>No leads found matching your filters</h2></div>';
        document.getElementById('resultsCount').textContent = '0';
        renderPagination();
        return;
    }
    
    container.innerHTML = pagePermits.map(permit => createLeadCard(permit)).join('');
    document.getElementById('resultsCount').textContent = state.filteredPermits.length;
    
    // Add click handlers to expand cards
    document.querySelectorAll('.lead-card-header').forEach(header => {
        header.addEventListener('click', () => {
            header.parentElement.classList.toggle('expanded');
        });
    });
    
    renderPagination();
}

// Create lead card HTML
function createLeadCard(permit) {
    const quality = getLeadQuality(permit.lead_score);
    const status = getPermitStatus(permit.exp_date);
    const contacts = parseContacts(permit);
    const estimatedValue = estimateProjectCost(permit);
    
    return `
        <div class="lead-card">
            <div class="lead-card-header">
                <div class="lead-card-title-row">
                    <div style="flex: 1;">
                        <span class="lead-badge ${quality.class}">${quality.icon} ${quality.label}</span>
                        <h3>${permit.address || 'No Address'}</h3>
                        <p style="color: var(--text-muted); font-size: 0.875rem;">
                            <i class="fas fa-chart-line"></i> Score: <span style="color: var(--primary-color); font-weight: 600;">${permit.lead_score}</span> 
                            | <i class="fas fa-users"></i> ${permit.contact_count} Contact${permit.contact_count !== 1 ? 's' : ''}
                            ${permit.has_mobile ? ' | <i class="fas fa-mobile-alt" style="color: var(--success-color);"></i> Mobile' : ''}
                        </p>
                    </div>
                    <i class="fas fa-chevron-down lead-card-chevron"></i>
                </div>
                
                <div class="lead-preview-stats">
                    <div class="preview-stat">
                        <i class="fas fa-hammer"></i>
                        <div class="preview-stat-content">
                            <div class="preview-stat-label">Job Type</div>
                            <div class="preview-stat-value">${permit.job_type || 'N/A'}</div>
                        </div>
                    </div>
                    <div class="preview-stat">
                        <i class="fas fa-building"></i>
                        <div class="preview-stat-content">
                            <div class="preview-stat-label">Units/Stories</div>
                            <div class="preview-stat-value">${permit.total_units || 0} / ${permit.stories || 0}</div>
                        </div>
                    </div>
                    <div class="preview-stat">
                        <i class="fas fa-calendar-alt"></i>
                        <div class="preview-stat-content">
                            <div class="preview-stat-label">Status</div>
                            <div class="preview-stat-value">
                                <span class="status-badge ${status.toLowerCase().replace(/\s+/g, '-')}">${status}</span>
                            </div>
                        </div>
                    </div>
                    <div class="preview-stat">
                        <i class="fas fa-dollar-sign"></i>
                        <div class="preview-stat-content">
                            <div class="preview-stat-label">Est. Value</div>
                            <div class="preview-stat-value">$${(estimatedValue / 1000).toFixed(0)}K</div>
                        </div>
                    </div>
                    <div class="preview-stat">
                        <i class="fas fa-briefcase"></i>
                        <div class="preview-stat-content">
                            <div class="preview-stat-label">Applicant</div>
                            <div class="preview-stat-value" style="font-size: 0.8rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${permit.applicant || 'N/A'}</div>
                        </div>
                    </div>
                    <div class="preview-stat">
                        <i class="fas fa-file-alt"></i>
                        <div class="preview-stat-content">
                            <div class="preview-stat-label">Permit #</div>
                            <div class="preview-stat-value" style="font-size: 0.8rem;">${permit.permit_no || 'N/A'}</div>
                        </div>
                    </div>
                </div>
            </div>
            <div class="lead-card-body">
                <div class="lead-info-grid">
                    <div class="info-section">
                        <h4>🏗️ Property Information</h4>
                        ${createInfoItem('Permit #', permit.permit_no, 'fa-file-alt')}
                        ${createInfoItem('Job Type', permit.job_type, 'fa-hammer')}
                        ${createInfoItem('Issue Date', formatDate(permit.issue_date), 'fa-calendar-plus')}
                        ${createInfoItem('Exp Date', formatDate(permit.exp_date), 'fa-calendar-times')}
                        ${createInfoItem('Status', `<span class="status-badge ${status.toLowerCase().replace(/\s+/g, '-')}">${status}</span>`, 'fa-info-circle')}
                        ${createInfoItem('Total Units', permit.total_units || 'N/A', 'fa-building')}
                        ${createInfoItem('Stories', permit.stories || 'N/A', 'fa-layer-group')}
                        ${createInfoItem('Use Type', permit.use_type || 'N/A', 'fa-tag')}
                        ${createInfoItem('Applicant', permit.applicant || 'N/A', 'fa-briefcase')}
                    </div>
                    <div class="info-section">
                        <h4>👥 Contacts (${permit.contact_count})</h4>
                        ${contacts.length > 0 ? contacts.map((c, idx) => `
                            <div class="contact-card" style="animation-delay: ${idx * 0.05}s;">
                                <strong><i class="fas fa-user-circle"></i> ${escapeHtml(c.name)}</strong>
                                <a href="tel:${c.phone}"><i class="fas fa-phone-alt"></i> ${escapeHtml(c.phone)}</a>
                            </div>
                        `).join('') : '<p style="color: var(--text-muted); text-align: center; padding: 2rem;">No contacts available</p>'}
                        ${permit.link ? `
                            <div style="margin-top: 1rem;">
                                <a href="${permit.link}" target="_blank" class="btn btn-primary" style="width: 100%; justify-content: center; display: flex; align-items: center; gap: 0.5rem;">
                                    <i class="fas fa-external-link-alt"></i> View Official Permit Details
                                </a>
                            </div>
                        ` : ''}
                    </div>
                </div>
                
                <div class="smart-insights">
                    <div class="insights-header">
                        <h4>💡 Smart Insights & Analytics</h4>
                    </div>
                    <div class="insights-content">
                        ${createSmartInsights(permit)}
                    </div>
                </div>
            </div>
        </div>
    `;
}

// Helper functions
function createInfoItem(label, value, icon = '') {
    const iconHtml = icon ? `<i class="fas ${icon}"></i>` : '';
    return `<div class="info-item"><strong>${iconHtml} ${label}:</strong> <span>${value || 'N/A'}</span></div>`;
}

function createSmartInsights(permit) {
    const insights = [];
    const estimatedValue = estimateProjectCost(permit);
    const quality = getLeadQuality(permit.lead_score);
    const status = getPermitStatus(permit.exp_date);
    
    // Building Intelligence - Show if available
    if (permit.current_owner_name || permit.year_built || permit.residential_units) {
        insights.push(`
            <div class="insight-item" style="border-left-color: var(--primary-color); background: var(--primary-light);">
                <strong>🏢 Building Intelligence</strong>
                ${permit.current_owner_name ? `<p><strong>Owner:</strong> ${permit.current_owner_name}</p>` : ''}
                <div class="building-metrics-inline">
                    ${permit.year_built ? `<div class="building-metric-chip"><span class="icon">📅</span><span class="value">${permit.year_built}</span></div>` : ''}
                    ${permit.residential_units ? `<div class="building-metric-chip"><span class="icon">🏠</span><span class="value">${permit.residential_units} units</span></div>` : ''}
                    ${permit.num_floors ? `<div class="building-metric-chip"><span class="icon">📏</span><span class="value">${permit.num_floors} floors</span></div>` : ''}
                    ${permit.building_sqft ? `<div class="building-metric-chip"><span class="icon">📐</span><span class="value">${parseInt(permit.building_sqft).toLocaleString()} ft²</span></div>` : ''}
                </div>
                ${permit.building_class ? `<small>Building Class: ${permit.building_class}</small>` : ''}
            </div>
        `);
    }
    
    // Property Value Insight
    if (permit.purchase_price || estimatedValue) {
        const purchasePrice = permit.purchase_price ? parseFloat(permit.purchase_price) : null;
        insights.push(`
            <div class="insight-item">
                <strong>💰 Property Value</strong>
                ${purchasePrice ? `<p>Last Sale: $${purchasePrice.toLocaleString()}</p>` : ''}
                ${permit.purchase_date ? `<small>Purchased: ${new Date(permit.purchase_date).toLocaleDateString()}</small><br>` : ''}
                <p>Est. Project Value: $${estimatedValue.toLocaleString()}</p>
                <small>Based on job type (${permit.job_type}), ${permit.total_units || 0} units, and ${permit.stories || 0} stories</small>
            </div>
        `);
    } else {
        insights.push(`
            <div class="insight-item">
                <strong>💰 Estimated Project Value</strong>
                <p>$${estimatedValue.toLocaleString()}</p>
                <small>Based on job type (${permit.job_type}), ${permit.total_units || 0} units, and ${permit.stories || 0} stories</small>
            </div>
        `);
    }
    
    // Lead Priority
    insights.push(`
        <div class="insight-item" style="border-left-color: ${quality.class === 'hot' ? 'var(--danger-color)' : quality.class === 'warm' ? 'var(--warning-color)' : 'var(--info-color)'};">
            <strong>${quality.icon} Lead Priority: ${quality.label}</strong>
            <p>${permit.lead_score}/100</p>
            <small>${getLeadPriorityMessage(permit.lead_score, permit.contact_count, permit.has_mobile)}</small>
        </div>
    `);
    
    // Permit Timeline
    if (permit.exp_date) {
        const expDate = new Date(permit.exp_date);
        const now = new Date();
        const daysRemaining = Math.ceil((expDate - now) / (1000 * 60 * 60 * 24));
        const timelineColor = daysRemaining < 0 ? 'var(--danger-color)' : daysRemaining <= 30 ? 'var(--warning-color)' : 'var(--success-color)';
        
        insights.push(`
            <div class="insight-item" style="border-left-color: ${timelineColor};">
                <strong>⏱️ Permit Timeline</strong>
                <p>${Math.abs(daysRemaining)} Days</p>
                <small>${daysRemaining < 0 ? 'Expired' : daysRemaining <= 30 ? 'Expiring soon - urgent follow-up recommended' : 'Active permit with time remaining'}</small>
            </div>
        `);
    }
    
    // Contact Quality
    if (permit.contact_count > 0) {
        insights.push(`
            <div class="insight-item" style="border-left-color: ${permit.has_mobile ? 'var(--success-color)' : 'var(--info-color)'};">
                <strong>📞 Contact Quality</strong>
                <p>${permit.contact_count} Contact${permit.contact_count !== 1 ? 's' : ''}</p>
                <small>${permit.has_mobile ? '✓ Mobile number available - higher conversion rate expected' : 'Landline numbers only - may require multiple attempts'}</small>
            </div>
        `);
    }
    
    return insights.join('');
}

function getLeadPriorityMessage(score, contactCount, hasMobile) {
    if (score >= 70) {
        return `High priority lead with ${contactCount} contact(s)${hasMobile ? ' and mobile number' : ''}. Recommend immediate follow-up.`;
    } else if (score >= 50) {
        return `Moderate priority lead. Good potential with ${contactCount} contact(s). Follow up within 24-48 hours.`;
    } else {
        return `Lower priority lead. Consider follow-up after addressing higher priority leads.`;
    }
}

function estimateProjectCost(permit) {
    const baseCosts = {
        'NB': 200000,
        'AL': 50000,
        'DM': 30000,
        'A1': 75000
    };
    
    const base = baseCosts[permit.job_type] || 50000;
    const units = parseInt(permit.total_units) || 0;
    const stories = parseInt(permit.stories) || 0;
    
    if (units > 0) {
        return Math.min(Math.max(base * units, 25000), 50000000);
    } else if (stories > 0) {
        return Math.min(Math.max(base * stories * 2, 25000), 50000000);
    }
    
    return base;
}

function getLeadQuality(score) {
    if (score >= 70) return { icon: '🔥', label: 'Hot', class: 'hot' };
    if (score >= 50) return { icon: '⚡', label: 'Warm', class: 'warm' };
    return { icon: '💡', label: 'Cold', class: 'cold' };
}

function getPermitStatus(expDate) {
    if (!expDate) return 'Unknown';
    const exp = new Date(expDate);
    const now = new Date();
    const daysDiff = Math.ceil((exp - now) / (1000 * 60 * 60 * 24));
    
    if (daysDiff < 0) return 'Expired';
    if (daysDiff <= 30) return 'Expiring Soon';
    return 'Active';
}

function parseContacts(permit) {
    const contacts = [];
    if (!permit.contact_names) return contacts;
    
    const names = permit.contact_names.split('|').map(n => n.trim());
    const phones = permit.contact_phones ? permit.contact_phones.split('|').map(p => p.trim()) : [];
    
    names.forEach((name, i) => {
        if (name) {
            contacts.push({
                name: name,
                phone: phones[i] || 'N/A'
            });
        }
    });
    
    return contacts;
}

function formatDate(dateStr) {
    if (!dateStr) return 'N/A';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Pagination
function renderPagination() {
    const totalPages = Math.ceil(state.filteredPermits.length / state.resultsPerPage);
    const pagination = document.getElementById('pagination');
    
    pagination.innerHTML = `
        <button ${state.currentPage === 0 ? 'disabled' : ''} onclick="goToPage(0)">⏮️ First</button>
        <button ${state.currentPage === 0 ? 'disabled' : ''} onclick="goToPage(${state.currentPage - 1})">◀️ Previous</button>
        <span class="page-info">Page ${state.currentPage + 1} of ${totalPages || 1}</span>
        <button ${state.currentPage >= totalPages - 1 ? 'disabled' : ''} onclick="goToPage(${state.currentPage + 1})">Next ▶️</button>
        <button ${state.currentPage >= totalPages - 1 ? 'disabled' : ''} onclick="goToPage(${totalPages - 1})">Last ⏭️</button>
    `;
}

function goToPage(page) {
    state.currentPage = page;
    renderLeads();
    window.scrollTo(0, 0);
}

// Update stats
function updateStats() {
    const permits = state.filteredPermits;
    document.getElementById('totalLeads').textContent = permits.length;
    document.getElementById('totalContacts').textContent = permits.reduce((sum, p) => sum + (p.contact_count || 0), 0);
    
    const avgScore = permits.length > 0 
        ? Math.round(permits.reduce((sum, p) => sum + (p.lead_score || 0), 0) / permits.length)
        : 0;
    document.getElementById('avgScore').textContent = avgScore;
    
    const hotLeads = permits.filter(p => p.lead_score >= 70).length;
    document.getElementById('hotLeads').textContent = hotLeads;
    
    // Update smart filter counts
    updateSmartFilterCounts();
    
    const mobileCount = permits.filter(p => p.has_mobile).length;
    document.getElementById('mobileCount').textContent = mobileCount;
}

// Handle date filter change
function handleDateFilterChange(e) {
    const value = e.target.value;
    state.filters.dateFilter = value;
    
    const customRange = document.getElementById('customDateRange');
    if (value === 'custom') {
        customRange.style.display = 'block';
        return;
    } else {
        customRange.style.display = 'none';
    }
    
    const now = new Date();
    let from = new Date();
    
    switch(value) {
        case '24h':
            from.setDate(now.getDate() - 1);
            break;
        case 'week':
            from.setDate(now.getDate() - 7);
            break;
        case 'month':
            from.setDate(now.getDate() - 30);
            break;
        case 'quarter':
            from.setDate(now.getDate() - 90);
            break;
        case '6months':
            from.setDate(now.getDate() - 180);
            break;
        case 'year':
            from.setDate(now.getDate() - 365);
            break;
        case 'all':
            from = new Date('2000-01-01');
            break;
    }
    
    state.filters.dateFrom = from;
    state.filters.dateTo = now;
    applyFilters();
}

// Handle smart filter
function handleSmartFilter(filter) {
    // Update active button
    document.querySelectorAll('.smart-filter-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    event.target.classList.add('active');
    
    state.filters.smartFilter = filter;
    
    // Apply smart filter logic
    applyFilters();
}

// Update smart filter counts
function updateSmartFilterCounts() {
    const filters = ['all', 'small_nb', 'multifamily_alt', 'large', 'single_family', 'demo', 'recent', 'expiring_soon'];
    
    filters.forEach(filterType => {
        const btn = document.querySelector(`.smart-filter-btn[data-filter="${filterType}"]`);
        if (!btn) return;
        
        // Get count for this filter applied to all permits (not just filtered)
        const count = applySmartFilter(state.permits, filterType).length;
        
        // Update button with count badge
        const existingBadge = btn.querySelector('.filter-count');
        if (existingBadge) {
            existingBadge.textContent = count;
        } else {
            const countBadge = document.createElement('span');
            countBadge.className = 'filter-count';
            countBadge.textContent = count;
            countBadge.style.cssText = 'display: block; font-size: 0.75rem; opacity: 0.8; margin-top: 0.25rem;';
            btn.appendChild(countBadge);
        }
    });
}

// Apply smart filter logic
function applySmartFilter(permits, filterType) {
    const now = new Date();
    const thirtyDaysAgo = new Date(now.getTime() - (30 * 24 * 60 * 60 * 1000));
    const thirtyDaysFromNow = new Date(now.getTime() + (30 * 24 * 60 * 60 * 1000));
    
    switch(filterType) {
        case 'all':
            return permits;
            
        case 'small_nb':
            // Small New Buildings: NB permits with less than 30 units
            return permits.filter(p => {
                const units = parseInt(p.total_units) || 0;
                return p.job_type === 'NB' && units > 0 && units < 30;
            });
            
        case 'multifamily_alt':
            // Multi-Family Renovations: Alterations (AL, A1, A2, A3) with multiple units
            return permits.filter(p => {
                const units = parseInt(p.total_units) || 0;
                const altTypes = ['AL', 'A1', 'A2', 'A3'];
                return altTypes.includes(p.job_type) && units >= 2;
            });
            
        case 'large':
            // Large Projects: 50+ units regardless of job type
            return permits.filter(p => {
                const units = parseInt(p.total_units) || 0;
                return units >= 50;
            });
            
        case 'single_family':
            // Single-Family Homes: 1-2 units (NB or AL types)
            return permits.filter(p => {
                const units = parseInt(p.total_units) || 0;
                return units >= 1 && units <= 2;
            });
            
        case 'demo':
            // Active Demo Sites: DM (demolition) permits that are not expired
            return permits.filter(p => {
                if (p.job_type !== 'DM') return false;
                if (!p.exp_date) return true;
                const expDate = new Date(p.exp_date);
                return expDate >= now;
            });
            
        case 'recent':
            // Recently Issued: Permits issued in the last 30 days
            return permits.filter(p => {
                if (!p.issue_date) return false;
                const issueDate = new Date(p.issue_date);
                return issueDate >= thirtyDaysAgo;
            });
            
        case 'expiring_soon':
            // Expiring Soon: Active permits expiring within 30 days
            return permits.filter(p => {
                if (!p.exp_date) return false;
                const expDate = new Date(p.exp_date);
                return expDate >= now && expDate <= thirtyDaysFromNow;
            });
            
        default:
            return permits;
    }
}

// Contact search
async function handleContactSearch(e) {
    const query = e.target.value;
    const resultsDiv = document.getElementById('contactSearchResults');
    
    if (query.length < 2) {
        resultsDiv.innerHTML = '';
        return;
    }
    
    try {
        const response = await fetch(`${API_BASE}/search-contact?q=${encodeURIComponent(query)}`);
        const data = await response.json();
        
        if (data.results && data.results.length > 0) {
            resultsDiv.innerHTML = `
                <p style="color: var(--success-color); margin-bottom: 0.5rem;">✅ Found ${data.results.length} permit(s)</p>
                ${data.results.slice(0, 5).map(r => `
                    <a href="/permit/${r.id}" target="_blank" class="search-result-item" style="text-decoration: none; color: inherit; display: block;">
                        <strong>${r.address}</strong>
                        <p>📞 ${r.contact_phone} | 🏗️ ${r.job_type}</p>
                        <p>📅 ${formatDate(r.issue_date)}</p>
                    </a>
                `).join('')}
                ${data.results.length > 5 ? `<p style="text-align: center; color: var(--text-secondary); font-size: 0.85rem;">Showing 5 of ${data.results.length} results - Click to view details</p>` : ''}
            `;
        } else {
            resultsDiv.innerHTML = `<p style="color: var(--warning-color);">No permits found for "${query}"</p>`;
        }
    } catch (error) {
        console.error('Contact search error:', error);
        resultsDiv.innerHTML = '<p style="color: var(--danger-color);">Search error. Please try again.</p>';
    }
}

// Tab switching
function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
    });
    event.target.classList.add('active');
    
    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    
    const tabs = {
        'leads': 'leadsTab',
        'buildings': 'buildingsTab',
        'visualizations': 'visualizationsTab',
        'map': 'mapTab'
    };
    
    document.getElementById(tabs[tabName]).classList.add('active');
    
    // Load data for specific tabs
    if (tabName === 'buildings' && state.buildings.length === 0) {
        loadBuildings();
    } else if (tabName === 'visualizations' && Object.keys(state.charts).length === 0) {
        loadVisualizations();
        loadBuildingCharts();
    }
}

// Load visualizations
function loadVisualizations() {
    // Job Type Chart
    const jobTypes = {};
    state.filteredPermits.forEach(p => {
        jobTypes[p.job_type] = (jobTypes[p.job_type] || 0) + 1;
    });
    
    createChart('jobTypeChart', 'pie', Object.keys(jobTypes), Object.values(jobTypes), 'Job Types');
    
    // Add more chart implementations here
}

// Create chart
function createChart(canvasId, type, labels, data, title) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    
    if (state.charts[canvasId]) {
        state.charts[canvasId].destroy();
    }
    
    state.charts[canvasId] = new Chart(ctx, {
        type: type,
        data: {
            labels: labels,
            datasets: [{
                label: title,
                data: data,
                backgroundColor: [
                    '#4a9eff', '#ff6b6b', '#ffc107', '#28a745', '#17a2b8',
                    '#6c757d', '#e83e8c', '#fd7e14', '#20c997', '#6f42c1'
                ]
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    labels: { color: '#ffffff' }
                }
            }
        }
    });
}

// Load map
function loadMap() {
    const mapDiv = document.getElementById('map');
    if (!mapDiv) return;
    
    // Clear existing map if it exists
    if (state.mapInstance) {
        state.mapInstance.remove();
        state.mapInstance = null;
    }
    
    const permits = state.filteredPermits.filter(p => p.latitude && p.longitude);
    
    if (permits.length === 0) {
        mapDiv.innerHTML = '<div style="display: flex; align-items: center; justify-content: center; height: 100%; color: var(--text-muted);"><p>No permits with location data to display</p></div>';
        return;
    }
    
    // Clear the map div
    mapDiv.innerHTML = '';
    
    // Initialize Leaflet map
    const avgLat = permits.reduce((sum, p) => sum + parseFloat(p.latitude), 0) / permits.length;
    const avgLng = permits.reduce((sum, p) => sum + parseFloat(p.longitude), 0) / permits.length;
    
    const map = L.map('map').setView([avgLat, avgLng], 11);
    state.mapInstance = map;
    
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap contributors',
        maxZoom: 19
    }).addTo(map);
    
    // Add markers with enhanced popups
    permits.forEach(permit => {
        const quality = getLeadQuality(permit.lead_score);
        const issueDate = permit.issue_date ? new Date(permit.issue_date).toLocaleDateString() : 'N/A';
        
        // Create custom marker icon based on lead quality
        const markerColor = quality.class === 'hot' ? '#ef4444' : quality.class === 'warm' ? '#f59e0b' : '#06b6d4';
        
        const customIcon = L.divIcon({
            className: 'custom-marker',
            html: `<div style="background: ${markerColor}; width: 30px; height: 30px; border-radius: 50%; border: 3px solid white; box-shadow: 0 2px 8px rgba(0,0,0,0.3); display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 12px;">${permit.lead_score}</div>`,
            iconSize: [30, 30],
            iconAnchor: [15, 15]
        });
        
        const popupContent = `
            <div style="min-width: 220px; font-family: 'Inter', sans-serif;">
                <div style="font-weight: 700; font-size: 1.1em; margin-bottom: 0.5rem; color: #1a1a2e;">
                    ${escapeHtml(permit.address || 'No Address')}
                </div>
                <div style="padding: 0.5rem 0; border-top: 1px solid #e2e8f0;">
                    <div style="display: flex; justify-content: space-between; margin: 0.25rem 0;">
                        <span style="color: #64748b; font-size: 0.85em;">Job Type:</span>
                        <strong style="color: #1a1a2e; font-size: 0.85em;">${permit.job_type || 'N/A'}</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin: 0.25rem 0;">
                        <span style="color: #64748b; font-size: 0.85em;">Units:</span>
                        <strong style="color: #1a1a2e; font-size: 0.85em;">${permit.total_units || 0}</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin: 0.25rem 0;">
                        <span style="color: #64748b; font-size: 0.85em;">Contacts:</span>
                        <strong style="color: #1a1a2e; font-size: 0.85em;">${permit.contact_count || 0}</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between; margin: 0.25rem 0;">
                        <span style="color: #64748b; font-size: 0.85em;">Issued:</span>
                        <strong style="color: #1a1a2e; font-size: 0.85em;">${issueDate}</strong>
                    </div>
                </div>
                <div style="margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid #e2e8f0;">
                    <div style="display: inline-block; padding: 0.25rem 0.75rem; background: ${markerColor}; color: white; border-radius: 12px; font-size: 0.8em; font-weight: 600;">
                        ${quality.icon} ${permit.lead_score}/100 - ${quality.label}
                    </div>
                </div>
                <a href="/permit/${permit.id}" target="_blank" style="display: block; margin-top: 0.75rem; padding: 0.5rem; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; text-align: center; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 0.85em;">
                    View Details →
                </a>
            </div>
        `;
        
        L.marker([parseFloat(permit.latitude), parseFloat(permit.longitude)], { icon: customIcon })
            .bindPopup(popupContent, {
                maxWidth: 300,
                className: 'custom-popup'
            })
            .addTo(map);
    });
}

// Clear filters
function clearFilters() {
    state.filters = {
        dateFilter: 'quarter',
        dateFrom: null,
        dateTo: null,
        smartFilter: 'all',
        hasContacts: true,
        mobileOnly: false,
        minContacts: 0,
        minScore: 0,
        permitStatus: ['Active'],
        permitType: 'all',
        unitsRange: 500,
        storiesRange: 100,
        contactSearch: '',
        globalSearch: ''
    };
    
    // Reset UI
    document.getElementById('dateFilter').value = 'all';
    document.getElementById('hasContacts').checked = true;
    document.getElementById('mobileOnly').checked = false;
    document.getElementById('minContacts').value = 0;
    document.getElementById('minContactsValue').textContent = '0';
    document.getElementById('minScore').value = 0;
    document.getElementById('minScoreValue').textContent = '0';
    document.getElementById('unitsRange').value = 500;
    document.getElementById('unitsRangeValue').textContent = 'All';
    document.getElementById('storiesRange').value = 100;
    document.getElementById('storiesRangeValue').textContent = 'All';
    document.getElementById('permitType').value = 'all';
    document.getElementById('globalSearch').value = '';
    document.getElementById('contactSearch').value = '';
    
    document.querySelectorAll('input[name="permitStatus"]').forEach(cb => {
        cb.checked = true;
    });
    
    document.querySelectorAll('.smart-filter-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === 'all');
    });
    
    applyFilters();
}

// Utility functions
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function showLoading(show) {
    const overlay = document.getElementById('loadingOverlay');
    overlay.classList.toggle('active', show);
}

function updateLastUpdated() {
    const now = new Date();
    document.getElementById('lastUpdated').textContent = now.toLocaleString();
}

// ============================================================================
// BUILDING INTELLIGENCE FUNCTIONS
// ============================================================================

// Load buildings data
async function loadBuildings() {
    try {
        const response = await fetch(`${API_BASE}/buildings`);
        const data = await response.json();
        
        if (data.success) {
            state.buildings = data.buildings || [];
            renderBuildings();
        }
    } catch (error) {
        console.error('Error loading buildings:', error);
    }
}

// Render buildings
function renderBuildings() {
    const container = document.getElementById('buildingsContainer');
    if (!container) return;
    
    if (state.buildings.length === 0) {
        container.innerHTML = '<div class="no-results">No building data available yet. Run building intelligence pipeline to populate this data.</div>';
        return;
    }
    
    container.innerHTML = state.buildings.map(building => `
        <div class="building-card">
            <div class="building-header">
                <div class="building-title">
                    <h3>${building.address || 'Address N/A'}</h3>
                    <div class="building-bbl">BBL: ${building.bbl}</div>
                </div>
                <div class="enrichment-badges">
                    ${building.current_owner_name ? '<span class="enrichment-badge pluto">✓ PLUTO</span>' : ''}
                    ${building.purchase_date ? '<span class="enrichment-badge acris">✓ ACRIS</span>' : ''}
                    ${building.linked_permits > 0 ? `<span class="enrichment-badge contacts">${building.linked_permits} Permits</span>` : ''}
                </div>
            </div>
            
            ${building.current_owner_name ? `
                <div class="building-owner">
                    <div class="owner-label">Property Owner</div>
                    <div class="owner-name">${building.current_owner_name}</div>
                </div>
            ` : ''}
            
            <div class="building-metrics">
                ${building.year_built ? `
                    <div class="metric-item">
                        <div class="metric-label">Year Built</div>
                        <div class="metric-value">${building.year_built}</div>
                    </div>
                ` : ''}
                ${building.residential_units ? `
                    <div class="metric-item">
                        <div class="metric-label">Units</div>
                        <div class="metric-value highlight">${building.residential_units}</div>
                    </div>
                ` : ''}
                ${building.num_floors ? `
                    <div class="metric-item">
                        <div class="metric-label">Floors</div>
                        <div class="metric-value">${building.num_floors}</div>
                    </div>
                ` : ''}
                ${building.building_sqft ? `
                    <div class="metric-item">
                        <div class="metric-label">Sq Ft</div>
                        <div class="metric-value">${parseInt(building.building_sqft).toLocaleString()}</div>
                    </div>
                ` : ''}
                ${building.building_class ? `
                    <div class="metric-item">
                        <div class="metric-label">Building Class</div>
                        <div class="metric-value">${building.building_class}</div>
                    </div>
                ` : ''}
                ${building.purchase_price ? `
                    <div class="metric-item">
                        <div class="metric-label">Purchase Price</div>
                        <div class="metric-value highlight">$${parseInt(building.purchase_price).toLocaleString()}</div>
                    </div>
                ` : ''}
            </div>
            
            <div class="building-footer">
                <div class="permit-count-badge">
                    <i class="fas fa-file-alt"></i>
                    <span class="count">${building.linked_permits || 0}</span> Linked Permits
                </div>
                <button class="view-building-btn" onclick="viewBuildingDetail(${building.id})">
                    View Details <i class="fas fa-arrow-right"></i>
                </button>
            </div>
        </div>
    `).join('');
}

// View building detail
async function viewBuildingDetail(buildingId) {
    try {
        const response = await fetch(`${API_BASE}/buildings/${buildingId}`);
        const data = await response.json();
        
        if (data.success) {
            showBuildingModal(data);
        }
    } catch (error) {
        console.error('Error loading building detail:', error);
    }
}

// Show building modal
function showBuildingModal(data) {
    const { building, permits, contacts } = data;
    
    const modalHTML = `
        <div class="modal-overlay active" id="buildingModal">
            <div class="modal-content">
                <div class="modal-header">
                    <h2>${building.address || 'Building Details'}</h2>
                    <button class="modal-close" onclick="closeBuildingModal()">
                        <i class="fas fa-times"></i>
                    </button>
                </div>
                <div class="modal-body">
                    <!-- Owner Section -->
                    ${building.current_owner_name ? `
                        <div class="detail-section">
                            <h3><i class="fas fa-user"></i> Owner Information</h3>
                            <div class="detail-grid">
                                <div class="detail-item">
                                    <div class="detail-label">Owner Name</div>
                                    <div class="detail-value">${building.current_owner_name}</div>
                                </div>
                                ${building.owner_mailing_address ? `
                                    <div class="detail-item">
                                        <div class="detail-label">Mailing Address</div>
                                        <div class="detail-value">${building.owner_mailing_address}</div>
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    ` : ''}
                    
                    <!-- Property Details -->
                    <div class="detail-section">
                        <h3><i class="fas fa-building"></i> Property Details</h3>
                        <div class="detail-grid">
                            <div class="detail-item">
                                <div class="detail-label">BBL</div>
                                <div class="detail-value">${building.bbl}</div>
                            </div>
                            ${building.year_built ? `
                                <div class="detail-item">
                                    <div class="detail-label">Year Built</div>
                                    <div class="detail-value">${building.year_built}</div>
                                </div>
                            ` : ''}
                            ${building.building_class ? `
                                <div class="detail-item">
                                    <div class="detail-label">Building Class</div>
                                    <div class="detail-value">${building.building_class}</div>
                                </div>
                            ` : ''}
                            ${building.residential_units ? `
                                <div class="detail-item">
                                    <div class="detail-label">Residential Units</div>
                                    <div class="detail-value">${building.residential_units}</div>
                                </div>
                            ` : ''}
                            ${building.total_units ? `
                                <div class="detail-item">
                                    <div class="detail-label">Total Units</div>
                                    <div class="detail-value">${building.total_units}</div>
                                </div>
                            ` : ''}
                            ${building.num_floors ? `
                                <div class="detail-item">
                                    <div class="detail-label">Number of Floors</div>
                                    <div class="detail-value">${building.num_floors}</div>
                                </div>
                            ` : ''}
                            ${building.building_sqft ? `
                                <div class="detail-item">
                                    <div class="detail-label">Building Sq Ft</div>
                                    <div class="detail-value">${parseInt(building.building_sqft).toLocaleString()}</div>
                                </div>
                            ` : ''}
                            ${building.lot_sqft ? `
                                <div class="detail-item">
                                    <div class="detail-label">Lot Sq Ft</div>
                                    <div class="detail-value">${parseInt(building.lot_sqft).toLocaleString()}</div>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                    
                    <!-- Financial Information -->
                    ${building.purchase_date || building.purchase_price ? `
                        <div class="detail-section">
                            <h3><i class="fas fa-dollar-sign"></i> Financial Information</h3>
                            <div class="detail-grid">
                                ${building.purchase_date ? `
                                    <div class="detail-item">
                                        <div class="detail-label">Purchase Date</div>
                                        <div class="detail-value">${new Date(building.purchase_date).toLocaleDateString()}</div>
                                    </div>
                                ` : ''}
                                ${building.purchase_price ? `
                                    <div class="detail-item">
                                        <div class="detail-label">Purchase Price</div>
                                        <div class="detail-value">$${parseInt(building.purchase_price).toLocaleString()}</div>
                                    </div>
                                ` : ''}
                                ${building.mortgage_amount ? `
                                    <div class="detail-item">
                                        <div class="detail-label">Mortgage Amount</div>
                                        <div class="detail-value">$${parseInt(building.mortgage_amount).toLocaleString()}</div>
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    ` : ''}
                    
                    <!-- Permits -->
                    ${permits && permits.length > 0 ? `
                        <div class="detail-section">
                            <h3><i class="fas fa-file-alt"></i> Linked Permits (${permits.length})</h3>
                            <div class="contact-list">
                                ${permits.map(permit => `
                                    <div class="contact-item">
                                        <div class="contact-info">
                                            <div class="contact-name">${permit.permit_no}</div>
                                            <div class="contact-phone">${permit.job_type || ''} - ${permit.issue_date ? new Date(permit.issue_date).toLocaleDateString() : 'N/A'}</div>
                                        </div>
                                        <span style="color: var(--text-muted);">${permit.contact_count || 0} contacts</span>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    ` : ''}
                    
                    <!-- Contacts -->
                    ${contacts && contacts.length > 0 ? `
                        <div class="detail-section">
                            <h3><i class="fas fa-phone"></i> All Contacts (${contacts.length})</h3>
                            <div class="contact-list">
                                ${contacts.map(contact => `
                                    <div class="contact-item">
                                        <div class="contact-info">
                                            <div class="contact-name">${contact.name || 'Unknown'}</div>
                                            <div class="contact-phone">${contact.phone || 'No phone'}</div>
                                        </div>
                                        ${contact.is_mobile ? '<span class="enrichment-badge contacts">📱 Mobile</span>' : ''}
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    ` : ''}
                </div>
            </div>
        </div>
    `;
    
    // Remove existing modal if any
    const existing = document.getElementById('buildingModal');
    if (existing) existing.remove();
    
    // Add modal to body
    document.body.insertAdjacentHTML('beforeend', modalHTML);
    
    // Close on overlay click
    document.querySelector('.modal-overlay').addEventListener('click', (e) => {
        if (e.target.classList.contains('modal-overlay')) {
            closeBuildingModal();
        }
    });
}

function closeBuildingModal() {
    const modal = document.getElementById('buildingModal');
    if (modal) modal.remove();
}

// Update stats to include building intelligence
async function updateStats() {
    try {
        const response = await fetch(`${API_BASE}/stats`);
        const data = await response.json();
        
        if (data.success) {
            state.stats = data.stats;
            
            // Update stat cards
            document.getElementById('totalLeads').textContent = data.stats.total_permits.toLocaleString();
            document.getElementById('totalContacts').textContent = data.stats.total_contacts.toLocaleString();
            document.getElementById('mobileCount').textContent = data.stats.mobile_contacts.toLocaleString();
            document.getElementById('totalBuildings').textContent = data.stats.total_buildings.toLocaleString();
            document.getElementById('enrichmentRate').textContent = `${data.stats.enrichment_rate}%`;
            
            // Update building tab stats
            if (document.getElementById('buildingsTotal')) {
                document.getElementById('buildingsTotal').textContent = data.stats.total_buildings;
                document.getElementById('buildingsWithOwners').textContent = data.stats.buildings_with_owners;
                document.getElementById('buildingsWithAcris').textContent = data.stats.buildings_with_acris;
                document.getElementById('permitsWithBbl').textContent = data.stats.permits_with_bbl;
            }
        }
    } catch (error) {
        console.error('Error updating stats:', error);
    }
}

// Load building intelligence charts
async function loadBuildingCharts() {
    try {
        // Top Owners Chart
        const ownersResponse = await fetch(`${API_BASE}/charts/owners`);
        const ownersData = await ownersResponse.json();
        
        if (ownersData.success && ownersData.labels.length > 0) {
            createBuildingChart('ownersChart', 'bar', ownersData.labels, ownersData.permit_counts, 'Permit Count by Owner');
        }
        
        // Building Age Distribution
        const ageResponse = await fetch(`${API_BASE}/charts/building-ages`);
        const ageData = await ageResponse.json();
        
        if (ageData.success && ageData.labels.length > 0) {
            createBuildingChart('buildingAgeChart', 'doughnut', ageData.labels, ageData.data, 'Buildings by Age');
        }
        
        // Unit Distribution
        const unitResponse = await fetch(`${API_BASE}/charts/unit-distribution`);
        const unitData = await unitResponse.json();
        
        if (unitData.success && unitData.labels.length > 0) {
            createBuildingChart('unitDistChart', 'pie', unitData.labels, unitData.data, 'Buildings by Size');
        }
    } catch (error) {
        console.error('Error loading building charts:', error);
    }
}

function createBuildingChart(canvasId, type, labels, data, title) {
    const ctx = document.getElementById(canvasId);
    if (!ctx) return;
    
    if (state.charts[canvasId]) {
        state.charts[canvasId].destroy();
    }
    
    const colors = [
        '#667eea', '#764ba2', '#f093fb', '#f5576c', '#4facfe',
        '#00f2fe', '#43e97b', '#38f9d7', '#fa709a', '#fee140'
    ];
    
    state.charts[canvasId] = new Chart(ctx, {
        type: type,
        data: {
            labels: labels,
            datasets: [{
                label: title,
                data: data,
                backgroundColor: colors,
                borderColor: type === 'bar' ? colors : 'rgba(255, 255, 255, 0.1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            plugins: {
                legend: {
                    labels: { 
                        color: '#ffffff',
                        font: { size: 12 }
                    },
                    display: type !== 'bar'
                },
                title: {
                    display: false
                }
            },
            scales: type === 'bar' ? {
                y: {
                    beginAtZero: true,
                    ticks: { color: '#ffffff' },
                    grid: { color: 'rgba(255, 255, 255, 0.1)' }
                },
                x: {
                    ticks: { 
                        color: '#ffffff',
                        maxRotation: 45,
                        minRotation: 45
                    },
                    grid: { display: false }
                }
            } : {}
        }
    });
}

