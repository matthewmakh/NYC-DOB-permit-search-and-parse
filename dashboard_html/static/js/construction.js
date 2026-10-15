// Construction Intelligence Dashboard - Enhanced Version
// Features: Real-time filtering, search, quick filters, keyboard shortcuts, toast notifications

const AppState = {
    map: null,
    markersLayer: null,
    permits: [],
    contractors: [],
    stats: {},
    filters: {
        borough: '',
        jobType: '',
        days: '90',  // String to support 'all' option
        permitActivityMode: 'within',
        searchText: '',
        hasContact: false,
        minLeadScore: 0,
        sortBy: 'date'  // Add sort parameter
    },
    pagination: {
        currentPage: 1,
        perPage: 20,  // Default to 20 per page
        totalCount: 0,
        totalPages: 1
    }
};

function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return String(unsafe)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function showToast(message, type) {
    const colors = { success: '#10b981', error: '#ef4444', info: '#3b82f6' };
    const icons = { success: 'check-circle', error: 'exclamation-circle', info: 'info-circle' };
    
    const toast = document.createElement('div');
    toast.style.cssText = `position:fixed;top:20px;right:20px;padding:1rem 1.5rem;background:${colors[type]};color:var(--on-accent);border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.15);z-index:10000;display:flex;align-items:center;gap:0.75rem;font-weight:500;animation:slideIn 0.3s;`;
    toast.innerHTML = `<i class="fas fa-${icons[type]}"></i><span>${escapeHtml(message)}</span>`;
    
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

document.addEventListener('DOMContentLoaded', function() {
    console.log('🏗️ Construction Intelligence initializing...');

    const initialQuery = new URLSearchParams(window.location.search).get('q') || '';
    AppState.filters.searchText = initialQuery.trim();
    const searchInput = document.getElementById('searchInput');
    if (searchInput) searchInput.value = initialQuery;
    
    if (typeof L === 'undefined') {
        showToast('Map unavailable. You can still browse permit results.', 'error');
        document.getElementById('mapLoadingOverlay')?.classList.add('hidden');
    } else {
        initializeMap();
    }
    setupEventListeners();
    setupKeyboardShortcuts();
    loadAllData();
});

function initializeMap() {
    AppState.map = L.map('constructionMap').setView([40.7128, -74.0060], 11);
    
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap',
        maxZoom: 18
    }).addTo(AppState.map);
    
    AppState.markersLayer = L.markerClusterGroup({
        chunkedLoading: true,
        spiderfyOnMaxZoom: true,
        maxClusterRadius: 50
    });
    
    AppState.map.addLayer(AppState.markersLayer);
    console.log('✅ Map initialized');
}

function setupEventListeners() {
    const filterMap = {
        'filterBorough': 'borough',
        'filterJobType': 'jobType'
    };
    
    Object.entries(filterMap).forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', function() {
                AppState.filters[key] = this.value;
                applyFilters();
            });
        }
    });

    const activityMode = document.getElementById('filterActivityMode');
    activityMode?.addEventListener('change', function() {
        AppState.filters.permitActivityMode = this.value === 'inactive' ? 'inactive' : 'within';
        updateConstructionActivityHint();
        applyFilters();
    });

    const daysSelect = document.getElementById('filterDays');
    const customDays = document.getElementById('filterCustomDays');
    daysSelect?.addEventListener('change', function() {
        if (this.value === 'custom') {
            customDays.style.display = 'block';
            customDays.focus();
            updateConstructionActivityHint();
            return;
        }
        customDays.style.display = 'none';
        customDays.value = '';
        AppState.filters.days = this.value === 'all' ? 'all' : parseInt(this.value, 10);
        updateConstructionActivityHint();
        applyFilters();
    });
    customDays?.addEventListener('change', function() {
        const rawDays = parseInt(this.value, 10);
        if (!Number.isFinite(rawDays) || rawDays <= 0) return;
        const days = Math.min(3650, rawDays);
        this.value = days;
        AppState.filters.days = days;
        updateConstructionActivityHint();
        applyFilters();
    });
    updateConstructionActivityHint();
    
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        let timeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                AppState.filters.searchText = this.value.trim();
                AppState.pagination.currentPage = 1;
                loadPermits();
            }, 300);
        });
    }
    
    setupQuickFilters();
}

