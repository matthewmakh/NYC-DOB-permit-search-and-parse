/**
 * Building Intelligence Profile - Social Media Style Interface
 * Comprehensive property data display with transparent risk scoring
 */

let buildingData = null;

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Format number as currency with commas and dollar sign
 */
function formatCurrency(amount) {
    if (!amount || amount === null || amount === undefined) return 'N/A';
    const num = typeof amount === 'string' ? parseFloat(amount) : amount;
    if (isNaN(num)) return 'N/A';
    return '$' + num.toLocaleString('en-US', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    });
}

/**
 * Format large numbers with K, M, B suffixes
 */
function formatLargeNumber(amount) {
    if (!amount || amount === null || amount === undefined) return 'N/A';
    const num = typeof amount === 'string' ? parseFloat(amount) : amount;
    if (isNaN(num)) return 'N/A';
    
    if (num >= 1000000000) {
        return '$' + (num / 1000000000).toFixed(2) + 'B';
    } else if (num >= 1000000) {
        return '$' + (num / 1000000).toFixed(2) + 'M';
    } else if (num >= 1000) {
        return '$' + (num / 1000).toFixed(1) + 'K';
    }
    return formatCurrency(num);
}

/**
 * Format regular numbers with commas (no dollar sign)
 */
function formatNumber(num) {
    if (!num || num === null || num === undefined) return 'N/A';
    const parsed = typeof num === 'string' ? parseFloat(num) : num;
    if (isNaN(parsed)) return num; // Return original if not a number
    return parsed.toLocaleString('en-US');
}

/**
 * Format phone number to (XXX) XXX-XXXX format
 */
function formatPhoneNumber(phone) {
    if (!phone) return 'N/A';
    
    // Remove all non-numeric characters
    const cleaned = String(phone).replace(/\D/g, '');
    
    // Format based on length
    if (cleaned.length === 10) {
        return `(${cleaned.slice(0, 3)}) ${cleaned.slice(3, 6)}-${cleaned.slice(6)}`;
    } else if (cleaned.length === 11 && cleaned[0] === '1') {
        // Handle +1 country code
        return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
    } else if (cleaned.length > 0) {
        // Return with dashes for other formats
        return cleaned;
    }
    
    return phone; // Return original if can't format
}

// ============================================================================
// INITIALIZATION
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Loading building profile for BBL:', BBL);
    
    // Setup tab navigation
    setupTabNavigation();
    
    // Setup modal
    setupRiskModal();
    
    // Load building data
    await loadBuildingProfile();
});

// ============================================================================
// DATA LOADING
// ============================================================================

async function loadBuildingProfile() {
    try {
        const response = await fetch(`/api/building-profile/${BBL}`);
        const data = await response.json();
        
        if (!data.success) {
            showError('Property not found or error loading data');
            return;
        }
        
        buildingData = data;
        console.log('Building data loaded:', buildingData);
        
        // Render all sections
        renderHeroSection();
        renderOverviewTab();
        renderFinancialsTab();
        renderOwnersTab();
        renderTransactionsTab();
        renderPermitsTab();
        renderViolationsTab();
        renderActivityTab();
        renderContactsTab();
        
        // Update tab badges
        updateTabBadges();
        
    } catch (error) {
        console.error('Error loading building profile:', error);
        showError('Failed to load building data');
    }
}

// ============================================================================
// UPDATE TAB BADGES
// ============================================================================

function updateTabBadges() {
    const { building, permits, transactions, contacts, activity_timeline } = buildingData;
    
    // Owners badge - count of owner sources
    const ownerCount = [
        building.current_owner_name,
        building.owner_name_rpad,
        building.owner_name_hpd,
        building.ecb_respondent_name
    ].filter(o => o).length;
    if (ownerCount > 0) {
        setBadge('owners-badge', ownerCount);
    }
    
    // Transactions badge
    if (transactions && transactions.length > 0) {
        setBadge('transactions-badge', transactions.length);
    }
    
    // Permits badge
    if (permits && permits.length > 0) {
        setBadge('permits-badge', permits.length);
    }
    
    // Violations badge - total violations across all types
    const totalViolations = (building.hpd_total_violations || 0) + 
                           (building.ecb_violation_count || 0) + 
                           (building.dob_violation_count || 0);
    if (totalViolations > 0) {
        setBadge('violations-badge', totalViolations);
    }
    
    // Activity badge
    if (activity_timeline && activity_timeline.length > 0) {
        setBadge('activity-badge', activity_timeline.length);
    }
    
    // Contacts badge
    const usefulContacts = contacts.filter(c => c.phone || c.permit_count);
    if (usefulContacts.length > 0) {
        setBadge('contacts-badge', usefulContacts.length);
    }
}

function setBadge(badgeId, count) {
    const badge = document.getElementById(badgeId);
    if (badge) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.classList.add('show');
    }
}

// ============================================================================
// HERO SECTION
// ============================================================================

function renderHeroSection() {
    const { building, building_class_description, owners, risk_assessment } = buildingData;
    
    // Address
    document.getElementById('building-address').textContent = building.address || 'Address Unknown';
    document.getElementById('bbl-display').textContent = building.bbl;
    
    // Risk Score with color coding
    const riskCard = document.getElementById('risk-score-card');
    const riskValue = document.getElementById('risk-score-value');
    const riskLabel = document.getElementById('risk-score-label');
    
    riskValue.textContent = risk_assessment.score;
    riskLabel.textContent = risk_assessment.label;
    riskCard.className = `risk-score-card risk-${risk_assessment.color}`;
    
    // Owner Sources (ALL 4 sources with attribution)
    const ownerSourcesEl = document.getElementById('owner-sources');
    ownerSourcesEl.innerHTML = '';
    
    const sourceLabels = {
        'pluto': 'NYC PLUTO Database',
        'rpad': 'Real Property Assessment (Tax Records)',
        'hpd': 'HPD Registered Owner',
        'ecb': 'ECB Violation Respondent'
    };
    
    Object.entries(owners).forEach(([source, name]) => {
        if (name) {
            const ownerItem = document.createElement('div');
            ownerItem.className = 'owner-item';
            ownerItem.innerHTML = `
                <span class="owner-source">${sourceLabels[source]}</span>
                <span class="owner-name">${name}</span>
            `;
            ownerSourcesEl.appendChild(ownerItem);
        }
    });
    
    // If no owners found
    if (ownerSourcesEl.children.length === 0) {
        ownerSourcesEl.innerHTML = '<div class="no-data">No owner information available</div>';
    }
}

