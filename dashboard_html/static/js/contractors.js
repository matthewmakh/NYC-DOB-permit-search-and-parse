// ============================================================================
// CONTRACTOR DIRECTORY & PROFILE JAVASCRIPT
// ============================================================================

// Global state
let currentPage = 1;
let currentSearch = '';
let currentSort = 'total_jobs';
let currentOrder = 'desc';
let contractorData = null;

// ============================================================================
// CONTRACTOR DIRECTORY PAGE
// ============================================================================

if (document.getElementById('contractorSearch')) {
    // Initialize directory page
    initializeDirectoryPage();
}

function initializeDirectoryPage() {
    // Load initial contractors
    loadContractors();
    
    // Search input
    const searchInput = document.getElementById('contractorSearch');
    const clearSearchBtn = document.getElementById('clearSearch');
    
    searchInput.addEventListener('input', debounce((e) => {
        currentSearch = e.target.value.trim();
        currentPage = 1;
        clearSearchBtn.style.display = currentSearch ? 'block' : 'none';
        loadContractors();
    }, 500));
    
    clearSearchBtn.addEventListener('click', () => {
        searchInput.value = '';
        currentSearch = '';
        currentPage = 1;
        clearSearchBtn.style.display = 'none';
        loadContractors();
    });
    
    // Sort controls
    document.getElementById('sortBy').addEventListener('change', (e) => {
        currentSort = e.target.value;
        currentPage = 1;
        loadContractors();
    });
    
    document.getElementById('sortOrder').addEventListener('change', (e) => {
        currentOrder = e.target.value;
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
    
    // Export button
    document.getElementById('exportBtn').addEventListener('click', exportContractors);
}

async function loadContractors() {
    const grid = document.getElementById('contractorsGrid');
    
    // Show loading
    grid.innerHTML = `
        <div class="loading-placeholder">
            <div class="loading-spinner"></div>
            <p>Loading contractors...</p>
        </div>
    `;
    
    try {
        const params = new URLSearchParams({
            page: currentPage,
            per_page: 50,
            sort_by: currentSort,
            sort_order: currentOrder
        });
        
        if (currentSearch) {
            params.append('search', currentSearch);
        }
        
        const response = await fetch(`/api/contractors/search?${params}`);
        const data = await response.json();
        
        if (data.success) {
            displayContractors(data.contractors);
            updatePagination(data.pagination);
            updateHeaderStats(data.pagination.total);
        } else {
            showError(grid, data.error || 'Failed to load contractors');
        }
    } catch (error) {
        console.error('Error loading contractors:', error);
        showError(grid, 'Network error. Please try again.');
    }
}

function displayContractors(contractors) {
    const grid = document.getElementById('contractorsGrid');
    
    if (contractors.length === 0) {
        grid.innerHTML = `
            <div style="grid-column: 1/-1; text-align: center; padding: 60px 20px;">
                <i class="fas fa-search" style="font-size: 4em; color: #ccc; margin-bottom: 20px;"></i>
                <h3 style="color: #666;">No contractors found</h3>
                <p style="color: #999;">Try adjusting your search criteria</p>
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
                        <div class="contractor-license">
                            <i class="fas fa-id-card"></i>
                            License: ${escapeHtml(contractor.license)}
                        </div>
                    ` : ''}
                </div>
            </div>
            
            <div class="contractor-stats">
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${contractor.total_jobs}</div>
                    <div class="contractor-stat-label">Total Jobs</div>
                </div>
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${contractor.active_jobs}</div>
                    <div class="contractor-stat-label">Active Jobs</div>
                </div>
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${contractor.unique_properties}</div>
                    <div class="contractor-stat-label">Properties</div>
                </div>
                <div class="contractor-stat">
                    <div class="contractor-stat-value">${formatCurrency(contractor.largest_project)}</div>
                    <div class="contractor-stat-label">Largest Project</div>
                </div>
            </div>
            
            <div class="contractor-meta">
                <span>
                    <i class="fas fa-calendar"></i>
                    ${formatDate(contractor.most_recent_job)}
                </span>
                <span>
                    <i class="fas fa-tools"></i>
                    ${contractor.job_types ? contractor.job_types.substring(0, 20) + '...' : 'N/A'}
                </span>
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
    // Load contractor data
    loadContractorProfile();
    
    // Tab switching
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.dataset.tab;
            switchTab(tabName);
        });
    });
    
    // Stat cards click to switch tabs
    document.querySelectorAll('.stat-card.clickable').forEach(card => {
        card.addEventListener('click', () => {
            const tabName = card.dataset.tab;
            if (tabName) {
                switchTab(tabName);
                // Scroll to tabs
                document.querySelector('.content-tabs').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
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
        } else {
            showError(document.querySelector('.profile-header'), data.error || 'Contractor not found');
        }
    } catch (error) {
        console.error('Error loading contractor profile:', error);
        showError(document.querySelector('.profile-header'), 'Failed to load contractor profile');
    }
}

function displayContractorStats(contractor) {
    // Update header
    document.getElementById('contractorName').textContent = contractor.contractor_name;
    document.getElementById('contractorLicense').textContent = contractor.license ? 
        `License: ${contractor.license}` : 'License: Not Available';
    
    if (contractor.most_recent_job) {
        document.getElementById('mostRecentJob').innerHTML = `
            <i class="fas fa-calendar"></i> ${formatDate(contractor.most_recent_job)}
        `;
    }
    
    if (contractor.job_types) {
        document.getElementById('jobTypes').innerHTML = `
            <i class="fas fa-briefcase"></i> ${contractor.job_types}
        `;
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
        permitsList.innerHTML = '<p style="text-align: center; color: #999;">No permits found</p>';
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
        buildingsList.innerHTML = '<p style="text-align: center; color: #999;">No buildings found</p>';
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

function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });
    
    // Update tab content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `${tabName}Tab`);
    });
    
    // Update stat cards active state
    document.querySelectorAll('.stat-card').forEach(card => {
        card.classList.toggle('active', card.dataset.tab === tabName);
    });
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
    // Find the profile container or create error in the provided container
    const errorHTML = `
        <div style="display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 80px 20px; background: white; border-radius: 16px; margin: 40px auto; max-width: 600px;">
            <div style="width: 80px; height: 80px; background: linear-gradient(135deg, #ff4757 0%, #ff6348 100%); border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-bottom: 24px;">
                <i class="fas fa-exclamation-circle" style="font-size: 2.5em; color: white;"></i>
            </div>
            <h2 style="color: #1a1a1a; margin: 0 0 12px 0;">Error</h2>
            <p style="color: #666; font-size: 1.1em; margin: 0;">${escapeHtml(message)}</p>
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

function exportContractors() {
    alert('Export functionality coming soon!');
}

console.log('Contractors module loaded successfully');
