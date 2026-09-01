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
