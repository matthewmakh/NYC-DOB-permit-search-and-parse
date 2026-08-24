// ============================================================================
// CONTRACTOR DIRECTORY & PROFILE JAVASCRIPT
// ============================================================================

// Global state
let currentPage = 1;
let currentPerPage = 50;
let currentSearch = '';
let currentSort = [];   // Sort keys in pick order; empty means the API default
let currentOrder = 'desc';
let contractorData = null;

// Filters this page owns. The rest — borough, property type, building class,
// units, value, kind of work, recency, violations — live in SharedFilters and
// mean exactly what they mean on the properties page.
let ownFilters = {
    minJobs: null,
    maxJobs: null,
    minActiveJobs: null,
    minProperties: null,
    maxProperties: null,
    participantRole: '',
    probableGcOnly: false,
};
const OWN_FILTER_PARAMS = {
    minJobs: 'min_jobs',
    maxJobs: 'max_jobs',
    minActiveJobs: 'min_active_jobs',
    minProperties: 'min_properties',
    maxProperties: 'max_properties',
};
let sharedFilters = {};

// ============================================================================
// CONTRACTOR DIRECTORY PAGE
// ============================================================================

if (document.getElementById('contractorSearch')) {
    // Initialize directory page
    initializeDirectoryPage();
}

function initializeDirectoryPage() {
    MultiSelect.init();
    sharedFilters = SharedFilters.read();

    SharedFilters.loadFacets();
    loadContractors();

    // Any shared filter changing means a new first page of results.
    SharedFilters.bind(() => {
        sharedFilters = SharedFilters.read();
        currentPage = 1;
        loadContractors();
    });

    // Search
    document.getElementById('contractorSearch').addEventListener('input', debounce((e) => {
        currentSearch = e.target.value.trim();
        currentPage = 1;
        loadContractors();
    }, 500));

    document.getElementById('participantRole')?.addEventListener('change', (e) => {
        ownFilters.participantRole = e.target.value;
        currentPage = 1;
        loadContractors();
    });
    document.getElementById('probableGcOnly')?.addEventListener('change', (e) => {
        ownFilters.probableGcOnly = e.target.checked;
        currentPage = 1;
        loadContractors();
    });

    // Contractor-scale filters
    Object.keys(OWN_FILTER_PARAMS).forEach(id => {
        const node = document.getElementById(id);
        if (!node) return;
        const apply = () => {
            const raw = node.value !== '' ? Number(node.value) : null;
            ownFilters[id] = Number.isFinite(raw) ? raw : null;
            currentPage = 1;
            loadContractors();
        };
        node.addEventListener('change', apply);
        node.addEventListener('input', debounce(apply, 600));
    });

    document.getElementById('clearFiltersBtn').addEventListener('click', clearFilters);

    // Sort controls — several keys allowed; later ones break ties.
    document.getElementById('sortBy').addEventListener('change', () => {
        currentSort = MultiSelect.values('sortBy');
        currentPage = 1;
        loadContractors();
    });

    document.getElementById('sortOrder').addEventListener('change', (e) => {
        currentOrder = e.target.value;
        currentPage = 1;
        loadContractors();
    });

    document.getElementById('perPage').addEventListener('change', (e) => {
        currentPerPage = parseInt(e.target.value, 10) || 50;
        currentPage = 1;
        loadContractors();
    });
    
    // Pagination
    document.getElementById('prevPage').addEventListener('click', () => {
        if (currentPage > 1) {
            currentPage--;
            loadContractors();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }
    });

    document.getElementById('nextPage').addEventListener('click', () => {
        currentPage++;
        loadContractors();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    });
}