// ============================================================================
// RISK SCORING MODAL
// ============================================================================

function setupRiskModal() {
    const modal = document.getElementById('risk-explanation-modal');
    const btn = document.getElementById('risk-explanation-btn');
    const closeBtn = document.querySelector('.modal-close');
    
    btn.onclick = () => {
        renderRiskExplanation();
        modal.style.display = 'block';
    };
    
    closeBtn.onclick = () => {
        modal.style.display = 'none';
    };
    
    window.onclick = (event) => {
        if (event.target === modal) {
            modal.style.display = 'none';
        }
    };
}

function renderRiskExplanation() {
    const { risk_assessment } = buildingData;
    const factorsList = document.getElementById('risk-factors-list');
    
    factorsList.innerHTML = '';
    
    if (risk_assessment.factors.length === 0) {
        factorsList.innerHTML = '<p class="no-risk-factors">✅ No significant risk factors identified for this property.</p>';
    } else {
        risk_assessment.factors.forEach(factor => {
            const factorCard = document.createElement('div');
            factorCard.className = `risk-factor-card severity-${factor.severity}`;
            factorCard.innerHTML = `
                <div class="risk-factor-header">
                    <span class="risk-factor-name">${factor.factor}</span>
                    <span class="risk-factor-points">+${factor.points} points</span>
                </div>
                <div class="risk-factor-details">${factor.details}</div>
            `;
            factorsList.appendChild(factorCard);
        });
    }
    
    document.getElementById('modal-risk-score').textContent = risk_assessment.score;
}

// ============================================================================
// TAB NAVIGATION
// ============================================================================

function setupTabNavigation() {
    const tabBtns = document.querySelectorAll('.tab-btn');
    
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.getAttribute('data-tab');
            switchTab(tabName);
        });
    });
}

function switchTab(tabName) {
    // Update buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('active');
        if (btn.getAttribute('data-tab') === tabName) {
            btn.classList.add('active');
        }
    });
    
    // Update content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });
    
    document.getElementById(`tab-${tabName}`).classList.add('active');
    
    // Auto-load detailed violations when violations tab is opened
    if (tabName === 'violations') {
        // Check if we have violations and haven't loaded details yet
        const detailsContainer = document.getElementById('detailed-violations');
        if (detailsContainer && detailsContainer.innerHTML === '') {
            const { building } = buildingData;
            const hasViolations = building.hpd_total_violations || building.ecb_violation_count || building.dob_violation_count;
            if (hasViolations) {
                loadHPDViolationDetails();
            }
        }
    }
}

// ============================================================================
// OVERVIEW TAB
// ============================================================================

function renderOverviewTab() {
    const { building, building_class_description, stats } = buildingData;
    
    // Building Basics
    const basicsEl = document.getElementById('building-basics');
    basicsEl.innerHTML = `
        <div class="info-row">
            <span class="info-label">Building Type:</span>
            <span class="info-value">
                <strong>${building.building_class || 'Unknown'}</strong> - ${building_class_description}
            </span>
        </div>
        <div class="info-row">
            <span class="info-label">Borough:</span>
            <span class="info-value">${getBoroughName(building.borough)}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Year Built:</span>
            <span class="info-value">${building.year_built || 'Unknown'}</span>
        </div>
        ${building.year_altered ? `
        <div class="info-row">
            <span class="info-label">Year Altered:</span>
            <span class="info-value">${building.year_altered}</span>
        </div>
        ` : ''}
        <div class="info-row">
            <span class="info-label">Units:</span>
            <span class="info-value">${building.total_units ? formatNumber(building.total_units) : 'Unknown'}</span>
        </div>
        <div class="info-row">
            <span class="info-label">Square Footage:</span>
            <span class="info-value">${building.building_sqft ? formatNumber(building.building_sqft) + ' sq ft' : 'Unknown'}</span>
        </div>
    `;
    
    // Property Stats
    const statsEl = document.getElementById('property-stats');
    statsEl.innerHTML = `
        <div class="stat-item">
            <div class="stat-value">${stats.total_permits ? formatNumber(stats.total_permits) : 0}</div>
            <div class="stat-label">Permits Filed</div>
        </div>
        <div class="stat-item">
            <div class="stat-value">${stats.total_transactions ? formatNumber(stats.total_transactions) : 0}</div>
            <div class="stat-label">Transactions</div>
        </div>
        <div class="stat-item">
            <div class="stat-value">${stats.total_violations ? formatNumber(stats.total_violations) : 0}</div>
            <div class="stat-label">Violations</div>
        </div>
        <div class="stat-item">
            <div class="stat-value">${stats.total_contacts ? formatNumber(stats.total_contacts) : 0}</div>
            <div class="stat-label">Contacts</div>
        </div>
        <div class="stat-item">
            <div class="stat-value">${stats.years_owned ? stats.years_owned + ' yrs' : 'N/A'}</div>
            <div class="stat-label">Current Ownership</div>
        </div>
    `;
    
    // Quick Metrics
    const metricsEl = document.getElementById('quick-metrics');
    const metrics = [];
    
    if (building.is_cash_purchase !== null) {
        metrics.push({
            label: 'Purchase Type',
            value: building.is_cash_purchase ? '💵 Cash Purchase' : '🏦 Financed',
            class: building.is_cash_purchase ? 'metric-highlight' : ''
        });
    }
    
    if (building.financing_ratio !== null) {
        metrics.push({
            label: 'Financing Ratio',
            value: `${(building.financing_ratio * 100).toFixed(1)}%`,
            class: ''
        });
    }
    
    if (building.sale_price) {
        metrics.push({
            label: 'Last Sale Price',
            value: '$' + formatNumber(building.sale_price),
            class: ''
        });
    }
    
    if (building.assessed_total_value) {
        metrics.push({
            label: 'Assessed Value',
            value: '$' + formatNumber(building.assessed_total_value),
            class: ''
        });
    }
    
    metricsEl.innerHTML = metrics.map(m => `
        <div class="metric-row ${m.class}">
            <span class="metric-label">${m.label}:</span>
            <span class="metric-value">${m.value}</span>
        </div>
    `).join('');
}

