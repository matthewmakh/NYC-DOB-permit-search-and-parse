// Local UI fixture only: no real accounts, enrichment providers, or payments.
async page => {
    const origin = 'http://127.0.0.1:5099';
    if (!page.url().startsWith(origin + '/')) throw new Error('Use the local mobile preview.');
    const assert = (ok, message) => { if (!ok) throw new Error(message); };
    const errors = [];
    const onError = error => errors.push(error.message);
    page.on('pageerror', onError);
    await page.setViewportSize({width:1440, height:950});
    try {
        for (const [path, api, input, list, card, key] of [
            ['/buyers', '/api/repeat-buyers', '#buyerSearch', '#buyersList', '.buyer-card', 'buyers'],
            ['/alerts', '/api/sales-alerts', '#alertSearch', '#alertList', '.alert-card', 'alerts'],
        ]) {
            let mode = 'upstream';
            const payload = label => ({success:true, [key]: key === 'buyers'
                ? [{buyer_name:label, account_score:80, distinct_projects:3}]
                : [{title:label, summary:'Synthetic alert', alert_type:'new_filing'}]});
            const pattern = '**' + api + '*';
            await page.route(pattern, async route => {
                if (mode === 'upstream') return route.fulfill({status:502, contentType:'text/html', body:'Bad gateway'});
                if (mode === 'network') return route.abort('failed');
                const params = new URL(route.request().url()).searchParams;
                const label = params.get('search') || params.get('q') || 'Loaded fixture';
                if (mode === 'race' && label === 'Old request') await new Promise(resolve => setTimeout(resolve, 1000));
                await route.fulfill({json:payload(label)});
            });
            try {
                await page.goto(origin + path);
                await page.locator(list).getByRole('button', {name:'Retry', exact:true}).waitFor();
                assert(!(await page.locator(list).innerText()).includes('Loading'), path + ': upstream error stops loading');
                mode = 'network';
                await page.locator(list).getByRole('button', {name:'Retry', exact:true}).click();
                await page.locator(list + ' [role=alert]').waitFor();
                mode = 'success';
                await page.locator(list).getByRole('button', {name:'Retry', exact:true}).click();
                await page.locator(card).waitFor();
                assert((await page.locator(list).innerText()).includes('Loaded fixture'), path + ': retry recovers');
                mode = 'race';
                const oldRequest = page.waitForRequest(request => {
                    const url = new URL(request.url());
                    return url.pathname === api && [...url.searchParams.values()].includes('Old request');
                });
                await page.locator(input).fill('Old request');
                await oldRequest;
                await page.locator(input).fill('Latest request');
                await page.waitForFunction(selector => document.querySelector(selector).textContent.includes('Latest request'), list);
                await page.waitForTimeout(1100);
                assert(!(await page.locator(list).innerText()).includes('Old request'), path + ': stale response cannot overwrite new results');
            } finally { await page.unroute(pattern); }
        }

        let permitMode = 'failure';
        const permitPattern = '**/api/construction/permits*';
        const permitFixture = await (await page.request.get(origin + '/api/construction/permits')).json();
        await page.route(permitPattern, route => permitMode === 'failure'
            ? route.fulfill({status:503, json:{success:false}})
            : route.fulfill({json:permitFixture}));
        try {
            await page.goto(origin + '/permits');
            await page.locator('#retryPermits').waitFor();
            permitMode = 'success';
            await page.locator('#retryPermits').click();
            await page.locator('.permit-result-card').first().waitFor();
            assert((await page.locator('.permit-result-card').first().getAttribute('href')) === '/permit/1', 'Permit cards use keyboard-accessible profile links');
            for (const sort of ['date_asc', 'address', 'applicant', 'type', 'score', 'contacts']) {
                const sent = page.waitForRequest(request => new URL(request.url()).pathname === '/api/construction/permits');
                await page.locator('#sortBy').selectOption(sort);
                assert(new URL((await sent).url()).searchParams.get('sort') === sort, 'Permit sort reaches server: ' + sort);
            }
            assert(await page.locator('#perPage option[value="500"],#perPage option[value="1000"]').count() === 0, 'Page sizes respect API capacity');
        } finally { await page.unroute(permitPattern); }

        let unlocked = false, failPayment = true;
        const requests = [];
        const contact = {id:71, name:'Taylor Tester', type:'applicant', enriched:true, has_access:false};
        const getPattern = '**/api/building/*/enriched-contacts';
        const postPattern = '**/api/enrichment/permit-contact';
        await page.route(getPattern, route => route.fulfill({json:{success:true, owner_contacts:[],
            permit_contacts:[unlocked ? {...contact,has_access:true,phones:[{number:'2125550199'}],emails:[]} : contact]}}));
        await page.route(postPattern, route => {
            requests.push(route.request().postDataJSON());
            if (failPayment) return route.fulfill({status:402,json:{success:false,error:'Synthetic billing failure'}});
            unlocked = true;
            return route.fulfill({json:{success:true, charged:true, data:{phones:[],emails:[]}}});
        });
        try {
            await page.goto(origin + '/property/1001230045');
            await page.locator('[data-tab=contacts]').click();
            const button = page.locator('[data-unlock-permit-contact="71"]');
            await button.waitFor();
            await page.evaluate(() => { window.confirm = () => false; });
            await button.click();
            assert(requests.length === 0, 'Cancelling unlock makes no purchase request');
            await page.evaluate(() => { window.confirm = () => true; });
            await button.click();
            await page.locator('.contact-card .enrich-error').waitFor();
            assert(await button.isEnabled(), 'Billing failure restores unlock button');
            assert(await button.innerText() === 'Unlock for $0.50', 'Unlock label survives failure');
            assert(requests[0].bbl === '1001230045' && requests[0].contact_name === contact.name && requests[0].contact_type === 'applicant', 'Unlock uses canonical enrichment API and contact identity');
            failPayment = false;
            await button.click();
            await page.locator('.enriched-contact-card').waitFor();
            assert(await button.count() === 0, 'Successful unlock refreshes contact directory');
            assert((await page.locator('#contacts-directory').innerText()).includes('Taylor Tester'), 'Unlocked contact remains visible');
            assert(requests.length === 2, 'One request per confirmed unlock attempt');
        } finally {
            await page.unroute(getPattern);
            await page.unroute(postPattern);
        }
        assert(errors.length === 0, 'Uncaught browser errors: ' + errors.join('; '));
        return {passed:24, checks:'buyers/alerts failure, retry, response ordering; permit failure recovery, sort wiring and links; contact unlock cancellation, billing failure, retry, payload, refresh'};
    } finally { page.off('pageerror', onError); }
}
