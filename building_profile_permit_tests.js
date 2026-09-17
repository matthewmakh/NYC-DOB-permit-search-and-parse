'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(
    'dashboard_html/static/js/building_profile.js', 'utf8');

const context = vm.createContext({
    URL,
    Date,
    JSON,
    Number,
    String,
    Set,
    Map,
    Math,
    console,
    BBL: '3011810068',
    setTimeout,
    clearTimeout,
    fetch: async () => ({ json: async () => ({ success: false }) }),
    document: {
        addEventListener() {},
        getElementById() { return null; },
        querySelectorAll() { return []; },
    },
    window: {
        location: { origin: 'https://local.test' },
        addEventListener() {},
        matchMedia() { return { matches: false, addEventListener() {} }; },
    },
});

vm.runInContext(source, context);

const records = [
    {
        id: 10,
        permit_no: 'B01344580-P1',
        issue_date: null,
        filing_date: '2026-04-22',
        record_kind: 'job_filing',
        job_type: 'Alteration',
        job_type_label: 'Alteration',
        filing_status: 'Approved',
        applicant: 'Nayan Soni',
        work_description: 'Type: Alteration, Building: Other, Est. Cost: $1',
    },
    {
        id: 11,
        permit_no: 'B00863621-P1',
        issue_date: null,
        filing_date: '2026-05-08',
        record_kind: 'job_filing',
        job_type: 'New Building',
        job_type_label: 'New Building',
        filing_status: 'Plan Examiner Review',
        applicant: 'Nataliya Donskoy',
    },
    {
        id: 12,
        permit_no: 'B01422328-I1',
        issue_date: '2026-06-01',
        filing_date: null,
        record_kind: 'issued_permit',
        work_type: 'Protection and Mechanical Methods',
        work_type_label: 'Protection and Mechanical Methods',
        permit_status: 'Permit Issued',
        applicant: 'CORE SCAFFOLD SYSTEMS INC',
        work_description: 'INSTALLATION OF TEMPORARY ROOF PROTECTION AS PER PLANS.',
    },
    {
        id: 13,
        permit_no: 'B01343302-P1',
        issue_date: null,
        filing_date: '2026-02-17',
        record_kind: 'job_filing',
        job_type: 'Alteration',
        filing_status: 'Objections',
        applicant: '<img src=x onerror=alert(1)>',
    },
];

context.records = records;
const newest = JSON.parse(vm.runInContext(
    "JSON.stringify(records.slice().sort((a, b) => comparePermitDates(a, b, 'desc')).map(p => p.permit_no))",
    context));
const oldest = JSON.parse(vm.runInContext(
    "JSON.stringify(records.slice().sort((a, b) => comparePermitDates(a, b, 'asc')).map(p => p.permit_no))",
    context));

assert.deepEqual(newest, [
    'B01422328-I1', 'B00863621-P1', 'B01344580-P1', 'B01343302-P1',
]);
assert.deepEqual(oldest, [
    'B01343302-P1', 'B01344580-P1', 'B00863621-P1', 'B01422328-I1',
]);

context.testPermit = records[3];
const filingCard = vm.runInContext('renderPermitCard(testPermit, 3)', context);
assert.match(filingCard, /<button type="button" class="permit-card"/);
assert.match(filingCard, /Job filing/);
assert.match(filingCard, /permit-status-critical/);
assert.match(filingCard, /Objections/);
assert.match(filingCard, /&lt;img src=x onerror=alert\(1\)&gt;/);
assert.doesNotMatch(filingCard, /<img src=x/);
assert.match(filingCard, /Filed/);

context.testPermit = records[2];
const issuedCard = vm.runInContext('renderPermitCard(testPermit, 2)', context);
assert.match(issuedCard, /Issued permit/);
assert.match(issuedCard, /Protection and Mechanical Methods/);
assert.match(issuedCard, /INSTALLATION OF TEMPORARY ROOF PROTECTION/);
assert.match(issuedCard, /permit-status-positive/);

context.testPermit = records[0];
const genericCard = vm.runInContext('renderPermitCard(testPermit, 0)', context);
assert.doesNotMatch(genericCard, /Est\. Cost/);

vm.runInContext("buildingData = { building: { id: 5, bbl: '3011810068' } };", context);
context.enrichable = { id: 10 };
context.missingId = {};
const enrichButton = vm.runInContext(
    "buildEnrichButton(enrichable, 'Nayan Soni', 'applicant')", context);
assert.match(enrichButton, /data-enrich-permit-contact/);
assert.doesNotMatch(enrichButton, /onclick=/);
assert.equal(vm.runInContext(
    "buildEnrichButton(missingId, 'Nayan Soni', 'applicant')", context), '');

console.log('building profile permit cards: 17 checks passed');