function setupQuickFilters() {
    const quickFilters = {
        'quickFilterHotLeads': () => {
            AppState.filters.minLeadScore = AppState.filters.minLeadScore === 70 ? 0 : 70;
            AppState.pagination.currentPage = 1;
            loadPermits();
        },
        'quickFilterRecent': () => {
            AppState.filters.permitActivityMode = 'within';
            document.getElementById('filterActivityMode').value = 'within';
            AppState.filters.days = AppState.filters.days === '7' || AppState.filters.days === 7 ? '90' : '7';
            document.getElementById('filterDays').value = AppState.filters.days;
            document.getElementById('filterCustomDays').style.display = 'none';
            updateConstructionActivityHint();
            applyFilters();
        },
        'quickFilterContacts': () => {
            AppState.filters.hasContact = !AppState.filters.hasContact;
            AppState.pagination.currentPage = 1;
            loadPermits();
        }
    };
    
    Object.entries(quickFilters).forEach(([id, handler]) => {
        const btn = document.getElementById(id);
        if (btn) {
            btn.addEventListener('click', function() {
                this.classList.toggle('active');
                handler();
            });
        }
    });
}

function setupKeyboardShortcuts() {
    document.addEventListener('keydown', function(e) {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            if (e.key === 'Escape') e.target.blur();
            return;
        }
        
        const shortcuts = {
            '/': () => document.getElementById('searchInput')?.focus(),
            'f': () => document.querySelector('.filter-select')?.focus(),
            'F': () => document.querySelector('.filter-select')?.focus(),
            'e': exportPermits,
            'E': exportPermits
        };
        
        if (shortcuts[e.key]) {
            e.preventDefault();
            shortcuts[e.key]();
        }
    });
}

function loadAllData() {
    loadStats();
    loadPermits();
    loadMapData();
}

function updateConstructionActivityHint() {
    const hint = document.getElementById('constructionActivityHint');
    if (!hint) return;
    const inactive = AppState.filters.permitActivityMode === 'inactive';
    const dayChoice = document.getElementById('filterDays')?.value;
    const customDays = document.getElementById('filterCustomDays')?.value;
    if (dayChoice === 'all') {
        hint.textContent = 'Includes dated and undated records. Future issue dates are excluded.';
    } else if (dayChoice === 'custom' && !customDays) {
        hint.textContent = 'Enter a custom number of days to apply this timing rule.';
    } else {
        hint.textContent = inactive
            ? 'Shows individual permit records issued before the selected cutoff.'
            : 'Shows individual permit records issued inside the selected window.';
    }
    const label = dayChoice === 'all' ? 'All permits' : (inactive ? 'Older permits' : 'Recent permits');
    setTextById('statPermitLabel', label);
    setTextById('permitsListTitle', label);
}

function appendPermitTimeParams(params) {
    params.set('days', AppState.filters.days);
    params.set('permit_activity_mode', AppState.filters.permitActivityMode);
    return params;
}

function loadStats() {
    const params = appendPermitTimeParams(new URLSearchParams());
    fetch(`/api/construction/stats?${params}`)
        .then(r => r.json())
        .then(data => {
            if (!data.success) throw new Error('Statistics request failed');
            if (data.success) {
                AppState.stats = data.stats;
                displayStats(data.stats);
            }
        })
        .catch(err => {
            console.error('Stats error:', err);
            showToast('Failed to load statistics', 'error');
        });
}