// ============================================================================
// FINANCIALS TAB
// ============================================================================

function renderFinancialsTab() {
    const { building } = buildingData;
    const container = document.getElementById('financials-content');
    
    let html = '<div class="financials-grid">';
    
    // Sale Information
    if (building.sale_price || building.sale_date) {
        html += `
        <div class="financial-card">
            <h4>Last Sale</h4>
            <div class="financial-rows">
                ${building.sale_price ? `<div class="fin-row"><span>Price:</span><span>$${formatNumber(building.sale_price)}</span></div>` : ''}
                ${building.sale_date ? `<div class="fin-row"><span>Date:</span><span>${formatDate(building.sale_date)}</span></div>` : ''}
                ${building.sale_buyer_primary ? `<div class="fin-row"><span>Buyer:</span><span>${building.sale_buyer_primary}</span></div>` : ''}
                ${building.sale_seller_primary ? `<div class="fin-row"><span>Seller:</span><span>${building.sale_seller_primary}</span></div>` : ''}
            </div>
        </div>`;
    }
    
    // Mortgage Information
    if (building.mortgage_amount) {
        html += `
        <div class="financial-card">
            <h4>Current Mortgage</h4>
            <div class="financial-rows">
                <div class="fin-row"><span>Amount:</span><span>$${formatNumber(building.mortgage_amount)}</span></div>
                ${building.mortgage_date ? `<div class="fin-row"><span>Date:</span><span>${formatDate(building.mortgage_date)}</span></div>` : ''}
                ${building.mortgage_lender_primary ? `<div class="fin-row"><span>Lender:</span><span>${building.mortgage_lender_primary}</span></div>` : ''}
            </div>
        </div>`;
    }
    
    // Assessment Values
    if (building.assessed_total_value || building.assessed_land_value) {
        html += `
        <div class="financial-card">
            <h4>Tax Assessment</h4>
            <div class="financial-rows">
                ${building.assessed_total_value ? `<div class="fin-row"><span>Total Value:</span><span>$${formatNumber(building.assessed_total_value)}</span></div>` : ''}
                ${building.assessed_land_value ? `<div class="fin-row"><span>Land Value:</span><span>$${formatNumber(building.assessed_land_value)}</span></div>` : ''}
            </div>
        </div>`;
    }
    
    // Tax Liens & ECB
    const hasLienData = building.has_tax_delinquency || building.ecb_total_balance;
    if (hasLienData) {
        html += `<div class="financial-card alert-card">
            <h4>⚠️ Outstanding Liabilities</h4>
            <div class="financial-rows">`;
        
        if (building.has_tax_delinquency) {
            html += `
                <div class="fin-row alert">
                    <span>Tax Delinquency:</span>
                    <span>${building.tax_delinquency_count} notice(s) ${building.tax_delinquency_water_only ? '(Water Only)' : '(Property Tax)'}</span>
                </div>`;
        }
        
        if (building.ecb_total_balance && building.ecb_total_balance > 0) {
            html += `
                <div class="fin-row alert">
                    <span>ECB Outstanding:</span>
                    <span class="alert-value">$${formatNumber(building.ecb_total_balance)}</span>
                </div>
                <div class="fin-row">
                    <span>Open Violations:</span>
                    <span>${building.ecb_open_violations || 0}</span>
                </div>`;
        }
        
        html += `</div></div>`;
    }
    
    html += '</div>';
    
    container.innerHTML = html;
}

// ============================================================================
// OWNERS TAB
// ============================================================================

function renderOwnersTab() {
    const { owners, parties } = buildingData;
    const container = document.getElementById('owners-content');
    
    let html = '<div class="owners-list">';
    
    // Current Owners (All Sources)
    html += '<h4>Current Owner Information</h4>';
    html += '<div class="current-owners">';
    
    const sourceInfo = {
        'pluto': { label: 'NYC PLUTO Database', icon: '🗺️' },
        'rpad': { label: 'Tax Assessment Records', icon: '💰' },
        'hpd': { label: 'HPD Registered Owner', icon: '🏠' },
        'ecb': { label: 'ECB Violation Respondent', icon: '⚖️' }
    };
    
    Object.entries(owners).forEach(([source, name]) => {
        if (name) {
            const info = sourceInfo[source];
            html += `
            <div class="owner-source-card">
                <div class="owner-source-icon">${info.icon}</div>
                <div class="owner-source-info">
                    <div class="owner-source-label">${info.label}</div>
                    <div class="owner-source-name">${name}</div>
                </div>
            </div>`;
        }
    });
    
    html += '</div>';
    
    // Historical Owners (from ACRIS parties)
    if (parties && parties.length > 0) {
        const buyers = parties.filter(p => p.party_type === 'buyer');
        const sellers = parties.filter(p => p.party_type === 'seller');
        
        if (sellers.length > 0) {
            html += '<h4>Previous Owners (ACRIS History)</h4>';
            html += '<div class="historical-owners">';
            
            // Get unique sellers
            const uniqueSellers = [...new Set(sellers.map(s => s.party_name))];
            uniqueSellers.slice(0, 10).forEach(sellerName => {
                const sellerData = sellers.find(s => s.party_name === sellerName);
                html += `
                <div class="historical-owner-card">
                    <div class="ho-name">${sellerName}</div>
                    ${sellerData.recorded_date ? `<div class="ho-date">Sold: ${formatDate(sellerData.recorded_date)}</div>` : ''}
                    ${sellerData.address_1 ? `<div class="ho-address">${sellerData.address_1}, ${sellerData.city}, ${sellerData.state} ${sellerData.zip_code}</div>` : ''}
                </div>`;
            });
            
            html += '</div>';
        }
    }
    
    html += '</div>';
    container.innerHTML = html;
}

