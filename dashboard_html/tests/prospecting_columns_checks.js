// Real pointer drags and persisted preferences against the disposable preview.
async page => {
    const base='http://127.0.0.1:5101';
    if(!page.url().startsWith(base+'/')) throw new Error('Use the disposable local preview.');
    const assert=(ok,message)=>{if(!ok)throw new Error(message);};
    const order=async p=>p.locator('#prospect-head th[data-column]').evaluateAll(nodes=>nodes.map(n=>n.dataset.column));
    const saved=async()=>page.waitForFunction(()=>document.getElementById('prospect-layout-status').textContent==='Layout saved');
    await page.request.post(base+'/__test/user/1');
    await page.setViewportSize({width:1440,height:1000});
    await page.emulateMedia({colorScheme:'light'});
    await page.goto(base+'/crm/prospecting');
    await page.getByRole('button',{name:'Import CSV',exact:true}).click();
    await page.locator('#prospect-upload-form input[type=file]').setInputFiles('dashboard_html/tests/fixtures/prospecting.csv');
    await page.getByRole('button',{name:'Preview file',exact:true}).click();
    await page.locator('#prospect-preview:not([hidden])').waitFor();
    await page.getByRole('button',{name:'Create prospect list',exact:true}).click();
    await page.waitForURL(/prospecting\/[0-9]+$/);
    await page.locator('[data-column-handle=c2]').waitFor();
    const url=page.url();
    const from=await page.locator('[data-column-handle=c2]').boundingBox();
    const to=await page.locator('[data-column-handle=lead]').boundingBox();
    await page.mouse.move(from.x+from.width/2,from.y+from.height/2);
    await page.mouse.down();
    await page.mouse.move(to.x+15,to.y+to.height/2,{steps:12});
    await page.mouse.up();await saved();
    assert((await order(page))[0]==='c2','Pointer drag moves Phone ahead of Lead');
    const aligned=await page.locator('.prospect-table').evaluate(table=>{
        const ids=[...table.querySelectorAll('thead th[data-column]')].map(n=>n.dataset.column);
        return [...table.querySelectorAll('tbody tr')].every(row=>JSON.stringify([...row.querySelectorAll('td[data-column]')].map(n=>n.dataset.column))===JSON.stringify(ids));
    });
    assert(aligned,'Header and row values remain aligned');
    assert(await page.locator('#prospect-rows tr').first().locator('td[data-column]').first().innerText()==='212-555-0100','Correct phone moves with header');
    await page.locator('[data-column-handle=status]').focus();await page.keyboard.press('Alt+ArrowRight');await saved();
    const reordered=await order(page);
    assert(reordered.indexOf('status')>reordered.indexOf('c3'),'Tracking columns support keyboard moves');
    await page.reload();await page.locator('[data-column-handle]').first().waitFor();
    assert(JSON.stringify(await order(page))===JSON.stringify(reordered),'Order survives reload');
    await page.locator('#prospect-filters [name=q]').fill('Other');
    await page.getByRole('button',{name:'Apply',exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll('#prospect-rows tr').length===1);
    assert(JSON.stringify(await order(page))===JSON.stringify(reordered),'Filtering keeps order');
    await page.locator('.prospect-columns summary').click();
    await page.locator('#prospect-columns input[value=c3]').uncheck();await saved();
    assert(!(await order(page)).includes('c3'),'Column hidden');
    await page.reload();await page.locator('[data-column-handle]').first().waitFor();
    assert(!(await order(page)).includes('c3'),'Hidden columns survive reload');
    await page.locator('.prospect-columns summary').click();
    await page.locator('#prospect-columns input[value=c3]').check();await saved();
    assert(JSON.stringify(await order(page))===JSON.stringify(reordered),'Revealed column keeps its chosen position');
    await page.locator('.prospect-columns summary').click();
    // Fresh browser storage represents another device signed in as the same user.
    const fresh=await page.context().browser().newContext();
    try {
        const other=await fresh.newPage();await other.goto(url);await other.locator('[data-column-handle]').first().waitFor();
        assert(JSON.stringify(await order(other))===JSON.stringify(reordered),'Layout restored with no browser storage');
    } finally {await fresh.close();}
    await page.route('**/api/lists/*/layout',route=>route.fulfill({status:500,contentType:'application/json',body:'{"error":"Test save failure"}'}));
    await page.locator('[data-column-handle=lead]').focus();await page.keyboard.press('Alt+ArrowRight');
    await page.locator('#prospect-layout-retry:not([hidden])').waitFor();
    assert((await page.locator('#prospect-layout-status').innerText()).includes('not saved'),'Failure is visible');
    await page.unroute('**/api/lists/*/layout');
    await page.locator('#prospect-layout-retry').click();await saved();
    const afterRetry=await order(page);
    await page.goto(base+'/crm/prospecting');await page.goto(url);await page.locator('[data-column-handle]').first().waitFor();
    assert(JSON.stringify(await order(page))===JSON.stringify(afterRetry),'Retry persists latest order after reopening');
    // Actual Chromium touch events exercise the same pointer handling on a phone.
    await page.setViewportSize({width:375,height:812});
    await page.locator('[data-column-handle]').first().scrollIntoViewIfNeeded();
    const first=await page.locator('[data-column-handle]').nth(0).boundingBox();
    const second=await page.locator('[data-column-handle]').nth(1).boundingBox();
    const firstId=(await order(page))[0],secondId=(await order(page))[1];
    const cdp=await page.context().newCDPSession(page);
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:second.x+20,y:second.y+second.height/2}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:first.x+15,y:first.y+first.height/2}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await saved();
    assert((await order(page))[0]===secondId && firstId!==secondId,'Touch drag reorders on mobile');
    await cdp.detach();
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'No mobile page overflow');
    await page.screenshot({path:'/tmp/prospecting-columns-mobile.png'});
    await page.locator('.prospect-columns summary').click();
    const menu=await page.locator('#prospect-columns').boundingBox();
    assert(menu.x>=0 && menu.x+menu.width<=375,'Mobile column menu fits the viewport');
    const beforeMenuMove=await order(page);
    await page.locator(`#prospect-columns [data-move-column="${beforeMenuMove[0]}"][data-direction="1"]`).click();await saved();
    assert((await order(page))[0]!==beforeMenuMove[0],'Column menu arrows reorder columns');
    await page.locator('.prospect-columns summary').click();
    return {passed:true,checks:'Pointer and touch drag, keyboard, cell alignment, reload/reopen, filtering, visibility, fresh browser, failed-save retry, mobile'};
}