async function loadContractors() {
    const grid = document.getElementById('contractorsGrid');
    
    // Show skeleton loading
    grid.innerHTML = Array(6).fill(0).map(() => `
        <div class="skeleton-card">
            <div class="skeleton-header">
                <div class="skeleton-avatar"></div>
                <div class="skeleton-info">
                    <div class="skeleton-line"></div>
                    <div class="skeleton-line short"></div>
                </div>
            </div>
            <div class="skeleton-stats">
                <div class="skeleton-stat"></div>
                <div class="skeleton-stat"></div>
                <div class="skeleton-stat"></div>
                <div class="skeleton-stat"></div>
            </div>
        </div>
    `).join('');
    
    try {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: currentPerPage,
            sort_order: currentOrder
        });
        currentSort.forEach(key => params.append('sort_by', key));

        if (currentSearch) {
            params.append('search', currentSearch);
        }

        SharedFilters.toParams(params, sharedFilters);
        Object.entries(OWN_FILTER_PARAMS).forEach(([id, param]) => {
            const value = ownFilters[id];
            if (value !== null && value !== undefined) params.append(param, value);
        });
        if (ownFilters.participantRole) params.set('role', ownFilters.participantRole);
        if (ownFilters.probableGcOnly) params.set('probable_gc', 'true');
        
        const response = await fetch(`/api/contractors/search?${params}`);
        const data = await response.json();
        
        if (data.success) {
            displayContractors(data.contractors);
            updatePagination(data.pagination);
            updateHeaderStats(data.pagination.total);
            // The hero metric already carries the total; the toolbar only
            // says which page of it you are looking at.
            const count = document.getElementById('resultsCount');
            if (count) {
                count.textContent = data.pagination.pages > 1
                    ? `Page ${data.pagination.page} of ${formatNumber(data.pagination.pages)}`
                    : '';
            }
        } else {
            showError(grid, data.error || 'Failed to load contractors');
        }
    } catch (error) {
        console.error('Error loading contractors:', error);
        showError(grid, 'Network error. Please try again.');
    }
}

// What kind of work this contractor actually does, biggest category first.
// The API resolves it across work_type / permit_type / job_type, so a trade
// that only populates one of those still reads properly.
function renderWorkMix(contractor) {
    const mix = contractor.work_mix || [];
    if (!mix.length) return '';

    const total = mix.reduce((sum, item) => sum + item.count, 0) || 1;
    const chips = mix.map(item => {
        const share = Math.round((item.count / total) * 100);
        return `
            <span class="work-chip" title="${escapeHtml(item.label)} — ${formatNumber(item.count)} permits">
                <span class="work-chip-bar" style="width: ${share}%"></span>
                <span class="work-chip-text">${escapeHtml(item.label)}</span>
                <span class="work-chip-count">${formatNumber(item.count)}</span>
            </span>
        `;
    }).join('');

    const more = contractor.work_mix_other
        ? `<span class="work-chip work-chip-more">+${contractor.work_mix_other} more</span>`
        : '';

    return `
        <div class="contractor-work">
            <div class="contractor-work-label">Work</div>
            <div class="work-chips">${chips}${more}</div>
        </div>
    `;
}

function displayContractors(contractors) {
    const grid = document.getElementById('contractorsGrid');
    
    if (contractors.length === 0) {
        grid.innerHTML = `
            <div class="grid-empty">
                <i class="fas fa-search"></i>
                <h3>No permit participants found</h3>
                <p>Try adjusting your search criteria</p>
            </div>
        `;
        return;
    }

    grid.innerHTML = contractors.map(contractor => `
        <div class="contractor-card" data-contractor-name="${escapeHtml(contractor.contractor_name)}">
            <div class="contractor-header">
                <div class="contractor-avatar">
                    <i class="fas fa-hard-hat"></i>
                </div>
                <div class="contractor-info">
                    <h3>${escapeHtml(contractor.contractor_name)}</h3>
                    ${contractor.license ? `
                        <div class="contractor-license">License ${escapeHtml(contractor.license)}</div>
                    ` : ''}
                    ${contractor.participant_roles ? `
                        <div class="contractor-license">${escapeHtml(contractor.participant_roles.replaceAll('_', ' '))}</div>
                    ` : ''}
                    ${contractor.role_confidence != null ? `
                        <div class="contractor-license">Role confidence ${Math.round(Number(contractor.role_confidence) * 100)}%${
                            contractor.contractor_confidence != null
                                ? ` · contractor confidence ${Math.round(Number(contractor.contractor_confidence) * 100)}%`
                                : ''}</div>
                    ` : ''}
                </div>
            </div>

            <div class="contractor-stats">
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${contractor.total_jobs}</div>
                    <div class="contractor-stat-label">Total jobs</div>
                </div>
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${contractor.active_jobs}</div>
                    <div class="contractor-stat-label">Active</div>
                </div>
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${contractor.unique_properties}</div>
                    <div class="contractor-stat-label">Properties</div>
                </div>
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${formatCurrency(contractor.largest_project)}</div>
                    <div class="contractor-stat-label">Largest</div>
                </div>
            </div>

            ${renderWorkMix(contractor)}

            <div class="contractor-meta">
                <span>
                    <i class="fas fa-calendar"></i>
                    Last permit ${formatDate(contractor.most_recent_job)}
                </span>
                ${contractor.license_type ? `<span>
                    <i class="fas fa-id-card"></i>
                    ${escapeHtml(contractor.license_type)}
                </span>` : ''}
            </div>
        </div>
    `).join('');
    
    // Add click listeners to contractor cards
    document.querySelectorAll('.contractor-card').forEach(card => {
        card.addEventListener('click', () => {
            const contractorName = card.dataset.contractorName;
            navigateToContractor(contractorName);
        });
    });
}

