/**
 * Activity Tracking Client-Side Module
 * Tracks user interactions and sends them to the server for logging
 */

(function() {
    'use strict';

    const ActivityTracker = {
        // Configuration
        config: {
            endpoint: '/api/activity/track',
            batchSize: 5,
            batchTimeout: 10000, // 10 seconds
            enabled: true,
            debug: false
        },

        // Queue for batching events
        eventQueue: [],
        batchTimer: null,

        /**
         * Initialize the tracker
         */
        init: function(options = {}) {
            Object.assign(this.config, options);
            
            if (!this.config.enabled) return;

            this.setupEventListeners();
            this.log('Activity tracker initialized');
        },

        /**
         * Setup automatic event listeners
         */
        setupEventListeners: function() {
            // Track link clicks
            document.addEventListener('click', (e) => {
                const link = e.target.closest('a');
                if (link && link.href) {
                    this.trackClick(link, 'link');
                }
            });

            // Track button clicks with data-track attribute
            document.addEventListener('click', (e) => {
                const button = e.target.closest('button, [role="button"]');
                if (button) {
                    const trackId = button.dataset.track || button.id;
                    if (trackId) {
                        this.trackClick(button, 'button', trackId);
                    }
                }
            });

            // Track section visibility (for important sections)
            if ('IntersectionObserver' in window) {
                this.setupSectionTracking();
            }

            // Track tab/modal interactions
            document.addEventListener('click', (e) => {
                const tab = e.target.closest('[data-tab], .tab, [role="tab"]');
                if (tab) {
                    this.trackTabSwitch(tab);
                }
            });

            // Track filter changes
            document.addEventListener('change', (e) => {
                const filter = e.target.closest('[data-filter], .filter-select, .filter-input');
                if (filter) {
                    this.trackFilterChange(filter);
                }
            });

            // Track form submissions
            document.addEventListener('submit', (e) => {
                const form = e.target;
                if (form.dataset.track !== 'false') {
                    this.trackFormSubmit(form);
                }
            });
        },

        /**
         * Track section visibility using IntersectionObserver
         */
        setupSectionTracking: function() {
            const sections = document.querySelectorAll('[data-section], section[id]');
            if (sections.length === 0) return;

            const trackedSections = new Set();

            const observer = new IntersectionObserver((entries) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const sectionId = entry.target.dataset.section || entry.target.id;
                        if (sectionId && !trackedSections.has(sectionId)) {
                            trackedSections.add(sectionId);
                            this.track('section_view', {
                                section_id: sectionId,
                                section_name: entry.target.dataset.sectionName || sectionId
                            });
                        }
                    }
                });
            }, { threshold: 0.5 });

            sections.forEach(section => observer.observe(section));
        },

        /**
         * Track a click event
         */
        trackClick: function(element, elementType, customId = null) {
            const data = {
                element_id: customId || element.id || element.dataset.track,
                element_type: elementType,
                element_text: this.getElementText(element),
                element_href: element.href || null,
                element_class: element.className || null
            };

            this.track('click', data);
        },

        /**
         * Track tab switch
         */
        trackTabSwitch: function(tab) {
            this.track('tab_switch', {
                tab_id: tab.id || tab.dataset.tab,
                tab_text: this.getElementText(tab)
            });
        },

        /**
         * Track filter change
         */
        trackFilterChange: function(filter) {
            this.track('filter_change', {
                filter_id: filter.id || filter.name,
                filter_value: filter.value,
                filter_type: filter.type || filter.tagName.toLowerCase()
            });
        },

        /**
         * Track form submission
         */
        trackFormSubmit: function(form) {
            this.track('form_submit', {
                form_id: form.id || form.name,
                form_action: form.action
            });
        },

        /**
         * Track a search
         */
        trackSearch: function(query, resultCount = null) {
            this.track('search', {
                query: query,
                result_count: resultCount
            });
        },

        /**
         * Track an export
         */
        trackExport: function(exportType, recordCount = null, filters = null) {
            this.track('export', {
                export_type: exportType,
                record_count: recordCount,
                filters: filters
            });
        },

        /**
         * Track a data view (viewing a specific record)
         */
        trackDataView: function(dataType, dataId, dataName = null) {
            this.track('data_view', {
                data_type: dataType,
                data_id: dataId,
                data_name: dataName
            });
        },

        /**
         * Track an error
         */
        trackError: function(errorMessage, errorType = 'error', context = null) {
            this.track('error', {
                error_message: errorMessage,
                error_type: errorType,
                context: context
            }, true); // Send immediately
        },

        /**
         * Generic track function
         */
        track: function(eventType, data = {}, immediate = false) {
            if (!this.config.enabled) return;

            const event = {
                type: eventType,
                data: data,
                timestamp: new Date().toISOString(),
                page_url: window.location.href,
                page_title: document.title,
                screen_width: window.innerWidth,
                screen_height: window.innerHeight
            };

            this.log('Tracking event:', event);

            if (immediate) {
                this.sendEvents([event]);
            } else {
                this.queueEvent(event);
            }
        },

        /**
         * Queue event for batch sending
         */
        queueEvent: function(event) {
            this.eventQueue.push(event);

            if (this.eventQueue.length >= this.config.batchSize) {
                this.flushQueue();
            } else if (!this.batchTimer) {
                this.batchTimer = setTimeout(() => this.flushQueue(), this.config.batchTimeout);
            }
        },

        /**
         * Flush the event queue
         */
        flushQueue: function() {
            if (this.batchTimer) {
                clearTimeout(this.batchTimer);
                this.batchTimer = null;
            }

            if (this.eventQueue.length === 0) return;

            const events = [...this.eventQueue];
            this.eventQueue = [];

            this.sendEvents(events);
        },

        /**
         * Send events to server
         */
        sendEvents: function(events) {
            if (events.length === 0) return;

            // Use sendBeacon if available for reliability
            if (navigator.sendBeacon) {
                const blob = new Blob([JSON.stringify({ events })], { type: 'application/json' });
                navigator.sendBeacon(this.config.endpoint, blob);
            } else {
                // Fallback to fetch
                fetch(this.config.endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ events }),
                    keepalive: true
                }).catch(err => this.log('Failed to send events:', err));
            }
        },

        /**
         * Get readable text from element
         */
        getElementText: function(element) {
            // Try to get a readable label
            const text = element.innerText || 
                         element.title || 
                         element.getAttribute('aria-label') ||
                         element.alt ||
                         '';
            return text.trim().substring(0, 100);
        },

        /**
         * Debug logging
         */
        log: function(...args) {
            if (this.config.debug) {
                console.log('[ActivityTracker]', ...args);
            }
        }
    };

    // Flush queue before page unload
    window.addEventListener('beforeunload', () => {
        ActivityTracker.flushQueue();
    });

    // Expose globally
    window.ActivityTracker = ActivityTracker;

    // Auto-initialize if not explicitly disabled
    if (document.body.dataset.trackingDisabled !== 'true') {
        ActivityTracker.init();
    }
})();
