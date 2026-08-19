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
    modal.className = 'modal';
    modal.style.display = 'block';
    modal.innerHTML = `
        <div class="modal-content autoadd-modal-content">
            <h3>Property not in our database yet</h3>
            <p>
                We can run a full lookup on <strong>${escapeHtml(query)}</strong>
                using NYC's public data: PLUTO, RPAD, HPD, ACRIS, tax liens,
                and NY Secretary of State. Takes about 10&ndash;20 seconds.
            </p>
            <p class="fineprint">
                Free &mdash; no contact enrichment runs unless you click Enrich
                on the resulting page.
            </p>
            <div id="autoAddStatus" class="autoadd-status"></div>
            <div class="autoadd-actions">
                <button id="autoAddCancel" class="btn btn-secondary">Cancel</button>
                <button id="autoAddConfirm" class="btn btn-primary">Look up this property</button>
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
    statusEl.classList.remove('error', 'success');
    statusEl.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Resolving address and running enrichment&hellip;';
    confirmBtn.disabled = true;
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
            statusEl.classList.add('error');
            confirmBtn.disabled = false;
            cancelBtn.disabled = false;
            return;
        }

        // Show a brief success state with the per-step report so the user
        // knows what actually came back before we redirect.
        const reportLines = Object.entries(data.report || {})
            .map(([step, status]) => `<li><strong>${step}</strong>: ${escapeHtml(String(status))}</li>`)
            .join('');
        statusEl.innerHTML = `
            <strong>Done</strong> — BBL ${data.bbl}. Redirecting&hellip;
            <ul>${reportLines}</ul>
        `;
        statusEl.classList.add('success');
        setTimeout(() => {
            window.location.href = `/property/${data.bbl}`;
        }, 1200);
    } catch (e) {
        statusEl.innerHTML = `<strong>Request failed:</strong> ${escapeHtml(String(e))}`;
        statusEl.classList.add('error');
        confirmBtn.disabled = false;
        cancelBtn.disabled = false;
    }
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
        container.className = 'toast-stack';
        document.body.appendChild(container);
    }

    const notification = document.createElement('div');
    notification.className = `toast toast-${type}`;

    const icons = {
        info: 'info-circle',
        warning: 'exclamation-triangle',
        error: 'exclamation-circle',
        success: 'check-circle'
    };

    notification.innerHTML = `
        <i class="fas fa-${icons[type] || icons.info}"></i>
        <span>${escapeHtml(message)}</span>
    `;

    container.appendChild(notification);

    // Auto-remove after 5 seconds
    setTimeout(() => {
        notification.classList.add('leaving');
        setTimeout(() => notification.remove(), 250);
    }, 5000);
}

async function fetchSuggestions(query) {
    const suggestions = document.getElementById('searchSuggestions');
    
    // Show loading state
    suggestions.innerHTML = `
        <div class="suggestion-note">
            <div class="inline-loading">
                <div class="spinner spinner-sm"></div>
                <span>Searching&hellip;</span>
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
                <div class="suggestion-note">
                    No matches yet &mdash; press Enter to search all data
                </div>
            `;
            return;
        }

        // Render suggestions - SANITIZED to prevent XSS
        suggestions.innerHTML = results.map(result => `
            <div class="suggestion-item" onclick="selectSuggestion('${escapeHtml(result.bbl)}')">
                <div>
                    <div class="suggestion-title">${escapeHtml(result.address || 'Address unknown')}</div>
                    <div class="suggestion-meta">
                        <span>${escapeHtml(result.owner || 'Owner unknown')}</span>
                        <span>&middot;</span>
                        <span>BBL ${formatBBL(result.bbl)}</span>
                        ${result.match_type ? `<span class="suggestion-chip">${escapeHtml(result.match_type)}</span>` : ''}
                    </div>
                </div>
                <div class="suggestion-count">${parseInt(result.permits) || 0} permits</div>
            </div>
        `).join('');

        suggestions.classList.add('active');
    } catch (error) {
        console.error('Suggestion fetch error:', error);
        suggestions.innerHTML = `
            <div class="suggestion-note">
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
        animateNumber('activePermits', stats.activePermits || 0);
        animateNumber('recentSales', stats.recentSales || 0);
        animateNumber('totalProperties', stats.totalProperties || 0);
        animateNumber('qualifiedLeads', stats.qualifiedLeads || 0);

        state.stats = stats;
    } catch (error) {
        console.error('Failed to load market stats:', error);
        // Don't show made-up numbers if the API is down
        statElements.forEach(id => {
            const element = document.getElementById(id);
            if (element) element.textContent = '—';
        });
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
