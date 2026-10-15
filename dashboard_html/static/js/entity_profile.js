// Entity research profile: renders one dossier and follows its background job.
(function () {
    'use strict';

    const page = document.getElementById('entityPage');
    if (!page) return;
    const DOSSIER_ID = Number(page.dataset.dossierId);
    const POLL_MS = 2500;

    const state = {
        data: null,
        tierFilter: 'sure',       // 'sure' = strong + exact, 'all' includes candidates
        polling: null,
        expanded: {},             // card id -> show all rows
        busy: false,
    };

    const STEP_LABELS = {
        db: 'Our database', acris: 'ACRIS deeds & mortgages', hpd: 'HPD registrations',
        dob_bis: 'DOB BIS permits', dob_now_filings: 'DOB NOW filings', dob_now_permits: 'DOB NOW permits',
        ecb: 'ECB violations', hpd_litigation: 'HPD litigation', sos: 'NY Dept. of State',
        targets: 'Choosing connected names', hop_acris: 'ACRIS for connections', hop_hpd: 'HPD for connections',
        hop_dob: 'DOB for connections', hop_sos: 'NY DOS for connections',
    };
    const TIER_LABELS = { strong: 'Corroborated', exact: 'Name match', candidate: 'Partial' };
    const SOURCE_LINKS = {
        acris: 'https://a836-acris.nyc.gov/DS/DocumentSearch/PartyName',
        hpd: 'https://hpdonline.nyc.gov/hpdonline/',
        dob_bis: 'https://a810-bisweb.nyc.gov/bisweb/',
        dob_now_filings: 'https://a810-dobnow.nyc.gov/publish/Index.html#!/',
        dob_now_permits: 'https://a810-dobnow.nyc.gov/publish/Index.html#!/',
        ecb: 'https://a820-ecbticketfinder.nyc.gov/',
        hpd_litigation: 'https://hpdonline.nyc.gov/hpdonline/',
        sos: 'https://apps.dos.ny.gov/publicInquiry/',
    };

    // ----------------------------------------------------------------- utils
    function esc(v) {
        return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
    function el(id) { return document.getElementById(id); }
    function fmtDate(v) {
        if (!v) return '';
        const d = new Date(v);
        return isNaN(d) ? String(v).slice(0, 10) : d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
    }
    function fmtMoney(v) {
        if (v == null || v === '' || isNaN(Number(v))) return '';
        return Number(v).toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
    }
    function fmtNum(v) { return Number(v || 0).toLocaleString(); }
    function researchUrl(name, extra) {
        const params = new URLSearchParams({ name, ...(extra || {}) });
        return '/entity/research?' + params.toString();
    }
    function nameLink(name, extra) {
        return `<a class="entity-link" href="${esc(researchUrl(name, extra))}">${esc(name)}</a>`;
    }
    function tierBadge(tier) {
        return `<span class="tier-badge tier-${esc(tier)}">${esc(TIER_LABELS[tier] || tier)}</span>`;
    }
    function partyAddress(pa) {
        if (!pa) return '';
        return [pa.street, pa.unit, pa.city, pa.state, pa.zip].filter(Boolean).join(', ');
    }
    function notice(text, kind) {
        const box = el('entityNotice');
        if (!text) { box.hidden = true; return; }
        box.textContent = text;
        box.className = 'entity-notice' + (kind ? ' ' + kind : '');
        box.hidden = false;
    }
    async function post(path, body) {
        const resp = await fetch(path, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-Entity-Research': '1' },
            body: JSON.stringify(body || {}),
        });
        let data = null;
        try { data = await resp.json(); } catch (_e) { data = null; }
        if (!resp.ok || !data || !data.success) {
            throw new Error((data && data.error) || `Request failed (HTTP ${resp.status})`);
        }
        return data;
    }

    // ------------------------------------------------------------------ load
    async function load() {
        const resp = await fetch(`/api/entity/${DOSSIER_ID}`, { credentials: 'same-origin' });
        if (!resp.ok) {
            notice(resp.status === 404 ? 'This research has expired or was deleted.' : 'Could not load this research right now.', 'error');
            return;
        }
        state.data = await resp.json();
        render();
        schedulePolling();
    }

    function activeJob() {
        const jobs = (state.data && state.data.jobs) || {};
        return Object.values(jobs).find(j => j.status === 'queued' || j.status === 'running') || null;
    }

    function schedulePolling() {
        clearTimeout(state.polling);
        if (!activeJob()) return;
        state.polling = setTimeout(async () => {
            try {
                const job = activeJob();
                const resp = await fetch(`/api/entity/jobs/${job.id}`, { credentials: 'same-origin' });
                const data = await resp.json();
                if (data && data.job) {
                    state.data.jobs[data.job.kind] = data.job;
                    renderSteps();
                    // Stream results in while sources finish; a full reload is cheap.
                    const done = data.job.status === 'complete' || data.job.status === 'failed';
                    if (done || Object.keys(data.job.steps || {}).length % 2 === 0) {
                        await load();
                        return;
                    }
                }
            } catch (_e) { /* retry on the next tick */ }
            schedulePolling();
        }, POLL_MS);
    }

    // ---------------------------------------------------------------- render
    function render() {
        const { dossier, rows } = state.data;
        renderHeader(dossier);
        renderSteps();
        renderGlance(rows);
        renderDatabase(rows);
        renderProperties();
        renderTransactions(rows);
        renderPermits(rows);
        renderCompliance(rows);
        renderEntities(rows);
        renderExpansion();
        renderConnections();
        renderCrm();
        renderSources();
        el('retentionDays').textContent = dossier.retention_days;
        el('cacheHours').textContent = dossier.cache_hours;
    }

    function renderHeader(d) {
        el('entityName').textContent = d.display_name;
        el('kindChip').textContent = { person: 'Person', organization: 'Company / entity', multiple: 'Multiple parties', unknown: 'Unclassified name' }[d.entity_kind] || d.entity_kind;
        const job = activeJob();
        const research = (state.data.jobs || {}).research;
        const status = el('statusChip');
        if (job) {
            status.textContent = job.kind === 'expand' ? 'Expanding connections…' : 'Searching public records…';
        } else if (research && research.status === 'failed') {
            status.textContent = 'Last search failed';
        } else if (research) {
            status.textContent = 'Research complete';
        } else {
            status.textContent = 'Not yet searched';
        }
        const fresh = el('freshChip');
        if (d.external_checked_at) {
            fresh.hidden = false;
            fresh.textContent = `Public records checked ${fmtDate(d.external_checked_at)}`;
        }
        const expiry = el('expiryChip');
        expiry.hidden = false;
        expiry.textContent = d.permanent ? 'Kept permanently' : (d.expires_at ? `Auto-deletes ${fmtDate(d.expires_at)} unless kept` : '');
        const keep = el('keepBtn');
        keep.disabled = state.busy;
        keep.textContent = d.permanent ? 'Stop keeping' : 'Keep permanently';
        keep.title = d.permanent ? 'Allow this research to expire again' : 'Never auto-delete this research';
        el('refreshBtn').disabled = state.busy || !!job;
        el('expandBtn').disabled = state.busy || !!job || !research || research.status !== 'complete';
        el('expandBtn').textContent = d.expanded_at ? 'Expand connections again' : 'Expand connections';
    }

    function renderSteps() {
        const list = el('entitySteps');
        const job = activeJob() || (state.data.jobs || {}).research;
        if (!job) { list.innerHTML = ''; return; }
        const order = (state.data.steps || {})[job.kind] || Object.keys(job.steps || {});
        const steps = job.steps || {};
        const finished = job.status === 'complete' || job.status === 'failed';
        list.innerHTML = order.map(key => {
            const s = steps[key] || { status: finished ? 'skipped' : 'pending' };
            const count = s.status === 'done' && s.count != null ? `<span class="step-count">${fmtNum(s.count)}</span>` : '';
            const title = s.error ? `Failed: ${s.error}` : (s.note || '');
            const label = s.status === 'cached' ? 'cached' : s.status === 'skipped' ? 'skipped' : s.status === 'failed' ? 'unavailable' : '';
            return `<li class="step-${esc(s.status)}" title="${esc(title)}"><span class="step-dot"></span>${esc(STEP_LABELS[key] || key)}${count}${label ? `<span class="step-count">${label}</span>` : ''}</li>`;
        }).join('');
        renderHeader(state.data.dossier);
    }

    function subjectRows(rows) {
        // Direct rows for the subject; candidates only when the filter asks for them.
        return rows.filter(r => !r.hop && (state.tierFilter === 'all' || r.match_tier !== 'candidate'));
    }

    function renderGlance(rows) {
        const own = subjectRows(rows);
        const lots = new Set(own.filter(r => r.bbl).map(r => r.bbl));
        el('glanceLots').textContent = fmtNum(lots.size);
        el('glanceAcris').textContent = fmtNum(new Set(own.filter(r => r.source === 'acris').map(r => r.record_id)).size);
        el('glanceDob').textContent = fmtNum(own.filter(r => r.source.startsWith('dob')).length);
        el('glanceCompliance').textContent = fmtNum(own.filter(r => ['hpd', 'ecb', 'hpd_litigation'].includes(r.source)).length);
        el('glanceConnections').textContent = fmtNum((state.data.connections || []).length);
        el('glanceDb').textContent = fmtNum(own.filter(r => r.in_database).length);
    }

    function renderDatabase(rows) {
        const own = subjectRows(rows).filter(r => r.in_database);
        const crm = state.data.crm || {};
        const lots = {};
        own.forEach(r => {
            if (!r.bbl) return;
            const g = lots[r.bbl] = lots[r.bbl] || { bbl: r.bbl, address: r.address, roles: new Set(), sources: new Set() };
            if (r.address && (!g.address || r.address.length > g.address.length)) g.address = r.address;
            g.roles.add(r.role);
            g.sources.add(r.source);
        });
        const items = Object.values(lots);
        const contacts = own.filter(r => r.source === 'permit_contact');
        el('dbCount').textContent = fmtNum(items.length + (crm.contacts || []).length);
        if (!items.length && !contacts.length && !(crm.contacts || []).length && !(crm.buildings || []).length) {
            el('dbBody').innerHTML = '<p class="entity-muted">Nothing in our database names this entity yet. Properties found in public records can be added from the list below.</p>';
            return;
        }
        let html = '';
        if (items.length) {
            html += '<div class="entity-rows">' + items.slice(0, 40).map(g => `
                <div class="entity-row">
                    <div>
                        <div class="entity-row-title"><a href="/property/${esc(g.bbl)}">${esc(g.address || 'Address unavailable')}</a><span class="entity-chip db">Tracked</span></div>
                        <div class="entity-row-meta"><span class="mono">BBL ${esc(g.bbl)}</span> · ${esc([...g.roles].slice(0, 4).join(', '))}</div>
                    </div>
                    <div class="entity-row-side"><a class="btn btn-secondary btn-sm" href="/property/${esc(g.bbl)}">Open profile</a></div>
                </div>`).join('') + '</div>';
            if (items.length > 40) html += `<p class="entity-muted entity-more">${fmtNum(items.length - 40)} more tracked lots appear in the property list below.</p>`;
        }
        if (contacts.length) {
            html += `<p class="entity-row-meta entity-more"><strong>${fmtNum(contacts.length)}</strong> permit contact record${contacts.length === 1 ? '' : 's'} carry this name${contacts.some(c => c.details && c.details.phone) ? ', with phone numbers on the permit pages' : ''}.</p>`;
        }
        el('dbBody').innerHTML = html;
    }

    function renderProperties() {
        const props = (state.data.properties || []).filter(p => p.hop === 0 && (state.tierFilter === 'all' || p.tier !== 'candidate'));
        el('propertiesCount').textContent = fmtNum(props.length);
        const body = el('propertiesBody');
        if (!props.length) {
            const hidden = (state.data.properties || []).filter(p => p.hop === 0 && p.tier === 'candidate').length;
            body.innerHTML = `<p class="entity-muted">${hidden ? `No lots carry an exact name match. ${fmtNum(hidden)} partial match${hidden === 1 ? '' : 'es'} are hidden by the filter above.` : 'No lots found yet.'}</p>`;
            return;
        }
        const limit = state.expanded.properties ? props.length : 30;
        body.innerHTML = '<div class="entity-rows">' + props.slice(0, limit).map(p => `
            <div class="entity-row${p.tier === 'candidate' ? ' is-candidate' : ''}" data-bbl="${esc(p.bbl)}">
                <div>
                    <div class="entity-row-title">
                        ${p.in_database ? `<a href="/property/${esc(p.bbl)}">${esc(p.address || 'Address unavailable')}</a>` : esc(p.address || 'Address unavailable')}
                        ${tierBadge(p.tier)}
                        ${p.in_database ? '<span class="entity-chip db">Tracked</span>' : ''}
                    </div>
                    <div class="entity-row-meta">
                        <span class="mono">BBL ${esc(p.bbl)}</span> · ${fmtNum(p.records)} record${p.records === 1 ? '' : 's'}${p.latest_date ? ` · latest ${esc(fmtDate(p.latest_date))}` : ''}
                        <div class="entity-chip-row">${p.roles.map(r => `<span class="entity-chip">${esc(r)}</span>`).join('')}</div>
                        ${p.names.length > 1 || (p.names[0] && p.names[0].toUpperCase() !== state.data.dossier.display_name.toUpperCase()) ? `<div class="entity-row-as-written">Written as: ${esc(p.names.join(' · '))}</div>` : ''}
                    </div>
                </div>
                <div class="entity-row-side">
                    ${p.in_database
                        ? `<a class="btn btn-secondary btn-sm" href="/property/${esc(p.bbl)}">Open profile</a>`
                        : `<button type="button" class="btn btn-primary btn-sm" data-add-bbl="${esc(p.bbl)}" title="Create a tracked building record for this lot and run the free public-data enrichment">Add permanently</button>`}
                </div>
            </div>`).join('') + '</div>' +
            (props.length > limit ? `<button type="button" class="btn btn-secondary btn-sm entity-more" data-expand="properties">Show all ${fmtNum(props.length)} lots</button>` : '');
    }

    function partiesLine(details) {
        const parties = (details && details.parties) || [];
        if (!parties.length) return '';
        return `<div class="entity-row-parties">With: ${parties.slice(0, 6).map(p => `${nameLink(p.name)}${p.role ? ` <span>(${esc(p.role)})</span>` : ''}`).join(', ')}${parties.length > 6 ? ` and ${parties.length - 6} more` : ''}</div>`;
    }

    function recordRow(r, title, meta) {
        const link = r.source_url && /^https?:\/\//.test(r.source_url) ? `<a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Source ↗</a>` : (r.source_url ? `<a href="${esc(r.source_url)}">Open</a>` : '');
        const propertyLink = r.bbl ? (r.in_database ? `<a href="/property/${esc(r.bbl)}">Property</a>` : `<span class="mono">BBL ${esc(r.bbl)}</span>`) : '';
        return `<div class="entity-row${r.match_tier === 'candidate' ? ' is-candidate' : ''}">
            <div>
                <div class="entity-row-title">${title} ${tierBadge(r.match_tier)}${r.hop ? '<span class="tier-badge tier-hop">via ' + esc(r.via || 'connection') + '</span>' : ''}</div>
                <div class="entity-row-meta">${meta}${r.name_as_written && r.name_as_written.toUpperCase() !== state.data.dossier.display_name.toUpperCase() ? `<div class="entity-row-as-written">Written as: ${esc(r.name_as_written)}</div>` : ''}${partiesLine(r.details)}</div>
            </div>
            <div class="entity-row-side">${r.record_date ? `<span>${esc(fmtDate(r.record_date))}</span>` : ''}${propertyLink}${link}</div>
        </div>`;
    }

    function listCard(cardKey, countId, bodyId, rows, renderOne, emptyText) {
        el(countId).textContent = fmtNum(rows.length);
        const body = el(bodyId);
        if (!rows.length) { body.innerHTML = `<p class="entity-muted">${esc(emptyText)}</p>`; return; }
        const limit = state.expanded[cardKey] ? rows.length : 20;
        body.innerHTML = '<div class="entity-rows">' + rows.slice(0, limit).map(renderOne).join('') + '</div>' +
            (rows.length > limit ? `<button type="button" class="btn btn-secondary btn-sm entity-more" data-expand="${esc(cardKey)}">Show all ${fmtNum(rows.length)}</button>` : '');
    }

    function renderTransactions(rows) {
        const own = subjectRows(rows).filter(r => r.source === 'acris');
        listCard('transactions', 'transactionsCount', 'transactionsBody', own, r => {
            const d = r.details || {};
            const title = `${esc(d.doc_type || 'Document')} · ${esc(r.role || 'party')}${d.amount ? ` · ${esc(fmtMoney(d.amount))}` : ''}`;
            const meta = `${esc(r.address || (r.bbl ? 'Lot ' + r.bbl : 'Lot not recorded'))}${d.crfn ? ` · CRFN <span class="mono">${esc(d.crfn)}</span>` : ''}${partyAddress(r.party_address) ? `<div>Party address: ${esc(partyAddress(r.party_address))}</div>` : ''}`;
            return recordRow(r, title, meta);
        }, 'No ACRIS documents name this party.');
    }

    function renderPermits(rows) {
        const own = subjectRows(rows).filter(r => r.source.startsWith('dob'));
        listCard('permits', 'permitsCount', 'permitsBody', own, r => {
            const d = r.details || {};
            const title = `${esc(r.role)} · ${esc(d.job_type || d.work_type || 'Job')}${d.job_number ? ` <span class="mono">${esc(d.job_number)}</span>` : ''}`;
            const meta = `${esc(r.address || (r.bbl ? 'Lot ' + r.bbl : ''))}${d.status ? ` · ${esc(d.status)}` : ''}${d.cost ? ` · est. ${esc(fmtMoney(d.cost))}` : ''}${d.description ? `<div>${esc(d.description)}</div>` : ''}<div class="entity-chip-row"><span class="entity-chip">${esc((state.data.source_labels || {})[r.source] || r.source)}</span></div>`;
            return recordRow(r, title, meta);
        }, 'No DOB permits or filings name this party.');
    }

    function renderCompliance(rows) {
        const own = subjectRows(rows).filter(r => ['hpd', 'ecb', 'hpd_litigation'].includes(r.source));
        listCard('compliance', 'complianceCount', 'complianceBody', own, r => {
            const d = r.details || {};
            const label = (state.data.source_labels || {})[r.source] || r.source;
            let title = `${esc(label)} · ${esc(r.role)}`;
            let meta = esc(r.address || (r.bbl ? 'Lot ' + r.bbl : ''));
            if (r.source === 'ecb') {
                meta += `${d.violation_type ? ` · ${esc(d.violation_type)}` : ''}${d.status ? ` · ${esc(d.status)}` : ''}${d.balance_due ? ` · balance ${esc(fmtMoney(d.balance_due))}` : ''}${d.description ? `<div>${esc(d.description)}</div>` : ''}`;
            } else if (r.source === 'hpd_litigation') {
                meta += `${d.case_type ? ` · ${esc(d.case_type)}` : ''}${d.status ? ` · ${esc(d.status)}` : ''}${d.penalty ? ` · penalty ${esc(fmtMoney(d.penalty))}` : ''}`;
            } else {
                meta += `${d.title ? ` · ${esc(d.title)}` : ''}${partyAddress(r.party_address) ? `<div>Business address: ${esc(partyAddress(r.party_address))}</div>` : ''}`;
            }
            return recordRow(r, title, meta);
        }, 'No HPD registrations, ECB violations or housing cases name this party.');
    }

    function renderEntities(rows) {
        const own = rows.filter(r => r.source === 'sos' && !r.hop);
        el('entitiesCount').textContent = fmtNum(own.length);
        const body = el('entitiesBody');
        const research = (state.data.jobs || {}).research;
        const sosStep = research && research.steps && research.steps.sos;
        if (!own.length) {
            body.innerHTML = `<p class="entity-muted">${esc(sosStep && sosStep.note ? sosStep.note : (sosStep && sosStep.status === 'done' ? 'No registered entity with this name was found.' : 'No NY Department of State record loaded yet.'))}</p>`;
            return;
        }
        body.innerHTML = own.map(r => {
            const d = r.details || {};
            const people = d.people || [];
            return `<div class="entity-row">
                <div>
                    <div class="entity-row-title">${esc(r.name_as_written)} ${tierBadge(r.match_tier)}</div>
                    <div class="entity-row-meta">${esc(d.entity_type || 'Entity')}${d.status ? ` · ${esc(d.status)}` : ''}${d.jurisdiction ? ` · ${esc(d.jurisdiction)}` : ''}${d.dos_id ? ` · DOS ID <span class="mono">${esc(d.dos_id)}</span>` : ''}${r.record_date ? ` · formed ${esc(fmtDate(r.record_date))}` : ''}
                        ${people.length ? `<ul class="entity-people">${people.map(p => `<li>${p.is_person ? nameLink(p.name, { role: p.role, address: (p.address || {}).street || '', city: (p.address || {}).city || '', state: (p.address || {}).state || '', zip: (p.address || {}).zip || '', source: 'sos' }) : esc(p.name)} <span>· ${esc(p.role)}${p.is_agent ? ' (service agent, not an owner)' : ''}</span>${partyAddress(p.address) ? `<div class="entity-row-as-written">${esc(partyAddress(p.address))}</div>` : ''}</li>`).join('')}</ul>` : ''}
                    </div>
                </div>
                <div class="entity-row-side"><a href="${esc(r.source_url || SOURCE_LINKS.sos)}" target="_blank" rel="noopener noreferrer">NY DOS ↗</a></div>
            </div>`;
        }).join('');
    }

    function renderExpansion() {
        const groups = state.data.expansions || [];
        const card = el('expansionCard');
        const hopRows = (state.data.rows || []).filter(r => r.hop);
        card.hidden = !groups.length && !(state.data.dossier.expanded_at);
        el('expansionCount').textContent = fmtNum(hopRows.length);
        if (!groups.length) {
            el('expansionBody').innerHTML = '<p class="entity-muted">Expansion found no additional records for the connected names.</p>';
            return;
        }
        el('expansionBody').innerHTML = groups.map(g => {
            const rows = hopRows.filter(r => (r.via || 'Connected name') === (g.via || 'Connected name'));
            const lots = {};
            rows.forEach(r => { if (r.bbl) { lots[r.bbl] = lots[r.bbl] || { bbl: r.bbl, address: r.address, n: 0, inDb: false }; lots[r.bbl].n++; lots[r.bbl].inDb = lots[r.bbl].inDb || r.in_database; } });
            const lotList = Object.values(lots).slice(0, 12);
            return `<div class="entity-row">
                <div>
                    <div class="entity-row-title">${nameLink(g.via || '')} <span class="tier-badge tier-hop">${esc(g.kind)}</span></div>
                    <div class="entity-row-meta">${fmtNum(g.records)} record${g.records === 1 ? '' : 's'} across ${fmtNum(g.lots)} lot${g.lots === 1 ? '' : 's'} · ${esc(g.sources.map(s => (state.data.source_labels || {})[s] || s).join(', '))}
                        ${lotList.length ? `<ul class="entity-people">${lotList.map(l => `<li>${l.inDb ? `<a href="/property/${esc(l.bbl)}">${esc(l.address || l.bbl)}</a>` : esc(l.address || l.bbl)} <span class="mono">· ${esc(l.bbl)}</span></li>`).join('')}${Object.keys(lots).length > 12 ? `<li>and ${Object.keys(lots).length - 12} more lots</li>` : ''}</ul>` : ''}
                    </div>
                </div>
                <div class="entity-row-side"><a class="btn btn-secondary btn-sm" href="${esc(g.research_url)}">Research this name</a></div>
            </div>`;
        }).join('');
    }

    function renderConnections() {
        const items = state.data.connections || [];
        el('connectionsCount').textContent = fmtNum(items.length);
        const body = el('connectionsBody');
        if (!items.length) { body.innerHTML = '<p class="entity-muted">No other names appear on the same records yet.</p>'; return; }
        const limit = state.expanded.connections ? items.length : 15;
        body.innerHTML = items.slice(0, limit).map(c => `
            <div class="entity-connection">
                <div class="entity-connection-name"><a href="${esc(c.research_url)}">${esc(c.name)}</a><span class="entity-chip">${esc(c.kind === 'person' ? 'person' : c.kind === 'organization' ? 'entity' : 'name')}${c.is_agent ? ' · agent' : ''}</span></div>
                <div class="entity-connection-meta">${fmtNum(c.records)} shared record${c.records === 1 ? '' : 's'}${c.lots ? ` · ${fmtNum(c.lots)} lot${c.lots === 1 ? '' : 's'}` : ''}<br>${c.relationships.map(r => `${esc(r.label)}${r.count > 1 ? ` ×${r.count}` : ''}`).join('; ')}</div>
            </div>`).join('') +
            (items.length > limit ? `<button type="button" class="btn btn-secondary btn-sm entity-more" data-expand="connections">Show all ${fmtNum(items.length)}</button>` : '');
    }

    function renderCrm() {
        const crm = state.data.crm || {};
        const body = el('crmBody');
        let html = '';
        if ((crm.do_not_contact || []).length) {
            html += `<div class="entity-dnc"><strong>Do not contact</strong> is set for this name on ${crm.do_not_contact.length} propert${crm.do_not_contact.length === 1 ? 'y' : 'ies'}: ${crm.do_not_contact.map(b => `<a href="/property/${esc(b)}">${esc(b)}</a>`).join(', ')}.</div>`;
        }
        if ((crm.contacts || []).length) {
            html += '<p class="entity-row-meta"><strong>Contacts</strong></p>' + crm.contacts.map(c => `<div class="entity-connection"><div class="entity-connection-name"><a href="/crm/contacts/${esc(c.id)}">${esc(c.name || 'Unnamed')}</a></div><div class="entity-connection-meta">${esc([c.title, c.company].filter(Boolean).join(' · '))}${c.last_contacted_at ? ` · last contacted ${esc(fmtDate(c.last_contacted_at))}` : ''}</div></div>`).join('');
        }
        if ((crm.buildings || []).length) {
            html += '<p class="entity-row-meta entity-more"><strong>Buildings</strong></p>' + crm.buildings.map(b => `<div class="entity-connection"><div class="entity-connection-name"><a href="/property/${esc(b.bbl)}">${esc(b.address || b.bbl)}</a><span class="entity-chip">${esc(b.stage || '')}</span></div><div class="entity-connection-meta">Owner recorded as ${esc(b.owner_name || '')}</div></div>`).join('');
        }
        if ((crm.research || []).length) {
            html += `<p class="entity-row-meta entity-more">${fmtNum(crm.research.length)} owner-research review${crm.research.length === 1 ? '' : 's'} saved for this name on property profiles.</p>`;
        }
        body.innerHTML = html || '<p class="entity-muted">No CRM contacts, buildings or review notes for this name on your team.</p>';
    }

    function renderSources() {
        const research = (state.data.jobs || {}).research;
        const steps = (research && research.steps) || {};
        const labels = state.data.source_labels || {};
        const order = (state.data.steps || {}).research || Object.keys(steps);
        el('sourcesBody').innerHTML = order.map(key => {
            const s = steps[key] || {};
            const status = s.status === 'done' ? `${fmtNum(s.count || 0)} records` : s.status === 'cached' ? 'cached' : s.status === 'failed' ? 'unavailable' : s.status === 'skipped' ? 'not applicable' : s.status === 'running' ? 'searching…' : 'pending';
            const link = SOURCE_LINKS[key] ? `<a href="${esc(SOURCE_LINKS[key])}" target="_blank" rel="noopener noreferrer">open ↗</a>` : '';
            return `<div class="entity-source"><div><div>${esc(labels[key] || STEP_LABELS[key] || key)}</div>${s.note || s.error ? `<div class="entity-source-note">${esc(s.error || s.note)}</div>` : ''}</div><div style="text-align:right">${esc(status)}<br>${link}</div></div>`;
        }).join('') || '<p class="entity-muted">No sources searched yet.</p>';
    }

    // --------------------------------------------------------------- actions
    async function act(button, fn, okText) {
        state.busy = true;
        renderHeader(state.data.dossier);
        const original = button.textContent;
        button.textContent = 'Working…';
        try {
            await fn();
            if (okText) notice(okText, 'success');
            await load();
        } catch (e) {
            notice(e.message || 'Request failed', 'error');
        } finally {
            state.busy = false;
            button.textContent = original;
            if (state.data) renderHeader(state.data.dossier);
        }
    }

    el('keepBtn').addEventListener('click', e => {
        const permanent = !state.data.dossier.permanent;
        act(e.currentTarget, () => post(`/api/entity/${DOSSIER_ID}/keep`, { permanent }),
            permanent ? 'This research is now kept permanently.' : 'This research will expire again after 60 days without use.');
    });
    el('refreshBtn').addEventListener('click', e => {
        act(e.currentTarget, () => post(`/api/entity/${DOSSIER_ID}/refresh`), 'Re-running the public-record searches.');
    });
    el('expandBtn').addEventListener('click', e => {
        act(e.currentTarget, () => post(`/api/entity/${DOSSIER_ID}/expand`), 'Following connected names one hop out.');
    });

    page.addEventListener('click', async e => {
        const filter = e.target.closest('[data-tier-filter]');
        if (filter) {
            state.tierFilter = filter.dataset.tierFilter;
            page.querySelectorAll('[data-tier-filter]').forEach(b => b.setAttribute('aria-pressed', String(b === filter)));
            render();
            return;
        }
        const more = e.target.closest('[data-expand]');
        if (more) { state.expanded[more.dataset.expand] = true; render(); return; }
        const jump = e.target.closest('[data-jump]');
        if (jump) { const target = el(jump.dataset.jump); if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
        const add = e.target.closest('[data-add-bbl]');
        if (add) {
            const bbl = add.dataset.addBbl;
            add.disabled = true;
            add.textContent = 'Adding…';
            try {
                const data = await post(`/api/entity/${DOSSIER_ID}/add-property`, { bbl });
                notice(`Added BBL ${bbl} to the property database. Free public-data enrichment is running in the background.`, 'success');
                await load();
                if (data.url) {
                    const row = page.querySelector(`[data-bbl="${bbl}"] .entity-row-side`);
                    if (row) row.innerHTML = `<a class="btn btn-secondary btn-sm" href="${esc(data.url)}">Open profile</a>`;
                }
            } catch (err) {
                notice(err.message || 'Could not add that property.', 'error');
                add.disabled = false;
                add.textContent = 'Add permanently';
            }
        }
    });

    load().catch(() => notice('Could not load this research right now.', 'error'));
})();
