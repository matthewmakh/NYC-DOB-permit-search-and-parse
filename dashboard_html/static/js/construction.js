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
    toast.style.cssText = `position:fixed;top:20px;right:20px;padding:1rem 1.5rem;background:${colors[type]};color:white;border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.15);z-index:10000;display:flex;align-items:center;gap:0.75rem;font-weight:500;animation:slideIn 0.3s;`;
    toast.innerHTML = `<i class="fas fa-${icons[type]}"></i><span>${escapeHtml(message)}</span>`;
    
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
}

document.addEventListener('DOMContentLoaded', function() {
    console.log('🏗️ Construction Intelligence initializing...');
    
    if (typeof L === 'undefined') {
        showToast('Map library failed to load', 'error');
        return;
    }
    
    initializeMap();
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
        'filterJobType': 'jobType',
        'filterDays': 'days'
    };
    
    Object.entries(filterMap).forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', function() {
                if (key === 'days') {
                    // Handle both numeric and 'all' values
                    AppState.filters[key] = this.value === 'all' ? 'all' : parseInt(this.value);
                } else {
                    AppState.filters[key] = this.value;
                }
                applyFilters();
            });
        }
    });
    
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        let timeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                AppState.filters.searchText = this.value.toLowerCase();
                applyFiltersLocal();
            }, 300);
        });
    }
    
    setupQuickFilters();
}