// Disclosure navigation, persistence, and lazy loading without a browser.
const storage = new Map();
const nodes = new Map();
function fakeNode(id, tagName = 'DETAILS') {
    const listeners = {};
    const attributes = {};
    return { id, tagName, open: false, hidden: true, textContent: '',
        classList: { toggle() {} },
        addEventListener(type, handler) { listeners[type] = handler; },
        dispatch(type) { listeners[type]?.(); },
        setAttribute(name, value) { attributes[name] = value; },
        getAttribute(name) { return attributes[name]; },
        scrollIntoView(options) { this.scrollOptions = options; },
        click() { listeners.click?.(); },
    };
}
const disclosures = ['activity', 'permits', 'transactions', 'violations', 'contacts']
    .map(name => fakeNode(`tab-${name}`));
disclosures.forEach(node => nodes.set(node.id, node));
nodes.set('tab-building', fakeNode('tab-building', 'SECTION'));
nodes.set('building-facts-toggle', fakeNode('building-facts-toggle', 'BUTTON'));
nodes.set('building-facts-groups', fakeNode('building-facts-groups', 'DIV'));
context.document.getElementById = id => nodes.get(id) || null;
context.document.querySelectorAll = selector => selector === '.profile-disclosure' ? disclosures : [];
context.window.location.hash = '';
context.window.sessionStorage = {
    getItem: key => storage.get(key) || null,
    setItem: (key, value) => storage.set(key, value),
};
vm.runInContext('setupProfileDisclosures(); setupBuildingFactsDisclosure()', context);
assert.ok(disclosures.every(node => !node.open), 'record sections start collapsed');
assert.equal(nodes.get('building-facts-groups').hidden, true, 'full record starts compact on desktop too');
vm.runInContext("switchTab('permits')", context);
assert.equal(nodes.get('tab-permits').open, true, 'navigation reveals the destination');
assert.equal(nodes.get('tab-permits').scrollOptions.block, 'start');
assert.equal(nodes.get('tab-activity').open, false, 'other sections retain their state');
nodes.get('tab-permits').dispatch('toggle');
assert.equal(JSON.parse(storage.get('property-sections:v1:3011810068'))['tab-permits'], true);
nodes.get('tab-permits').open = false;
vm.runInContext('setupProfileDisclosures()', context);
assert.equal(nodes.get('tab-permits').open, true, 'reload restores section preference');
context.window.matchMedia = () => ({matches: true});
vm.runInContext("switchTab('transactions'); switchTab('building')", context);
assert.equal(nodes.get('tab-transactions').scrollOptions.behavior, 'auto');
assert.equal(nodes.get('building-facts-groups').hidden, false, 'Building nav opens full facts');
context.window.location.hash = '#tab-activity';
vm.runInContext('setupProfileDisclosures()', context);
assert.equal(nodes.get('tab-activity').open, true, 'deep links reveal collapsed content');
context.window.location.hash = '';
let lazyCalls = 0;
context.countLazyLoad = () => { lazyCalls += 1; };
vm.runInContext('loadViolationDetailsOnce = countLazyLoad; setupViolationsLazyLoad()', context);
assert.equal(lazyCalls, 0, 'closed violation section makes no live request');
nodes.get('tab-violations').open = true;
nodes.get('tab-violations').dispatch('toggle');
assert.equal(lazyCalls, 1, 'expanding violations requests live details');
context.window.sessionStorage.getItem = () => { throw new Error('storage blocked'); };
context.window.sessionStorage.setItem = () => { throw new Error('storage blocked'); };
assert.doesNotThrow(() => vm.runInContext('setupProfileDisclosures()', context));
assert.doesNotThrow(() => nodes.get('tab-permits').dispatch('toggle'));
console.log('profile disclosure navigation, persistence, reduced motion and lazy-loading checks passed');

// Source links must survive rendering and keep untrusted record text escaped.
context.sourceInfo = {url: 'https://apps.dos.ny.gov/publicInquiry/', label: 'NY DOS', hint: 'Search DOS ID: 6719932'};
const linkedName = vm.runInContext("renderSourceName('<img src=x>', sourceInfo)", context);
assert.match(linkedName, /href="https:\/\/apps.dos.ny.gov\/publicInquiry\/"/);
assert.match(linkedName, /rel="noopener noreferrer"/);
assert.match(linkedName, /&lt;img src=x&gt;/);
assert.match(linkedName, /Search DOS ID: 6719932/);
context.sourceInfo.url = 'javascript:alert(1)';
assert.equal(vm.runInContext("renderSourceName('Name', sourceInfo)", context), 'Name');
assert.equal(vm.runInContext("renderSourceName('Name & Co', undefined)", context), 'Name &amp; Co');
const modal = {innerHTML:'', style:{}, querySelectorAll() {return [];}};
nodes.set('permit-modal', modal);
context.linkedPermit = {...records[0], source_link: {
    url:'https://a810-dobnow.nyc.gov/publish/Index.html#!/', label:'Open in DOB NOW',
    hint:'Under Search the Public Portal, choose Job Number and enter B01344580.'
}, link:'https://a810-bisweb.nyc.gov/wrong-record'};
vm.runInContext('buildingData = {permits:[linkedPermit]}; showPermitDetails(0)', context);
assert.match(modal.innerHTML, /Open in DOB NOW/);
assert.match(modal.innerHTML, /enter B01344580/);
assert.doesNotMatch(modal.innerHTML, /wrong-record/);
console.log('source link rendering and DOB NOW modal checks passed');