function displayStats(stats) {
    setTextById('statActivePermits', stats.total_permits.toLocaleString());
    setTextById('statTotalValue', stats.total_value > 0 ? `$${(stats.total_value / 1000000).toFixed(1)}M` : '$0');
    setTextById('statTopBorough', stats.boroughs?.[0]?.borough || '-');
    setTextById('statTrendingType', stats.job_types?.[0]?.job_type || '-');
    
    const contactRate = stats.total_permits > 0 ? Math.round((stats.with_contacts / stats.total_permits) * 100) : 0;
    
    const quickStatsHTML = `
        <div style="background:var(--accent);color:var(--on-accent);padding:1.5rem;border-radius:12px;margin-bottom:1rem;">
            <div style="display:flex;align-items:center;gap:1rem;">
                <div style="font-size:2rem;"><i class="fas fa-phone"></i></div>
                <div>
                    <div style="font-size:2rem;font-weight:700;">${stats.with_contacts}</div>
                    <div style="opacity:0.9;">With Contacts</div>
                </div>
            </div>
        </div>
        <div style="background:var(--amber);color:var(--on-accent);padding:1.5rem;border-radius:12px;margin-bottom:1rem;">
            <div style="display:flex;align-items:center;gap:1rem;">
                <div style="font-size:2rem;"><i class="fas fa-fire"></i></div>
                <div>
                    <div style="font-size:2rem;font-weight:700;">${stats.hot_leads}</div>
                    <div style="opacity:0.9;">Hot Leads</div>
                </div>
            </div>
        </div>
        <div style="background:var(--green);color:var(--on-accent);padding:1.5rem;border-radius:12px;">
            <div style="display:flex;align-items:center;gap:1rem;">
                <div style="font-size:2rem;"><i class="fas fa-percentage"></i></div>
                <div>
                    <div style="font-size:2rem;font-weight:700;">${contactRate}%</div>
                    <div style="opacity:0.9;">Contact Rate</div>
                </div>
            </div>
        </div>
    `;
    
    setHtmlById('quickStats', quickStatsHTML);
    displayJobTypesChart(stats.job_types || []);
    displayBoroughsChart(stats.boroughs || []);
    if (document.getElementById('trendsTab')?.classList.contains('active')) displayPermitTrends(stats);
}

let permitTrendChart = null;
function displayPermitTrends(stats) {
    const points = stats.timeline || [];
    const status = document.getElementById('trendsStatus');
    const canvas = document.getElementById('trendsChart');
    if (!status || !canvas) return;
    const monthly = stats.timeline_unit === 'month';
    const label = value => new Date(`${value}T12:00:00Z`).toLocaleDateString('en-US',
        monthly ? {month:'short', year:'numeric', timeZone:'UTC'} : {month:'short', day:'numeric', timeZone:'UTC'});
    document.getElementById('trendsRows').innerHTML = points.map(point =>
        `<tr><th scope="row">${escapeHtml(label(point.date))}</th><td>${Number(point.count).toLocaleString()}</td></tr>`).join('');
    status.textContent = points.length
        ? `Permits issued per ${monthly ? 'month' : 'day'} in the selected time window${points.length === 120 ? ' · latest 120 periods' : ''}.`
        : 'No dated permit activity in the selected time window.';
    if (permitTrendChart) { permitTrendChart.destroy(); permitTrendChart = null; }
    canvas.parentElement.hidden = !points.length;
    if (!points.length) return;
    if (typeof Chart === 'undefined') {
        canvas.parentElement.hidden = true;
        document.getElementById('trendsData').open = true;
        status.textContent += ' Chart unavailable; activity is listed in the table below.';
        return;
    }
    const theme = getComputedStyle(document.documentElement);
    permitTrendChart = new Chart(canvas, {
        type: 'bar',
        data: {labels: points.map(point => label(point.date)), datasets: [{
            label: 'Permits issued', data: points.map(point => point.count),
            backgroundColor: theme.getPropertyValue('--accent').trim(), borderRadius: 3
        }]},
        options: {responsive:true, maintainAspectRatio:false, animation:false,
            plugins:{legend:{display:false}},
            scales:{x:{ticks:{maxTicksLimit:8,color:theme.getPropertyValue('--ink-2').trim()},grid:{display:false}},
                    y:{beginAtZero:true,ticks:{precision:0,color:theme.getPropertyValue('--ink-2').trim()},grid:{color:theme.getPropertyValue('--line').trim()}}}}
    });
}
document.addEventListener('themechange', () => {
    if (document.getElementById('trendsTab')?.classList.contains('active')) displayPermitTrends(AppState.stats);
});

