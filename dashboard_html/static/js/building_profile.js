/**
 * Building Intelligence Profile - Social Media Style Interface
 * Comprehensive property data display with transparent risk scoring
 */

let buildingData = null;
let ownerHistoryFilter = 'all';

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

// Service-of-Process / Registered Agents are NOT the property owner — they're
// the contact designated to receive legal mail. Mirrors the server-side check
// in enrichment_service.is_sos_agent_title().
const SOS_AGENT_TITLES = new Set(['SERVICE OF PROCESS AGENT', 'REGISTERED AGENT']);
function isSosAgentTitle(title) {
    if (!title) return false;
    return SOS_AGENT_TITLES.has(String(title).trim().toUpperCase());
}

function isDeedDocument(docType) {
    return String(docType || '').toUpperCase().includes('DEED');
}

// Display-only fallback for older cached API responses. New responses carry
// the server's stricter entity_kind/is_person classification, and the server
// remains the final authority before any paid enrichment request.
function looksLikeHumanName(name) {
    const value = String(name || '').trim();
    if (!value || /[0-9;&]/.test(value)) return false;
    const organizationTerms = /\b(LLC|INC(?:ORPORATED)?|CORP(?:ORATION)?|LTD|LIMITED|COMPANY|BANK|BANC|MORTGAGE|LENDING|FINANCIAL|FINANCE|FUNDING|SERVICING|TRUST|TRUSTEE|FUND|ASSOCIATION|AUTHORITY|CREDIT\s+UNION|FANNIE\s+MAE|FREDDIE\s+MAC|MERS)\b/i;
    if (organizationTerms.test(value)) return false;
    const words = value.replace(',', ' ').split(/\s+/).filter(Boolean);
    return words.length >= 2 && words.length <= 7;
}

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

function hasFactValue(value) {
    return value !== null && value !== undefined && value !== '';
}