function clearFilters() {
    SharedFilters.clear();
    Object.keys(OWN_FILTER_PARAMS).forEach(id => {
        const node = document.getElementById(id);
        if (node) node.value = '';
        ownFilters[id] = null;
    });
    const role = document.getElementById('participantRole');
    const probableGc = document.getElementById('probableGcOnly');
    if (role) role.value = '';
    if (probableGc) probableGc.checked = false;
    ownFilters.participantRole = '';
    ownFilters.probableGcOnly = false;
    const search = document.getElementById('contractorSearch');
    if (search) search.value = '';
    currentSearch = '';
    sharedFilters = SharedFilters.read();
    currentPage = 1;
    loadContractors();
}

function updatePagination(pagination) {
    const paginationDiv = document.getElementById('pagination');
    const prevBtn = document.getElementById('prevPage');
    const nextBtn = document.getElementById('nextPage');
    const pageInfo = document.getElementById('pageInfo');
    
    paginationDiv.style.display = pagination.pages > 1 ? 'flex' : 'none';
    pageInfo.textContent = `Page ${pagination.page} of ${pagination.pages}`;
    prevBtn.disabled = pagination.page <= 1;
    nextBtn.disabled = pagination.page >= pagination.pages;
}

function updateHeaderStats(total) {
    document.getElementById('totalContractors').textContent = formatNumber(total);
    // Active contractors would need a separate API call or be included in the response
}

function navigateToContractor(contractorName) {
    window.location.href = `/contractor/${encodeURIComponent(contractorName)}`;
}

// ============================================================================
// CONTRACTOR PROFILE PAGE
// ============================================================================

if (typeof CONTRACTOR_NAME !== 'undefined') {
    // Initialize profile page
    initializeProfilePage();
}

function initializeProfilePage() {
    // Add loading class to all stat elements
    document.querySelectorAll('.stat-card').forEach(card => card.classList.add('loading'));
    document.querySelectorAll('.sidebar-card').forEach(card => card.classList.add('loading'));
    document.querySelector('.profile-license')?.classList.add('loading');
    document.querySelector('.profile-meta')?.classList.add('loading');
    
    // Load contractor data
    loadContractorProfile();
    
    // Tab switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.dataset.tab;
            switchTab(tabName);
        });
    });
    
    // Stat tiles scroll to the card they summarize
    document.querySelectorAll('.stat-card.clickable').forEach(card => {
        card.addEventListener('click', () => {
            const tabName = card.dataset.tab;
            if (tabName) switchTab(tabName);
        });
    });
    
    // Search within tabs
    document.getElementById('permitsSearch')?.addEventListener('input', debounce((e) => {
        filterPermits(e.target.value);
    }, 300));
    
    document.getElementById('buildingsSearch')?.addEventListener('input', debounce((e) => {
        filterBuildings(e.target.value);
    }, 300));
}