function displayJobTypesChart(jobTypes) {
    if (!jobTypes.length) return;
    
    const maxCount = Math.max(...jobTypes.map(jt => jt.count));
    const colors = ['#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#06b6d4'];
    
    const html = jobTypes.slice(0, 6).map((jt, i) => {
        const width = (jt.count / maxCount) * 100;
        return `
            <div style="margin-bottom:1rem;">
                <div style="display:flex;justify-content:space-between;margin-bottom:0.5rem;font-size:0.875rem;">
                    <span style="font-weight:600;">${escapeHtml(jt.job_type)}</span>
                    <span style="color:var(--ink-2);">${jt.count}</span>
                </div>
                <div style="background:var(--line);border-radius:4px;height:8px;overflow:hidden;">
                    <div style="background:${colors[i%colors.length]};width:${width}%;height:100%;transition:width 0.3s;"></div>
                </div>
            </div>
        `;
    }).join('');
    
    setHtmlById('typesChart', html);
}

function displayBoroughsChart(boroughs) {
    if (!boroughs.length) return;
    
    const maxCount = Math.max(...boroughs.map(b => b.count));
    const colors = ['#ef4444', '#f59e0b', '#10b981', '#3b82f6', '#8b5cf6'];
    
    const html = boroughs.map((b, i) => {
        const width = (b.count / maxCount) * 100;
        return `
            <div style="margin-bottom:1rem;">
                <div style="display:flex;justify-content:space-between;margin-bottom:0.5rem;font-size:0.875rem;">
                    <span style="font-weight:600;">${escapeHtml(b.borough)}</span>
                    <span style="color:var(--ink-2);">${b.count}</span>
                </div>
                <div style="background:var(--line);border-radius:4px;height:8px;overflow:hidden;">
                    <div style="background:${colors[i%colors.length]};width:${width}%;height:100%;transition:width 0.3s;"></div>
                </div>
            </div>
        `;
    }).join('');
    
    setHtmlById('boroughsChart', html);
}

let permitRequest = 0;
function loadPermits() {
    const requestId = ++permitRequest;
    const offset = (AppState.pagination.currentPage - 1) * AppState.pagination.perPage;
    const params = appendPermitTimeParams(new URLSearchParams({
        limit: AppState.pagination.perPage,
        offset: offset,
        sort: AppState.filters.sortBy
    }));
    if (AppState.filters.borough) params.append('borough', AppState.filters.borough);
    if (AppState.filters.jobType) params.append('job_type', AppState.filters.jobType);
    if (AppState.filters.searchText) params.append('q', AppState.filters.searchText);
    if (AppState.filters.hasContact) params.set('has_contact', 'true');
    if (AppState.filters.minLeadScore > 0) params.set('min_score', AppState.filters.minLeadScore);
    
    showLoadingSkeleton('permitsList', 5);
    
    fetch(`/api/construction/permits?${params}`)
        .then(r => r.json())
        .then(data => {
            if (requestId !== permitRequest) return;
            if (!data.success) throw new Error('Permit request failed');
            if (data.success) {
                AppState.permits = data.permits;
                AppState.pagination.totalCount = data.total_count;
                AppState.pagination.totalPages = data.pagination.total_pages;
                displayPermitsList(AppState.permits);
                updatePaginationUI();
                console.log(`✅ Loaded ${data.permits.length} permits (Page ${AppState.pagination.currentPage}/${AppState.pagination.totalPages})`);
            }
        })
        .catch(err => {
            if (requestId !== permitRequest) return;
            console.error('Permits error:', err);
            const list = document.getElementById('permitsList');
            list.innerHTML = '<div class="empty-state" role="alert"><p>Could not load permits. Your filters are still here.</p><button type="button" class="btn btn-secondary" id="retryPermits">Retry</button></div>';
            document.getElementById('retryPermits').addEventListener('click', loadPermits);
        });
}