// ============================================================================
// TRANSACTIONS TAB
// ============================================================================

function renderTransactionsTab() {
    const { transactions, parties } = buildingData;
    const container = document.getElementById('transactions-content');
    
    if (!transactions || transactions.length === 0) {
        container.innerHTML = '<div class="no-data">No ACRIS transaction history available</div>';
        return;
    }
    
    // Store data globally for filtering
    window.transactionsData = transactions;
    window.partiesData = parties;
    
    // Get unique document types
    const docTypes = [...new Set(transactions.map(t => t.doc_type).filter(Boolean))];
    
    let html = `
    <div class="transactions-controls">
        <div class="filter-group">
            <label>Document Type:</label>
            <select id="filter-doc-type" onchange="filterTransactions()">
                <option value="all">All</option>
                ${docTypes.map(type => `<option value="${type}">${getDocTypeLabel(type)}</option>`).join('')}
            </select>
        </div>
        <div class="filter-group">
            <label>Amount:</label>
            <select id="filter-amount" onchange="filterTransactions()">
                <option value="all">All</option>
                <option value="with-amount">With Amount</option>
                <option value="no-amount">No Amount</option>
            </select>
        </div>
        <div class="filter-group">
            <label>Sort by:</label>
            <select id="sort-transactions" onchange="filterTransactions()">
                <option value="date-desc">Date (Newest First)</option>
                <option value="date-asc">Date (Oldest First)</option>
                <option value="amount-desc">Amount (Highest First)</option>
                <option value="amount-asc">Amount (Lowest First)</option>
                <option value="doc-type">Document Type</option>
            </select>
        </div>
    </div>
    <div class="transactions-list" id="transactions-list-container">`;
    
    transactions.forEach(txn => {
        // Get parties for this transaction
        const txnParties = parties.filter(p => p.document_id === txn.document_id);
        const buyers = txnParties.filter(p => p.party_type === 'buyer');
        const sellers = txnParties.filter(p => p.party_type === 'seller');
        const lenders = txnParties.filter(p => p.party_type === 'lender');
        
        html += `
        <div class="transaction-card" data-doc-type="${txn.doc_type}" data-amount="${txn.doc_amount || 0}">
            <div class="txn-header">
                <span class="txn-type">${getDocTypeLabel(txn.doc_type)}</span>
                <span class="txn-date">${formatDate(txn.recorded_date)}</span>
            </div>
            ${txn.doc_amount ? `<div class="txn-amount">${formatCurrency(txn.doc_amount)}</div>` : ''}
            <div class="txn-details">
                <div class="txn-detail-row"><span>Document ID:</span><span>${txn.document_id}</span></div>
                ${txn.crfn ? `<div class="txn-detail-row"><span>CRFN:</span><span>${txn.crfn}</span></div>` : ''}
            </div>`;
        
        // Show parties
        if (buyers.length > 0) {
            html += '<div class="txn-parties"><strong>Buyers:</strong> ' + buyers.map(b => b.party_name).join(', ') + '</div>';
        }
        if (sellers.length > 0) {
            html += '<div class="txn-parties"><strong>Sellers:</strong> ' + sellers.map(s => s.party_name).join(', ') + '</div>';
        }
        if (lenders.length > 0) {
            html += '<div class="txn-parties"><strong>Lenders:</strong> ' + lenders.map(l => l.party_name).join(', ') + '</div>';
        }
        
        html += '</div>';
    });
    
    html += '</div>';
    container.innerHTML = html;
}