function formatFactNumber(value, maximumFractionDigits = 2) {
    if (!hasFactValue(value)) return null;
    const parsed = typeof value === 'string' ? Number(value) : value;
    if (!Number.isFinite(parsed)) return String(value);
    return parsed.toLocaleString('en-US', { maximumFractionDigits });
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function safeHttpHref(value) {
    if (!value) return null;
    try {
        const url = new URL(String(value), window.location.origin);
        return ['http:', 'https:'].includes(url.protocol) ? url.href : null;
    } catch (_error) {
        return null;
    }
}

// Manual people lookup: opening this link never calls the paid enrichment API.
function truePeopleSearchUrl(name, person = {}) {
    const isPerson = person.is_person ?? (person.entity_kind
        ? person.entity_kind === 'person' : looksLikeHumanName(name));
    if (!isPerson || !String(name || '').trim()) return null;

    // ACRIS/RPAD use LAST, FIRST; keep suffixes in FIRST LAST, JR order.
    const searchName = normalizePeopleSearchName(name);

    // Prefer this person's recorded city/state; otherwise use the property area.
    const building = buildingData?.building || {};
    const city = String(person.city || '').trim();
    const state = String(person.state || '').trim();
    const zip = String(person.zip_code || person.zip || '').match(/^\d{5}\b/)?.[0];
    let location = city ? [city, state].filter(Boolean).join(', ') : zip || state;
    if (!location) {
        const borough = String(building.borough_name || building.borough || '').trim();
        const area = /^[1-5]$/.test(borough) ? getBoroughName(borough) : borough;
        const propertyZip = String(building.zip_code || '').trim().match(/^\d{5}\b/)?.[0];
        location = propertyZip || (area && !/^unknown$/i.test(area)
            ? `${/^manhattan$/i.test(area) ? 'New York' : area}, NY` : 'NY');
    }

    return buildPeopleSearchUrl(searchName, location);
}

function renderTruePeopleSearchLink(name, person = {}) {
    const url = truePeopleSearchUrl(name, person);
    if (!url) return '';
    const label = `Preview search for ${name} on TruePeopleSearch`;
    const payload = {name, is_person: true, city: person.city, state: person.state, zip_code: person.zip_code || person.zip};
    return `<button type="button" class="truepeople-search-link" data-preview-url="${escapeHtml(url)}"
        data-people-search="${escapeHtml(JSON.stringify(payload))}" ${researchPersonBlocked({name, ...person}) ? 'disabled' : ''} aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}">Enrich via TruePeopleSearch <span aria-hidden="true">↗</span></button>`;
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

// Show license info popup with other permits from same license
async function showLicenseInfo(licenseNumber, licenseType) {
    // Close existing permit modal if open
    const existingModal = document.querySelector('.permit-modal');
    if (existingModal) {
        existingModal.remove();
    }
    
    // Create loading modal
    const modal = document.createElement('div');
    modal.className = 'permit-modal license-modal';
    modal.innerHTML = `
        <div class="permit-modal-content">
            <button class="modal-close" onclick="this.closest('.permit-modal').remove()">&times;</button>
            <h2>License #${licenseNumber}${licenseType ? ` (${licenseType})` : ''}</h2>
            <div class="license-loading">Loading permits by this licensee...</div>
        </div>
    `;
    document.body.appendChild(modal);
    
    try {
        const response = await fetch(`/api/license/${licenseNumber}/permits`);
        const data = await response.json();
        
        if (!data.success) {
            modal.querySelector('.license-loading').innerHTML = `<div class="error">Error: ${data.error}</div>`;
            return;
        }
        
        let html = `
            <div class="license-summary">
                <div class="license-stat"><span class="stat-value">${data.total_permits}</span><span class="stat-label">Total Permits</span></div>
                <div class="license-stat"><span class="stat-value">${data.unique_buildings}</span><span class="stat-label">Buildings</span></div>
                ${data.contractor_name ? `<div class="license-stat"><span class="stat-value">${data.contractor_name}</span><span class="stat-label">Contractor</span></div>` : ''}
            </div>
            <h3>Recent Permits</h3>
            <div class="license-permits-list">
        `;
        
        for (const permit of data.permits.slice(0, 10)) {
            html += `
                <div class="license-permit-item">
                    <div class="permit-item-header">
                        <a href="/property/${permit.bbl}" class="permit-address">${permit.address || 'Unknown Address'}</a>
                        <span class="permit-date-small">${permit.issue_date ? formatDate(permit.issue_date) : 'No date'}</span>
                    </div>
                    <div class="permit-item-details">
                        <span class="permit-type-badge">${permit.job_type || 'Permit'}</span>
                        <span class="permit-no-small">#${permit.permit_no}</span>
                    </div>
                </div>
            `;
        }
        
        if (data.permits.length > 10) {
            html += `<div class="more-permits">+ ${data.permits.length - 10} more permits</div>`;
        }
        
        html += '</div>';
        modal.querySelector('.permit-modal-content').innerHTML = `
            <button class="modal-close" onclick="this.closest('.permit-modal').remove()">&times;</button>
            <h2>License #${licenseNumber}${licenseType ? ` (${licenseType})` : ''}</h2>
            ${html}
        `;
    } catch (error) {
        modal.querySelector('.license-loading').innerHTML = `<div class="error">Failed to load license data</div>`;
    }
}

// ============================================================================
// INITIALIZATION
// ============================================================================

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Loading building profile for BBL:', BBL);
    
    // Setup tab navigation
    setupProfileDisclosures();
    setupTabNavigation();
    
    // Setup modal
    setupRiskModal();

    // Keep the top of the profile compact while making the full tax-lot
    // record one clear action away on every screen size.
    setupBuildingFactsDisclosure();
    setupSourceCopyButtons();
    setupOwnerResearch();
    
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
        renderGlanceStrip();
        renderSignalsCard();
        renderOverviewTab();
        renderFinancialsTab();
        renderOwnersTab();
        renderTransactionsTab();
        renderPermitsTab();
        renderViolationsTab();
        renderActivityTab();
        renderContactsTab();
        renderDataSourceDirectory();
        loadOwnerResearch();
        loadOwnerSourceStatus();

        // Update tab badges
        updateTabBadges();

        // Fetch live violation details only if the section is open.
        setupViolationsLazyLoad();

        // Opening a shared section link before data arrives can shift its
        // position. Align it again once the profile has rendered.
        const linkedSection = window.location.hash.slice(1).replace(/^tab-/, '');
        if (document.getElementById(`tab-${linkedSection}`)) switchTab(linkedSection);

        // Refresh high-value physical facts independently of the nightly row.
        // The rest of the dossier stays usable if NYC Open Data is slow.
        loadLiveBuildingFacts();
        
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
    const counts = {
        activity: (activity_timeline || []).length,
        permits: (permits || []).length,
        transactions: (transactions || []).length,
        violations: ['hpd_total_violations', 'ecb_violation_count',
            'dob_violation_count', 'dob_safety_violation_count']
            .reduce((sum, key) => sum + (Number(building[key]) || 0), 0),
    };
    Object.entries(counts).forEach(([key, count]) => {
        const node = document.getElementById(`${key}-section-count`);
        if (node) node.textContent = count.toLocaleString();
    });
    
    // Owners badge - count of owner sources
    const ownerCount = [
        building.sale_buyer_primary,
        building.current_owner_name,
        building.owner_name_rpad,
        building.owner_name_hpd,
        building.ecb_respondent_name,
        building.sos_principal_name
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
                           (building.dob_violation_count || 0) +
                           (building.dob_safety_violation_count || 0);
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

function renderSourceHelp(source) {
    const hint = source?.hint ? `<small class="record-source-hint">${escapeHtml(source.hint)}</small>` : '';
    const value = String(source?.lookup_value || '').trim();
    const copyLabel = `Copy ${source?.lookup_label || 'ID'}`;
    const copy = value ? `<button type="button" class="record-source-copy" data-source-copy="${escapeHtml(value)}" data-copy-label="${escapeHtml(copyLabel)}" aria-label="${escapeHtml(copyLabel)}" aria-live="polite">${escapeHtml(copyLabel)}</button>` : '';
    return hint || copy ? `<span class="record-source-help">${hint}${copy}</span>` : '';
}

function setupSourceCopyButtons() {
    document.addEventListener('click', async (event) => {
        const button = event.target?.closest?.('[data-source-copy]');
        if (!button) return;
        try {
            await navigator.clipboard.writeText(button.dataset.sourceCopy);
            button.textContent = 'Copied';
            button.setAttribute('aria-label', 'Copied');
            setTimeout(() => {
                button.textContent = button.dataset.copyLabel || 'Copy ID';
                button.setAttribute('aria-label', button.textContent);
            }, 2200);
        } catch (_error) {
            button.textContent = 'Select the value above';
            button.setAttribute('aria-label', button.textContent);
        }
    });
}

function renderSourceName(name, source, showHint = true) {
    const url = safeHttpHref(source?.url);
    if (!url) return escapeHtml(name || '');
    return `<a class="record-source-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" title="${escapeHtml(source.label || 'View source')} (opens in a new tab)">${escapeHtml(name)} <span aria-hidden="true">↗</span></a>${showHint ? renderSourceHelp(source) : ''}`;
}

function ownerSourceName(source, name) {
    return renderSourceName(name, buildingData.owner_source_links?.[source]);
}

function renderOwnerSourceDate(source) {
    const info = buildingData.owner_source_dates?.[source] || {};
    const reported = info.reported_date ? formatDate(info.reported_date) : null;
    const hasReportedDate = reported && reported !== 'Unknown';
    const reportText = hasReportedDate
        ? `${info.date_label || 'Last reported'}: ${reported}`
        : info.period || 'Last reported: date unavailable';
    const checked = info.checked_at ? formatDate(info.checked_at) : null;
    return `<span class="owner-source-date"${info.note ? ` title="${escapeHtml(info.note)}"` : ''}>
        ${escapeHtml(reportText)}${hasReportedDate && info.period ? ` · ${escapeHtml(info.period)}` : ''}
        ${checked && checked !== 'Unknown' ? `<span class="owner-source-checked">Last checked: ${escapeHtml(checked)}</span>` : ''}
        ${info.refresh_failed ? '<span class="owner-source-warning">Refresh unavailable · showing last saved record</span>' : ''}
    </span>`;
}

function ownerSourceKey(label) {
    if (/secretary/i.test(label)) return 'sos';
    if (/acris/i.test(label)) return 'acris';
    if (/pluto/i.test(label)) return 'pluto';
    if (/rpad|historical tax/i.test(label)) return 'rpad';
    if (/hpd/i.test(label)) return 'hpd';
    if (/ecb/i.test(label)) return 'ecb';
    return null;
}

function transactionSourceName(transaction) {
    return renderSourceName(transaction.document_id, {
        url: transaction.source_url, label: 'View recorded document in ACRIS'
    });
}

function renderDataSourceDirectory() {
    const container = document.getElementById('source-directory');
    if (!container) return;
    const links = buildingData.owner_source_links || {};
    const rows = [
        ['PLUTO building facts', links.pluto, 'Direct lot page'],
        ['Historical RPAD assessment', links.rpad, 'Search BBL; through FY2018/19'],
        ['HPD registration & violations', links.hpd, 'Search address'],
        ['ACRIS deeds & mortgages', links.acris_parcel, 'Direct parcel search'],
        ['BIS jobs & permits', links.bis, links.bis?.hint ? 'Choose building' : 'Direct building profile'],
        ['OATH/ECB violations', links.ecb, links.ecb?.hint ? 'Choose building' : 'Direct violation list'],
        ['DOB NOW filings & Safety', links.dob_now, 'Search job number'],
        ['NY Secretary of State', links.sos, 'Search DOS ID or entity'],
    ];
    container.innerHTML = rows.map(([label, source, timing]) => {
        const url = safeHttpHref(source?.url);
        const name = url
            ? `<a class="record-source-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" aria-label="${escapeHtml(label)} (opens in a new tab)">${escapeHtml(label)} <span aria-hidden="true">↗</span></a>`
            : `<span>${escapeHtml(label)}</span>`;
        return `<div class="source-row">${name}<span class="source-when">${escapeHtml(timing)}</span></div>`;
    }).join('');
}

function renderHeroSection() {
    const { building, building_class_description, owners, sos_data, risk_assessment } = buildingData;
    
    // Full Address with Borough and Zip
    const addressParts = [building.address || 'Address Unknown'];
    if (building.borough_name) {
        addressParts.push(building.borough_name);
    }
    if (building.zip_code) {
        addressParts.push('NY ' + building.zip_code);
    }
    
    document.getElementById('building-address').innerHTML = `
        <span class="address-street">${building.address || 'Address Unknown'}</span>
        ${building.borough_name || building.zip_code ? `<span class="address-city">${building.borough_name || ''}${building.borough_name && building.zip_code ? ', ' : ''}${building.zip_code ? 'NY ' + building.zip_code : ''}</span>` : ''}
    `;
    document.getElementById('bbl-display').textContent = building.bbl;

    const crumb = document.getElementById('crumb-borough');
    if (crumb) crumb.textContent = building.borough_name || 'NYC';

    // BIN and building-class chips only render when we actually have them.
    const binDisplay = document.getElementById('bin-display');
    if (building.bin) {
        binDisplay.textContent = 'BIN ' + building.bin;
        binDisplay.style.display = '';
    } else {
        binDisplay.style.display = 'none';
    }
    const classChip = document.getElementById('class-chip');
    if (classChip && building.building_class) {
        classChip.textContent = `${building.building_class} · ${building_class_description}`;
        classChip.style.display = '';
    }
    
    // Risk Score with color coding
    const riskCard = document.getElementById('risk-score-card');
    const riskValue = document.getElementById('risk-score-value');
    const riskLabel = document.getElementById('risk-score-label');
    
    riskValue.textContent = risk_assessment.score;
    riskLabel.textContent = risk_assessment.label;
    riskCard.className = `risk-score-card risk-${risk_assessment.color}`;
    
    // Owner Sources (ALL sources with attribution)
    const ownerSourcesEl = document.getElementById('owner-sources');
    ownerSourcesEl.innerHTML = '';
    
    const sourceLabels = {
        'acris': 'ACRIS Latest Deed Grantee',
        'pluto': 'NYC PLUTO Database',
        'rpad': 'Historical RPAD Assessment',
        'hpd': 'HPD Registered Owner',
        'ecb': 'ECB Violation Respondent'
    };
    
    // Show SOS data first if available (most valuable intel)
    if (sos_data && sos_data.principal_name) {
        const sosItem = document.createElement('div');
        const isAgent = isSosAgentTitle(sos_data.principal_title);
        // Real-person signal: a person-shaped name AND not an agent title.
        const isMismatch = sos_data.entity_match === 'mismatch';
        const isRealPerson = !isAgent && !isMismatch &&
            (sos_data.is_person === true ||
             (sos_data.is_person === undefined && looksLikeHumanName(sos_data.principal_name)));
        // Tone down the yellow highlight when the SOS hit is just an agent —
        // they're not the owner, so we shouldn't make them look like the answer.
        // The registered entity may not be the company any of our owner
        // fields name — the lookup used to accept the first Active search hit
        // without checking. A mismatch means these people run some OTHER
        // company, so the row is demoted and called out rather than shown as
        // the answer.
        sosItem.className = 'owner-item sos-highlight'
            + (isAgent ? ' sos-agent' : '')
            + (isMismatch ? ' sos-mismatch' : '');

        sosItem.innerHTML = `
            <span class="owner-source sos-source">
                NY Secretary of State
                ${isRealPerson ? '<span class="real-person-badge">REAL PERSON</span>' : ''}
                ${isAgent ? '<span class="agent-badge" title="Designated for service of process — not the property owner">AGENT</span>' : ''}
                ${isMismatch ? '<span class="mismatch-badge" title="The registered company does not match any owner name on record for this property">UNVERIFIED</span>' : ''}
            </span>
            <span class="owner-name sos-name">${ownerSourceName('sos', sos_data.principal_name)}</span>
            ${renderOwnerSourceDate('sos')}
            ${isRealPerson ? renderTruePeopleSearchLink(sos_data.principal_name, sos_data.principal_address || {}) : ''}
            ${sos_data.principal_title ? `<span class="sos-title">${sos_data.principal_title}</span>` : ''}
            <span class="sos-entity">Behind: ${sos_data.entity_name || 'LLC'} (${sos_data.entity_status || 'Unknown'})</span>
            ${sos_data.lookup_source ? `<span class="sos-provenance">Looked up from ${sos_data.lookup_source}</span>` : ''}
            ${isMismatch ? `<span class="sos-warning">
                This company does not match any owner name on record here.
                Treat these contacts as unverified.
            </span>` : ''}
        `;
        ownerSourcesEl.appendChild(sosItem);
    }

    Object.entries(owners).forEach(([source, name]) => {
        if (name) {
            const ownerItem = document.createElement('div');
            ownerItem.className = 'owner-item';
            ownerItem.innerHTML = `
                <span class="owner-source">${sourceLabels[source]}</span>
                <span class="owner-person-actions">
                    <span class="owner-name">${ownerSourceName(source, name)}</span>
                    ${renderOwnerSourceDate(source)}
                    ${renderTruePeopleSearchLink(name, buildingData.owner_classifications?.[source] || {})}
                </span>
            `;
            ownerSourcesEl.appendChild(ownerItem);
        }
    });
    
    // If no owners found
    if (ownerSourcesEl.children.length === 0) {
        ownerSourcesEl.innerHTML = '<div class="no-data">No owner information available</div>';
    }
    
    // Add Enrich Owner button after owner list
    addEnrichOwnerButton(ownerSourcesEl);
}

// ============================================================================
// GLANCE STRIP + SIGNALS (dossier header widgets)
// ============================================================================

function renderGlanceStrip() {
    const { building } = buildingData;
    const strip = document.getElementById('glance-strip');
    if (!strip) return;

    const openViolations = (building.hpd_open_violations || 0) +
                           (building.ecb_open_violations || 0) +
                           (building.dob_open_violations || 0) +
                           (building.dob_safety_open_violations || 0);

    const tiles = [
        { label: 'Assessed value',
          value: building.assessed_total_value ? formatLargeNumber(building.assessed_total_value) : '—' },
        { label: building.sale_date ? `Last sale · ${String(building.sale_date).slice(0, 4)}` : 'Last sale',
          value: building.sale_price ? formatLargeNumber(building.sale_price) : '—' },
        { label: 'Financing',
          value: building.is_cash_purchase ? 'Likely cash'
               : (building.financing_ratio !== null && building.financing_ratio !== undefined)
                   ? `${(building.financing_ratio * 100).toFixed(1)}%` : '—' },
        { label: 'Units', value: building.total_units ? formatNumber(building.total_units) : '—' },
        { label: 'Year built', value: building.year_built || '—' },
        { label: 'Open violations', value: formatNumber(openViolations),
          tone: openViolations > 0 ? 'warn' : 'ok' },
    ];

    strip.innerHTML = tiles.map(t => `
        <div class="glance-tile">
            <span class="glance-label">${t.label}</span>
            <span class="glance-value ${t.tone || ''}">${t.value}</span>
        </div>`).join('');
}

function renderSignalsCard() {
    const { building } = buildingData;
    const card = document.getElementById('signals-card');
    const list = document.getElementById('signals-list');
    if (!card || !list) return;

    // Ordered by how loudly each one should speak; only real values render,
    // and the card stays hidden when the signals pipeline hasn't run yet.
    const signals = [];
    if (building.on_speculation_watch_list) {
        signals.push({ tone: 'red', text: 'On the HPD speculation watch list' });
    }
    if (building.has_tax_delinquency) {
        signals.push({ tone: 'amber',
                       text: `Lien-sale notice — ${building.tax_delinquency_count} notice(s)${building.tax_delinquency_water_only ? ' (water only)' : ''}` });
    }
    if (building.ecb_total_balance > 0) {
        signals.push({ tone: 'amber', text: `ECB balance outstanding — $${formatNumber(building.ecb_total_balance)}` });
    }
    if (building.litigation_open_count > 0) {
        signals.push({ tone: 'amber', text: `${building.litigation_open_count} open HPD litigation case(s)` });
    }
    if (building.eviction_count > 0) {
        signals.push({ tone: 'amber', text: `${building.eviction_count} marshal eviction(s) on record` });
    }
    if (building.dob_active_complaint_count > 0) {
        signals.push({ tone: 'amber', text: `${building.dob_active_complaint_count} active DOB complaint(s)` });
    }
    if (building.is_free_and_clear) {
        signals.push({ tone: 'green', text: 'Free and clear — no open mortgage' });
    } else if (building.open_mortgage_count > 1) {
        signals.push({ tone: 'amber', text: `${building.open_mortgage_count} open mortgages` });
    }
    if (building.unused_far && Number(building.unused_far) >= 0.5 && building.max_resid_far) {
        signals.push({ tone: 'accent',
                       text: `Unused FAR — ${Number(building.unused_far).toFixed(1)} of ${Number(building.max_resid_far).toFixed(1)} buildable remains` });
    }
    if (building.has_senior_exemption || building.has_disabled_exemption) {
        signals.push({ tone: 'accent', text: 'Senior/disabled tax exemption on file' });
    }
    if (building.latest_co_date) {
        signals.push({ tone: 'green', text: `Certificate of occupancy issued ${formatDate(building.latest_co_date)}` });
    }
    if (building.fisp_status) {
        signals.push({ tone: 'neutral', text: `Facade (FISP): ${building.fisp_status}${building.fisp_cycle ? ` — cycle ${building.fisp_cycle}` : ''}` });
    }

    if (!signals.length) return;
    card.style.display = '';
    list.innerHTML = signals.slice(0, 7).map(s => `
        <div class="signal-row signal-${s.tone}">
            <span class="signal-dot"></span>
            <span>${s.text}</span>
        </div>`).join('');
}

// ============================================================================
// OWNER ENRICHMENT
// ============================================================================

function addEnrichOwnerButton(container) {
    const building = buildingData.building;
    const buildingId = building.id;
    
    // Use pre-loaded enrichment data from building profile API (no separate API call needed)
    const enrichmentData = buildingData.enrichment;
    
    if (!enrichmentData) return;
    
    // Create enrichment section
    const enrichSection = document.createElement('div');
    enrichSection.className = 'enrich-owner-section';
    
    const hasEnrichedDataPerOwner = enrichmentData.enrichment_data_per_owner && enrichmentData.enrichment_data_per_owner.length > 0;
    const hasEnrichedData = hasEnrichedDataPerOwner || (enrichmentData.already_enriched && enrichmentData.enrichment_data);
    const hasAvailableOwners = enrichmentData.available_owners && enrichmentData.available_owners.length > 0;
    const hasEnrichedOwners = enrichmentData.enriched_owners && enrichmentData.enriched_owners.length > 0;
    
    let html = '';
    
    // Show already unlocked data if any - prefer per-owner display
    if (hasEnrichedDataPerOwner) {
        html += `
            <div class="enriched-data-box">
                <h4>Owner Contact Info <span class="unlocked-badge">UNLOCKED</span></h4>
                ${renderEnrichedDataPerOwner(enrichmentData.enrichment_data_per_owner)}
            </div>
        `;
    } else if (hasEnrichedData) {
        // Fallback to combined data for backward compatibility
        html += `
            <div class="enriched-data-box">
                <h4>Owner Contact Info <span class="unlocked-badge">UNLOCKED</span></h4>
                ${renderEnrichedData(enrichmentData.enrichment_data)}
            </div>
        `;
    }
    
    // Show button to enrich more owners if available
    if (hasAvailableOwners) {
        if (!enrichmentData.logged_in) {
            html += `
                <div class="enrich-prompt">
                    <a href="/login?next=${encodeURIComponent(window.location.pathname)}" class="enrich-owner-btn login-required">
                        Get Owner Phone & Email
                        <span class="enrich-cost">Login Required</span>
                    </a>
                    <p class="enrich-note">Sign in to unlock owner contact information</p>
                </div>
            `;
        } else {
            // Show enrich button for logged in users
            const cost = enrichmentData.cost === 0 ? 'FREE' : `$${enrichmentData.cost.toFixed(2)}`;
            const batchCost = enrichmentData.batch_cost || enrichmentData.cost;
            const batchDisplay = batchCost === 0 ? '' : ` ($${batchCost.toFixed(2)} in bulk)`;
            const btnText = hasEnrichedOwners ? 'Enrich More Owners' : 'Get Owner Phone & Email';
            html += `
                <div class="enrich-prompt">
                    <button class="enrich-owner-btn" onclick="showEnrichModal(${buildingId})">
                        ${btnText}
                        <span class="enrich-cost">${cost} each${batchDisplay}</span>
                    </button>
                    <p class="enrich-note">${enrichmentData.available_owners.length} verified human candidate(s) available to look up</p>
                </div>
            `;
        }
    }
    
    if (!html.trim()) return;  // nothing to offer — don't render an empty box
    enrichSection.innerHTML = html;
    
    container.appendChild(enrichSection);
}

function renderEnrichedDataPerOwner(dataList) {
    // Render enrichment data grouped by owner
    let html = '';
    
    dataList.forEach((ownerData, index) => {
        const ownerName = ownerData.owner_name || 'Unknown Owner';
        html += `<div class="owner-contacts-group ${index > 0 ? 'owner-divider' : ''}">`;
        html += `<div class="owner-name-header">${ownerName}</div>`;
        html += '<div class="enriched-contacts">';
        
        if (ownerData.phones && ownerData.phones.length > 0) {
            html += '<div class="enriched-phones">';
            ownerData.phones.forEach(phone => {
                html += `
                    <a href="tel:${phone.number}" class="contact-link phone-link">
                        ${formatPhoneNumber(phone.number)}
                        <span class="phone-type">${phone.type || ''}</span>
                    </a>
                `;
            });
            html += '</div>';
        }
        
        if (ownerData.emails && ownerData.emails.length > 0) {
            html += '<div class="enriched-emails">';
            ownerData.emails.forEach(email => {
                html += `
                    <a href="mailto:${email.email}" class="contact-link email-link">
                        ${email.email}
                    </a>
                `;
            });
            html += '</div>';
        }
        
        if ((!ownerData.phones || ownerData.phones.length === 0) && (!ownerData.emails || ownerData.emails.length === 0)) {
            html += '<p class="no-contacts">No contact info found</p>';
        }
        
        html += '</div></div>';
    });
    
    return html;
}

function renderEnrichedData(data) {
    // Combined render. These contacts can come from more than one person —
    // an agent and an owner both get looked up — so each carries the name it
    // was found under. Never show a bare number here; you cannot tell whose
    // it is before you dial.
    let html = '<div class="enriched-contacts">';

    if (data.phones && data.phones.length > 0) {
        html += '<div class="enriched-phones">';
        data.phones.forEach(phone => {
            html += `
                <a href="tel:${phone.number}" class="contact-link phone-link">
                    ${formatPhoneNumber(phone.number)}
                    <span class="phone-type">${phone.type || ''}</span>
                    ${phone.owner_name ? `<span class="contact-owner">${phone.owner_name}</span>` : ''}
                </a>
            `;
        });
        html += '</div>';
    }

    if (data.emails && data.emails.length > 0) {
        html += '<div class="enriched-emails">';
        data.emails.forEach(email => {
            html += `
                <a href="mailto:${email.email}" class="contact-link email-link">
                    ${email.email}
                    ${email.owner_name ? `<span class="contact-owner">${email.owner_name}</span>` : ''}
                </a>
            `;
        });
        html += '</div>';
    }
    
    html += '</div>';
    return html;
}

function formatPhoneNumber(phone) {
    if (!phone) return '';
    const cleaned = phone.replace(/\D/g, '');
    if (cleaned.length === 10) {
        return `(${cleaned.slice(0,3)}) ${cleaned.slice(3,6)}-${cleaned.slice(6)}`;
    }
    return phone;
}

function showEnrichModal(buildingId) {
    // Use pre-loaded enrichment data
    const enrichmentData = buildingData.enrichment;
    const availableOwners = enrichmentData?.available_owners || [];
    const enrichedOwners = enrichmentData?.enriched_owners || [];
    
    if (availableOwners.length === 0) {
        alert('No more owners available to enrich for this property.');
        return;
    }
    
    // Create and show modal
    const modal = document.createElement('div');
    modal.className = 'modal enrich-modal';
    modal.id = 'enrich-modal';
    modal.style.display = 'block';
    
    const cost = enrichmentData.cost === 0 ? 'FREE (Admin)' : `$${enrichmentData.cost.toFixed(2)}`;
    
    // Build enriched owners section
    let enrichedHtml = '';
    if (enrichedOwners.length > 0) {
        enrichedHtml = `
            <div class="already-enriched-section">
                <h4>Already Enriched</h4>
                ${enrichedOwners.map(owner => `
                    <div class="owner-option enriched disabled">
                        <div class="owner-option-content">
                            <span class="owner-option-name">${escapeHtml(owner.name)}</span>
                            <span class="owner-option-source">${escapeHtml(owner.source)}</span>
                            ${ownerSourceKey(owner.source) ? renderOwnerSourceDate(ownerSourceKey(owner.source)) : ''}
                            <span class="enriched-badge">Unlocked</span>
                            ${renderTruePeopleSearchLink(owner.name, owner)}
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }
    
    // Auto-select first recommended or first available
    const firstRecommendedIdx = availableOwners.findIndex(o => o.recommended && !researchPersonBlocked(o));
    const autoSelectIdx = firstRecommendedIdx >= 0 ? firstRecommendedIdx : availableOwners.findIndex(o => !researchPersonBlocked(o));
    
    modal.innerHTML = `
        <div class="modal-content enrich-modal-content">
            <button type="button" class="modal-close" aria-label="Close owner enrichment" onclick="closeEnrichModal()">&times;</button>
            <h2>Get Owner Contact Information</h2>
            <p class="modal-subtitle">Only confident human names associated with this property are eligible. Companies, banks, trusts, and agents are never sent to the paid lookup.</p>
            
            ${enrichedHtml}
            
            <div class="owner-selection">
                <h4>Human candidates (${availableOwners.length})</h4>
                ${availableOwners.map((owner, idx) => `
                    <div class="owner-option ${owner.recommended ? 'recommended' : ''}">
                        <label class="owner-option-choice">
                            <input type="radio" name="owner" value="${idx}" ${idx === autoSelectIdx ? 'checked' : ''} ${researchPersonBlocked(owner) ? 'disabled' : ''}>
                            <span class="owner-option-content">
                                <span class="owner-option-name">${escapeHtml(owner.name)}</span>
                                <span class="owner-option-source">${escapeHtml(owner.source)}</span>
                                ${ownerSourceKey(owner.source) ? renderOwnerSourceDate(ownerSourceKey(owner.source)) : ''}
                                <span class="real-person-badge">PERSON</span>
                                ${researchPersonBlocked(owner) ? '<span class="research-status research-status-blocked">Do not contact</span>' : ''}
                                ${owner.recommended ? '<span class="recommended-badge">Recommended</span>' : ''}
                                ${owner.reason ? `<span class="owner-option-reason">${escapeHtml(owner.reason)}</span>` : ''}
                            </span>
                        </label>
                        ${renderTruePeopleSearchLink(owner.name, owner)}
                    </div>
                `).join('')}
            </div>
            <p class="enrich-note">TruePeopleSearch opens a name and location search in a new tab. Results are not saved automatically.</p>
            
            <div class="enrich-footer">
                <p class="enrich-cost-display">Contact unlock: <strong>${cost}</strong></p>
                <button class="btn btn-primary enrich-confirm-btn" onclick="confirmEnrich(${buildingId})" ${autoSelectIdx < 0 ? 'disabled' : ''}>
                    Unlock Contact Info
                </button>
            </div>
        </div>
    `;
    
    document.body.appendChild(modal);
    
    // Store owners data for later use
    window.enrichOwners = availableOwners;
}

function closeEnrichModal() {
    const modal = document.getElementById('enrich-modal');
    if (modal) {
        modal.remove();
    }
}

async function confirmEnrich(buildingId) {
    const selectedRadio = document.querySelector('input[name="owner"]:checked');
    if (!selectedRadio) {
        alert('Please select an owner to enrich.');
        return;
    }
    
    const ownerIdx = parseInt(selectedRadio.value);
    const owner = window.enrichOwners[ownerIdx];
    if (researchPersonBlocked(owner)) {
        alert('Do not contact is set for this name. Change its review status before enriching.');
        return;
    }
    
    const btn = document.querySelector('.enrich-confirm-btn');
    btn.disabled = true;
    btn.textContent = 'Processing...';
    
    try {
        // The server resolves street/borough/ZIP from building_id. Do not
        // compose location here: building.borough is the numeric NYC code,
        // not a city name, and client-provided addresses are not authoritative.
        const response = await fetch('/api/enrichment/enrich', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                building_id: buildingId,
                owner_name: owner.name
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            closeEnrichModal();
            
            // Show success and refresh the owner section
            if (data.data) {
                // Update the UI with the new data
                const enrichSection = document.querySelector('.enrich-owner-section');
                if (enrichSection) {
                    enrichSection.innerHTML = `
                        <div class="enriched-data-box success-flash">
                            <h4>Owner Contact Info <span class="unlocked-badge">UNLOCKED</span></h4>
                            ${renderEnrichedData(data.data)}
                        </div>
                    `;
                }
            }
            
            if (data.charged) {
                showNotification('Contact info unlocked! $0.50 charged.', 'success');
            } else {
                showNotification('Contact info retrieved!', 'success');
            }
        } else {
            alert(data.error || 'Failed to enrich owner information.');
            btn.disabled = false;
            btn.textContent = 'Unlock Contact Info';
        }
        
    } catch (error) {
        console.error('Enrichment error:', error);
        alert('An error occurred. Please try again.');
        btn.disabled = false;
        btn.textContent = 'Unlock Contact Info';
    }
}

function showNotification(message, type = 'info') {
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.textContent = message;
    document.body.appendChild(notification);
    
    setTimeout(() => {
        notification.classList.add('fade-out');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
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
        factorsList.innerHTML = '<p class="no-risk-factors">No significant risk factors identified for this property.</p>';
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

// Every former tab is a section on one page now. The nav buttons scroll,
// and a scrollspy keeps the active state honest while the user scrolls
// on their own.
function setupProfileDisclosures() {
    const cards = Array.from(document.querySelectorAll('.profile-disclosure'));
    const storageKey = `property-sections:v1:${BBL}`;
    let saved = {};
    try {
        saved = JSON.parse(window.sessionStorage.getItem(storageKey) || '{}') || {};
    } catch (_error) { /* Storage may be unavailable. Native toggles still work. */ }
    cards.forEach(card => {
        if (typeof saved[card.id] === 'boolean') card.open = saved[card.id];
        card.addEventListener('toggle', () => {
            try {
                const states = Object.fromEntries(cards.map(item => [item.id, item.open]));
                window.sessionStorage.setItem(storageKey, JSON.stringify(states));
            } catch (_error) { /* Persistence is optional. */ }
            if (card.id === 'tab-violations' && card.open) loadViolationDetailsOnce();
        });
    });
    const openHashSection = () => {
        const name = window.location.hash.slice(1).replace(/^tab-/, '');
        if (['overview', 'building', 'owners', 'contacts', 'financials',
             'activity', 'permits', 'transactions', 'violations'].includes(name)) {
            switchTab(name);
        }
    };
    window.addEventListener('hashchange', openHashSection);
    openHashSection();
}

function setupTabNavigation() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            switchTab(btn.getAttribute('data-tab'));
        });
    });

    // Deterministic scrollspy: the active section is the last one whose top
    // has passed the sticky-header line. Ratio-based observers pick the
    // biggest section on screen, which is wrong next to short ones.
    const sections = Array.from(document.querySelectorAll(
        'section[id^="tab-"], details[id^="tab-"]'));
    if (sections.length) {
        let ticking = false;
        const markActive = () => {
            ticking = false;
            // A click told us where we're going; don't let the spy overrule
            // it while the smooth scroll is still travelling (or when the
            // target section can't physically reach the top of the page).
            if (Date.now() < spyHoldUntil) return;
            let current = 'overview';
            for (const s of sections) {
                if (s.getBoundingClientRect().top <= 150) {
                    current = s.id.replace(/^tab-/, '');
                }
            }
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.toggle('active', btn.getAttribute('data-tab') === current);
            });
        };
        window.addEventListener('scroll', () => {
            if (!ticking) {
                ticking = true;
                requestAnimationFrame(markActive);
            }
        }, { passive: true });
    }

    const historyToggle = document.getElementById('toggle-owner-history');
    if (historyToggle) {
        historyToggle.addEventListener('click', () => {
            const panel = document.getElementById('owners-content');
            const open = panel.style.display !== 'none';
            panel.style.display = open ? 'none' : '';
            historyToggle.textContent = open ? 'Ownership history' : 'Hide history';
        });
    }

    document.querySelectorAll('#timeline-filters .pill-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#timeline-filters .pill-btn')
                .forEach(b => b.classList.toggle('active', b === btn));
            const type = btn.getAttribute('data-type');
            document.querySelectorAll('#activity-feed .activity-item').forEach(item => {
                item.style.display =
                    (type === 'all' || item.dataset.eventType === type) ? '' : 'none';
            });
        });
    });
}

