// Local synthetic fixtures only. Guards against reintroducing mixed page themes.
async page => {
    const paths = ['/', '/properties', '/contractors', '/permits', '/buyers', '/alerts',
        '/search-results?q=West', '/property/1001230045', '/permit/1', '/contractor/example',
        '/entities', '/entity/1', '/admin/activity', '/admin/team', '/crm', '/crm/deals',
        '/auth/login', '/auth/signup', '/auth/team-setup', '/auth/verify-email', '/auth/forgot-password'];
    const failures = [], errors = [];
    const check = (ok, message) => { if (!ok) failures.push(message); };
    page.on('pageerror', error => errors.push(`${page.url()}: ${error.message}`));
    await page.setViewportSize({ width: 1440, height: 950 });
    let pageChecks = 0;
    for (const theme of ['light', 'dark']) {
        await page.goto('http://127.0.0.1:5099/');
        await page.evaluate(value => localStorage.setItem('theme', value), theme);
        for (const path of paths) {
            const response = await page.goto('http://127.0.0.1:5099' + path);
            await page.waitForTimeout(200);
            check(response.status() === 200, `${theme} ${path}: HTTP ${response.status()}`);
            const state = await page.evaluate(() => {
                const root = getComputedStyle(document.documentElement);
                return {
                    theme: document.documentElement.dataset.theme,
                    accent: root.getPropertyValue('--accent').trim(),
                    background: getComputedStyle(document.body).backgroundColor,
                    links: [...document.querySelectorAll('link[rel="stylesheet"]')].map(el => el.href),
                    scripts: [...document.querySelectorAll('script[src]')].map(el => el.src),
                    navs: document.querySelectorAll('.site-nav').length,
                    overflow: document.documentElement.scrollWidth > innerWidth + 1,
                };
            });
            check(state.theme === theme, `${theme} ${path}: appearance preference lost`);
            check(state.accent === (theme === 'light' ? '#0f766e' : '#79dccc'), `${theme} ${path}: different accent ${state.accent}`);
            check(state.background === (theme === 'light' ? 'rgb(247, 247, 245)' : 'rgb(14, 14, 16)'), `${theme} ${path}: different canvas ${state.background}`);
            check(!state.links.some(url => /\/(main|styles)\.css$/.test(url)), `${theme} ${path}: legacy theme loaded`);
            check(state.links.filter(url => url.endsWith('/app.css')).length === 1, `${theme} ${path}: app theme absent or duplicated`);
            check(new Set(state.scripts).size === state.scripts.length, `${theme} ${path}: duplicate script execution`);
            check(state.navs === (path.startsWith('/auth/') ? 0 : 1), `${theme} ${path}: navigation missing or duplicated`);
            check(!state.overflow, `${theme} ${path}: page overflow`);
            pageChecks++;
        }
    }
    await page.goto('http://127.0.0.1:5099/permits');
    await page.locator('#permitsList .permit-result-card').first().waitFor();
    const permitSurface = await page.locator('#permitsList .permit-result-card').first().evaluate(el => getComputedStyle(el).backgroundColor);
    check(permitSurface === 'rgb(28, 28, 30)', `Dark permit cards use a different surface: ${permitSurface}`);
    const verticalFields = await page.locator('.filter-panel .filter-group').evaluateAll(groups => groups.every(group => {
        const label = group.querySelector('label'), input = group.querySelector('input,select');
        return !label || !input || label.getBoundingClientRect().bottom <= input.getBoundingClientRect().top + 1;
    }));
    check(verticalFields, 'Permit filter labels and controls do not stack vertically');
    await page.getByRole('button', { name: /Top Contractors/ }).click();
    const contractorLink = page.locator('#contractorsList a').first();
    await contractorLink.waitFor();
    check((await contractorLink.getAttribute('href')).startsWith('/contractor/'), 'Permit contractor results do not use the canonical profile');
    check(await page.locator('#contractorModal').count() === 0, 'Legacy contractor profile modal is still present');
    await page.goto('http://127.0.0.1:5099/properties');
    await page.getByRole('button', { name: 'Appearance', exact: true }).click();
    await page.getByRole('menuitemradio', { name: 'Light', exact: true }).click();
    await page.goto('http://127.0.0.1:5099/crm');
    check(await page.evaluate(() => document.documentElement.dataset.theme === 'light'), 'CRM lost the nav appearance preference');
    check(errors.length === 0, `Uncaught browser errors: ${errors.join('; ')}`);
    if (failures.length) throw new Error(JSON.stringify({ pageChecks, failures }));
    return { pageChecks, failures, checks: 'shared canvas and accent, light/dark preference, shell assets, script uniqueness, navigation, overflow, permit field layout, CRM theme persistence, uncaught JavaScript errors' };
}
