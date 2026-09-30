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
const manualLookup = vm.runInContext(
    "buildEnrichButton(missingId, 'Nayan Soni', 'applicant')", context);
assert.match(manualLookup, /truepeoplesearch\.com\/results/);
assert.doesNotMatch(manualLookup, /data-enrich-permit-contact/);
assert.match(enrichButton, /target="_blank" rel="noopener noreferrer"/);

// Source names, encoding, and location fallbacks must produce usable searches.
vm.runInContext("buildingData = { building: { borough: '3', zip_code: '11225' } };", context);
let searchUrl = new URL(vm.runInContext("truePeopleSearchUrl('BROOK, SCHNEUR')", context));
assert.equal(searchUrl.origin, 'https://www.truepeoplesearch.com');
assert.equal(searchUrl.pathname, '/results');
assert.equal(searchUrl.searchParams.get('name'), 'SCHNEUR BROOK');
assert.equal(searchUrl.searchParams.get('citystatezip'), '11225');
searchUrl = new URL(vm.runInContext(
    `truePeopleSearchUrl("O'NEIL, JOSÉ", {city: 'Fort Lee', state: 'NJ'})`, context));
assert.equal(searchUrl.searchParams.get('name'), "JOSÉ O'NEIL");
assert.equal(searchUrl.searchParams.get('citystatezip'), 'Fort Lee, NJ');
searchUrl = new URL(vm.runInContext(
    "truePeopleSearchUrl('Smith, John, Jr.', {zip_code: '07024-1234'})", context));
assert.equal(searchUrl.searchParams.get('name'), 'John Smith Jr.');
assert.equal(searchUrl.searchParams.get('citystatezip'), '07024');
assert.equal(new URL(vm.runInContext("truePeopleSearchUrl('John Smith, Jr.')", context)).searchParams.get('name'), 'John Smith Jr.');
assert.equal(vm.runInContext("renderTruePeopleSearchLink('ZB 521 LLC')", context), '');
assert.equal(vm.runInContext("renderTruePeopleSearchLink('Community Housing', {is_person: false})", context), '');
assert.equal(vm.runInContext("renderTruePeopleSearchLink('')", context), '');
assert.equal(vm.runInContext("renderTruePeopleSearchLink('Jordan Davis', {entity_kind: 'organization'})", context), '');
const escapedLink = vm.runInContext(`renderTruePeopleSearchLink('Jane "JJ" Davis')`, context);
assert.match(escapedLink, /Jane &quot;JJ&quot; Davis/);
assert.match(escapedLink, /&amp;citystatezip=/);
vm.runInContext("buildingData = { building: { borough: '1' } };", context);
assert.equal(new URL(vm.runInContext("truePeopleSearchUrl('Jordan Davis')", context)).searchParams.get('citystatezip'), 'New York, NY');
vm.runInContext("buildingData = { building: { borough: '3' } };", context);
assert.equal(new URL(vm.runInContext("truePeopleSearchUrl('Jordan Davis')", context)).searchParams.get('citystatezip'), 'Brooklyn, NY');

console.log('building profile permit cards and manual people search: passed');

vm.runInContext(`buildingData.owner_source_dates = {
    acris: {reported_date: '2026-09-01', date_label: 'Deed recorded', checked_at: '2026-09-30T12:00:00'},
    rpad: {period: 'FY 2018/19 · Final'},
    pluto: {period: 'PLUTO 26v2', checked_at: '2026-09-30T12:00:00', refresh_failed: true},
    sos: {checked_at: '2026-09-30T12:00:00', note: 'A "quoted" note'},
};`, context);
const deedDate = vm.runInContext("renderOwnerSourceDate('acris')", context);
assert.match(deedDate, /Deed recorded: Sep 1, 2026/);
assert.match(deedDate, /Last checked: Sep 30, 2026/);
assert.match(vm.runInContext("renderOwnerSourceDate('rpad')", context), /FY 2018\/19 · Final/);
const plutoDate = vm.runInContext("renderOwnerSourceDate('pluto')", context);
assert.match(plutoDate, /PLUTO 26v2/);
assert.match(plutoDate, /Refresh unavailable/);
assert.doesNotMatch(plutoDate, /Last reported: Sep 30/);
const sosDate = vm.runInContext("renderOwnerSourceDate('sos')", context);
assert.match(sosDate, /Last reported: date unavailable/);
assert.match(sosDate, /A &quot;quoted&quot; note/);
assert.equal(vm.runInContext("ownerSourceKey('Historical Tax Records (RPAD)')", context), 'rpad');
console.log('owner source report dates, fiscal periods, and separate refresh dates: passed');

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
context.sourceInfo = {url: 'https://apps.dos.ny.gov/publicInquiry/', label: 'NY DOS',
    hint: 'Search DOS ID: 6719932', lookup_value: '6719932', lookup_label: 'DOS ID'};
