/* Server-owned jobs; this workspace only starts runs and reviews their evidence. */
(() => {
    'use strict';
    const config=window.PROSPECT_CONFIG, sheet=window.ProspectSheet;
    if(!config || !sheet)return;
    const $=id=>document.getElementById(id), {api,esc}=sheet;
    const inbox=$('prospect-research-inbox'), dialog=$('prospect-enrichment-dialog');
    const root=`/lists/${config.listId}/enrichment`;
    let state=null, jobId=null, page=1, view='all', screen='run', focusId=null;
    let timer, feedTimer, generation=0, pending=false, initialized=false, candidates=[], picked=new Set(), pickerLimit=50;
    let leadSignature='', detailSignature='', requestKey=null, retryPayload=null, loadError='';
    const selections=new Map();
    const labels={queued:'Queued',running:'Researching',found:'Match found',needs_review:'Possible match',not_found:'No match',failed:'Source issue',skipped:'Skipped',cancelled:'Stopped'};
    const date=value=>new Date(value).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
    const modeName=mode=>mode==='internal'?'Existing records':'Advanced research';
    const running=()=>!!((state?.counts.queued||0)+(state?.counts.running||0));
    const name=item=>item.input.fields.name||item.input.fields.company||item.input.fields.address||`Lead ${item.input.position}`;
    const choiceKey=id=>`research:${config.userId}:${config.listId}:${id}`;
    function choices(item){
        if(['queued','running'].includes(item.status))return new Set();
        if(!selections.has(item.id)){
            let saved;try{saved=JSON.parse(sessionStorage.getItem(choiceKey(item.id)));}catch(_){}
            selections.set(item.id,new Set(Array.isArray(saved)?saved:(item.result.findings||[]).flatMap((f,i)=>f.default_selected?[f.index??i]:[])));
        }
        const allowed=new Set((item.result.findings||[]).map((f,i)=>f.index??i));
        for(const id of selections.get(item.id))if(!allowed.has(id))selections.get(item.id).delete(id);
        return selections.get(item.id);
    }
    function remember(id){try{sessionStorage.setItem(choiceKey(id),JSON.stringify([...selections.get(id)]));}catch(_){} }
    function setHTML(id,html){if($(id).innerHTML!==html)$(id).innerHTML=html;}
    function switchScreen(next){screen=next;$('research-setup').hidden=next!=='setup';$('research-run').hidden=next!=='run';$('research-new').classList.toggle('is-active',next==='setup');dialog.querySelector('.research-main').scrollTop=0;}
    async function feed(){
        clearTimeout(feedTimer);
        try{
            const data=await api('/enrichment-runs');
            const jobs=data.jobs.filter(j=>j.remaining||j.ready).slice(0,4);
            inbox.hidden=!jobs.length;
            inbox.innerHTML=jobs.length?`<div class="research-inbox-heading"><strong>Your research activity</strong><span>Saved runs · continue where you left off</span></div><div class="research-inbox-grid">${jobs.map(j=>`<a class="research-inbox-card" href="/crm/prospecting/${j.list_id}?research=${j.id}"><span class="research-dot ${j.remaining?'is-live':''}"></span><span><strong>${esc(j.list_name)}</strong><small>${esc(modeName(j.mode))} · ${j.remaining?`${j.total-j.remaining}/${j.total} finished`:`${j.ready} leads to review`}</small></span><span>${j.remaining?'View progress':'Review findings'} →</span></a>`).join('')}</div>`:'';
            feedTimer=setTimeout(feed,data.jobs.some(j=>j.remaining)?8000:30000);
        }catch(_){feedTimer=setTimeout(feed,15000);}
    }
    feed();
    if(!config.listId)return;
    function renderHistory(){
        setHTML('research-history',state?.jobs.length?state.jobs.map(j=>`<button type="button" data-run="${j.id}" class="research-run-link ${screen==='run'&&jobId===j.id?'is-active':''}" aria-pressed="${screen==='run'&&jobId===j.id}"><span><span class="research-dot ${j.remaining?'is-live':''}"></span>${esc(modeName(j.mode))}</span><small>${esc(date(j.created_at))} · ${j.total} leads</small><em>${j.remaining?`${j.running} running · ${j.remaining-j.running} queued`:j.ready?`${j.ready} ready to review`:j.cancelled_at?'Stopped':'Reviewed / complete'}</em></button>`).join(''):'<div class="research-history-empty">Your runs will appear here.<br>They keep running after you close this window.</div>');
    }
    function picker(){
        const q=$('research-search').value.trim().toLowerCase();
        const visible=candidates.filter(r=>`${r.name} ${r.company} ${r.address}`.toLowerCase().includes(q));
        $('research-picked-count').textContent=`${picked.size} selected`;
        $('research-launch-count').textContent=picked.size?`${picked.size} lead${picked.size===1?'':'s'} ready to research`:'Choose leads to begin';
        $('research-start').disabled=pending||!picked.size;
        $('research-pick-all').textContent=q?`Select ${visible.filter(r=>!r.busy).length} matches`:'Select all available';
        $('research-picker').innerHTML=visible.length?visible.slice(0,pickerLimit).map(r=>`<label class="research-picker-row ${r.busy?'is-busy':''}"><input type="checkbox" data-pick="${r.id}" ${picked.has(r.id)?'checked':''} ${r.busy?'disabled':''}><span class="research-avatar" aria-hidden="true">${esc(r.name.slice(0,1).toUpperCase())}</span><span><strong>${esc(r.name)}</strong><small>${esc(r.company||r.address||'No company or address yet')}</small></span>${r.busy?'<em>Already in progress</em>':''}</label>`).join(''):'<div class="research-empty">No eligible leads match this search.</div>';
        $('research-picker-more').hidden=visible.length<=pickerLimit;
        $('research-picker-more').textContent=`Show more (${Math.min(pickerLimit,visible.length)} of ${visible.length})`;
    }
    async function setup(selected=false){
        await sheet.finishEdits();
        const data=await api(root+'/candidates');candidates=data.rows;
        const ids=selected?new Set(sheet.getSelected()):new Set(candidates.map(r=>r.id));
        picked=new Set(candidates.filter(r=>!r.busy&&ids.has(r.id)).map(r=>r.id));
        $('research-search').value='';pickerLimit=50;requestKey=null;retryPayload=null;
        $('research-eligibility').textContent=`${candidates.filter(r=>!r.busy).length} available · ${candidates.filter(r=>r.busy).length} already in progress · ${data.excluded} archived, Do not contact or in CRM`;
        switchScreen('setup');renderHistory();picker();
    }
    function selectionCount(){
        if(!state)return;
        const items=state.items.filter(i=>!i.reviewed_at), n=items.reduce((sum,i)=>sum+choices(i).size,0);
        const focused=items.find(i=>i.id===focusId), selected=focused?choices(focused).size:0;
        $('prospect-enrichment-selection').textContent=selected?`${selected} selected for this lead · ${n} on this page`:'Select findings to save to this lead.';
        $('prospect-enrichment-approve').disabled=pending||!selected||state.stale;
        $('prospect-enrichment-approve-run').disabled=pending||!state.job||running()||state.stale||state.reviewed>=(state.counts.found||0)+(state.counts.needs_review||0);
        $('prospect-enrichment-dismiss').disabled=pending||!items.some(i=>i.result.findings?.length&&!choices(i).size)||state.stale;
    }
    function renderDetail(force=false){
        const item=state.items.find(i=>i.id===focusId);
        const signature=JSON.stringify([item,state.stale]);
        if(!force&&signature===detailSignature)return;
        detailSignature=signature;
        if(!item){$('research-detail').innerHTML='<div class="research-empty"><h4>No leads in this view</h4><p>Choose another filter or return as research finishes.</p></div>';return;}
        const findings=item.result.findings||[], chosen=choices(item), disabled=!!item.reviewed_at||state.stale;
        const head=`<div class="research-detail-head"><span class="research-kicker">ROW ${item.input.position} · ${esc(item.reviewed_at?'REVIEWED':labels[item.status].toUpperCase())}</span><h4>${esc(name(item))}</h4><p>${esc(item.input.fields.company||item.input.fields.address||'')}</p></div>`;
        const body=findings.map((f,index)=>`<article class="research-finding ${f.conflict?'has-conflict':''}"><label><input type="checkbox" data-finding="${index}" ${item.reviewed_at?(item.decision.includes(f.index??index)?'checked':''):(chosen.has(f.index??index)?'checked':'')} ${disabled?'disabled':''}><span><strong>${esc(f.label)}</strong><span class="research-value">${esc(f.value)}</span>${f.kind==='field'?`<small>${f.current?`Replaces: ${esc(f.current)}`:'Adds to an empty column'}</small>`:'<small>Save as research</small>'}${!f.default_selected?'<em>Needs your review</em>':''}</span></label><div class="research-evidence"><a href="${esc(f.source.url)}" target="_blank" rel="noopener noreferrer">${esc(f.source.label)} ↗</a><details><summary>Why this finding?</summary><p>${esc(f.source.hint||'')}</p><p>${esc(f.basis)}</p></details></div></article>`).join('');
        const empty=item.status==='running'?`<div class="research-empty"><span class="research-spinner" aria-hidden="true"></span><h4>${esc(item.current_source||'Researching this lead')}</h4><p>We’ll collect the findings here. You can review other leads or close this window.</p></div>`:item.status==='queued'?'<div class="research-empty"><h4>In the research queue</h4><p>This lead will start automatically as a research slot opens.</p></div>':'<div class="research-empty"><h4>No findings to review</h4><p>Add a company, NYC address or BBL to improve the next search.</p></div>';
        $('research-detail').innerHTML=head+(item.result.restriction?`<p class="prospect-error">${esc(item.result.restriction)}</p>`:'')+(item.result.errors||[]).map(error=>`<p class="research-source-error">${esc(error)}</p>`).join('')+(body||empty)+(item.result.checks?.length?`<details class="research-checked"><summary>${item.result.checks.length} source checks</summary><ul>${item.result.checks.map(c=>`<li>${esc(c)}</li>`).join('')}</ul></details>`:'');
    }
    function render(){
        renderHistory();if(!state.job)return;
        const c=state.counts, remaining=(c.queued||0)+(c.running||0), done=state.total-remaining, pct=Math.round(done/state.total*100)||0;
        $('research-run-title').textContent=modeName(state.job.mode);
        $('research-run-date').textContent=`${state.total} leads · Started ${date(state.job.created_at)}`;
        $('prospect-enrichment-status').textContent=remaining?`${done} of ${state.total} finished · ${c.running||0} researching in parallel. Runs continue in the background.`:`Research complete · ${c.found||0} matched · ${state.reviewed||0} reviewed`;
        $('prospect-enrichment-cancel').hidden=!remaining;
        $('prospect-enrichment-unresolved').hidden=!!remaining||!state.unresolved;
        $('prospect-enrichment-unresolved').textContent=`Research ${state.unresolved||0} unresolved`;
        setHTML('prospect-enrichment-summary',`<div class="research-progress"><div><span class="research-status-pill ${remaining?'is-live':''}">${remaining?'Researching in background':state.job.cancelled_at?'Run stopped':'Research complete'}</span><strong>${done}<span> / ${state.total} leads finished</span></strong><p>${remaining?`${c.running||0} running in parallel · ${c.queued||0} queued. You can close this window.`:'Your findings are ready. Choose what belongs in your list.'}</p></div><span class="research-percent">${pct}%</span><progress max="${state.total}" value="${done}" aria-label="Research progress"></progress></div><div class="research-metrics"><div><strong>${c.found||0}</strong><span>Matched</span></div><div><strong>${c.needs_review||0}</strong><span>Possible matches</span></div><div><strong>${c.not_found||0}</strong><span>No match</span></div><div><strong>${state.source_issues||0}</strong><span>Source issues</span></div></div>${state.stale?'<p class="prospect-error">The list changed. Start a new run before approving these findings.</p>':''}`);
        setHTML('research-active',(state.active_items||[]).map(i=>`<div class="research-active-row"><span class="research-dot is-live"></span><strong>${esc(name(i))}</strong><span>${esc(i.current_source||'Checking records')}…</span></div>`).join(''));
        document.querySelectorAll('[data-research-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.researchView===view)));
        if(!state.items.some(i=>i.id===focusId))focusId=state.items.find(i=>i.result.findings?.length&&!i.reviewed_at)?.id||state.items[0]?.id||null;
        const leadHTML=state.items.map(i=>`<button type="button" data-research-lead="${i.id}" class="research-lead ${i.id===focusId?'is-active':''}" aria-pressed="${i.id===focusId}"><span class="research-avatar" aria-hidden="true">${esc(name(i).slice(0,1).toUpperCase())}</span><span><strong>${esc(name(i))}</strong><small>${esc(i.input.fields.company||i.input.fields.address||'Lead '+i.input.position)}</small><em class="research-badge status-${i.status}">${esc(i.reviewed_at?'Reviewed':labels[i.status])}${i.result.findings?.length?' · '+i.result.findings.length:''}</em></span></button>`).join('');
        if(leadHTML!==leadSignature){$('research-lead-list').innerHTML=leadHTML||'<p class="research-caption">No leads in this view.</p>';leadSignature=leadHTML;}
        renderDetail();selectionCount();
        const total=state.filtered_total??state.total;
        $('prospect-enrichment-page').textContent=total?`${(page-1)*25+1}–${Math.min(page*25,total)} of ${total} leads · ${state.reviewed} reviewed`:'No leads';
        $('prospect-enrichment-prev').disabled=pending||page<=1;$('prospect-enrichment-next').disabled=pending||page*25>=total;
    }
    async function load(){
        clearTimeout(timer);const sequence=++generation;
        try{
            const data=await api(root+`?page=${page}&view=${view}${jobId?'&job_id='+jobId:''}`);
            if(sequence!==generation)return;
            if(loadError&&$('prospect-enrichment-error').textContent===loadError)$('prospect-enrichment-error').textContent='';
            loadError='';
            state=data;jobId=data.job?.id||null;page=data.page||1;render();
            timer=setTimeout(load,state.jobs.some(j=>j.remaining)?(dialog.open?3000:10000):20000);
        }catch(error){if(sequence!==generation)return;loadError=error.message+' Retrying…';$('prospect-enrichment-error').textContent=loadError;timer=setTimeout(load,8000);}
    }
    async function act(fn){
        if(pending)return;pending=true;clearTimeout(timer);++generation;$('prospect-enrichment-error').textContent='';
        dialog.querySelector('.research-bulk').open=false;
        const controls=[...dialog.querySelectorAll('button,input,select')].filter(el=>!el.hasAttribute('data-close'));
        controls.forEach(el=>el.disabled=true);
        try{await fn();}catch(error){$('prospect-enrichment-error').textContent=error.message;}
        finally{pending=false;controls.forEach(el=>el.disabled=false);if(screen==='setup')picker();await load();feed();}
    }
    async function open(selected=false,requested=null){
        if(pending)return;
        pending=true;
        $('research-setup').hidden=true;$('research-run').hidden=true;$('research-loading').hidden=false;
        dialog.querySelector('.research-sidebar').inert=true;
        $('prospect-enrichment-error').textContent='';
        try{
            await sheet.finishEdits();if(!dialog.open)dialog.showModal();
            if(requested){jobId=requested;page=1;view='all';}
            await load();
            if(selected||!state?.job)await setup(selected);else{switchScreen('run');renderHistory();}
        }catch(error){$('prospect-enrichment-error').textContent=error.message;}
        finally{
            pending=false;$('research-loading').hidden=true;dialog.querySelector('.research-sidebar').inert=false;
            if(screen==='setup')picker();else selectionCount();
        }
    }
    $('prospect-enrich').onclick=()=>open();$('prospect-bulk-enrich').onclick=()=>open(true);
    $('research-new').onclick=()=>act(()=>setup());
    $('research-search').oninput=()=>{pickerLimit=50;picker();};
    $('research-picker-more').onclick=()=>{pickerLimit+=50;picker();};
    $('research-pick-all').onclick=()=>{const q=$('research-search').value.toLowerCase().trim();candidates.filter(r=>!r.busy&&`${r.name} ${r.company} ${r.address}`.toLowerCase().includes(q)).forEach(r=>picked.add(r.id));picker();};
    $('research-pick-none').onclick=()=>{picked.clear();picker();};
    $('research-picker').onchange=e=>{const id=Number(e.target.dataset.pick);if(!id)return;e.target.checked?picked.add(id):picked.delete(id);requestKey=null;retryPayload=null;picker();};
    $('research-start').onclick=()=>act(async()=>{
        await sheet.finishEdits();
        const base={mode:dialog.querySelector('[name=research-mode]:checked').value,row_ids:[...picked].sort((a,b)=>a-b)};
        const signature=JSON.stringify(base);if(signature!==retryPayload){requestKey=crypto.randomUUID();retryPayload=signature;}
        const result=await api(root,{method:'POST',body:{...base,request_key:requestKey}});
        jobId=result.job_id;page=1;view='all';focusId=null;switchScreen('run');
        sheet.notice(`${result.count||'Your'} leads queued. Research continues if you leave this page.${result.skipped?' '+result.skipped+' unavailable leads skipped.':''}`);
    });
    $('research-history').onclick=e=>{const b=e.target.closest('[data-run]');if(!b||pending)return;jobId=Number(b.dataset.run);page=1;view='all';focusId=null;switchScreen('run');load();};
    $('research-lead-list').onclick=e=>{const b=e.target.closest('[data-research-lead]');if(!b||pending)return;focusId=Number(b.dataset.researchLead);render();};
    document.querySelectorAll('[data-research-view]').forEach(b=>b.onclick=()=>{view=b.dataset.researchView;page=1;focusId=null;load();});
    $('research-detail').onchange=e=>{
        const input=e.target;if(!input.matches('[data-finding]'))return;
        const item=state.items.find(i=>i.id===focusId), f=item.result.findings[Number(input.dataset.finding)], index=f.index??Number(input.dataset.finding);
        const chosen=choices(item);input.checked?chosen.add(index):chosen.delete(index);remember(item.id);selectionCount();
    };
    $('prospect-enrichment-check').onclick=()=>{state.items.filter(i=>!i.reviewed_at&&!['queued','running'].includes(i.status)).forEach(i=>{selections.set(i.id,new Set((i.result.findings||[]).flatMap((f,n)=>f.default_selected?[f.index??n]:[])));remember(i.id);});renderDetail(true);selectionCount();};
    $('prospect-enrichment-clear').onclick=()=>{state.items.filter(i=>!['queued','running'].includes(i.status)).forEach(i=>{selections.set(i.id,new Set());remember(i.id);});renderDetail(true);selectionCount();};
    async function approve(items){
        const result=await api(root+`/${jobId}/approve`,{method:'POST',body:{items}});
        await sheet.refresh();sheet.notice(`${result.approved} findings saved to approved research. Undo is available on the sheet.`);
    }
    $('prospect-enrichment-approve').onclick=()=>act(async()=>{
        const item=state.items.find(i=>i.id===focusId);await approve([{id:item.id,selected:[...choices(item)]}]);
        focusId=state.items.find(i=>i.id!==item.id&&!i.reviewed_at&&i.result.findings?.length)?.id||item.id;
    });
    $('prospect-enrichment-dismiss').onclick=()=>act(()=>approve(state.items.filter(i=>!i.reviewed_at&&i.result.findings?.length&&!choices(i).size).map(i=>({id:i.id,selected:[]}))));
    $('prospect-enrichment-approve-run').onclick=()=>act(async()=>{
        let approved=0;
        try{
            for(let p=1;p<=Math.ceil(state.total/25);p++){
                const batch=await api(root+`?job_id=${jobId}&page=${p}`);
                const items=batch.items.filter(i=>!i.reviewed_at&&choices(i).size).map(i=>({id:i.id,selected:[...choices(i)]}));
                if(items.length){const result=await api(root+`/${jobId}/approve`,{method:'POST',body:{items}});approved+=result.approved;}
                $('prospect-enrichment-selection').textContent=`${approved} findings saved · page ${p} of ${Math.ceil(state.total/25)}`;
            }
            sheet.notice(`${approved} findings saved. Undo reverses the last saved group.`);
        }catch(error){throw new Error(`${approved} findings saved before stopping. ${error.message}`);}finally{await sheet.refresh();}
    });
    $('prospect-enrichment-unresolved').onclick=()=>act(async()=>{
        const result=await api(root,{method:'POST',body:{mode:'advanced',unresolved_job_id:jobId,request_key:crypto.randomUUID()}});
        jobId=result.job_id;page=1;view='all';focusId=null;
    });
    $('prospect-enrichment-cancel').onclick=()=>act(()=>api(root+`/${jobId}/cancel`,{method:'POST',body:{}}));
    $('prospect-enrichment-prev').onclick=()=>{page--;focusId=null;load();};$('prospect-enrichment-next').onclick=()=>{page++;focusId=null;load();};
    dialog.addEventListener('close',()=>{if(running())sheet.notice('Research is still running. Open research to check progress or review findings.');});
    document.addEventListener('prospect:loaded',()=>{
        if(initialized)return;initialized=true;
        const url=new URL(location.href), requested=url.searchParams.get('research');
        if(requested&&/^\d+$/.test(requested)){url.searchParams.delete('research');window.history.replaceState(null,'',url);open(false,Number(requested));}
        else load();
    });
})();