async function loadContractorProfile() {
    try {
        const response = await fetch(`/api/contractor/${encodeURIComponent(CONTRACTOR_NAME)}`);
        const data = await response.json();
        
        if (data.success) {
            contractorData = data;
            displayContractorStats(data.contractor);
            displayPermits(data.permits);
            displayBuildings(data.buildings);
            renderProfileWorkMix(data.permits);
            renderBoroughsFact(data.buildings);
        } else {
            showError(document.querySelector('.profile-header'), data.error || 'Permit participant not found');
        }
    } catch (error) {
        console.error('Error loading contractor profile:', error);
        showError(document.querySelector('.profile-header'), 'Failed to load contractor profile');
    }
}

function displayContractorStats(contractor) {
    // Remove loading class from all elements
    document.querySelectorAll('.stat-card').forEach(card => card.classList.remove('loading'));
    document.querySelectorAll('.sidebar-card').forEach(card => card.classList.remove('loading'));
    document.querySelector('.profile-license')?.classList.remove('loading');
    document.querySelector('.profile-meta')?.classList.remove('loading');
    
    // Update header
    document.getElementById('contractorName').textContent = contractor.contractor_name;
    document.getElementById('contractorLicense').textContent = contractor.license ?
        `Licence ${contractor.license}` : 'No licence on file';
    const participantRoles = document.getElementById('participantRoles');
    if (participantRoles && contractor.participant_roles) {
        const confidence = contractor.role_confidence != null
            ? ` · ${Math.round(Number(contractor.role_confidence) * 100)}% role confidence`
            : '';
        participantRoles.textContent = contractor.participant_roles.replaceAll('_', ' ') + confidence;
        participantRoles.style.display = '';
    }

    // Licence rail card only appears when there is a number to show.
    const licenseCard = document.getElementById('licenseCard');
    if (licenseCard && contractor.license) {
        licenseCard.style.display = '';
        document.getElementById('licenseNumber').textContent = contractor.license;
        const lookupBtn = document.getElementById('licenseLookupBtn');
        if (lookupBtn) {
            if (typeof showLicenseInfo === 'function') {
                lookupBtn.addEventListener('click', () =>
                    showLicenseInfo(contractor.license, contractor.license_type || null));
            } else {
                lookupBtn.style.display = 'none';
            }
        }
    }

    if (contractor.most_recent_job) {
        document.getElementById('mostRecentJob').textContent =
            `Last permit ${formatDate(contractor.most_recent_job)}`;
    }
    
    if (contractor.job_types) {
        const jobTypesChip = document.getElementById('jobTypes');
        jobTypesChip.textContent = contractor.job_types;
        jobTypesChip.style.display = '';
    }
    
    // Update main stat cards
    document.getElementById('totalJobs').textContent = formatNumber(contractor.total_jobs);
    document.getElementById('activeJobs').textContent = formatNumber(contractor.active_jobs);
    document.getElementById('uniqueProperties').textContent = formatNumber(contractor.unique_properties);
    document.getElementById('totalValue').textContent = formatCurrency(contractor.total_value);
    
    // Update sidebar stats (About section)
    if (document.getElementById('firstJob')) {
        document.getElementById('firstJob').textContent = contractor.first_job ? 
            formatDate(contractor.first_job) : 'N/A';
    }
    
    if (document.getElementById('jobTypeVariety')) {
        document.getElementById('jobTypeVariety').textContent = contractor.job_type_variety || 'N/A';
    }
    
    if (document.getElementById('avgProjectValue')) {
        document.getElementById('avgProjectValue').textContent = contractor.avg_project_value ?
            formatCurrency(contractor.avg_project_value) : 'N/A';
    }
    
    // Update sidebar quick stats (Highlights section)
    if (document.getElementById('sidebarActiveJobs')) {
        document.getElementById('sidebarActiveJobs').textContent = formatNumber(contractor.active_jobs);
    }
    
    if (document.getElementById('sidebarLargestProject')) {
        document.getElementById('sidebarLargestProject').textContent = contractor.largest_project ?
            formatCurrency(contractor.largest_project) : 'N/A';
    }
    
    // Update tab badges
    if (document.getElementById('jobsLastYearBadge')) {
        document.getElementById('jobsLastYearBadge').textContent = formatNumber(contractor.jobs_last_year || 0);
    }
}

