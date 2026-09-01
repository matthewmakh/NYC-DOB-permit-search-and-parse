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
    setupTabNavigation();
    
    // Setup modal
    setupRiskModal();

    // Keep the top of the profile compact while making the full tax-lot
    // record one clear action away (expanded by default on wide screens).
    setupBuildingFactsDisclosure();
    
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

        // Update tab badges
        updateTabBadges();

        // Violations detail lists used to load when their tab was opened;
        // on the one-page dossier they load the first time the section
        // scrolls into view instead.
        setupViolationsLazyLoad();

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
        'rpad': 'Historical RPAD Assessment (through FY2018/19)',
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
            <span class="owner-name sos-name">${sos_data.principal_name}</span>
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
                <span class="owner-name">${name}</span>
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
          value: building.is_cash_purchase ? 'Cash'
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
        signals.push({ tone: building.tax_delinquency_water_only ? 'amber' : 'red',
                       text: `Tax delinquency — ${building.tax_delinquency_count} notice(s)${building.tax_delinquency_water_only ? ' (water only)' : ''}` });
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
                            <span class="owner-option-name">${owner.name}</span>
                            <span class="owner-option-source">${owner.source}</span>
                            <span class="enriched-badge">Unlocked</span>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }
    
    // Auto-select first recommended or first available
    const firstRecommendedIdx = availableOwners.findIndex(o => o.recommended);
    const autoSelectIdx = firstRecommendedIdx >= 0 ? firstRecommendedIdx : 0;
    
    modal.innerHTML = `
        <div class="modal-content enrich-modal-content">
            <button type="button" class="modal-close" aria-label="Close owner enrichment" onclick="closeEnrichModal()">&times;</button>
            <h2>Get Owner Contact Information</h2>
            <p class="modal-subtitle">Only confident human names associated with this property are eligible. Companies, banks, trusts, and agents are never sent to the paid lookup.</p>
            
            ${enrichedHtml}
            
            <div class="owner-selection">
                <h4>Human candidates (${availableOwners.length})</h4>
                ${availableOwners.map((owner, idx) => `
                    <label class="owner-option ${owner.recommended ? 'recommended' : ''}">
                        <input type="radio" name="owner" value="${idx}" ${idx === autoSelectIdx ? 'checked' : ''}>
                        <div class="owner-option-content">
                            <span class="owner-option-name">${owner.name}</span>
                            <span class="owner-option-source">${owner.source}</span>
                            <span class="real-person-badge">PERSON</span>
                            ${owner.recommended ? '<span class="recommended-badge">Recommended</span>' : ''}
                            ${owner.reason ? `<span class="owner-option-reason">${owner.reason}</span>` : ''}
                        </div>
                    </label>
                `).join('')}
            </div>
            
            <div class="enrich-footer">
                <p class="enrich-cost-display">Cost: <strong>${cost}</strong> per lookup</p>
                <button class="btn btn-primary enrich-confirm-btn" onclick="confirmEnrich(${buildingId})">
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
function setupTabNavigation() {
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            switchTab(btn.getAttribute('data-tab'));
        });
    });

    // Deterministic scrollspy: the active section is the last one whose top
    // has passed the sticky-header line. Ratio-based observers pick the
    // biggest section on screen, which is wrong next to short ones.
    const sections = Array.from(document.querySelectorAll('section[id^="tab-"]'));
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
    if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (tabName === 'violations') loadViolationDetailsOnce();
}

// The heavy per-violation lists come from live Open Data calls, so they
// still load lazily — on first sight of the section instead of a tab click.
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
    if (!section) return;
    if (!('IntersectionObserver' in window)) {
        loadViolationDetailsOnce();
        return;
    }
    const once = new IntersectionObserver(entries => {
        if (entries.some(e => e.isIntersecting)) {
            loadViolationDetailsOnce();
            once.disconnect();
        }
    }, { rootMargin: '200px' });
    once.observe(section);
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

    const wideScreen = window.matchMedia('(min-width: 901px)');
    let userChangedState = false;

    const setExpanded = (expanded) => {
        groups.hidden = !expanded;
        button.setAttribute('aria-expanded', String(expanded));
        button.textContent = expanded ? 'Hide full record' : 'Show full record';
    };

    setExpanded(wideScreen.matches);
    button.addEventListener('click', () => {
        userChangedState = true;
        setExpanded(button.getAttribute('aria-expanded') !== 'true');
    });

    const buildingNav = document.querySelector('[data-tab="building"]');
    if (buildingNav) {
        buildingNav.addEventListener('click', () => {
            userChangedState = true;
            setExpanded(true);
        });
    }

    wideScreen.addEventListener('change', event => {
        if (!userChangedState) setExpanded(event.matches);
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
    
    if (building.is_cash_purchase !== null) {
        metrics.push({
            label: 'Purchase Type',
            value: building.is_cash_purchase ? 'Cash Purchase' : 'Financed',
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
            flag.textContent = 'Cash purchase';
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
            <h4>Outstanding Liabilities</h4>
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
                    <div class="sos-principal-name">${sos_data.principal_name}</div>
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
    html += '<h4>Current Owner Information</h4>';
    html += '<div class="current-owners">';
    
    const sourceInfo = {
        'acris': { label: 'ACRIS Latest Deed Grantee', icon: '' },
        'pluto': { label: 'NYC PLUTO Database', icon: '' },
        'rpad': { label: 'Historical RPAD Assessment (through FY2018/19)', icon: '' },
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
                    <div class="owner-source-name">${name}</div>
                    <span class="entity-kind-badge entity-${kind}">${kind === 'person' ? 'Person' : kind === 'organization' ? 'Organization' : kind === 'multiple' ? 'Multiple parties' : 'Unclassified'}</span>
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
    const safePermitLink = safeHttpHref(permit.link);
    
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
            ${safePermitLink ? `<a href="${escapeHtml(safePermitLink)}" target="_blank" rel="noopener noreferrer" class="btn-view-dob">View on DOB Website →</a>` : ''}
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
    if (!permitId) return '';
    
    // Create unique button ID
    const buttonId = `enrich-btn-${contactType}-${permitId}`;
    
    return `
        <div class="enrich-contact-section" id="enrich-section-${contactType}-${permitId}">
            <button type="button" class="enrich-contact-btn" id="${escapeHtml(buttonId)}"
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
        const usefulContacts = contacts.filter(c => c.phone || c.permit_count);
        
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