let spyHoldUntil = 0;

function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
    });
    spyHoldUntil = Date.now() + 1200;
    const section = document.getElementById(`tab-${tabName}`);
    if (section) {
        if (section.tagName === 'DETAILS') section.open = true;
        if (tabName === 'building') {
            const toggle = document.getElementById('building-facts-toggle');
            if (toggle && toggle.getAttribute('aria-expanded') === 'false') toggle.click();
        }
        const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        section.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
    }
    if (tabName === 'violations') loadViolationDetailsOnce();
}

// The heavy per-violation lists come from live Open Data calls, so they
// load only when the user expands the section or selects it in navigation.
let violationDetailsLoaded = false;

function loadViolationDetailsOnce() {
    if (violationDetailsLoaded || !buildingData) return;
    const { building } = buildingData;
    violationDetailsLoaded = true;

    const ecbContainer = document.getElementById('ecb-violations-container');
    if (ecbContainer && ecbContainer.innerHTML === '' && building.ecb_violation_count > 0) {
        loadECBViolationDetails();
    }
    const hpdContainer = document.getElementById('hpd-violations-container');
    if (hpdContainer && hpdContainer.innerHTML === '' && building.hpd_total_violations > 0) {
        loadHPDViolationDetails();
    }
    // Always check the daily Safety feed. It can contain a new violation even
    // when every stored/legacy count was zero at the last enrichment run.
    const safetyContainer = document.getElementById('dob-safety-violations-container');
    if (safetyContainer && safetyContainer.innerHTML === '') {
        loadSafetyViolationDetails();
    }
}

function setupViolationsLazyLoad() {
    const section = document.getElementById('tab-violations');
    if (section && section.open) loadViolationDetailsOnce();
}

// ============================================================================
// LIVE PLUTO BUILDING + LOT FACTS
// ============================================================================

const PLUTO_LAND_USE = {
    '1': 'One & two family buildings',
    '2': 'Multi-family walk-up buildings',
    '3': 'Multi-family elevator buildings',
    '4': 'Mixed residential & commercial',
    '5': 'Commercial & office buildings',
    '6': 'Industrial & manufacturing',
    '7': 'Transportation & utility',
    '8': 'Public facilities & institutions',
    '9': 'Open space & outdoor recreation',
    '10': 'Parking facilities',
    '11': 'Vacant land',
};

const PLUTO_OWNER_TYPE = {
    C: 'City ownership',
    M: 'Mixed city & private ownership',
    O: 'Public authority, state or federal ownership',
    P: 'Private ownership',
    X: 'Fully tax-exempt ownership',
};

const PLUTO_LOT_TYPE = {
    '0': 'Unknown',
    '1': 'Block assemblage',
    '2': 'Waterfront',
    '3': 'Corner lot',
    '4': 'Through lot',
    '5': 'Inside lot',
    '6': 'Interior lot',
    '7': 'Island lot',
    '8': 'Alley lot',
    '9': 'Submerged land lot',
};

const PLUTO_BASEMENT_TYPE = {
    '0': 'No basement',
    '1': 'Above-grade full basement',
    '2': 'Below-grade full basement',
    '3': 'Above-grade partial basement',
    '4': 'Below-grade partial basement',
    '5': 'Unknown basement type',
};

const PLUTO_PROXIMITY = {
    '0': 'Not available',
    '1': 'Detached',
    '2': 'Semi-attached',
    '3': 'Attached',
};

const PLUTO_EXTENSION = {
    E: 'Extension',
    G: 'Garage',
    EG: 'Extension and garage',
    N: 'None',
};

function setupBuildingFactsDisclosure() {
    const button = document.getElementById('building-facts-toggle');
    const groups = document.getElementById('building-facts-groups');
    if (!button || !groups) return;

    const setExpanded = (expanded) => {
        groups.hidden = !expanded;
        button.setAttribute('aria-expanded', String(expanded));
        button.textContent = expanded ? 'Hide full record' : 'Show full record';
    };

    setExpanded(false);
    button.addEventListener('click', () => {
        setExpanded(button.getAttribute('aria-expanded') !== 'true');
    });
}

function decodedPlutoCode(value, labels) {
    if (!hasFactValue(value)) return null;
    const code = String(value);
    return labels[code] ? `${labels[code]} · ${code}` : code;
}

function factArea(value) {
    const formatted = formatFactNumber(value, 0);
    return formatted === null ? null : `${formatted} sq ft`;
}

function factCurrency(value) {
    const formatted = formatFactNumber(value, 0);
    return formatted === null ? null : `$${formatted}`;
}

function factDimensions(front, depth) {
    if (!hasFactValue(front) && !hasFactValue(depth)) return null;
    if (hasFactValue(front) && hasFactValue(depth)) {
        return `${formatFactNumber(front)} × ${formatFactNumber(depth)} ft`;
    }
    return hasFactValue(front)
        ? `${formatFactNumber(front)} ft frontage`
        : `${formatFactNumber(depth)} ft depth`;
}

function factList(value) {
    if (!Array.isArray(value) || !value.length) return null;
    return value.join(', ');
}

function factRow(label, value, options = {}) {
    if (!hasFactValue(value)) return '';
    const className = options.mono ? 'building-fact-value mono' : 'building-fact-value';
    return `
        <div class="building-fact-row">
            <dt>${escapeHtml(label)}</dt>
            <dd class="${className}">${escapeHtml(value)}</dd>
        </div>`;
}

function factGroup(title, rows) {
    const populated = rows.filter(Boolean);
    if (!populated.length) return '';
    return `
        <article class="building-fact-group">
            <h4>${escapeHtml(title)}</h4>
            <dl>${populated.join('')}</dl>
        </article>`;
}

function renderBuildingFacts(building) {
    const highlightsEl = document.getElementById('building-facts-highlights');
    const groupsEl = document.getElementById('building-facts-groups');
    if (!highlightsEl || !groupsEl) return;

    const nonResidentialUnits = hasFactValue(building.non_residential_units)
        ? building.non_residential_units
        : (hasFactValue(building.total_units) && hasFactValue(building.residential_units)
            ? Math.max(Number(building.total_units) - Number(building.residential_units), 0)
            : null);
    const buildingType = building.building_class
        ? `${building.building_class}${buildingData.building_class_description ? ` · ${buildingData.building_class_description}` : ''}`
        : null;
    const zoningDistricts = factList(building.zoning_districts)
        || building.zoning_district
        || null;

    const highlights = [
        ['Total units', formatFactNumber(building.total_units, 0)],
        ['Residential units', formatFactNumber(building.residential_units, 0)],
        ['Buildings on lot', formatFactNumber(building.number_of_buildings, 0)],
        ['Floors', formatFactNumber(building.num_floors)],
        ['Building area', factArea(building.building_sqft)],
        ['Lot area', factArea(building.lot_sqft)],
    ].filter(([, value]) => hasFactValue(value));

    highlightsEl.innerHTML = highlights.length
        ? highlights.map(([label, value]) => `
            <div class="building-fact-highlight">
                <span>${escapeHtml(label)}</span>
                <strong>${escapeHtml(value)}</strong>
            </div>`).join('')
        : '<div class="building-facts-empty">No physical building facts are stored yet.</div>';

    const useAndScale = [
        factRow('Building class', buildingType),
        factRow('Land use', decodedPlutoCode(building.land_use, PLUTO_LAND_USE)),
        factRow('Year built', building.year_built),
        factRow('First alteration', building.year_altered),
        factRow('Second alteration', building.year_altered_2),
        factRow('Total units', formatFactNumber(building.total_units, 0)),
        factRow('Residential units', formatFactNumber(building.residential_units, 0)),
        factRow('Non-residential units', formatFactNumber(nonResidentialUnits, 0)),
        factRow('Buildings on lot', formatFactNumber(building.number_of_buildings, 0)),
        factRow('Floors', formatFactNumber(building.num_floors)),
    ];

    const floorArea = [
        factRow('Total building area', factArea(building.building_sqft)),
        factRow('Residential area', factArea(building.residential_sqft)),
        factRow('Commercial area', factArea(building.commercial_sqft)),
        factRow('Retail area', factArea(building.retail_sqft)),
        factRow('Office area', factArea(building.office_sqft)),
        factRow('Garage area', factArea(building.garage_sqft)),
        factRow('Storage area', factArea(building.storage_sqft)),
        factRow('Factory area', factArea(building.factory_sqft)),
        factRow('Other area', factArea(building.other_sqft)),
    ];

    const lotAndForm = [
        factRow('Lot area', factArea(building.lot_sqft)),
        factRow('Lot dimensions', factDimensions(building.lot_front_ft, building.lot_depth_ft)),
        factRow('Primary building dimensions', factDimensions(building.building_front_ft, building.building_depth_ft)),
        factRow('Lot type', decodedPlutoCode(building.lot_type_code, PLUTO_LOT_TYPE)),
        factRow('Building relationship', decodedPlutoCode(building.proximity_code, PLUTO_PROXIMITY)),
        factRow('Basement', decodedPlutoCode(building.basement_code, PLUTO_BASEMENT_TYPE)),
        factRow('Extension / garage', decodedPlutoCode(building.extension_code, PLUTO_EXTENSION)),
        factRow('Irregular lot', hasFactValue(building.irregular_lot) ? (building.irregular_lot ? 'Yes' : 'No') : null),
        factRow('Easements', formatFactNumber(building.easement_count, 0)),
    ];

    const zoning = [
        factRow('Zoning district', zoningDistricts, { mono: true }),
        factRow('Commercial overlay', factList(building.commercial_overlays), { mono: true }),
        factRow('Special district', factList(building.special_districts), { mono: true }),
        factRow('Limited-height district', building.limited_height_district, { mono: true }),
        factRow('Split zoning lot', hasFactValue(building.split_zone) ? (building.split_zone ? 'Yes' : 'No') : null),
        factRow('Zoning map', building.zoning_map, { mono: true }),
        factRow('Built FAR', formatFactNumber(building.built_far)),
        factRow('Maximum residential FAR', formatFactNumber(building.max_resid_far)),
        factRow('Affordable residential FAR', formatFactNumber(building.max_affordable_res_far)),
        factRow('Maximum commercial FAR', formatFactNumber(building.max_comm_far)),
        factRow('Maximum facility FAR', formatFactNumber(building.max_facility_far)),
        factRow('Maximum manufacturing FAR', formatFactNumber(building.max_manufacturing_far)),
        factRow('Residential / commercial FAR headroom', formatFactNumber(building.unused_far)),
    ];

    const assessment = [
        factRow('PLUTO owner of record', building.owner_name || building.current_owner_name),
        factRow('Ownership type', decodedPlutoCode(building.pluto_owner_type, PLUTO_OWNER_TYPE)),
        factRow('Assessed land value', factCurrency(building.assessed_land_value_pluto || building.assessed_land_value)),
        factRow('Assessed total value', factCurrency(building.assessed_total_value_pluto || building.assessed_total_value)),
        factRow('Tax-exempt value', factCurrency(building.exempt_total_value)),
        factRow('PLUTO release', building.pluto_version, { mono: true }),
    ];

    const location = [
        factRow('ZIP code', building.zip_code, { mono: true }),
        factRow('Community district', building.community_district),
        factRow('City Council district', building.council_district),
        factRow('School district', building.school_district),
        factRow('Police precinct', building.police_precinct),
        factRow('Fire company', building.fire_company, { mono: true }),
        factRow('Sanitation district', [building.sanitation_district, building.sanitation_subsection].filter(hasFactValue).join(' · ') || null),
        factRow('2020 census tract', building.census_tract_2020, { mono: true }),
        factRow('Transit zone', building.transit_zone),
        factRow('Historic district', building.historic_district),
        factRow('Landmark', building.landmark_name),
        factRow('Environmental designation', building.environmental_designation, { mono: true }),
        factRow('2007 FEMA flood flag', hasFactValue(building.fema_2007_flood_zone) ? (building.fema_2007_flood_zone ? 'Yes' : 'No') : null),
        factRow('2015 preliminary flood flag', hasFactValue(building.preliminary_2015_flood_zone) ? (building.preliminary_2015_flood_zone ? 'Yes' : 'No') : null),
        factRow('Coordinates', hasFactValue(building.latitude) && hasFactValue(building.longitude)
            ? `${Number(building.latitude).toFixed(6)}, ${Number(building.longitude).toFixed(6)}` : null, { mono: true }),
        factRow('Tax map', building.tax_map, { mono: true }),
        factRow('Sanborn map', building.sanborn_map, { mono: true }),
    ];

    groupsEl.innerHTML = [
        factGroup('Use & scale', useAndScale),
        factGroup('Floor area', floorArea),
        factGroup('Lot & building form', lotAndForm),
        factGroup('Zoning & capacity', zoning),
        factGroup('Assessment & ownership', assessment),
        factGroup('Districts, services & flags', location),
    ].filter(Boolean).join('');
}

async function loadLiveBuildingFacts() {
    const sourceEl = document.getElementById('building-facts-source');
    const noteEl = document.getElementById('building-facts-note');
    try {
        const response = await fetch(`/api/property/${BBL}/building-facts`);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'PLUTO building facts unavailable');
        }

        Object.entries(data.facts || {}).forEach(([key, value]) => {
            if (hasFactValue(value)) buildingData.building[key] = value;
        });
        renderBuildingFacts(buildingData.building);
        renderGlanceStrip();

        if (sourceEl) {
            const checked = data.checked_at ? new Date(data.checked_at).toLocaleTimeString([], {
                hour: 'numeric', minute: '2-digit',
            }) : 'just now';
            sourceEl.textContent = `${data.facts.pluto_version || 'Latest PLUTO'} · checked ${checked}`;
            sourceEl.href = data.source.url;
            sourceEl.classList.remove('source-warning');
        }
        if (noteEl) {
            noteEl.textContent = 'Live PLUTO tax-lot record. Condominium units are generally aggregated to the billing lot; floor areas are NYC estimates.';
        }
    } catch (error) {
        console.warn('Live PLUTO building facts unavailable:', error);
        if (sourceEl) {
            sourceEl.textContent = 'Showing nightly PLUTO data · live check unavailable';
            sourceEl.classList.add('source-warning');
        }
        if (noteEl) {
            noteEl.textContent = 'Showing the latest stored tax-lot facts. The live NYC PLUTO check could not be completed; other profile sections are unaffected.';
        }
    }
}

// ============================================================================
// OVERVIEW TAB
// ============================================================================

