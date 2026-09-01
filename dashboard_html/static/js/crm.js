// CRM v2 interactions.
// Principles: no full reloads after common actions (partials refresh in
// place), everything keyboard-reachable, sheets instead of modals, one
// delegated handler tree so freshly swapped markup just works.

(function () {
    'use strict';

    // ---------- tiny utils ----------

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $all = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    function esc(value) {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    function toast(message, type) {
        const stack = $('#crmToasts');
        if (!stack) return;
        const icons = { success: 'fa-circle-check', error: 'fa-triangle-exclamation', info: 'fa-circle-info', warning: 'fa-circle-exclamation' };
        const el = document.createElement('div');
        el.className = 'crm-toast is-' + (type || 'info');
        el.innerHTML = '<i class="fas ' + (icons[type] || icons.info) + '"></i><span>' + esc(message) + '</span>';
        stack.appendChild(el);
        setTimeout(() => { el.classList.add('leaving'); setTimeout(() => el.remove(), 220); }, 3000);
    }

    async function post(path, body) {
        const res = await fetch(path, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        let data = {};
        try { data = await res.json(); } catch (e) { /* non-JSON */ }
        if (!res.ok && !data.error) data.error = 'Request failed (' + res.status + ')';
        data._status = res.status;
        return data;
    }

    async function getJSON(path) {
        const res = await fetch(path);
        try { return await res.json(); } catch (e) { return { success: false }; }
    }

    function busy(btn, on) {
        if (!btn) return;
        if (on) {
            btn.dataset.origHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner spinner-sm" style="border-top-color:currentColor;border-color:rgba(127,127,127,.35);border-top-color:currentColor"></span>';
        } else {
            btn.disabled = false;
            if (btn.dataset.origHtml) btn.innerHTML = btn.dataset.origHtml;
        }
    }

    function localISODate(offsetDays) {
        const d = new Date();
        d.setDate(d.getDate() + (offsetDays || 0));
        return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
    }

    function selectChip(container, button) {
        $all('.crm-choice', container).forEach(b => b.classList.remove('is-selected'));
        if (button) button.classList.add('is-selected');
    }
    function chipValue(container) {
        const chosen = $('.crm-choice.is-selected', container);
        return chosen ? chosen.dataset.value : null;
    }

    // ---------- in-place refresh of [data-partial] regions ----------

    async function refreshPartials() {
        const regions = $all('[data-partial]');
        if (!regions.length) { location.reload(); return; }
        await Promise.all(regions.map(async (el) => {
            try {
                const res = await fetch(el.dataset.partial, { headers: { 'X-Requested-With': 'fetch' } });
                if (!res.ok) return;
                const html = await res.text();
                el.style.transition = 'opacity 120ms';
                el.style.opacity = '0.55';
                el.innerHTML = html;
                requestAnimationFrame(() => { el.style.opacity = '1'; });
            } catch (e) { /* keep stale markup */ }
        }));
        document.dispatchEvent(new CustomEvent('crm:refreshed'));
    }

    // After a write: refresh what can be refreshed in place, else reload.
    function changed() {
        if (window.CRM_FOCUS) { document.dispatchEvent(new CustomEvent('crm:focus-changed')); return; }
        refreshPartials();
    }

    // ---------- theme ----------

    function applyTheme(pref) {
        const dark = pref === 'dark' || (pref === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
        document.documentElement.toggleAttribute('data-crm-theme', dark);
        if (dark) document.documentElement.setAttribute('data-crm-theme', 'dark');
        document.documentElement.setAttribute('data-crm-pref', pref);
        $all('.js-theme button').forEach(b => b.classList.toggle('is-active', b.dataset.theme === pref));
    }
    (function initTheme() {
        let pref = 'auto';
        try { pref = localStorage.getItem('crm-theme') || 'auto'; } catch (e) { /* no storage */ }
        applyTheme(pref);
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            let p = 'auto';
            try { p = localStorage.getItem('crm-theme') || 'auto'; } catch (e) { /* */ }
            if (p === 'auto') applyTheme('auto');
        });
    })();

    // ---------- sheets ----------

    function resetButtons(root) {
        $all('button[disabled]', root).forEach(b => {
            b.disabled = false;
            if (b.dataset.origHtml) { b.innerHTML = b.dataset.origHtml; delete b.dataset.origHtml; }
        });
    }

    function openSheet(id) {
        const sheet = document.getElementById(id);
        if (!sheet) return null;
        resetButtons(sheet);
        sheet.classList.add('is-open');
        const first = $('input:not([type=hidden]):not([type=checkbox]), textarea, select', sheet);
        if (first && window.innerWidth > 860) setTimeout(() => first.focus(), 30);
        return sheet;
    }
    function closeSheet(sheet) { if (sheet) sheet.classList.remove('is-open'); }
    function closeAllSheets() { $all('.crm-sheet.is-open').forEach(closeSheet); closePalette(); }

    document.addEventListener('click', (e) => {
        const closer = e.target.closest('.js-sheet-close');
        if (closer) { closeSheet(closer.closest('.crm-sheet')); return; }
        if (e.target.classList.contains('crm-sheet')) closeSheet(e.target);
        if (e.target.classList.contains('crm-palette')) closePalette();
        const opener = e.target.closest('.js-open-sheet');
        if (opener) { e.preventDefault(); openSheet(opener.dataset.sheet); }
    });

    // ---------- Contacted ----------

    const sContacted = $('#sheetContacted');
    let touch = null;
    let nudged = false;

    function openContacted(trigger) {
        if (!sContacted) return;
        const d = trigger.dataset;
        touch = {
            buildingId: d.buildingId || null, contactId: d.contactId || null,
            entity: d.entity || 'this lead', phone: d.phone || null,
            followupId: d.followupId || null, lastTouch: d.lastTouch || '',
        };
        nudged = false;
        $('[data-role="entity"]', sContacted).textContent = touch.entity;
        $('[data-role="phone-line"]', sContacted).textContent = touch.phone ? '· ' + touch.phone : '';
        const collision = $('[data-role="collision"]', sContacted);
        if (touch.lastTouch) {
            collision.innerHTML = '<i class="fas fa-circle-exclamation"></i><span>Heads up — ' + esc(touch.lastTouch) + '. Worth checking before calling again.</span>';
            collision.hidden = false;
        } else { collision.hidden = true; }
        selectChip($('[data-role="methods"]', sContacted), $('[data-role="methods"] .crm-choice', sContacted));
        selectChip($('[data-role="outcomes"]', sContacted), null);
        $('[data-role="note"]', sContacted).value = '';
        $('[data-role="nudge"]', sContacted).hidden = true;
        $('[data-role="save"]', sContacted).innerHTML = '<i class="fas fa-phone"></i> Save';
        $('[data-role="step1"]', sContacted).hidden = false;
        $('[data-role="step2"]', sContacted).hidden = true;
        $('[data-role="step3"]', sContacted).hidden = true;
        $('[data-role="fu-date"]', sContacted).value = localISODate(1);
        openSheet('sheetContacted');
        $('[data-role="note"]', sContacted).focus();
    }

    if (sContacted) {
        $all('[data-role="methods"] .crm-choice, [data-role="outcomes"] .crm-choice', sContacted).forEach(btn => {
            btn.addEventListener('click', () => {
                const container = btn.parentElement;
                if (btn.classList.contains('is-selected') && container.dataset.role === 'outcomes') btn.classList.remove('is-selected');
                else selectChip(container, btn);
            });
        });
        $('[data-role="save"]', sContacted).addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const note = $('[data-role="note"]', sContacted).value.trim();
            if (!note && !nudged) {
                nudged = true;
                $('[data-role="nudge"]', sContacted).hidden = false;
                btn.textContent = 'Save without note';
                return;
            }
            busy(btn, true);
            const data = await post('/crm/api/contacted', {
                building_id: touch.buildingId, contact_id: touch.contactId,
                method: chipValue($('[data-role="methods"]', sContacted)) || 'call',
                outcome: chipValue($('[data-role="outcomes"]', sContacted)),
                note, phone: touch.phone, complete_followup_id: touch.followupId,
            });
            busy(btn, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            document.dispatchEvent(new CustomEvent('crm:touch-logged', { detail: touch }));
            $('[data-role="step1"]', sContacted).hidden = true;
            $('[data-role="step2"]', sContacted).hidden = false;
        });
        async function followupThenFinish(days, dueDate) {
            const payload = { building_id: touch.buildingId, contact_id: touch.contactId, title: 'Follow up with ' + touch.entity };
            if (dueDate) payload.due_date = dueDate; else payload.days = days;
            const data = await post('/crm/api/followup', payload);
            finish(data.success ? 'Touch logged, follow-up set.' : 'Touch logged (follow-up failed).');
        }
        function finish(message) {
            $('[data-role="step2"]', sContacted).hidden = true;
            $('[data-role="step3"]', sContacted).hidden = false;
            $('[data-role="done-msg"]', sContacted).textContent = message;
            setTimeout(() => { closeSheet(sContacted); changed(); }, 600);
        }
        $all('[data-role="fu-quick"] .crm-choice', sContacted).forEach(btn => btn.addEventListener('click', () => followupThenFinish(parseInt(btn.dataset.days, 10))));
        $('[data-role="fu-date-save"]', sContacted).addEventListener('click', () => {
            const v = $('[data-role="fu-date"]', sContacted).value;
            if (!v) { toast('Pick a date first', 'warning'); return; }
            followupThenFinish(null, v);
        });
        $('[data-role="fu-skip"]', sContacted).addEventListener('click', () => finish('Touch logged.'));
    }

    // ---------- Visit ----------

    const sVisit = $('#sheetVisit');
    let visitBuilding = null;
    function openVisit(trigger) {
        visitBuilding = trigger.dataset.buildingId;
        $('[data-role="entity"]', sVisit).textContent = trigger.dataset.entity || 'this building';
        $('[data-role="visit-date"]', sVisit).value = localISODate(0);
        $('[data-role="note"]', sVisit).value = '';
        openSheet('sheetVisit');
    }
    if (sVisit) {
        $('[data-role="save"]', sVisit).addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            busy(btn, true);
            const data = await post('/crm/api/visit', {
                building_id: visitBuilding,
                visited_on: $('[data-role="visit-date"]', sVisit).value,
                note: $('[data-role="note"]', sVisit).value.trim(),
            });
            busy(btn, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeSheet(sVisit); toast('Visit logged', 'success'); changed();
        });
    }

    // ---------- Follow-up (create / edit) ----------

    const sFollowup = $('#sheetFollowup');
    let fu = null;
    async function fillRoster(select, selectedId) {
        if (!select) return;
        const data = await getJSON('/crm/api/roster');
        if (!data.success) return;
        select.innerHTML = '<option value="">Me</option>' + data.roster.map(u =>
            `<option value="${u.id}" ${String(u.id) === String(selectedId) ? 'selected' : ''}>${esc(u.name)}</option>`).join('');
    }
    function openFollowup(trigger, editData) {
        if (!sFollowup) return;
        const d = trigger ? trigger.dataset : {};
        fu = editData ? { id: editData.id } : { buildingId: d.buildingId || null, contactId: d.contactId || null };
        $('[data-role="title-text"]', sFollowup).textContent = editData ? 'Edit follow-up' : 'Set a follow-up';
        $('[data-role="entity-wrap"]', sFollowup).style.display = editData ? 'none' : '';
        $('[data-role="entity"]', sFollowup).textContent = d.entity || (window.CRM_RECORD && window.CRM_RECORD.entity) || 'this';
        $('[data-role="title"]', sFollowup).value = editData ? editData.title : '';
        $('[data-role="note"]', sFollowup).value = editData ? editData.note : '';
        $('[data-role="fu-date"]', sFollowup).value = editData ? editData.due : localISODate(1);
        $('[data-role="save"]', sFollowup).textContent = editData ? 'Save changes' : 'Set follow-up';
        $('[data-role="delete"]', sFollowup).classList.toggle('js-hidden', !editData);
        selectChip($('[data-role="fu-quick"]', sFollowup), editData ? null : $('[data-role="fu-quick"] .crm-choice', sFollowup));
        fillRoster($('[data-role="assignee"]', sFollowup), editData ? editData.assignee : '');
        openSheet('sheetFollowup');
    }
    if (sFollowup) {
        $all('[data-role="fu-quick"] .crm-choice', sFollowup).forEach(btn => btn.addEventListener('click', () => {
            selectChip(btn.parentElement, btn);
            $('[data-role="fu-date"]', sFollowup).value = localISODate(parseInt(btn.dataset.days, 10));
        }));
        $('[data-role="save"]', sFollowup).addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const due = $('[data-role="fu-date"]', sFollowup).value;
            if (!due) { toast('Pick a date', 'warning'); return; }
            busy(btn, true);
            const body = {
                title: $('[data-role="title"]', sFollowup).value.trim() || 'Follow up',
                note: $('[data-role="note"]', sFollowup).value.trim(),
                due_date: due,
                assigned_to_id: $('[data-role="assignee"]', sFollowup).value || null,
            };
            let data;
            if (fu.id) data = await post('/crm/api/followup/' + fu.id + '/update', body);
            else data = await post('/crm/api/followup', Object.assign(body, { building_id: fu.buildingId, contact_id: fu.contactId }));
            busy(btn, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeSheet(sFollowup); toast(fu.id ? 'Follow-up updated' : 'Follow-up set', 'success'); changed();
        });
        $('[data-role="delete"]', sFollowup).addEventListener('click', async () => {
            if (!fu.id || !confirm('Delete this follow-up?')) return;
            const data = await post('/crm/api/followup/' + fu.id + '/delete');
            if (data.success) { closeSheet(sFollowup); changed(); } else toast(data.error || 'Failed', 'error');
        });
    }

    // ---------- Add to list (single or bulk) ----------

    const sList = $('#sheetList');
    let listTarget = null;
    async function openListSheet(target, label) {
        listTarget = target;
        $('[data-role="entity"]', sList).textContent = label;
        $('[data-role="new-list"]', sList).value = '';
        const select = $('[data-role="list-select"]', sList);
        select.innerHTML = '<option value="">Choose a list…</option>';
        openSheet('sheetList');
        const data = await getJSON('/crm/api/lists');
        if (data.success) select.innerHTML += data.lists.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join('');
    }
    if (sList) {
        $('[data-role="save"]', sList).addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const listId = $('[data-role="list-select"]', sList).value;
            const newName = $('[data-role="new-list"]', sList).value.trim();
            if (!listId && !newName) { toast('Pick a list or name a new one', 'warning'); return; }
            busy(btn, true);
            let data;
            if (listTarget.bulkIds) {
                data = await post('/crm/api/buildings/bulk', { ids: listTarget.bulkIds, action: 'list', value: listId || null, new_list_name: newName || null });
            } else {
                data = await post('/crm/api/list-item', { list_id: listId || null, new_list_name: newName || null, building_id: listTarget.buildingId, contact_id: listTarget.contactId });
            }
            busy(btn, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeSheet(sList); toast('Added to list', 'success');
            if (listTarget.bulkIds) { clearBulk(); } else { changed(); }
        });
    }

    // ---------- Person (add / edit) ----------

    const sPerson = $('#sheetPerson');
    let person = null;
    const PERSON_FIELDS = ['name', 'title-field', 'email', 'company', 'phone', 'phone-ext', 'phone-label', 'source-detail'];

    function openPerson(trigger, editData) {
        person = editData ? { id: editData.id } : { buildingId: (trigger && trigger.dataset.buildingId) || null, force: false };
        $('[data-role="title-text"]', sPerson).textContent = editData ? 'Edit person' : 'Add a person';
        $('[data-role="add-only"]', sPerson).style.display = editData ? 'none' : '';
        $('[data-role="dup-warning"]', sPerson).hidden = true;
        $('[data-role="building-role-wrap"]', sPerson).style.display = (!editData && person.buildingId) ? '' : 'none';
        $('[data-role="save-another"]', sPerson).classList.toggle('js-hidden', !!editData);
        const set = (role, v) => { $('[data-role="' + role + '"]', sPerson).value = v || ''; };
        PERSON_FIELDS.forEach(f => set(f, ''));
        if (editData) {
            set('name', editData.name); set('title-field', editData.title);
            set('email', editData.email); set('company', editData.company);
        }
        $('[data-role="save"]', sPerson).textContent = editData ? 'Save changes' : 'Add person';
        $('[data-role="save-another"]', sPerson).textContent = 'Save & add another';
        openSheet('sheetPerson');
    }

    function resetPersonForm() {
        person.force = false;
        PERSON_FIELDS.forEach(f => { $('[data-role="' + f + '"]', sPerson).value = ''; });
        $('[data-role="dup-warning"]', sPerson).hidden = true;
        $('[data-role="save"]', sPerson).textContent = 'Add person';
        $('[data-role="save-another"]', sPerson).textContent = 'Save & add another';
        $('[data-role="name"]', sPerson).focus();
    }

    async function savePerson(btn, keepOpen) {
        const name = $('[data-role="name"]', sPerson).value.trim();
        if (!name) { toast('A name is required', 'warning'); $('[data-role="name"]', sPerson).focus(); return; }
        const fields = {
            name, title: $('[data-role="title-field"]', sPerson).value,
            email: $('[data-role="email"]', sPerson).value, company: $('[data-role="company"]', sPerson).value,
        };
        busy(btn, true);
        let data;
        if (person.id) {
            data = await post('/crm/api/contact/' + person.id + '/update', fields);
        } else {
            data = await post('/crm/api/contact', Object.assign(fields, {
                phone: $('[data-role="phone"]', sPerson).value,
                phone_ext: $('[data-role="phone-ext"]', sPerson).value,
                phone_label: $('[data-role="phone-label"]', sPerson).value,
                source_detail: $('[data-role="source-detail"]', sPerson).value,
                building_id: person.buildingId,
                building_role: $('[data-role="building-role"]', sPerson).value,
                force: person.force,
            }));
        }
        busy(btn, false);
        if (data.duplicate) {
            person.force = true;
            const warn = $('[data-role="dup-warning"]', sPerson);
            warn.innerHTML = '<i class="fas fa-circle-exclamation"></i><span>' + esc(data.error) +
                (data.matches && data.matches.length ? ' — <a href="/crm/contacts/' + data.matches[0].id + '">open theirs</a>' : '') +
                '. Press again if this really is a different person.</span>';
            warn.hidden = false;
            btn.textContent = keepOpen ? 'Add anyway & continue' : 'Add anyway';
            return;
        }
        if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
        if (keepOpen) {
            toast(name + ' added — next one', 'success');
            resetPersonForm();
            refreshPartials();
            return;
        }
        closeSheet(sPerson);
        if (person.id || person.buildingId) { toast('Saved', 'success'); changed(); }
        else if (data.contact_id) { window.location.href = '/crm/contacts/' + data.contact_id; }
        else { changed(); }
    }

    if (sPerson) {
        $('[data-role="save"]', sPerson).addEventListener('click', (e) => savePerson(e.currentTarget, false));
        $('[data-role="save-another"]', sPerson).addEventListener('click', (e) => savePerson(e.currentTarget, true));
    }

    // ---------- Phone (add / edit) ----------

    const sPhone = $('#sheetPhone');
    let phone = null;
    function openPhone(trigger, editData) {
        phone = editData ? { id: editData.id } : { contactId: trigger.dataset.contactId, force: false };
        $('[data-role="title-text"]', sPhone).textContent = editData ? 'Edit number' : 'Add a number';
        $('[data-role="entity"]', sPhone).textContent = editData ? editData.number : (trigger.dataset.entity || 'this person');
        $('[data-role="dup-warning"]', sPhone).hidden = true;
        $('[data-role="number-wrap"]', sPhone).style.display = editData ? 'none' : '';
        $('[data-role="source-wrap"]', sPhone).style.display = editData ? 'none' : '';
        $('[data-role="phone"]', sPhone).value = '';
        $('[data-role="phone-ext"]', sPhone).value = editData ? (editData.ext || '') : '';
        $('[data-role="phone-label"]', sPhone).value = editData ? editData.label : '';
        $('[data-role="source-detail"]', sPhone).value = '';
        $('[data-role="make-primary"]', sPhone).checked = editData ? editData.primary : false;
        $('[data-role="delete"]', sPhone).classList.toggle('js-hidden', !editData);
        $('[data-role="save"]', sPhone).textContent = editData ? 'Save' : 'Add number';
        openSheet('sheetPhone');
    }
    if (sPhone) {
        $('[data-role="save"]', sPhone).addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            busy(btn, true);
            let data;
            if (phone.id) {
                data = await post('/crm/api/phone/' + phone.id + '/update', {
                    label: $('[data-role="phone-label"]', sPhone).value,
                    extension: $('[data-role="phone-ext"]', sPhone).value,
                    make_primary: $('[data-role="make-primary"]', sPhone).checked,
                });
            } else {
                data = await post('/crm/api/phone', {
                    contact_id: phone.contactId, number: $('[data-role="phone"]', sPhone).value,
                    extension: $('[data-role="phone-ext"]', sPhone).value,
                    label: $('[data-role="phone-label"]', sPhone).value,
                    source_detail: $('[data-role="source-detail"]', sPhone).value,
                    make_primary: $('[data-role="make-primary"]', sPhone).checked, force: phone.force,
                });
            }
            busy(btn, false);
            if (data.duplicate) {
                phone.force = true;
                const warn = $('[data-role="dup-warning"]', sPhone);
                warn.innerHTML = '<i class="fas fa-circle-exclamation"></i><span>' + esc(data.error) + '. Press <strong>Add anyway</strong> to keep it here too.</span>';
                warn.hidden = false;
                btn.textContent = 'Add anyway';
                return;
            }
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeSheet(sPhone); toast('Saved', 'success'); changed();
        });
        $('[data-role="delete"]', sPhone).addEventListener('click', async () => {
            if (!phone.id || !confirm('Delete this number?')) return;
            const data = await post('/crm/api/phone/' + phone.id + '/delete');
            if (data.success) { closeSheet(sPhone); changed(); } else toast(data.error || 'Failed', 'error');
        });
    }

    // ---------- Edit building ----------

    const sBuilding = $('#sheetBuilding');
    let editingBuilding = null;
    function openBuildingEdit(data) {
        editingBuilding = data.id;
        $all('[data-field]', sBuilding).forEach(input => { input.value = data[input.dataset.field] == null ? '' : data[input.dataset.field]; });
        openSheet('sheetBuilding');
    }
    if (sBuilding) {
        $('[data-role="save"]', sBuilding).addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            const fields = {};
            $all('[data-field]', sBuilding).forEach(input => { fields[input.dataset.field] = input.value; });
            if (!fields.address.trim()) { toast('Address is required', 'warning'); return; }
            busy(btn, true);
            const data = await post('/crm/api/building/' + editingBuilding + '/update', fields);
            busy(btn, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeSheet(sBuilding); toast('Building updated', 'success'); changed();
        });
    }

    // ---------- Merge ----------

    const sMerge = $('#sheetMerge');
    let merge = null;
    let mergeTimer = null;
    function renderMergeResults(rows) {
        const box = $('[data-role="merge-results"]', sMerge);
        const list = rows.filter(r => String(r.id) !== String(merge.sourceId));
        if (!list.length) { box.innerHTML = '<div class="crm-palette__empty">No matches</div>'; return; }
        box.innerHTML = list.map(r =>
            `<div class="crm-cell crm-cell--link js-merge-pick" data-id="${r.id}" data-name="${esc(r.name)}">
                <div class="crm-cell__body"><div class="crm-cell__title">${esc(r.name)}</div><div class="crm-cell__sub">${esc(r.company || r.title || '')}</div></div>
                <span class="crm-cell__chevron"><i class="fas fa-chevron-right"></i></span></div>`).join('');
    }
    function pickMergeTarget(id, name) {
        merge.targetId = id;
        $('[data-role="merge-summary"]', sMerge).innerHTML = '<i class="fas fa-code-merge"></i><span>Everything on <strong>' + esc(merge.sourceName) + '</strong> moves to <strong>' + esc(name) + '</strong>, and the duplicate is deleted.</span>';
        $('[data-role="merge-summary"]', sMerge).hidden = false;
        $('[data-role="save"]', sMerge).disabled = false;
        $all('.js-merge-pick', sMerge).forEach(el => el.style.background = String(el.dataset.id) === String(id) ? 'var(--c-blue-tint)' : '');
    }
    function openMerge(trigger) {
        merge = { sourceId: trigger.dataset.contactId, sourceName: trigger.dataset.entity, targetId: null };
        $('[data-role="source-name"]', sMerge).textContent = merge.sourceName;
        $('[data-role="merge-search"]', sMerge).value = '';
        $('[data-role="merge-results"]', sMerge).innerHTML = '<div class="crm-palette__empty">Type a name or number to find the record to keep.</div>';
        $('[data-role="merge-summary"]', sMerge).hidden = true;
        $('[data-role="save"]', sMerge).disabled = true;
        openSheet('sheetMerge');
        if (trigger.dataset.targetId) {
            $('[data-role="merge-results"]', sMerge).innerHTML = `<div class="crm-cell crm-cell--link js-merge-pick" data-id="${trigger.dataset.targetId}" data-name="${esc(trigger.dataset.targetName)}"><div class="crm-cell__body"><div class="crm-cell__title">${esc(trigger.dataset.targetName)}</div><div class="crm-cell__sub">Suggested — shares a phone number</div></div></div>`;
            pickMergeTarget(trigger.dataset.targetId, trigger.dataset.targetName);
        }
    }
    if (sMerge) {
        $('[data-role="merge-search"]', sMerge).addEventListener('input', (e) => {
            clearTimeout(mergeTimer);
            const q = e.target.value.trim();
            mergeTimer = setTimeout(async () => {
                if (q.length < 2) return;
                const data = await getJSON('/crm/api/search?q=' + encodeURIComponent(q));
                if (data.success) renderMergeResults(data.contacts || []);
            }, 160);
        });
        sMerge.addEventListener('click', (e) => {
            const pick = e.target.closest('.js-merge-pick');
            if (pick) pickMergeTarget(pick.dataset.id, pick.dataset.name);
        });
        $('[data-role="save"]', sMerge).addEventListener('click', async (e) => {
            const btn = e.currentTarget;
            if (!merge.targetId) return;
            busy(btn, true);
            const data = await post('/crm/api/contact/merge', { source_id: merge.sourceId, target_id: merge.targetId });
            busy(btn, false);
            if (!data.success) { toast(data.error || 'Merge failed', 'error'); return; }
            toast('Merged', 'success');
            window.location.href = '/crm/contacts/' + data.target_id;
        });
    }

    // ---------- Command palette ----------

    const palette = $('#crmPalette');
    let paletteTimer = null;
    let paletteIndex = 0;
    const paletteDefault = palette ? $('[data-role="results"]', palette).innerHTML : '';
    function openPalette() {
        if (!palette) return;
        palette.classList.add('is-open');
        const input = $('[data-role="q"]', palette);
        input.value = '';
        $('[data-role="results"]', palette).innerHTML = paletteDefault;
        paletteIndex = 0; highlightPalette();
        setTimeout(() => input.focus(), 20);
    }
    function closePalette() { if (palette) palette.classList.remove('is-open'); }
    function highlightPalette() {
        const items = $all('.crm-palette__item', palette);
        items.forEach((el, i) => el.classList.toggle('is-active', i === paletteIndex));
        const active = items[paletteIndex];
        if (active) active.scrollIntoView({ block: 'nearest' });
    }
    function renderPalette(data, q) {
        const groups = [];
        const item = (href, icon, title, sub, trail) =>
            `<a class="crm-palette__item" href="${href}"><span class="p-icon"><i class="fas ${icon}"></i></span><span class="p-body"><span class="p-title">${esc(title)}</span>${sub ? `<span class="p-sub">${esc(sub)}</span>` : ''}</span>${trail ? `<span class="p-trail">${esc(trail)}</span>` : ''}</a>`;
        if (data.buildings && data.buildings.length) groups.push('<div class="crm-palette__group">Buildings</div>' + data.buildings.map(b => item('/crm/buildings/' + b.id, 'fa-building', b.address, b.borough || '', b.last_contacted_at ? 'touched ' + b.last_contacted_at : '')).join(''));
        if (data.contacts && data.contacts.length) groups.push('<div class="crm-palette__group">People</div>' + data.contacts.map(c => item('/crm/contacts/' + c.id, 'fa-user', c.name, [c.title, c.company].filter(Boolean).join(' · '), c.last_contacted_at ? 'touched ' + c.last_contacted_at : '')).join(''));
        if (data.lists && data.lists.length) groups.push('<div class="crm-palette__group">Lists</div>' + data.lists.map(l => item('/crm/lists/' + l.id, 'fa-list-ul', l.name, '', '')).join(''));
        groups.push('<div class="crm-palette__group">Actions</div>' +
            item('/crm/buildings?q=' + encodeURIComponent(q), 'fa-magnifying-glass', 'Search buildings for “' + q + '”', '', '') +
            item('/crm/contacts?q=' + encodeURIComponent(q), 'fa-magnifying-glass', 'Search people for “' + q + '”', '', '') +
            item('/properties?search=' + encodeURIComponent(q), 'fa-magnifying-glass-location', 'Look up “' + q + '” in the permit database', '', ''));
        $('[data-role="results"]', palette).innerHTML = groups.join('');
        paletteIndex = 0; highlightPalette();
    }
    if (palette) {
        const input = $('[data-role="q"]', palette);
        input.addEventListener('input', () => {
            clearTimeout(paletteTimer);
            const q = input.value.trim();
            if (q.length < 2) { $('[data-role="results"]', palette).innerHTML = paletteDefault; paletteIndex = 0; highlightPalette(); return; }
            paletteTimer = setTimeout(async () => {
                const data = await getJSON('/crm/api/search?q=' + encodeURIComponent(q));
                if (data.success) renderPalette(data, q);
            }, 140);
        });
        input.addEventListener('keydown', (e) => {
            const items = $all('.crm-palette__item', palette);
            if (e.key === 'ArrowDown') { e.preventDefault(); paletteIndex = Math.min(items.length - 1, paletteIndex + 1); highlightPalette(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); paletteIndex = Math.max(0, paletteIndex - 1); highlightPalette(); }
            else if (e.key === 'Enter') { e.preventDefault(); const active = items[paletteIndex]; if (active) window.location.href = active.getAttribute('href'); }
        });
        palette.addEventListener('mousemove', (e) => {
            const it = e.target.closest('.crm-palette__item');
            if (!it) return;
            const items = $all('.crm-palette__item', palette);
            const idx = items.indexOf(it);
            if (idx >= 0 && idx !== paletteIndex) { paletteIndex = idx; highlightPalette(); }
        });
    }

    // ---------- keyboard shortcuts ----------

    let goChord = false;
    document.addEventListener('keydown', (e) => {
        const tag = (e.target.tagName || '').toLowerCase();
        const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || e.target.isContentEditable;
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); return; }
        if (e.key === 'Escape') { closeAllSheets(); return; }
        if (typing) return;
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        const record = window.CRM_RECORD;
        const key = e.key;
        if (goChord) {
            goChord = false;
            if (key === 't') window.location.href = '/crm';
            else if (key === 'b') window.location.href = '/crm/buildings';
            else if (key === 'c') window.location.href = '/crm/contacts';
            else if (key === 'l') window.location.href = '/crm/lists';
            else if (key === 'f') window.location.href = '/crm/followups';
            return;
        }
        if (key === 'g') { goChord = true; setTimeout(() => { goChord = false; }, 900); return; }
        if (key === '/') { e.preventDefault(); openPalette(); return; }
        if (key === '?') { openSheet('sheetHelp'); return; }
        if (window.CRM_FOCUS) {
            if (key === 'ArrowRight' || key === 's') { document.dispatchEvent(new CustomEvent('crm:focus-skip')); return; }
            if (key === 'ArrowLeft') { document.dispatchEvent(new CustomEvent('crm:focus-prev')); return; }
            if (key === 'c') { const btn = $('#crmFocus [data-role="contacted"]'); if (btn) btn.click(); return; }
        }
        if (record) {
            if (key === 'c') { const btn = $('.crm-quick .js-contacted'); if (btn) btn.click(); }
            else if (key === 'n') { const ta = $('.js-note-composer textarea'); if (ta) { ta.focus(); e.preventDefault(); } }
            else if (key === 'f') { const btn = $('.crm-quick .js-followup'); if (btn) btn.click(); }
            else if (key === 'v') { const btn = $('.crm-quick .js-visit'); if (btn) btn.click(); }
        }
    });

    // ---------- delegated clicks ----------

    document.addEventListener('click', async (e) => {
            const btn = e.currentTarget;
        const t = e.target;
        const openPal = t.closest('.js-open-palette');
        if (openPal) { e.preventDefault(); closeAllSheets(); openPalette(); return; }

        const themeBtn = t.closest('.js-theme button');
        if (themeBtn) { try { localStorage.setItem('crm-theme', themeBtn.dataset.theme); } catch (err) { /* */ } applyTheme(themeBtn.dataset.theme); return; }

        const contacted = t.closest('.js-contacted');
        if (contacted) { openContacted(contacted); return; }
        const visit = t.closest('.js-visit');
        if (visit) { openVisit(visit); return; }
        const followup = t.closest('.js-followup');
        if (followup) { openFollowup(followup); return; }
        const fuEdit = t.closest('.js-fu-edit');
        if (fuEdit) { openFollowup(null, { id: fuEdit.dataset.id, title: fuEdit.dataset.title, due: fuEdit.dataset.due, note: fuEdit.dataset.note, assignee: fuEdit.dataset.assignee }); return; }
        const addToList = t.closest('.js-add-to-list');
        if (addToList) { openListSheet({ buildingId: addToList.dataset.buildingId || null, contactId: addToList.dataset.contactId || null }, addToList.dataset.entity || 'this'); return; }
        const addPerson = t.closest('.js-add-person');
        if (addPerson) { openPerson(addPerson); return; }
        const editPerson = t.closest('.js-edit-person');
        if (editPerson) { openPerson(null, JSON.parse(editPerson.dataset.contact)); return; }
        const addPhone = t.closest('.js-add-phone');
        if (addPhone) { openPhone(addPhone); return; }
        const editPhone = t.closest('.js-edit-phone');
        if (editPhone) { openPhone(null, { id: editPhone.dataset.id, label: editPhone.dataset.label, ext: editPhone.dataset.ext, number: editPhone.dataset.number, primary: editPhone.dataset.primary === '1' }); return; }
        const editBuilding = t.closest('.js-edit-building');
        if (editBuilding) { openBuildingEdit(JSON.parse(editBuilding.dataset.building)); return; }
        const mergeBtn = t.closest('.js-merge');
        if (mergeBtn) { openMerge(mergeBtn); return; }

        const star = t.closest('.js-star');
        if (star) {
            e.preventDefault(); e.stopPropagation();
            const was = star.classList.contains('is-starred');
            star.classList.toggle('is-starred');
            star.querySelector('i').className = (was ? 'far' : 'fas') + ' fa-star';
            const data = await post('/crm/api/star', { building_id: star.dataset.buildingId || null, contact_id: star.dataset.contactId || null });
            if (!data.success) { star.classList.toggle('is-starred'); star.querySelector('i').className = (was ? 'fas' : 'far') + ' fa-star'; toast(data.error || 'Could not update star', 'error'); }
            return;
        }

        const stepperStep = t.closest('.crm-stepper__step');
        if (stepperStep && !stepperStep.classList.contains('is-current')) {
            const data = await post('/crm/api/stage', { building_id: stepperStep.dataset.buildingId, stage: stepperStep.dataset.stage });
            if (data.success) { toast('Stage: ' + data.label, 'success'); changed(); } else toast(data.error || 'Failed', 'error');
            return;
        }

        const done = t.closest('.js-fu-done');
        if (done) {
            const data = await post('/crm/api/followup/' + done.dataset.id + '/done');
            if (data.success) { const row = done.closest('[data-followup-row]'); if (row) row.classList.add('is-done'); toast('Done ✓', 'success'); setTimeout(changed, 350); }
            else toast(data.error || 'Failed', 'error');
            return;
        }
        const snooze = t.closest('.js-fu-snooze');
        if (snooze) {
            const data = await post('/crm/api/followup/' + snooze.dataset.id + '/snooze', { days: 1 });
            if (data.success) { toast('Snoozed to tomorrow', 'info'); changed(); } else toast(data.error || 'Failed', 'error');
            return;
        }
        const reopen = t.closest('.js-fu-reopen');
        if (reopen) { const data = await post('/crm/api/followup/' + reopen.dataset.id + '/reopen'); if (data.success) changed(); else toast(data.error || 'Failed', 'error'); return; }

        const pin = t.closest('.js-pin');
        if (pin) { const data = await post('/crm/api/activity/' + pin.dataset.id + '/pin'); if (data.success) changed(); else toast(data.error || 'Failed', 'error'); return; }
        const evDelete = t.closest('.js-ev-delete');
        if (evDelete) {
            if (!confirm('Delete this entry? This cannot be undone.')) return;
            const data = await post('/crm/api/activity/' + evDelete.dataset.id + '/delete');
            if (data.success) changed(); else toast(data.error || 'Could not delete', 'error');
            return;
        }
        const phoneStatus = t.closest('.js-phone-status');
        if (phoneStatus) { const data = await post('/crm/api/phone/' + phoneStatus.dataset.id + '/status', { status: phoneStatus.dataset.status }); if (data.success) changed(); else toast(data.error || 'Failed', 'error'); return; }
        const dnc = t.closest('.js-dnc');
        if (dnc) { const data = await post('/crm/api/dnc', { contact_id: dnc.dataset.contactId, value: dnc.dataset.value === '1' }); if (data.success) changed(); else toast(data.error || 'Failed', 'error'); return; }
        const unlink = t.closest('.js-unlink');
        if (unlink) {
            if (!confirm('Remove this person from the building? Their record stays in Contacts.')) return;
            const data = await post('/crm/api/building-contact/unlink', { building_id: unlink.dataset.buildingId, contact_id: unlink.dataset.contactId });
            if (data.success) changed(); else toast(data.error || 'Failed', 'error');
            return;
        }
        const itemRemove = t.closest('.js-item-remove');
        if (itemRemove) { const data = await post('/crm/api/list-item/' + itemRemove.dataset.id + '/remove'); if (data.success) { const row = itemRemove.closest('.crm-cell'); if (row) row.remove(); } else toast(data.error || 'Failed', 'error'); return; }
        const listDelete = t.closest('.js-list-delete');
        if (listDelete) {
            if (!confirm('Delete this list? The buildings and people on it stay in the CRM.')) return;
            const data = await post('/crm/api/list/' + listDelete.dataset.id + '/delete');
            if (data.success) window.location.href = '/crm/lists'; else toast(data.error || 'Failed', 'error');
            return;
        }
        const listRename = t.closest('.js-list-rename');
        if (listRename) {
            const name = prompt('List name:', listRename.dataset.name);
            if (!name || !name.trim()) return;
            const data = await post('/crm/api/list/' + listRename.dataset.listId + '/update', { name: name.trim() });
            if (data.success) location.reload(); else toast(data.error || 'Failed', 'error');
            return;
        }
        const filterDelete = t.closest('.js-filter-delete');
        if (filterDelete) {
            if (!confirm('Remove this saved lead list?')) return;
            const data = await post('/crm/api/saved-filter/' + filterDelete.dataset.id + '/delete');
            if (data.success) location.reload(); else toast(data.error || 'Failed', 'error');
            return;
        }
        const buildingDelete = t.closest('.js-building-delete');
        if (buildingDelete) {
            if (!confirm('Remove this building and its whole CRM history? This cannot be undone.')) return;
            const data = await post('/crm/api/building/' + buildingDelete.dataset.id + '/delete');
            if (data.success) window.location.href = '/crm/buildings'; else toast(data.error || 'Failed', 'error');
            return;
        }
        const contactDelete = t.closest('.js-contact-delete');
        if (contactDelete) {
            if (!confirm('Delete this person and all their numbers, links, and history?')) return;
            const data = await post('/crm/api/contact/' + contactDelete.dataset.id + '/delete');
            if (data.success) window.location.href = '/crm/contacts'; else toast(data.error || 'Failed', 'error');
            return;
        }
        const newList = t.closest('.js-new-list');
        if (newList) {
            const name = prompt('Name the new list:');
            if (!name || !name.trim()) return;
            const data = await post('/crm/api/list', { name: name.trim() });
            if (data.success) window.location.href = '/crm/lists/' + data.list_id; else toast(data.error || 'Failed', 'error');
            return;
        }
    });

    // ---------- delegated changes ----------

    document.addEventListener('change', async (e) => {
        const t = e.target;
        const assign = t.closest('.js-assign-select');
        if (assign) { const data = await post('/crm/api/assign', { building_id: assign.dataset.buildingId, user_id: assign.value || null }); toast(data.success ? 'Assignment saved' : (data.error || 'Failed'), data.success ? 'success' : 'error'); return; }
        const role = t.closest('.js-role-select');
        if (role) { const data = await post('/crm/api/building-contact/role', { building_id: role.dataset.buildingId, contact_id: role.dataset.contactId, role: role.value }); if (!data.success) toast(data.error || 'Failed', 'error'); return; }
        const listAssign = t.closest('.js-list-assign');
        if (listAssign) { const data = await post('/crm/api/list/' + listAssign.dataset.listId + '/update', { assigned_to_id: listAssign.value || null }); toast(data.success ? 'List assignee saved' : (data.error || 'Failed'), data.success ? 'success' : 'error'); return; }
        if (t.classList.contains('js-bulk-check') || t.classList.contains('js-bulk-all')) { onBulkChange(t); return; }
        const bulkStage = t.closest('.js-bulk-stage');
        if (bulkStage && bulkStage.value) { await runBulk('stage', bulkStage.value); bulkStage.value = ''; return; }
        const bulkAssign = t.closest('.js-bulk-assign');
        if (bulkAssign && bulkAssign.value) { await runBulk('assign', bulkAssign.value === '__none' ? null : bulkAssign.value); bulkAssign.value = ''; return; }
    });

    // ---------- note composer (delegated: partial swaps recreate it) ----------

    document.addEventListener('click', async (e) => {
            const btn = e.currentTarget;
        const save = e.target.closest('.js-note-save');
        if (!save) return;
        const form = save.closest('.js-note-composer');
        const textarea = $('textarea', form);
        const note = textarea.value.trim();
        if (!note) { textarea.focus(); return; }
        busy(save, true);
        const data = await post('/crm/api/note', { building_id: form.dataset.buildingId || null, contact_id: form.dataset.contactId || null, note });
        busy(save, false);
        if (data.success) { textarea.value = ''; changed(); } else toast(data.error || 'Failed', 'error');
    });
    document.addEventListener('keydown', (e) => {
        if (!(e.metaKey || e.ctrlKey) || e.key !== 'Enter') return;
        const form = e.target.closest && e.target.closest('.js-note-composer');
        if (form) { e.preventDefault(); $('.js-note-save', form).click(); }
    });

    // ---------- bulk selection (buildings) ----------

    const bulkBar = $('#crmBulkBar');
    function selectedIds() { return $all('.js-bulk-check:checked').map(cb => cb.value); }
    function onBulkChange(el) {
        if (el.classList.contains('js-bulk-all')) $all('.js-bulk-check').forEach(cb => { cb.checked = el.checked; });
        $all('.js-bulk-check').forEach(cb => { const card = cb.closest('.crm-card'); if (card) card.classList.toggle('is-selected', cb.checked); });
        const n = selectedIds().length;
        if (bulkBar) { bulkBar.hidden = n === 0; $('[data-role="count"]', bulkBar).textContent = n + ' selected'; }
    }
    function clearBulk() {
        $all('.js-bulk-check, .js-bulk-all').forEach(cb => { cb.checked = false; });
        $all('.crm-card.is-selected').forEach(c => c.classList.remove('is-selected'));
        if (bulkBar) bulkBar.hidden = true;
        location.reload();
    }
    async function runBulk(action, value) {
        const ids = selectedIds();
        if (!ids.length) return;
        const data = await post('/crm/api/buildings/bulk', { ids, action, value });
        if (data.success) { toast('Updated ' + data.updated + ' building' + (data.updated === 1 ? '' : 's'), 'success'); clearBulk(); }
        else toast(data.error || 'Failed', 'error');
    }
    if (bulkBar) {
        $('.js-bulk-list', bulkBar).addEventListener('click', () => { const ids = selectedIds(); if (ids.length) openListSheet({ bulkIds: ids }, ids.length + ' buildings'); });
        $('.js-bulk-star', bulkBar).addEventListener('click', () => runBulk('star'));
        $('.js-bulk-clear', bulkBar).addEventListener('click', () => { $all('.js-bulk-check, .js-bulk-all').forEach(cb => { cb.checked = false; }); onBulkChange(bulkBar); });
    }

    // ---------- board drag & drop ----------

    (function initBoard() {
        const board = $('#crmBoard');
        if (!board) return;
        let dragging = null;
        board.addEventListener('dragstart', (e) => {
            const card = e.target.closest('.js-bcard');
            if (!card) return;
            dragging = card; card.classList.add('is-dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', card.dataset.buildingId);
        });
        board.addEventListener('dragend', () => { if (dragging) dragging.classList.remove('is-dragging'); dragging = null; $all('.js-col.is-dragover').forEach(c => c.classList.remove('is-dragover')); });
        board.addEventListener('dragover', (e) => { const col = e.target.closest('.js-col'); if (!col) return; e.preventDefault(); e.dataTransfer.dropEffect = 'move'; $all('.js-col.is-dragover').forEach(c => c.classList.remove('is-dragover')); col.classList.add('is-dragover'); });
        board.addEventListener('dragleave', (e) => { const col = e.target.closest('.js-col'); if (col && !col.contains(e.relatedTarget)) col.classList.remove('is-dragover'); });
        board.addEventListener('drop', async (e) => {
            const col = e.target.closest('.js-col');
            if (!col || !dragging) return;
            e.preventDefault();
            const fromCol = dragging.closest('.js-col');
            const stage = col.dataset.stage;
            if (fromCol === col) return;
            const body = $('.crm-col__body', col);
            const empty = $('.js-col-empty', body); if (empty) empty.remove();
            body.prepend(dragging);
            dragging.dataset.stage = stage;
            const recount = (c) => { $('.js-col-count', c).textContent = $all('.js-bcard', c).length; if (!$all('.js-bcard', c).length && !$('.js-col-empty', c)) $('.crm-col__body', c).innerHTML = '<div class="crm-col__empty js-col-empty">Drop here</div>'; };
            recount(col); recount(fromCol);
            const data = await post('/crm/api/stage', { building_id: dragging.dataset.buildingId, stage });
            if (data.success) toast('Moved to ' + data.label, 'success');
            else { toast(data.error || 'Could not move', 'error'); location.reload(); }
        });
    })();

    // ---------- contacts: filter-as-you-type ----------

    (function initContactFilter() {
        const input = $('[data-role="contact-filter"]');
        const list = $('#contactsList');
        if (!input || !list) return;
        input.addEventListener('input', () => {
            const q = input.value.trim().toLowerCase();
            const digits = q.replace(/\D/g, '');
            $all('.js-contact-row', list).forEach(row => {
                const hay = row.dataset.search || '';
                const hit = !q || hay.includes(q) || (digits.length >= 3 && hay.replace(/\D/g, '').includes(digits));
                row.style.display = hit ? '' : 'none';
            });
            $all('.crm-alpha__letter', list).forEach(letter => {
                let el = letter.nextElementSibling; let any = false;
                while (el && !el.classList.contains('crm-alpha__letter')) { if (el.style.display !== 'none') any = true; el = el.nextElementSibling; }
                letter.style.display = any ? '' : 'none';
            });
        });
    })();

    // ---------- tooltips for [data-tip] ----------

    (function initTips() {
        const tip = $('#crmTip');
        if (!tip) return;
        document.addEventListener('mousemove', (e) => {
            const el = e.target.closest && e.target.closest('[data-tip]');
            if (!el) { tip.hidden = true; return; }
            tip.textContent = el.dataset.tip;
            tip.style.left = e.clientX + 'px';
            tip.style.top = e.clientY + 'px';
            tip.hidden = false;
        });
    })();

    // ---------- focus mode ----------

    (function initFocus() {
        const cfg = window.CRM_FOCUS;
        const root = $('#crmFocus');
        if (!cfg || !root || !cfg.queue || !cfg.queue.length) return;
        const queue = cfg.queue;
        let index = 0;
        let logged = 0;
        const doneSet = new Set();
        const card = $('[data-role="card"]', root);
        const why = $('[data-role="why"]', root);

        function current() { return queue[index]; }
        function updateBar() {
            $('[data-role="progress"]', root).style.width = Math.round(100 * index / queue.length) + '%';
            $('[data-role="count"]', root).textContent = (index + 1) + ' of ' + queue.length;
        }
        async function show() {
            if (index >= queue.length) {
                $('[data-role="done"]', root).hidden = false;
                $('[data-role="done-count"]', root).textContent = logged;
                card.hidden = true; $('.crm-focus__nav', root).hidden = true; $('.crm-focus__bar', root).hidden = true; why.hidden = true;
                return;
            }
            const item = current();
            updateBar();
            why.hidden = !item.sub;
            why.innerHTML = '<i class="fas fa-circle-info"></i> ' + esc(item.sub || '');
            $('[data-role="open"]', root).href = (item.type === 'building' ? '/crm/buildings/' : '/crm/contacts/') + item.id;
            const btn = $('[data-role="contacted"]', root);
            btn.dataset.buildingId = item.type === 'building' ? item.id : '';
            btn.dataset.contactId = item.type === 'contact' ? item.id : '';
            btn.dataset.entity = item.label;
            btn.dataset.followupId = item.followup_id || '';
            card.style.opacity = '0.5';
            try {
                const res = await fetch('/crm/partials/focus/' + item.type + '/' + item.id);
                card.innerHTML = res.ok ? await res.text() : '<div class="crm-group"><div class="crm-empty">Could not load this lead.</div></div>';
            } catch (e) { card.innerHTML = '<div class="crm-group"><div class="crm-empty">Could not load this lead.</div></div>'; }
            card.style.opacity = '1';
            card.classList.remove('crm-focus__card'); void card.offsetWidth; card.classList.add('crm-focus__card');
        }
        function next() { if (index < queue.length) index += 1; show(); }
        function prev() { if (index > 0) index -= 1; show(); }
        $('[data-role="skip"]', root).addEventListener('click', next);
        $('[data-role="prev"]', root).addEventListener('click', prev);
        $('[data-role="contacted"]', root).addEventListener('click', (e) => openContacted(e.currentTarget));
        document.addEventListener('crm:focus-skip', next);
        document.addEventListener('crm:focus-prev', prev);
        document.addEventListener('crm:touch-logged', (e) => {
            const item = current();
            const d = e.detail || {};
            if (item && ((item.type === 'building' && String(d.buildingId) === String(item.id)) || (item.type === 'contact' && String(d.contactId) === String(item.id)) || d.buildingId || d.contactId)) {
                logged += 1; doneSet.add(index);
            }
        });
        document.addEventListener('crm:focus-changed', () => { if (doneSet.has(index)) next(); else show(); });
        show();
    })();

    window.crmToast = toast;
    window.crmRefresh = refreshPartials;
})();
