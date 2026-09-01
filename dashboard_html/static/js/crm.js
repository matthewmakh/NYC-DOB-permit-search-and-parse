// CRM interactions: the Contacted dialog, stars, follow-ups, lists, people.
// Server-rendered pages are the source of truth — after a write we reload,
// except for the optimistic star and pin toggles.

(function () {
    'use strict';

    // ---------- helpers ----------

    function $(sel, root) { return (root || document).querySelector(sel); }
    function $all(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value == null ? '' : String(value);
        return div.innerHTML;
    }

    function toast(message, type) {
        const stack = $('#crmToastStack');
        if (!stack) return;
        const el = document.createElement('div');
        el.className = 'toast toast-' + (type || 'info');
        const icons = { success: 'fa-check-circle', error: 'fa-triangle-exclamation', info: 'fa-circle-info', warning: 'fa-circle-exclamation' };
        el.innerHTML = '<i class="fas ' + (icons[type] || icons.info) + '"></i><span>' + escapeHtml(message) + '</span>';
        stack.appendChild(el);
        setTimeout(() => { el.classList.add('leaving'); setTimeout(() => el.remove(), 220); }, 3200);
    }

    async function api(path, body) {
        const res = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        let data = {};
        try { data = await res.json(); } catch (e) { /* non-JSON error body */ }
        if (!res.ok && !data.error) data.error = 'Request failed (' + res.status + ')';
        data._status = res.status;
        return data;
    }

    function busy(btn, on) {
        if (!btn) return;
        if (on) {
            btn.dataset.origHtml = btn.innerHTML;
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner spinner-sm" style="border-top-color:#fff"></span>';
        } else {
            btn.disabled = false;
            if (btn.dataset.origHtml) btn.innerHTML = btn.dataset.origHtml;
        }
    }

    function openModal(modal) { modal.classList.add('is-open'); }
    function closeModal(modal) { modal.classList.remove('is-open'); }

    document.addEventListener('click', (e) => {
        const closer = e.target.closest('.js-modal-close');
        if (closer) { closeModal(closer.closest('.modal')); return; }
        if (e.target.classList && e.target.classList.contains('modal')) closeModal(e.target);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') $all('.modal.is-open').forEach(closeModal);
    });

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

    // ---------- the Contacted dialog ----------

    const contactedModal = $('#crmContactedModal');
    let touchTarget = null;      // {buildingId, contactId, entity, phone}
    let nudged = false;
    let savedActivityForFollowup = null;

    function openContacted(trigger) {
        touchTarget = {
            buildingId: trigger.dataset.buildingId || null,
            contactId: trigger.dataset.contactId || null,
            entity: trigger.dataset.entity || 'this lead',
            phone: trigger.dataset.phone || null,
        };
        nudged = false;
        savedActivityForFollowup = null;
        $('[data-role="entity"]', contactedModal).textContent = touchTarget.entity;
        $('[data-role="phone-line"]', contactedModal).textContent = touchTarget.phone ? '· ' + touchTarget.phone : '';
        const collision = $('[data-role="collision"]', contactedModal);
        if (trigger.dataset.lastTouch) {
            collision.innerHTML = '<i class="fas fa-circle-exclamation"></i> Heads up — ' + escapeHtml(trigger.dataset.lastTouch) + '. Worth checking before calling again.';
            collision.hidden = false;
        } else {
            collision.hidden = true;
        }
        selectChip($('[data-role="methods"]', contactedModal), $('[data-role="methods"] .crm-choice', contactedModal));
        selectChip($('[data-role="outcomes"]', contactedModal), null);
        $('[data-role="note"]', contactedModal).value = '';
        $('[data-role="nudge"]', contactedModal).hidden = true;
        const save = $('[data-role="save"]', contactedModal);
        save.innerHTML = '<i class="fas fa-phone" style="font-size:11px"></i> Save';
        $('[data-role="step1"]', contactedModal).hidden = false;
        $('[data-role="step2"]', contactedModal).hidden = true;
        $('[data-role="step3"]', contactedModal).hidden = true;
        $('[data-role="fu-date"]', contactedModal).value = localISODate(1);
        openModal(contactedModal);
        $('[data-role="note"]', contactedModal).focus();
    }

    if (contactedModal) {
        $all('[data-role="methods"] .crm-choice, [data-role="outcomes"] .crm-choice', contactedModal).forEach(btn => {
            btn.addEventListener('click', () => {
                const container = btn.parentElement;
                if (btn.classList.contains('is-selected') && container.dataset.role === 'outcomes') {
                    btn.classList.remove('is-selected'); // outcome is optional; allow unpicking
                } else {
                    selectChip(container, btn);
                }
            });
        });

        $('[data-role="save"]', contactedModal).addEventListener('click', async (e) => {
            const note = $('[data-role="note"]', contactedModal).value.trim();
            if (!note && !nudged) {
                // Nudge once, never block: relabel and let the second press through.
                nudged = true;
                $('[data-role="nudge"]', contactedModal).hidden = false;
                e.target.textContent = 'Save without note';
                return;
            }
            busy(e.target, true);
            const data = await api('/crm/api/contacted', {
                building_id: touchTarget.buildingId,
                contact_id: touchTarget.contactId,
                method: chipValue($('[data-role="methods"]', contactedModal)) || 'call',
                outcome: chipValue($('[data-role="outcomes"]', contactedModal)),
                note: note,
                phone: touchTarget.phone,
            });
            busy(e.target, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            savedActivityForFollowup = data.activity_id;
            $('[data-role="step1"]', contactedModal).hidden = true;
            $('[data-role="step2"]', contactedModal).hidden = false;
        });

        async function createFollowupAndFinish(dueDays, dueDate) {
            const payload = {
                building_id: touchTarget.buildingId,
                contact_id: touchTarget.contactId,
                title: 'Follow up with ' + touchTarget.entity,
            };
            if (dueDate) payload.due_date = dueDate; else payload.days = dueDays;
            const data = await api('/crm/api/followup', payload);
            finishContacted(data.success ? 'Touch logged, follow-up set.' : 'Touch logged (follow-up failed).');
        }

        function finishContacted(message) {
            $('[data-role="step2"]', contactedModal).hidden = true;
            $('[data-role="step3"]', contactedModal).hidden = false;
            $('[data-role="done-msg"]', contactedModal).textContent = message;
            setTimeout(() => { closeModal(contactedModal); location.reload(); }, 700);
        }

        $all('[data-role="fu-quick"] .crm-choice', contactedModal).forEach(btn => {
            btn.addEventListener('click', () => createFollowupAndFinish(parseInt(btn.dataset.days, 10)));
        });
        $('[data-role="fu-date-save"]', contactedModal).addEventListener('click', () => {
            const value = $('[data-role="fu-date"]', contactedModal).value;
            if (!value) { toast('Pick a date first', 'warning'); return; }
            createFollowupAndFinish(null, value);
        });
        $('[data-role="fu-skip"]', contactedModal).addEventListener('click', () => finishContacted('Touch logged.'));
    }

    // ---------- visit dialog ----------

    const visitModal = $('#crmVisitModal');
    let visitBuilding = null;

    function openVisit(trigger) {
        visitBuilding = trigger.dataset.buildingId;
        $('[data-role="entity"]', visitModal).textContent = trigger.dataset.entity || 'this building';
        $('[data-role="visit-date"]', visitModal).value = localISODate(0);
        $('[data-role="note"]', visitModal).value = '';
        openModal(visitModal);
    }

    if (visitModal) {
        $('[data-role="save"]', visitModal).addEventListener('click', async (e) => {
            busy(e.target, true);
            const data = await api('/crm/api/visit', {
                building_id: visitBuilding,
                visited_on: $('[data-role="visit-date"]', visitModal).value,
                note: $('[data-role="note"]', visitModal).value.trim(),
            });
            busy(e.target, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeModal(visitModal);
            location.reload();
        });
    }

    // ---------- follow-up dialog ----------

    const followupModal = $('#crmFollowupModal');
    let followupTarget = null;

    function openFollowup(trigger) {
        followupTarget = {
            buildingId: trigger.dataset.buildingId || null,
            contactId: trigger.dataset.contactId || null,
        };
        $('[data-role="entity"]', followupModal).textContent = trigger.dataset.entity || 'this lead';
        $('[data-role="title"]', followupModal).value = '';
        $('[data-role="note"]', followupModal).value = '';
        $('[data-role="fu-date"]', followupModal).value = localISODate(1);
        selectChip($('[data-role="fu-quick"]', followupModal), $('[data-role="fu-quick"] .crm-choice', followupModal));
        openModal(followupModal);
        $('[data-role="title"]', followupModal).focus();
    }

    if (followupModal) {
        $all('[data-role="fu-quick"] .crm-choice', followupModal).forEach(btn => {
            btn.addEventListener('click', () => {
                selectChip(btn.parentElement, btn);
                $('[data-role="fu-date"]', followupModal).value = localISODate(parseInt(btn.dataset.days, 10));
            });
        });
        $('[data-role="save"]', followupModal).addEventListener('click', async (e) => {
            const due = $('[data-role="fu-date"]', followupModal).value;
            if (!due) { toast('Pick a date', 'warning'); return; }
            busy(e.target, true);
            const data = await api('/crm/api/followup', {
                building_id: followupTarget.buildingId,
                contact_id: followupTarget.contactId,
                title: $('[data-role="title"]', followupModal).value.trim() || 'Follow up',
                note: $('[data-role="note"]', followupModal).value.trim(),
                due_date: due,
            });
            busy(e.target, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeModal(followupModal);
            location.reload();
        });
    }

    // ---------- add-to-list dialog ----------

    const listModal = $('#crmListModal');
    let listTarget = null;

    function openListModal(trigger) {
        listTarget = {
            buildingId: trigger.dataset.buildingId || null,
            contactId: trigger.dataset.contactId || null,
        };
        $('[data-role="entity"]', listModal).textContent = trigger.dataset.entity || 'this lead';
        const select = $('[data-role="list-select"]', listModal);
        const lists = window.CRM_LISTS || [];
        select.innerHTML = '<option value="">Choose a list…</option>' + lists.map(l =>
            '<option value="' + l.id + '">' + escapeHtml(l.name) + '</option>').join('');
        $('[data-role="new-list"]', listModal).value = '';
        openModal(listModal);
    }

    if (listModal) {
        $('[data-role="save"]', listModal).addEventListener('click', async (e) => {
            const listId = $('[data-role="list-select"]', listModal).value;
            const newName = $('[data-role="new-list"]', listModal).value.trim();
            if (!listId && !newName) { toast('Pick a list or name a new one', 'warning'); return; }
            busy(e.target, true);
            const data = await api('/crm/api/list-item', {
                list_id: listId || null,
                new_list_name: newName || null,
                building_id: listTarget.buildingId,
                contact_id: listTarget.contactId,
            });
            busy(e.target, false);
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeModal(listModal);
            location.reload();
        });
    }

    // ---------- add-person dialog ----------

    const contactModal = $('#crmContactModal');
    let contactBuilding = null;
    let contactForce = false;

    function openContactModal(trigger) {
        contactBuilding = trigger.dataset.buildingId || null;
        contactForce = false;
        $('[data-role="dup-warning"]', contactModal).hidden = true;
        $('[data-role="building-role-wrap"]', contactModal).style.display = contactBuilding ? '' : 'none';
        ['name', 'title-field', 'phone', 'phone-label', 'email', 'company', 'source-detail'].forEach(role => {
            $('[data-role="' + role + '"]', contactModal).value = '';
        });
        const save = $('[data-role="save"]', contactModal);
        save.textContent = 'Add person';
        openModal(contactModal);
        $('[data-role="name"]', contactModal).focus();
    }

    if (contactModal) {
        $('[data-role="save"]', contactModal).addEventListener('click', async (e) => {
            const name = $('[data-role="name"]', contactModal).value.trim();
            if (!name) { toast('A name is required', 'warning'); return; }
            busy(e.target, true);
            const data = await api('/crm/api/contact', {
                name: name,
                title: $('[data-role="title-field"]', contactModal).value,
                phone: $('[data-role="phone"]', contactModal).value,
                phone_label: $('[data-role="phone-label"]', contactModal).value,
                email: $('[data-role="email"]', contactModal).value,
                company: $('[data-role="company"]', contactModal).value,
                source_detail: $('[data-role="source-detail"]', contactModal).value,
                building_id: contactBuilding,
                building_role: $('[data-role="building-role"]', contactModal).value,
                force: contactForce,
            });
            busy(e.target, false);
            if (data.duplicate) {
                contactForce = true;
                const warn = $('[data-role="dup-warning"]', contactModal);
                warn.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + escapeHtml(data.error) +
                    (data.matches && data.matches.length ? ' — <a href="/crm/contacts/' + data.matches[0].id + '">open theirs</a>' : '') +
                    '. Press <strong>Add anyway</strong> if this really is a different person.';
                warn.hidden = false;
                e.target.textContent = 'Add anyway';
                return;
            }
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeModal(contactModal);
            location.reload();
        });
    }

    // ---------- add-phone dialog ----------

    const phoneModal = $('#crmPhoneModal');
    let phoneContact = null;
    let phoneForce = false;

    function openPhoneModal(trigger) {
        phoneContact = trigger.dataset.contactId;
        phoneForce = false;
        $('[data-role="entity"]', phoneModal).textContent = trigger.dataset.entity || 'this person';
        $('[data-role="dup-warning"]', phoneModal).hidden = true;
        $('[data-role="phone"]', phoneModal).value = '';
        $('[data-role="phone-label"]', phoneModal).value = '';
        $('[data-role="source-detail"]', phoneModal).value = '';
        $('[data-role="make-primary"]', phoneModal).checked = false;
        $('[data-role="save"]', phoneModal).textContent = 'Add number';
        openModal(phoneModal);
        $('[data-role="phone"]', phoneModal).focus();
    }

    if (phoneModal) {
        $('[data-role="save"]', phoneModal).addEventListener('click', async (e) => {
            busy(e.target, true);
            const data = await api('/crm/api/phone', {
                contact_id: phoneContact,
                number: $('[data-role="phone"]', phoneModal).value,
                label: $('[data-role="phone-label"]', phoneModal).value,
                source_detail: $('[data-role="source-detail"]', phoneModal).value,
                make_primary: $('[data-role="make-primary"]', phoneModal).checked,
                force: phoneForce,
            });
            busy(e.target, false);
            if (data.duplicate) {
                phoneForce = true;
                const warn = $('[data-role="dup-warning"]', phoneModal);
                warn.innerHTML = '<i class="fas fa-circle-exclamation"></i> ' + escapeHtml(data.error) + '. Press <strong>Add anyway</strong> to keep it here too.';
                warn.hidden = false;
                e.target.textContent = 'Add anyway';
                return;
            }
            if (!data.success) { toast(data.error || 'Could not save', 'error'); return; }
            closeModal(phoneModal);
            location.reload();
        });
    }

    // ---------- delegated click handlers ----------

    document.addEventListener('click', async (e) => {
        const contacted = e.target.closest('.js-contacted');
        if (contacted) { openContacted(contacted); return; }

        const visit = e.target.closest('.js-visit');
        if (visit) { openVisit(visit); return; }

        const followup = e.target.closest('.js-followup');
        if (followup) { openFollowup(followup); return; }

        const addToList = e.target.closest('.js-add-to-list');
        if (addToList) { openListModal(addToList); return; }

        const addContact = e.target.closest('.js-add-contact');
        if (addContact) { openContactModal(addContact); return; }

        const addPhone = e.target.closest('.js-add-phone');
        if (addPhone) { openPhoneModal(addPhone); return; }

        const star = e.target.closest('.js-star');
        if (star) {
            e.preventDefault();
            e.stopPropagation();
            // Optimistic: flip immediately, revert on failure.
            const wasStarred = star.classList.contains('is-starred');
            star.classList.toggle('is-starred');
            star.querySelector('i').className = (wasStarred ? 'far' : 'fas') + ' fa-star';
            const data = await api('/crm/api/star', {
                building_id: star.dataset.buildingId || null,
                contact_id: star.dataset.contactId || null,
            });
            if (!data.success) {
                star.classList.toggle('is-starred');
                star.querySelector('i').className = (wasStarred ? 'fas' : 'far') + ' fa-star';
                toast(data.error || 'Could not update star', 'error');
            }
            return;
        }

        const done = e.target.closest('.js-fu-done');
        if (done) {
            const data = await api('/crm/api/followup/' + done.dataset.id + '/done');
            if (data.success) {
                const row = done.closest('[data-followup-row]');
                if (row) { row.style.opacity = '0.4'; row.querySelectorAll('button').forEach(b => b.disabled = true); }
                toast('Done ✓', 'success');
            } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const snooze = e.target.closest('.js-fu-snooze');
        if (snooze) {
            const data = await api('/crm/api/followup/' + snooze.dataset.id + '/snooze', { days: 1 });
            if (data.success) {
                const row = snooze.closest('[data-followup-row]');
                if (row) row.style.opacity = '0.4';
                toast('Snoozed to tomorrow', 'info');
            } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const pin = e.target.closest('.js-pin');
        if (pin) {
            const data = await api('/crm/api/activity/' + pin.dataset.id + '/pin');
            if (data.success) { location.reload(); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const evDelete = e.target.closest('.js-ev-delete');
        if (evDelete) {
            if (!confirm('Delete this entry? This cannot be undone.')) return;
            const data = await api('/crm/api/activity/' + evDelete.dataset.id + '/delete');
            if (data.success) { location.reload(); } else { toast(data.error || 'Could not delete', 'error'); }
            return;
        }

        const phoneStatus = e.target.closest('.js-phone-status');
        if (phoneStatus) {
            const data = await api('/crm/api/phone/' + phoneStatus.dataset.id + '/status',
                { status: phoneStatus.dataset.status });
            if (data.success) { location.reload(); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const dnc = e.target.closest('.js-dnc');
        if (dnc) {
            const data = await api('/crm/api/dnc', {
                contact_id: dnc.dataset.contactId,
                value: dnc.dataset.value === '1',
            });
            if (data.success) { location.reload(); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const itemRemove = e.target.closest('.js-item-remove');
        if (itemRemove) {
            const data = await api('/crm/api/list-item/' + itemRemove.dataset.id + '/remove');
            if (data.success) {
                const row = itemRemove.closest('.crm-row');
                if (row) row.remove();
            } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const listDelete = e.target.closest('.js-list-delete');
        if (listDelete) {
            if (!confirm('Delete this list? The buildings and people on it stay in the CRM.')) return;
            const data = await api('/crm/api/list/' + listDelete.dataset.id + '/delete');
            if (data.success) { window.location.href = '/crm/lists'; } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const filterDelete = e.target.closest('.js-filter-delete');
        if (filterDelete) {
            if (!confirm('Remove this saved lead list?')) return;
            const data = await api('/crm/api/saved-filter/' + filterDelete.dataset.id + '/delete');
            if (data.success) { location.reload(); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const buildingDelete = e.target.closest('.js-building-delete');
        if (buildingDelete) {
            if (!confirm('Remove this building and its whole CRM history? This cannot be undone.')) return;
            const data = await api('/crm/api/building/' + buildingDelete.dataset.id + '/delete');
            if (data.success) { window.location.href = '/crm/buildings'; } else { toast(data.error || 'Failed', 'error'); }
            return;
        }

        const newList = e.target.closest('.js-new-list');
        if (newList) {
            const name = prompt('Name the new list:');
            if (!name || !name.trim()) return;
            const data = await api('/crm/api/list', { name: name.trim() });
            if (data.success) { location.reload(); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }
    });

    // ---------- delegated change handlers ----------

    document.addEventListener('change', async (e) => {
        const stage = e.target.closest('.js-stage-select');
        if (stage) {
            const data = await api('/crm/api/stage', {
                building_id: stage.dataset.buildingId, stage: stage.value,
            });
            if (data.success) { location.reload(); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }
        const assign = e.target.closest('.js-assign-select');
        if (assign) {
            const data = await api('/crm/api/assign', {
                building_id: assign.dataset.buildingId, user_id: assign.value || null,
            });
            if (data.success) { toast('Assignment saved', 'success'); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }
        const listAssign = e.target.closest('.js-list-assign');
        if (listAssign) {
            const data = await api('/crm/api/list/' + listAssign.dataset.listId + '/update', {
                assigned_to_id: listAssign.value || null,
            });
            if (data.success) { toast('List assignee saved', 'success'); } else { toast(data.error || 'Failed', 'error'); }
            return;
        }
    });

    // ---------- inline note composer ----------

    $all('.js-note-composer').forEach(form => {
        const button = $('.js-note-save', form);
        const textarea = $('textarea', form);
        button.addEventListener('click', async () => {
            const note = textarea.value.trim();
            if (!note) { textarea.focus(); return; }
            busy(button, true);
            const data = await api('/crm/api/note', {
                building_id: form.dataset.buildingId || null,
                contact_id: form.dataset.contactId || null,
                note: note,
            });
            busy(button, false);
            if (data.success) { location.reload(); } else { toast(data.error || 'Failed', 'error'); }
        });
        textarea.addEventListener('keydown', (e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') button.click();
        });
    });

    window.crmToast = toast;
})();
