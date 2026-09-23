/* Prospecting deliberately makes no CRM writes until the promotion form is submitted. */
(() => {
    'use strict';
    const config = window.PROSPECT_CONFIG;
    if (!config) return;
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const options = (values, selected) => Object.entries(values).map(([v, label]) => `<option value="${esc(v)}" ${v === selected ? 'selected' : ''}>${esc(label)}</option>`).join('');
    const apiRoot = '/crm/prospecting/api';
    let listing, rows = [], page = 1, pages = 1, visible = new Set(), preview, importKey, uploadFile;
    let rowDetail, touchKey, loadingSequence = 0;
    const saves = new Map();
    const dateTime = value => value ? new Intl.DateTimeFormat('en-US', {timeZone:'America/New_York', month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(value)) : 'Never';
    const nyInputTime = () => {
        const parts = new Intl.DateTimeFormat('sv-SE', {timeZone:'America/New_York', year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date());
        return parts.replace(' ', 'T');
    };
    function notice(message) { $('prospect-message').textContent = message; }
    async function api(path, {method='GET', body} = {}) {
        const headers = {'X-CSRF-Token': config.csrf};
        if (body && !(body instanceof FormData)) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(body); }
        const response = await fetch(apiRoot + path, {method, headers, body, credentials:'same-origin'});
        if (response.redirected) throw new Error('Your session expired. Reload this page to sign in.');
        let data;
        try { data = await response.json(); } catch (_) { throw new Error('The server could not finish this request. Please retry.'); }
        if (!response.ok || !data.success) throw new Error(data.error || 'Request failed. Please retry.');
        return data;
    }
    async function busy(form, errorId, work) {
        const buttons = [...form.querySelectorAll('button[type="submit"]')];
        buttons.forEach(b => {b.disabled = true;});
        $(errorId).textContent = '';
        try { await work(); } catch (error) { $(errorId).textContent = error.message; }
        finally { buttons.forEach(b => {b.disabled = false;}); }
    }
    function workingData() {
        const form = $('prospect-notes-form');
        return form ? Object.fromEntries(new FormData(form)) : null;
    }
    function workingChanged() {
        const data = workingData(), row = rowDetail?.row;
        return data && row && (data.notes !== row.notes || data.status !== row.status || data.next_follow_up !== (row.next_follow_up || ''));
    }
    function canCloseLead() {
        const touch = $('prospect-touch-form');
        return !(workingChanged() || touch?.elements.note.value) || window.confirm('Discard the unsaved notes in this lead?');
    }
    document.querySelectorAll('.prospect-dialog [data-close]').forEach(b => b.addEventListener('click', () => {
        const dialog = b.closest('dialog');
        if (dialog.id !== 'prospect-lead-dialog' || canCloseLead()) dialog.close();
    }));
    $('prospect-lead-dialog').addEventListener('cancel', event => { if (!canCloseLead()) event.preventDefault(); });
    document.querySelectorAll('.prospect-dialog').forEach(dialog => dialog.addEventListener('keydown', event => {
        // Keep CRM's global navigation/palette shortcuts out of the native modal.
        event.stopPropagation();
        if (event.key === 'Escape') {
            event.preventDefault();
            if (dialog.id !== 'prospect-lead-dialog' || canCloseLead()) dialog.close();
        }
    }));
    window.addEventListener('beforeunload', event => {
        if (saves.size || document.querySelector('#prospect-rows textarea') || ($('prospect-lead-dialog').open && (workingChanged() || $('prospect-touch-form')?.elements.note.value))) {
            event.preventDefault(); event.returnValue = '';
        }
    });
    async function saveWorkingNotes(id) {
        if (!workingChanged()) return;
        const result = await api(`/rows/${id}`, {method:'PATCH',body:{...workingData(),version:rowDetail.row.version}});
        rowDetail.row = result.row;
    }
    function field(row, name) { return row.cells[listing.mapping[name]] || ''; }
    function label(row) { return field(row,'name') || field(row,'company') || field(row,'address') || `Lead ${row.position}`; }
    function safeLink(value) {
        try { const url = new URL(value); if (['http:', 'https:'].includes(url.protocol)) return `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${esc(value)}</a>`; } catch (_) { /* plain text */ }
        return esc(value);
    }
    function ownerName(id, name) { return Number(id) === config.userId ? 'Me' : (name || 'Unassigned'); }
    function ownerOptions(selected) {
        return (config.roster || []).map(u => `<option value="${u.id}" ${u.id === Number(selected) ? 'selected' : ''}>${esc(ownerName(u.id,u.name))}</option>`).join('');
    }
    async function loadLists() {
        const data = await api('/lists');
        function renderLists() {
            const owner = $('prospect-owner-filter')?.value || '';
            const lists = data.lists.filter(l => !owner || String(l.assigned_to_id || 'unassigned') === owner);
            $('prospect-lists').innerHTML = lists.length ? lists.map(l => `<a class="prospect-list-card" href="/crm/prospecting/${l.id}"><div><h2>${esc(l.name)}</h2><p>${l.row_count} leads · ${l.promoted_count} added to CRM</p><p>Assigned to ${esc(ownerName(l.assigned_to_id,l.assigned_to_name))} · Uploaded by ${esc(ownerName(l.added_by_id,l.added_by_name))}</p></div><span>${l.due_count ? `${l.due_count} follow-ups due` : 'Open list →'}</span></a>`).join('') : `<div class="prospect-empty"><h2>${data.lists.length ? 'No lists assigned to this person' : 'Your next good contact starts here'}</h2><p>Import a list, keep your research together, and track each conversation.</p><button class="cbtn cbtn-primary" type="button" id="prospect-first-import">Import a CSV</button></div>`;
            $('prospect-first-import')?.addEventListener('click', openImport);
        }
        if (config.isAdmin) {
            $('prospect-owner-filter-wrap').hidden=false;
            const owners = new Map((config.roster || []).map(u => [String(u.id),ownerName(u.id,u.name)]));
            data.lists.forEach(l => owners.set(String(l.assigned_to_id || 'unassigned'),ownerName(l.assigned_to_id,l.assigned_to_name)));
            $('prospect-owner-filter').innerHTML='<option value="">All team lists</option>' + options(Object.fromEntries(owners),'');
            $('prospect-owner-filter').onchange=renderLists;
        }
        renderLists();
    }
    function filters() {
        const f = $('prospect-filters').elements;
        return new URLSearchParams({q:f.q.value, status:f.status.value, sort:f.sort.value, due:String(f.due.checked), page:String(page)});
    }
    async function loadRows() {
        await Promise.all([...saves.values()]);
        const sequence = ++loadingSequence;
        $('prospect-workspace').setAttribute('aria-busy','true');
        try {
            const data = await api(`/lists/${config.listId}?${filters()}`);
            if (sequence !== loadingSequence) return;
            const first = !listing;
            listing = data.listing; rows = data.rows; page = data.page; pages = data.pages;
            $('prospect-title').textContent = listing.name;
            $('prospect-subtitle').textContent = `Imported from ${listing.filename}. Click a lead to open its research and outreach history.`;
            $('prospect-back').hidden = false; $('prospect-export').hidden = false;
            $('prospect-export').href = `/crm/prospecting/${listing.id}/export.csv`;
            $('prospect-workspace').hidden = false;
            $('prospect-owner-label').textContent = `Assigned to ${ownerName(listing.assigned_to_id,listing.assigned_to_name)} · Uploaded by ${ownerName(listing.added_by_id,listing.added_by_name)}`;
            if (config.isAdmin) {
                const select = $('prospect-assignment-form').elements.assigned_to_id;
                select.innerHTML = ownerOptions(listing.assigned_to_id);
                if (!(config.roster || []).some(u => u.id === listing.assigned_to_id)) {
                    select.insertAdjacentHTML('afterbegin',`<option value="" selected disabled>${esc(listing.assigned_to_name || 'Choose an owner')}</option>`);
                }
            }
            $('prospect-summary').innerHTML = [['total','leads'],['touched','contacted or attempted'],['due','follow-ups due'],['promoted','added to CRM']].map(([key, text]) => `<div><strong>${data.summary[key]}</strong><span>${text}</span></div>`).join('');
            if (first) initColumns();
            renderTable();
            $('prospect-page-label').textContent = `${data.total.toLocaleString()} matching leads · Page ${page} of ${pages}`;
            $('prospect-prev').disabled = page <= 1; $('prospect-next').disabled = page >= pages;
        } finally { if (sequence === loadingSequence) $('prospect-workspace').removeAttribute('aria-busy'); }
    }
    function initColumns() {
        visible = new Set(['phone','email','title','address'].map(f => listing.mapping[f]).filter(Boolean));
        if (!visible.size) listing.columns.slice(0,6).forEach(c => visible.add(c.id));
        try { const saved = JSON.parse(localStorage.getItem(`prospect-columns-${listing.id}`)); if (Array.isArray(saved)) visible = new Set(saved); } catch (_) {}
        $('prospect-columns').innerHTML = listing.columns.map(c => `<label><input type="checkbox" value="${c.id}" ${visible.has(c.id) ? 'checked' : ''}>${esc(c.label)}</label>`).join('');
        $('prospect-columns').onchange = async event => {
            try { await Promise.all([...saves.values()]); }
            catch (error) { event.target.checked = visible.has(event.target.value); notice(error.message); return; }
            event.target.checked ? visible.add(event.target.value) : visible.delete(event.target.value);
            try { localStorage.setItem(`prospect-columns-${listing.id}`, JSON.stringify([...visible])); } catch (_) {}
            renderTable();
        };
    }
    function renderTable() {
        const columns = listing.columns.filter(c => visible.has(c.id));
        const contactIds = [listing.mapping.phone, listing.mapping.email];
        const contactColumns = contactIds.map(id => columns.find(c => c.id === id)).filter((c,i,all) => c && all.indexOf(c) === i);
        const researchColumns = columns.filter(c => !contactIds.includes(c.id));
        const headers = cols => cols.map(c => `<th scope="col">${esc(c.label)}</th>`).join('');
        $('prospect-head').innerHTML = '<tr><th scope="col">Lead</th><th scope="col">Contact status</th>' + headers(contactColumns) + '<th scope="col">Last touch · New York</th><th scope="col" class="prospect-count-col">Touches</th><th scope="col">Next follow-up</th>' + headers(researchColumns) + '</tr>';
        $('prospect-rows').innerHTML = rows.length ? rows.map(row => {
            const locked = !!row.promoted_at;
            const cells = cols => cols.map(c => `<td><button type="button" class="prospect-cell" data-cell="${c.id}" data-id="${row.id}" title="${esc(row.cells[c.id])}" aria-label="Edit ${esc(c.label)} for ${esc(label(row))}" ${locked ? 'disabled' : ''}>${esc(row.cells[c.id]) || '—'}</button></td>`).join('');
            return `<tr data-row="${row.id}"><td><button class="prospect-open" data-open="${row.id}">${esc(label(row))}<small>${locked ? 'Added to CRM' : esc(field(row,'company') || `Row ${row.position}`)}</small></button></td>
                <td>${locked ? '<span>Added to CRM</span>' : `<select aria-label="Status for ${esc(label(row))}" data-status="${row.id}">${options(config.statuses,row.status)}</select>`}</td>
                ${cells(contactColumns)}<td><span>${esc(dateTime(row.last_touch_at))}</span></td><td class="prospect-count-col"><span>${row.touch_count}</span></td>
                <td>${locked ? `<span>${esc(row.next_follow_up || '—')}</span>` : `<input type="date" aria-label="Next follow-up for ${esc(label(row))}" data-follow-up="${row.id}" value="${esc(row.next_follow_up || '')}" ${['do_not_contact','not_interested'].includes(row.status) ? 'disabled' : ''}>`}</td>
                ${cells(researchColumns)}</tr>`;
        }).join('') : `<tr><td colspan="${5 + columns.length}"><span>No leads match these filters.</span></td></tr>`;
    }
    function saveRow(id, changes) {
        const previous = saves.get(id) || Promise.resolve();
        const pending = previous.catch(() => {}).then(async () => {
            const row = rows.find(r => r.id === id);
            const result = await api(`/rows/${id}`, {method:'PATCH', body:{version:row.version,...changes}});
            Object.assign(row, result.row);
            return row;
        });
        saves.set(id, pending);
        pending.finally(() => { if (saves.get(id) === pending) saves.delete(id); }).catch(() => {});
        return pending;
    }
    $('prospect-rows').addEventListener('change', async event => {
        const el = event.target, id = Number(el.dataset.status || el.dataset.followUp);
        if (!id) return;
        const row = rows.find(r => r.id === id), old = el.dataset.status ? row.status : (row.next_follow_up || '');
        el.disabled = true;
        try {
            await saveRow(id, el.dataset.status ? {status:el.value} : {next_follow_up:el.value});
            const due = el.closest('tr').querySelector('[data-follow-up]');
            due.value = row.next_follow_up || ''; due.disabled = ['do_not_contact','not_interested'].includes(row.status);
            notice('Saved.');
            await loadRows();
        } catch (error) { el.value = old; notice(error.message); }
        finally { if (el.dataset.status || !['do_not_contact','not_interested'].includes(row.status)) el.disabled = false; }
    });
    $('prospect-rows').addEventListener('click', async event => {
        const open = event.target.closest('[data-open]');
        if (open) { await openLead(Number(open.dataset.open)); return; }
        const button = event.target.closest('[data-cell]');
        if (!button || button.disabled) return;
        const id = Number(button.dataset.id), cid = button.dataset.cell, row = rows.find(r => r.id === id);
        const textarea = document.createElement('textarea');
        textarea.value = row.cells[cid]; textarea.maxLength = 10000; textarea.setAttribute('aria-label',button.getAttribute('aria-label'));
        button.replaceWith(textarea); textarea.focus(); let saving = false, done = false;
        async function finish(cancel = false) {
            if (saving || done) return;
            if (!cancel && textarea.value !== row.cells[cid]) {
                saving = true; textarea.disabled = true;
                try { await saveRow(id, {cells:{[cid]:textarea.value}}); notice('Cell saved.'); }
                catch (error) { notice(error.message); textarea.disabled = false; saving = false; textarea.focus(); return; }
            }
            done = true; button.textContent = row.cells[cid] || '—'; button.title = row.cells[cid]; textarea.replaceWith(button);
            const openButton = button.closest('tr').querySelector('[data-open]');
            openButton.innerHTML = `${esc(label(row))}<small>${esc(field(row,'company') || `Row ${row.position}`)}</small>`;
        }
        textarea.addEventListener('blur', () => finish());
        textarea.addEventListener('keydown', event => {
            if (event.key === 'Escape') { event.preventDefault(); finish(true).then(() => button.focus()); }
            if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); finish().then(() => {if(done) button.focus();}); }
        });
    });
    $('prospect-filters').addEventListener('submit', e => {e.preventDefault(); page=1; loadRows().catch(e => notice(e.message));});
    $('prospect-prev').onclick = () => {page--; loadRows().catch(e => notice(e.message));};
    $('prospect-next').onclick = () => {page++; loadRows().catch(e => notice(e.message));};
    $('prospect-filters').elements.status.insertAdjacentHTML('beforeend',options(config.statuses,''));

    function openImport() { $('prospect-import-dialog').showModal(); }
    $('prospect-import').onclick = openImport;
    const uploadForm = $('prospect-upload-form'), importForm = $('prospect-import-form');
    if (config.isAdmin) {
        $('prospect-import-owner').innerHTML = ownerOptions(config.userId);
        $('prospect-assignment-form').addEventListener('submit', event => {
            event.preventDefault(); const form=event.target;
            busy(form,'prospect-assignment-error',async () => {
                await Promise.all([...saves.values()]);
                const result=await api(`/lists/${listing.id}/assignment`,{method:'PATCH',body:{
                    assigned_to_id:Number(form.elements.assigned_to_id.value),version:listing.version}});
                await loadRows();
                notice(`List assigned to ${ownerName(result.listing.assigned_to_id,result.listing.assigned_to_name)}. Research and touch history preserved.`);
            });
        });
    }
    uploadForm.addEventListener('change', () => { preview = null; $('prospect-preview').hidden=true; });
    function uploadBody() {
        const data = new FormData(); data.set('file', uploadFile);
        data.set('delimiter', uploadForm.elements.delimiter.value);
        data.set('has_header', String(uploadForm.elements.has_header.checked)); return data;
    }
    uploadForm.addEventListener('submit', event => {
        event.preventDefault();
        busy(uploadForm,'prospect-import-error',async () => {
            uploadFile = uploadForm.elements.file.files[0];
            preview = null; $('prospect-preview').hidden=true;
            if (!uploadFile || uploadFile.size > 10*1024*1024) throw new Error('Choose a CSV or TSV file up to 10 MB.');
            preview = await api('/preview',{method:'POST',body:uploadBody()}); importKey = crypto.randomUUID();
            importForm.elements.name.value = uploadFile.name.replace(/\.[^.]+$/,'').replace(/[_-]+/g,' ').slice(0,200);
            $('prospect-preview-summary').textContent = `${preview.row_count.toLocaleString()} leads · ${preview.columns.length} columns${preview.duplicate_count ? ` · ${preview.duplicate_count} identical rows will be kept for review` : ''}`;
            $('prospect-mapping').innerHTML = Object.entries(config.fields).map(([field,label]) => `<label>${esc(label)}<select name="map_${field}"><option value="">Not mapped</option>${preview.columns.map(c => `<option value="${c.id}" ${preview.mapping[field] === c.id ? 'selected' : ''}>${esc(c.label)} (column ${Number(c.id.slice(1))+1})</option>`).join('')}</select></label>`).join('');
            $('prospect-preview-table').innerHTML = '<table><thead><tr>' + preview.columns.map(c => `<th>${esc(c.label)}</th>`).join('') + '</tr></thead><tbody>' + preview.rows.map(r => '<tr>' + preview.columns.map(c => `<td>${esc(r[c.id])}</td>`).join('') + '</tr>').join('') + '</tbody></table>';
            $('prospect-preview').hidden=false; importForm.elements.name.focus();
        });
    });
    importForm.addEventListener('submit', event => {
        event.preventDefault();
        busy(importForm,'prospect-import-error',async () => {
            if (!preview) throw new Error('Preview the file before importing.');
            const data = uploadBody(), mapping = {};
            Object.keys(config.fields).forEach(field => { mapping[field] = importForm.elements[`map_${field}`].value; });
            data.set('mapping',JSON.stringify(mapping)); data.set('name',importForm.elements.name.value); data.set('import_key',importKey);
            if (config.isAdmin) data.set('assigned_to_id',importForm.elements.assigned_to_id.value);
            const result = await api('/import',{method:'POST',body:data});
            window.location.assign(`/crm/prospecting/${result.list_id}`);
        });
    });
    async function openLead(id, keepOpen=false) {
        try {
            if (saves.has(id)) await saves.get(id);
            const detail = await api(`/rows/${id}`); rowDetail = detail;
            const row = detail.row, f = detail.fields;
            touchKey = crypto.randomUUID();
            $('prospect-lead-title').textContent = f.name || f.company || f.address || `Lead ${row.position}`;
            $('prospect-lead-error').textContent = '';
            const research = `<details><summary>All imported columns (${detail.listing.columns.length})</summary><dl class="prospect-research">${detail.listing.columns.map(c => `<dt>${esc(c.label)}</dt><dd>${safeLink(row.cells[c.id]) || '—'}</dd>`).join('')}</dl></details>`;
            const history = `<h3>Touch history · ${row.touch_count}</h3>${detail.touches.length ? detail.touches.map(t => `<div class="prospect-touch"><strong>${esc(config.methods[t.method])} · ${esc(config.outcomes[t.outcome])}</strong><p>${esc(t.note)}</p><small>${esc(dateTime(t.occurred_at))} · New York</small></div>`).join('') : '<p>No outreach logged yet.</p>'}`;
            if (row.promoted_at) {
                $('prospect-lead-body').innerHTML = `<p>This lead’s research, notes, touches, and follow-up were carried into CRM.</p>${row.promoted_contact_id ? `<a class="cbtn cbtn-primary" href="/crm/contacts/${row.promoted_contact_id}">Open CRM contact</a>` : `<p>${row.crm_contact_restricted ? 'This CRM contact is assigned to another person. Ask your team admin to review its assignment.' : 'The CRM contact has since been deleted.'}</p>`}${research}<p>${esc(row.notes)}</p>${history}`;
            } else {
                $('prospect-lead-body').innerHTML = `<form id="prospect-notes-form" class="prospect-form"><div class="prospect-form-grid"><label>Status<select name="status">${options(config.statuses,row.status)}</select></label><label>Next follow-up<input type="date" name="next_follow_up" value="${esc(row.next_follow_up || '')}"></label></div><label>Working notes<textarea name="notes" maxlength="20000">${esc(row.notes)}</textarea></label><button class="cbtn" type="submit">Save notes &amp; status</button></form>${research}
                    <h3>Log a touch</h3>${row.status === 'do_not_contact' ? '<p>This lead is marked Do not contact. Review its status before logging outreach.</p>' : `<form id="prospect-touch-form" class="prospect-form"><div class="prospect-form-grid"><label>Method<select name="method">${options(config.methods,'call')}</select></label><label>Outcome<select name="outcome">${options(config.outcomes,'no_answer')}</select></label><label>When (New York time)<input type="datetime-local" name="occurred_at" value="${nyInputTime()}" required></label><label>Status after touch<select name="status">${options(config.statuses,row.status==='new' ? 'attempted' : row.status)}</select></label></div><label>Conversation notes<textarea name="note" maxlength="20000"></textarea></label><label>Next follow-up<input type="date" name="next_follow_up" value="${esc(row.next_follow_up || '')}"></label><button type="submit" class="cbtn cbtn-primary">Save touch</button></form>`}
                    ${history}<div class="prospect-promote"><details><summary>Ready for CRM? Review &amp; add contact</summary><p>The original list stays here. Research, notes, touch history, and the next follow-up carry over. A matching name and email or phone links to the existing contact.</p><form id="prospect-promote-form" class="prospect-form"><div class="prospect-form-grid">${['name','company','title','email','phone','secondary_phone'].map(field => `<label>${esc(config.fields[field])}<input name="${field}" value="${esc(f[field])}" maxlength="${['title','phone','secondary_phone'].includes(field)?150:255}" ${field==='name'?'required':''} ${field==='email'?'type="email"':''}></label>`).join('')}</div><button type="submit" class="cbtn cbtn-primary" ${row.status==='do_not_contact'?'disabled':''}>Add to CRM</button></form></details></div>`;
                bindLeadForms(id);
            }
            if (!keepOpen && !$('prospect-lead-dialog').open) $('prospect-lead-dialog').showModal();
        } catch (error) { keepOpen ? $('prospect-lead-error').textContent = error.message : notice(error.message); }
    }
    function bindLeadForms(id) {
        $('prospect-notes-form').addEventListener('submit', event => {
            event.preventDefault(); const form=event.target;
            busy(form,'prospect-lead-error',async () => {
                if ($('prospect-touch-form')?.elements.note.value) throw new Error('Save the pending touch first. Your working notes will be saved with it.');
                const data=Object.fromEntries(new FormData(form));
                await api(`/rows/${id}`, {method:'PATCH',body:{...data,version:rowDetail.row.version}});
                await openLead(id,true); await loadRows(); notice('Notes and status saved.');
            });
        });
        const touchForm=$('prospect-touch-form');
        if (touchForm) {
            touchForm.elements.outcome.addEventListener('change', () => {
                touchForm.elements.status.value = ({spoke:'connected',meeting_set:'interested',not_interested:'not_interested',callback_requested:'nurture'})[touchForm.elements.outcome.value] || 'attempted';
            });
            touchForm.addEventListener('submit', event => {
                event.preventDefault();
                busy(touchForm,'prospect-lead-error',async () => {
                    const data=Object.fromEntries(new FormData(touchForm));
                    await saveWorkingNotes(id);
                    await api(`/rows/${id}/touches`, {method:'POST',body:{...data,version:rowDetail.row.version,request_key:touchKey}});
                    await openLead(id,true); await loadRows(); notice('Touch saved.');
                });
            });
        }
        $('prospect-promote-form').addEventListener('submit', event => {
            event.preventDefault(); const form=event.target;
            busy(form,'prospect-lead-error',async () => {
                if ($('prospect-touch-form')?.elements.note.value) throw new Error('Save the pending touch before adding this lead to CRM.');
                await saveWorkingNotes(id);
                const result=await api(`/rows/${id}/promote`, {method:'POST',body:{...Object.fromEntries(new FormData(form)),version:rowDetail.row.version}});
                await openLead(id,true); await loadRows();
                notice(result.existing ? 'Linked to the matching CRM contact. Research and outreach history carried over.' : 'Contact added to CRM with research and outreach history.');
            });
        });
    }
    (config.listId ? loadRows() : loadLists()).catch(error => notice(error.message));
})();