function renderOverviewTab() {
    const { building, stats } = buildingData;
    renderBuildingFacts(building);
    
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
            <div class="stat-label">Years Owned</div>
        </div>
    `;
    
    // Quick Metrics
    const metricsEl = document.getElementById('quick-metrics');
    const metrics = [];
    
    if (typeof building.is_cash_purchase === 'boolean') {
        metrics.push({
            label: 'Purchase Type',
            value: building.is_cash_purchase ? 'Likely cash purchase' : 'Financed',
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

    // Header flag: the one-line read on how this building is held.
    const flag = document.getElementById('financing-flag');
    if (flag) {
        if (building.is_cash_purchase) {
            flag.textContent = 'Likely cash purchase';
            flag.className = 'fin-flag flag-green';
            flag.style.display = '';
        } else if (building.financing_ratio !== null && building.financing_ratio !== undefined) {
            flag.textContent = `Financed · ${(building.financing_ratio * 100).toFixed(0)}% LTV`;
            flag.className = 'fin-flag flag-accent';
            flag.style.display = '';
        }
    }

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
    if (building.mortgage_amount && building.has_open_mortgage) {
        html += `
        <div class="financial-card">
            <h4>Open Mortgage Instrument</h4>
            <div class="financial-rows">
                <div class="fin-row"><span>Recorded amount:</span><span>$${formatNumber(building.mortgage_amount)}</span></div>
                ${building.mortgage_date ? `<div class="fin-row"><span>Date:</span><span>${formatDate(building.mortgage_date)}</span></div>` : ''}
                ${building.mortgage_lender_primary ? `<div class="fin-row"><span>Lender:</span><span>${building.mortgage_lender_primary}</span></div>` : ''}
                <div class="fin-row"><span>Status:</span><span>Apparently open in ACRIS</span></div>
            </div>
        </div>`;
    } else if (building.is_free_and_clear) {
        html += `
        <div class="financial-card">
            <h4>Mortgage Status</h4>
            <div class="financial-rows">
                <div class="fin-row"><span>Status:</span><span>No open mortgage found</span></div>
                ${building.last_satisfaction_date ? `<div class="fin-row"><span>Last satisfaction:</span><span>${formatDate(building.last_satisfaction_date)}</span></div>` : ''}
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
            <h4>Lien notices & recorded balances</h4>
            <div class="financial-rows">`;
        
        if (building.has_tax_delinquency) {
            html += `
                <div class="fin-row alert">
                    <span>Lien-sale notice:</span>
                    <span>${building.tax_delinquency_count} notice(s) ${building.tax_delinquency_water_only ? '(Water Only)' : '(Property Tax)'} — current debt unverified</span>
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
    const { owners, owner_classifications = {}, parties, sos_data } = buildingData;
    const container = document.getElementById('owners-content');
    
    let html = '<div class="owners-list">';
    
    // SOS Data - Real Person Behind LLC (PREMIUM SECTION)
    if (sos_data && sos_data.principal_name) {
        const isAgent = isSosAgentTitle(sos_data.principal_title);
        const isEntityMismatch = sos_data.entity_match === 'mismatch';
        const isRealPerson = !isAgent && !isEntityMismatch &&
            (sos_data.is_person === true ||
             (sos_data.is_person === undefined && looksLikeHumanName(sos_data.principal_name)));
        const sosHeading = isAgent ? 'SOS — Service Agent (not the owner)'
            : isEntityMismatch ? 'SOS Contact — Entity Mismatch'
            : isRealPerson ? 'Real Person Behind Owner Entity'
            : 'SOS Principal Record';

        html += `
        <div class="sos-section ${isRealPerson ? 'real-person-found' : ''}${(isAgent || isEntityMismatch) ? ' sos-agent' : ''}">
            <h4>${sosHeading}</h4>
            <div class="sos-card">
                <div class="sos-main">
                    <div class="sos-principal-name">${ownerSourceName('sos', sos_data.principal_name)}</div>
                    ${renderOwnerSourceDate('sos')}
                    ${isRealPerson ? renderTruePeopleSearchLink(sos_data.principal_name, sos_data.principal_address || {}) : ''}
                    ${sos_data.principal_title ? `<div class="sos-principal-title">${sos_data.principal_title}</div>` : ''}
                    ${isRealPerson ? '<span class="real-person-badge-large">REAL PERSON IDENTIFIED</span>' : ''}
                    ${isAgent ? '<span class="agent-badge-large" title="Designated for service of process — not the property owner">AGENT — not the owner</span>' : ''}
                    ${isEntityMismatch ? '<span class="agent-badge-large" title="The SOS company did not match any recorded owner entity">ENTITY MISMATCH — excluded from enrichment</span>' : ''}
                </div>
                <div class="sos-details">
                    <div class="sos-detail-row">
                        <span class="sos-label">Entity Name:</span>
                        <span class="sos-value">${sos_data.entity_name || 'N/A'}</span>
                    </div>
                    <div class="sos-detail-row">
                        <span class="sos-label">Entity Status:</span>
                        <span class="sos-value sos-status-${(sos_data.entity_status || '').toLowerCase()}">${sos_data.entity_status || 'N/A'}</span>
                    </div>
                    ${sos_data.dos_id ? `
                    <div class="sos-detail-row">
                        <span class="sos-label">DOS Filing ID:</span>
                        <span class="sos-value">${sos_data.dos_id}</span>
                    </div>` : ''}
                    ${sos_data.formation_date ? `
                    <div class="sos-detail-row">
                        <span class="sos-label">Formation Date:</span>
                        <span class="sos-value">${formatDate(sos_data.formation_date)}</span>
                    </div>` : ''}
                    ${sos_data.principal_address && sos_data.principal_address.street ? `
                    <div class="sos-detail-row">
                        <span class="sos-label">Principal Address:</span>
                        <span class="sos-value">${sos_data.principal_address.street}, ${sos_data.principal_address.city}, ${sos_data.principal_address.state} ${sos_data.principal_address.zip}</span>
                    </div>` : ''}
                </div>
            </div>
        </div>`;
    }
    
    // Current Owners (All Sources)
    html += '<h4>Latest record by source</h4>';
    html += '<div class="current-owners">';
    
    const sourceInfo = {
        'acris': { label: 'ACRIS Latest Deed Grantee', icon: '' },
        'pluto': { label: 'NYC PLUTO Database', icon: '' },
        'rpad': { label: 'Historical RPAD Assessment', icon: '' },
        'hpd': { label: 'HPD Registered Owner', icon: '' },
        'ecb': { label: 'ECB Violation Respondent', icon: '' }
    };
    
    Object.entries(owners).forEach(([source, name]) => {
        if (name) {
            const info = sourceInfo[source] || { label: source, icon: '' };
            const classification = owner_classifications[source] || {};
            const kind = classification.entity_kind ||
                (looksLikeHumanName(name) ? 'person' : 'unknown');
            html += `
            <div class="owner-source-card">
                <div class="owner-source-icon">${info.icon}</div>
                <div class="owner-source-info">
                    <div class="owner-source-label">${info.label}</div>
                    <div class="owner-source-name">${ownerSourceName(source, name)}</div>
                    ${renderOwnerSourceDate(source)}
                    <span class="entity-kind-badge entity-${kind}">${kind === 'person' ? 'Person' : kind === 'organization' ? 'Organization' : kind === 'multiple' ? 'Multiple parties' : 'Unclassified'}</span>
                    ${renderTruePeopleSearchLink(name, classification)}
                </div>
            </div>`;
        }
    });
    
    html += '</div>';
    
    const deedOwners = getPriorDeedOwners(parties || []);
    if (deedOwners.length > 0) {
        const peopleCount = deedOwners.filter(owner => owner.is_person).length;
        html += `
            <div class="ownership-history-header">
                <div>
                    <h4>Prior Deed Owners</h4>
                    <p>Grantors on recorded deeds only. Mortgage lenders and loan assignees are excluded.</p>
                </div>
                <div class="owner-history-filter" role="group" aria-label="Filter prior deed owners">
                    <button type="button" data-owner-filter="all" aria-pressed="true" onclick="setOwnerHistoryFilter('all')">
                        All owners <span>${deedOwners.length}</span>
                    </button>
                    <button type="button" data-owner-filter="people" aria-pressed="false" onclick="setOwnerHistoryFilter('people')">
                        People only <span>${peopleCount}</span>
                    </button>
                </div>
            </div>
            <div class="historical-owners" id="historical-owners-list"></div>`;
    }
    
    html += '</div>';
    container.innerHTML = html;
    setOwnerHistoryFilter(ownerHistoryFilter);
}

function getPriorDeedOwners(parties) {
    const seen = new Set();
    return parties
        .filter(party => party.party_type === 'seller' &&
            (party.is_ownership_party === true ||
             (party.is_ownership_party === undefined && isDeedDocument(party.doc_type))))
        .filter(party => {
            const key = `${String(party.party_name || '').trim().toUpperCase()}|${party.document_id || party.recorded_date || ''}`;
            if (!party.party_name || seen.has(key)) return false;
            seen.add(key);
            return true;
        })
        .map(party => ({
            ...party,
            entity_kind: party.entity_kind ||
                (looksLikeHumanName(party.party_name) ? 'person' : 'unknown'),
            is_person: party.is_person === true ||
                (party.is_person === undefined && looksLikeHumanName(party.party_name)),
        }));
}

function setOwnerHistoryFilter(filter) {
    ownerHistoryFilter = filter === 'people' ? 'people' : 'all';
    document.querySelectorAll('[data-owner-filter]').forEach(button => {
        const selected = button.dataset.ownerFilter === ownerHistoryFilter;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-pressed', String(selected));
    });
    renderPriorDeedOwners();
}