function applyFiltersLocal() {
    // Client-side search filtering only (for already loaded permits)
    let filtered = [...AppState.permits];
    
    if (AppState.filters.searchText) {
        const search = AppState.filters.searchText;
        filtered = filtered.filter(p =>
            (p.address || '').toLowerCase().includes(search) ||
            (p.permit_no || '').toLowerCase().includes(search) ||
            (p.applicant || '').toLowerCase().includes(search) ||
            (p.permittee_business_name || '').toLowerCase().includes(search)
        );
    }
    
    if (AppState.filters.minLeadScore > 0) {
        filtered = filtered.filter(p => (p.lead_score || 0) >= AppState.filters.minLeadScore);
    }
    
    if (AppState.filters.hasContact) {
        filtered = filtered.filter(p => (p.contact_count || 0) > 0);
    }
    
    displayPermitsList(filtered);
    setTextById('permitsListCount', `${filtered.length} of ${AppState.pagination.totalCount} permits`);
}

function applyFilters() {
    AppState.pagination.currentPage = 1; // Reset to first page
    loadStats();
    loadPermits();
    loadMapData();
}

function displayPermitsList(permits) {
    const listEl = document.getElementById('permitsList');
    if (!listEl) return;
    
    if (permits.length === 0) {
        listEl.innerHTML = `
            <div style="text-align:center;padding:3rem;color:var(--ink-2);">
                <i class="fas fa-search" style="font-size:3rem;margin-bottom:1rem;opacity:0.3;"></i>
                <div style="font-size:1.125rem;font-weight:600;">No permits found</div>
                <div style="margin-top:0.5rem;">Try adjusting your filters</div>
            </div>
        `;
        return;
    }
    
    const html = permits.map(p => {
        const leadScore = p.lead_score || 0;
        const contactCount = p.contact_count || 0;
        const jobType = p.job_type || 'OTHER';
        const issueDate = p.issue_date ? new Date(p.issue_date).toLocaleDateString('en-US', {timeZone: 'UTC'}) : 'N/A';
        
        const leadBadge = leadScore >= 70 ?
            '<span style="background:#10b981;color:var(--on-accent);padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;">🔥 HOT</span>' :
            leadScore >= 50 ?
            '<span style="background:#f59e0b;color:var(--on-accent);padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;">⚡ WARM</span>' :
            '<span style="background:var(--ink-2);color:var(--on-accent);padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;">❄️ COLD</span>';
        
        return `
            <a class="permit-result-card" href="/permit/${encodeURIComponent(p.id)}" target="_blank" rel="noopener" style="display:block;text-decoration:none;color:inherit;background:var(--surface);border:1px solid var(--line);border-left:4px solid ${getJobTypeColor(jobType)};border-radius:12px;padding:1.5rem;margin-bottom:1rem;">
                <div style="display:flex;justify-content:space-between;margin-bottom:1rem;">
                    <div style="flex:1;">
                        <div style="font-size:1.125rem;font-weight:700;color:var(--ink);margin-bottom:0.25rem;">
                            ${escapeHtml(jobType)} - ${escapeHtml(p.address || 'No address')}
                        </div>
                        <div style="color:var(--ink-2);font-size:0.875rem;">
                            <i class="fas fa-map-marker-alt"></i> ${escapeHtml(p.borough || 'Unknown')} • 
                            <i class="fas fa-calendar"></i> ${issueDate}
                        </div>
                    </div>
                    <div style="display:flex;gap:0.5rem;align-items:center;">
                        ${leadBadge}
                        <span style="background:var(--surface-2);padding:0.5rem 0.75rem;border-radius:8px;font-weight:600;color:var(--ink-2);">
                            ${jobType}
                        </span>
                    </div>
                </div>
                
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;padding:1rem;background:var(--surface-2);border-radius:8px;">
                    <div>
                        <div style="font-size:0.75rem;color:var(--ink-2);text-transform:uppercase;margin-bottom:0.25rem;">Permit #</div>
                        <div style="font-weight:600;color:var(--ink);">${escapeHtml(p.permit_no || 'N/A')}</div>
                    </div>
                    <div>
                        <div style="font-size:0.75rem;color:var(--ink-2);text-transform:uppercase;margin-bottom:0.25rem;">Contacts</div>
                        <div style="font-weight:600;color:${contactCount > 0 ? '#10b981' : 'var(--ink-2)'};">
                            <i class="fas fa-phone"></i> ${contactCount}
                        </div>
                    </div>
                    <div>
                        <div style="font-size:0.75rem;color:var(--ink-2);text-transform:uppercase;margin-bottom:0.25rem;">Lead Score</div>
                        <div style="font-weight:600;color:${leadScore >= 70 ? '#10b981' : leadScore >= 50 ? '#f59e0b' : 'var(--ink-2)'};">
                            ⭐ ${leadScore}/100
                        </div>
                    </div>
                    ${p.applicant ? `
                        <div>
                            <div style="font-size:0.75rem;color:var(--ink-2);text-transform:uppercase;margin-bottom:0.25rem;">Applicant</div>
                            <div style="font-weight:600;color:var(--ink);font-size:0.875rem;">${escapeHtml(p.applicant)}</div>
                        </div>
                    ` : ''}
                </div>
            </a>
        `;
    }).join('');
    
    listEl.innerHTML = html;
}

