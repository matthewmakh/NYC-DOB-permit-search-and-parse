'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const nodes = new Map();
const handlers = new Map();
const fakeNode = () => ({
    innerHTML: '', textContent: '', value: '', hidden: false, disabled: false, isConnected: true,
    attrs: {}, listeners: {}, classList: {toggle() {}},
    setAttribute(key, value) { this.attrs[key] = value; },
    getAttribute(key) { return this.attrs[key]; },
    removeAttribute(key) { delete this.attrs[key]; if (key === 'href') delete this.href; },
    addEventListener(key, fn) { this.listeners[key] = fn; }, focus() { this.focused = true; },
});
for (const id of ['owner-research-feedback', 'owner-research-people', 'owner-research-conflicts', 'owner-research-sources', 'owner-research-history']) nodes.set(id, fakeNode());
let dialog;
const document = {
    addEventListener(key, fn) { handlers.set(key, fn); },
    getElementById(id) { return nodes.get(id) || null; },
    querySelectorAll() { return []; }, activeElement: fakeNode(),
    body: {appendChild(node) { nodes.set(node.id, node); }},
    createElement(tag) {
        assert.equal(tag, 'dialog');
        const selectors = new Map();
        dialog = {...fakeNode(),
            querySelector(selector) {
                if (!selectors.has(selector)) selectors.set(selector, fakeNode());
                return selectors.get(selector);
            },
            showModal() { this.open = true; },
            close() { this.open = false; this.listeners.close?.(); },
            remove() { this.isConnected = false; nodes.delete(this.id); },
        };
        return dialog;
    },
};
const context = vm.createContext({URL, Date, JSON, Number, String, Set, Map, Math, Object, console,
    BBL: '3011810068', setTimeout: () => 1, clearTimeout() {}, document,
    fetch: async () => ({ok: true, json: async () => ({success: true, people: []})}),
    window: {location: {origin: 'https://local.test'}, addEventListener() {}},
});
vm.runInContext(fs.readFileSync('dashboard_html/static/js/building_profile.js', 'utf8'), context);
const evaluate = code => vm.runInContext(code, context);
const plain = code => JSON.parse(evaluate(`JSON.stringify(${code})`));
context.person = {
    id: 'person-1', name: "O'NEIL, JOSÉ", role: 'Latest deed grantee', is_person: true,
    sources: [{key: 'acris', label: 'ACRIS', reported_date: '2026-09-23', date_label: 'Deed recorded', url: 'https://example.com/deed'}],
    locations: [{id: 'acris-1', city: 'Fort Lee', state: 'NJ', zip_code: '07024', source: 'ACRIS deed', reported_date: '2026-09-23', kind: 'Recorded party address', address_1: '999 NEVER SEND STREET'}],
    default_location_id: 'acris-1', research: {version: 0, status: 'not_researched', match_status: 'unreviewed'},
};
evaluate("buildingData = {building: {borough: '3', zip_code: '11225'}}; ownerResearchState.data = {people: [person], conflicts: []}");
let options = plain('ownerSearchOptions(person)');
assert.equal(options[0].value, 'Fort Lee, NJ');
assert.match(options[0].label, /ACRIS deed.*Sep 23, 2026/);
assert.equal(options.find(option => option.id === 'property').value, '11225');
assert.match(options.find(option => option.id === 'property').label, /property location \(fallback\)/);
assert.equal(options.at(-1).id, 'name-only');
assert.doesNotMatch(JSON.stringify(options), /NEVER SEND STREET/);
assert.equal(new URL(evaluate(`buildPeopleSearchUrl("JOSÉ O'NEIL", '')`)).searchParams.has('citystatezip'), false);
assert.equal(new URL(evaluate(`buildPeopleSearchUrl("JOSÉ O'NEIL", 'Fort Lee, NJ')`)).searchParams.get('name'), "JOSÉ O'NEIL");
assert.equal(evaluate(`buildPeopleSearchUrl('  ', 'NY')`), null);
assert.equal(evaluate("validResearchResultUrl('https://www.truepeoplesearch.com/results?name=Jordan')"), true);
assert.equal(evaluate("validResearchResultUrl('http://www.truepeoplesearch.com/results')"), false);
assert.equal(evaluate("validResearchResultUrl('https://truepeoplesearch.com.evil.test/result')"), false);
assert.equal(evaluate("validResearchResultUrl('https://user@truepeoplesearch.com/result')"), false);
assert.equal(evaluate("validResearchResultUrl('https://truepeoplesearch.com:444/result')"), false);
assert.equal(evaluate(`normalizePeopleSearchName('Smith, John, Jr.')`), 'John Smith Jr.');
assert.equal(evaluate(`researchNameKey('SMITH, JOHN') === researchNameKey('John Smith')`), true);
assert.equal(evaluate(`researchPersonBlocked(person)`), false);
assert.equal(evaluate(`researchPersonBlocked({...person, do_not_contact: true})`), true, 'server DNC flag blocks a card even without a local review');
let card = evaluate('renderResearchPerson(person)');
assert.match(card, /Latest deed grantee/);
assert.match(card, /Unreviewed/);
assert.doesNotMatch(card, /Confirmed match|mailto:|tel:|NEVER SEND STREET/);
context.malicious = {...context.person, name: '<img src=x onerror=alert(1)>', role: 'Owner<script>',
    sources: [{label: '<svg>', url: 'javascript:alert(1)', reported_date: null}],
    research: {status: 'do_not_contact', match_status: 'wrong_person', notes: '</textarea><script>bad()</script>', result_url: 'javascript:alert(1)', phones: ['<img>'], emails: ['" onmouseover="x']},
};
card = evaluate('renderResearchPerson(malicious)');
assert.match(card, /data-research-action="search" disabled/);
assert.match(card, /Do not contact/);
assert.match(card, /Wrong person/);
assert.match(card, /&lt;img src=x onerror=alert\(1\)&gt;/);
assert.doesNotMatch(card, /<script>|<svg>|href="javascript:|<img>/);
evaluate("ownerResearchState.data.people.push({...person, id: 'different-evidence', name: \"JOSÉ O'NEIL\", research: {status: 'do_not_contact'}})");
assert.equal(evaluate('researchPersonBlocked(person)'), true, 'DNC conservatively covers ambiguous same-named records without merging them');
evaluate('ownerResearchState.data.people.pop()');

// The actual external link exists only inside the editable preview dialog.
const trigger = evaluate('renderTruePeopleSearchLink(person.name, person)');
assert.match(trigger, /<button.*data-people-search=/s);
assert.doesNotMatch(trigger, /href=/);
evaluate('openPeopleSearchPreview(person)');
assert.match(dialog.innerHTML, /target="_blank" rel="noopener noreferrer"/);
assert.match(dialog.innerHTML, /name-only/);
assert.equal(dialog.open, true);
const nameField = dialog.querySelector('#research-search-name');
const locationField = dialog.querySelector('#research-search-location');
nameField.value = 'José O’Neil';
locationField.value = 'name-only';
locationField.listeners.change();
assert.equal(new URL(dialog.querySelector('#research-search-open').href).searchParams.has('citystatezip'), false);
locationField.value = 'custom';
const customField = dialog.querySelector('#research-search-custom');
customField.value = '123 Main Street';
customField.listeners.input();
assert.equal(dialog.querySelector('#research-search-open').getAttribute('aria-disabled'), 'true');
assert.match(dialog.querySelector('#research-search-error').textContent, /street address/);
customField.value = '07024'; customField.listeners.input();
assert.equal(new URL(dialog.querySelector('#research-search-open').href).searchParams.get('citystatezip'), '07024');
evaluate("ownerResearchState.data.people.push({id:'dnc-morgan', name:'Morgan Lee', do_not_contact:true, research:{status:'not_researched'}})");
nameField.value = 'Morgan Lee'; nameField.listeners.input();
assert.equal(dialog.querySelector('#research-search-open').getAttribute('aria-disabled'), 'true');
assert.match(dialog.querySelector('#research-search-error').textContent, /Do not contact/);
nameField.value = 'José O’Neil'; nameField.listeners.input();
assert.equal(dialog.querySelector('#research-search-open').getAttribute('aria-disabled'), 'false');
nameField.value = 'LEE, MORGAN'; // A click must re-check even without a preceding input event.
let clickPrevented = false;
dialog.querySelector('#research-search-open').listeners.click({preventDefault() { clickPrevented = true; }});
assert.equal(clickPrevented, true);
assert.equal(dialog.querySelector('#research-search-open').href, undefined);
evaluate('ownerResearchState.data.people.pop()');
nameField.value = ' '; nameField.listeners.input();
assert.equal(dialog.querySelector('#research-search-open').getAttribute('aria-disabled'), 'true');
assert.equal(dialog.querySelector('#research-search-url').value, '');
dialog.close();
assert.equal(document.activeElement.focused, true, 'dialog restores prior focus');

(async () => {
    // Same name alone must never attach a legacy source/contact to another
    // source's address or saved review identity.
    evaluate('setupOwnerResearch()');
    const legacy = {disabled:false, dataset:{peopleSearch:JSON.stringify({name:context.person.name, city:'Albany', state:'NY', is_person:true})}};
    await handlers.get('click')({preventDefault() {}, target:{closest(selector) {return selector === '[data-people-search]' ? legacy : null;}}});
    assert.match(dialog.innerHTML, /Albany, NY — reported contact location/);
    assert.doesNotMatch(dialog.innerHTML, /Fort Lee, NJ —|id="research-search-save"/);
    dialog.close();

    context.fetch = async () => ({ok: false, status: 401, json: async () => ({error: 'Sign in again'})});
    assert.equal(await evaluate('loadOwnerResearch()'), false);
    assert.match(nodes.get('owner-research-feedback').textContent, /Sign in again/);
    assert.equal(evaluate('ownerResearchState.data.people.length'), 1, 'failed reload retains last good cards');
    context.fetch = async () => ({ok: false, status: 500, json: async () => { throw new Error('bad json'); }});
    await assert.rejects(evaluate("researchRequest('/test')"), /unreadable response/);

    // A failed save keeps typed form contents. Background card updates cannot replace the dialog.
    evaluate('openOwnerReview(person)');
    const reviewDialog = dialog;
    const form = dialog.querySelector('#owner-review-form');
    const submit = fakeNode();
    form.querySelector = () => submit;
    form.elements = {
        status: {value: 'needs_review'}, match_status: {value: 'possible_match'},
        result_url: {value: 'https://www.truepeoplesearch.com/result/person'},
        phones: {value: '212-555-0100\n212-555-0100'}, emails: {value: 'jose@example.test'}, notes: {value: 'Keep my draft'},
    };
    let sent;
    context.fetch = async (url, init) => {
        sent = {url, init};
        return {ok: false, status: 409, json: async () => ({error: 'Version conflict'})};
    };
    await form.listeners.submit({preventDefault() {}});
    assert.equal(sent.init.headers['X-Owner-Research'], '1');
    assert.equal(sent.init.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(sent.init.body).phones, ['212-555-0100']);
    assert.equal(JSON.parse(sent.init.body).version, 0);
    assert.match(dialog.querySelector('#research-review-error').textContent, /Another teammate/);
    assert.equal(form.elements.notes.value, 'Keep my draft');
    assert.equal(submit.disabled, false);
    evaluate('renderOwnerResearch()');
    assert.equal(dialog, reviewDialog);
    assert.equal(dialog.open, true);

    context.historyData = {success: true, sources: [{key: 'hpd', label: 'HPD', status: 'failed', can_refresh: true, error: '<script>network</script>'}], history: [
        {source: 'hpd', kind: 'baseline', observed_at: '2026-09-30', after: {records: [{name: '<img>', city: 'New York'}]}},
        {source: 'hpd', kind: 'change', observed_at: '2026-10-01', changes: [{field: 'registered_owner', before: '<old>', after: '<new>'}]},
    ]};
    evaluate('ownerResearchState.sources = historyData; renderOwnerSourceStatus()');
    assert.match(nodes.get('owner-research-history').innerHTML, /First saved snapshot/);
    assert.match(nodes.get('owner-research-history').innerHTML, /Before:<\/span> &lt;old&gt;/);
    assert.doesNotMatch(nodes.get('owner-research-history').innerHTML, /\[object Object\]|<img>/);
    assert.doesNotMatch(nodes.get('owner-research-sources').innerHTML, /<script>/);
    context.retryData = {success:true, sources:[{key:'hpd', label:'HPD', status:'queued', can_refresh:false,
        error:'The latest attempt failed. Previously saved records are preserved.', next_attempt_at:new Date(Date.now()+3600000).toISOString()}], history:[]};
    let pollCalls = 0;
    context.setTimeout = () => {pollCalls += 1; return 1;};
    context.fetch = async () => ({ok:true, json:async () => context.retryData});
    await evaluate('loadOwnerSourceStatus()');
    assert.match(nodes.get('owner-research-sources').innerHTML, /Retry scheduled/);
    assert.match(nodes.get('owner-research-sources').innerHTML, /Next retry/);
    assert.doesNotMatch(nodes.get('owner-research-sources').innerHTML, /Refresh in progress|Refreshing…/);
    assert.equal(pollCalls, 0, 'long scheduled backoff must not poll as though a refresh is running');
    console.log('owner research URL, locality provenance, DNC, XSS, dialog, save conflict, failure recovery, and history checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