function renderPriorDeedOwners() {
    const container = document.getElementById('historical-owners-list');
    if (!container || !buildingData) return;
    const allOwners = getPriorDeedOwners(buildingData.parties || []);
    const owners = ownerHistoryFilter === 'people'
        ? allOwners.filter(owner => owner.is_person)
        : allOwners;

    if (!owners.length) {
        container.innerHTML = '<div class="no-data owner-filter-empty">No people were confidently identified in the deed-owner history.</div>';
        return;
    }

    container.innerHTML = owners.slice(0, 50).map(owner => {
        const address = [owner.address_1, owner.address_2, owner.city,
                         owner.state, owner.zip_code].filter(Boolean).join(', ');
        const kindLabel = owner.is_person ? 'Person'
            : owner.entity_kind === 'organization' ? 'Organization'
            : owner.entity_kind === 'multiple' ? 'Multiple parties' : 'Unclassified';
        return `
            <div class="historical-owner-card">
                <div class="ho-main">
                    <div class="ho-name">${owner.party_name}</div>
                    <span class="entity-kind-badge entity-${owner.entity_kind}">${kindLabel}</span>
                </div>
                ${owner.recorded_date ? `<div class="ho-date">Deed recorded: ${formatDate(owner.recorded_date)}</div>` : ''}
                ${address ? `<div class="ho-address">${address}</div>` : ''}
                ${renderTruePeopleSearchLink(owner.party_name, owner)}
            </div>`;
    }).join('');
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
        const borrowers = txnParties.filter(p => p.party_type === 'borrower');
        const assignors = txnParties.filter(p => p.party_type === 'assignor');
        const assignees = txnParties.filter(p => p.party_type === 'assignee');
        
        html += `
        <div class="transaction-card" data-doc-type="${txn.doc_type}" data-amount="${txn.doc_amount || 0}">
            <div class="txn-header">
                <span class="txn-type">${getDocTypeLabel(txn.doc_type)}</span>
                <span class="txn-date">${formatDate(txn.recorded_date)}</span>
            </div>
            ${txn.doc_amount ? `<div class="txn-amount">${formatCurrency(txn.doc_amount)}</div>` : ''}
            <div class="txn-details">
                <div class="txn-detail-row"><span>Document ID:</span><span>${transactionSourceName(txn)}</span></div>
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
        if (borrowers.length > 0) {
            html += '<div class="txn-parties"><strong>Borrowers:</strong> ' + borrowers.map(p => p.party_name).join(', ') + '</div>';
        }
        if (assignors.length > 0) {
            html += '<div class="txn-parties"><strong>Assignors:</strong> ' + assignors.map(p => p.party_name).join(', ') + '</div>';
        }
        if (assignees.length > 0) {
            html += '<div class="txn-parties"><strong>Assignees:</strong> ' + assignees.map(p => p.party_name).join(', ') + '</div>';
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
        const borrowers = txnParties.filter(p => p.party_type === 'borrower');
        const assignors = txnParties.filter(p => p.party_type === 'assignor');
        const assignees = txnParties.filter(p => p.party_type === 'assignee');
        
        html += `
        <div class="transaction-card" data-doc-type="${txn.doc_type}" data-amount="${txn.doc_amount || 0}">
            <div class="txn-header">
                <span class="txn-type">${getDocTypeLabel(txn.doc_type)}</span>
                <span class="txn-date">${formatDate(txn.recorded_date)}</span>
            </div>
            ${txn.doc_amount ? `<div class="txn-amount">${formatCurrency(txn.doc_amount)}</div>` : ''}
            <div class="txn-details">
                <div class="txn-detail-row"><span>Document ID:</span><span>${transactionSourceName(txn)}</span></div>
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
        if (borrowers.length > 0) {
            html += '<div class="txn-parties"><strong>Borrowers:</strong> ' + borrowers.map(p => p.party_name).join(', ') + '</div>';
        }
        if (assignors.length > 0) {
            html += '<div class="txn-parties"><strong>Assignors:</strong> ' + assignors.map(p => p.party_name).join(', ') + '</div>';
        }
        if (assignees.length > 0) {
            html += '<div class="txn-parties"><strong>Assignees:</strong> ' + assignees.map(p => p.party_name).join(', ') + '</div>';
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

    // A profile can contain both issued permits and DOB NOW job filings. Keep
    // them together for history, but label them accurately in every card.
    const permitTypes = new Map();
    permits.forEach(permit => {
        permitTypes.set(getPermitTypeKey(permit), getPermitTypeLabel(permit));
    });
    const sortedPermitTypes = [...permitTypes.entries()]
        .sort((a, b) => a[1].localeCompare(b[1]));
    
    let html = `
    <div class="permits-summary">
        <div><strong>${permits.length.toLocaleString('en-US')}</strong> DOB records</div>
        <p>Includes issued permits and filings that may still be under review.</p>
    </div>
    <div class="permits-controls" aria-label="Permit list controls">
        <label class="permit-control" for="filter-job-type">
            <span>Record type</span>
            <select id="filter-job-type" onchange="filterPermits()">
                <option value="all">All</option>
                ${sortedPermitTypes.map(([value, label]) =>
                    `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`).join('')}
            </select>
        </label>
        <label class="permit-control" for="sort-permits">
            <span>Sort</span>
            <select id="sort-permits" onchange="filterPermits()">
                <option value="date-desc">Newest first</option>
                <option value="date-asc">Oldest first</option>
                <option value="job-type">Record type</option>
            </select>
        </label>
        <span class="permit-results-count" id="permit-results-count" aria-live="polite"></span>
    </div>
    <div class="permits-list" id="permits-list-container"></div>`;
    container.innerHTML = html;
    filterPermits();
}

function filterPermits() {
    if (!window.permitsData) return;
    
    const jobTypeFilter = document.getElementById('filter-job-type').value;
    const sortOption = document.getElementById('sort-permits').value;
    
    // Filter permits
    let filtered = window.permitsData.filter(permit => {
        if (jobTypeFilter !== 'all' && getPermitTypeKey(permit) !== jobTypeFilter) return false;
        return true;
    });
    
    // Sort permits
    filtered.sort((a, b) => {
        switch(sortOption) {
            case 'date-desc':
                return comparePermitDates(a, b, 'desc');
            case 'date-asc':
                return comparePermitDates(a, b, 'asc');
            case 'job-type':
                return getPermitTypeLabel(a).localeCompare(getPermitTypeLabel(b)) ||
                    comparePermitDates(a, b, 'desc');
            default:
                return 0;
        }
    });
    
    // Render filtered permits
    const container = document.getElementById('permits-list-container');
    const resultCount = document.getElementById('permit-results-count');
    if (resultCount) {
        resultCount.textContent = filtered.length === window.permitsData.length
            ? `${filtered.length.toLocaleString('en-US')} shown`
            : `${filtered.length.toLocaleString('en-US')} of ${window.permitsData.length.toLocaleString('en-US')} shown`;
    }
    if (filtered.length === 0) {
        container.innerHTML = '<div class="no-data">No permits match the selected filters</div>';
        return;
    }
    
    container.innerHTML = filtered.map(permit => {
        // Find original index for showPermitDetails
        const originalIndex = window.permitsData.indexOf(permit);
        return renderPermitCard(permit, originalIndex);
    }).join('');

    container.querySelectorAll('.permit-card').forEach(card => {
        card.addEventListener('click', () => {
            showPermitDetails(Number(card.dataset.index));
        });
    });
}

function getPermitTypeKey(permit) {
    return String(permit.job_type || permit.work_type || '__other__');
}

function getPermitTypeLabel(permit) {
    return String(
        permit.job_type_label || permit.job_type ||
        permit.work_type_label || permit.work_type ||
        'Other DOB record'
    );
}

function getPermitDateInfo(permit) {
    const isIssued = Boolean(permit.issue_date);
    const value = permit.effective_date || permit.issue_date || permit.filing_date || null;
    const parsed = value ? Date.parse(value) : Number.NaN;
    return {
        label: isIssued ? 'Issued' : (permit.filing_date ? 'Filed' : 'Date'),
        value,
        timestamp: Number.isFinite(parsed) ? parsed : null,
    };
}

function comparePermitDates(a, b, direction) {
    const aTime = getPermitDateInfo(a).timestamp;
    const bTime = getPermitDateInfo(b).timestamp;
    if (aTime === null && bTime !== null) return 1;
    if (aTime !== null && bTime === null) return -1;
    if (aTime !== bTime) {
        return direction === 'asc' ? aTime - bTime : bTime - aTime;
    }
    return String(b.permit_no || '').localeCompare(String(a.permit_no || ''));
}

function getPermitRecordKind(permit) {
    const kind = permit.record_kind || (permit.issue_date ? 'issued_permit' :
        (permit.filing_date ? 'job_filing' : 'dob_record'));
    if (kind === 'issued_permit') return 'Issued permit';
    if (kind === 'job_filing') return 'Job filing';
    return 'DOB record';
}

function getPermitStatus(permit) {
    return String(
        (permit.issue_date ? permit.permit_status : permit.filing_status) ||
        permit.permit_status || permit.filing_status || ''
    ).trim();
}

function getPermitStatusClass(status) {
    const value = String(status || '').toUpperCase();
    if (/ISSUED|APPROVED|COMPLETE|ACTIVE/.test(value)) return 'positive';
    if (/OBJECTION|DISAPPROV|DENIED|REJECT|REVOK/.test(value)) return 'critical';
    if (/PENDING|REVIEW|PROCESS|ASSIGN/.test(value)) return 'attention';
    return 'neutral';
}

function getPermitDescription(permit) {
    const description = String(permit.work_description || '').trim();
    if (!description || /^Type:/i.test(description)) return '';
    return description;
}

function renderPermitCard(permit, originalIndex) {
    const date = getPermitDateInfo(permit);
    const kind = getPermitRecordKind(permit);
    const status = getPermitStatus(permit);
    const workLabel = String(permit.work_type_label || permit.work_type || '').trim();
    const jobLabel = String(permit.job_type_label || permit.job_type || '').trim();
    const title = workLabel || jobLabel || 'Construction record';
    const context = workLabel && jobLabel && workLabel !== jobLabel ? jobLabel : '';
    const description = getPermitDescription(permit);
    const applicant = String(permit.applicant || '').trim();
    const permittee = String(permit.permittee_business_name || '').trim();
    const sameContact = applicant && permittee && applicant.toUpperCase() === permittee.toUpperCase();
    const contacts = [];
    if (sameContact) {
        contacts.push(['Applicant & permittee', applicant]);
    } else {
        if (applicant) contacts.push(['Applicant', applicant]);
        if (permittee) contacts.push(['Permittee', permittee]);
    }
    const permitNumber = String(permit.permit_no || 'Number unavailable');
    const dateText = date.value ? formatDate(date.value) : 'Not available';
    const isoDate = date.value && Number.isFinite(Date.parse(date.value))
        ? new Date(date.value).toISOString().slice(0, 10)
        : '';
    const ariaLabel = `Open ${kind.toLowerCase()} ${permitNumber}, ${title}`;

    return `
        <button type="button" class="permit-card" data-index="${originalIndex}"
                aria-label="${escapeHtml(ariaLabel)}">
            <span class="permit-card-main">
                <span class="permit-card-eyebrow">
                    <span class="permit-record-kind">${escapeHtml(kind)}</span>
                    ${status ? `<span class="permit-status permit-status-${getPermitStatusClass(status)}">${escapeHtml(status)}</span>` : ''}
                </span>
                <span class="permit-card-title">${escapeHtml(title)}</span>
                ${context ? `<span class="permit-card-context">${escapeHtml(context)}</span>` : ''}
                <span class="permit-no">#${escapeHtml(permitNumber)}</span>
                ${description ? `<span class="permit-card-description">${escapeHtml(description)}</span>` : ''}
                ${contacts.length ? `<span class="permit-people">${contacts.map(([label, value]) => `
                    <span class="permit-person">
                        <span class="permit-person-label">${escapeHtml(label)}</span>
                        <span class="permit-person-value">${escapeHtml(value)}</span>
                    </span>`).join('')}</span>` : ''}
            </span>
            <span class="permit-card-aside">
                <span class="permit-date-label">${escapeHtml(date.label)}</span>
                <time class="permit-date-value"${isoDate ? ` datetime="${isoDate}"` : ''}>${escapeHtml(dateText)}</time>
                <span class="permit-card-action">View details <span aria-hidden="true">→</span></span>
            </span>
        </button>`;
}

function showPermitDetails(index) {
    const permit = buildingData.permits[index];
    if (!permit) return;
    
    // Helper function to add row only if value exists
    const addRow = (label, value) => {
        if (value && value !== 'N/A' && value !== null && value !== undefined) {
            return `<div class="detail-row"><span class="detail-label">${escapeHtml(label)}:</span><span class="detail-value">${escapeHtml(value)}</span></div>`;
        }
        return '';
    };
    const permitSource = permit.source_link;
    const safePermitLink = safeHttpHref(permitSource?.url);
    
    let html = `
    <div class="permit-detail-modal-content">
        <h2>DOB record #${escapeHtml(permit.permit_no || 'Unknown')}</h2>
        <div class="permit-detail-grid">`;
    
    // Basic Information - always show
    html += `
            <div class="detail-section">
                <h3>Basic Information</h3>
                ${addRow('Permit Number', permit.permit_no)}
                ${addRow('Job Type', permit.job_type_label || permit.job_type)}
                ${addRow('Work Type', permit.work_type_label || permit.work_type)}
                ${permit.issue_date ? addRow('Issue Date', formatDate(permit.issue_date)) : ''}
                ${permit.exp_date ? addRow('Expiration Date', formatDate(permit.exp_date)) : ''}
                ${permit.filing_date ? addRow('Filing Date', formatDate(permit.filing_date)) : ''}
                ${addRow('Permit Status', permit.permit_status)}
                ${addRow('Filing Status', permit.filing_status)}
                ${addRow('Self-Certified', permit.self_cert)}
                ${addRow('Fee Type', permit.fee_type)}
            </div>`;
    
    // Work Details - only if has work description or related data
    const hasWorkDetails = permit.work_description || permit.proposed_job_start;
    if (hasWorkDetails) {
        html += `
            <div class="detail-section">
                <h3>Work Details</h3>
                ${addRow('Work Description', permit.work_description)}
                ${permit.proposed_job_start ? addRow('Proposed Start Date', formatDate(permit.proposed_job_start)) : ''}
            </div>`;
    }
    
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
        const applicantEnrichBtn = buildEnrichButton(permit, permit.applicant, 'applicant');
        html += `
            <div class="detail-section">
                <h3>Applicant</h3>
                ${addRow('Name', permit.applicant)}
                <div id="applicant-enriched-data-${permit.id}"></div>
                ${applicantEnrichBtn}
            </div>`;
    }
    
    // Permittee - only if has data
    const hasPermitteeData = permit.permittee_business_name || permit.permittee_license_type || 
                             permit.permittee_license_number || permit.permittee_phone;
    if (hasPermitteeData) {
        // Make license number clickable
        let licenseDisplay = permit.permittee_license_number;
        if (permit.permittee_license_number) {
            const licenseType = permit.permittee_license_type || '';
            licenseDisplay = `<a href="#" class="license-link"
                data-license-number="${escapeHtml(permit.permittee_license_number)}"
                data-license-type="${escapeHtml(licenseType)}">${escapeHtml(permit.permittee_license_number)}</a>`;
        }
        const permitteeEnrichBtn = buildEnrichButton(permit, permit.permittee_business_name, 'permittee', 
            permit.permittee_license_number, permit.permittee_license_type, permit.permittee_phone);
        html += `
            <div class="detail-section">
                <h3>Permittee</h3>
                ${addRow('Business Name', permit.permittee_business_name)}
                ${addRow('License Type', permit.permittee_license_type)}
                ${permit.permittee_license_number ? `<div class="detail-row"><span>License #</span><span>${licenseDisplay}</span></div>` : ''}
                ${addRow('Phone', permit.permittee_phone ? formatPhoneNumber(permit.permittee_phone) : null)}
                <div id="permittee-enriched-data-${permit.id}"></div>
                ${permitteeEnrichBtn}
            </div>`;
    }
    
    // Owner - only if has data
    if (permit.owner_business_name || permit.owner_phone) {
        const ownerEnrichBtn = buildEnrichButton(permit, permit.owner_business_name, 'owner', null, null, permit.owner_phone);
        html += `
            <div class="detail-section">
                <h3>Owner</h3>
                ${addRow('Business Name', permit.owner_business_name)}
                ${addRow('Phone', permit.owner_phone ? formatPhoneNumber(permit.owner_phone) : null)}
                <div id="owner-enriched-data-${permit.id}"></div>
                ${ownerEnrichBtn}
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
            ${safePermitLink ? `<div class="permit-source-action"><a href="${escapeHtml(safePermitLink)}" target="_blank" rel="noopener noreferrer" class="btn-view-dob">${escapeHtml(permitSource.label)} ↗</a>${renderSourceHelp(permitSource)}</div>` : ''}
            <button onclick="closePermitModal()" class="btn-close-modal">Close</button>
        </div>
    </div>`;
    
    const modal = document.getElementById('permit-modal');
    modal.innerHTML = html;
    modal.style.display = 'flex';
    modal.querySelectorAll('.license-link').forEach(link => {
        link.addEventListener('click', event => {
            event.preventDefault();
            showLicenseInfo(link.dataset.licenseNumber, link.dataset.licenseType || '');
        });
    });
    bindPermitEnrichButtons(modal);
}

/**
 * Build an enrich button for a contact in the permit modal
 */
function buildEnrichButton(permit, contactName, contactType, licenseNumber = null, licenseType = null, existingPhone = null) {
    if (!contactName) return '';
    if (!looksLikeHumanName(contactName)) return '';
    
    const bbl = buildingData?.building?.bbl || BBL;
    const buildingId = buildingData?.building?.id;
    const permitId = permit.id;
    const peopleSearchLink = renderTruePeopleSearchLink(contactName);
    if (!permitId) return peopleSearchLink;
    
    // Create unique button ID
    const buttonId = `enrich-btn-${contactType}-${permitId}`;
    
    return `
        <div class="enrich-contact-section" id="enrich-section-${contactType}-${permitId}">
            <button type="button" class="enrich-contact-btn" id="${escapeHtml(buttonId)}"
                ${researchPersonBlocked({name: contactName}) ? 'disabled title="Do not contact is set for this name on this property."' : ''}
                data-enrich-permit-contact
                data-bbl="${escapeHtml(bbl)}"
                data-building-id="${escapeHtml(buildingId || '')}"
                data-permit-id="${escapeHtml(permitId)}"
                data-contact-name="${escapeHtml(contactName)}"
                data-contact-type="${escapeHtml(contactType)}"
                data-license-number="${escapeHtml(licenseNumber || '')}"
                data-license-type="${escapeHtml(licenseType || '')}"
                data-existing-phone="${escapeHtml(existingPhone || '')}">
                Get Contact Info
                <span class="enrich-cost">$0.50</span>
            </button>
            ${peopleSearchLink}
        </div>
    `;
}

function bindPermitEnrichButtons(container) {
    container.querySelectorAll('[data-enrich-permit-contact]').forEach(button => {
        button.addEventListener('click', () => {
            enrichPermitContact(
                button.dataset.bbl,
                Number(button.dataset.buildingId) || null,
                Number(button.dataset.permitId),
                button.dataset.contactName,
                button.dataset.contactType,
                button.dataset.licenseNumber,
                button.dataset.licenseType,
                button.dataset.existingPhone,
                button
            );
        });
    });
}

/**
 * Enrich a permit contact (called from the enrich button)
 */
async function enrichPermitContact(bbl, buildingId, permitId, contactName, contactType, licenseNumber, licenseType, existingPhone, button) {
    if (researchPersonBlocked({name: contactName})) {
        button.disabled = true;
        button.textContent = 'Do not contact';
        return;
    }
    // Disable button and show loading
    button.disabled = true;
    button.innerHTML = '<span class="loading-spinner"></span> Enriching...';
    
    try {
        const response = await fetch('/api/enrichment/permit-contact', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                bbl: bbl,
                building_id: buildingId,
                permit_id: permitId,
                contact_name: contactName,
                contact_type: contactType,
                license_number: licenseNumber || null,
                license_type: licenseType || null,
                original_phone: existingPhone || null
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            // Show enriched data
            const dataContainer = document.getElementById(`${contactType}-enriched-data-${permitId}`);
            if (dataContainer) {
                dataContainer.innerHTML = renderEnrichedContactData(data.data, contactName);
            }
            
            // Update button to show success
            button.outerHTML = `
                <div class="enrich-success">
                    Contact info unlocked${data.charged ? ' - $0.50 charged' : ''}
                </div>
            `;
            
            // Refresh contacts tab to show new enriched contact
            if (typeof renderContactsTab === 'function') {
                await refreshEnrichedContacts();
            }
        } else {
            // Show error
            button.disabled = false;
            button.innerHTML = `Get Contact Info <span class="enrich-cost">$0.50</span>`;
            
            // Show error message
            const section = button.closest('.enrich-contact-section');
            if (section) {
                section.insertAdjacentHTML('beforeend', `
                    <div class="enrich-error">${escapeHtml(data.error || 'Enrichment failed')}</div>
                `);
            }
        }
    } catch (error) {
        console.error('Enrichment error:', error);
        button.disabled = false;
        button.innerHTML = `Get Contact Info <span class="enrich-cost">$0.50</span>`;
    }
}

/**
 * Render enriched contact data in the permit modal
 */
function renderEnrichedContactData(data, contactName) {
    if (!data) return '';
    
    let html = '<div class="enriched-contact-data">';
    
    // Phones
    if (data.phones && data.phones.length > 0) {
        html += '<div class="enriched-phones">';
        data.phones.forEach(phone => {
            html += `
                <div class="enriched-phone-item">
                    <span class="phone-icon"></span>
                    <span class="phone-number">${formatPhoneNumber(phone.number)}</span>
                    ${phone.type ? `<span class="phone-type">${phone.type}</span>` : ''}
                    ${phone.is_valid === false ? `<span class="phone-invalid"></span>` : ''}
                </div>
            `;
        });
        html += '</div>';
    }
    
    // Emails
    if (data.emails && data.emails.length > 0) {
        html += '<div class="enriched-emails">';
        data.emails.forEach(email => {
            html += `
                <div class="enriched-email-item">
                    <span class="email-icon"></span>
                    <a href="mailto:${email.email}" class="email-address">${email.email}</a>
                </div>
            `;
        });
        html += '</div>';
    }
    
    html += '</div>';
    return html;
}

/**
 * Refresh enriched contacts for the Contacts tab
 */
async function refreshEnrichedContacts() {
    try {
        const bbl = buildingData?.building?.bbl || BBL;
        const response = await fetch(`/api/building/${bbl}/enriched-contacts`);
        const data = await response.json();
        
        if (data.success) {
            // Update buildingData with new enriched contacts
            buildingData.enriched_contacts = {
                permit_contacts: data.permit_contacts,
                owner_contacts: data.owner_contacts
            };
            
            // Re-render contacts tab
            renderContactsTab();
        }
    } catch (error) {
        console.error('Error refreshing enriched contacts:', error);
    }
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
// LICENSE LOOKUP
// ============================================================================

async function showLicenseInfo(licenseNumber, licenseType) {
    // Show loading state in modal
    const modal = document.getElementById('permit-modal');
    modal.innerHTML = `
        <div class="permit-modal-content license-modal">
            <button class="modal-close" onclick="closePermitModal()">×</button>
            <h2>License #${licenseNumber}</h2>
            <div class="license-loading">
                <div class="spinner"></div>
                Loading license information...
            </div>
        </div>`;
    modal.style.display = 'flex';
    
    try {
        const response = await fetch(`/api/license/${licenseNumber}/permits`);
        const data = await response.json();
        
        if (!data.success) {
            throw new Error(data.error || 'Failed to load license data');
        }
        
        // Build work type breakdown HTML with proper bar chart
        let workTypeHtml = '';
        if (data.work_types && data.work_types.length > 0) {
            workTypeHtml = '<div class="work-types-breakdown"><h4>Work Types</h4><div class="work-type-bars">';
            const maxCount = data.work_types[0].count;
            data.work_types.slice(0, 5).forEach(wt => {
                const pct = Math.round((wt.count / maxCount) * 100);
                const label = wt.work_type.length > 20 ? wt.work_type.substring(0, 20) + '...' : wt.work_type;
                workTypeHtml += `
                    <div class="work-type-row">
                        <span class="work-type-label" title="${wt.work_type}">${label}</span>
                        <div class="work-type-bar-container">
                            <div class="work-type-bar" style="width: ${pct}%"></div>
                        </div>
                        <span class="work-type-count">${wt.count}</span>
                    </div>`;
            });
            workTypeHtml += '</div></div>';
        }
        
        // Build permits list (show top 10)
        let permitsHtml = '';
        const displayPermits = data.permits.slice(0, 10);
        if (displayPermits.length > 0) {
            permitsHtml = '<div class="license-permits-list"><h4>Recent Permits</h4>';
            displayPermits.forEach(p => {
                const dateStr = p.issue_date ? formatDate(p.issue_date) :
                               (p.filing_date ? 'Filed ' + formatDate(p.filing_date) : 'No date');
                permitsHtml += `
                    <div class="license-permit-item">
                        <div class="permit-item-header">
                            <a href="/property/${p.bbl}" class="permit-address">${p.address || 'Unknown Address'}</a>
                            <span class="permit-date-small">${dateStr}</span>
                        </div>
                        <div class="permit-item-details">
                            <span class="permit-type-badge">${p.job_type || 'N/A'}</span>
                            <span class="permit-work-type-small">${p.work_type || ''}</span>
                            <span class="permit-no-small">#${p.permit_no}</span>
                        </div>
                    </div>`;
            });
            if (data.total_permits > 10) {
                permitsHtml += `<div class="more-permits">... and ${data.total_permits - 10} more permits</div>`;
            }
            permitsHtml += '</div>';
        }
        
        // Build NYC Open Data enrichment section if available
        let nycLicenseHtml = '';
        if (data.nyc_license_info) {
            const lic = data.nyc_license_info;
            const statusClass = lic.license_status === 'ACTIVE' ? 'status-active' : 'status-expired';
            nycLicenseHtml = `
                <div class="nyc-license-info">
                    <h4>NYC DOB License Record</h4>
                    <div class="license-details-grid">
                        ${lic.first_name || lic.last_name ? `<div class="lic-row"><span>Name:</span><span>${lic.first_name || ''} ${lic.last_name || ''}</span></div>` : ''}
                        ${lic.business_name ? `<div class="lic-row"><span>Business:</span><span>${lic.business_name}</span></div>` : ''}
                        ${lic.license_type ? `<div class="lic-row"><span>Type:</span><span>${lic.license_type}</span></div>` : ''}
                        ${lic.license_status ? `<div class="lic-row"><span>Status:</span><span class="${statusClass}">${lic.license_status}</span></div>` : ''}
                        ${lic.business_phone_number ? `<div class="lic-row"><span>Phone:</span><span><a href="tel:${lic.business_phone_number}">${formatPhoneNumber(lic.business_phone_number)}</a></span></div>` : ''}
                        ${lic.business_email ? `<div class="lic-row"><span>Email:</span><span><a href="mailto:${lic.business_email}">${lic.business_email.toLowerCase()}</a></span></div>` : ''}
                        ${lic.business_house_number || lic.business_street_name ? `<div class="lic-row"><span>Address:</span><span>${lic.business_house_number || ''} ${lic.business_street_name || ''}, ${lic.license_business_city || ''} ${lic.business_state || ''} ${lic.business_zip_code || ''}</span></div>` : ''}
                    </div>
                </div>`;
        }
        
        // Build NY State license info section if available
        let nysLicenseHtml = '';
        if (data.nys_license_info) {
            const nys = data.nys_license_info;
            const statusClass = nys.status === 'Registered' ? 'status-active' : 'status-expired';
            nysLicenseHtml = `
                <div class="nys-license-info">
                    <h4>NY State License Record</h4>
                    <div class="license-details-grid">
                        ${nys.name ? `<div class="lic-row"><span>Name:</span><span>${nys.name}</span></div>` : ''}
                        ${nys.profession ? `<div class="lic-row"><span>Profession:</span><span>${nys.profession}</span></div>` : ''}
                        ${nys.status ? `<div class="lic-row"><span>Status:</span><span class="${statusClass}">${nys.status}</span></div>` : ''}
                        ${nys.registered_through ? `<div class="lic-row"><span>Registered Through:</span><span>${nys.registered_through}</span></div>` : ''}
                        ${nys.date_of_licensure ? `<div class="lic-row"><span>Licensed Since:</span><span>${nys.date_of_licensure}</span></div>` : ''}
                        ${nys.address ? `<div class="lic-row"><span>Location:</span><span>${nys.address}</span></div>` : ''}
                        ${nys.enforcement_actions ? `<div class="lic-row warning"><span>Enforcement Actions:</span><span>Yes</span></div>` : ''}
                    </div>
                </div>`;
        }
        
        modal.innerHTML = `
            <div class="permit-modal-content license-modal">
                <button class="modal-close" onclick="closePermitModal()">×</button>
                
                <div class="license-header">
                    <h2>License #${licenseNumber}</h2>
                    ${data.license_type_full ? `<span class="license-type-badge">${data.license_type_full}</span>` : ''}
                </div>
                
                ${data.applicant_name ? `<div class="licensee-name">${data.applicant_name}</div>` : ''}
                ${data.contractor_name ? `<div class="contractor-name">Company: ${data.contractor_name}</div>` : ''}
                
                ${data.specialty ? `<div class="specialty-badge">Specialty: ${data.specialty}</div>` : ''}
                
                ${nycLicenseHtml}
                ${nysLicenseHtml}
                
                <div class="license-stats">
                    <div class="license-stat">
                        <span class="stat-value">${data.total_permits}</span>
                        <span class="stat-label">Total Permits</span>
                    </div>
                    <div class="license-stat">
                        <span class="stat-value">${data.unique_buildings}</span>
                        <span class="stat-label">Buildings</span>
                    </div>
                </div>
                
                ${workTypeHtml}
                ${permitsHtml}
                
                <div class="permit-modal-actions">
                    <button class="btn-close-modal" onclick="closePermitModal()">Close</button>
                </div>
            </div>`;
            
    } catch (error) {
        console.error('License lookup error:', error);
        modal.innerHTML = `
            <div class="permit-modal-content license-modal">
                <button class="modal-close" onclick="closePermitModal()">×</button>
                <h2>License #${licenseNumber}</h2>
                <div class="error-message">Failed to load license information</div>
                <div class="permit-modal-actions">
                    <button class="btn-close-modal" onclick="closePermitModal()">Close</button>
                </div>
            </div>`;
    }
}

// ============================================================================
// VIOLATIONS TAB
// ============================================================================

function renderViolationsTab() {
    const { building } = buildingData;
    const container = document.getElementById('violations-content');
    
    // Calculate total amounts owed
    const ecbBalance = building.ecb_total_balance || 0;
    const hpdBalance = 0; // HPD doesn't have financial penalties in our data
    const totalOwed = ecbBalance + hpdBalance;
    
    let html = '';
    
    // Total violations owed banner (only show if there's money owed)
    if (totalOwed > 0) {
        html += `
        <div class="total-violations-owed">
            <div class="total-owed-icon"></div>
            <div class="total-owed-content">
                <div class="total-owed-label">Total Outstanding Violations</div>
                <div class="total-owed-amount">$${formatNumber(totalOwed)}</div>
            </div>
        </div>`;
    }
    
    // Side-by-side layout for ECB and HPD violations
    html += '<div class="violations-side-by-side">';
    
    // Left side: ECB Violations
    html += '<div class="violations-column">';
    html += '<h3>ECB Violations';
    if (ecbBalance > 0) {
        html += ` <span class="violation-amount-header">$${formatNumber(ecbBalance)} owed</span>`;
    }
    html += '</h3>';
    if (building.ecb_violation_count && building.ecb_violation_count > 0) {
        html += '<div id="ecb-violations-container"></div>';
    } else {
        html += '<div class="no-data">No ECB violations on record</div>';
    }
    html += '</div>';

    // Daily DOB NOW Safety feed. This source is intentionally rendered on
    // every property, even when all stored counts are zero, because the live
    // call is what closes the freshness gap.
    html += `
        <section class="dob-safety-section">
            <div class="dob-safety-head">
                <div>
                    <h3>DOB NOW Safety violations <span class="source-pill">Daily feed</span></h3>
                    <p>Boilers, elevators, façades, gas piping, sprinklers, energy and Local Law civil penalties.</p>
                </div>
                <a href="https://data.cityofnewyork.us/d/855j-jady" target="_blank" rel="noopener">View source</a>
            </div>
            <div id="dob-safety-violations-container"></div>
        </section>`;
    
    // Right side: HPD Violations
    html += '<div class="violations-column">';
    html += '<h3>HPD Violations';
    if (hpdBalance > 0) {
        html += ` <span class="violation-amount-header">$${formatNumber(hpdBalance)} owed</span>`;
    }
    html += '</h3>';
    if (building.hpd_total_violations && building.hpd_total_violations > 0) {
        html += '<div id="hpd-violations-container"></div>';
    } else {
        html += '<div class="no-data">No HPD violations on record</div>';
    }
    html += '</div>';
    
    html += '</div>';
    
    // DOB Violations summary (below the side-by-side)
    if (building.dob_violation_count && building.dob_violation_count > 0) {
        html += `
        <div class="dob-violations-summary">
            <h4>Legacy DOB / BIS violations summary</h4>
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
    
    container.innerHTML = html;
}

// ============================================================================
// LOAD DAILY DOB NOW SAFETY VIOLATIONS
// ============================================================================

function escapeSafetyText(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

async function loadSafetyViolationDetails() {
    const container = document.getElementById('dob-safety-violations-container');
    if (!container) return;
    container.innerHTML = '<div class="loading">Checking the daily DOB Safety feed…</div>';

    try {
        const response = await fetch(`/api/property/${BBL}/safety-violations`);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.error || 'Safety feed unavailable');
        }

        buildingData.building.dob_safety_violation_count = data.total_count;
        buildingData.building.dob_safety_open_violations = data.open_count;
        updateTabBadges();
        renderGlanceStrip();

        if (!data.total_count) {
            const checked = data.checked_at ? new Date(data.checked_at).toLocaleString() : 'just now';
            container.innerHTML = `
                <div class="safety-clear-state">
                    <strong>No DOB Safety violations found</strong>
                    <span>Live check completed ${escapeSafetyText(checked)}.</span>
                </div>`;
            return;
        }

        window.dobSafetyViolationsData = data.violations || [];
        const deviceOptions = (data.by_device_type || []).map(item =>
            `<option value="${escapeSafetyText(item.device_type)}">${escapeSafetyText(item.device_type)} (${formatNumber(item.count)})</option>`
        ).join('');
        const checked = data.checked_at ? new Date(data.checked_at).toLocaleString() : 'just now';

        container.innerHTML = `
            <div class="violation-stats safety-violation-stats">
                <div class="viol-stat">
                    <div class="viol-stat-value">${formatNumber(data.total_count)}</div>
                    <div class="viol-stat-label">Total in daily feed</div>
                </div>
                <div class="viol-stat ${data.open_count ? 'has-open' : ''}">
                    <div class="viol-stat-value">${formatNumber(data.open_count)}</div>
                    <div class="viol-stat-label">Active / pending</div>
                </div>
                <div class="viol-stat">
                    <div class="viol-stat-value">${formatNumber(data.closed_count)}</div>
                    <div class="viol-stat-label">Closed / cured</div>
                </div>
            </div>
            <div class="violations-controls">
                <div class="filter-group">
                    <label for="filter-safety-status">Status:</label>
                    <select id="filter-safety-status" onchange="filterSafetyViolations()">
                        <option value="all">All</option>
                        <option value="open">Active / pending</option>
                        <option value="closed">Closed / cured</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label for="filter-safety-device">Program:</label>
                    <select id="filter-safety-device" onchange="filterSafetyViolations()">
                        <option value="all">All programs</option>
                        ${deviceOptions}
                    </select>
                </div>
                <div class="filter-group">
                    <label for="sort-safety">Sort:</label>
                    <select id="sort-safety" onchange="filterSafetyViolations()">
                        <option value="date-desc">Newest first</option>
                        <option value="date-asc">Oldest first</option>
                    </select>
                </div>
                <span class="safety-checked">Checked ${escapeSafetyText(checked)}</span>
            </div>
            <div class="violations-list" id="dob-safety-violations-list"></div>
            ${data.has_more ? '<div class="note">Showing the 500 newest records. Summary counts include the full result.</div>' : ''}`;
        filterSafetyViolations();
    } catch (error) {
        console.error('Error loading DOB Safety violations:', error);
        container.innerHTML = `
            <div class="safety-error-state">
                <strong>Daily DOB Safety check unavailable</strong>
                <span>${escapeSafetyText(error.message)}</span>
                <button type="button" class="linklike" onclick="loadSafetyViolationDetails()">Retry</button>
            </div>`;
    }
}