function loadMapData() {
    // Show loading overlay
    const loadingOverlay = document.getElementById('mapLoadingOverlay');
    const loadingCount = document.getElementById('loadingCount');
    if (loadingOverlay) {
        loadingOverlay.classList.remove('hidden');
        loadingCount.textContent = 'Fetching permit data...';
    }
    
    const params = appendPermitTimeParams(new URLSearchParams());
    if (AppState.filters.borough) params.append('borough', AppState.filters.borough);
    if (AppState.filters.jobType) params.append('job_type', AppState.filters.jobType);
    
    fetch(`/api/construction/map-data?${params}`)
        .then(r => r.json())
        .then(data => {
            if (!data.success) throw new Error('Map request failed');
            if (data.success) {
                if (loadingCount) {
                    loadingCount.textContent = `Loading ${data.locations.length} markers...`;
                }
                displayMapMarkers(data.locations);
                console.log(`✅ Loaded ${data.locations.length} map markers`);
                
                // Hide loading overlay after markers are displayed
                setTimeout(() => {
                    if (loadingOverlay) {
                        loadingOverlay.classList.add('hidden');
                    }
                }, 300);
            }
        })
        .catch(err => {
            console.error('Map data error:', err);
            showToast('Failed to load map data', 'error');
            // Hide loading overlay on error
            if (loadingOverlay) {
                loadingOverlay.classList.add('hidden');
            }
        });
}

