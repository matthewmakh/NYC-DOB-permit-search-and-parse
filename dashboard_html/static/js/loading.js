// ==========================================
// Universal Loading States & Animations
// ==========================================

/**
 * Show a loading spinner in a container
 * @param {string|HTMLElement} container - Container element or selector
 * @param {string} message - Optional loading message
 */
function showLoading(container, message = 'Loading...') {
    const element = typeof container === 'string' 
        ? document.querySelector(container) 
        : container;
    
    if (!element) return;
    
    element.innerHTML = `
        <div class="loading-container">
            <div class="spinner"></div>
            <div class="loading-text">${message}</div>
        </div>
    `;
}

/**
 * Show skeleton loaders in a container
 * @param {string|HTMLElement} container - Container element or selector
 * @param {number} count - Number of skeleton items
 */
function showSkeleton(container, count = 3) {
    const element = typeof container === 'string' 
        ? document.querySelector(container) 
        : container;
    
    if (!element) return;
    
    let html = '';
    for (let i = 0; i < count; i++) {
        html += `
            <div class="skeleton skeleton-card" style="margin-bottom: 1rem;"></div>
        `;
    }
    element.innerHTML = html;
}

/**
 * Show a page-level loading overlay
 * @param {string} message - Loading message
 * @returns {HTMLElement} - The overlay element
 */
function showPageLoading(message = 'Loading...') {
    // Remove existing overlay if any
    hidePageLoading();
    
    const overlay = document.createElement('div');
    overlay.id = 'globalLoadingOverlay';
    overlay.className = 'page-loading';
    overlay.innerHTML = `
        <div class="page-loading-content">
            <div class="spinner spinner-lg"></div>
            <div class="loading-text" style="font-size: 1rem; margin-top: 1rem;">${message}</div>
        </div>
    `;
    
    document.body.appendChild(overlay);
    
    // Fade in
    setTimeout(() => {
        overlay.style.opacity = '1';
    }, 10);
    
    return overlay;
}

/**
 * Hide the page-level loading overlay
 */
function hidePageLoading() {
    const overlay = document.getElementById('globalLoadingOverlay');
    if (overlay) {
        overlay.style.opacity = '0';
        setTimeout(() => overlay.remove(), 200);
    }
}

/**
 * Show an empty state
 * @param {string|HTMLElement} container - Container element or selector
 * @param {Object} options - Configuration options
 */
function showEmptyState(container, options = {}) {
    const {
        icon = '🔍',
        title = 'No Data Found',
        message = 'There is no data to display.',
        actionText = null,
        actionCallback = null
    } = options;
    
    const element = typeof container === 'string' 
        ? document.querySelector(container) 
        : container;
    
    if (!element) return;
    
    element.innerHTML = `
        <div class="empty-state">
            <div class="empty-state-icon">${icon}</div>
            <div class="empty-state-title">${title}</div>
            <div class="empty-state-text">${message}</div>
            ${actionText ? `
                <button class="btn btn-primary" style="margin-top: 1rem;" onclick="${actionCallback}">
                    ${actionText}
                </button>
            ` : ''}
        </div>
    `;
}

/**
 * Show an error state
 * @param {string|HTMLElement} container - Container element or selector
 * @param {string} message - Error message
 */
function showErrorState(container, message = 'An error occurred') {
    showEmptyState(container, {
        icon: '⚠️',
        title: 'Error',
        message: message
    });
}

/**
 * Show inline loading (e.g., in a button)
 * @param {string|HTMLElement} element - Element or selector
 * @param {string} text - Loading text
 */
function showInlineLoading(element, text = 'Loading...') {
    const el = typeof element === 'string' 
        ? document.querySelector(element) 
        : element;
    
    if (!el) return;
    
    // Store original content
    el.dataset.originalContent = el.innerHTML;
    el.disabled = true;
    
    el.innerHTML = `
        <div class="inline-loading">
            <div class="spinner spinner-sm"></div>
            <span>${text}</span>
        </div>
    `;
}

/**
 * Hide inline loading and restore original content
 * @param {string|HTMLElement} element - Element or selector
 */
function hideInlineLoading(element) {
    const el = typeof element === 'string' 
        ? document.querySelector(element) 
        : element;
    
    if (!el) return;
    
    el.innerHTML = el.dataset.originalContent || el.innerHTML;
    el.disabled = false;
    delete el.dataset.originalContent;
}

/**
 * Show a progress bar
 * @param {string|HTMLElement} container - Container element or selector
 * @param {number} progress - Progress percentage (0-100)
 * @param {string} label - Optional label
 */
function showProgress(container, progress, label = '') {
    const element = typeof container === 'string' 
        ? document.querySelector(container) 
        : container;
    
    if (!element) return;
    
    element.innerHTML = `
        <div style="padding: 1rem;">
            ${label ? `<div style="margin-bottom: 0.5rem; color: var(--text-secondary); font-size: 0.875rem;">${label}</div>` : ''}
            <div style="background: var(--bg-tertiary); height: 8px; border-radius: 4px; overflow: hidden;">
                <div style="background: var(--primary); height: 100%; width: ${Math.min(100, Math.max(0, progress))}%; transition: width 0.3s ease;"></div>
            </div>
            <div style="margin-top: 0.5rem; color: var(--text-muted); font-size: 0.875rem; text-align: right;">
                ${Math.round(progress)}%
            </div>
        </div>
    `;
}

/**
 * Create a loading toast notification
 * @param {string} message - Toast message
 * @param {number} duration - Duration in ms (0 for persistent)
 * @returns {HTMLElement} - The toast element
 */
function showLoadingToast(message, duration = 0) {
    const toast = document.createElement('div');
    toast.className = 'loading-toast';
    toast.style.cssText = `
        position: fixed;
        bottom: 2rem;
        right: 2rem;
        background: var(--bg-secondary);
        border: 1px solid var(--border-color);
        border-radius: var(--radius-lg);
        padding: 1rem 1.5rem;
        box-shadow: var(--shadow-lg);
        z-index: 10000;
        display: flex;
        align-items: center;
        gap: 1rem;
        animation: slideInRight 0.3s ease;
    `;
    
    toast.innerHTML = `
        <div class="spinner spinner-sm"></div>
        <span style="color: var(--text-primary);">${message}</span>
    `;
    
    document.body.appendChild(toast);
    
    if (duration > 0) {
        setTimeout(() => {
            toast.style.animation = 'slideOutRight 0.3s ease';
            setTimeout(() => toast.remove(), 300);
        }, duration);
    }
    
    return toast;
}

/**
 * Remove a loading toast
 * @param {HTMLElement} toast - The toast element
 */
function hideLoadingToast(toast) {
    if (toast) {
        toast.style.animation = 'slideOutRight 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }
}

// Add necessary animations
if (!document.getElementById('loadingAnimations')) {
    const style = document.createElement('style');
    style.id = 'loadingAnimations';
    style.textContent = `
        @keyframes slideInRight {
            from {
                transform: translateX(100%);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
        
        @keyframes slideOutRight {
            from {
                transform: translateX(0);
                opacity: 1;
            }
            to {
                transform: translateX(100%);
                opacity: 0;
            }
        }
    `;
    document.head.appendChild(style);
}

// Export for use in other scripts
window.Loading = {
    show: showLoading,
    showSkeleton: showSkeleton,
    showPage: showPageLoading,
    hidePage: hidePageLoading,
    showEmpty: showEmptyState,
    showError: showErrorState,
    showInline: showInlineLoading,
    hideInline: hideInlineLoading,
    showProgress: showProgress,
    showToast: showLoadingToast,
    hideToast: hideLoadingToast
};
