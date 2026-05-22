// ==========================================
// NYC Real Estate Intelligence Platform
// Homepage JavaScript
// ==========================================

// State Management
const state = {
    searchQuery: '',
    searchResults: [],
    stats: {
        activePermits: 0,
        recentSales: 0,
        totalProperties: 1361,
        qualifiedLeads: 0
    }
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initializeSearch();
    loadMarketStats();
    initializeExamples();
    initializeNavigation();
});

// =========================
// SEARCH FUNCTIONALITY
// =========================

function initializeSearch() {
    const searchInput = document.getElementById('universalSearch');
    const searchBtn = document.getElementById('searchBtn');
    const suggestions = document.getElementById('searchSuggestions');
    
    // Search on button click
    searchBtn.addEventListener('click', performSearch);
    
    // Search on Enter key
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            performSearch();
        }
    });
    
    // Autocomplete on input
    let debounceTimer;
    searchInput.addEventListener('input', (e) => {
        clearTimeout(debounceTimer);
        const query = e.target.value.trim();
        
        if (query.length < 2) {
            suggestions.classList.remove('active');
            return;
        }
        
        debounceTimer = setTimeout(() => {
            fetchSuggestions(query);
        }, 300);
    });
    
    // Close suggestions when clicking outside
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.search-container')) {
            suggestions.classList.remove('active');
        }
    });
}

/**
 * Normalize address input for better searching
 * Handles common abbreviations and formatting
 */
function normalizeAddressInput(query) {
    if (!query) return query;
    
    let normalized = query.trim();
    
    // Remove extra whitespace
    normalized = normalized.replace(/\s+/g, ' ');
    
    return normalized;
}

async function performSearch() {
    const searchInput = document.getElementById('universalSearch');
    let query = searchInput.value.trim();
    
    if (!query) {
        showNotification('Please enter a search term', 'warning');
        return;
    }
    
    console.log('Searching for:', query);
    
    // Normalize the query for address searches
    query = normalizeAddressInput(query);
    
    // Check if BBL format (e.g., 1-00234-0056 or 1002340056)
    const bblPatternDash = /^\d{1}-\d{5}-\d{4}$/;
    const bblPatternNoDash = /^\d{10}$/;
    
    if (bblPatternDash.test(query)) {
        const bbl = query.replace(/-/g, '');
        window.location.href = `/property/${bbl}`;
        return;
    }
    
    if (bblPatternNoDash.test(query)) {
        window.location.href = `/property/${query}`;
        return;
    }
    
    // Show loading state
    const searchBtn = document.getElementById('searchBtn');
    const originalText = searchBtn.innerHTML;
    searchBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Searching...';
    searchBtn.disabled = true;
    
    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        
        if (!response.ok) {
            throw new Error(`Search failed with status ${response.status}`);
        }
        
        const results = await response.json();
        
        console.log('Search returned', results.length, 'results');
        
        if (results.length === 0) {
            // Not in our DB. If the input looks like a property address
            // (digits + street name) we can run the full free enrichment
            // pipeline to pull it in. Owner names like "JOHN SMITH" don't
            // qualify — there's nothing to look up.
            if (looksLikePropertyQuery(query)) {
                offerAutoAddProperty(query);
            } else {
                showNotification('No results found. Try a different search term or check your spelling.', 'info');
            }
        } else if (results.length === 1) {
            // Single result - go directly to property page
            window.location.href = `/property/${results[0].bbl}`;
        } else {
            // Multiple results - go to results page
            window.location.href = `/search-results?q=${encodeURIComponent(query)}`;
        }
    } catch (error) {
        console.error('Search error:', error);
        showNotification('Search failed. Please try again.', 'error');
    } finally {
        searchBtn.innerHTML = originalText;
        searchBtn.disabled = false;
    }
}

/**
 * Heuristic: does this look like a property address rather than an owner name?
 * Must start with at least one digit (the house number) and have a non-digit
 * word after it (the street). "141 WYONA STREET" yes, "JOHN SMITH" no.
 */