function setupQuickFilters() {
    const quickFilters = {
        'quickFilterHotLeads': () => {
            AppState.filters.minLeadScore = AppState.filters.minLeadScore === 70 ? 0 : 70;
            applyFiltersLocal();
        },
        'quickFilterRecent': () => {
            AppState.filters.days = AppState.filters.days === '7' || AppState.filters.days === 7 ? '90' : '7';
            document.getElementById('filterDays').value = AppState.filters.days;
            applyFilters();
        },
        'quickFilterContacts': () => {
            AppState.filters.hasContact = !AppState.filters.hasContact;
            applyFiltersLocal();
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

function loadStats() {
    fetch(`/api/construction/stats?days=${AppState.filters.days}`)
        .then(r => r.json())
        .then(data => {
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
        <div style="background:linear-gradient(135deg,#3b82f6,#2563eb);color:white;padding:1.5rem;border-radius:12px;margin-bottom:1rem;">
            <div style="display:flex;align-items:center;gap:1rem;">
                <div style="font-size:2rem;"><i class="fas fa-phone"></i></div>
                <div>
                    <div style="font-size:2rem;font-weight:700;">${stats.with_contacts}</div>
                    <div style="opacity:0.9;">With Contacts</div>
                </div>
            </div>
        </div>
        <div style="background:linear-gradient(135deg,#f59e0b,#d97706);color:white;padding:1.5rem;border-radius:12px;margin-bottom:1rem;">
            <div style="display:flex;align-items:center;gap:1rem;">
                <div style="font-size:2rem;"><i class="fas fa-fire"></i></div>
                <div>
                    <div style="font-size:2rem;font-weight:700;">${stats.hot_leads}</div>
                    <div style="opacity:0.9;">Hot Leads</div>
                </div>
            </div>
        </div>
        <div style="background:linear-gradient(135deg,#10b981,#059669);color:white;padding:1.5rem;border-radius:12px;">
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
}

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
                    <span style="color:#6b7280;">${jt.count}</span>
                </div>
                <div style="background:#e5e7eb;border-radius:4px;height:8px;overflow:hidden;">
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
                    <span style="color:#6b7280;">${b.count}</span>
                </div>
                <div style="background:#e5e7eb;border-radius:4px;height:8px;overflow:hidden;">
                    <div style="background:${colors[i%colors.length]};width:${width}%;height:100%;transition:width 0.3s;"></div>
                </div>
            </div>
        `;
    }).join('');
    
    setHtmlById('boroughsChart', html);
}

function loadPermits() {
    const offset = (AppState.pagination.currentPage - 1) * AppState.pagination.perPage;
    const params = new URLSearchParams({ 
        days: AppState.filters.days, 
        limit: AppState.pagination.perPage,
        offset: offset,
        sort: AppState.filters.sortBy
    });
    if (AppState.filters.borough) params.append('borough', AppState.filters.borough);
    if (AppState.filters.jobType) params.append('job_type', AppState.filters.jobType);
    
    showLoadingSkeleton('permitsList', 5);
    
    fetch(`/api/construction/permits?${params}`)
        .then(r => r.json())
        .then(data => {
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
            console.error('Permits error:', err);
            showToast('Failed to load permits', 'error');
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
            <div style="text-align:center;padding:3rem;color:#6b7280;">
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
        const issueDate = p.issue_date ? new Date(p.issue_date).toLocaleDateString() : 'N/A';
        
        const leadBadge = leadScore >= 70 ?
            '<span style="background:#10b981;color:white;padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;">🔥 HOT</span>' :
            leadScore >= 50 ?
            '<span style="background:#f59e0b;color:white;padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;">⚡ WARM</span>' :
            '<span style="background:#6b7280;color:white;padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;">❄️ COLD</span>';
        
        return `
            <div onclick="window.open('/permit/${p.id}', '_blank')" style="cursor:pointer;background:white;border:1px solid #e5e7eb;border-left:4px solid ${getJobTypeColor(jobType)};border-radius:12px;padding:1.5rem;margin-bottom:1rem;transition:all 0.2s;" onmouseover="this.style.transform='translateY(-2px)';this.style.boxShadow='0 8px 24px rgba(0,0,0,0.12)'" onmouseout="this.style.transform='translateY(0)';this.style.boxShadow='none'">
                <div style="display:flex;justify-content:space-between;margin-bottom:1rem;">
                    <div style="flex:1;">
                        <div style="font-size:1.125rem;font-weight:700;color:#111827;margin-bottom:0.25rem;">
                            ${escapeHtml(jobType)} - ${escapeHtml(p.address || 'No address')}
                        </div>
                        <div style="color:#6b7280;font-size:0.875rem;">
                            <i class="fas fa-map-marker-alt"></i> ${escapeHtml(p.borough || 'Unknown')} • 
                            <i class="fas fa-calendar"></i> ${issueDate}
                        </div>
                    </div>
                    <div style="display:flex;gap:0.5rem;align-items:center;">
                        ${leadBadge}
                        <span style="background:#f3f4f6;padding:0.5rem 0.75rem;border-radius:8px;font-weight:600;color:#374151;">
                            ${jobType}
                        </span>
                    </div>
                </div>
                
                <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1rem;padding:1rem;background:#f9fafb;border-radius:8px;">
                    <div>
                        <div style="font-size:0.75rem;color:#6b7280;text-transform:uppercase;margin-bottom:0.25rem;">Permit #</div>
                        <div style="font-weight:600;color:#111827;">${escapeHtml(p.permit_no || 'N/A')}</div>
                    </div>
                    <div>
                        <div style="font-size:0.75rem;color:#6b7280;text-transform:uppercase;margin-bottom:0.25rem;">Contacts</div>
                        <div style="font-weight:600;color:${contactCount > 0 ? '#10b981' : '#6b7280'};">
                            <i class="fas fa-phone"></i> ${contactCount}
                        </div>
                    </div>
                    <div>
                        <div style="font-size:0.75rem;color:#6b7280;text-transform:uppercase;margin-bottom:0.25rem;">Lead Score</div>
                        <div style="font-weight:600;color:${leadScore >= 70 ? '#10b981' : leadScore >= 50 ? '#f59e0b' : '#6b7280'};">
                            ⭐ ${leadScore}/100
                        </div>
                    </div>
                    ${p.applicant ? `
                        <div>
                            <div style="font-size:0.75rem;color:#6b7280;text-transform:uppercase;margin-bottom:0.25rem;">Applicant</div>
                            <div style="font-weight:600;color:#111827;font-size:0.875rem;">${escapeHtml(p.applicant)}</div>
                        </div>
                    ` : ''}
                </div>
            </div>
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
    
    const params = new URLSearchParams({ days: AppState.filters.days });
    if (AppState.filters.borough) params.append('borough', AppState.filters.borough);
    if (AppState.filters.jobType) params.append('job_type', AppState.filters.jobType);
    
    fetch(`/api/construction/map-data?${params}`)
        .then(r => r.json())
        .then(data => {
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
            
            const issueDate = loc.issue_date ? new Date(loc.issue_date).toLocaleDateString() : 'N/A';
            const leadBadge = leadScore >= 70 ? '🔥 HOT LEAD' : leadScore >= 50 ? '⚡ WARM' : '';
            
            marker.bindPopup(`
                <div style="min-width:280px;font-family:system-ui;">
                    <div style="font-size:1.125rem;font-weight:700;margin-bottom:0.5rem;color:#111827;">
                        ${escapeHtml(loc.job_type)} - ${escapeHtml(loc.address || 'No address')}
                    </div>
                    ${leadBadge ? `<div style="background:${leadScore >= 70 ? '#10b981' : '#f59e0b'};color:white;display:inline-block;padding:0.25rem 0.75rem;border-radius:12px;font-size:0.75rem;font-weight:600;margin-bottom:0.75rem;">${leadBadge}</div>` : ''}
                    <div style="color:#6b7280;font-size:0.875rem;margin-bottom:0.75rem;">
                        <i class="fas fa-map-marker-alt"></i> ${escapeHtml(loc.borough || 'Unknown')} • ${issueDate}
                    </div>
                    <div style="background:#f9fafb;padding:0.75rem;border-radius:6px;margin-bottom:0.75rem;">
                        <div style="font-size:0.75rem;color:#6b7280;margin-bottom:0.25rem;">PERMIT NUMBER</div>
                        <div style="font-weight:600;color:#111827;">${escapeHtml(loc.permit_no || 'N/A')}</div>
                    </div>
                    <div style="display:flex;gap:0.75rem;margin-bottom:0.75rem;">
                        <div style="flex:1;background:#f9fafb;padding:0.5rem;border-radius:6px;text-align:center;">
                            <div style="font-size:0.75rem;color:#6b7280;">Contacts</div>
                            <div style="font-weight:700;color:${(loc.contact_count || 0) > 0 ? '#10b981' : '#6b7280'};">
                                ${loc.contact_count || 0}
                            </div>
                        </div>
                        <div style="flex:1;background:#f9fafb;padding:0.5rem;border-radius:6px;text-align:center;">
                            <div style="font-size:0.75rem;color:#6b7280;">Score</div>
                            <div style="font-weight:700;color:${leadScore >= 70 ? '#10b981' : leadScore >= 50 ? '#f59e0b' : '#6b7280'};">
                                ${leadScore}
                            </div>
                        </div>
                    </div>
                    <a href="/permit/${loc.id}" target="_blank" style="display:block;background:linear-gradient(135deg,#3b82f6,#2563eb);color:white;text-align:center;padding:0.75rem;border-radius:6px;text-decoration:none;font-weight:600;">
                        View Full Details →
                    </a>
                </div>
            `);
            
            AppState.markersLayer.addLayer(marker);
        }
    });
}

function loadContractors() {
    fetch(`/api/construction/contractors?days=${AppState.filters.days}&limit=20`)
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
        listEl.innerHTML = '<div style="text-align:center;padding:2rem;color:#6b7280;">No contractors found</div>';
        return;
    }
    
    const html = contractors.map((c, i) => {
        const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : '';
        return `
            <div onclick="openContractorModal('${escapeHtml(c.contractor_name)}')" style="display:flex;align-items:center;gap:1rem;padding:1.25rem;background:white;border-radius:12px;border:1px solid #e5e7eb;margin-bottom:0.75rem;transition:all 0.2s;cursor:pointer;" onmouseover="this.style.borderColor='#3b82f6';this.style.transform='translateX(4px)'" onmouseout="this.style.borderColor='#e5e7eb';this.style.transform='translateX(0)'">
                <div style="font-size:1.5rem;font-weight:700;color:#6b7280;min-width:40px;text-align:center;">
                    ${medal || `#${i + 1}`}
                </div>
                <div style="flex:1;">
                    <div style="font-weight:700;font-size:1rem;color:#111827;margin-bottom:0.25rem;">
                        ${escapeHtml(c.contractor_name)}
                    </div>
                    <div style="font-size:0.875rem;color:#6b7280;">
                        <i class="fas fa-hard-hat"></i> ${c.permit_count} active permits
                        ${c.job_types ? ` • ${escapeHtml(c.job_types)}` : ''}
                    </div>
                </div>
                <div style="background:linear-gradient(135deg,#3b82f6,#2563eb);color:white;padding:0.5rem 1rem;border-radius:8px;font-weight:700;">
                    ${c.permit_count}
                </div>
            </div>
        `;
    }).join('');
    
    listEl.innerHTML = html;
}

function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    event.target.closest('.tab-btn').classList.add('active');
    
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    const tabEl = document.getElementById(`${tabName}Tab`);
    if (tabEl) tabEl.classList.add('active');
    
    if (tabName === 'contractors') {
        loadContractors();
    }
}

function sortPermitsList() {
    const sortBy = document.getElementById('sortBy')?.value;
    if (!sortBy) return;
    
    // Map frontend sort values to backend API values
    const sortMap = {
        'date_desc': 'date',
        'date_asc': 'date',  // Will handle asc in backend if needed
        'score': 'score',
        'contacts': 'contacts',
        'address': 'date',  // Fallback to date for now
        'applicant': 'date',  // Fallback to date for now
        'type': 'date'  // Fallback to date for now
    };
    
    AppState.filters.sortBy = sortMap[sortBy] || 'date';
    AppState.pagination.currentPage = 1; // Reset to first page when sorting
    loadPermits();
}

function clearFilters() {
    document.getElementById('filterBorough').value = '';
    document.getElementById('filterJobType').value = '';
    document.getElementById('filterDays').value = '90';
    document.getElementById('searchInput').value = '';
    
    document.querySelectorAll('.quick-filter-btn').forEach(btn => btn.classList.remove('active'));
    
    AppState.filters = {
        borough: '',
        jobType: '',
        days: 90,
        searchText: '',
        hasContact: false,
        minLeadScore: 0
    };
    
    showToast('Filters cleared', 'info');
    applyFilters();
}

function exportPermits() {
    const params = new URLSearchParams({ days: AppState.filters.days });
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
    return colors[jobType] || '#6b7280';
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
            <div style="background:#f3f4f6;border-radius:12px;padding:1.5rem;margin-bottom:1rem;animation:pulse 1.5s infinite;">
                <div style="background:#e5e7eb;height:20px;width:70%;border-radius:4px;margin-bottom:0.75rem;"></div>
                <div style="background:#e5e7eb;height:16px;width:40%;border-radius:4px;margin-bottom:1rem;"></div>
                <div style="background:#e5e7eb;height:60px;width:100%;border-radius:8px;"></div>
            </div>
        `;
    }
    container.innerHTML = html;
}

const style = document.createElement('style');
style.textContent = '@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.5}}';
document.head.appendChild(style);

// Modal functions
function openContractorModal(contractorName) {
    const modal = document.getElementById('contractorModal');
    const modalTitle = document.getElementById('contractorModalTitle');
    const modalBody = document.getElementById('contractorModalBody');
    
    if (!modal || !modalBody) return;
    
    // Show modal
    modal.classList.add('active');
    document.body.style.overflow = 'hidden';
    
    // Update title
    modalTitle.innerHTML = `<i class="fas fa-hard-hat"></i> ${escapeHtml(contractorName)}`;
    
    // Show loading state
    modalBody.innerHTML = `
        <div class="modal-loading">
            <div class="modal-spinner"></div>
            <div style="margin-top: 1rem; color: var(--text-secondary);">Loading contractor data...</div>
        </div>
    `;
    
    // Fetch contractor data
    fetch(`/api/contractor/${encodeURIComponent(contractorName)}`)
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                displayContractorModal(data);
            } else {
                modalBody.innerHTML = `
                    <div style="text-align:center;padding:3rem;color:#ef4444;">
                        <i class="fas fa-exclamation-circle" style="font-size:3rem;margin-bottom:1rem;"></i>
                        <div style="font-size:1.125rem;font-weight:600;">Failed to load contractor</div>
                        <div style="margin-top:0.5rem;color:#6b7280;">${escapeHtml(data.error || 'Unknown error')}</div>
                    </div>
                `;
            }
        })
        .catch(err => {
            console.error('Error loading contractor:', err);
            modalBody.innerHTML = `
                <div style="text-align:center;padding:3rem;color:#ef4444;">
                    <i class="fas fa-exclamation-circle" style="font-size:3rem;margin-bottom:1rem;"></i>
                    <div style="font-size:1.125rem;font-weight:600;">Error loading contractor</div>
                    <div style="margin-top:0.5rem;color:#6b7280;">${escapeHtml(err.message)}</div>
                </div>
            `;
        });
}

