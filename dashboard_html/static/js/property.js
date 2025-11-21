// ==========================================
// Property Detail Page JavaScript
// ==========================================

let propertyData = null;

// Initialize on page load
document.addEventListener('DOMContentLoaded', () => {
    initializeTabs();
    initializeButtons();
    loadPropertyData();
    
    // Make functions globally accessible
    window.showPermitDetails = showPermitDetails;
});

// =========================
// DATA LOADING
// =========================

async function loadPropertyData() {
    // Show loading state
    showLoadingState();
    
    try {
        const response = await fetch(`/api/property/${BBL}`);
        const data = await response.json();
        
        if (!data.success) {
            showError('Property not found');
            return;
        }
        
        propertyData = data;
        
        // Populate all sections with slight delay for animation
        hideLoadingState();
        populateHeader(data.building);
        populateStats(data);
        populateOverview(data.building);
        populatePermits(data.permits, data.contacts || []);
        populateSales(data.transactions, data.parties || []);
        populateOwnerIntel(data.building);
        populateRecentActivity(data);
        
    } catch (error) {
        console.error('Error loading property:', error);
        showError('Failed to load property data');
    }
}

function showLoadingState() {
    // Add loading overlay
    const overlay = document.createElement('div');
    overlay.id = 'pageLoadingOverlay';
    overlay.className = 'page-loading';
    overlay.innerHTML = `
        <div class="page-loading-content">
            <div class="spinner spinner-lg"></div>
            <div class="loading-text" style="font-size: 1rem; margin-top: 1rem;">Loading property data...</div>
        </div>
    `;
    document.body.appendChild(overlay);
    
    // Show skeleton loaders in tabs
    const tabContents = ['overviewData', 'permitsData', 'salesData', 'financialsData', 'ownerData'];
    tabContents.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.innerHTML = `
                <div class="skeleton skeleton-title"></div>
                <div class="skeleton skeleton-line"></div>
                <div class="skeleton skeleton-line"></div>
                <div class="skeleton skeleton-line"></div>
                <div class="skeleton skeleton-card"></div>
                <div class="skeleton skeleton-line"></div>
                <div class="skeleton skeleton-line"></div>
            `;
        }
    });
}

function hideLoadingState() {
    const overlay = document.getElementById('pageLoadingOverlay');
    if (overlay) {
        overlay.style.opacity = '0';
        setTimeout(() => overlay.remove(), 200);
    }
}

// =========================
// HEADER & STATS
// =========================

function populateHeader(building) {
    // Address
    document.getElementById('propertyAddress').textContent = building.address || 'Unknown Address';
    
    // BBL
    const bblFormatted = formatBBL(building.bbl);
    document.getElementById('propertyBBL').textContent = `BBL: ${bblFormatted}`;
    
    // Borough
    const boroughs = {1: 'Manhattan', 2: 'Bronx', 3: 'Brooklyn', 4: 'Queens', 5: 'Staten Island'};
    const borough = boroughs[building.bbl?.toString()[0]] || 'Unknown';
    document.getElementById('propertyBorough').textContent = borough;
    
    // Zip
    document.getElementById('propertyZip').textContent = building.zipcode || 'No zip';
    
    // Owner
    document.getElementById('propertyOwner').textContent = 
        `Owner: ${building.current_owner_name || 'Unknown'}`;
}

function populateStats(data) {
    // Permits count
    document.getElementById('statPermits').textContent = data.permits?.length || 0;
    document.getElementById('tabBadgePermits').textContent = data.permits?.length || 0;
    
    // Sales count
    const salesCount = data.transactions?.filter(t => 
        t.doc_type && t.doc_type.includes('DEED')
    ).length || 0;
    document.getElementById('statSales').textContent = salesCount;
    document.getElementById('tabBadgeSales').textContent = salesCount;
    
    // Units
    const totalUnits = data.building.total_units || data.building.residential_units || '-';
    document.getElementById('statUnits').textContent = totalUnits;
    
    // Year Built
    document.getElementById('statYearBuilt').textContent = data.building.year_built || '-';
    
    // Last Sale
    const lastSale = data.building.sale_price;
    if (lastSale && lastSale > 0) {
        document.getElementById('statLastSale').textContent = `$${formatCurrency(lastSale)}`;
    } else {
        document.getElementById('statLastSale').textContent = '-';
    }
    
    // Building Class
    document.getElementById('statBuildingClass').textContent = data.building.building_class || '-';
}

// =========================
// OVERVIEW TAB
// =========================

function populateOverview(building) {
    // Building Details
    document.getElementById('detailAddress').textContent = building.address || '-';
    document.getElementById('detailBBL').textContent = formatBBL(building.bbl) || '-';
    
    const boroughs = {1: 'Manhattan', 2: 'Bronx', 3: 'Brooklyn', 4: 'Queens', 5: 'Staten Island'};
    const borough = boroughs[building.bbl?.toString()[0]] || '-';
    document.getElementById('detailBorough').textContent = borough;
    
    // Extract block and lot from BBL if not provided
    const bblStr = building.bbl?.toString() || '';
    const block = building.block || (bblStr.length >= 6 ? bblStr.substring(1, 6) : '-');
    const lot = building.lot || (bblStr.length === 10 ? bblStr.substring(6) : '-');
    
    document.getElementById('detailBlock').textContent = block;
    document.getElementById('detailLot').textContent = lot;
    document.getElementById('detailZip').textContent = building.zipcode || '-';
    
    // Building class with description
    const buildingClass = building.building_class || '-';
    document.getElementById('detailBuildingClass').textContent = buildingClass;
    
    document.getElementById('detailYearBuilt').textContent = building.year_built || '-';
    
    // Calculate total units from residential + commercial  
    const resUnits = building.residential_units || 0;
    const totalUnits = building.total_units || resUnits || '-';
    
    document.getElementById('detailUnits').textContent = totalUnits;
    document.getElementById('detailResUnits').textContent = resUnits || '-';
    document.getElementById('detailComUnits').textContent = (totalUnits > resUnits ? totalUnits - resUnits : 0) || '-';
    document.getElementById('detailLotArea').textContent = building.lot_sqft ? formatNumber(building.lot_sqft) : '-';
    document.getElementById('detailBuildingArea').textContent = building.building_sqft ? formatNumber(building.building_sqft) : '-';
    document.getElementById('detailZoning').textContent = building.land_use || '-';
    
    // Owner Information - Show ALL sources
    const ownerSources = [];
    if (building.current_owner_name) {
        ownerSources.push({label: 'PLUTO', name: building.current_owner_name});
    }
    if (building.owner_name_rpad && building.owner_name_rpad !== building.current_owner_name) {
        ownerSources.push({label: 'RPAD', name: building.owner_name_rpad});
    }
    if (building.owner_name_hpd && building.owner_name_hpd !== building.current_owner_name && building.owner_name_hpd !== building.owner_name_rpad) {
        ownerSources.push({label: 'HPD', name: building.owner_name_hpd});
    }
    
    // Display all owner names
    const primaryOwner = building.current_owner_name || building.owner_name_rpad || building.owner_name_hpd || 'Unknown';
    if (ownerSources.length > 1) {
        document.getElementById('detailOwnerName').innerHTML = ownerSources.map(src => 
            `<div style="margin-bottom: 0.5rem;">
                <span style="background: var(--bg-tertiary); padding: 0.125rem 0.5rem; border-radius: var(--radius-sm); font-size: 0.75rem; color: var(--primary); font-weight: 600; margin-right: 0.5rem;">${src.label}</span>
                ${src.name}
            </div>`
        ).join('');
    } else {
        document.getElementById('detailOwnerName').textContent = primaryOwner;
    }
    
    // Owner address - show ALL available address fields
    const ownerAddressParts = [];
    if (building.owner_address) ownerAddressParts.push(building.owner_address);
    if (building.owner_city) ownerAddressParts.push(building.owner_city);
    if (building.owner_state) ownerAddressParts.push(building.owner_state);
    if (building.owner_zip) ownerAddressParts.push(building.owner_zip);
    
    const ownerAddress = ownerAddressParts.join(', ') || 'Not available';
    document.getElementById('detailOwnerAddress').textContent = ownerAddress;
    
    // Calculate owner's portfolio size and value (count buildings with same owner)
    if (building.current_owner_name) {
        fetch(`/api/search?q=${encodeURIComponent(building.current_owner_name)}`)
            .then(r => r.json())
            .then(results => {
                const portfolioSize = results.length;
                document.getElementById('detailOwnerProperties').textContent = portfolioSize || '1';
                if (portfolioSize > 1) {
                    document.getElementById('detailOwnerProperties').innerHTML = 
                        `${portfolioSize} <a href="/search-results?q=${encodeURIComponent(building.current_owner_name)}" style="color: var(--primary); text-decoration: none;">View →</a>`;
                }
                
                // Calculate total portfolio value
                const totalValue = results.reduce((sum, prop) => {
                    const value = parseFloat(prop.assessed_value) || parseFloat(prop.sale_price) || 0;
                    return sum + value;
                }, 0);
                
                if (totalValue > 0) {
                    document.getElementById('detailOwnerValue').textContent = `$${formatCurrency(totalValue)}`;
                } else {
                    document.getElementById('detailOwnerValue').textContent = 'N/A';
                }
            });
    } else {
        document.getElementById('detailOwnerProperties').textContent = '-';
        document.getElementById('detailOwnerValue').textContent = '-';
    }
    
    // Key Metrics - Construction Activity
    const permitCount = propertyData?.permits?.length || 0;
    const recentPermits = propertyData?.permits?.filter(p => {
        const issueDate = new Date(p.issue_date);
        const monthsAgo = (new Date() - issueDate) / (1000 * 60 * 60 * 24 * 30);
        return monthsAgo <= 12;
    }).length || 0;
    
    document.getElementById('metricConstruction').textContent = `${permitCount} total`;
    document.getElementById('metricConstructionTrend').textContent = 
        recentPermits > 0 ? `${recentPermits} in last year` : 'None recent';
    document.getElementById('metricConstructionTrend').className = 
        recentPermits > 0 ? 'metric-trend positive' : 'metric-trend';
    
    // Investment Score (calculate based on activity)
    let investmentScore = 0;
    if (permitCount > 0) investmentScore += 30;
    if (recentPermits > 0) investmentScore += 20;
    if (building.acris_total_transactions > 5) investmentScore += 25;
    if (building.sale_price > 0) investmentScore += 15;
    if (building.residential_units > 4) investmentScore += 10;
    
    document.getElementById('metricInvestment').textContent = investmentScore > 0 ? investmentScore : '-';
    document.getElementById('metricInvestmentTrend').textContent = 
        investmentScore > 60 ? 'High potential' : investmentScore > 30 ? 'Moderate' : investmentScore > 0 ? 'Low' : 'No data';
    document.getElementById('metricInvestmentTrend').className = 
        investmentScore > 60 ? 'metric-trend positive' : 'metric-trend';
    
    // Market Value
    const marketValue = building.sale_price;
    if (marketValue && marketValue > 0) {
        document.getElementById('metricValue').textContent = `$${formatCurrency(marketValue)}`;
        const saleDate = building.sale_date ? new Date(building.sale_date) : null;
        if (saleDate) {
            const yearsAgo = (new Date() - saleDate) / (1000 * 60 * 60 * 24 * 365);
            document.getElementById('metricValueTrend').textContent = 
                yearsAgo < 1 ? 'Recent sale' : `${Math.round(yearsAgo)}yr ago`;
        } else {
            document.getElementById('metricValueTrend').textContent = 'Last sale';
        }
    } else {
        document.getElementById('metricValue').textContent = '-';
        document.getElementById('metricValueTrend').textContent = 'No sales data';
    }
}