function looksLikePropertyQuery(query) {
    if (!query) return false;
    const trimmed = query.trim();
    return /^\d+[A-Z0-9\-]*\s+\S+/i.test(trimmed);
}

/**
 * Show a modal asking whether to run a full free-enrichment lookup on a
 * property that isn't yet in our database. On confirm, kicks off the
 * /api/property/auto-add request and redirects to the new building page.
 */
function offerAutoAddProperty(query) {
    // Remove any existing modal first.
    const existing = document.getElementById('autoAddModal');
    if (existing) existing.remove();

    const modal = document.createElement('div');
    modal.id = 'autoAddModal';
    modal.style.cssText = `
        position: fixed; inset: 0; background: rgba(0,0,0,0.6);
        display: flex; align-items: center; justify-content: center;
        z-index: 10000;
    `;
    modal.innerHTML = `
        <div style="background: #1e1e2f; color: #fff; padding: 1.75rem 2rem;
                    border-radius: 12px; max-width: 480px; width: 92%;
                    box-shadow: 0 12px 40px rgba(0,0,0,0.5);">
            <h3 style="margin: 0 0 0.5rem 0; font-size: 1.2rem;">
                🔍 Property not in our database yet
            </h3>
            <p style="margin: 0 0 1rem 0; color: #cbd0e0; font-size: 0.95rem;">
                We can run a full lookup on <strong>${escapeHtml(query)}</strong>
                using NYC's public data: PLUTO, RPAD, HPD, ACRIS, tax liens,
                and NY Secretary of State. Takes about 10&ndash;20 seconds.
            </p>
            <p style="margin: 0 0 1.25rem 0; color: #8b92a8; font-size: 0.85rem;">
                Free &mdash; no contact enrichment runs unless you click Enrich
                on the resulting page.
            </p>
            <div id="autoAddStatus" style="display: none; margin-bottom: 1rem;
                 padding: 0.75rem; background: rgba(102,126,234,0.1);
                 border-radius: 6px; font-size: 0.88rem;"></div>
            <div style="display: flex; gap: 0.5rem; justify-content: flex-end;">
                <button id="autoAddCancel" style="padding: 0.6rem 1.2rem;
                        background: transparent; color: #cbd0e0;
                        border: 1px solid #555; border-radius: 6px;
                        cursor: pointer;">Cancel</button>
                <button id="autoAddConfirm" style="padding: 0.6rem 1.2rem;
                        background: linear-gradient(135deg, #667eea, #764ba2);
                        color: #fff; border: none; border-radius: 6px;
                        font-weight: 600; cursor: pointer;">
                    Look up this property
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    document.getElementById('autoAddCancel').addEventListener('click', () => {
        modal.remove();
    });
    document.getElementById('autoAddConfirm').addEventListener('click', () => {
        runAutoAdd(query, modal);
    });
}

async function runAutoAdd(query, modal) {
    const statusEl = document.getElementById('autoAddStatus');
    const confirmBtn = document.getElementById('autoAddConfirm');
    const cancelBtn = document.getElementById('autoAddCancel');
    statusEl.style.display = 'block';
    statusEl.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Resolving address and running enrichment&hellip;';
    confirmBtn.disabled = true;
    confirmBtn.style.opacity = '0.5';
    cancelBtn.disabled = true;

    try {
        const resp = await fetch('/api/property/auto-add', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({query}),
        });
        const data = await resp.json();

        if (!data.success) {
            statusEl.innerHTML = `<strong>Couldn't look it up:</strong> ${escapeHtml(data.error || 'unknown error')}`;
            statusEl.style.background = 'rgba(220, 38, 38, 0.15)';
            confirmBtn.disabled = false;
            confirmBtn.style.opacity = '1';
            cancelBtn.disabled = false;
            return;
        }

        // Show a brief success state with the per-step report so the user
        // knows what actually came back before we redirect.
        const reportLines = Object.entries(data.report || {})
            .map(([step, status]) => `<li><strong>${step}</strong>: ${escapeHtml(String(status))}</li>`)
            .join('');
        statusEl.innerHTML = `
            <strong>✓ Done</strong> — BBL ${data.bbl}. Redirecting&hellip;
            <ul style="margin: 0.5rem 0 0 1rem; padding: 0; font-size: 0.82rem; color: #aab;">
                ${reportLines}
            </ul>
        `;
        statusEl.style.background = 'rgba(34,197,94,0.12)';
        setTimeout(() => {
            window.location.href = `/property/${data.bbl}`;
        }, 1200);
    } catch (e) {
        statusEl.innerHTML = `<strong>Request failed:</strong> ${escapeHtml(String(e))}`;
        statusEl.style.background = 'rgba(220, 38, 38, 0.15)';
        confirmBtn.disabled = false;
        confirmBtn.style.opacity = '1';
        cancelBtn.disabled = false;
    }
}