function filterTransactions() {
    if (!window.transactionsData) return;
    
    const docTypeFilter = document.getElementById('filter-doc-type').value;
    const amountFilter = document.getElementById('filter-amount').value;
    const sortOption = document.getElementById('sort-transactions').value;
    
    // Filter transactions
    let filtered = window.transactionsData.filter(txn => {
        // Document type filter
        if (docTypeFilter !== 'all' && txn.doc_type !== docTypeFilter) return false;
        
        // Amount filter
        if (amountFilter === 'with-amount' && (!txn.doc_amount || txn.doc_amount === 0)) return false;
        if (amountFilter === 'no-amount' && txn.doc_amount && txn.doc_amount > 0) return false;
        
        return true;
    });
    
    // Sort transactions
    filtered.sort((a, b) => {
        switch(sortOption) {
            case 'date-desc':
                return new Date(b.recorded_date || 0) - new Date(a.recorded_date || 0);
            case 'date-asc':
                return new Date(a.recorded_date || 0) - new Date(b.recorded_date || 0);
            case 'amount-desc':
                return (b.doc_amount || 0) - (a.doc_amount || 0);
            case 'amount-asc':
                return (a.doc_amount || 0) - (b.doc_amount || 0);
            case 'doc-type':
                return (a.doc_type || '').localeCompare(b.doc_type || '');
            default:
                return 0;
        }
    });
    
    // Render filtered transactions
    const container = document.getElementById('transactions-list-container');
    if (filtered.length === 0) {
        container.innerHTML = '<div class="no-data">No transactions match the selected filters</div>';
        return;
    }
    
    let html = '';
    filtered.forEach(txn => {
        // Get parties for this transaction
        const txnParties = window.partiesData.filter(p => p.document_id === txn.document_id);
        const buyers = txnParties.filter(p => p.party_type === 'buyer');
        const sellers = txnParties.filter(p => p.party_type === 'seller');
        const lenders = txnParties.filter(p => p.party_type === 'lender');
        
        html += `
        <div class="transaction-card" data-doc-type="${txn.doc_type}" data-amount="${txn.doc_amount || 0}">
            <div class="txn-header">
                <span class="txn-type">${getDocTypeLabel(txn.doc_type)}</span>
                <span class="txn-date">${formatDate(txn.recorded_date)}</span>
            </div>
            ${txn.doc_amount ? `<div class="txn-amount">${formatCurrency(txn.doc_amount)}</div>` : ''}
            <div class="txn-details">
                <div class="txn-detail-row"><span>Document ID:</span><span>${txn.document_id}</span></div>
                ${txn.crfn ? `<div class="txn-detail-row"><span>CRFN:</span><span>${txn.crfn}</span></div>` : ''}
            </div>`;
        
        // Show parties
        if (buyers.length > 0) {
            html += '<div class="txn-parties"><strong>Buyers:</strong> ' + buyers.map(b => b.party_name).join(', ') + '</div>';
        }
        if (sellers.length > 0) {
            html += '<div class="txn-parties"><strong>Sellers:</strong> ' + sellers.map(s => s.party_name).join(', ') + '</div>';
        }
        if (lenders.length > 0) {
            html += '<div class="txn-parties"><strong>Lenders:</strong> ' + lenders.map(l => l.party_name).join(', ') + '</div>';
        }
        
        html += '</div>';
    });
    container.innerHTML = html;
}

// ============================================================================
// PERMITS TAB
// ============================================================================

function renderPermitsTab() {
    const { permits } = buildingData;
    const container = document.getElementById('permits-content');
    
    if (!permits || permits.length === 0) {
        container.innerHTML = '<div class="no-data">No permits filed for this property</div>';
        return;
    }
    
    // Store permits globally for filtering
    window.permitsData = permits;
    
    // Get unique job types
    const jobTypes = [...new Set(permits.map(p => p.job_type).filter(Boolean))];
    
    let html = `
    <div class="permits-controls">
        <div class="filter-group">
            <label>Job Type:</label>
            <select id="filter-job-type" onchange="filterPermits()">
                <option value="all">All</option>
                ${jobTypes.map(type => `<option value="${type}">${type}</option>`).join('')}
            </select>
        </div>
        <div class="filter-group">
            <label>Sort by:</label>
            <select id="sort-permits" onchange="filterPermits()">
                <option value="date-desc">Date (Newest First)</option>
                <option value="date-asc">Date (Oldest First)</option>
                <option value="job-type">Job Type</option>
            </select>
        </div>
    </div>
    <div class="permits-list" id="permits-list-container">`;
    
    permits.forEach((permit, index) => {
        html += `
        <div class="permit-card" onclick="showPermitDetails(${index})" data-index="${index}">
            <div class="permit-header">
                <span class="permit-type">${permit.job_type || 'Permit'}</span>
                <span class="permit-date">${formatDate(permit.issue_date)}</span>
            </div>
            <div class="permit-no">Permit #${permit.permit_no}</div>
            ${permit.work_type ? `<div class="permit-work-type">${permit.work_type}</div>` : ''}
            <div class="permit-details">
                ${permit.applicant ? `<div class="permit-detail-row"><span>Applicant:</span><span>${permit.applicant}</span></div>` : ''}
                ${permit.permittee_business_name ? `<div class="permit-detail-row"><span>Contractor:</span><span>${permit.permittee_business_name}</span></div>` : ''}
            </div>
            <div class="permit-click-hint">Click for details →</div>
        </div>`;
    });
    
    html += '</div>';
    container.innerHTML = html;
}

function filterPermits() {
    if (!window.permitsData) return;
    
    const jobTypeFilter = document.getElementById('filter-job-type').value;
    const sortOption = document.getElementById('sort-permits').value;
    
    // Filter permits
    let filtered = window.permitsData.filter(permit => {
        if (jobTypeFilter !== 'all' && permit.job_type !== jobTypeFilter) return false;
        return true;
    });
    
    // Sort permits
    filtered.sort((a, b) => {
        switch(sortOption) {
            case 'date-desc':
                return new Date(b.issue_date || 0) - new Date(a.issue_date || 0);
            case 'date-asc':
                return new Date(a.issue_date || 0) - new Date(b.issue_date || 0);
            case 'job-type':
                return (a.job_type || '').localeCompare(b.job_type || '');
            default:
                return 0;
        }
    });
    
    // Render filtered permits
    const container = document.getElementById('permits-list-container');
    if (filtered.length === 0) {
        container.innerHTML = '<div class="no-data">No permits match the selected filters</div>';
        return;
    }
    
    let html = '';
    filtered.forEach((permit, index) => {
        // Find original index for showPermitDetails
        const originalIndex = window.permitsData.indexOf(permit);
        html += `
        <div class="permit-card" onclick="showPermitDetails(${originalIndex})" data-index="${originalIndex}">
            <div class="permit-header">
                <span class="permit-type">${permit.job_type || 'Permit'}</span>
                <span class="permit-date">${formatDate(permit.issue_date)}</span>
            </div>
            <div class="permit-no">Permit #${permit.permit_no}</div>
            ${permit.work_type ? `<div class="permit-work-type">${permit.work_type}</div>` : ''}
            <div class="permit-details">
                ${permit.applicant ? `<div class="permit-detail-row"><span>Applicant:</span><span>${permit.applicant}</span></div>` : ''}
                ${permit.permittee_business_name ? `<div class="permit-detail-row"><span>Contractor:</span><span>${permit.permittee_business_name}</span></div>` : ''}
            </div>
            <div class="permit-click-hint">Click for details →</div>
        </div>`;
    });
    container.innerHTML = html;
}