function displayPermits(permits) {
    const permitsList = document.getElementById('permitsList');
    document.getElementById('permitsCount').textContent = permits.length;
    
    if (permits.length === 0) {
        permitsList.innerHTML = '<p style="text-align: center; color: #999; padding: 40px;">No permits found</p>';
        return;
    }
    
    permitsList.innerHTML = permits.map(permit => `
        <div class="permit-item" onclick="navigateToPermit(${permit.id})">
            <div class="permit-icon">
                <i class="fas fa-file-contract"></i>
            </div>
            <div class="permit-details">
                <div class="permit-header">
                    <span class="permit-number">${permit.permit_no}</span>
                    <span class="permit-type">${permit.job_type || 'N/A'}</span>
                </div>
                <div class="permit-address">${permit.address || 'Address not available'}</div>
                <div class="permit-meta">
                    ${permit.bbl ? `<span><i class="fas fa-map-marker-alt"></i> BBL: ${permit.bbl}</span>` : ''}
                    ${permit.stories ? `<span><i class="fas fa-building"></i> ${permit.stories} stories</span>` : ''}
                    ${permit.total_units ? `<span><i class="fas fa-home"></i> ${permit.total_units} units</span>` : ''}
                    ${permit.current_owner_name ? `<span><i class="fas fa-user"></i> ${permit.current_owner_name}</span>` : ''}
                </div>
            </div>
            <div class="permit-stats">
                ${permit.assessed_total_value ? `
                    <div class="permit-value">${formatCurrency(permit.assessed_total_value)}</div>
                ` : ''}
                <div class="permit-date">${formatDate(permit.issue_date)}</div>
            </div>
        </div>
    `).join('');
}

function displayBuildings(buildings) {
    const buildingsList = document.getElementById('buildingsList');
    document.getElementById('buildingsCount').textContent = buildings.length;
    
    if (buildings.length === 0) {
        buildingsList.innerHTML = '<p style="text-align: center; color: #999; padding: 40px;">No buildings found</p>';
        return;
    }
    
    buildingsList.innerHTML = buildings.map(building => `
        <div class="building-item" onclick="navigateToProperty('${building.bbl}')">
            <div class="building-icon">
                <i class="fas fa-building"></i>
            </div>
            <div class="building-details">
                <div class="building-header">
                    <span class="permit-number">${building.address || 'Address not available'}</span>
                </div>
                <div class="permit-meta">
                    <span><i class="fas fa-map-marker-alt"></i> BBL: ${building.bbl}</span>
                    <span><i class="fas fa-file-contract"></i> ${building.permit_count} permits</span>
                    ${building.total_units ? `<span><i class="fas fa-home"></i> ${building.total_units} units</span>` : ''}
                    ${building.building_class ? `<span><i class="fas fa-tag"></i> ${building.building_class}</span>` : ''}
                    ${building.current_owner_name ? `<span><i class="fas fa-user"></i> ${building.current_owner_name}</span>` : ''}
                </div>
                <div class="permit-meta" style="margin-top: 8px;">
                    <span><i class="fas fa-calendar-check"></i> Most recent: ${formatDate(building.most_recent_work)}</span>
                    ${building.job_types ? `<span><i class="fas fa-tools"></i> ${building.job_types}</span>` : ''}
                </div>
            </div>
            <div class="building-stats">
                ${building.assessed_total_value ? `
                    <div class="building-value">${formatCurrency(building.assessed_total_value)}</div>
                ` : ''}
            </div>
        </div>
    `).join('');
}