function filterSafetyViolations() {
    const rows = window.dobSafetyViolationsData || [];
    const status = document.getElementById('filter-safety-status')?.value || 'all';
    const device = document.getElementById('filter-safety-device')?.value || 'all';
    const sort = document.getElementById('sort-safety')?.value || 'date-desc';
    const container = document.getElementById('dob-safety-violations-list');
    if (!container) return;

    const filtered = rows.filter(row => {
        if (status === 'open' && !row.is_open) return false;
        if (status === 'closed' && row.is_open) return false;
        return device === 'all' || row.device_type === device;
    }).sort((a, b) => {
        const comparison = String(a.issue_date || '').localeCompare(String(b.issue_date || ''));
        return sort === 'date-asc' ? comparison : -comparison;
    });

    if (!filtered.length) {
        container.innerHTML = '<div class="no-data">No Safety violations match these filters</div>';
        return;
    }

    container.innerHTML = filtered.map(row => `
        <article class="violation-detail-card ${row.is_open ? 'violation-open' : 'violation-closed'}">
            <div class="viol-detail-header">
                <span class="viol-id">${escapeSafetyText(row.violation_number || 'Number unavailable')}</span>
                <span class="viol-class">${escapeSafetyText(row.device_type || 'DOB Safety')}</span>
                <span class="viol-status ${row.is_open ? 'status-open' : 'status-closed'}">${escapeSafetyText(row.status || 'Unknown')}</span>
            </div>
            <div class="viol-detail-description">
                <strong>${escapeSafetyText(row.violation_type || 'Safety violation')}</strong>
                ${row.remarks ? `<br>${escapeSafetyText(row.remarks)}` : ''}
            </div>
            <div class="viol-detail-info">
                ${row.issue_date ? `<div><strong>Issued:</strong> ${escapeSafetyText(formatDate(row.issue_date))}</div>` : ''}
                ${row.cycle_end_date ? `<div><strong>Cycle ends:</strong> ${escapeSafetyText(formatDate(row.cycle_end_date))}</div>` : ''}
                ${row.device_number ? `<div><strong>Device:</strong> ${escapeSafetyText(row.device_number)}</div>` : ''}
                ${row.bin ? `<div><strong>BIN:</strong> ${escapeSafetyText(row.bin)}</div>` : ''}
            </div>
        </article>`).join('');
}

// ============================================================================
// LOAD DETAILED ECB VIOLATIONS
// ============================================================================

async function loadECBViolationDetails() {
    const { building } = buildingData;
    const container = document.getElementById('ecb-violations-container');
    container.innerHTML = '<div class="loading">Loading ECB violations...</div>';
    
    try {
        const boro = building.bbl[0];
        const block = building.bbl.substring(1, 6);
        const lot = building.bbl.substring(6, 10);
        
        const apiUrl = `https://data.cityofnewyork.us/resource/6bgk-3dad.json?boro=${boro}&block=${block}&lot=${lot}&$order=issue_date DESC&$limit=500`;
        
        const response = await fetch(apiUrl);
        const violations = await response.json();
        
        if (!violations || violations.length === 0) {
            container.innerHTML = '<div class="no-data">No detailed ECB violations found</div>';
            return;
        }
        
        // Store violations for filtering
        window.ecbViolationsData = violations;
        
        let html = `<div class="violation-summary">${violations.length} violation${violations.length > 1 ? 's' : ''} found</div>`;
        
        // Filters and sorting
        html += `
        <div class="violations-controls">
            <div class="filter-group">
                <label>Status:</label>
                <select id="filter-ecb-status" onchange="filterECBViolations()">
                    <option value="all">All</option>
                    <option value="open">Open/Active</option>
                    <option value="closed">Closed</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Sort by:</label>
                <select id="sort-ecb" onchange="filterECBViolations()">
                    <option value="date-desc">Date (Newest First)</option>
                    <option value="date-asc">Date (Oldest First)</option>
                    <option value="balance-desc">Balance (Highest)</option>
                    <option value="balance-asc">Balance (Lowest)</option>
                </select>
            </div>
        </div>`;
        
        html += '<div class="violations-list" id="ecb-violations-list"></div>';
        container.innerHTML = html;
        
        // Initial render
        filterECBViolations();
        
    } catch (error) {
        console.error('Error loading ECB violations:', error);
        container.innerHTML = '<div class="error">Error loading ECB violations: ' + error.message + '</div>';
    }
}

function filterECBViolations() {
    if (!window.ecbViolationsData) return;
    
    const statusFilter = document.getElementById('filter-ecb-status')?.value || 'all';
    const sortOption = document.getElementById('sort-ecb')?.value || 'date-desc';
    
    // Filter violations
    let filtered = window.ecbViolationsData.filter(v => {
        const balance = parseFloat(v.balance_due || 0);
        const status = (v.ecb_violation_status || '').toUpperCase();
        
        if (statusFilter === 'open' && balance <= 0 && status !== 'ACTIVE') return false;
        if (statusFilter === 'closed' && (balance > 0 || status === 'ACTIVE')) return false;
        
        return true;
    });
    
    // Sort violations
    filtered.sort((a, b) => {
        switch(sortOption) {
            case 'date-desc':
                return (b.issue_date || '').localeCompare(a.issue_date || '');
            case 'date-asc':
                return (a.issue_date || '').localeCompare(b.issue_date || '');
            case 'balance-desc':
                return parseFloat(b.balance_due || 0) - parseFloat(a.balance_due || 0);
            case 'balance-asc':
                return parseFloat(a.balance_due || 0) - parseFloat(b.balance_due || 0);
            default:
                return 0;
        }
    });
    
    // Render filtered violations
    const container = document.getElementById('ecb-violations-list');
    if (filtered.length === 0) {
        container.innerHTML = '<div class="no-data">No violations match the selected filters</div>';
        return;
    }
    
    let html = '';
    filtered.forEach(v => {
        const balance = parseFloat(v.balance_due || 0);
        const penalty = parseFloat(v.penality_imposed || 0);
        const paid = parseFloat(v.amount_paid || 0);
        const status = v.ecb_violation_status || 'Unknown';
        const isOpen = balance > 0 || status.toUpperCase() === 'ACTIVE';
        
        html += `
        <div class="violation-detail-card ${isOpen ? 'violation-open' : 'violation-closed'} ${balance > 0 ? 'violation-alert' : ''}">
            <div class="viol-detail-header">
                <span class="viol-id">ISN: ${v.isn_dob_bis_extract || 'N/A'}</span>
                <span class="viol-status ${isOpen ? 'status-open' : 'status-closed'}">
                    ${status}
                </span>
            </div>
            <div class="viol-detail-description">
                <strong>${v.violation_type || 'ECB Violation'}</strong>
                ${v.section_law_description ? `<br>${v.section_law_description}` : ''}
            </div>
            <div class="viol-detail-info">
                ${v.issue_date ? `<div><strong>Issue Date:</strong> ${formatDate(v.issue_date)}</div>` : ''}
                ${v.hearing_date ? `<div><strong>Hearing Date:</strong> ${formatDate(v.hearing_date.substring(0, 8))}</div>` : ''}
                ${v.hearing_status ? `<div><strong>Hearing Status:</strong> ${v.hearing_status}</div>` : ''}
                ${penalty > 0 ? `<div><strong>Penalty:</strong> $${formatNumber(penalty)}</div>` : ''}
                ${paid > 0 ? `<div><strong>Paid:</strong> $${formatNumber(paid)}</div>` : ''}
                ${balance > 0 ? `<div><strong>Balance Due:</strong> <span class="viol-amount-alert">$${formatNumber(balance)}</span></div>` : ''}
                ${v.respondent_name ? `<div><strong>Respondent:</strong> ${v.respondent_name}</div>` : ''}
                ${v.respondent_house_number || v.respondent_street ? `<div><strong>Address:</strong> ${v.respondent_house_number || ''} ${v.respondent_street || ''}</div>` : ''}
            </div>
        </div>`;
    });
    container.innerHTML = html;
}

// ============================================================================
// LOAD DETAILED HPD VIOLATIONS
// ============================================================================

async function loadHPDViolationDetails() {
    const container = document.getElementById('hpd-violations-container');
    container.innerHTML = '<div class="loading">Loading HPD violations...</div>';
    
    try {
        const response = await fetch(`/api/property/${BBL}/violations`);
        const data = await response.json();
        
        console.log('Violations API response:', data);
        
        if (!data.success) {
            container.innerHTML = `<div class="error">Failed to load HPD violations: ${data.error || 'Unknown error'}</div>`;
            return;
        }
        
        if (data.violations.length === 0) {
            container.innerHTML = '<div class="no-data">No HPD violations found</div>';
            return;
        }
        
        // Store violations data globally for filtering
        window.hpdViolationsData = data.violations;
        
        let html = `<div class="violation-summary">${data.total_count} violation${data.total_count > 1 ? 's' : ''} found</div>`;
        
        // Filters and sorting controls
        html += `
        <div class="violations-controls">
            <div class="filter-group">
                <label>Status:</label>
                <select id="filter-hpd-status" onchange="filterHPDViolations()">
                    <option value="all">All</option>
                    <option value="open">Open Only</option>
                    <option value="closed">Closed Only</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Class:</label>
                <select id="filter-hpd-class" onchange="filterHPDViolations()">
                    <option value="all">All</option>
                    <option value="A">Class A</option>
                    <option value="B">Class B</option>
                    <option value="C">Class C</option>
                    <option value="I">Class I</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Sort by:</label>
                <select id="sort-hpd" onchange="filterHPDViolations()">
                    <option value="date-desc">Date (Newest First)</option>
                    <option value="date-asc">Date (Oldest First)</option>
                    <option value="class-asc">Class (A-Z)</option>
                    <option value="class-desc">Class (Z-A)</option>
                </select>
            </div>
        </div>`;
        
        // Individual violations
        html += '<div class="violations-list" id="hpd-violations-list">';
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
        container.innerHTML = html;
        
    } catch (error) {
        console.error('Error loading HPD violations:', error);
        container.innerHTML = '<div class="error">Error loading HPD violations: ' + error.message + '</div>';
    }
}