const linkedName = vm.runInContext("renderSourceName('<img src=x>', sourceInfo)", context);
assert.match(linkedName, /href="https:\/\/apps.dos.ny.gov\/publicInquiry\/"/);
assert.match(linkedName, /rel="noopener noreferrer"/);
assert.match(linkedName, /&lt;img src=x&gt;/);
assert.match(linkedName, /Search DOS ID: 6719932/);
assert.match(linkedName, /data-source-copy="6719932"/);
assert.match(linkedName, /aria-label="Copy DOS ID"/);
context.sourceInfo.lookup_value = '" onmouseover="alert(1)';
assert.doesNotMatch(vm.runInContext("renderSourceName('Name', sourceInfo)", context),
    /data-source-copy="" onmouseover=/);
context.sourceInfo.url = 'javascript:alert(1)';
assert.equal(vm.runInContext("renderSourceName('Name', sourceInfo)", context), 'Name');
assert.equal(vm.runInContext("renderSourceName('Name & Co', undefined)", context), 'Name &amp; Co');
const modal = {innerHTML:'', style:{}, querySelectorAll() {return [];}};
nodes.set('permit-modal', modal);
context.linkedPermit = {...records[0], source_link: {
    url:'https://a810-dobnow.nyc.gov/publish/Index.html#!/', label:'Open in DOB NOW',
    hint:'Under Search the Public Portal, choose Job Number and enter B01344580.',
    lookup_value:'B01344580', lookup_label:'job number'
}, link:'https://a810-bisweb.nyc.gov/wrong-record'};
vm.runInContext('buildingData = {permits:[linkedPermit]}; showPermitDetails(0)', context);
assert.match(modal.innerHTML, /Open in DOB NOW/);
assert.match(modal.innerHTML, /enter B01344580/);
assert.match(modal.innerHTML, /data-source-copy="B01344580"/);
assert.doesNotMatch(modal.innerHTML, /wrong-record/);
const directory = fakeNode('source-directory', 'DIV');
nodes.set('source-directory', directory);
context.directoryLinks = {
    pluto: {url:'https://zola.planning.nyc.gov/l/lot/3/1298/66'},
    ecb: {url:'https://a810-bisweb.nyc.gov/bisweb/ECBQueryByLocationServlet?allbin=3034250'},
    acris_parcel: {url:'https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?borough=3&block=1298&lot=66'},
    dob_now: {url:'https://a810-dobnow.nyc.gov/publish/Index.html#!/'},
};
vm.runInContext('buildingData = {owner_source_links: directoryLinks}; renderDataSourceDirectory()', context);
assert.match(directory.innerHTML, /Direct violation list/);
assert.match(directory.innerHTML, /ECBQueryByLocationServlet/);
assert.match(directory.innerHTML, /ACRIS deeds &amp; mortgages/);

let copyClickHandler;
context.document.addEventListener = (type, handler) => {
    if (type === 'click') copyClickHandler = handler;
};
context.navigator = {clipboard: {writeText: async value => { context.copiedValue = value; }}};
context.setTimeout = () => 0;
vm.runInContext('setupSourceCopyButtons()', context);
const copyButton = {dataset: {sourceCopy: 'B01344580', copyLabel: 'Copy job number'},
    textContent: 'Copy job number', setAttribute(name, value) { this[name] = value; }};
copyClickHandler({target: {closest: () => copyButton}}).then(() => {
    assert.equal(context.copiedValue, 'B01344580');
    assert.equal(copyButton.textContent, 'Copied');
    assert.equal(copyButton['aria-label'], 'Copied');
    console.log('source link, directory, copy, and DOB NOW modal checks passed');
}).catch(error => { console.error(error); process.exitCode = 1; });