// Permits and buildings are both always on the page now — a "tab" click
// just scrolls its card into view.
function switchTab(tabName) {
    document.getElementById(`${tabName}Tab`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/**
 * "What they do" rail card: share of this contractor's permits by job type.
 * Computed from the permits the profile already loaded — no extra request.
 */
function renderProfileWorkMix(permits) {
    const card = document.getElementById('workMixCard');
    const bars = document.getElementById('workMixBars');
    if (!card || !bars || !permits || !permits.length) return;

    const counts = {};
    let counted = 0;
    permits.forEach(p => {
        const key = (p.job_type || '').trim();
        if (!key) return;
        counts[key] = (counts[key] || 0) + 1;
        counted += 1;
    });
    if (!counted) return;

    const top = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const shown = top.slice(0, 4);
    const otherCount = top.slice(4).reduce((sum, [, n]) => sum + n, 0);
    if (otherCount) shown.push(['Other', otherCount]);

    bars.innerHTML = shown.map(([label, n], i) => {
        const pct = Math.round((n / counted) * 100);
        return `
        <div class="workmix-row">
            <div class="workmix-head"><span>${escapeHtml(label)}</span><span>${pct}%</span></div>
            <div class="workmix-track"><div class="workmix-fill ${label === 'Other' ? 'fill-muted' : ''}" style="width: ${Math.max(pct, 2)}%"></div></div>
        </div>`;
    }).join('');

    document.getElementById('workMixNote').textContent =
        `Share of ${formatNumber(counted)} permits, by job type on the permit.`;
    card.style.display = '';
}

/** Which boroughs this contractor's buildings sit in, for the About card. */
function renderBoroughsFact(buildings) {
    const row = document.getElementById('boroughsRow');
    const fact = document.getElementById('boroughsFact');
    if (!row || !fact || !buildings || !buildings.length) return;

    const names = { '1': 'Mn', '2': 'Bx', '3': 'Bk', '4': 'Qn', '5': 'SI' };
    const seen = [];
    buildings.forEach(b => {
        const short = names[String(b.borough)] || null;
        if (short && !seen.includes(short)) seen.push(short);
    });
    if (!seen.length) return;
    fact.textContent = seen.join(' · ');
    row.style.display = '';
}

function filterPermits(searchTerm) {
    const items = document.querySelectorAll('.permit-item');
    const term = searchTerm.toLowerCase();
    
    items.forEach(item => {
        const text = item.textContent.toLowerCase();
        item.style.display = text.includes(term) ? 'grid' : 'none';
    });
}

function filterBuildings(searchTerm) {
    const items = document.querySelectorAll('.building-item');
    const term = searchTerm.toLowerCase();
    
    items.forEach(item => {
        const text = item.textContent.toLowerCase();
        item.style.display = text.includes(term) ? 'grid' : 'none';
    });
}

// ============================================================================
// NAVIGATION FUNCTIONS
// ============================================================================

function navigateToPermit(permitId) {
    window.open(`/permit/${permitId}`, '_blank');
}

function navigateToProperty(bbl) {
    window.location.href = `/property/${bbl}`;
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

function formatCurrency(value) {
    if (!value || value === 0) return 'N/A';
    if (value >= 1000000) {
        return `$${(value / 1000000).toFixed(1)}M`;
    }
    if (value >= 1000) {
        return `$${(value / 1000).toFixed(0)}K`;
    }
    return `$${value.toLocaleString()}`;
}

function formatNumber(value) {
    if (!value && value !== 0) return '0';
    if (value >= 1000000) {
        return `${(value / 1000000).toFixed(1)}M`;
    }
    if (value >= 1000) {
        return `${(value / 1000).toFixed(1)}K`;
    }
    return value.toLocaleString();
}

function formatDate(dateString) {
    if (!dateString) return 'N/A';
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function truncate(text, maxLength) {
    if (!text) return '';
    return text.length > maxLength ? text.substring(0, maxLength) + '…' : text;
}

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

function showError(container, message) {
    // Inline styles so this renders correctly on both the directory page
    // (app.css) and the contractor profile page (main.css).
    const errorHTML = `
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 64px 20px; background: #fff; border: 1px solid #e5e5e0; border-radius: 14px; margin: 40px auto; max-width: 520px; text-align: center;">
            <i class="fas fa-exclamation-circle" style="font-size: 1.8em; color: #bb3a2e; margin-bottom: 14px;"></i>
            <h2 style="color: #1a1a17; font-size: 1.1em; margin: 0 0 8px 0;">Something went wrong</h2>
            <p style="color: #55554f; font-size: 0.95em; margin: 0;">${escapeHtml(message)}</p>
        </div>
    `;

    // If we're on the profile page, show in main content area
    const profileMain = document.querySelector('.profile-main');
    if (profileMain) {
        profileMain.innerHTML = errorHTML;
    } else {
        container.innerHTML = errorHTML;
    }
}