function filterHPDViolations() {
    if (!window.hpdViolationsData) return;
    
    const statusFilter = document.getElementById('filter-hpd-status').value;
    const classFilter = document.getElementById('filter-hpd-class').value;
    const sortOption = document.getElementById('sort-hpd').value;
    
    // Filter violations
    let filtered = window.hpdViolationsData.filter(v => {
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
    const container = document.getElementById('hpd-violations-list');
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
    
    // Pills, not emoji: the event's type is what the reader filters on.
    const typeLabels = { permit: 'Permit', transaction: 'Property record', violation: 'Violation' };

    let html = '<div class="activity-timeline">';

    activity_timeline.forEach(event => {
        const type = event.type || 'other';
        html += `
        <div class="activity-item" data-event-type="${type}">
            <div class="activity-date">${formatDate(event.date)}</div>
            <span class="activity-pill pill-${type}">${type === 'transaction' && event.document_type
                ? getDocTypeLabel(event.document_type)
                : (typeLabels[type] || type)}</span>
            <div class="activity-content">
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
    
    let html = '';
    
    // First, show enriched contacts (most valuable)
    const enrichedContacts = buildingData.enriched_contacts || {};
    const hasEnrichedContacts = (enrichedContacts.permit_contacts && enrichedContacts.permit_contacts.length > 0) ||
                                 (enrichedContacts.owner_contacts && enrichedContacts.owner_contacts.length > 0);
    
    if (hasEnrichedContacts) {
        html += '<div class="contacts-section enriched-contacts-section">';
        html += '<h4 class="contacts-section-title">Enriched Contacts <span class="enriched-badge">VERIFIED</span></h4>';
        html += '<div class="contacts-list enriched-list">';
        
        // Owner enrichments
        if (enrichedContacts.owner_contacts) {
            enrichedContacts.owner_contacts.forEach(contact => {
                html += renderEnrichedContactCard(contact, 'Property Owner');
            });
        }
        
        // Permit contact enrichments
        if (enrichedContacts.permit_contacts) {
            enrichedContacts.permit_contacts.forEach(contact => {
                if (contact.has_access) {
                    html += renderEnrichedContactCard(contact, getContactTypeLabel(contact.type));
                } else if (contact.enriched) {
                    // Show locked card
                    html += `
                        <div class="contact-card locked-contact">
                            <div class="contact-name">${contact.name}</div>
                            <div class="contact-role">${getContactTypeLabel(contact.type)}</div>
                            <div class="contact-locked">
                                Contact enriched - <button class="unlock-btn" onclick="unlockPermitContact('${contact.id}')">Unlock for $0.50</button>
                            </div>
                            ${renderTruePeopleSearchLink(contact.name, contact)}
                        </div>
                    `;
                }
            });
        }
        
        html += '</div></div>';
    }
    
    // Then show permit contacts (from permit data)
    if (!contacts || contacts.length === 0) {
        if (!hasEnrichedContacts) {
            container.innerHTML = '<div class="no-data">No contacts available</div>';
            return;
        }
    } else {
        // Filter to only contacts with phone numbers or useful info
        const usefulContacts = contacts.filter(c => c.phone || c.permit_count || truePeopleSearchUrl(c.name, c));
        
        if (usefulContacts.length > 0) {
            html += '<div class="contacts-section permit-contacts-section">';
            html += '<h4 class="contacts-section-title">People on the permits</h4>';
            html += '<div class="contacts-list">';
            
            usefulContacts.forEach(contact => {
                html += `
                <div class="contact-card">
                    <div class="contact-name">${contact.name}</div>
                    <div class="contact-role">${contact.role}</div>
                    ${contact.phone ? `
                        <div class="contact-phone">
                            ${formatPhoneNumber(contact.phone)}
                            ${contact.is_mobile ? ' <span class="mobile-badge">Mobile</span>' : ''}
                            ${contact.line_type ? ` <span class="line-type-badge">${contact.line_type}</span>` : ''}
                        </div>
                    ` : ''}
                    ${contact.needs_revalidation ? '<div class="contact-carrier">Needs phone revalidation</div>' : ''}
                    ${(contact.source || '').includes('legacy_contacts_backup') ? '<div class="contact-carrier">Recovered historical evidence</div>' : ''}
                    ${contact.carrier ? `<div class="contact-carrier">Carrier: ${contact.carrier}</div>` : ''}
                    ${contact.license || contact.license_number ? `<div class="contact-license">License: ${[contact.license, contact.license_number].filter(Boolean).join(' ')}</div>` : ''}
                    ${contact.permit_count ? `<div class="contact-permits">${formatNumber(contact.permit_count)} permit(s) filed</div>` : ''}
                    ${renderTruePeopleSearchLink(contact.name, contact)}
                </div>`;
            });
            
            html += '</div></div>';
        } else if (!hasEnrichedContacts) {
            html = `
                <div class="no-data">
                    <p><strong>${contacts.length} contractors</strong> have worked on this property</p>
                    <p>Phone numbers not available in current dataset</p>
                    <p><em>Tip: Click on a permit and use "Get Contact Info" to find phone numbers</em></p>
                </div>`;
        }
    }
    
    container.innerHTML = html || '<div class="no-data">No contacts available</div>';
    
    // Load enriched contacts if not already loaded
    if (!buildingData.enriched_contacts) {
        loadEnrichedContacts();
    }
}

/**
 * Render an enriched contact card for the Contacts tab
 */
function renderEnrichedContactCard(contact, roleLabel) {
    let html = `
        <div class="contact-card enriched-contact-card">
            <div class="contact-header">
                <div class="contact-name">${contact.name}</div>
                <span class="verified-badge">Verified</span>
            </div>
            <div class="contact-role">${roleLabel}</div>
    `;
    
    // Show phones
    if (contact.phones && contact.phones.length > 0) {
        contact.phones.forEach(phone => {
            html += `
                <div class="contact-phone enriched-phone">
                    <a href="tel:${phone.number}">${formatPhoneNumber(phone.number)}</a>
                    ${phone.type ? `<span class="phone-type-badge">${phone.type}</span>` : ''}
                </div>
            `;
        });
    }
    
    // Show emails
    if (contact.emails && contact.emails.length > 0) {
        contact.emails.forEach(email => {
            html += `
                <div class="contact-email enriched-email">
                    <a href="mailto:${email.email}">${email.email}</a>
                </div>
            `;
        });
    }
    
    // Show license info if available
    if (contact.license_number) {
        html += `
            <div class="contact-license">
                License: ${contact.license_number}${contact.license_type ? ` (${contact.license_type})` : ''}
            </div>
        `;
    }
    
    // Show enriched date
    if (contact.enriched_at) {
        const date = new Date(contact.enriched_at);
        html += `<div class="contact-enriched-date">Enriched: ${date.toLocaleDateString()}</div>`;
    }
    html += renderTruePeopleSearchLink(contact.name, contact);
    
    html += '</div>';
    return html;
}

/**
 * Get display label for contact type
 */
function getContactTypeLabel(type) {
    const labels = {
        'applicant': 'Permit Applicant',
        'permittee': 'Licensed Contractor',
        'owner': 'Property Owner',
        'superintendent': 'Superintendent'
    };
    return labels[type] || type;
}

/**
 * Load enriched contacts from API
 */
async function loadEnrichedContacts() {
    try {
        const bbl = buildingData?.building?.bbl || BBL;
        const response = await fetch(`/api/building/${bbl}/enriched-contacts`);
        const data = await response.json();
        
        if (data.success) {
            buildingData.enriched_contacts = {
                permit_contacts: data.permit_contacts,
                owner_contacts: data.owner_contacts
            };
            
            // Re-render if we got new data
            if ((data.permit_contacts && data.permit_contacts.length > 0) ||
                (data.owner_contacts && data.owner_contacts.length > 0)) {
                renderContactsTab();
            }
        }
    } catch (error) {
        console.error('Error loading enriched contacts:', error);
    }
}

/**
 * Unlock a permit contact that was enriched by another user
 */
async function unlockPermitContact(enrichmentId) {
    // TODO: Implement unlock flow - similar to enrich but just grants access
    alert('Contact unlock coming soon! For now, please re-enrich from the permit modal.');
}


// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

function formatDate(dateStr) {
    if (!dateStr) return 'Unknown';
    // PostgreSQL DATE values arrive as YYYY-MM-DD. JavaScript interprets
    // that form as midnight UTC, which renders as the previous day in NYC.
    const text = String(dateStr);
    const date = /^\d{4}-\d{2}-\d{2}$/.test(text)
        ? new Date(`${text}T12:00:00`)
        : new Date(text);
    if (Number.isNaN(date.getTime())) return 'Unknown';
    return date.toLocaleDateString('en-US', {
        year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC'
    });
}

function formatPermitDate(permit) {
    if (permit.issue_date) {
        return 'Issued: ' + formatDate(permit.issue_date);
    } else if (permit.filing_date) {
        return 'Filed: ' + formatDate(permit.filing_date);
    }
    return 'No Date';
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
        'DEED': 'Deed Transfer',
        'DEEDO': 'Deed (Other)',
        'MTGE': 'Mortgage',
        'M&CON': 'Mortgage & Consolidation',
        'AGMT': 'Agreement',
        'SAT': 'Satisfaction of Mortgage',
        'SATF': 'Satisfaction (Full)',
        'UCC': 'UCC Filing',
        'ASST': 'Assignment'
    };
    return labels[docType] || docType;
}

function showError(message) {
    console.error(message);
    document.getElementById('building-address').textContent = 'Error Loading Property';
    document.getElementById('risk-score-value').textContent = '!';
    document.getElementById('risk-score-label').textContent = 'ERROR';
}

// ============================================================================
// OWNER RESEARCH — evidence, manual review, and source refresh
// ============================================================================

const ownerResearchState = {
    data: null, sources: null, loading: false, sequence: 0,
    sourceSequence: 0, pollTimer: null, pollCount: 0, pendingSources: new Set(),
};
const RESEARCH_STATUSES = {
    not_researched: 'Not researched', needs_review: 'Needs review',
    contact_found: 'Contact found', do_not_contact: 'Do not contact',
};
const RESEARCH_MATCHES = {
    unreviewed: 'Unreviewed', confirmed_match: 'Confirmed match',
    possible_match: 'Possible match', wrong_person: 'Wrong person',
};

function normalizePeopleSearchName(name) {
    const parts = String(name || '').trim().replace(/\s+/g, ' ').split(',').map(part => part.trim()).filter(Boolean);
    const suffix = /^(?:JR\.?|SR\.?|II|III|IV|V)$/i;
    return parts.length > 1 && !suffix.test(parts[1])
        ? [parts[1], parts[0], ...parts.slice(2)].join(' ') : parts.join(' ');
}

function buildPeopleSearchUrl(name, location = '') {
    if (!String(name || '').trim()) return null;
    const url = new URL('https://www.truepeoplesearch.com/results');
    url.searchParams.set('name', String(name).trim());
    if (String(location || '').trim()) url.searchParams.set('citystatezip', String(location).trim());
    return url.href;
}

function researchLocationText(location = {}) {
    const city = String(location.city || '').trim();
    const state = String(location.state || '').trim();
    const zip = String(location.zip_code || location.zip || '').match(/^\d{5}\b/)?.[0];
    return city ? [city, state].filter(Boolean).join(', ') : zip || state;
}

function propertyResearchLocation() {
    if (ownerResearchState.data?.property_location) return ownerResearchState.data.property_location;
    const building = buildingData?.building || {};
    const borough = String(building.borough_name || building.borough || '').trim();
    const area = /^[1-5]$/.test(borough) ? getBoroughName(borough) : borough;
    return {id: 'property', label: 'Property location', is_property: true,
        zip_code: building.zip_code,
        city: building.zip_code ? '' : /^manhattan$/i.test(area) ? 'New York' : area,
        state: 'NY'};
}

function ownerSearchOptions(person = {}) {
    const locations = Array.isArray(person.locations) ? person.locations : [];
    const options = locations.filter(location => !location.is_property && researchLocationText(location)).map((location, index) => ({
        id: String(location.id || `reported-${index}`), value: researchLocationText(location),
        label: `${researchLocationText(location)} — ${location.source || location.label || 'Reported location'}${location.reported_date ? ` · ${formatDate(location.reported_date)}` : ' · date unavailable'}`,
        note: `${location.kind || 'Source-reported location'}. This is not confirmation of a current home address.`,
    }));
    if (!options.length && researchLocationText(person)) {
        options.push({id: 'reported-contact', value: researchLocationText(person),
            label: `${researchLocationText(person)} — reported contact location`, note: 'Reported contact location; date unavailable.'});
    }
    const property = propertyResearchLocation();
    const propertyText = property.zip_code ? String(property.zip_code).match(/^\d{5}\b/)?.[0] : researchLocationText(property);
    if (propertyText) options.push({id: 'property', value: propertyText,
        label: `${propertyText} — property location (fallback)`,
        note: 'Property location fallback. The person may live or receive mail elsewhere.'});
    options.push({id: 'custom', value: '', label: 'Enter another city/state or ZIP', note: 'Use a city/state or ZIP, not a street address.'});
    options.push({id: 'name-only', value: '', label: 'Search by name only', note: 'Search without a location filter.'});
    return options;
}

function researchNameKey(name) {
    return normalizePeopleSearchName(name).toLocaleUpperCase('en-US').replace(/[^\p{L}\p{N}]/gu, '');
}

function researchPersonBlocked(person) {
    if (person?.do_not_contact || person?.research?.status === 'do_not_contact') return true;
    const key = researchNameKey(person?.name);
    return Boolean(key && ownerResearchState.data?.people?.some(candidate =>
        (candidate.do_not_contact || candidate.research?.status === 'do_not_contact') && researchNameKey(candidate.name) === key));
}

function researchFeedback(message, isError = false) {
    const node = document.getElementById('owner-research-feedback');
    if (!node) return;
    node.textContent = message;
    node.classList.toggle('research-error', isError);
}

async function researchRequest(url, options = {}) {
    const response = await fetch(url, {credentials: 'same-origin', ...options});
    let data;
    try { data = await response.json(); } catch (_error) {
        throw new Error(response.status === 401 || response.redirected
            ? 'Your session has expired. Sign in again, then reload research.' : 'The server returned an unreadable response. Please try again.');
    }
    if (!response.ok || !data.success) {
        const error = new Error(data.error || data.message || (response.status === 401 ? 'Please sign in again.' : 'Unable to complete this request.'));
        error.status = response.status;
        throw error;
    }
    return data;
}

async function loadOwnerResearch() {
    if (!document.getElementById('owner-research-people')) return false;
    const sequence = ++ownerResearchState.sequence;
    ownerResearchState.loading = true;
    try {
        const data = await researchRequest(`/api/property/${encodeURIComponent(BBL)}/owner-research`);
        if (sequence !== ownerResearchState.sequence) return false;
        ownerResearchState.data = data;
        renderOwnerResearch();
        return true;
    } catch (error) {
        if (sequence === ownerResearchState.sequence) {
            researchFeedback(error.message, true);
            if (!ownerResearchState.data) document.getElementById('owner-research-people').innerHTML =
                '<p class="research-muted">Research could not be loaded. Use Reload research to try again.</p>';
        }
        return false;
    } finally {
        if (sequence === ownerResearchState.sequence) ownerResearchState.loading = false;
    }
}

function renderResearchSource(source) {
    const href = safeHttpHref(source.url);
    const label = escapeHtml(source.label || source.key || 'Source');
    const date = source.reported_date ? `${source.date_label || 'Reported'}: ${formatDate(source.reported_date)}` : source.period || 'Reported date unavailable';
    return `<li>${href ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label} ↗</a>` : `<strong>${label}</strong>`}
        <span>${escapeHtml(date)}${source.reported_date && source.period ? ` · ${escapeHtml(source.period)}` : ''}</span></li>`;
}

function renderResearchPerson(person) {
    const review = person.research || {};
    const blocked = researchPersonBlocked(person);
    const locations = Array.isArray(person.locations) ? person.locations : [];
    const reviewer = typeof review.reviewed_by === 'object' ? review.reviewed_by?.name : review.reviewed_by;
    const phones = (review.phones || []).map(value => escapeHtml(value)).join(' · ');
    const emails = (review.emails || []).map(value => escapeHtml(value)).join(' · ');
    const resultUrl = safeHttpHref(review.result_url);
    return `<article class="research-person${blocked ? ' research-person-blocked' : ''}" data-research-person="${escapeHtml(person.id)}">
        <div class="research-person-head"><div><h5>${escapeHtml(person.name)}</h5><p>${escapeHtml(person.role || 'Reported person')}${person.historical ? ' · Historical record' : ''}</p></div>
            <span class="research-status${blocked ? ' research-status-blocked' : ''}">${escapeHtml(blocked ? RESEARCH_STATUSES.do_not_contact : RESEARCH_STATUSES[review.status] || RESEARCH_STATUSES.not_researched)}</span></div>
        <ul class="research-evidence">${(person.sources || []).map(renderResearchSource).join('')}</ul>
        <div class="research-locations"><strong>Reported search locations</strong>${locations.length ? `<ul>${locations.map(location =>
            `<li>${escapeHtml(researchLocationText(location) || 'Location unavailable')}<span>${escapeHtml(location.source || location.label || 'Source-reported')}${location.reported_date ? ` · ${escapeHtml(formatDate(location.reported_date))}` : ' · date unavailable'}${location.kind ? ` · ${escapeHtml(location.kind)}` : ''}</span></li>`).join('')}</ul>`
            : '<p>No matched owner locality available. Search can use the property location as a fallback.</p>'}</div>
        <div class="research-review-summary"><span class="research-match research-match-${Object.hasOwn(RESEARCH_MATCHES, review.match_status) ? review.match_status : 'unreviewed'}">${escapeHtml(RESEARCH_MATCHES[review.match_status] || RESEARCH_MATCHES.unreviewed)}</span>
            ${review.match_status === 'wrong_person' ? '<p>This saved result was marked as a different person.</p>' : ''}
            ${review.match_status === 'possible_match' ? '<p>Match still needs confirmation.</p>' : ''}
            ${phones ? `<p><strong>Saved phone:</strong> ${phones}</p>` : ''}${emails ? `<p><strong>Saved email:</strong> ${emails}</p>` : ''}
            ${resultUrl ? blocked ? '<p class="research-muted">Reviewed result retained. External lookup is disabled while do not contact is set.</p>' : `<p><a href="${escapeHtml(resultUrl)}" target="_blank" rel="noopener noreferrer">Reviewed result ↗</a></p>` : ''}
            ${review.notes ? `<p class="research-note">${escapeHtml(review.notes)}</p>` : ''}
            ${review.reviewed_at ? `<p class="research-muted">Reviewed ${escapeHtml(formatDate(review.reviewed_at))}${reviewer ? ` by ${escapeHtml(reviewer)}` : ''}</p>` : ''}</div>
        ${blocked ? '<p class="research-dnc-note">Do not contact. People searches and enrichment are disabled for this name on this property.</p>' : ''}
        <div class="research-actions"><button type="button" class="research-button research-button-primary" data-research-action="search" ${blocked || person.is_person === false ? 'disabled' : ''}>Preview people search</button>
            <button type="button" class="research-button" data-research-action="review">${review.reviewed_at ? 'Edit review' : 'Save a reviewed result'}</button></div>
    </article>`;
}

function renderOwnerResearch() {
    const data = ownerResearchState.data;
    if (!data) return;
    const people = document.getElementById('owner-research-people');
    if (people) people.innerHTML = data.people?.length ? data.people.map(renderResearchPerson).join('')
        : '<p class="research-muted">No individual people found in the saved ownership sources yet. Refresh a source below to check for newer records.</p>';
    const conflicts = document.getElementById('owner-research-conflicts');
    if (conflicts) conflicts.innerHTML = data.conflicts?.length ? `<div class="research-conflicts"><strong>Review these differences</strong><ul>${data.conflicts.map(conflict =>
        `<li>${escapeHtml(conflict.message)}</li>`).join('')}</ul><p>Roles and reporting dates can explain differences. These flags do not establish who owns the property.</p></div>` : '';
    // Disable the older manual entry points too, without merging same-named records.
    document.querySelectorAll('[data-people-search]').forEach(button => {
        try {
            const person = JSON.parse(button.dataset.peopleSearch);
            button.disabled = researchPersonBlocked(person);
            button.title = button.disabled ? 'Do not contact is set for this name on this property.' : `Preview search for ${person.name}`;
        } catch (_error) { button.disabled = true; }
    });
}

function openResearchDialog(title, body) {
    document.getElementById('owner-research-dialog')?.close();
    const previousFocus = document.activeElement;
    const dialog = document.createElement('dialog');
    dialog.id = 'owner-research-dialog';
    dialog.className = 'research-dialog';
    dialog.setAttribute('aria-labelledby', 'research-dialog-title');
    dialog.innerHTML = `<div class="research-dialog-head"><h2 id="research-dialog-title">${escapeHtml(title)}</h2><button type="button" class="research-dialog-close" aria-label="Close dialog">×</button></div>${body}`;
    dialog.querySelector('.research-dialog-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => {
        dialog.remove();
        if (previousFocus?.isConnected) previousFocus.focus();
    }, {once: true});
    dialog.addEventListener('click', event => {
        if (event.target === dialog) {
            const rect = dialog.getBoundingClientRect();
            if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
        }
    });
    document.body.appendChild(dialog);
    dialog.showModal(); // Native modal dialog supplies focus containment and Escape handling.
    return dialog;
}

function openPeopleSearchPreview(person) {
    if (researchPersonBlocked(person)) {
        researchFeedback('Do not contact is set for this name. Change its review status before searching.', true);
        return;
    }
    const options = ownerSearchOptions(person);
    const selected = options.find(option => option.id === person.default_location_id)?.id || options[0].id;
    const dialog = openResearchDialog('Preview people search', `
        <p class="research-muted">Choose the name and location to send to TruePeopleSearch. Results open in a new tab; return here to save your reviewed match.</p>
        <div class="research-form"><label for="research-search-name">Name</label><input id="research-search-name" maxlength="200" value="${escapeHtml(normalizePeopleSearchName(person.name))}" autocomplete="off">
        <label for="research-search-location">Search location</label><select id="research-search-location">${options.map(option => `<option value="${escapeHtml(option.id)}" ${option.id === selected ? 'selected' : ''}>${escapeHtml(option.label)}</option>`).join('')}</select>
        <div id="research-custom-location" hidden><label for="research-search-custom">City and state, or ZIP</label><input id="research-search-custom" maxlength="120" placeholder="e.g. Fort Lee, NJ or 07024" autocomplete="off"></div>
        <p id="research-location-note" class="research-muted"></p>
        <p id="research-search-error" class="research-error" role="alert"></p>
        <label for="research-search-url">Search link</label><input id="research-search-url" class="research-url" readonly>
        <div class="research-dialog-actions"><a id="research-search-open" class="research-button research-button-primary" target="_blank" rel="noopener noreferrer">Open TruePeopleSearch ↗</a>${person.id ? '<button type="button" class="research-button" id="research-search-save">Save a reviewed result</button>' : ''}</div></div>`);
    const name = dialog.querySelector('#research-search-name');
    const select = dialog.querySelector('#research-search-location');
    const custom = dialog.querySelector('#research-search-custom');
    const open = dialog.querySelector('#research-search-open');
    const update = () => {
        const option = options.find(item => item.id === select.value) || options[0];
        dialog.querySelector('#research-custom-location').hidden = option.id !== 'custom';
        dialog.querySelector('#research-location-note').textContent = option.note;
        const location = option.id === 'custom' ? custom.value : option.value;
        const invalidStreet = option.id === 'custom' && /\d+\s+\S/.test(location.trim()) && !/^\d{5}(?:-\d{4})?$/.test(location.trim());
        const blocked = researchPersonBlocked(person) || researchPersonBlocked({name: name.value});
        const error = blocked ? 'Do not contact is set for this name. People search is disabled.' : !name.value.trim() ? 'Enter a name to search.' : invalidStreet ? 'Use a city/state or ZIP instead of a street address.' : option.id === 'custom' && !location.trim() ? 'Enter a city/state or ZIP, or choose Search by name only.' : '';
        const url = error ? null : buildPeopleSearchUrl(name.value, location);
        dialog.querySelector('#research-search-error').textContent = error;
        dialog.querySelector('#research-search-url').value = url || '';
        if (url) open.href = url; else open.removeAttribute('href');
        open.setAttribute('aria-disabled', String(!url));
    };
    [name, select, custom].forEach(input => input.addEventListener('input', update));
    select.addEventListener('change', update);
    open.addEventListener('click', event => {
        update();
        if (open.getAttribute('aria-disabled') === 'true' || researchPersonBlocked(person) || researchPersonBlocked({name: name.value})) event.preventDefault();
    });
    dialog.querySelector('#research-search-save')?.addEventListener('click', () => {
        dialog.close();
        openOwnerReview(person);
    });
    update();
    name.focus();
}

function researchSelectOptions(values, selected) {
    return Object.entries(values).map(([value, label]) => `<option value="${value}" ${selected === value ? 'selected' : ''}>${label}</option>`).join('');
}

function researchContactLines(value) {
    return [...new Set(String(value || '').split(/[\n;,]+/).map(item => item.trim()).filter(Boolean))];
}

function validResearchResultUrl(value) {
    if (!value) return true;
    try {
        const url = new URL(value);
        return url.protocol === 'https:' && ['truepeoplesearch.com', 'www.truepeoplesearch.com'].includes(url.hostname)
            && !url.username && !url.password && (!url.port || url.port === '443') && !/[\s\\]/.test(value);
    } catch (_error) { return false; }
}

function openOwnerReview(person) {
    const review = person.research || {};
    const dialog = openResearchDialog(`Review ${person.name}`, `
        <form id="owner-review-form" class="research-form">
            <p class="research-muted">Save only contact details you reviewed for this person. The match label records your assessment, not an automatic identity verification.</p>
            <label for="research-review-status">Research status</label><select id="research-review-status" name="status">${researchSelectOptions(RESEARCH_STATUSES, review.status || 'not_researched')}</select>
            <label for="research-review-match">Result match</label><select id="research-review-match" name="match_status">${researchSelectOptions(RESEARCH_MATCHES, review.match_status || 'unreviewed')}</select>
            <label for="research-review-url">TruePeopleSearch result link <span class="research-muted">(optional)</span></label><input type="url" id="research-review-url" name="result_url" value="${escapeHtml(review.result_url || '')}" maxlength="2048" placeholder="https://www.truepeoplesearch.com/…">
            <div class="research-form-columns"><div><label for="research-review-phones">Phones <span class="research-muted">(one per line)</span></label><textarea id="research-review-phones" name="phones" rows="3" maxlength="2000">${escapeHtml((review.phones || []).join('\n'))}</textarea></div>
            <div><label for="research-review-emails">Emails <span class="research-muted">(one per line)</span></label><textarea id="research-review-emails" name="emails" rows="3" maxlength="3000">${escapeHtml((review.emails || []).join('\n'))}</textarea></div></div>
            <label for="research-review-notes">Review notes</label><textarea id="research-review-notes" name="notes" rows="3" maxlength="4000" placeholder="Why this looks like a match, or what needs another look">${escapeHtml(review.notes || '')}</textarea>
            <p class="research-muted">Do not contact disables people searches and enrichment for this name on this property. To mark a wrong person, clear their phone and email first. Saved reviews are shared with your team.</p>
            <p id="research-review-error" class="research-error" role="alert"></p>
            <div class="research-dialog-actions"><button type="submit" class="research-button research-button-primary">Save review</button><button type="button" id="research-review-cancel" class="research-button">Cancel</button></div>
        </form>`);
    const form = dialog.querySelector('#owner-review-form');
    dialog.querySelector('#research-review-cancel').addEventListener('click', () => dialog.close());
    form.addEventListener('submit', async event => {
        event.preventDefault();
        const save = form.querySelector('button[type="submit"]');
        if (save.disabled) return;
        const fields = form.elements;
        const payload = {version: review.version || 0, status: fields.status.value,
            match_status: fields.match_status.value, result_url: fields.result_url.value.trim(),
            phones: researchContactLines(fields.phones.value), emails: researchContactLines(fields.emails.value),
            notes: fields.notes.value.trim()};
        const errorNode = dialog.querySelector('#research-review-error');
        if (!validResearchResultUrl(payload.result_url)) {
            errorNode.textContent = 'Use an HTTPS TruePeopleSearch result link.';
            return;
        }
        save.disabled = true;
        save.textContent = 'Saving…';
        errorNode.textContent = '';
        try {
            const data = await researchRequest(`/api/property/${encodeURIComponent(BBL)}/owner-research/${encodeURIComponent(person.id)}`, {
                method: 'POST', headers: {'Content-Type': 'application/json', 'X-Owner-Research': '1'}, body: JSON.stringify(payload),
            });
            if (data.person && ownerResearchState.data) {
                const index = ownerResearchState.data.people.findIndex(item => item.id === person.id);
                if (index >= 0) ownerResearchState.data.people[index] = data.person;
                renderOwnerResearch();
            }
            dialog.close();
            researchFeedback(`Review saved for ${person.name}.`);
            loadOwnerResearch();
        } catch (error) {
            errorNode.textContent = error.status === 409
                ? 'Another teammate updated this review. Your edits remain here. Copy any notes you need, close this dialog, and reload research before saving again.' : error.message;
        } finally {
            save.disabled = false;
            save.textContent = 'Save review';
        }
    });
}

function researchHistoryValue(value) {
    if (value === null || value === undefined || value === '') return 'Not reported';
    if (Array.isArray(value)) return value.length ? value.map(researchHistoryValue).join('; ') : 'None';
    if (typeof value === 'object') return Object.entries(value).filter(([, item]) => item !== null && item !== '' && item !== undefined)
        .map(([key, item]) => `${key.replace(/_/g, ' ')}: ${researchHistoryValue(item)}`).join(' · ') || 'Not reported';
    return String(value);
}

function researchRetryScheduled(source) {
    return source.status === 'queued' && Boolean(source.error) && source.next_attempt_at
        && new Date(source.next_attempt_at).getTime() > Date.now();
}

function renderOwnerSourceStatus() {
    const data = ownerResearchState.sources;
    if (!data) return;
    const node = document.getElementById('owner-research-sources');
    if (node) node.innerHTML = (data.sources || []).map(source => {
        const pending = ['queued', 'running'].includes(source.status) || ownerResearchState.pendingSources.has(source.key);
        const retryScheduled = researchRetryScheduled(source);
        return `<div class="research-source-row"><div><strong>${escapeHtml(source.label || source.key)}</strong>
            <span>${escapeHtml(retryScheduled ? 'Retry scheduled' : pending ? 'Refresh in progress' : source.status === 'failed' ? 'Last refresh failed' : source.checked_at ? `Last checked ${formatDate(source.checked_at)}` : 'Not checked yet')}</span>
            ${source.error ? `<span class="research-error">${escapeHtml(source.error)}</span>` : ''}
            ${retryScheduled || (!pending && !source.can_refresh && source.next_attempt_at) ? `<span>${retryScheduled ? 'Next retry' : 'Refresh available after'} ${escapeHtml(new Date(source.next_attempt_at).toLocaleString('en-US'))}</span>` : ''}</div>
            <button type="button" class="research-button" data-research-action="refresh" data-research-source="${escapeHtml(source.key)}" ${pending || !source.can_refresh ? 'disabled' : ''}>${retryScheduled ? 'Retry scheduled' : pending ? 'Refreshing…' : 'Refresh'}</button></div>`;
    }).join('') || '<p class="research-muted">Source refresh is not available for this property yet.</p>';
    const historyNode = document.getElementById('owner-research-history');
    if (historyNode) historyNode.innerHTML = `<h5>Observed changes</h5>${data.history?.length ? data.history.map(entry => {
        const source = data.sources?.find(item => item.key === entry.source)?.label || entry.source;
        const changes = entry.changes || [];
        return `<details class="research-history-item"><summary>${escapeHtml(source || 'Source')} · ${entry.kind === 'baseline' ? 'First saved snapshot' : 'Record changed'} <span>${escapeHtml(formatDate(entry.observed_at))}</span></summary>
            <p class="research-muted">Observed ${escapeHtml(formatDate(entry.observed_at))}${entry.reported_date ? ` · Source reported ${escapeHtml(formatDate(entry.reported_date))}` : ' · Source report date unavailable'}</p>
            ${entry.kind === 'baseline' ? `<p>${escapeHtml(researchHistoryValue(entry.after))}</p>` : changes.length ? changes.map(change =>
                `<div class="research-history-change"><strong>${escapeHtml(String(change.field || 'Record').replace(/_/g, ' '))}</strong><p><span>Before:</span> ${escapeHtml(researchHistoryValue(change.before))}</p><p><span>After:</span> ${escapeHtml(researchHistoryValue(change.after))}</p></div>`).join('')
                : `<p><strong>Before:</strong> ${escapeHtml(researchHistoryValue(entry.before))}</p><p><strong>After:</strong> ${escapeHtml(researchHistoryValue(entry.after))}</p>`}</details>`;
    }).join('') : '<p class="research-muted">No saved source changes yet. The first successful refresh establishes a baseline.</p>'}`;
}

async function loadOwnerSourceStatus({poll = false} = {}) {
    if (!document.getElementById('owner-research-sources')) return;
    const sequence = ++ownerResearchState.sourceSequence;
    clearTimeout(ownerResearchState.pollTimer);
    try {
        const data = await researchRequest(`/api/property/${encodeURIComponent(BBL)}/owner-sources`);
        if (sequence !== ownerResearchState.sourceSequence) return;
        const previouslyPending = new Set(ownerResearchState.pendingSources);
        ownerResearchState.sources = data;
        ownerResearchState.pendingSources = new Set((data.sources || []).filter(source => ['queued', 'running'].includes(source.status)).map(source => source.key));
        const finished = [...previouslyPending].some(key => !ownerResearchState.pendingSources.has(key));
        renderOwnerSourceStatus();
        if (finished) {
            await loadOwnerResearch();
            // Keep the original ownership summary in step with the research
            // cards without replacing an open review dialog or its draft.
            await refreshOwnerProfileSummary();
            const failed = (data.sources || []).some(source => previouslyPending.has(source.key) && source.status === 'failed');
            researchFeedback(failed ? 'A source refresh failed. Showing the last saved records; your reviews are unchanged.' : 'Source refresh complete. People and reported locations have been updated.', failed);
        }
        const activeSources = (data.sources || []).filter(source => ['queued', 'running'].includes(source.status) && !researchRetryScheduled(source));
        if (activeSources.length) {
            if (!poll) ownerResearchState.pollCount = 0;
            if (++ownerResearchState.pollCount <= 20) ownerResearchState.pollTimer = setTimeout(() => loadOwnerSourceStatus({poll: true}), 3000);
            else researchFeedback('The source is still refreshing. Use Reload research to check again; your open review stays intact.');
        } else if (ownerResearchState.pendingSources.size) {
            researchFeedback('A source retry is scheduled. Previously saved records remain available; check the next retry time below.');
        }
        return true;
    } catch (error) {
        researchFeedback(error.message, true);
        if (!ownerResearchState.sources) document.getElementById('owner-research-sources').innerHTML = '<p class="research-muted">Source status could not be loaded. Use Reload research to try again.</p>';
        return false;
    }
}

async function refreshOwnerProfileSummary() {
    try {
        const data = await researchRequest(`/api/building-profile/${encodeURIComponent(BBL)}`);
        buildingData = data;
        renderHeroSection();
        renderOwnersTab();
        renderOwnerResearch();
        updateTabBadges();
    } catch (_error) {
        // Research remains usable even if the broader profile is unavailable.
        // Its independent reload action retries the ownership summary too.
    }
}

async function refreshOwnerResearchSource(key) {
    if (ownerResearchState.pendingSources.has(key)) return;
    ownerResearchState.pendingSources.add(key);
    renderOwnerSourceStatus();
    try {
        const data = await researchRequest(`/api/property/${encodeURIComponent(BBL)}/owner-sources/${encodeURIComponent(key)}/refresh`, {
            method: 'POST', headers: {'Content-Type': 'application/json', 'X-Owner-Research': '1'}, body: '{}',
        });
        researchFeedback(data.message || (data.status === 'failed' ? 'Refresh failed. Showing the last saved record.' : 'Refresh requested.'), data.status === 'failed');
        await loadOwnerSourceStatus();
    } catch (error) {
        ownerResearchState.pendingSources.delete(key);
        renderOwnerSourceStatus();
        researchFeedback(error.message, true);
    }
}

function setupOwnerResearch() {
    document.addEventListener('click', async event => {
        const manual = event.target?.closest?.('[data-people-search]');
        if (manual) {
            event.preventDefault();
            if (manual.disabled) return;
            if (!ownerResearchState.data && !await loadOwnerResearch()) return;
            try {
                const supplied = JSON.parse(manual.dataset.peopleSearch);
                // A legacy row carries only its own reported locality. A shared
                // name cannot establish which research identity it belongs to.
                openPeopleSearchPreview(supplied);
            } catch (_error) { researchFeedback('Unable to prepare this people search. Reload research and try again.', true); }
            return;
        }
        const button = event.target?.closest?.('[data-research-action]');
        if (!button || button.disabled) return;
        const action = button.dataset.researchAction;
        if (action === 'reload') {
            button.disabled = true;
            researchFeedback('Checking saved research and source status…');
            const loaded = await loadOwnerResearch();
            const sourcesLoaded = await loadOwnerSourceStatus();
            if (loaded && sourcesLoaded) await refreshOwnerProfileSummary();
            if (loaded && sourcesLoaded) researchFeedback('Research reloaded. Any open review edits are unchanged.');
            button.disabled = false;
            return;
        }
        if (action === 'refresh') { refreshOwnerResearchSource(button.dataset.researchSource); return; }
        const id = button.closest('[data-research-person]')?.dataset.researchPerson;
        const person = ownerResearchState.data?.people?.find(item => item.id === id);
        if (!person) return;
        if (action === 'search') openPeopleSearchPreview(person);
        if (action === 'review') openOwnerReview(person);
    });
    window.addEventListener('pagehide', () => clearTimeout(ownerResearchState.pollTimer));
}