function closeContractorModal(event) {
    // Only close if clicking overlay or close button
    if (event && event.target.closest('.modal-container')) return;
    
    const modal = document.getElementById('contractorModal');
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

function displayContractorModal(data) {
    const modalBody = document.getElementById('contractorModalBody');
    if (!modalBody) return;
    
    const contractor = data.contractor || {};
    const permits = data.permits || [];
    const buildings = data.buildings || [];
    
    // Format dates
    const formatDate = (dateStr) => {
        if (!dateStr) return '-';
        return new Date(dateStr).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    };
    
    // Format currency
    const formatCurrency = (value) => {
        if (!value || value === 0) return '-';
        if (value >= 1000000) return `$${(value / 1000000).toFixed(1)}M`;
        if (value >= 1000) return `$${(value / 1000).toFixed(0)}K`;
        return `$${value.toLocaleString()}`;
    };
    
    // Parse job types
    const jobTypesArray = contractor.job_types ? contractor.job_types.split(', ').filter(Boolean) : [];
    const jobTypeCounts = {};
    permits.forEach(p => {
        if (p.job_type) {
            jobTypeCounts[p.job_type] = (jobTypeCounts[p.job_type] || 0) + 1;
        }
    });
    const jobTypesWithCounts = Object.entries(jobTypeCounts).map(([type, count]) => ({ job_type: type, count }));
    
    const html = `
        <style>
            .modal-profile-header {
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                height: 120px;
                margin: -2rem -2rem 0 -2rem;
                border-radius: var(--radius-lg) var(--radius-lg) 0 0;
                position: relative;
            }
            .modal-profile-content {
                padding: 0 2rem 2rem 2rem;
            }
            .modal-profile-avatar-container {
                position: relative;
                margin-top: -60px;
                margin-bottom: 1.5rem;
            }
            .modal-profile-avatar {
                width: 120px;
                height: 120px;
                border-radius: 50%;
                background: white;
                border: 5px solid white;
                box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 3em;
                color: #667eea;
                margin: 0 auto;
            }
            .modal-profile-badge {
                position: absolute;
                bottom: 5px;
                right: calc(50% - 50px);
                width: 35px;
                height: 35px;
                background: #ffd32a;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                color: #1a1a1a;
                font-size: 1em;
                border: 3px solid white;
                box-shadow: 0 2px 8px rgba(0,0,0,0.15);
            }
            .modal-profile-info {
                text-align: center;
                margin-bottom: 1.5rem;
            }
            .modal-profile-info h2 {
                margin: 0 0 0.5rem 0;
                font-size: 1.8em;
                font-weight: 700;
                color: #e2e8f0;
            }
            .modal-profile-meta {
                display: flex;
                gap: 20px;
                justify-content: center;
                font-size: 0.9em;
                color: #666;
                margin-top: 0.75rem;
            }
            .modal-profile-meta span {
                display: flex;
                align-items: center;
                gap: 5px;
            }
            .modal-profile-meta i {
                color: #667eea;
            }
            .modal-stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 1rem;
                margin-bottom: 2rem;
            }
            .modal-stat-card {
                background: white;
                border: 2px solid #e0e0e0;
                border-radius: 12px;
                padding: 1.25rem;
                text-align: center;
                transition: all 0.2s;
            }
            .modal-stat-card:hover {
                border-color: #667eea;
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
            }
            .modal-stat-icon {
                width: 50px;
                height: 50px;
                border-radius: 12px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-size: 1.5em;
                margin: 0 auto 0.75rem;
            }
            .modal-stat-value {
                font-size: 2em;
                font-weight: 700;
                color: #667eea;
                margin-bottom: 0.25rem;
            }
            .modal-stat-label {
                font-size: 0.9em;
                color: #1a1a1a;
                font-weight: 600;
                margin-bottom: 0.25rem;
            }
            .modal-stat-subtitle {
                font-size: 0.8em;
                color: #666;
            }
            .modal-sidebar-grid {
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 1.5rem;
                margin-bottom: 2rem;
            }
            .modal-sidebar-card {
                background: white;
                border: 1px solid #e0e0e0;
                border-radius: 16px;
                padding: 1.5rem;
            }
            .modal-sidebar-card h3 {
                margin: 0 0 1rem 0;
                font-size: 1.1em;
                color: #1a1a1a;
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .modal-sidebar-card h3 i {
                color: #667eea;
            }
            .modal-about-stats {
                display: flex;
                flex-direction: column;
                gap: 0.75rem;
            }
            .modal-about-stat {
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 10px;
                background: #f8f9fa;
                border-radius: 10px;
            }
            .modal-about-stat i {
                font-size: 1.3em;
                color: #667eea;
                width: 30px;
                text-align: center;
            }
            .modal-about-stat strong {
                display: block;
                font-size: 1em;
                color: #1a1a1a;
            }
            .modal-about-stat span {
                font-size: 0.8em;
                color: #666;
            }
            .modal-permits-section {
                background: white;
                border: 1px solid #e0e0e0;
                border-radius: 16px;
                padding: 1.5rem;
            }
            .modal-permits-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 1rem;
                padding-bottom: 1rem;
                border-bottom: 1px solid #e0e0e0;
            }
            .modal-permits-header h3 {
                margin: 0;
                font-size: 1.2em;
                color: #1a1a1a;
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .modal-permits-header i {
                color: #667eea;
            }
            .modal-permit-badge {
                background: #667eea;
                color: white;
                padding: 0.35rem 0.85rem;
                border-radius: 20px;
                font-size: 0.9em;
                font-weight: 600;
            }
            .modal-permits-list {
                max-height: 400px;
                overflow-y: auto;
            }
            .modal-tabs {
                display: flex;
                gap: 10px;
                margin-bottom: 1.5rem;
                border-bottom: 2px solid #e0e0e0;
            }
            .modal-tab-btn {
                padding: 0.75rem 1.5rem;
                background: transparent;
                border: none;
                color: #666;
                font-weight: 600;
                cursor: pointer;
                border-bottom: 3px solid transparent;
                margin-bottom: -2px;
                transition: all 0.2s;
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .modal-tab-btn:hover {
                color: #667eea;
            }
            .modal-tab-btn.active {
                color: #667eea;
                border-bottom-color: #667eea;
            }
            .modal-tab-badge {
                background: #e0e0e0;
                color: #666;
                padding: 0.2rem 0.6rem;
                border-radius: 12px;
                font-size: 0.85em;
            }
            .modal-tab-btn.active .modal-tab-badge {
                background: #667eea;
                color: white;
            }
            .modal-tab-content {
                display: none;
            }
            .modal-tab-content.active {
                display: block;
            }
            .modal-permit-card {
                border: 2px solid #e0e0e0;
                border-radius: 12px;
                padding: 1.25rem;
                margin-bottom: 0.75rem;
                cursor: pointer;
                transition: all 0.2s;
                position: relative;
                overflow: hidden;
            }
            .modal-permit-card::before {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                width: 4px;
                height: 100%;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                transform: scaleY(0);
                transform-origin: top;
                transition: transform 0.3s ease;
            }
            .modal-permit-card:hover {
                border-color: #667eea;
                box-shadow: 0 4px 12px rgba(102, 126, 234, 0.15);
                transform: translateX(4px);
            }
            .modal-permit-card:hover::before {
                transform: scaleY(1);
            }
            .modal-permit-header {
                display: flex;
                justify-content: space-between;
                align-items: start;
                margin-bottom: 0.75rem;
            }
            .modal-permit-title {
                font-weight: 700;
                font-size: 1.1em;
                color: #1a1a1a;
                margin-bottom: 0.25rem;
            }
            .modal-permit-type-badge {
                padding: 0.35rem 0.75rem;
                border-radius: 20px;
                font-size: 0.8em;
                font-weight: 700;
                text-transform: uppercase;
            }
            .modal-permit-details {
                display: flex;
                gap: 15px;
                font-size: 0.85em;
                color: #666;
            }
            .modal-permit-details span {
                display: flex;
                align-items: center;
                gap: 4px;
            }
            .modal-permit-details i {
                color: #667eea;
            }
        </style>
        
        <div class="modal-profile-header"></div>
        <div class="modal-profile-content">
            <div class="modal-profile-avatar-container">
                <div class="modal-profile-avatar">
                    <i class="fas fa-hard-hat"></i>
                </div>
                <div class="modal-profile-badge">
                    <i class="fas fa-certificate"></i>
                </div>
            </div>
            
            <div class="modal-profile-info">
                <h2>${escapeHtml(contractor.contractor_name || 'Unknown')}</h2>
                ${contractor.license ? `<div style="color:#666;font-size:0.9em;margin-bottom:0.5rem;"><i class="fas fa-id-card"></i> License: ${escapeHtml(contractor.license)}</div>` : ''}
                <div class="modal-profile-meta">
                    <span>
                        <i class="fas fa-calendar-check"></i>
                        Most Recent: ${formatDate(contractor.most_recent_job)}
                    </span>
                    <span>
                        <i class="fas fa-tools"></i>
                        ${jobTypesArray.length} Specialties
                    </span>
                </div>
            </div>
            
            <!-- Stats Grid -->
            <div class="modal-stats-grid">
                <div class="modal-stat-card">
                    <div class="modal-stat-icon">
                        <i class="fas fa-file-contract"></i>
                    </div>
                    <div class="modal-stat-value">${contractor.total_jobs || 0}</div>
                    <div class="modal-stat-label">Total Jobs</div>
                    <div class="modal-stat-subtitle">All time permits</div>
                </div>
                
                <div class="modal-stat-card">
                    <div class="modal-stat-icon">
                        <i class="fas fa-hammer"></i>
                    </div>
                    <div class="modal-stat-value">${contractor.active_jobs || 0}</div>
                    <div class="modal-stat-label">Active Jobs</div>
                    <div class="modal-stat-subtitle">Last 90 days</div>
                </div>
                
                <div class="modal-stat-card">
                    <div class="modal-stat-icon">
                        <i class="fas fa-building"></i>
                    </div>
                    <div class="modal-stat-value">${contractor.unique_properties || 0}</div>
                    <div class="modal-stat-label">Properties</div>
                    <div class="modal-stat-subtitle">Unique buildings</div>
                </div>
                
                <div class="modal-stat-card">
                    <div class="modal-stat-icon">
                        <i class="fas fa-chart-line"></i>
                    </div>
                    <div class="modal-stat-value">${formatCurrency(contractor.total_value)}</div>
                    <div class="modal-stat-label">Total Value</div>
                    <div class="modal-stat-subtitle">Portfolio value</div>
                </div>
            </div>
            
            <!-- Sidebar Cards -->
            <div class="modal-sidebar-grid">
                <div class="modal-sidebar-card">
                    <h3><i class="fas fa-info-circle"></i> About</h3>
                    <div class="modal-about-stats">
                        <div class="modal-about-stat">
                            <i class="fas fa-calendar-plus"></i>
                            <div>
                                <strong>${formatDate(contractor.first_job)}</strong>
                                <span>First Job</span>
                            </div>
                        </div>
                        <div class="modal-about-stat">
                            <i class="fas fa-chart-line"></i>
                            <div>
                                <strong>${contractor.job_type_variety || 0} Types</strong>
                                <span>Job Variety</span>
                            </div>
                        </div>
                        <div class="modal-about-stat">
                            <i class="fas fa-dollar-sign"></i>
                            <div>
                                <strong>${formatCurrency(contractor.avg_project_value)}</strong>
                                <span>Avg Project</span>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="modal-sidebar-card">
                    <h3><i class="fas fa-trophy"></i> Highlights</h3>
                    <div class="modal-about-stats">
                        <div class="modal-about-stat">
                            <i class="fas fa-fire"></i>
                            <div>
                                <strong>${contractor.jobs_last_year || 0}</strong>
                                <span>Jobs Last Year</span>
                            </div>
                        </div>
                        <div class="modal-about-stat">
                            <i class="fas fa-crown"></i>
                            <div>
                                <strong>${formatCurrency(contractor.largest_project)}</strong>
                                <span>Largest Project</span>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Tabs Section -->
            <div class="modal-permits-section">
                <div class="modal-tabs">
                    <button class="modal-tab-btn active" onclick="switchModalTab(event, 'permits')">
                        <i class="fas fa-file-contract"></i>
                        <span>Permits</span>
                        <span class="modal-tab-badge">${permits.length}</span>
                    </button>
                    <button class="modal-tab-btn" onclick="switchModalTab(event, 'buildings')">
                        <i class="fas fa-building"></i>
                        <span>Buildings</span>
                        <span class="modal-tab-badge">${buildings.length}</span>
                    </button>
                </div>
                
                <!-- Permits Tab -->
                <div id="modalPermitsTab" class="modal-tab-content active">
                    <div class="modal-permits-list">
                    ${permits.length > 0 ? permits.slice(0, 10).map(p => {
                        const jobType = p.job_type || 'N/A';
                        const color = getJobTypeColor(jobType);
                        return `
                            <div class="modal-permit-card" onclick="window.open('/permit/${p.id}', '_blank')">
                                <div class="modal-permit-header">
                                    <div>
                                        <div class="modal-permit-title">
                                            ${escapeHtml(jobType)} - ${escapeHtml(p.address || 'No address')}
                                        </div>
                                        <div class="modal-permit-details">
                                            <span>
                                                <i class="fas fa-map-marker-alt"></i>
                                                ${escapeHtml(p.borough || 'Unknown')}
                                            </span>
                                            <span>
                                                <i class="fas fa-calendar"></i>
                                                ${formatDate(p.issue_date)}
                                            </span>
                                            <span>
                                                <i class="fas fa-file-alt"></i>
                                                ${escapeHtml(p.permit_no || 'N/A')}
                                            </span>
                                        </div>
                                    </div>
                                    <span class="modal-permit-type-badge" style="background:${color};color:white;">
                                        ${escapeHtml(jobType)}
                                    </span>
                                </div>
                            </div>
                        `;
                    }).join('') : '<div style="text-align:center;padding:2rem;color:#666;">No permits found</div>'}
                    </div>
                </div>
                
                <!-- Buildings Tab -->
                <div id="modalBuildingsTab" class="modal-tab-content">
                    <div class="modal-permits-list">
                        ${buildings.length > 0 ? buildings.map(b => {
                            return `
                                <div class="modal-permit-card" onclick="window.open('/property/${b.bbl}', '_blank')">
                                    <div class="modal-permit-header">
                                        <div>
                                            <div class="modal-permit-title">
                                                ${escapeHtml(b.address || 'No address')}
                                            </div>
                                            <div class="modal-permit-details">
                                                <span>
                                                    <i class="fas fa-map-marker-alt"></i>
                                                    ${escapeHtml(b.borough || 'Unknown')}
                                                </span>
                                                <span>
                                                    <i class="fas fa-hard-hat"></i>
                                                    ${b.permit_count} permit${b.permit_count !== 1 ? 's' : ''}
                                                </span>
                                                <span>
                                                    <i class="fas fa-building"></i>
                                                    ${b.total_units || 0} units
                                                </span>
                                                ${b.assessed_total_value ? `
                                                <span>
                                                    <i class="fas fa-dollar-sign"></i>
                                                    ${formatCurrency(b.assessed_total_value)}
                                                </span>
                                                ` : ''}
                                            </div>
                                            ${b.job_types ? `
                                            <div style="margin-top:0.5rem;font-size:0.8em;color:#666;">
                                                <i class="fas fa-tools"></i> ${escapeHtml(b.job_types)}
                                            </div>
                                            ` : ''}
                                            ${b.current_owner_name ? `
                                            <div style="margin-top:0.25rem;font-size:0.8em;color:#666;">
                                                <i class="fas fa-user"></i> ${escapeHtml(b.current_owner_name)}
                                            </div>
                                            ` : ''}
                                        </div>
                                        <span class="modal-permit-type-badge" style="background:#667eea;color:white;">
                                            BBL: ${escapeHtml(b.bbl || 'N/A')}
                                        </span>
                                    </div>
                                </div>
                            `;
                        }).join('') : '<div style="text-align:center;padding:2rem;color:#666;">No buildings found</div>'}
                    </div>
                </div>
            </div>
        </div>
    `;
    
    modalBody.innerHTML = html;
}

function switchModalTab(event, tabName) {
    // Update tab buttons
    document.querySelectorAll('.modal-tab-btn').forEach(btn => btn.classList.remove('active'));
    event.currentTarget.classList.add('active');
    
    // Update tab content
    document.querySelectorAll('.modal-tab-content').forEach(content => content.classList.remove('active'));
    const targetTab = document.getElementById(`modal${tabName.charAt(0).toUpperCase() + tabName.slice(1)}Tab`);
    if (targetTab) targetTab.classList.add('active');
}

// Close modal on Escape key
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        closeContractorModal();
    }
});

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
