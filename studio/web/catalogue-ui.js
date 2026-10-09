import {applicationTypes,rolePresets,toolDescriptors,outputTypes,outputPresets,connectorDescriptors} from '/catalogue.js';

const capabilityText=capability=>({read:'Read',write:'Approval required',inbound:'Inbound trigger'})[capability]||'Unknown';
// A host instance is its own entry: its id is the instance, and it names the
// service it runs. A plain service entry has no instance.
const entryService=entry=>entry.integration||entry.id;
const entryInstance=entry=>entry.integration?entry.id:'';

// The catalogue shows what this host can actually do. A bundled description is
// a template's suggestion, never evidence that an account is connected.
function connectionState(service,host){
  const connected=(host.connections||[]).filter(item=>item.integration===entryService(service)
    &&entryInstance(service)===(item.instance||'')
    &&(!host.application||item.applications.includes(host.application)));
  if(connected.length)return {label:connected.length===1?'Connected for this application':`${connected.length} connections for this application`,connected:true};
  if(service.available!==true)return {label:'Needs host setup',connected:false};
  return {label:'Available — not connected yet',connected:false};
}
function connectionCard(service,host,e){
  const state=connectionState(service,host);
  const reads=service.operations.filter(op=>op.capability==='read'),writes=service.operations.filter(op=>op.capability==='write');
  const inbound=service.operations.filter(op=>op.capability==='inbound');
  const summary=[reads.length?`${reads.length} read ${reads.length===1?'action':'actions'}`:'',writes.length?`${writes.length} change ${writes.length===1?'action':'actions'} needing approval`:'',inbound.length?'receives signed deliveries':''].filter(Boolean);
  const action=service.available===true?`<button class="button" data-catalogue-connect="${e(service.id)}">${state.connected?'Manage':'Connect'}</button>`:'';
  return `<section class="panel${state.connected?' is-connected':''}"><h3>${e(service.name)}</h3><p>${e(service.description)}</p><span class="catalogue-meta">${e(state.label)}</span>${summary.length?`<p class="field-help">${e(summary.join(' · '))}</p>`:''}<details class="quiet-details"><summary>What it can do</summary><ul>${service.operations.map(op=>`<li>${e(op.description)} · ${e(capabilityText(op.capability))}</li>`).join('')||'<li>This host has no execution adapter for it yet.</li>'}</ul></details>${action}</section>`;
}
function connectionsMarkup(host,e){
  const services=host.integrations||[];
  const installed=services.filter(service=>service.available===true),unavailable=services.filter(service=>service.available!==true);
  const grouped=[...new Set(installed.map(service=>service.category))].sort();
  const section=(title,items,note)=>`<details class="quiet-details catalogue-group" open><summary>${e(title)}<span>${items.length}</span></summary>${note?`<p class="field-help">${e(note)}</p>`:''}<div class="catalogue-cards">${items.map(service=>connectionCard(service,host,e)).join('')}</div></details>`;
  if(!services.length)return `<p class="catalogue-intro">This host has not reported which services it can run. ${e(host.enabled?'Reload the page.':'Set CONCLAVE_INTEGRATION_KEY on the host to store connection credentials.')}</p>`;
  return `<p class="catalogue-intro">${e(host.enabled?'These are the services this host can run. Connecting an account does not grant an agent access: you still choose which actions each team member may use, and changes still need approval.':'These are the services this host can run. Credential storage is off until the host sets CONCLAVE_INTEGRATION_KEY.')}</p>${grouped.map(category=>section(category,installed.filter(service=>service.category===category))).join('')}${unavailable.length?section('Not installed on this host',unavailable,'An operator must configure these on the host. Nothing here can be connected from the browser.'):''}${bundledMarkup(host,e)}`;
}
function bundledMarkup(host,e){
  const described=new Set((host.integrations||[]).flatMap(service=>[service.id,entryService(service)]));
  const others=connectorDescriptors.filter(connector=>connector.origin!=='integration'&&!described.has(connector.id));
  if(!others.length)return '';
  return `<details class="quiet-details catalogue-group"><summary>Suggested by templates, not installed on this host<span>${others.length}</span></summary><p class="field-help">Templates may mention these services. Selecting one records intent for the plan; it grants nothing until an operator installs the adapter or an extension reports it.</p><div class="catalogue-cards">${others.map(c=>`<section class="panel"><h3>${e(c.name)}</h3><p>${e(c.description)}</p><span class="catalogue-meta">${c.available===true?'Reported by an installed extension':'Not installed'}</span><button class="button" data-catalogue-connector="${e(c.id)}">Add to plan</button></section>`).join('')}</div></details>`;
}
export function catalogueMarkup(tab,escape,audience='all',host={}){
  const e=escape;
  const nav=`<nav class="catalogue-tabs" aria-label="Catalogue sections">${[['applications','Templates'],['connectors','Connections']].map(([id,label])=>`<button data-catalogue-tab="${id}" class="${tab===id?'active':''}" aria-pressed="${tab===id}">${label}</button>`).join('')}<details class="catalogue-more"><summary>Components</summary>${[['members','Roles'],['tools','Tools'],['outputs','Results']].map(([id,label])=>`<button data-catalogue-tab="${id}" aria-pressed="${tab===id}">${label}</button>`).join('')}</details></nav>`;
  const shown=applicationTypes.filter(app=>audience==='all'||app.research?.audience===audience);
  const card=app=>`<article class="panel calm-template"><h3>${e(app.name)}</h3><p>${e(app.example)}</p><span class="catalogue-meta">${app.members.length} team members · ${app.outputs.length} results</span><div class="template-actions"><button class="button" data-catalogue-create="${app.id}">Use template</button><details class="template-details"><summary>Details</summary><div class="template-detail-body"><h4>Team</h4><ul>${app.members.map(m=>`<li>${e(m.name)}${rolePresets.find(r=>r.id===m.preset)?.kind==='human'?' · Person':''}</li>`).join('')}</ul><h4>Results</h4><ul>${app.outputs.map(o=>`<li>${e(o.name)}</li>`).join('')}</ul>${app.inputs?`<h4>You provide</h4><ul>${app.inputs.map(i=>`<li>${e(i.label)}</li>`).join('')}</ul>`:''}<details><summary>Plan and review</summary><ol>${(app.plan||app.outputs.map(o=>({name:o.name}))).map(step=>`<li>${e(step.name)}</li>`).join('')}</ol>${app.review_checks?`<ul>${app.review_checks.map(c=>`<li>${e(c)}</li>`).join('')}</ul>`:''}</details><details><summary>Tools and connections</summary><p>${app.tools.map(id=>e(toolDescriptors.find(t=>t.id===id)?.name||id)).join(', ')}</p>${app.connector_requirements?`<ul>${app.connector_requirements.map(r=>`<li>${e(connectorDescriptors.find(c=>c.id===r.suggested)?.name)} · ${e(r.purpose)}${r.required?'':' · optional'}</li>`).join('')}</ul>`:''}</details>${app.research?`<details><summary>Research</summary><p>${e(app.research.summary)}</p><a href="${e(app.research.url)}" target="_blank" rel="noopener">Read the research</a></details>`:''}</div></details></div></article>`;
  let content='';
  if(tab==='applications')content=`<div class="catalogue-tabs audience-tabs" aria-label="Application audience">${[['all','All'],['Customer support lead','Support'],['Research and strategy analyst','Research'],['Content marketing manager','Content']].map(([id,label])=>`<button data-catalogue-audience="${e(id)}" aria-pressed="${id===audience}" class="${id===audience?'active':''}">${label}</button>`).join('')}</div><div class="template-cards">${shown.filter(a=>a.featured).map(card).join('')}</div>${shown.some(a=>!a.featured)?`<details class="quiet-details"><summary>More templates · ${shown.filter(a=>!a.featured).length}</summary><div class="template-cards">${shown.filter(a=>!a.featured).map(card).join('')}</div></details>`:''}<details class="quiet-details"><summary>Compare complete teams</summary><div class="catalogue-table"><table><thead><tr><th>Application</th><th>Team members</th><th>Results</th></tr></thead><tbody>${shown.map(app=>`<tr><td>${e(app.name)}</td><td>${app.members.map(m=>e(m.name)).join(', ')}</td><td>${app.outputs.map(o=>e(o.name)).join(', ')}</td></tr>`).join('')}</tbody></table></div></details><button class="text-button" data-catalogue-blank="true">Start custom application</button>`;
  if(tab==='connectors')content=connectionsMarkup(host,e);
  if(tab==='members')content=`<div class="catalogue-cards">${rolePresets.map(role=>`<section class="panel"><h3>${e(role.name)}</h3><span class="catalogue-meta">${role.kind==='human'?'Person':'AI'}</span><details class="quiet-details"><summary>Responsibility</summary><p>${e(role.description)}</p></details><button class="button" data-catalogue-role="${role.id}">Add role</button></section>`).join('')}</div>`;
  if(tab==='tools')content=[...new Set(toolDescriptors.map(t=>t.category))].map(category=>`<details class="quiet-details catalogue-group"><summary>${e(category)}</summary><div class="catalogue-cards">${toolDescriptors.filter(t=>t.category===category).map(t=>`<section class="panel"><h3>${e(t.name)}</h3><p>${e(t.description)}</p><button class="button" data-catalogue-tool="${t.id}">Add tool</button></section>`).join('')}</div></details>`).join('');
  if(tab==='outputs')content=`<div class="catalogue-cards">${outputPresets.map(p=>`<section class="panel"><h3>${e(p.name)}</h3><details class="quiet-details"><summary>What it includes</summary><p>${e(p.task)}</p></details><button class="button" data-catalogue-output="${p.id}">Add result</button></section>`).join('')}</div><details class="quiet-details"><summary>General result types</summary><div class="catalogue-cards">${outputTypes.map(o=>`<section class="panel"><h3>${e(o.name)}</h3><p>${e(o.description)}</p><button class="button" data-catalogue-output="${o.id}">Add result</button></section>`).join('')}</div></details>`;
  return `<h2 id="catalogue-title">${tab==='applications'?'Start with a template':tab==='connectors'?'Connections':tab==='members'?'Roles':tab==='tools'?'Tools':'Results'}</h2>${nav}${content}<p class="catalogue-footer">Templates define the team and plan. Run your saved application through Conclave.</p>`;
}
