/* Pending proposals are separate from sheet cells until explicit approval. */
(() => {
    'use strict';
    const config=window.PROSPECT_CONFIG, sheet=window.ProspectSheet;
    if(!config?.listId || !sheet)return;
    const $=id=>document.getElementById(id), {api,esc}=sheet;
    const root=`/lists/${config.listId}/enrichment`, dialog=$('prospect-enrichment-dialog');
    let state, jobId=null, page=1, timer, generation=0, pending=false, initial=true;
    const selections=new Map(), expanded=new Set();
    const labels={queued:'Queued',running:'Researching',found:'Match found',needs_review:'Possible match',not_found:'No match found',failed:'Source error',skipped:'Skipped',cancelled:'Cancelled'};
    const running=()=>!!((state?.counts.queued||0)+(state?.counts.running||0));
    const choices=item=>{
        if(['queued','running'].includes(item.status))return new Set();
        if(!selections.has(item.id))selections.set(item.id,new Set((item.result.findings||[]).flatMap((f,i)=>f.default_selected?[f.index??i]:[])));
        const allowed=new Set((item.result.findings||[]).map((f,i)=>f.index??i));
        for(const id of selections.get(item.id))if(!allowed.has(id))selections.get(item.id).delete(id);
        return selections.get(item.id);
    };
    function selectionCount(){
        const eligible=(state?.items||[]).filter(i=>!i.reviewed_at);
        const n=eligible.reduce((sum,i)=>sum+choices(i).size,0);
        $('prospect-enrichment-selection').textContent=`${n} findings selected on this page. Unchecked findings on approved leads are declined. Nothing is saved automatically.`;
        $('prospect-enrichment-approve').disabled=pending || !n || state?.stale;
        $('prospect-enrichment-approve-run').disabled=pending || !state?.job || running() || state?.stale || state.reviewed>=(state.counts.found||0)+(state.counts.needs_review||0);
        $('prospect-enrichment-dismiss').disabled=pending || !eligible.some(i=>(i.result.findings||[]).length && !choices(i).size) || state?.stale;
    }
    function render(){
        const counts=state.counts, active=running(), total=state.total||0;
        $('prospect-enrichment-runs').innerHTML=state.jobs.length?state.jobs.map(j=>`<option value="${j.id}" ${j.id===state.job?.id?'selected':''}>${j.mode==='internal'?'Existing records':'Advanced research'} · ${esc(new Date(j.created_at).toLocaleString())}</option>`).join(''):'<option value="">No runs yet</option>';
        $('prospect-enrichment-internal').disabled=pending || active;
        $('prospect-enrichment-advanced').disabled=pending || active;
        $('prospect-enrichment-cancel').hidden=!active;
        const unresolved=state.unresolved||0;
        $('prospect-enrichment-unresolved').hidden=active || !unresolved || !state.job;
        $('prospect-enrichment-unresolved').textContent=`Research ${unresolved} unresolved lead${unresolved===1?'':'s'}`;
        const summary=state.job?`${counts.found||0} matched · ${counts.not_found||0} not found · ${counts.needs_review||0} need review · ${state.source_issues||0} source issues${active?` · ${(counts.queued||0)+(counts.running||0)} remaining`:''} · ${state.reviewed||0} reviewed`:'No research yet. Start a check to find matches and missing information.';
        $('prospect-enrichment-status').textContent=summary;
        $('prospect-enrichment-summary').innerHTML=`<p><strong>${esc(summary)}</strong></p>${state.job?`<progress max="${total||1}" value="${total-(counts.queued||0)-(counts.running||0)}" aria-label="Research progress"></progress><p class="prospect-hint">${counts.skipped||0} skipped · ${counts.cancelled||0} cancelled. Matched means a person, company or property record matched—not that every contact detail is verified.</p>`:''}${state.stale?'<p class="prospect-error">The list assignment or columns changed. Start a fresh check before approving results.</p>':''}`;
        // Preserve checkbox decisions and expanded cards across progress updates.
        $('prospect-enrichment-items').innerHTML=state.items.map(item=>{
            const fields=item.input.fields, findings=item.result.findings||[], chosen=choices(item);
            const disabled=!!item.reviewed_at || state.stale;
            return `<details class="prospect-finding-card" data-item="${item.id}" ${expanded.has(item.id)?'open':''}><summary><span><strong>${esc(fields.name||fields.company||fields.address||'Lead '+item.input.position)}</strong><small>Row ${item.input.position} · ${esc(fields.company||fields.address||'')}</small></span><span>${item.reviewed_at?'Reviewed':esc(labels[item.status]||item.status)} · ${findings.length} finding${findings.length===1?'':'s'}</span></summary><div class="prospect-finding-body">${item.result.restriction?`<p class="prospect-error">${esc(item.result.restriction)}</p>`:''}${(item.result.errors||[]).map(e=>`<p class="prospect-error">${esc(e)}</p>`).join('')}${findings.map((f,index)=>`<label class="prospect-finding ${f.conflict?'prospect-finding-conflict':''}"><input type="checkbox" data-finding="${index}" data-item-id="${item.id}" ${item.reviewed_at?(item.decision.includes(f.index??index)?'checked':''):(chosen.has(f.index??index)?'checked':'')} ${disabled?'disabled':''}><span><strong>${esc(f.label)}</strong><span class="prospect-finding-value">${esc(f.value)}</span>${f.kind==='field'?`<small>${f.current?`Current: ${esc(f.current)} → proposed replacement`:'Fill empty mapped column'}</small>`:'<small>Save to approved research</small>'}${!f.default_selected?'<small class="prospect-review-required">Review this finding before selecting it</small>':''}<a href="${esc(f.source.url)}" target="_blank" rel="noopener noreferrer">${esc(f.source.label)} ↗</a>${f.source.hint?`<small>${esc(f.source.hint)}</small>`:''}<small>${esc(f.basis)}</small></span></label>`).join('')}${!findings.length?`<p>${item.status==='not_found'?'No matching records returned. Try advanced research or add a company, NYC address or BBL.':active?'Research will appear here as this lead is checked.':'No findings to approve.'}</p>`:''}${item.result.checks?.length?`<details><summary>Sources checked</summary><ul>${item.result.checks.map(c=>`<li>${esc(c)}</li>`).join('')}</ul></details>`:''}</div></details>`;
        }).join('');
        $('prospect-enrichment-items').querySelectorAll('details[data-item]').forEach(d=>d.addEventListener('toggle',()=>{d.open?expanded.add(Number(d.dataset.item)):expanded.delete(Number(d.dataset.item));}));
        $('prospect-enrichment-page').textContent=total?`Leads ${(page-1)*25+1}–${Math.min(page*25,total)} of ${total}`:'';
        $('prospect-enrichment-prev').disabled=pending || page<=1;
        $('prospect-enrichment-next').disabled=pending || page*25>=total;
        selectionCount();
    }
    async function load(){
        clearTimeout(timer); const sequence=++generation;
        try{
            const data=await api(root+`?page=${page}${jobId?'&job_id='+jobId:''}`);
            if(sequence!==generation)return;
            state=data; jobId=data.job?.id||null; render();
            if(running())timer=setTimeout(()=>load(),dialog.open?4000:12000);
        }catch(error){$('prospect-enrichment-error').textContent=error.message;sheet.notice(error.message);}
    }
    async function act(fn){
        if(pending)return;pending=true;$('prospect-enrichment-error').textContent='';
        dialog.querySelectorAll('button,select').forEach(el=>{if(!el.hasAttribute('data-close'))el.disabled=true;});
        clearTimeout(timer); ++generation;
        try{await fn();}catch(error){$('prospect-enrichment-error').textContent=error.message;}
        finally{pending=false;dialog.querySelectorAll('button,select').forEach(el=>el.disabled=false);await load();}
    }
    async function open(selected=false){
        try{await sheet.finishEdits();$('prospect-enrichment-scope').value=selected?'selected':'all';dialog.showModal();await load();}
        catch(error){sheet.notice(error.message);}
    }
    $('prospect-enrich').onclick=()=>open();$('prospect-bulk-enrich').onclick=()=>open(true);
    async function start(mode,unresolved=false){
        await sheet.finishEdits();
        const data={mode,request_key:crypto.randomUUID()};
        if(unresolved)data.unresolved_job_id=jobId;
        else if($('prospect-enrichment-scope').value==='selected'){
            data.row_ids=sheet.getSelected();if(!data.row_ids.length)throw new Error('Select leads in the sheet first, or choose the whole list.');
        }
        const result=await api(root,{method:'POST',body:data});jobId=result.job_id;page=1;selections.clear();expanded.clear();
    }
    $('prospect-enrichment-internal').onclick=()=>act(()=>start('internal'));
    $('prospect-enrichment-advanced').onclick=()=>act(()=>start('advanced'));
    $('prospect-enrichment-unresolved').onclick=()=>act(()=>start('advanced',true));
    $('prospect-enrichment-cancel').onclick=()=>act(()=>api(root+`/${jobId}/cancel`,{method:'POST',body:{}}));
    $('prospect-enrichment-runs').onchange=()=>{jobId=Number($('prospect-enrichment-runs').value);page=1;load();};
    $('prospect-enrichment-items').onchange=e=>{
        const input=e.target;if(!input.matches('[data-finding]'))return;
        const chosen=selections.get(Number(input.dataset.itemId)), item=state.items.find(i=>i.id===Number(input.dataset.itemId));
        const f=item.result.findings[Number(input.dataset.finding)], index=f.index??Number(input.dataset.finding);
        input.checked?chosen.add(index):chosen.delete(index);selectionCount();
    };
    $('prospect-enrichment-check').onclick=()=>{state.items.filter(i=>!i.reviewed_at&&!['queued','running'].includes(i.status)).forEach(i=>selections.set(i.id,new Set((i.result.findings||[]).flatMap((f,index)=>f.default_selected?[f.index??index]:[]))));render();};
    $('prospect-enrichment-clear').onclick=()=>{state.items.forEach(i=>selections.set(i.id,new Set()));render();};
    async function approve(dismiss=false){
        const items=state.items.filter(i=>!i.reviewed_at && i.result.findings?.length && (dismiss?!choices(i).size:choices(i).size))
            .map(i=>({id:i.id,selected:[...choices(i)]}));
        const result=await api(root+`/${jobId}/approve`,{method:'POST',body:{items}});
        await sheet.refresh();sheet.notice(`${result.approved} findings approved. Research is saved with sources; you can undo the latest approval from the sheet.`);
    }
    $('prospect-enrichment-approve').onclick=()=>act(()=>approve());
    $('prospect-enrichment-approve-run').onclick=()=>act(async()=>{
        let approved=0, savedPages=0;
        try{
            for(let p=1;p<=Math.ceil(state.total/25);p++){
                const batch=await api(root+`?job_id=${jobId}&page=${p}`);
                const items=batch.items.filter(i=>!i.reviewed_at && choices(i).size).map(i=>({id:i.id,selected:[...choices(i)]}));
                if(!items.length)continue;
                const result=await api(root+`/${jobId}/approve`,{method:'POST',body:{items}});
                approved+=result.approved;savedPages++;
                $('prospect-enrichment-selection').textContent=`${approved} findings saved · processing page ${p} of ${Math.ceil(state.total/25)}…`;
            }
            sheet.notice(`${approved} findings approved across the run. Your unchecked choices were respected. Undo affects the latest saved page.`);
        }catch(error){throw new Error(`${approved} findings saved across ${savedPages} pages before stopping. ${error.message}`);}
        finally{await sheet.refresh();}
    });
    $('prospect-enrichment-dismiss').onclick=()=>act(()=>approve(true));
    $('prospect-enrichment-prev').onclick=()=>{page--;load();};$('prospect-enrichment-next').onclick=()=>{page++;load();};
    document.addEventListener('prospect:loaded',()=>{
        if(!initial)return;initial=false;
        const requested=new URLSearchParams(location.search).get('research');
        if(requested && /^\d+$/.test(requested)){
            jobId=Number(requested);
            const url=new URL(location.href);url.searchParams.delete('research');history.replaceState(null,'',url);
            open();
        }else load();
    });
})();