function populateRecentActivity(data) {
    const activityList = document.getElementById('recentActivity');
    const activities = [];
    
    // Add recent permits with more detail
    if (data.permits && data.permits.length > 0) {
        const recentPermits = data.permits.slice(0, 3);
        recentPermits.forEach(permit => {
            const jobTypeLabels = {
                'NB': 'New Building',
                'A1': 'Major Alteration',
                'A2': 'Minor Alteration',
                'A3': 'Alteration'
            };
            const jobLabel = jobTypeLabels[permit.job_type] || permit.job_type;
            const value = permit.initial_cost ? ` - $${formatCurrency(permit.initial_cost)}` : '';
            
            activities.push({
                icon: '🏗️',
                title: `${jobLabel} Permit${value}`,
                meta: `${permit.issue_date ? formatDate(permit.issue_date) : 'Date unknown'} • ${permit.applicant_name || 'Applicant unknown'}`,
                date: permit.issue_date ? new Date(permit.issue_date) : new Date(0)
            });
        });
    }
    
    // Add recent sales with more detail
    if (data.transactions && data.transactions.length > 0) {
        const recentSales = data.transactions
            .filter(t => t.doc_type && t.doc_type.includes('DEED'))
            .slice(0, 3);
        recentSales.forEach(sale => {
            const amount = sale.doc_amount ? `$${formatCurrency(sale.doc_amount)}` : 'Amount unknown';
            const docType = sale.doc_type === 'DEED' ? 'Property Sale' : 
                           sale.doc_type.includes('CORRECTIVE') ? 'Corrective Deed' : 
                           sale.doc_type;
            activities.push({
                icon: '💰',
                title: `${docType} - ${amount}`,
                meta: sale.recorded_date ? formatDate(sale.recorded_date) : 'Date unknown',
                date: sale.recorded_date ? new Date(sale.recorded_date) : new Date(0)
            });
        });
    }
    
    // Add mortgages
    if (data.transactions && data.transactions.length > 0) {
        const mortgages = data.transactions
            .filter(t => t.doc_type === 'MTGE')
            .slice(0, 2);
        mortgages.forEach(mtg => {
            const amount = mtg.doc_amount ? `$${formatCurrency(mtg.doc_amount)}` : 'Amount unknown';
            activities.push({
                icon: '🏦',
                title: `Mortgage Filed - ${amount}`,
                meta: mtg.recorded_date ? formatDate(mtg.recorded_date) : 'Date unknown',
                date: mtg.recorded_date ? new Date(mtg.recorded_date) : new Date(0)
            });
        });
    }
    
    // Sort by date
    activities.sort((a, b) => b.date - a.date);
    
    if (activities.length === 0) {
        activityList.innerHTML = '<div class="empty-state"><i class="fas fa-clock"></i><p>No recent activity</p></div>';
        return;
    }
    
    activityList.innerHTML = activities.slice(0, 8).map(activity => `
        <div class="activity-item">
            <div class="activity-icon">${activity.icon}</div>
            <div class="activity-content">
                <div class="activity-title">${activity.title}</div>
                <div class="activity-meta">${activity.meta}</div>
            </div>
        </div>
    `).join('');
}

// =========================
// PERMITS TAB
// =========================

