(() => {
    const root = document.querySelector('.team-page');
    if (!root) return;

    const csrf = root.dataset.csrfToken;
    const membersList = document.getElementById('membersList');
    const teamError = document.getElementById('teamError');
    const inviteForm = document.getElementById('inviteForm');
    const inviteBtn = document.getElementById('inviteBtn');
    const inviteResult = document.getElementById('inviteResult');
    const inviteUrl = document.getElementById('inviteUrl');

    const escapeHtml = (value) => {
        const node = document.createElement('div');
        node.textContent = value == null ? '' : String(value);
        return node.innerHTML;
    };

    const money = (value) => new Intl.NumberFormat('en-US', {
        style: 'currency', currency: 'USD'
    }).format(Number(value || 0));

    const shortDate = (value) => value
        ? new Intl.DateTimeFormat('en-US', {month: 'short', day: 'numeric', year: 'numeric'}).format(new Date(value))
        : '—';

    const showError = (message) => {
        teamError.textContent = message;
        teamError.hidden = false;
    };

    const clearError = () => {
        teamError.hidden = true;
        teamError.textContent = '';
    };

    async function api(url, options = {}) {
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': csrf,
                ...(options.headers || {})
            }
        });
        const data = await response.json();
        if (!response.ok || !data.success) throw new Error(data.error || 'Request failed');
        return data;
    }

    function renderBilling(billing) {
        const status = document.getElementById('billingStatus');
        const method = document.getElementById('billingMethod');
        if (billing.ready) {
            status.textContent = 'Ready';
            status.className = 'status-pill is-ready';
            const brand = (billing.brand || 'Card').replace(/^./, c => c.toUpperCase());
            method.innerHTML = `
                <div class="billing-method__icon"><i class="fas fa-credit-card" aria-hidden="true"></i></div>
                <div><strong>${escapeHtml(brand)} •••• ${escapeHtml(billing.last4)}</strong>
                <span>Expires ${escapeHtml(billing.exp_month)}/${escapeHtml(billing.exp_year)}</span></div>`;
        } else {
            status.textContent = billing.unavailable ? 'Unavailable' : 'Card required';
            status.className = 'status-pill is-warning';
            method.innerHTML = `
                <div class="billing-method__icon"><i class="fas fa-triangle-exclamation" aria-hidden="true"></i></div>
                <div><strong>${billing.unavailable ? 'Stripe could not be reached' : 'No usage card is ready'}</strong>
                <span>${billing.unavailable ? 'Try again in a moment.' : 'Add a card before employees run paid lookups.'}</span></div>`;
        }
    }

    function memberActions(member) {
        if (member.status === 'pending' || member.status === 'expired') {
            return `<button class="btn btn-secondary" data-action="regenerate" data-id="${member.id}">New link</button>
                    ${member.status === 'pending' ? `<button class="btn btn-danger" data-action="revoke" data-id="${member.id}">Cancel</button>` : ''}`;
        }
        if (member.status === 'active') {
            return `<button class="btn btn-danger" data-action="revoke" data-id="${member.id}">Revoke access</button>`;
        }
        if (member.member_user_id) {
            return `<button class="btn btn-secondary" data-action="reactivate" data-id="${member.id}">Re-enable</button>`;
        }
        return '';
    }

    function renderMembers(members) {
        if (!members.length) {
            membersList.innerHTML = '<div class="team-empty">No employee accounts yet. Add the first one above.</div>';
            return;
        }
        membersList.innerHTML = members.map(member => {
            const statusLabel = member.status === 'pending' ? 'Pending setup' :
                member.status.charAt(0).toUpperCase() + member.status.slice(1);
            const secondary = member.status === 'pending'
                ? `Setup link expires ${shortDate(member.invite_expires_at)}`
                : `Last login ${shortDate(member.last_login)}`;
            return `<article class="member-row">
                <div class="member-identity">
                    <strong>${escapeHtml(member.display_name || member.member_email)}</strong>
                    <span>${escapeHtml(member.member_email)}</span>
                    <span>${escapeHtml(secondary)}</span>
                </div>
                <div><span class="member-status member-status--${escapeHtml(member.status)}">${escapeHtml(statusLabel)}</span></div>
                <div class="member-meta"><span>Usage paid by you</span><strong>${money(member.spend_30d)} · 30d</strong></div>
                <div class="member-actions">${memberActions(member)}</div>
            </article>`;
        }).join('');
    }

    async function loadTeam() {
        clearError();
        root.setAttribute('aria-busy', 'true');
        try {
            const data = await api('/api/admin/team');
            document.getElementById('activeCount').textContent = data.summary.active;
            document.getElementById('pendingCount').textContent = data.summary.pending;
            document.getElementById('spend30d').textContent = money(data.summary.spend_30d);
            renderBilling(data.billing);
            renderMembers(data.members);
        } catch (error) {
            showError(error.message);
            membersList.innerHTML = '<div class="team-empty">Team accounts could not be loaded.</div>';
        } finally {
            root.removeAttribute('aria-busy');
        }
    }

    inviteForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        clearError();
        inviteBtn.disabled = true;
        inviteBtn.textContent = 'Assigning…';
        try {
            const data = await api('/api/admin/team/invitations', {
                method: 'POST',
                body: JSON.stringify({
                    email: document.getElementById('employeeEmail').value,
                    display_name: document.getElementById('employeeName').value
                })
            });
            inviteForm.reset();
            if (data.account.invite_url) {
                inviteUrl.value = data.account.invite_url;
                inviteResult.hidden = false;
                inviteResult.scrollIntoView({behavior: 'smooth', block: 'nearest'});
            } else {
                inviteResult.hidden = true;
            }
            await loadTeam();
        } catch (error) {
            showError(error.message);
        } finally {
            inviteBtn.disabled = false;
            inviteBtn.innerHTML = '<i class="fas fa-user-plus" aria-hidden="true"></i> Assign account';
        }
    });

    document.getElementById('copyInviteBtn').addEventListener('click', async (event) => {
        await navigator.clipboard.writeText(inviteUrl.value);
        event.currentTarget.textContent = 'Copied';
        setTimeout(() => { event.currentTarget.textContent = 'Copy link'; }, 1600);
    });

    document.getElementById('setupBillingBtn').addEventListener('click', async (event) => {
        const button = event.currentTarget;
        const original = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<i class="fas fa-circle-notch fa-spin" aria-hidden="true"></i> Opening Stripe…';
        try {
            const data = await api('/api/admin/team/billing/setup', {method: 'POST', body: '{}'});
            window.location.assign(data.checkout_url);
        } catch (error) {
            showError(error.message);
            button.disabled = false;
            button.innerHTML = original;
        }
    });

    membersList.addEventListener('click', async (event) => {
        const button = event.target.closest('[data-action]');
        if (!button) return;
        const action = button.dataset.action;
        if (action === 'revoke' && !window.confirm('Revoke this employee’s sponsored access? Their active sessions will end immediately.')) return;
        const original = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<i class="fas fa-circle-notch fa-spin" aria-hidden="true"></i> Working…';
        try {
            const data = await api(`/api/admin/team/${button.dataset.id}/${action}`, {method: 'POST', body: '{}'});
            if (data.account && data.account.invite_url) {
                inviteUrl.value = data.account.invite_url;
                inviteResult.hidden = false;
            }
            await loadTeam();
        } catch (error) {
            showError(error.message);
            button.disabled = false;
            button.innerHTML = original;
        }
    });

    document.getElementById('refreshTeamBtn').addEventListener('click', async (event) => {
        const button = event.currentTarget;
        const original = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<i class="fas fa-circle-notch fa-spin" aria-hidden="true"></i> Refreshing…';
        await loadTeam();
        button.disabled = false;
        button.innerHTML = original;
    });
    loadTeam();
})();
