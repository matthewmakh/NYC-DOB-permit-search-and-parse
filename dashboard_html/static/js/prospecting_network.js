/* Relationships and additions remain within the private prospecting sheet. */
(() => {
    'use strict';
    if(!window.PROSPECT_CONFIG?.listId) return;
    const sheet=window.ProspectSheet, {api,esc,busy,notice}=sheet, $=id=>document.getElementById(id);
    const root=`/lists/${window.PROSPECT_CONFIG.listId}`;
    let network={nodes:[],links:[],relations:{}}, activeView='people', profileId, editProfile, addKey, addVersion;
    let nodeById=new Map(), linksByNode=new Map(), cardLimit=200;
    let appendFile, appendPreview, appendKey, appendVersion, networkPromise;
    const name=node=>node.display_name || node.name;
    const kindLabel=kind=>({person:'Person',company:'Company',building:'Building'})[kind];
    const nodeOptions=nodes=>nodes.map(n=>`<option value="${n.id}">${esc(name(n))} · ${kindLabel(n.kind)}${n.position?` · Row ${n.position}`:''}</option>`).join('');
    async function loadNetwork() {
        if(networkPromise)return networkPromise;
        networkPromise=(async()=>{await sheet.finishEdits();await api(root+'/network/build',{method:'POST',body:{}});network=await api(root+'/network');
            nodeById=new Map(network.nodes.map(n=>[n.id,n]));linksByNode=new Map();
            network.links.forEach(link=>[link.source_id,link.target_id].forEach(id=>{if(!linksByNode.has(id))linksByNode.set(id,[]);linksByNode.get(id).push(link);}));return network;})();
        try{return await networkPromise;}finally{networkPromise=null;}
    }
    function connections(id) {return linksByNode.get(id)||[];}
    function related(id,kind) {return [...new Set(connections(id).map(l=>l.source_id===id?l.target_id:l.source_id))].map(id=>nodeById.get(id)).filter(n=>n?.kind===kind);}
    function managementPeople(id) {return connections(id).filter(l=>l.target_id===id && l.relation==='manages').flatMap(l=>related(l.source_id,'person'));}
    function renderCards() {
        const query=$('prospect-network-search').value.trim().toLowerCase();
        const nodes=network.nodes.filter(n=>n.kind===activeView && [name(n),n.address,n.notes].join(' ').toLowerCase().includes(query));
        $('prospect-network-cards').innerHTML=nodes.length?nodes.slice(0,cardLimit).map(n=>{
            const people=new Set([...related(n.id,'person'),...(n.kind==='building'?managementPeople(n.id):[])].map(p=>p.id)).size;
            const others=related(n.id,activeView==='company'?'building':'company').length;
            return `<button type="button" class="prospect-network-card" data-open-profile="${n.id}"><span class="prospect-eyebrow">${kindLabel(n.kind)}</span><strong>${esc(name(n))}</strong>${n.address?`<span>${esc(n.address)}</span>`:''}<span>${people} ${people===1?'person':'people'} · ${others} ${activeView==='company'?(others===1?'building':'buildings'):(others===1?'company':'companies')}</span></button>`;
        }).join('')+(nodes.length>cardLimit?`<button type="button" class="cbtn" id="prospect-more-profiles">Show more (${nodes.length-cardLimit} remaining)</button>`:''):
            `<div class="prospect-empty"><h2>${query?'No matching profiles':`No ${activeView==='company'?'companies':'buildings'} yet`}</h2><p>${activeView==='company'?'Mapped company names appear here when you open this view. Add profiles and link their people and buildings.':'Add a building profile, then link the people and companies who own, manage or work with it.'}</p><button type="button" class="cbtn" data-new-profile="${activeView}">Add ${kindLabel(activeView).toLowerCase()}</button></div>`;
    }
    document.querySelectorAll('[data-network-view]').forEach(button=>button.addEventListener('click',async()=>{
        try {
            await sheet.finishEdits();const view=button.dataset.networkView;
            if(view!=='people'){button.disabled=true;await loadNetwork();}
            activeView=view;cardLimit=200;notice('');$('prospect-people-view').hidden=view!=='people';$('prospect-network-view').hidden=view==='people';
            document.querySelectorAll('[data-network-view]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
            if(view!=='people')renderCards();
        }catch(error){notice(error.message);}finally{button.disabled=false;}
    }));
    $('prospect-network-search').oninput=()=>{cardLimit=200;renderCards();};
    $('prospect-network-cards').addEventListener('click',e=>{if(e.target.id==='prospect-more-profiles'){cardLimit+=200;renderCards();}});
    $('prospect-add-person').onclick=()=>{
        const listing=sheet.getListing();addKey=crypto.randomUUID();addVersion=listing.version;
        $('prospect-person-fields').innerHTML=listing.columns.map(c=>`<label>${esc(c.label)}<input name="${c.id}" maxlength="10000"></label>`).join('');
        $('prospect-person-error').textContent='';$('prospect-person-dialog').showModal();
    };
    $('prospect-person-form').addEventListener('submit',event=>{
        event.preventDefault();busy(event.target,'prospect-person-error',async()=>{
            await api(root+'/people',{method:'POST',body:{cells:Object.fromEntries(new FormData(event.target)),request_key:addKey,version:addVersion}});
            $('prospect-person-dialog').close();await sheet.refresh();if(activeView!=='people'){await loadNetwork();renderCards();}
            notice('Person added to this list. Use Check duplicates to review possible matches.');
        });
    });
    const appendUpload=$('prospect-append-upload');
    function appendBody(){const data=new FormData();data.set('file',appendFile);data.set('delimiter',appendUpload.elements.delimiter.value);data.set('has_header',String(appendUpload.elements.has_header.checked));return data;}
    $('prospect-append-csv').onclick=()=>{appendUpload.reset();appendPreview=null;$('prospect-append-preview').hidden=true;$('prospect-append-error').textContent='';$('prospect-append-dialog').showModal();};
    appendUpload.onchange=()=>{appendPreview=null;$('prospect-append-preview').hidden=true;};
    appendUpload.addEventListener('submit',event=>{
        event.preventDefault();busy(appendUpload,'prospect-append-error',async()=>{
            appendFile=appendUpload.elements.file.files[0];if(!appendFile || appendFile.size>10*1024*1024)throw new Error('Choose a CSV or TSV up to 10 MB.');
            appendPreview=await api(root+'/append-preview',{method:'POST',body:appendBody()});appendKey=crypto.randomUUID();appendVersion=appendPreview.listing.version;
            $('prospect-append-summary').textContent=`${appendPreview.row_count} rows will be added · ${appendPreview.columns.length} incoming columns${appendPreview.duplicate_count?` · ${appendPreview.duplicate_count} identical rows in this file`:''}`;
            $('prospect-append-mapping').innerHTML=appendPreview.columns.map(c=>`<label>${esc(c.label)} →<select name="${c.id}"><option value="new" ${appendPreview.matches[c.id]==='new'?'selected':''}>Add as new column</option><option value="skip">Skip this column</option>${appendPreview.listing.columns.map(target=>`<option value="${target.id}" ${appendPreview.matches[c.id]===target.id?'selected':''}>${esc(target.label)} (${target.id})</option>`).join('')}</select></label>`).join('');
            $('prospect-append-preview').hidden=false;
        });
    });
    $('prospect-append-form').addEventListener('submit',event=>{
        event.preventDefault();busy(event.target,'prospect-append-error',async()=>{
            if(!appendPreview)throw new Error('Preview this file first.');
            const data=appendBody();data.set('mapping',JSON.stringify(Object.fromEntries(new FormData(event.target))));data.set('request_key',appendKey);data.set('version',appendVersion);
            const result=await api(root+'/append',{method:'POST',body:data});$('prospect-append-dialog').close();await sheet.refresh();
            if(activeView!=='people'){await loadNetwork();renderCards();}notice(`${result.count} leads added. Existing research and history preserved. Check duplicates before starting outreach.`);
        });
    });
    function openProfileEditor(node=null,kind='company') {
        editProfile=node;const form=$('prospect-profile-form');form.reset();form.elements.kind.disabled=!!node;form.elements.kind.value=node?.kind || kind;
        for(const field of ['name','address','website','notes'])form.elements[field].value=node?.[field] || '';
        $('prospect-profile-error').textContent='';$('prospect-profile-edit-dialog').showModal();
    }
    $('prospect-new-profile').onclick=()=>openProfileEditor(null,activeView==='building'?'building':'company');
    $('prospect-profile-form').addEventListener('submit',event=>{
        event.preventDefault();busy(event.target,'prospect-profile-error',async()=>{
            const data={...Object.fromEntries(new FormData(event.target)),kind:event.target.elements.kind.value};
            if(editProfile){data.id=editProfile.id;data.version=editProfile.version;}
            const result=await api(root+'/profiles',{method:'POST',body:data});$('prospect-profile-edit-dialog').close();await loadNetwork();if(activeView!=='people')renderCards();openProfile(result.entity_id);
        });
    });
    function openProfile(id) {
        const node=network.nodes.find(n=>n.id===id);if(!node){notice('Profile not found. Reload the sheet.');return;}
        profileId=id;$('prospect-profile-title').textContent=name(node);$('prospect-profile-kind').textContent=kindLabel(node.kind);$('prospect-link-error').textContent='';
        $('prospect-crm-reference').hidden=node.kind==='company'||!!node.promoted_at;
        $('prospect-crm-reference').open=false;$('prospect-crm-search').reset();$('prospect-crm-link').hidden=true;$('prospect-crm-search-message').textContent='';
        $('prospect-unlink-crm').hidden=!(node.crm_contact_id||node.crm_building_id||node.crm_restricted);
        const contactId=node.promoted_contact_id || node.crm_contact_id;
        const header=`${node.address?`<p>${esc(node.address)} <a target="_blank" rel="noopener noreferrer" href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(node.address)}">Map</a></p>`:''}${node.website?`<p><a target="_blank" rel="noopener noreferrer" href="${esc(node.website)}">${esc(node.website)}</a></p>`:''}${node.notes?`<p class="prospect-profile-notes">${esc(node.notes)}</p>`:''}<div class="prospect-column-tools">${node.row_id?`<button type="button" class="cbtn" data-open-profile-lead="${node.row_id}">Open lead &amp; outreach history</button>`:`<button type="button" class="cbtn" data-edit-profile="${id}">Edit profile</button>`}${contactId?`<a class="cbtn" href="/crm/contacts/${contactId}">Open CRM contact</a>`:''}${node.crm_building_id?`<a class="cbtn" href="/crm/buildings/${node.crm_building_id}">Open CRM building</a>`:''}</div>${node.crm_restricted?'<p>A linked CRM record is assigned to another person.</p>':''}`;
        const links=connections(id);
        const groups=['person','company','building'].map(kind=>{
            const relevant=links.filter(l=>network.nodes.find(n=>n.id===(l.source_id===id?l.target_id:l.source_id))?.kind===kind);
            if(!relevant.length)return '';
            return `<h3>${kind==='person'?'People':kind==='company'?'Companies':'Buildings'} · ${new Set(relevant.map(l=>l.source_id===id?l.target_id:l.source_id)).size}</h3>`+relevant.map(l=>{
                const other=network.nodes.find(n=>n.id===(l.source_id===id?l.target_id:l.source_id));
                const direction=l.source_id===id?`${network.relations[l.relation].label} →`:`← ${network.relations[l.relation].label}`;
                return `<div class="prospect-profile-link"><div><span class="prospect-eyebrow">${esc(direction)}</span><button type="button" class="prospect-profile-name" data-open-profile="${other.id}">${esc(name(other))}</button>${other.archived_at?'<span>Archived lead</span>':''}${l.note?`<p>${esc(l.note)}</p>`:''}</div><button class="cbtn cbtn-sm" type="button" data-remove-link="${l.id}" aria-label="Remove relationship with ${esc(name(other))}">Unlink</button></div>`;
            }).join('');
        }).join('');
        // A company's people and managed buildings are shown together; building
        // profiles also surface contacts reachable through their management company.
        let indirect='';
        if(node.kind==='building') {
            const managers=links.filter(l=>l.target_id===id && l.relation==='manages').map(l=>l.source_id);
            const people=[...new Map(managers.flatMap(cid=>related(cid,'person')).filter(p=>!related(id,'person').some(d=>d.id===p.id)).map(p=>[p.id,p])).values()];
            if(people.length)indirect='<h3>People at managing companies</h3>'+people.map(p=>`<button type="button" class="cbtn" data-open-profile="${p.id}">${esc(name(p))}</button>`).join(' ');
        }
        $('prospect-profile-body').innerHTML=header+(groups||'<p>No relationships yet. Link another person, company or building below.</p>')+indirect;
        const form=$('prospect-link-form');form.reset();form.elements.source_id.innerHTML=nodeOptions(network.nodes);form.elements.source_id.value=id;refreshRelationChoices();
        $('prospect-profile-dialog').querySelector('.prospect-link-editor').open=!links.length;
        if(!$('prospect-profile-dialog').open)$('prospect-profile-dialog').showModal();
    }
    function refreshRelationChoices() {
        const form=$('prospect-link-form'),source=network.nodes.find(n=>n.id===Number(form.elements.source_id.value));
        const previous=form.elements.relation.value;
        form.elements.relation.innerHTML=Object.entries(network.relations).filter(([,r])=>r.from.includes(source?.kind)).map(([key,r])=>`<option value="${key}" ${key===previous?'selected':''}>${esc(r.label)}</option>`).join('');refreshTargets();
    }
    function refreshTargets() {
        const form=$('prospect-link-form'),relation=network.relations[form.elements.relation.value];
        form.elements.target_id.innerHTML='<option value="">Choose a linked profile</option>'+nodeOptions(network.nodes.filter(n=>n.id!==Number(form.elements.source_id.value) && relation?.to.includes(n.kind)));
    }
    $('prospect-link-form').elements.source_id.onchange=refreshRelationChoices;$('prospect-link-form').elements.relation.onchange=refreshTargets;
    $('prospect-link-form').addEventListener('submit',event=>{
        event.preventDefault();busy(event.target,'prospect-link-error',async()=>{
            const data=Object.fromEntries(new FormData(event.target));data.source_id=Number(data.source_id);data.target_id=Number(data.target_id);
            await api(root+'/relationships',{method:'POST',body:data});await loadNetwork();if(activeView!=='people')renderCards();openProfile(profileId);
        });
    });
    $('prospect-crm-search').addEventListener('submit',event=>{
        event.preventDefault();busy(event.target,'prospect-link-error',async()=>{
            const node=network.nodes.find(n=>n.id===profileId),query=event.target.elements.q.value;
            const result=await api(root+`/crm-records?kind=${node.kind}&q=${encodeURIComponent(query)}`);
            $('prospect-crm-link').elements.record_id.innerHTML=result.records.map(r=>`<option value="${r.id}">${esc(r.name)}${r.detail?` · ${esc(r.detail)}`:''}</option>`).join('');
            $('prospect-crm-link').hidden=!result.records.length;$('prospect-crm-search-message').textContent=result.records.length?`${result.records.length} matching records (up to 25 shown).`:'No accessible CRM records match.';
        });
    });
    async function saveCrmLink(recordId) {
        const node=network.nodes.find(n=>n.id===profileId);
        await api(root+`/profiles/${profileId}/crm`,{method:'POST',body:{record_id:recordId,version:node.version}});
        await loadNetwork();openProfile(profileId);
    }
    $('prospect-crm-link').addEventListener('submit',event=>{event.preventDefault();busy(event.target,'prospect-link-error',()=>saveCrmLink(Number(event.target.elements.record_id.value)));});
    $('prospect-unlink-crm').onclick=async()=>{try{await saveCrmLink(null);}catch(error){$('prospect-link-error').textContent=error.message;}};
    document.addEventListener('click',async event=>{
        const button=event.target.closest('[data-open-profile],[data-profile-row],[data-new-profile],[data-edit-profile],[data-open-profile-lead],[data-remove-link]');if(!button)return;
        try {
            if(button.dataset.openProfile)openProfile(Number(button.dataset.openProfile));
            else if(button.dataset.profileRow){if(!sheet.canCloseLead())return;await loadNetwork();const node=network.nodes.find(n=>n.row_id===Number(button.dataset.profileRow));$('prospect-lead-dialog').close();openProfile(node.id);}
            else if(button.dataset.newProfile)openProfileEditor(null,button.dataset.newProfile);
            else if(button.dataset.editProfile)openProfileEditor(network.nodes.find(n=>n.id===Number(button.dataset.editProfile)));
            else if(button.dataset.openProfileLead){$('prospect-profile-dialog').close();await sheet.openLead(Number(button.dataset.openProfileLead));}
            else if(button.dataset.removeLink){
                if(!window.confirm('Remove this relationship? The profiles and their notes will remain.'))return;
                button.disabled=true;await api(root+`/relationships/${button.dataset.removeLink}`,{method:'DELETE'});await loadNetwork();if(activeView!=='people')renderCards();openProfile(profileId);
            }
        }catch(error){$('prospect-profile-dialog').open?$('prospect-link-error').textContent=error.message:notice(error.message);}finally{button.disabled=false;}
    });
})();