function displayMapMarkers(locations) {
    if (!AppState.markersLayer) return;
    
    AppState.markersLayer.clearLayers();
    
    locations.forEach(loc => {
        if (loc.latitude && loc.longitude) {
            const color = getJobTypeColor(loc.job_type);
            const leadScore = loc.lead_score || 0;
            
            const marker = L.circleMarker([loc.latitude, loc.longitude], {
                radius: leadScore >= 70 ? 10 : 8,
                fillColor: color,
                color: '#fff',
                weight: 2,
                fillOpacity: 0.85
            });
            
            const issueDate = loc.issue_date ? new Date(loc.issue_date).toLocaleDateString('en-US', {timeZone: 'UTC'}) : 'N/A';
            const leadBadge = leadScore >= 70 ? '🔥 HOT LEAD' : leadScore >= 50 ? '⚡ WARM' : '';
            
            marker.bindPopup(`
                <div style="min-width:280px;font-family:system-ui;">
                    <div style="font-size:1.125rem;font-weight:700;margin-bottom:0.5rem;color:var(--ink);">
                        ${escapeHtml(loc.job_type)} - ${escapeHtml(loc.address || 'No address')}
                    </div>
                    ${leadBadge ? `<div style="background:${leadScore >= 70 ? '#10b981' : '#f59e0b'};color:var(--on-accent);display:inline-block;padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;margin-bottom:0.75rem;">${leadBadge}</div>` : ''}
                    <div style="color:var(--ink-2);font-size:0.875rem;margin-bottom:0.75rem;">
                        <i class="fas fa-map-marker-alt"></i> ${escapeHtml(loc.borough || 'Unknown')} • ${issueDate}
                    </div>
                    <div style="background:var(--surface-2);padding:0.75rem;border-radius:6px;margin-bottom:0.75rem;">
                        <div style="font-size:0.75rem;color:var(--ink-2);margin-bottom:0.25rem;">PERMIT NUMBER</div>
                        <div style="font-weight:600;color:var(--ink);">${escapeHtml(loc.permit_no || 'N/A')}</div>
                    </div>
                    <div style="display:flex;gap:0.75rem;margin-bottom:0.75rem;">
                        <div style="flex:1;background:var(--surface-2);padding:0.5rem;border-radius:6px;text-align:center;">
                            <div style="font-size:0.75rem;color:var(--ink-2);">Contacts</div>
                            <div style="font-weight:700;color:${(loc.contact_count || 0) > 0 ? '#10b981' : 'var(--ink-2)'};">
                                ${loc.contact_count || 0}
                            </div>
                        </div>
                        <div style="flex:1;background:var(--surface-2);padding:0.5rem;border-radius:6px;text-align:center;">
                            <div style="font-size:0.75rem;color:var(--ink-2);">Score</div>
                            <div style="font-weight:700;color:${leadScore >= 70 ? '#10b981' : leadScore >= 50 ? '#f59e0b' : 'var(--ink-2)'};">
                                ${leadScore}
                            </div>
                        </div>
                    </div>
                    <a href="/permit/${loc.id}" target="_blank" style="display:block;background:var(--accent);color:var(--on-accent);text-align:center;padding:0.75rem;border-radius:6px;text-decoration:none;font-weight:600;">
                        View Full Details →
                    </a>
                </div>
            `);
            
            AppState.markersLayer.addLayer(marker);
        }
    });
}

function loadContractors() {
    const params = appendPermitTimeParams(new URLSearchParams({ limit: 20 }));
    fetch(`/api/construction/contractors?${params}`)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                AppState.contractors = data.contractors;
                displayContractors(data.contractors);
            }
        })
        .catch(err => {
            console.error('Contractors error:', err);
            showToast('Failed to load contractors', 'error');
        });
}

function displayContractors(contractors) {
    const listEl = document.getElementById('contractorsList');
    if (!listEl) return;
    
    if (contractors.length === 0) {
        listEl.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--ink-2);">No contractors found</div>';
        return;
    }
    
    const html = contractors.map((c, i) => `
        <a class="contractor-card permit-contractor-link" href="/contractor/${encodeURIComponent(c.contractor_name)}">
            <span class="contractor-rank">${i + 1}</span>
            <span class="permit-contractor-copy">
                <strong>${escapeHtml(c.contractor_name)}</strong>
                <span>${c.permit_count ?? c.total_jobs ?? 0} active permits${c.job_types ? ` · ${escapeHtml(c.job_types)}` : ''}</span>
            </span>
            <i class="fas fa-arrow-right" aria-hidden="true"></i>
        </a>
    `).join('');

    listEl.innerHTML = html;
}

function switchTab(tabName, trigger) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    trigger?.classList.add('active');
    
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    const tabEl = document.getElementById(`${tabName}Tab`);
    if (tabEl) tabEl.classList.add('active');
    
    if (tabName === 'contractors') {
        loadContractors();
    }
    if (tabName === 'trends') displayPermitTrends(AppState.stats);
}

function sortPermitsList() {
    const sortBy = document.getElementById('sortBy')?.value;
    if (!sortBy) return;
    
    // Map frontend sort values to backend API values
    const sortMap = {
        'date_desc': 'date',
        'date_asc': 'date_asc',
        'score': 'score',
        'contacts': 'contacts',
        'address': 'address',
        'applicant': 'applicant',
        'type': 'type'
    };
    
    AppState.filters.sortBy = sortMap[sortBy] || 'date';
    AppState.pagination.currentPage = 1; // Reset to first page when sorting
    loadPermits();
}

