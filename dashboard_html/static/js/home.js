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

async function performSearch() {
    const searchInput = document.getElementById('universalSearch');
    const query = searchInput.value.trim();
    
    if (!query) {
        alert('Please enter a search term');
        return;
    }
    
    console.log('Searching for:', query);
    
    // Check if BBL format (e.g., 1-00234-0056)
    const bblPattern = /^\d{1}-\d{5}-\d{4}$/;
    if (bblPattern.test(query)) {
        const bbl = query.replace(/-/g, '');
        window.location.href = `/property/${bbl}`;
        return;
    }
    
    // Show loading state
    const searchBtn = document.getElementById('searchBtn');
    const originalText = searchBtn.innerHTML;
    searchBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Searching...';
    searchBtn.disabled = true;
    
    try {
        const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
        const results = await response.json();
        
        if (results.length === 0) {
            alert('No results found. Try a different search term.');
        } else if (results.length === 1) {
            // Single result - go directly to property page
            window.location.href = `/property/${results[0].bbl}`;
        } else {
            // Multiple results - go to results page
            window.location.href = `/search-results?q=${encodeURIComponent(query)}`;
        }
    } catch (error) {
        console.error('Search error:', error);
        alert('Search failed. Please try again.');
    } finally {
        searchBtn.innerHTML = originalText;
        searchBtn.disabled = false;
    }
}

async function fetchSuggestions(query) {
    const suggestions = document.getElementById('searchSuggestions');
    
    try {
        const response = await fetch(`/api/suggest?q=${encodeURIComponent(query)}&limit=5`);
        const results = await response.json();
        
        if (results.length === 0) {
            suggestions.classList.remove('active');
            return;
        }
        
        // Render suggestions
        suggestions.innerHTML = results.map(result => `
            <div class="suggestion-item" onclick="selectSuggestion('${result.bbl}')">
                <div style="display: flex; justify-content: space-between; align-items: start;">
                    <div>
                        <div style="font-weight: 600; color: var(--text-primary);">
                            ${result.address}
                        </div>
                        <div style="font-size: 0.875rem; color: var(--text-muted); margin-top: 0.25rem;">
                            ${result.owner || 'Owner unknown'} • BBL: ${formatBBL(result.bbl)}
                        </div>
                    </div>
                    <div style="font-size: 0.75rem; color: var(--text-secondary); white-space: nowrap; margin-left: 1rem;">
                        ${result.permits || 0} permits
                    </div>
                </div>
            </div>
        `).join('');
        
        suggestions.classList.add('active');
    } catch (error) {
        console.error('Suggestion fetch error:', error);
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
        // Use fallback values already set in HTML
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

function showLoading() {
    // Add loading overlay if needed
    console.log('Loading...');
}

function hideLoading() {
    // Remove loading overlay
    console.log('Loading complete');
}

function showError(message) {
    alert(message);
}

// Export for use in other scripts
window.RealEstateIntel = {
    performSearch,
    selectSuggestion,
    loadMarketStats
};