function showPermitDetails(index) {
    const permit = buildingData.permits[index];
    
    // Helper function to add row only if value exists
    const addRow = (label, value) => {
        if (value && value !== 'N/A' && value !== null && value !== undefined) {
            return `<div class="detail-row"><span class="detail-label">${label}:</span><span class="detail-value">${value}</span></div>`;
        }
        return '';
    };
    
    let html = `
    <div class="permit-detail-modal-content">
        <h2>📋 Permit #${permit.permit_no}</h2>
        <div class="permit-detail-grid">`;
    
    // Basic Information - always show
    html += `
            <div class="detail-section">
                <h3>Basic Information</h3>
                ${addRow('Permit Number', permit.permit_no)}
                ${addRow('Job Type', permit.job_type)}
                ${addRow('Work Type', permit.work_type)}
                ${addRow('Issue Date', formatDate(permit.issue_date))}
                ${addRow('Permit Status', permit.permit_status)}
                ${addRow('Filing Status', permit.filing_status)}
            </div>`;
    
    // Property Details - only if has data
    const hasPropertyDetails = permit.address || permit.use_type || permit.stories || permit.total_units;
    if (hasPropertyDetails) {
        html += `
            <div class="detail-section">
                <h3>Property Details</h3>
                ${addRow('Address', permit.address)}
                ${addRow('Use Type', permit.use_type)}
                ${addRow('Stories', permit.stories ? formatNumber(permit.stories) : null)}
                ${addRow('Total Units', permit.total_units ? formatNumber(permit.total_units) : null)}
            </div>`;
    }
    
    // Applicant - only if has data
    if (permit.applicant) {
        html += `
            <div class="detail-section">
                <h3>Applicant</h3>
                ${addRow('Name', permit.applicant)}
            </div>`;
    }
    
    // Permittee - only if has data
    const hasPermitteeData = permit.permittee_business_name || permit.permittee_license_type || 
                             permit.permittee_license_number || permit.permittee_phone;
    if (hasPermitteeData) {
        html += `
            <div class="detail-section">
                <h3>Permittee</h3>
                ${addRow('Business Name', permit.permittee_business_name)}
                ${addRow('License Type', permit.permittee_license_type)}
                ${addRow('License #', permit.permittee_license_number)}
                ${addRow('Phone', permit.permittee_phone ? formatPhoneNumber(permit.permittee_phone) : null)}
            </div>`;
    }
    
    // Owner - only if has data
    if (permit.owner_business_name || permit.owner_phone) {
        html += `
            <div class="detail-section">
                <h3>Owner</h3>
                ${addRow('Business Name', permit.owner_business_name)}
                ${addRow('Phone', permit.owner_phone ? formatPhoneNumber(permit.owner_phone) : null)}
            </div>`;
    }
    
    // Superintendent - only if has data
    if (permit.superintendent_business_name) {
        html += `
            <div class="detail-section">
                <h3>Superintendent</h3>
                ${addRow('Business Name', permit.superintendent_business_name)}
            </div>`;
    }
    
    // Site Safety Manager - only if has data
    if (permit.site_safety_mgr_business_name) {
        html += `
            <div class="detail-section">
                <h3>Site Safety Manager</h3>
                ${addRow('Business Name', permit.site_safety_mgr_business_name)}
            </div>`;
    }
    
    html += `
        </div>
        
        <div class="permit-modal-actions">
            ${permit.link ? `<a href="${permit.link}" target="_blank" class="btn-view-dob">View on DOB Website →</a>` : ''}
            <button onclick="closePermitModal()" class="btn-close-modal">Close</button>
        </div>
    </div>`;
    
    document.getElementById('permit-modal').innerHTML = html;
    document.getElementById('permit-modal').style.display = 'flex';
}

function closePermitModal() {
    document.getElementById('permit-modal').style.display = 'none';
}

// Close modal when clicking outside
window.addEventListener('click', function(event) {
    const modal = document.getElementById('permit-modal');
    if (event.target === modal) {
        closePermitModal();
    }
});

// ============================================================================
// VIOLATIONS TAB
// ============================================================================