function clearFilters() {
    document.getElementById('sortBy').value = 'date_desc';
    document.getElementById('filterBorough').value = '';
    document.getElementById('filterJobType').value = '';
    document.getElementById('filterDays').value = '90';
    document.getElementById('filterActivityMode').value = 'within';
    document.getElementById('filterCustomDays').value = '';
    document.getElementById('filterCustomDays').style.display = 'none';
    document.getElementById('searchInput').value = '';
    
    document.querySelectorAll('.quick-filter-btn').forEach(btn => btn.classList.remove('active'));
    
    AppState.filters = {
        borough: '',
        jobType: '',
        days: 90,
        permitActivityMode: 'within',
        searchText: '',
        hasContact: false,
        minLeadScore: 0,
        sortBy: 'date'
    };
    updateConstructionActivityHint();
    
    showToast('Filters cleared', 'info');
    applyFilters();
}

function exportPermits() {
    const params = appendPermitTimeParams(new URLSearchParams());
    if (AppState.filters.borough) params.append('borough', AppState.filters.borough);
    if (AppState.filters.jobType) params.append('job_type', AppState.filters.jobType);
    
    showToast('Preparing export...', 'info');
    window.location.href = `/api/construction/export?${params}`;
    
    setTimeout(() => showToast('Export started', 'success'), 1000);
}

function getJobTypeColor(jobType) {
    const colors = {
        'NB': '#ef4444',
        'A1': '#f97316',
        'A2': '#eab308',
        'A3': '#84cc16',
        'DM': '#3b82f6',
        'SG': '#8b5cf6'
    };
    return colors[jobType] || 'var(--ink-2)';
}

function setTextById(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function setHtmlById(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
}

function showLoadingSkeleton(containerId, count) {
    const container = document.getElementById(containerId);
    if (!container) return;
    
    let html = '';
    for (let i = 0; i < count; i++) {
        html += `
            <div style="background:var(--surface-2);border-radius:12px;padding:1.5rem;margin-bottom:1rem;animation:pulse 1.5s infinite;">
                <div style="background:var(--line);height:20px;width:70%;border-radius:4px;margin-bottom:0.75rem;"></div>
                <div style="background:var(--line);height:16px;width:40%;border-radius:4px;margin-bottom:1rem;"></div>
                <div style="background:var(--line);height:60px;width:100%;border-radius:8px;"></div>
            </div>
        `;
    }
    container.innerHTML = html;
}

const style = document.createElement('style');
style.textContent = '@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}';
document.head.appendChild(style);

// Pagination functions
function updatePaginationUI() {
    const start = (AppState.pagination.currentPage - 1) * AppState.pagination.perPage + 1;
    const end = Math.min(AppState.pagination.currentPage * AppState.pagination.perPage, AppState.pagination.totalCount);
    
    setTextById('currentPage', AppState.pagination.currentPage);
    setTextById('totalPages', AppState.pagination.totalPages);
    setTextById('showingRange', `${start}-${end}`);
    setTextById('totalCount', AppState.pagination.totalCount);
    setTextById('permitsListCount', `${AppState.pagination.totalCount} permits total`);
    
    // Show/hide pagination controls
    const paginationEl = document.getElementById('paginationControls');
    if (paginationEl) {
        paginationEl.style.display = AppState.pagination.totalPages > 1 ? 'block' : 'none';
    }
}

function changePerPage() {
    const perPageSelect = document.getElementById('perPage');
    AppState.pagination.perPage = parseInt(perPageSelect.value);
    AppState.pagination.currentPage = 1;
    loadPermits();
}

function goToPage(page) {
    if (page >= 1 && page <= AppState.pagination.totalPages) {
        AppState.pagination.currentPage = page;
        loadPermits();
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }
}

function previousPage() {
    if (AppState.pagination.currentPage > 1) {
        goToPage(AppState.pagination.currentPage - 1);
    }
}

function nextPage() {
    if (AppState.pagination.currentPage < AppState.pagination.totalPages) {
        goToPage(AppState.pagination.currentPage + 1);
    }
}

function goToLastPage() {
    goToPage(AppState.pagination.totalPages);
}

console.log('🎨 Enhanced Construction Intelligence ready');
