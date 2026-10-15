'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('dashboard_html/static/js/properties.js', 'utf8');
const storage = new Map();
const listLocation = {
    pathname: '/properties',
    search: '?search=boiler&borough=3&financing_min=65&sort_by=value&page=4&per_page=25',
    href: '',
};
let documentY = 1800;

const context = vm.createContext({
    URLSearchParams,
    Date,
    JSON,
    Number,
    String,
    Set,
    Math,
    console,
    encodeURIComponent,
    setTimeout,
    clearTimeout,
    document: {
        readyState: 'loading',
        getElementById() { return null; },
        addEventListener() {},
        querySelector(selector) {
            if (!selector.includes('data-property-bbl')) return null;
            return { getBoundingClientRect: () => ({
                top: documentY - context.window.scrollY,
            }) };
        },
    },
    SharedFilters: {
        toPayload(shared) { return { borough: shared.boroughFilter || [] }; },
        fromParams(params) {
            return { boroughFilter: params.getAll('borough') };
        },
        toParams(params, shared) {
            (shared.boroughFilter || []).forEach(
                value => params.append('borough', value));
        },
    },
    MultiSelect: {},
    window: {
        location: listLocation,
        history: {
            scrollRestoration: 'auto',
            replaceState(_state, _title, next) {
                const parsed = new URL(next, 'https://local.test');
                listLocation.pathname = parsed.pathname;
                listLocation.search = parsed.search;
            },
        },
        sessionStorage: {
            setItem(key, value) { storage.set(key, value); },
            getItem(key) { return storage.has(key) ? storage.get(key) : null; },
            removeItem(key) { storage.delete(key); },
        },
        addEventListener() {},
        requestAnimationFrame(callback) { callback(); },
        scrollY: 1200,
        scrollTo({ top }) { this.scrollY = top; },
    },
});

vm.runInContext(source, context);
vm.runInContext(
    'restoreStateFromUrl(new URLSearchParams(window.location.search));', context);

const restored = JSON.parse(vm.runInContext(`JSON.stringify({
    search: state.filters.search,
    financing: state.filters.financingMin,
    borough: state.shared.boroughFilter,
    sort: state.sort.by,
    page: state.pagination.page,
    perPage: state.pagination.perPage,
    query: buildPropertiesParams().toString()
})`, context));

assert.equal(restored.search, 'boiler');
assert.equal(restored.financing, 65, 'percentage must not be divided twice');
assert.deepEqual(restored.borough, ['3']);
assert.deepEqual(restored.sort, ['value']);
assert.equal(restored.page, 4);
assert.equal(restored.perPage, 25);
assert.match(restored.query, /financing_min=65/);
assert.match(restored.query, /borough=3/);
assert.match(restored.query, /page=4/);

vm.runInContext("viewProperty('1000000018')", context);
const detailUrl = context.window.location.href;
assert.match(detailUrl, /^\/property\/1000000018\?return_to=/);
assert.match(decodeURIComponent(detailUrl), /\/properties\?search=boiler/);
assert.ok(storage.has('properties:list-navigation:v1'));

// Simulate a full list reload at scroll zero. The card's document position is
// unchanged; restoration should put it at the same viewport offset.
context.window.location.href = '';
context.window.scrollY = 0;
vm.runInContext(
    'initialPlaysSettled = true; initialPropertiesSettled = true; ' +
    'restoreListPositionIfReady(false);', context);
assert.equal(context.window.scrollY, 1200);
assert.equal(storage.has('properties:list-navigation:v1'), false);

console.log('properties navigation state: 13 checks passed');

// The person-owner checkbox survives links, bulk requests, and saved searches.
vm.runInContext("restoreStateFromUrl(new URLSearchParams('has_person_owner=true&owner_kind=llc&page=3'))", context);
assert.equal(vm.runInContext('state.filters.hasPersonOwner', context), true);
assert.equal(vm.runInContext("buildPropertiesParams().get('has_person_owner')", context), 'true');
assert.equal(vm.runInContext('buildBulkEnrichFiltersPayload().has_person_owner', context), true);
assert.match(vm.runInContext("describeSearch('has_person_owner=true').join(', ')", context), /Person listed as owner/);
assert.equal(vm.runInContext("buildPropertiesParams().get('owner_kind')", context), 'llc');
vm.runInContext('resetFilters()', context);
assert.equal(vm.runInContext('state.filters.hasPersonOwner', context), false);
assert.equal(vm.runInContext("buildPropertiesParams().has('has_person_owner')", context), false);
vm.runInContext("restoreStateFromUrl(new URLSearchParams('has_person_owner=false'))", context);
assert.equal(vm.runInContext('state.filters.hasPersonOwner', context), false);
console.log('person-owner URL, saved-search, bulk and reset checks passed');

// Clear all must also work before plays load, or when none are available.
const controls = new Map();
context.document.getElementById = id => {
    if (!controls.has(id)) controls.set(id, { checked: true, value: 'old', style: {}, innerHTML: '' });
    return controls.get(id);
};
context.SharedFilters.clear = () => {};
context.SharedFilters.read = () => ({});
vm.runInContext('loadProperties = () => {}; state.filters.hasPersonOwner = true; clearFilters();', context);
assert.equal(controls.get('hasPersonOwner').checked, false);
assert.equal(vm.runInContext('state.filters.hasPersonOwner', context), false);
assert.equal(vm.runInContext('state.pagination.page', context), 1);
console.log('clear-all with unavailable prebuilt filters passed');