function escapeHtml(s) {
    return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * Show a notification message to the user
 */
function showNotification(message, type = 'info') {
    // Check if notification container exists, create if not
    let container = document.getElementById('notificationContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notificationContainer';
        container.style.cssText = 'position: fixed; top: 80px; right: 20px; z-index: 9999; display: flex; flex-direction: column; gap: 10px;';
        document.body.appendChild(container);
    }
    
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    
    const colors = {
        info: { bg: '#e3f2fd', border: '#2196f3', text: '#1565c0' },
        warning: { bg: '#fff3e0', border: '#ff9800', text: '#e65100' },
        error: { bg: '#ffebee', border: '#f44336', text: '#c62828' },
        success: { bg: '#e8f5e9', border: '#4caf50', text: '#2e7d32' }
    };
    
    const color = colors[type] || colors.info;
    
    notification.style.cssText = `
        background: ${color.bg};
        border: 1px solid ${color.border};
        color: ${color.text};
        padding: 12px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        font-size: 14px;
        max-width: 350px;
        animation: slideIn 0.3s ease;
    `;
    
    notification.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px;">
            <i class="fas fa-${type === 'error' ? 'exclamation-circle' : type === 'warning' ? 'exclamation-triangle' : type === 'success' ? 'check-circle' : 'info-circle'}"></i>
            <span>${message}</span>
        </div>
    `;
    
    container.appendChild(notification);
    
    // Auto-remove after 5 seconds
    setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease';
        setTimeout(() => notification.remove(), 300);
    }, 5000);
}

async function fetchSuggestions(query) {
    const suggestions = document.getElementById('searchSuggestions');
    
    // Show loading state
    suggestions.innerHTML = `
        <div class="suggestion-item" style="justify-content: center; padding: 1rem;">
            <div class="inline-loading">
                <div class="spinner spinner-sm"></div>
                <span>Searching...</span>
            </div>
        </div>
    `;
    suggestions.classList.add('active');
    
    try {
        const response = await fetch(`/api/suggest?q=${encodeURIComponent(query)}&limit=8`);
        
        if (!response.ok) {
            throw new Error('Suggestion fetch failed');
        }
        
        const results = await response.json();
        
        if (results.length === 0) {
            suggestions.innerHTML = `
                <div class="suggestion-item" style="justify-content: center; padding: 1rem; color: var(--text-muted);">
                    <i class="fas fa-search" style="margin-right: 0.5rem;"></i>
                    No results found - press Enter to search all data
                </div>
            `;
            return;
        }
        
        // Render suggestions - SANITIZED to prevent XSS
        suggestions.innerHTML = results.map(result => `
            <div class="suggestion-item" onclick="selectSuggestion('${escapeHtml(result.bbl)}')">
                <div style="display: flex; justify-content: space-between; align-items: start;">
                    <div style="flex: 1;">
                        <div style="font-weight: 600; color: var(--text-primary);">
                            ${escapeHtml(result.address || 'Address Unknown')}
                        </div>
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem; display: flex; align-items: center; gap: 0.5rem;">
                            <span>${escapeHtml(result.owner || 'Owner unknown')}</span>
                            <span style="color: var(--border-color);">•</span>
                            <span>BBL: ${formatBBL(result.bbl)}</span>
                            ${result.match_type ? `
                                <span style="background: var(--primary-light); color: var(--primary); padding: 0.125rem 0.5rem; border-radius: var(--radius-sm); font-size: 0.75rem; font-weight: 600;">
                                    ${escapeHtml(result.match_type)}
                                </span>
                            ` : ''}
                        </div>
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-secondary); white-space: nowrap; margin-left: 1rem;">
                        ${parseInt(result.permits) || 0} permits
                    </div>
                </div>
            </div>
        `).join('');
        
        suggestions.classList.add('active');
    } catch (error) {
        console.error('Suggestion fetch error:', error);
        suggestions.innerHTML = `
            <div class="suggestion-item" style="justify-content: center; padding: 1rem; color: var(--text-muted);">
                <i class="fas fa-exclamation-triangle" style="margin-right: 0.5rem;"></i>
                Error loading suggestions
            </div>
        `;
    }
}

function selectSuggestion(bbl) {
    window.location.href = `/property/${bbl}`;
}

function formatBBL(bbl) {
    if (bbl.length === 10) {
        return `${bbl[0]}-${bbl.substr(1, 5)}-${bbl.substr(6, 4)}`;
    }
    return bbl;
}

// =========================
// EXAMPLE SEARCHES
// =========================

function initializeExamples() {
    const exampleLinks = document.querySelectorAll('.example-link');
    
    exampleLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const searchTerm = link.getAttribute('data-search');
            document.getElementById('universalSearch').value = searchTerm;
            performSearch();
        });
    });
}

// =========================
// MARKET STATS
// =========================

async function loadMarketStats() {
    // Show skeleton loaders
    const statElements = ['activePermits', 'recentSales', 'totalProperties', 'qualifiedLeads'];
    statElements.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.innerHTML = '<div class="dot-loader"><span></span><span></span><span></span></div>';
        }
    });
    
    try {
        const response = await fetch('/api/market-stats');
        const stats = await response.json();
        
        // Update stat displays with animation
        animateNumber('activePermits', stats.activePermits || 1968);
        animateNumber('recentSales', stats.recentSales || 1141);
        animateNumber('totalProperties', stats.totalProperties || 1361);
        animateNumber('qualifiedLeads', stats.qualifiedLeads || 937);
        
        state.stats = stats;
    } catch (error) {
        console.error('Failed to load market stats:', error);
        // Use fallback values
        animateNumber('activePermits', 1968);
        animateNumber('recentSales', 1141);
        animateNumber('totalProperties', 1361);
        animateNumber('qualifiedLeads', 937);
    }
}

function animateNumber(elementId, targetValue) {
    const element = document.getElementById(elementId);
    if (!element) return;
    
    const duration = 1500; // 1.5 seconds
    const startValue = 0;
    const startTime = performance.now();
    
    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        // Ease out cubic
        const easeProgress = 1 - Math.pow(1 - progress, 3);
        const currentValue = Math.floor(startValue + (targetValue - startValue) * easeProgress);
        
        element.textContent = currentValue.toLocaleString();
        
        if (progress < 1) {
            requestAnimationFrame(update);
        }
    }
    
    requestAnimationFrame(update);
}

// =========================
// NAVIGATION
// =========================

function initializeNavigation() {
    const savedListsBtn = document.getElementById('savedListsBtn');
    const settingsBtn = document.getElementById('settingsBtn');
    
    if (savedListsBtn) {
        savedListsBtn.addEventListener('click', () => {
            alert('Saved Lists feature coming soon!');
        });
    }
    
    if (settingsBtn) {
        settingsBtn.addEventListener('click', () => {
            alert('Settings feature coming soon!');
        });
    }
}

// =========================
// UTILITY FUNCTIONS
// =========================

/**
 * Escape HTML to prevent XSS attacks
 */
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showLoading() {
    // Add loading overlay if needed
    console.log('Loading...');
}

function hideLoading() {
    // Remove loading overlay
    console.log('Loading complete');
}

function showError(message) {
    showNotification(message, 'error');
}

// Export for use in other scripts
window.RealEstateIntel = {
    performSearch,
    selectSuggestion,
    loadMarketStats,
    escapeHtml
};