function renderViolationsTab() {
    const { building } = buildingData;
    const container = document.getElementById('violations-content');
    
    const hasViolations = building.ecb_violation_count || building.dob_violation_count || building.hpd_total_violations;
    
    if (!hasViolations) {
        container.innerHTML = '<div class="no-data">✅ No violations on record</div>';
        return;
    }
    
    let html = '<div class="violations-grid">';
    
    // ECB Violations
    if (building.ecb_violation_count && building.ecb_violation_count > 0) {
        html += `
        <div class="violation-card ${building.ecb_total_balance > 0 ? 'violation-alert' : ''}">
            <h4>⚖️ ECB Violations</h4>
            <div class="violation-stats">
                <div class="viol-stat">
                    <div class="viol-stat-value">${building.ecb_violation_count}</div>
                    <div class="viol-stat-label">Total Violations</div>
                </div>
                <div class="viol-stat">
                    <div class="viol-stat-value">${building.ecb_open_violations || 0}</div>
                    <div class="viol-stat-label">Open</div>
                </div>
            </div>
            ${building.ecb_total_balance ? `
            <div class="violation-financial">
                <div class="viol-fin-row">
                    <span>Total Balance:</span>
                    <span class="viol-amount-alert">$${formatNumber(building.ecb_total_balance)}</span>
                </div>
                ${building.ecb_total_penalty ? `<div class="viol-fin-row"><span>Total Penalty:</span><span>$${formatNumber(building.ecb_total_penalty)}</span></div>` : ''}
                ${building.ecb_amount_paid ? `<div class="viol-fin-row"><span>Amount Paid:</span><span>$${formatNumber(building.ecb_amount_paid)}</span></div>` : ''}
            </div>` : ''}
            ${building.ecb_most_recent_hearing_date ? `
            <div class="violation-hearing">
                <div class="viol-hearing-label">Most Recent Hearing:</div>
                <div class="viol-hearing-date">${formatDate(building.ecb_most_recent_hearing_date)}</div>
                ${building.ecb_most_recent_hearing_status ? `<div class="viol-hearing-status">${building.ecb_most_recent_hearing_status}</div>` : ''}
            </div>` : ''}
        </div>`;
    }
    
    // DOB Violations
    if (building.dob_violation_count && building.dob_violation_count > 0) {
        html += `
        <div class="violation-card">
            <h4>🏗️ DOB Violations</h4>
            <div class="violation-stats">
                <div class="viol-stat">
                    <div class="viol-stat-value">${building.dob_violation_count}</div>
                    <div class="viol-stat-label">Total Violations</div>
                </div>
                <div class="viol-stat">
                    <div class="viol-stat-value">${building.dob_open_violations || 0}</div>
                    <div class="viol-stat-label">Open</div>
                </div>
            </div>
        </div>`;
    }
    
    // HPD Violations
    if (building.hpd_total_violations && building.hpd_total_violations > 0) {
        html += `
        <div class="violation-card">
            <h4>🏠 HPD Violations</h4>
            <div class="violation-stats">
                <div class="viol-stat">
                    <div class="viol-stat-value">${building.hpd_total_violations}</div>
                    <div class="viol-stat-label">Total Violations</div>
                </div>
            </div>
            ${building.hpd_total_complaints ? `
            <div class="violation-extra">
                <span>Complaints:</span>
                <span>${building.hpd_total_complaints}</span>
            </div>` : ''}
        </div>`;
    }
    
    html += '</div>';
    
    // Container for detailed violations (will auto-load when tab is clicked)
    html += '<div id="detailed-violations" style="margin-top: 20px;"></div>';
    
    container.innerHTML = html;
}

// ============================================================================
// LOAD DETAILED HPD VIOLATIONS
// ============================================================================

async function loadHPDViolationDetails() {
    const detailsContainer = document.getElementById('detailed-violations');
    detailsContainer.innerHTML = '<div class="loading">Loading detailed violations...</div>';
    
    try {
        const response = await fetch(`/api/property/${BBL}/violations`);
        const data = await response.json();
        
        console.log('Violations API response:', data);
        
        if (!data.success) {
            detailsContainer.innerHTML = `<div class="error">Failed to load detailed violations: ${data.error || 'Unknown error'}</div>`;
            return;
        }
        
        if (data.violations.length === 0) {
            detailsContainer.innerHTML = '<div class="no-data">No detailed violations found</div>';
            return;
        }
        
        // Store violations data globally for filtering
        window.violationsData = data.violations;
        
        let html = '<div class="detailed-violations-container">';
        html += `<h3>📋 Detailed HPD Violations (${data.total_count} total)</h3>`;
        
        // Filters and sorting controls
        html += `
        <div class="violations-controls">
            <div class="filter-group">
                <label>Status:</label>
                <select id="filter-status" onchange="filterViolations()">
                    <option value="all">All</option>
                    <option value="open">Open Only</option>
                    <option value="closed">Closed Only</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Class:</label>
                <select id="filter-class" onchange="filterViolations()">
                    <option value="all">All</option>
                    <option value="A">Class A</option>
                    <option value="B">Class B</option>
                    <option value="C">Class C</option>
                    <option value="I">Class I</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Sort by:</label>
                <select id="sort-violations" onchange="filterViolations()">
                    <option value="date-desc">Date (Newest First)</option>
                    <option value="date-asc">Date (Oldest First)</option>
                    <option value="class-asc">Class (A-Z)</option>
                    <option value="class-desc">Class (Z-A)</option>
                </select>
            </div>
        </div>`;
        
        // Individual violations
        html += '<div class="violations-list" id="violations-list-container">';
        data.violations.forEach(v => {
            const statusClass = v.is_open ? 'violation-open' : 'violation-closed';
            html += `
            <div class="violation-detail-card ${statusClass}">
                <div class="viol-detail-header">
                    <span class="viol-id">ID: ${v.violation_id || 'N/A'}</span>
                    <span class="viol-class">Class ${v.class || 'Unknown'}</span>
                    <span class="viol-status ${v.is_open ? 'status-open' : 'status-closed'}">
                        ${v.current_status || 'Unknown'}
                    </span>
                </div>
                <div class="viol-detail-description">
                    ${v.description || 'No description available'}
                </div>
                <div class="viol-detail-info">
                    ${v.inspection_date ? `<div><strong>Inspection:</strong> ${formatDate(v.inspection_date)}</div>` : ''}
                    ${v.apartment !== 'N/A' ? `<div><strong>Unit:</strong> ${v.apartment}</div>` : ''}
                    ${v.story !== 'N/A' ? `<div><strong>Floor:</strong> ${v.story}</div>` : ''}
                    ${v.order_number ? `<div><strong>Order:</strong> ${v.order_number}</div>` : ''}
                </div>
            </div>`;
        });
        html += '</div>';
        
        if (data.has_more) {
            html += '<div class="note">Showing first 100 violations. Total: ' + data.total_count + '</div>';
        }
        
        html += '</div>';
        detailsContainer.innerHTML = html;
        
    } catch (error) {
        console.error('Error loading violations:', error);
        detailsContainer.innerHTML = '<div class="error">Error loading violations: ' + error.message + '</div>';
    }
}