function populatePermits(permits, contacts) {
    const permitsList = document.getElementById('permitsList');
    
    if (!permits || permits.length === 0) {
        permitsList.innerHTML = '<div class="empty-state"><i class="fas fa-file-alt"></i><p>No permits found for this property</p></div>';
        return;
    }
    
    // Group contacts by permit_id for easy lookup
    const contactsByPermit = {};
    if (contacts && contacts.length > 0) {
        contacts.forEach(contact => {
            const permitId = contact.permit_id?.toString();
            if (permitId) {
                if (!contactsByPermit[permitId]) {
                    contactsByPermit[permitId] = [];
                }
                contactsByPermit[permitId].push(contact);
            }
        });
    }
    
    permitsList.innerHTML = permits.map(permit => {
        const jobTypeLabels = {
            'NB': 'New Building',
            'A1': 'Alteration Type 1 - Major',
            'A2': 'Alteration Type 2 - Minor',
            'A3': 'Alteration Type 3 - Ordinary',
            'DM': 'Demolition',
            'SG': 'Sign',
            'EQ': 'Equipment',
            'FO': 'Foundation',
            'SH': 'Shed'
        };
        const jobTypeLabel = jobTypeLabels[permit.job_type] || permit.job_type;
        
        // Calculate age of permit
        const issueDate = permit.issue_date ? new Date(permit.issue_date) : null;
        const daysOld = issueDate ? Math.floor((new Date() - issueDate) / (1000 * 60 * 60 * 24)) : null;
        const ageLabel = daysOld !== null ? 
            (daysOld < 30 ? `${daysOld} days old` : 
             daysOld < 365 ? `${Math.floor(daysOld/30)} months old` : 
             `${Math.floor(daysOld/365)} years old`) : '';
        
        // Status badge color
        const statusColors = {
            'ISSUED': 'background: #22c55e;',
            'ACTIVE': 'background: #22c55e;',
            'PERMIT ISSUED': 'background: #22c55e;',
            'FILED': 'background: #3b82f6;',
            'PENDING': 'background: #f59e0b;',
            'IN PROGRESS': 'background: #f59e0b;',
            'APPROVED': 'background: #8b5cf6;',
            'COMPLETED': 'background: #10b981;',
            'WORK COMPLETE': 'background: #10b981;',
            'EXPIRED': 'background: #ef4444;',
            'CANCELLED': 'background: #ef4444;',
            'SUSPENDED': 'background: #ef4444;',
            'DISAPPROVED': 'background: #ef4444;'
        };
        const statusStyle = statusColors[permit.status?.toUpperCase()] || '';
        
        // Get contacts for this permit
        const permitContacts = contactsByPermit[permit.job_number] || [];
        
        // Calculate estimated cost if available
        const estimatedCost = permit.initial_cost || permit.estimated_cost;
        const jobId = permit.job_number || permit.permit_no;
        
        // Derive status from dates if status field is null
        let derivedStatus = permit.status;
        if (!derivedStatus && permit.issue_date) {
            const expDate = permit.exp_date ? new Date(permit.exp_date) : null;
            const now = new Date();
            if (expDate && expDate < now) {
                derivedStatus = 'EXPIRED';
            } else if (permit.issue_date) {
                derivedStatus = 'ISSUED';
            }
        }
        
        return `
            <div class="permit-card" style="cursor: pointer; transition: all 0.2s;" 
                 onclick="window.showPermitDetails('${jobId}')"
                 onmouseover="this.style.borderColor='var(--primary)'" 
                 onmouseout="this.style.borderColor='var(--border-color)'">
                <div class="permit-header">
                    <div>
                        <div class="permit-title">${jobTypeLabel}</div>
                        <div class="permit-meta">
                            Job #${permit.job_number || permit.permit_no}
                            ${ageLabel ? ` • ${ageLabel}` : ''}
                            ${estimatedCost ? ` • Est. $${formatCurrency(estimatedCost)}` : ''}
                        </div>
                    </div>
                    <div class="permit-badge" style="${statusStyle}">${permit.job_type}</div>
                </div>
                <div class="permit-details">
                    <div class="detail-item">
                        <span class="detail-label">Applicant</span>
                        <span class="detail-value">${permit.applicant || '-'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Issue Date</span>
                        <span class="detail-value">${permit.issue_date ? formatDate(permit.issue_date) : '-'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Status</span>
                        <span class="detail-value" style="font-weight: 600; color: ${statusStyle ? 'var(--primary)' : 'inherit'}">
                            ${derivedStatus || 'N/A'}
                        </span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Use Type</span>
                        <span class="detail-value">${permit.use_type || '-'}</span>
                    </div>
                    ${permit.total_dwelling_units ? `
                    <div class="detail-item">
                        <span class="detail-label">Total Units</span>
                        <span class="detail-value">${permit.total_dwelling_units} units</span>
                    </div>
                    ` : ''}
                    ${permit.stories ? `
                    <div class="detail-item">
                        <span class="detail-label">Stories</span>
                        <span class="detail-value">${permit.stories} floors</span>
                    </div>
                    ` : ''}
                    ${permit.work_description ? `
                    <div class="detail-item" style="grid-column: 1 / -1;">
                        <span class="detail-label">Description</span>
                        <span class="detail-value">${permit.work_description.substring(0, 150)}${permit.work_description.length > 150 ? '...' : ''}</span>
                    </div>
                    ` : ''}
                    ${permitContacts.length > 0 ? `
                    <div class="detail-item" style="grid-column: 1 / -1; margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color);">
                        <span class="detail-label">Contacts (${permitContacts.length})</span>
                        <div style="display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.5rem;">
                            ${permitContacts.map(contact => `
                                <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.5rem; background: var(--bg-tertiary); border-radius: var(--radius-sm);">
                                    <div>
                                        <div style="font-weight: 600; color: var(--text-primary);">
                                            ${contact.name || 'Name Unknown'}
                                        </div>
                                        ${contact.phone ? `
                                            <div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem;">
                                                📞 ${contact.phone}
                                            </div>
                                        ` : ''}
                                    </div>
                                    ${contact.is_checked ? `
                                        <span style="background: var(--accent); color: white; padding: 0.25rem 0.5rem; border-radius: var(--radius-sm); font-size: 0.75rem; font-weight: 600;">
                                            ✓ Verified
                                        </span>
                                    ` : ''}
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    ` : ''}
                </div>
                <div style="text-align: center; padding: 0.5rem; color: var(--text-muted); font-size: 0.875rem; border-top: 1px solid var(--border-color); margin-top: 0.5rem;">
                    Click for full details →
                </div>
            </div>
        `;
    }).join('');
}

// Show permit details modal
function showPermitDetails(jobNumber) {
    console.log('showPermitDetails called with:', jobNumber);
    console.log('propertyData:', propertyData);
    
    const permit = propertyData.permits.find(p => p.job_number === jobNumber || p.permit_no === jobNumber);
    console.log('Found permit:', permit);
    
    if (!permit) {
        console.error('Permit not found for job number:', jobNumber);
        return;
    }
    
    // Build contacts array from permits table data (not contacts table)
    const contacts = [];
    
    // Add applicant/permittee if phone exists
    if (permit.permittee_phone) {
        contacts.push({
            name: permit.applicant || permit.permittee_business_name || `${permit.permittee_first_name || ''} ${permit.permittee_last_name || ''}`.trim() || 'Permittee',
            phone: permit.permittee_phone,
            is_mobile: false,
            is_checked: false
        });
    }
    
    // Add owner if phone exists and different from permittee
    if (permit.owner_phone && permit.owner_phone !== permit.permittee_phone) {
        contacts.push({
            name: permit.owner_business_name || `${permit.owner_first_name || ''} ${permit.owner_last_name || ''}`.trim() || 'Property Owner',
            phone: permit.owner_phone,
            is_mobile: false,
            is_checked: false
        });
    }
    
    console.log('Found contacts from permit data:', contacts);
    
    const jobTypeLabels = {
        'NB': 'New Building',
        'A1': 'Alteration Type 1 - Major',
        'A2': 'Alteration Type 2 - Minor',
        'A3': 'Alteration Type 3 - Ordinary',
        'DM': 'Demolition',
        'SG': 'Sign',
        'EQ': 'Equipment',
        'FO': 'Foundation',
        'SH': 'Shed'
    };
    
    const statusColors = {
        'ISSUED': '#22c55e',
        'ACTIVE': '#22c55e',
        'PERMIT ISSUED': '#22c55e',
        'FILED': '#3b82f6',
        'PENDING': '#f59e0b',
        'IN PROGRESS': '#f59e0b',
        'APPROVED': '#8b5cf6',
        'COMPLETED': '#10b981',
        'WORK COMPLETE': '#10b981',
        'EXPIRED': '#ef4444',
        'CANCELLED': '#ef4444',
        'SUSPENDED': '#ef4444',
        'DISAPPROVED': '#ef4444'
    };
    
    // Derive status from dates if status field is null
    let derivedStatus = permit.status;
    if (!derivedStatus && permit.issue_date) {
        const expDate = permit.exp_date ? new Date(permit.exp_date) : null;
        const now = new Date();
        if (expDate && expDate < now) {
            derivedStatus = 'EXPIRED';
        } else if (permit.issue_date) {
            derivedStatus = 'ISSUED';
        }
    }
    
    const statusColor = statusColors[derivedStatus?.toUpperCase()] || '#6b7280';
    const estimatedCost = permit.initial_cost || permit.estimated_cost;
    
    const modalHTML = `
        <div style="position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.85); z-index: 10000; display: flex; align-items: center; justify-content: center; padding: 1rem; backdrop-filter: blur(4px);" onclick="this.remove()">
            <div style="background: var(--bg-secondary); border-radius: var(--radius-lg); max-width: 1200px; width: 100%; max-height: 95vh; overflow-y: auto; position: relative; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);" onclick="event.stopPropagation()">
                
                <!-- Header -->
                <div style="background: linear-gradient(135deg, var(--primary) 0%, #2563eb 100%); padding: 2rem; border-radius: var(--radius-lg) var(--radius-lg) 0 0; position: relative;">
                    <button onclick="this.closest('div[style*=fixed]').remove()" style="position: absolute; top: 1rem; right: 1rem; background: rgba(255,255,255,0.2); border: none; color: white; font-size: 1.5rem; width: 2.5rem; height: 2.5rem; border-radius: 50%; cursor: pointer; display: flex; align-items: center; justify-content: center; backdrop-filter: blur(10px); transition: all 0.2s;" onmouseover="this.style.background='rgba(255,255,255,0.3)'" onmouseout="this.style.background='rgba(255,255,255,0.2)'">×</button>
                    
                    <div style="display: flex; align-items: start; gap: 1.5rem;">
                        <div style="background: rgba(255,255,255,0.2); padding: 1rem; border-radius: var(--radius-md); backdrop-filter: blur(10px);">
                            <div style="font-size: 2rem;">📋</div>
                        </div>
                        <div style="flex: 1;">
                            <h2 style="color: white; margin: 0 0 0.5rem 0; font-size: 1.75rem;">${jobTypeLabels[permit.job_type] || permit.job_type}</h2>
                            <div style="display: flex; align-items: center; gap: 1rem; flex-wrap: wrap;">
                                <span style="background: rgba(255,255,255,0.2); color: white; padding: 0.5rem 1rem; border-radius: var(--radius-md); font-weight: 600; backdrop-filter: blur(10px);">
                                    Job #${permit.job_number || permit.permit_no}
                                </span>
                                ${derivedStatus ? `
                                    <span style="background: ${statusColor}; color: white; padding: 0.5rem 1rem; border-radius: var(--radius-md); font-weight: 600; text-transform: uppercase; font-size: 0.875rem;">
                                        ${derivedStatus}
                                    </span>
                                ` : ''}
                                ${estimatedCost ? `
                                    <span style="background: rgba(255,255,255,0.2); color: white; padding: 0.5rem 1rem; border-radius: var(--radius-md); font-weight: 600; backdrop-filter: blur(10px);">
                                        💰 Est. $${formatCurrency(estimatedCost)}
                                    </span>
                                ` : ''}
                            </div>
                        </div>
                    </div>
                </div>
                
                <div style="padding: 2rem;">
                    
                    <!-- Contacts Section -->
                    ${contacts.length > 0 ? `
                        <div style="background: var(--bg-tertiary); padding: 1.5rem; border-radius: var(--radius-md); margin-bottom: 2rem; border: 1px solid var(--border-color);">
                            <h3 style="color: var(--text-primary); margin: 0 0 1.5rem 0; display: flex; align-items: center; gap: 0.5rem;">
                                <span style="font-size: 1.5rem;">👥</span>
                                <span>Applicant & Contacts</span>
                                <span style="background: var(--primary); color: white; padding: 0.25rem 0.75rem; border-radius: var(--radius-full); font-size: 0.875rem; font-weight: 600;">${contacts.length}</span>
                            </h3>
                            <div style="display: grid; gap: 1rem;">
                                ${contacts.map((contact, index) => `
                                    <div style="background: var(--bg-secondary); padding: 1.5rem; border-radius: var(--radius-md); border: 1px solid var(--border-color); transition: all 0.2s;" onmouseover="this.style.borderColor='var(--primary)'" onmouseout="this.style.borderColor='var(--border-color)'">
                                        <div style="display: flex; justify-content: space-between; align-items: start; gap: 1.5rem; flex-wrap: wrap;">
                                            <div style="flex: 1; min-width: 250px;">
                                                <!-- Contact Header -->
                                                <div style="display: flex; align-items: start; gap: 1rem; margin-bottom: 1rem;">
                                                    <div style="background: linear-gradient(135deg, var(--primary) 0%, #2563eb 100%); color: white; width: 3rem; height: 3rem; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: 1.25rem; flex-shrink: 0; box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);">
                                                        ${(contact.name || 'Unknown')[0].toUpperCase()}
                                                    </div>
                                                    <div style="flex: 1;">
                                                        <div style="font-weight: 700; color: var(--text-primary); font-size: 1.125rem; margin-bottom: 0.25rem;">
                                                            ${contact.name || 'Name Unknown'}
                                                        </div>
                                                        ${index === 0 && permit.applicant && contact.name === permit.applicant ? `
                                                            <div style="display: inline-flex; align-items: center; gap: 0.25rem; background: linear-gradient(135deg, #8b5cf6 0%, #7c3aed 100%); color: white; padding: 0.25rem 0.75rem; border-radius: var(--radius-full); font-size: 0.75rem; font-weight: 600;">
                                                                <span>⭐</span>
                                                                <span>PRIMARY APPLICANT</span>
                                                            </div>
                                                        ` : ''}
                                                    </div>
                                                </div>
                                                
                                                <!-- Contact Details -->
                                                <div style="display: grid; gap: 0.75rem;">
                                                    ${contact.phone ? `
                                                        <div style="display: flex; align-items: center; gap: 1rem; background: var(--bg-tertiary); padding: 1rem; border-radius: var(--radius-md); border: 1px solid var(--border-color);">
                                                            <div style="background: ${contact.is_mobile ? '#10b981' : '#3b82f6'}; color: white; width: 2.5rem; height: 2.5rem; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.25rem; flex-shrink: 0;">
                                                                ${contact.is_mobile ? '📱' : '☎️'}
                                                            </div>
                                                            <div style="flex: 1;">
                                                                <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; margin-bottom: 0.25rem;">
                                                                    ${contact.is_mobile ? 'Mobile Phone' : 'Landline'}
                                                                </div>
                                                                <a href="tel:${contact.phone}" style="color: var(--primary); font-weight: 700; text-decoration: none; font-size: 1.125rem; transition: all 0.2s;" onmouseover="this.style.color='#2563eb'" onmouseout="this.style.color='var(--primary)'">
                                                                    ${contact.phone}
                                                                </a>
                                                            </div>
                                                            <a href="tel:${contact.phone}" style="background: var(--primary); color: white; padding: 0.5rem 1rem; border-radius: var(--radius-md); text-decoration: none; font-weight: 600; font-size: 0.875rem; transition: all 0.2s; white-space: nowrap;" onmouseover="this.style.background='#2563eb'" onmouseout="this.style.background='var(--primary)'">
                                                                Call Now
                                                            </a>
                                                        </div>
                                                    ` : ''}
                                                    
                                                    ${contact.assigned_to ? `
                                                        <div style="display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                                                            <span style="font-size: 1.25rem;">👤</span>
                                                            <div>
                                                                <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; font-weight: 600;">Assigned To</div>
                                                                <div style="font-weight: 600; color: var(--text-primary);">${contact.assigned_to}</div>
                                                            </div>
                                                            ${contact.assigned_at ? `
                                                                <div style="margin-left: auto; font-size: 0.875rem; color: var(--text-muted);">
                                                                    ${formatDate(contact.assigned_at)}
                                                                </div>
                                                            ` : ''}
                                                        </div>
                                                    ` : ''}
                                                </div>
                                            </div>
                                            
                                            <!-- Status Badges -->
                                            <div style="display: flex; flex-direction: column; gap: 0.5rem; align-items: flex-end;">
                                                ${contact.is_checked ? `
                                                    <span style="background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%); color: white; padding: 0.5rem 1rem; border-radius: var(--radius-md); font-size: 0.875rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem; white-space: nowrap; box-shadow: 0 4px 12px rgba(34, 197, 94, 0.3);">
                                                        <span style="font-size: 1.125rem;">✓</span>
                                                        <span>VERIFIED</span>
                                                    </span>
                                                ` : `
                                                    <span style="background: var(--bg-tertiary); color: var(--text-muted); padding: 0.5rem 1rem; border-radius: var(--radius-md); font-size: 0.875rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem; white-space: nowrap; border: 1px solid var(--border-color);">
                                                        <span>⏳</span>
                                                        <span>UNVERIFIED</span>
                                                    </span>
                                                `}
                                                ${contact.is_mobile ? `
                                                    <span style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 0.5rem 1rem; border-radius: var(--radius-md); font-size: 0.875rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem; white-space: nowrap;">
                                                        <span>📱</span>
                                                        <span>MOBILE</span>
                                                    </span>
                                                ` : contact.phone ? `
                                                    <span style="background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); color: white; padding: 0.5rem 1rem; border-radius: var(--radius-md); font-size: 0.875rem; font-weight: 600; display: flex; align-items: center; gap: 0.5rem; white-space: nowrap;">
                                                        <span>☎️</span>
                                                        <span>LANDLINE</span>
                                                    </span>
                                                ` : ''}
                                            </div>
                                        </div>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                    ` : ''}
                    
                    <!-- Key Details Section -->
                    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin-bottom: 2rem;">
                        ${[
                            {icon: '👤', label: 'Applicant', value: permit.applicant},
                            {icon: '📄', label: 'Permit Number', value: permit.permit_no},
                            {icon: '🏗️', label: 'Job Type', value: jobTypeLabels[permit.job_type] || permit.job_type},
                            {icon: '🏢', label: 'Building Type', value: permit.bldg_type},
                            {icon: '🔧', label: 'Work Type', value: permit.work_type},
                            {icon: '📋', label: 'Permit Type', value: permit.permit_type},
                            {icon: '🔖', label: 'Permit Subtype', value: permit.permit_subtype},
                            {icon: '🏘️', label: 'Use Type', value: permit.use_type},
                            {icon: '💳', label: 'Fee Type', value: permit.fee_type},
                            {icon: '📊', label: 'Filing Status', value: permit.filing_status},
                            {icon: '✅', label: 'Self Cert', value: permit.self_cert},
                            {icon: '🏠', label: 'Residential', value: permit.residential},
                            {icon: '👷', label: 'Assigned To', value: permit.assigned_to},
                        ].filter(item => item.value).map(item => `
                            <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md); border: 1px solid var(--border-color); transition: all 0.2s;" onmouseover="this.style.borderColor='var(--primary)'" onmouseout="this.style.borderColor='var(--border-color)'">
                                <div style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.5rem;">
                                    <span style="font-size: 1.25rem;">${item.icon}</span>
                                    <span style="font-size: 0.875rem; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">${item.label}</span>
                                </div>
                                <div style="font-weight: 600; color: var(--text-primary); font-size: 1rem;">${item.value}</div>
                            </div>
                        `).join('')}
                    </div>
                    
                    <!-- Timeline Section -->
                    <div style="background: var(--bg-tertiary); padding: 1.5rem; border-radius: var(--radius-md); margin-bottom: 2rem; border: 1px solid var(--border-color);">
                        <h3 style="color: var(--text-primary); margin: 0 0 1.5rem 0; display: flex; align-items: center; gap: 0.5rem;">
                            <span style="font-size: 1.5rem;">📅</span>
                            <span>Timeline & Dates</span>
                        </h3>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem;">
                            ${[
                                {label: 'Filing Date', value: permit.filing_date, icon: '📝'},
                                {label: 'Issue Date', value: permit.issue_date, icon: '✅'},
                                {label: 'Work Approved', value: permit.work_approved, icon: '🔨'},
                                {label: 'Proposed Start', value: permit.proposed_job_start, icon: '🚀'},
                                {label: 'Expiration Date', value: permit.exp_date, icon: '⏰'},
                                {label: 'Assigned At', value: permit.assigned_at, icon: '📆'},
                            ].filter(item => item.value).map(item => `
                                <div>
                                    <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                        <span>${item.icon}</span>
                                        <span>${item.label}</span>
                                    </div>
                                    <div style="font-weight: 600; color: var(--text-primary);">${formatDate(item.value)}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    
                    <!-- Building Details Section -->
                    <div style="background: var(--bg-tertiary); padding: 1.5rem; border-radius: var(--radius-md); margin-bottom: 2rem; border: 1px solid var(--border-color);">
                        <h3 style="color: var(--text-primary); margin: 0 0 1.5rem 0; display: flex; align-items: center; gap: 0.5rem;">
                            <span style="font-size: 1.5rem;">🏛️</span>
                            <span>Building Information</span>
                        </h3>
                        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1.5rem;">
                            ${[
                                {label: 'BIN', value: permit.bin, icon: '🔢'},
                                {label: 'BBL', value: permit.bbl ? formatBBL(permit.bbl) : null, icon: '📍'},
                                {label: 'Block', value: permit.block, icon: '🗺️'},
                                {label: 'Lot', value: permit.lot, icon: '📐'},
                                {label: 'Borough', value: permit.borough, icon: '🏙️'},
                                {label: 'Zip Code', value: permit.zip_code, icon: '📮'},
                                {label: 'Community Board', value: permit.community_board, icon: '🏛️'},
                                {label: 'Council District', value: permit.council_district, icon: '🏢'},
                                {label: 'Stories', value: permit.stories ? permit.stories + ' floors' : null, icon: '📏'},
                                {label: 'Total Units', value: permit.total_units, icon: '🏠'},
                                {label: 'Occupied Units', value: permit.occupied_units, icon: '👥'},
                                {label: 'Total Dwelling Units', value: permit.total_dwelling_units, icon: '🏘️'},
                                {label: 'Dwelling Occupied', value: permit.dwelling_units_occupied, icon: '🏘️'},
                                {label: 'Site Fill', value: permit.site_fill, icon: '🏗️'},
                                {label: 'Oil/Gas', value: permit.oil_gas, icon: '⚡'},
                                {label: 'Special District 1', value: permit.special_district_1, icon: '🎯'},
                                {label: 'Special District 2', value: permit.special_district_2, icon: '🎯'},
                            ].filter(item => item.value).map(item => `
                                <div>
                                    <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                        <span>${item.icon}</span>
                                        <span>${item.label}</span>
                                    </div>
                                    <div style="font-weight: 600; color: var(--text-primary);">${item.value}</div>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    
                    <!-- Personnel Section -->
                    ${(permit.superintendent_name || permit.site_safety_mgr_first_name || permit.site_safety_mgr_last_name || permit.permittee_license_number || permit.hic_license) ? `
                        <div style="background: var(--bg-tertiary); padding: 1.5rem; border-radius: var(--radius-md); margin-bottom: 2rem; border: 1px solid var(--border-color);">
                            <h3 style="color: var(--text-primary); margin: 0 0 1.5rem 0; display: flex; align-items: center; gap: 0.5rem;">
                                <span style="font-size: 1.5rem;">👷</span>
                                <span>Personnel & Licenses</span>
                            </h3>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem;">
                                ${permit.superintendent_name ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>👤</span>
                                            <span>Superintendent</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.superintendent_name}</div>
                                        ${permit.superintendent_business_name ? `<div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem;">${permit.superintendent_business_name}</div>` : ''}
                                    </div>
                                ` : ''}
                                ${permit.site_safety_mgr_first_name || permit.site_safety_mgr_last_name ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>🦺</span>
                                            <span>Site Safety Manager</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.site_safety_mgr_first_name} ${permit.site_safety_mgr_last_name}</div>
                                        ${permit.site_safety_mgr_business_name ? `<div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem;">${permit.site_safety_mgr_business_name}</div>` : ''}
                                    </div>
                                ` : ''}
                                ${permit.permittee_license_number ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>🎫</span>
                                            <span>Permittee License</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.permittee_license_type || 'N/A'} #${permit.permittee_license_number}</div>
                                    </div>
                                ` : ''}
                                ${permit.hic_license ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>🏗️</span>
                                            <span>HIC License</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.hic_license}</div>
                                    </div>
                                ` : ''}
                                ${permit.act_as_superintendent ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>⚙️</span>
                                            <span>Act as Superintendent</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.act_as_superintendent}</div>
                                    </div>
                                ` : ''}
                                ${permit.permittee_other_title ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>👔</span>
                                            <span>Permittee Title</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.permittee_other_title}</div>
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    ` : ''}
                    
                    <!-- Location Section -->
                    ${(permit.address || permit.latitude || permit.longitude) ? `
                        <div style="background: var(--bg-tertiary); padding: 1.5rem; border-radius: var(--radius-md); margin-bottom: 2rem; border: 1px solid var(--border-color);">
                            <h3 style="color: var(--text-primary); margin: 0 0 1.5rem 0; display: flex; align-items: center; gap: 0.5rem;">
                                <span style="font-size: 1.5rem;">📍</span>
                                <span>Location Details</span>
                            </h3>
                            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1.5rem;">
                                ${permit.address ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>🏠</span>
                                            <span>Address</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.address}</div>
                                    </div>
                                ` : ''}
                                ${permit.latitude ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>🌐</span>
                                            <span>Latitude</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.latitude.toFixed(6)}</div>
                                    </div>
                                ` : ''}
                                ${permit.longitude ? `
                                    <div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.25rem; display: flex; align-items: center; gap: 0.25rem;">
                                            <span>🌐</span>
                                            <span>Longitude</span>
                                        </div>
                                        <div style="font-weight: 600; color: var(--text-primary);">${permit.longitude.toFixed(6)}</div>
                                    </div>
                                ` : ''}
                                ${permit.latitude && permit.longitude ? `
                                    <div>
                                        <a href="https://www.google.com/maps?q=${permit.latitude},${permit.longitude}" target="_blank" style="display: inline-flex; align-items: center; gap: 0.5rem; background: var(--primary); color: white; padding: 0.75rem 1.5rem; border-radius: var(--radius-md); text-decoration: none; font-weight: 600; transition: all 0.2s;" onmouseover="this.style.opacity='0.9'" onmouseout="this.style.opacity='1'">
                                            <span>🗺️</span>
                                            <span>View on Map</span>
                                        </a>
                                    </div>
                                ` : ''}
                            </div>
                        </div>
                    ` : ''}
                    
                    <!-- Work Description -->
                    ${permit.work_description ? `
                        <div style="background: var(--bg-tertiary); padding: 1.5rem; border-radius: var(--radius-md); margin-bottom: 2rem; border: 1px solid var(--border-color);">
                            <h3 style="color: var(--text-primary); margin: 0 0 1rem 0; display: flex; align-items: center; gap: 0.5rem;">
                                <span style="font-size: 1.5rem;">📝</span>
                                <span>Work Description</span>
                            </h3>
                            <div style="background: var(--bg-secondary); padding: 1.25rem; border-radius: var(--radius-md); border-left: 4px solid var(--primary);">
                                <p style="color: var(--text-secondary); line-height: 1.8; margin: 0; font-size: 0.95rem;">${permit.work_description}</p>
                            </div>
                        </div>
                    ` : ''}
                    
                    <!-- Action Buttons -->
                    <div style="display: flex; gap: 1rem; justify-content: center; flex-wrap: wrap; padding-top: 1rem;">
                        ${permit.link ? `
                            <a href="${permit.link}" target="_blank" style="display: inline-flex; align-items: center; gap: 0.75rem; background: linear-gradient(135deg, var(--primary) 0%, #2563eb 100%); color: white; padding: 1rem 2.5rem; border-radius: var(--radius-lg); text-decoration: none; font-weight: 600; font-size: 1.125rem; box-shadow: 0 10px 25px -5px rgba(59, 130, 246, 0.3); transition: all 0.2s;" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 15px 30px -5px rgba(59, 130, 246, 0.4)'" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 10px 25px -5px rgba(59, 130, 246, 0.3)'">
                                <span>View on NYC DOB Website</span>
                                <span style="font-size: 1.25rem;">→</span>
                            </a>
                        ` : ''}
                        ${permit.latitude && permit.longitude ? `
                            <a href="https://www.google.com/maps?q=${permit.latitude},${permit.longitude}" target="_blank" style="display: inline-flex; align-items: center; gap: 0.75rem; background: linear-gradient(135deg, #10b981 0%, #059669 100%); color: white; padding: 1rem 2.5rem; border-radius: var(--radius-lg); text-decoration: none; font-weight: 600; font-size: 1.125rem; box-shadow: 0 10px 25px -5px rgba(16, 185, 129, 0.3); transition: all 0.2s;" onmouseover="this.style.transform='translateY(-2px)'; this.style.boxShadow='0 15px 30px -5px rgba(16, 185, 129, 0.4)'" onmouseout="this.style.transform='translateY(0)'; this.style.boxShadow='0 10px 25px -5px rgba(16, 185, 129, 0.3)'">
                                <span>🗺️</span>
                                <span>View Location on Map</span>
                            </a>
                        ` : ''}
                    </div>
                    
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHTML);
}

// =========================
// SALES TAB
// =========================

function populateSales(transactions, parties) {
    const salesList = document.getElementById('salesList');
    
    const sales = transactions?.filter(t => t.doc_type && t.doc_type.includes('DEED')) || [];
    
    if (sales.length === 0) {
        salesList.innerHTML = '<div class="empty-state"><i class="fas fa-dollar-sign"></i><p>No sales history found for this property</p></div>';
        return;
    }
    
    // Group parties by transaction_id
    const partiesByTransaction = {};
    if (parties && parties.length > 0) {
        parties.forEach(party => {
            const txId = party.transaction_id;
            if (txId) {
                if (!partiesByTransaction[txId]) {
                    partiesByTransaction[txId] = {buyers: [], sellers: [], lenders: []};
                }
                if (party.party_type === 'buyer') {
                    partiesByTransaction[txId].buyers.push(party);
                } else if (party.party_type === 'seller') {
                    partiesByTransaction[txId].sellers.push(party);
                } else if (party.party_type === 'lender') {
                    partiesByTransaction[txId].lenders.push(party);
                }
            }
        });
    }
    
    // Calculate price changes
    const sortedSales = [...sales].sort((a, b) => {
        const dateA = new Date(a.doc_date || a.recorded_date);
        const dateB = new Date(b.doc_date || b.recorded_date);
        return dateB - dateA;
    });
    
    salesList.innerHTML = sortedSales.map((sale, index) => {
        const amount = sale.doc_amount || 0;
        const prevSale = sortedSales[index + 1];
        const prevAmount = prevSale?.doc_amount || 0;
        
        // Calculate change
        let changeHtml = '';
        if (prevAmount > 0 && amount > 0) {
            const change = ((amount - prevAmount) / prevAmount * 100).toFixed(1);
            const changeClass = change > 0 ? 'positive' : change < 0 ? 'negative' : '';
            const changeIcon = change > 0 ? '▲' : change < 0 ? '▼' : '=';
            changeHtml = `<span class="metric-trend ${changeClass}">${changeIcon} ${Math.abs(change)}% from previous</span>`;
        }
        
        // Calculate time since sale
        const saleDate = sale.doc_date ? new Date(sale.doc_date) : sale.recorded_date ? new Date(sale.recorded_date) : null;
        const yearsAgo = saleDate ? ((new Date() - saleDate) / (1000 * 60 * 60 * 24 * 365)).toFixed(1) : null;
        const timeAgo = yearsAgo ? `${yearsAgo} years ago` : '';
        
        // Doc type label
        const docTypeLabel = sale.doc_type === 'DEED' ? 'Deed Transfer' :
                            sale.doc_type.includes('CORRECTIVE') ? 'Corrective Deed' :
                            sale.doc_type.includes('BARGAIN') ? 'Bargain & Sale Deed' :
                            sale.doc_type;
        
        // Get parties for this transaction
        const txParties = partiesByTransaction[sale.id] || {buyers: [], sellers: [], lenders: []};
        
        return `
            <div class="sale-card">
                <div class="sale-header">
                    <div>
                        <div class="sale-title">${docTypeLabel}</div>
                        <div class="sale-meta">
                            Document #${sale.document_id}
                            ${timeAgo ? ` • ${timeAgo}` : ''}
                        </div>
                    </div>
                    <div>
                        <div class="sale-badge">${amount > 0 ? `$${formatCurrency(amount)}` : 'Amount N/A'}</div>
                        ${changeHtml}
                    </div>
                </div>
                <div class="sale-details">
                    <div class="detail-item">
                        <span class="detail-label">Sale Date</span>
                        <span class="detail-value">${sale.doc_date ? formatDate(sale.doc_date) : '-'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Recorded Date</span>
                        <span class="detail-value">${sale.recorded_date ? formatDate(sale.recorded_date) : '-'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Amount</span>
                        <span class="detail-value">${amount > 0 ? `$${formatCurrency(amount)}` : '-'}</span>
                    </div>
                    <div class="detail-item">
                        <span class="detail-label">Document Type</span>
                        <span class="detail-value">${sale.doc_type}</span>
                    </div>
                    ${sale.percent_trans ? `
                    <div class="detail-item">
                        <span class="detail-label">Percent Transferred</span>
                        <span class="detail-value">${sale.percent_trans}%</span>
                    </div>
                    ` : ''}
                    ${(txParties.buyers.length > 0 || txParties.sellers.length > 0 || txParties.lenders.length > 0) ? `
                    <div class="detail-item" style="grid-column: 1 / -1; margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px solid var(--border-color);">
                        <span class="detail-label">Transaction Parties</span>
                        <div style="display: flex; flex-direction: column; gap: 1rem; margin-top: 0.5rem;">
                            ${txParties.sellers.length > 0 ? `
                                <div>
                                    <div style="font-weight: 600; color: var(--text-muted); font-size: 0.875rem; margin-bottom: 0.5rem;">
                                        SELLERS (${txParties.sellers.length})
                                    </div>
                                    ${txParties.sellers.map(seller => {
                                        const addressParts = [seller.address_1, seller.address_2, seller.city, seller.state, seller.zip_code].filter(Boolean);
                                        const fullAddress = addressParts.join(', ');
                                        return `
                                        <div style="padding: 0.5rem; background: var(--bg-tertiary); border-radius: var(--radius-sm); margin-bottom: 0.5rem;">
                                            <div style="font-weight: 600; color: var(--text-primary);">
                                                ${seller.party_name}
                                            </div>
                                            ${fullAddress ? `
                                                <div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem;">
                                                    📍 ${fullAddress}
                                                </div>
                                            ` : ''}
                                        </div>
                                        `;
                                    }).join('')}
                                </div>
                            ` : ''}
                            ${txParties.buyers.length > 0 ? `
                                <div>
                                    <div style="font-weight: 600; color: var(--text-muted); font-size: 0.875rem; margin-bottom: 0.5rem;">
                                        BUYERS (${txParties.buyers.length})
                                    </div>
                                    ${txParties.buyers.map(buyer => {
                                        const addressParts = [buyer.address_1, buyer.address_2, buyer.city, buyer.state, buyer.zip_code].filter(Boolean);
                                        const fullAddress = addressParts.join(', ');
                                        return `
                                        <div style="padding: 0.5rem; background: var(--bg-tertiary); border-radius: var(--radius-sm); margin-bottom: 0.5rem;">
                                            <div style="font-weight: 600; color: var(--text-primary);">
                                                ${buyer.party_name}
                                            </div>
                                            ${fullAddress ? `
                                                <div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem;">
                                                    📍 ${fullAddress}
                                                </div>
                                            ` : ''}
                                        </div>
                                        `;
                                    }).join('')}
                                </div>
                            ` : ''}
                            ${txParties.lenders.length > 0 ? `
                                <div>
                                    <div style="font-weight: 600; color: var(--text-muted); font-size: 0.875rem; margin-bottom: 0.5rem;">
                                        LENDERS (${txParties.lenders.length})
                                    </div>
                                    ${txParties.lenders.map(lender => {
                                        const addressParts = [lender.address_1, lender.address_2, lender.city, lender.state, lender.zip_code].filter(Boolean);
                                        const fullAddress = addressParts.join(', ');
                                        return `
                                        <div style="padding: 0.5rem; background: var(--bg-tertiary); border-radius: var(--radius-sm); margin-bottom: 0.5rem;">
                                            <div style="font-weight: 600; color: var(--text-primary);">
                                                ${lender.party_name}
                                            </div>
                                            ${fullAddress ? `
                                                <div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem;">
                                                    📍 ${fullAddress}
                                                </div>
                                            ` : ''}
                                        </div>
                                        `;
                                    }).join('')}
                                </div>
                            ` : ''}
                        </div>
                    </div>
                    ` : ''}
                </div>
            </div>
        `;
    }).join('');
}

// =========================
// OWNER INTEL TAB
// =========================

function populateOwnerIntel(building) {
    const ownerIntel = document.getElementById('ownerIntel');
    
    // Get ALL owner names for comprehensive portfolio search
    const ownerNames = [];
    if (building.current_owner_name) ownerNames.push({source: 'PLUTO', name: building.current_owner_name});
    if (building.owner_name_rpad && building.owner_name_rpad !== building.current_owner_name) {
        ownerNames.push({source: 'RPAD', name: building.owner_name_rpad});
    }
    if (building.owner_name_hpd && building.owner_name_hpd !== building.current_owner_name && building.owner_name_hpd !== building.owner_name_rpad) {
        ownerNames.push({source: 'HPD', name: building.owner_name_hpd});
    }
    
    if (ownerNames.length === 0) {
        ownerIntel.innerHTML = '<div class="info-card"><p style="color: var(--text-secondary);">Owner information not available</p></div>';
        return;
    }
    
    const primaryOwner = ownerNames[0].name;
    const ownerAddress = [building.owner_address, building.owner_city, building.owner_state, building.owner_zip].filter(Boolean).join(', ') || 'Address not available';
    
    // Fetch portfolio for ALL owner name sources and combine results
    const portfolioPromises = ownerNames.map(ownerObj => 
        fetch(`/api/search?q=${encodeURIComponent(ownerObj.name)}`)
            .then(r => r.json())
            .then(results => ({source: ownerObj.source, results: results || []}))
            .catch(err => ({source: ownerObj.source, results: []}))
    );
    
    Promise.all(portfolioPromises).then(portfoliosBySource => {
        // Combine and deduplicate by BBL
        const allProperties = new Map();
        portfoliosBySource.forEach(({source, results}) => {
            results.forEach(prop => {
                if (!allProperties.has(prop.bbl)) {
                    allProperties.set(prop.bbl, {...prop, sources: [source]});
                } else {
                    allProperties.get(prop.bbl).sources.push(source);
                }
            });
        });
        
        const portfolio = Array.from(allProperties.values());
        const portfolioSize = portfolio.length;
            const totalPermits = portfolio.reduce((sum, prop) => sum + (prop.permits || 0), 0);
            
            ownerIntel.innerHTML = `
                <div class="info-card">
                    <h3 class="card-title">👤 Owner Profile</h3>
                    <div class="info-grid">
                        ${ownerNames.length > 1 ? `
                            <div class="info-item full-width">
                                <span class="info-label">Owner Names (All Sources)</span>
                                <div style="display: flex; flex-direction: column; gap: 0.5rem;">
                                    ${ownerNames.map(o => `
                                        <div>
                                            <span style="background: var(--bg-tertiary); padding: 0.125rem 0.5rem; border-radius: var(--radius-sm); font-size: 0.75rem; color: var(--primary); font-weight: 600; margin-right: 0.5rem;">${o.source}</span>
                                            <span class="info-value">${o.name}</span>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        ` : `
                            <div class="info-item full-width">
                                <span class="info-label">Name (${ownerNames[0].source})</span>
                                <span class="info-value">${primaryOwner}</span>
                            </div>
                        `}
                        <div class="info-item full-width">
                            <span class="info-label">Mailing Address</span>
                            <span class="info-value">${ownerAddress}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Properties in Database</span>
                            <span class="info-value">${portfolioSize}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Total Permits</span>
                            <span class="info-value">${totalPermits}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Owner Type</span>
                            <span class="info-value">${portfolioSize > 10 ? 'Large Portfolio' : portfolioSize > 3 ? 'Multi-Property' : 'Small Portfolio'}</span>
                        </div>
                        <div class="info-item">
                            <span class="info-label">Activity Level</span>
                            <span class="info-value">${totalPermits > 10 ? 'Very Active' : totalPermits > 3 ? 'Active' : 'Low Activity'}</span>
                        </div>
                    </div>
                </div>
                
                ${portfolioSize > 1 ? `
                <div class="info-card" style="margin-top: var(--spacing-lg);">
                    <h3 class="card-title">🏢 Owner's Portfolio</h3>
                    <div style="margin-bottom: var(--spacing-md); color: var(--text-secondary);">
                        This owner has ${portfolioSize} ${portfolioSize === 1 ? 'property' : 'properties'} in our database
                        ${ownerNames.length > 1 ? `<span style="color: var(--primary); font-weight: 600;"> (combined from ${ownerNames.length} sources)</span>` : ''}
                    </div>
                    <div style="display: flex; flex-direction: column; gap: var(--spacing-sm);">
                        ${portfolio.slice(0, 5).map(prop => `
                            <div style="background: var(--bg-tertiary); padding: var(--spacing-md); border-radius: var(--radius-md); border: 1px solid var(--border-color); cursor: pointer; transition: all var(--transition-fast);"
                                 onclick="window.location.href='/property/${prop.bbl}'"
                                 onmouseover="this.style.borderColor='var(--primary)'"
                                 onmouseout="this.style.borderColor='var(--border-color)'">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <div>
                                        <div style="font-weight: 600; color: var(--text-primary); margin-bottom: 0.25rem;">
                                            ${prop.address || 'Address Unknown'}
                                        </div>
                                        <div style="font-size: 0.875rem; color: var(--text-muted);">
                                            BBL: ${formatBBL(prop.bbl)} • ${prop.permits || 0} permit${prop.permits !== 1 ? 's' : ''}
                                            ${prop.sources && prop.sources.length > 0 ? `<span style="margin-left: 0.5rem; color: var(--primary); font-size: 0.75rem;">📍 ${prop.sources.join(', ')}</span>` : ''}
                                        </div>
                                    </div>
                                    <i class="fas fa-arrow-right" style="color: var(--primary);"></i>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                    ${portfolioSize > 5 ? `
                        <button class="btn btn-outline" style="margin-top: var(--spacing-md); width: 100%;"
                                onclick="window.location.href='/search-results?q=${encodeURIComponent(primaryOwner)}'">
                            View All ${portfolioSize} Properties →
                        </button>
                    ` : ''}
                </div>
                ` : ''}
                
                <div class="info-card" style="margin-top: var(--spacing-lg);">
                    <h3 class="card-title">🎯 Owner Intelligence Insights</h3>
                    <div style="display: flex; flex-direction: column; gap: var(--spacing-md);">
                        ${totalPermits > 5 ? `
                            <div style="padding: var(--spacing-md); background: rgba(139, 92, 246, 0.1); border-left: 3px solid var(--primary); border-radius: var(--radius-md);">
                                <strong style="color: var(--primary);">Active Developer</strong> - This owner has ${totalPermits} permits across their portfolio
                            </div>
                        ` : ''}
                        ${portfolioSize > 10 ? `
                            <div style="padding: var(--spacing-md); background: rgba(16, 185, 129, 0.1); border-left: 3px solid var(--accent); border-radius: var(--radius-md);">
                                <strong style="color: var(--accent);">Major Portfolio Owner</strong> - Owns ${portfolioSize} properties in database
                            </div>
                        ` : ''}
                        ${building.sale_date && new Date(building.sale_date) > new Date(Date.now() - 365 * 24 * 60 * 60 * 1000) ? `
                            <div style="padding: var(--spacing-md); background: rgba(236, 72, 153, 0.1); border-left: 3px solid var(--secondary); border-radius: var(--radius-md);">
                                <strong style="color: var(--secondary);">Recent Acquisition</strong> - Property purchased within last year
                            </div>
                        ` : ''}
                    </div>
                </div>
            `;
        }).catch(err => {
            console.error('Error fetching owner portfolio:', err);
            ownerIntel.innerHTML = `
                <div class="info-card">
                    <h3 class="card-title">👤 Owner Profile</h3>
                    <div class="info-grid">
                        ${ownerNames.length > 1 ? `
                            <div class="info-item full-width">
                                <span class="info-label">Owner Names (All Sources)</span>
                                <div style="display: flex; flex-direction: column; gap: 0.5rem;">
                                    ${ownerNames.map(o => `
                                        <div>
                                            <span style="background: var(--bg-tertiary); padding: 0.125rem 0.5rem; border-radius: var(--radius-sm); font-size: 0.75rem; color: var(--primary); font-weight: 600; margin-right: 0.5rem;">${o.source}</span>
                                            <span class="info-value">${o.name}</span>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        ` : `
                            <div class="info-item full-width">
                                <span class="info-label">Name (${ownerNames[0].source})</span>
                                <span class="info-value">${primaryOwner}</span>
                            </div>
                        `}
                        </div>
                        <div class="info-item full-width">
                            <span class="info-label">Address</span>
                            <span class="info-value">${ownerAddress}</span>
                        </div>
                    </div>
                </div>
            `;
        });
}

// =========================
// TAB MANAGEMENT
// =========================

function initializeTabs() {
    const tabs = document.querySelectorAll('.tab');
    
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetTab = tab.getAttribute('data-tab');
            switchTab(targetTab);
        });
    });
}

function switchTab(tabName) {
    // Update active tab button
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.tab[data-tab="${tabName}"]`).classList.add('active');
    
    // Update active content
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById(tabName).classList.add('active');
    
    // Load data for specific tabs if needed
    if (tabName === 'financials') {
        populateFinancials();
    } else if (tabName === 'market') {
        populateMarket();
    }
}

function populateFinancials() {
    const financialsData = document.getElementById('financialsData');
    const building = propertyData.building;
    const transactions = propertyData.transactions || [];
    
    // Calculate key metrics with CORRECT field names
    const assessedTotal = parseFloat(building.assessed_total_value) || 0;
    const assessedLand = parseFloat(building.assessed_land_value) || 0;
    const assessedImprovement = assessedTotal - assessedLand; // Calculate since no field exists
    const lastSalePrice = parseFloat(building.sale_price) || 0;
    const lotSize = parseInt(building.lot_sqft) || 0;
    const buildingSize = parseInt(building.building_sqft) || 0;
    const totalUnits = parseInt(building.total_units) || 0;
    const residentialUnits = parseInt(building.residential_units) || 0;
    const commercialUnits = totalUnits > residentialUnits ? totalUnits - residentialUnits : 0; // Calculate
    
    // Calculate per unit and per sqft metrics
    const pricePerUnit = lastSalePrice > 0 && totalUnits > 0 ? lastSalePrice / totalUnits : 0;
    const pricePerSqft = lastSalePrice > 0 && buildingSize > 0 ? lastSalePrice / buildingSize : 0;
    const assessedPerSqft = assessedTotal > 0 && buildingSize > 0 ? assessedTotal / buildingSize : 0;
    
    // Get most recent transactions - use doc_amount NOT price
    const recentSales = transactions
        .filter(t => t.doc_type && t.doc_amount > 0)
        .sort((a, b) => new Date(b.recorded_date) - new Date(a.recorded_date))
        .slice(0, 10);
    
    financialsData.innerHTML = `
        <!-- Valuation Overview -->
        <div class="info-card" style="margin-bottom: var(--spacing-lg);">
            <h3 class="card-title" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: var(--spacing-lg);">
                <span style="font-size: 1.5rem;">💰</span>
                <span>Property Valuation</span>
            </h3>
            
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--spacing-md); margin-bottom: var(--spacing-lg);">
                ${assessedTotal > 0 ? `
                    <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md); border-left: 4px solid var(--primary);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Assessed Value</div>
                        <div style="font-size: 1.75rem; font-weight: 700; color: var(--text-primary);">$${formatCurrency(assessedTotal)}</div>
                        <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">NYC Tax Assessment</div>
                    </div>
                ` : ''}
                
                ${lastSalePrice > 0 ? `
                    <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md); border-left: 4px solid var(--accent);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Last Sale Price</div>
                        <div style="font-size: 1.75rem; font-weight: 700; color: var(--text-primary);">$${formatCurrency(lastSalePrice)}</div>
                        ${building.sale_date ? `
                            <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">${formatDate(building.sale_date)}</div>
                        ` : ''}
                    </div>
                ` : ''}
                
                ${assessedLand > 0 ? `
                    <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Land Value</div>
                        <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);">$${formatCurrency(assessedLand)}</div>
                        <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">${assessedTotal > 0 ? ((assessedLand/assessedTotal)*100).toFixed(1) + '% of total' : ''}</div>
                    </div>
                ` : ''}
                
                ${assessedImprovement > 0 ? `
                    <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Improvement Value</div>
                        <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);">$${formatCurrency(assessedImprovement)}</div>
                        <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">${assessedTotal > 0 ? ((assessedImprovement/assessedTotal)*100).toFixed(1) + '% of total' : ''}</div>
                    </div>
                ` : ''}
            </div>
        </div>
        
        <!-- Per Unit & Per Sqft Metrics -->
        ${(pricePerUnit > 0 || pricePerSqft > 0 || assessedPerSqft > 0) ? `
            <div class="info-card" style="margin-bottom: var(--spacing-lg);">
                <h3 class="card-title" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: var(--spacing-lg);">
                    <span style="font-size: 1.5rem;">📊</span>
                    <span>Unit Economics</span>
                </h3>
                
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: var(--spacing-md);">
                    ${pricePerUnit > 0 ? `
                        <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md);">
                            <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Price Per Unit</div>
                            <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);">$${formatCurrency(pricePerUnit)}</div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">${totalUnits} total units</div>
                        </div>
                    ` : ''}
                    
                    ${pricePerSqft > 0 ? `
                        <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md);">
                            <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Sale Price Per Sqft</div>
                            <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);">$${pricePerSqft.toFixed(2)}</div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">${formatNumber(buildingSize)} sqft</div>
                        </div>
                    ` : ''}
                    
                    ${assessedPerSqft > 0 ? `
                        <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md);">
                            <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Assessed Per Sqft</div>
                            <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);">$${assessedPerSqft.toFixed(2)}</div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">Tax assessment basis</div>
                        </div>
                    ` : ''}
                    
                    ${lotSize > 0 && lastSalePrice > 0 ? `
                        <div style="background: var(--bg-tertiary); padding: 1.25rem; border-radius: var(--radius-md);">
                            <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem; text-transform: uppercase; font-weight: 600;">Price Per Lot Sqft</div>
                            <div style="font-size: 1.5rem; font-weight: 700; color: var(--text-primary);">$${(lastSalePrice / lotSize).toFixed(2)}</div>
                            <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.5rem;">${formatNumber(lotSize)} lot sqft</div>
                        </div>
                    ` : ''}
                </div>
            </div>
        ` : ''}
        
        <!-- Property Details for Financial Analysis -->
        <div class="info-card" style="margin-bottom: var(--spacing-lg);">
            <h3 class="card-title" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: var(--spacing-lg);">
                <span style="font-size: 1.5rem;">🏗️</span>
                <span>Property Characteristics</span>
            </h3>
            
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: var(--spacing-md);">
                ${building.year_built ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Year Built</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${building.year_built}</div>
                        <div style="font-size: 0.875rem; color: var(--text-secondary); margin-top: 0.25rem;">${new Date().getFullYear() - building.year_built} years old</div>
                    </div>
                ` : ''}
                
                ${lotSize > 0 ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Lot Size</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${formatNumber(lotSize)} sqft</div>
                    </div>
                ` : ''}
                
                ${buildingSize > 0 ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Building Size</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${formatNumber(buildingSize)} sqft</div>
                    </div>
                ` : ''}
                
                ${residentialUnits > 0 ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Residential Units</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${residentialUnits}</div>
                    </div>
                ` : ''}
                
                ${commercialUnits > 0 ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Commercial Units</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${commercialUnits}</div>
                    </div>
                ` : ''}
                
                ${building.land_use ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Land Use Code</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${building.land_use}</div>
                    </div>
                ` : ''}
                
                ${building.building_class ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Building Class</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${building.building_class}</div>
                    </div>
                ` : ''}
                
                ${building.num_floors ? `
                    <div style="padding: 1rem; background: var(--bg-tertiary); border-radius: var(--radius-md);">
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-bottom: 0.5rem;">Number of Floors</div>
                        <div style="font-size: 1.25rem; font-weight: 600; color: var(--text-primary);">${building.num_floors}</div>
                    </div>
                ` : ''}
            </div>
        </div>
        
        <!-- Recent Transaction History -->
        ${recentSales.length > 0 ? `
            <div class="info-card">
                <h3 class="card-title" style="display: flex; align-items: center; gap: 0.5rem; margin-bottom: var(--spacing-lg);">
                    <span style="font-size: 1.5rem;">📈</span>
                    <span>Recent Transactions</span>
                </h3>
                
                <div style="overflow-x: auto;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <thead>
                            <tr style="border-bottom: 2px solid var(--border-color);">
                                <th style="padding: 0.75rem; text-align: left; font-size: 0.875rem; color: var(--text-muted); font-weight: 600;">Date</th>
                                <th style="padding: 0.75rem; text-align: left; font-size: 0.875rem; color: var(--text-muted); font-weight: 600;">Type</th>
                                <th style="padding: 0.75rem; text-align: right; font-size: 0.875rem; color: var(--text-muted); font-weight: 600;">Amount</th>
                                <th style="padding: 0.75rem; text-align: left; font-size: 0.875rem; color: var(--text-muted); font-weight: 600;">Document ID</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${recentSales.map(t => `
                                <tr style="border-bottom: 1px solid var(--border-color);">
                                    <td style="padding: 0.75rem; color: var(--text-primary); font-weight: 500;">
                                        ${t.recorded_date ? formatDate(t.recorded_date) : '-'}
                                    </td>
                                    <td style="padding: 0.75rem;">
                                        <span style="background: var(--bg-tertiary); color: var(--text-primary); padding: 0.25rem 0.75rem; border-radius: var(--radius-sm); font-size: 0.875rem; font-weight: 600;">
                                            ${t.doc_type || '-'}
                                        </span>
                                    </td>
                                    <td style="padding: 0.75rem; text-align: right; font-weight: 600; color: var(--text-primary);">
                                        ${t.doc_amount > 0 ? '$' + formatCurrency(t.doc_amount) : '-'}
                                    </td>
                                    <td style="padding: 0.75rem; color: var(--text-secondary); font-family: monospace; font-size: 0.875rem;">
                                        ${t.document_id || '-'}
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>
            </div>
        ` : ''}
        
        ${assessedTotal === 0 && lastSalePrice === 0 ? `
            <div class="info-card">
                <p style="color: var(--text-muted); text-align: center; padding: var(--spacing-xl);">
                    No financial data available for this property.
                </p>
            </div>
        ` : ''}
    `;
}

function populateMarket() {
    const marketData = document.getElementById('marketData');
    marketData.innerHTML = `
        <div class="info-card">
            <h3 class="card-title">Market Context</h3>
            <p style="color: var(--text-muted); margin-bottom: var(--spacing-lg);">
                Market analytics coming soon, including:
            </p>
            <ul style="color: var(--text-secondary); padding-left: var(--spacing-lg);">
                <li>Neighborhood comparable</li>
                <li>Price per square foot trends</li>
                <li>Market velocity indicators</li>
                <li>Zoning analysis</li>
                <li>Development pipeline</li>
            </ul>
        </div>
    `;
}

// =========================
// BUTTONS & ACTIONS
// =========================

function initializeButtons() {
    document.getElementById('backBtn').addEventListener('click', () => {
        window.history.back();
    });
    
    document.getElementById('savePropertyBtn').addEventListener('click', () => {
        alert('Save feature coming soon!');
    });
    
    document.getElementById('exportBtn').addEventListener('click', () => {
        exportPropertyReport();
    });
    
    document.getElementById('shareBtn').addEventListener('click', () => {
        alert('Share feature coming soon!');
    });
    
    document.getElementById('exportPermitsBtn')?.addEventListener('click', () => {
        exportPermits();
    });
    
    document.getElementById('exportSalesBtn')?.addEventListener('click', () => {
        exportSales();
    });
}

function exportPropertyReport() {
    alert('Export full property report feature coming soon!');
}

function exportPermits() {
    if (!propertyData || !propertyData.permits || propertyData.permits.length === 0) {
        alert('No permits to export');
        return;
    }
    
    const csv = convertToCSV(propertyData.permits);
    downloadCSV(csv, `permits_${BBL}.csv`);
}

function exportSales() {
    if (!propertyData || !propertyData.transactions || propertyData.transactions.length === 0) {
        alert('No sales to export');
        return;
    }
    
    const sales = propertyData.transactions.filter(t => t.doc_type && t.doc_type.includes('DEED'));
    const csv = convertToCSV(sales);
    downloadCSV(csv, `sales_${BBL}.csv`);
}

// =========================
// UTILITY FUNCTIONS
// =========================

function formatBBL(bbl) {
    if (!bbl) return '';
    const bblStr = bbl.toString();
    if (bblStr.length === 10) {
        return `${bblStr[0]}-${bblStr.substr(1, 5)}-${bblStr.substr(6, 4)}`;
    }
    return bblStr;
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('en-US', {
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(amount);
}

function formatNumber(num) {
    return new Intl.NumberFormat('en-US').format(num);
}

function convertToCSV(data) {
    if (!data || data.length === 0) return '';
    
    const headers = Object.keys(data[0]);
    const rows = data.map(row => 
        headers.map(header => {
            const value = row[header];
            return value !== null && value !== undefined ? `"${value}"` : '';
        }).join(',')
    );
    
    return [headers.join(','), ...rows].join('\n');
}

function downloadCSV(csv, filename) {
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    window.URL.revokeObjectURL(url);
}

function showError(message) {
    alert(message);
}
