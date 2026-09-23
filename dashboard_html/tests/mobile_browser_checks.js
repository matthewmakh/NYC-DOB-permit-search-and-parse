// Run with playwright-cli -s=mobile run-code --filename=dashboard_html/tests/mobile_browser_checks.js
// Start mobile_preview.py first. All data is local sample data.
async page => {
    page.setDefaultTimeout(5000);
    const failures = [];
    const check = (ok, message) => { if (!ok) failures.push(message); };
    const paths = ['/', '/properties', '/contractors', '/permits', '/buyers', '/alerts',
        '/search?q=West', '/property/1001230045', '/permit/1', '/crm', '/crm/buildings',
        '/crm/buildings?view=table', '/crm/buildings?view=board', '/crm/deals', '/crm/contacts',
        '/crm/notifications', '/crm/focus', '/crm/building_detail', '/crm/contact_detail', '/crm/deal_detail', '/crm/deal_form', '/crm/building_add', '/crm/lists', '/crm/list_detail', '/crm/starred', '/crm/team', '/crm/history', '/crm/followups', '/contractor/example', '/auth/team-setup', '/auth/login', '/auth/signup', '/admin/team', '/admin/activity'];
    let pageChecks = 0;
    for (const width of [320, 375, 768, 1440]) {
        await page.setViewportSize({ width, height: 844 });
        for (const path of paths) {
            const response = await page.goto('http://127.0.0.1:5099' + path, { waitUntil: 'domcontentloaded' });
            await page.waitForTimeout(150);
            check(response.status() === 200, `${width} ${path}: HTTP ${response.status()}`);
            const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
            check(!overflow, `${width} ${path}: page overflows horizontally`);
            pageChecks++;
        }
    }
    await page.setViewportSize({ width: 375, height: 812 });
    for (const [path, input] of [['/properties', '#universalSearch'], ['/contractors', '#contractorSearch'], ['/permits', '#searchInput']]) {
        await page.goto('http://127.0.0.1:5099' + path);
        const trigger = page.locator('.mobile-filter-trigger');
        await trigger.click();
        check(await page.locator('.mobile-filter-dialog').evaluate(e => e.open), `${path}: filter drawer did not open`);
        await page.locator(input).fill('West');
        await page.keyboard.press('Escape');
        check(await trigger.evaluate(e => document.activeElement === e), `${path}: focus did not return`);
        await trigger.click();
        check(await page.locator(input).inputValue() === 'West', `${path}: filter value lost`);
        await page.getByRole('button', { name: 'Show results', exact: true }).click();
        await page.setViewportSize({ width: 1440, height: 900 });
        await page.locator(input).waitFor({ state: 'visible' });
        check(await page.locator(input).isVisible(), `${path}: desktop filter not restored`);
        check(await page.locator(input).inputValue() === 'West', `${path}: resize lost filter value`);
        await page.setViewportSize({ width: 375, height: 812 });
    }
    await page.goto('http://127.0.0.1:5099/permits');
    check(!await page.locator('#constructionMap').isVisible(), 'Permit map should start collapsed on mobile');
    await page.locator('.mobile-map-toggle').click();
    check(await page.locator('#constructionMap').isVisible(), 'Permit map toggle did not reveal map');
    await page.locator('.mobile-map-toggle').click();
    await page.getByRole('button', { name: /Top Contractors/ }).click();
    await page.locator('#contractorsTab').waitFor({ state: 'visible' });
    check(await page.locator('#contractorsTab').isVisible(), 'Permit tab switch failed');

    await page.goto('http://127.0.0.1:5099/crm/deals');
    check(await page.locator('.mobile-record-table td').first().evaluate(e => getComputedStyle(e).display === 'block'), 'Deals did not become mobile records');
    await page.getByRole('button', { name: /More$/ }).click();
    check(await page.locator('#sheetMore').evaluate(e => e.classList.contains('is-open')), 'CRM More menu did not open');
    check(await page.locator('.crm-app').evaluate(e => e.inert), 'CRM background remains interactive behind sheet');
    await page.keyboard.press('Escape');
    check(!await page.locator('.crm-app').evaluate(e => e.inert), 'CRM remained inert after closing sheet');

    await page.goto('http://127.0.0.1:5099/search?q=West');
    await page.locator('#openFiltersBtn').click();
    await page.keyboard.press('Escape');
    check(await page.locator('#openFiltersBtn').evaluate(e => document.activeElement === e), 'Search focus did not return');
    await page.locator('#openFiltersBtn').click();
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForFunction(() => !document.body.classList.contains('sr-filters-open'));
    check(!await page.evaluate(() => document.body.classList.contains('sr-filters-open')), 'Search scroll lock survived desktop resize');

    await page.setViewportSize({ width: 667, height: 375 });
    await page.goto('http://127.0.0.1:5099/properties');
    await page.locator('.site-nav__toggle').click();
    await page.locator('.site-nav__link[href="/crm"]').scrollIntoViewIfNeeded();
    check(await page.locator('.site-nav__link[href="/crm"]').isVisible(), 'Landscape navigation inaccessible');
    await page.keyboard.press('Escape');
    await page.locator('.mobile-filter-trigger').click();
    await page.getByRole('button', { name: 'Show results', exact: true }).click();

    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' });
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto('http://127.0.0.1:5099/properties');
    check(await page.evaluate(() => document.documentElement.dataset.theme === 'dark'), 'Dark theme not applied');
    await page.locator('.mobile-filter-trigger').click();
    check(await page.locator('.mobile-filter-dialog').evaluate(e => getComputedStyle(e).backgroundColor !== 'rgb(255, 255, 255)'), 'Dark drawer uses a light surface');
    await page.keyboard.press('Escape');
    await page.emulateMedia({ reducedMotion: 'no-preference', colorScheme: 'light' });
    return { pageChecks, failures, interactions: 'filters, persistence, breakpoint changes, map, tabs, CRM sheets, search drawer, landscape menu, dark mode, reduced motion' };
}