function filterViolations() {
    if (!window.violationsData) return;
    
    const statusFilter = document.getElementById('filter-status').value;
    const classFilter = document.getElementById('filter-class').value;
    const sortOption = document.getElementById('sort-violations').value;
    
    // Filter violations
    let filtered = window.violationsData.filter(v => {
        // Status filter
        if (statusFilter === 'open' && !v.is_open) return false;
        if (statusFilter === 'closed' && v.is_open) return false;
        
        // Class filter
        if (classFilter !== 'all' && v.class !== classFilter) return false;
        
        return true;
    });
    
    // Sort violations
    filtered.sort((a, b) => {
        switch(sortOption) {
            case 'date-desc':
                return new Date(b.inspection_date || 0) - new Date(a.inspection_date || 0);
            case 'date-asc':
                return new Date(a.inspection_date || 0) - new Date(b.inspection_date || 0);
            case 'class-asc':
                return (a.class || '').localeCompare(b.class || '');
            case 'class-desc':
                return (b.class || '').localeCompare(a.class || '');
            default:
                return 0;
        }
    });
    
    // Render filtered violations
    const container = document.getElementById('violations-list-container');
    if (filtered.length === 0) {
        container.innerHTML = '<div class="no-data">No violations match the selected filters</div>';
        return;
    }
    
    let html = '';
    filtered.forEach(v => {
        const statusClass = v.is_open ? 'violation-open' : 'violation-closed';
        html += `
        <div class="violation-detail-card ${statusClass}">
            <div class="viol-detail-header">
                <span class="viol-id">ID: ${v.violation_id || 'N/A'}</span>
                <span class="viol-class">Class ${v.class || 'Unknown'}</span>
                <span class="viol-status ${v.is_open ? 'status-open' : 'status-closed'}">
                    ${v.current_status || 'Unknown'}
                </span>
            </div>
            <div class="viol-detail-description">
                ${v.description || 'No description available'}
            </div>
            <div class="viol-detail-info">
                ${v.inspection_date ? `<div><strong>Inspection:</strong> ${formatDate(v.inspection_date)}</div>` : ''}
                ${v.apartment !== 'N/A' ? `<div><strong>Unit:</strong> ${v.apartment}</div>` : ''}
                ${v.story !== 'N/A' ? `<div><strong>Floor:</strong> ${v.story}</div>` : ''}
                ${v.order_number ? `<div><strong>Order:</strong> ${v.order_number}</div>` : ''}
            </div>
        </div>`;
    });
    container.innerHTML = html;
}

// ============================================================================
// ACTIVITY TAB
// ============================================================================

function renderActivityTab() {
    const { activity_timeline } = buildingData;
    const container = document.getElementById('activity-feed');
    
    if (!activity_timeline || activity_timeline.length === 0) {
        container.innerHTML = '<div class="no-data">No activity recorded</div>';
        return;
    }
    
    let html = '<div class="activity-timeline">';
    
    activity_timeline.forEach(event => {
        html += `
        <div class="activity-item">
            <div class="activity-icon">${event.icon}</div>
            <div class="activity-content">
                <div class="activity-date">${formatDate(event.date)}</div>
                <div class="activity-title">${event.title}</div>
                <div class="activity-description">${event.description}</div>
            </div>
        </div>`;
    });
    
    html += '</div>';
    container.innerHTML = html;
}

// ============================================================================
// CONTACTS TAB
// ============================================================================

function renderContactsTab() {
    const { contacts } = buildingData;
    const container = document.getElementById('contacts-directory');
    
    if (!contacts || contacts.length === 0) {
        container.innerHTML = '<div class="no-data">No contacts available</div>';
        return;
    }
    
    // Filter to only contacts with phone numbers or useful info
    const usefulContacts = contacts.filter(c => c.phone || c.permit_count);
    
    if (usefulContacts.length === 0) {
        container.innerHTML = `
            <div class="no-data">
                <p>📋 <strong>${contacts.length} contractors</strong> have worked on this property</p>
                <p>Phone numbers not available in current dataset</p>
            </div>`;
        return;
    }
    
    let html = '<div class="contacts-list">';
    
    usefulContacts.forEach(contact => {
        html += `
        <div class="contact-card">
            <div class="contact-name">${contact.name}</div>
            <div class="contact-role">${contact.role}</div>
            ${contact.phone ? `
                <div class="contact-phone">
                    📞 ${formatPhoneNumber(contact.phone)}
                    ${contact.is_mobile ? ' <span class="mobile-badge">📱 Mobile</span>' : ''}
                    ${contact.line_type ? ` <span class="line-type-badge">${contact.line_type}</span>` : ''}
                </div>
            ` : ''}
            ${contact.carrier ? `<div class="contact-carrier">Carrier: ${contact.carrier}</div>` : ''}
            ${contact.license ? `<div class="contact-license">License: ${contact.license}</div>` : ''}
            ${contact.permit_count ? `<div class="contact-permits">${formatNumber(contact.permit_count)} permit(s) filed</div>` : ''}
        </div>`;
    });
    
    html += '</div>';
    container.innerHTML = html;
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

function formatDate(dateStr) {
    if (!dateStr) return 'Unknown';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function getBoroughName(code) {
    const boroughs = {
        '1': 'Manhattan',
        '2': 'Bronx',
        '3': 'Brooklyn',
        '4': 'Queens',
        '5': 'Staten Island'
    };
    return boroughs[code] || 'Unknown';
}

function getDocTypeLabel(docType) {
    const labels = {
        'DEED': '🏠 Deed Transfer',
        'DEEDO': '🏠 Deed (Other)',
        'MTGE': '🏦 Mortgage',
        'AGMT': '🏦 Agreement',
        'SAT': '✅ Satisfaction of Mortgage',
        'SATF': '✅ Satisfaction (Full)',
        'UCC': '📄 UCC Filing',
        'ASST': '📄 Assignment'
    };
    return labels[docType] || docType;
}

function showError(message) {
    console.error(message);
    document.getElementById('building-address').textContent = 'Error Loading Property';
    document.getElementById('risk-score-value').textContent = '!';
    document.getElementById('risk-score-label').textContent = 'ERROR';
}
