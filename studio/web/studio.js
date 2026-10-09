import {integrationManagerMarkup,connectionAssignmentMarkup} from '/integrations-ui.js';
import {updateRunDOM} from '/run-dom.js';
import {templates,toolCatalog,emptyDefinition,validateDefinition,initialState,emptyWorkspaceCatalogue,validateWorkspaceCatalogue,installWorkspaceCatalogue,validateRecordFields,validateRecordValues} from '/definition.js';
import {advancedMarkup,contractMarkup} from '/advanced.js';
import {applicationTypes,rolePresets,toolDescriptors,outputTypes,outputPresets,connectorDescriptors,registerHostTools,registerHostConnectors,recommendGoal,manualPlanning} from '/catalogue.js';
import {planConnections,canvasPositions,planShape,setCanvasMetrics,canvasEdgeCurve,canvasNodeWidth,canvasNodeHeight,validateRequestInputs,backendOptions,backendModels,backendIssue,backendLabel,defaultBackend} from '/definition.js';
import {catalogueMarkup} from '/catalogue-ui.js';
import {canvasFitZoom,uiIcon} from '/canvas-ui.js';
import {menuItem,menuSeparator,menuMarkup,menuTarget,menuFocusTargets} from '/menu-ui.js';
import {preflightIssues,resolvePreflightIssue,staleRunSteps} from '/definition.js';
let preflightReviewed=null;
const preflightVersion=()=>JSON.stringify([currentId,current().revision,current().definition,preflightIssues(current().definition)]);
import {reorderPlan,duplicatePlanStep,removePlanStep,insertPlanStep,insertMemberStep,attachPlanTool,connectPlanSteps,disconnectPlanSteps,moveCanvasStep,tidyCanvas,layoutSaveDefinition,placeInsertedStep,copyStepMember,planReadiness} from '/plan.js';
import {workbenchMarkup,executionPlanMarkup,executionDetailMarkup,canvasEdgePaths} from '/workbench-ui.js';
import {runProgressMarkup,runStepWorking} from '/run-canvas.js';
import {proposePlanChange,parseImportedDefinition,describeDefinitionChanges} from '/plan-assistant.js';
import {projectLiveRun,liveRunMarkup,liveFollowMode,liveStreamPath} from '/live.js';
import {apiUrl,apiHeaders} from '/api.js';

const $ = id => document.getElementById(id);
const escape = value => String(value ?? '').replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const requestInputs={live:{}};
const requestResources={};
let workspaceCatalogue={catalogue:emptyWorkspaceCatalogue(),revision:0};
let pendingToolApprovals=[];
let integrationCatalogue=[],integrationConnections=[],integrationEnabled=false,oauthProviders=[];
let runtime={local:false,available:false,tools:[],backends:[]},liveBusy=false,liveError='',liveTimer=null,liveWatch=null,liveClock=null;
const liveRuns=new Map(),liveHistory=new Map();
const steps = ['Purpose','Team','Access','Outputs','Plan','Controls'];
const accessCatalog=toolDescriptors;
let canvasZoom=null,runCanvasZoom=null,railOpen=false,showAllApplications=false,canvasPan=null,canvasNodeDrag=null,lastPlanPress={id:null,time:0},connectSource=null,selectedEdge=null,canvasConnect=null;
let showPlanResources=false,showPlanInspector=false,showPlanAssistant=false,showPlanReadiness=false,planInsertion=null,renderedPlanId=null;
let selectedStepId=null,resourcePane='steps',memberLibrary=[],libraryError='',memberSaving=false,selectedRunStepId=null;
let activityExpanded=false,activityFilter='',followActive=true,lastFollowedStep=null,renderedRunId=null;
const planCommands=new Map();
const commandState=()=>{if(!planCommands.has(currentId))planCommands.set(currentId,{text:'',error:'',proposal:null});return planCommands.get(currentId);};
let catalogueTab='applications',dragState=null,pointerDrag=null,humanDraft={decision:'',body:''};
let applications=[],currentId=null,view='plan',buildStep=0,advanced=false,saving=false,layoutSaved=false,layoutSaveTimer=null;
let preview=null,noticeTimer=null,dialogState=null,detailHistory=[];
const drafts=new Map();
let requests={live:''};
const current=()=>applications.find(a=>a.id===currentId),draft=()=>drafts.get(currentId);
const dirty=()=>!current()?.revision||JSON.stringify(draft())!==JSON.stringify(current().definition);
const agentName=(d,id)=>d.agents.find(a=>a.id===id)?.name||id;
const outputName=(d,key)=>d.state.find(s=>s.key===key)?.label||key;
const capabilityName=id=>accessCatalog.find(t=>t.id===id)?.name||id;

async function loadIntegrationConnections(){
  try{const result=await request('/api/integrations');integrationCatalogue=result.integrations;integrationEnabled=result.connections_enabled;oauthProviders=result.oauth_providers||[];integrationConnections=integrationEnabled?(await request('/api/connections')).connections:[];}catch{integrationCatalogue=[];integrationConnections=[];integrationEnabled=false;}
}
async function manageIntegrations(error='',focus=''){
  await loadIntegrationConnections();$('record-detail').innerHTML=integrationManagerMarkup(integrationCatalogue,integrationConnections,currentId,escape,integrationEnabled,error,oauthProviders);if(!$('record-dialog').open)$('record-dialog').showModal();
}
const connectionTools=()=>integrationConnections.filter(c=>c.applications.includes(currentId)).flatMap(c=>c.operations.map(op=>`connection.${c.id}.${op}`));
const readinessRuntime=()=>({...runtime,tools:[...runtime.tools,...connectionTools()]});
function notify(message){$('notice').textContent=message;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').textContent='',6500);}
// Where this bundle talks to, and how it authenticates: the page's own host by
// default, or whatever config.json beside the page says.
const apiSettingsNow=()=>window.CONCLAVE_API||{base:'',token:'',credentials:'same-origin'};
async function request(path,options={}){
  const settings=apiSettingsNow();
  const response=await fetch(apiUrl(settings,path),{...options,credentials:settings.credentials,headers:apiHeaders(settings,options)}),result=response.status===204?null:await response.json();
  if(!response.ok){
    const error=new Error(result.error||'The request could not be completed.');error.status=response.status;error.login=result.login||'';
    // A 401 that names a sign-in is the host saying this page is not signed in —
    // a session that lapsed mid-use reads the same way as a first visit, so it
    // becomes the same screen rather than a stray message.
    if(error.login&&response.status===401)showSignIn();
    throw error;
  }return result;
}
function fitCanvas(mode){
  const area=$('app').querySelector(`[data-canvas-mode="${mode}"]`),flow=area?.querySelector('.canvas-flow');if(!flow)return;
  const zoom=canvasFitZoom(area.clientWidth,area.clientHeight,parseFloat(flow.style.width),parseFloat(flow.style.height));
  if(mode==='run'){runCanvasZoom=zoom;applyRunZoom();}else{canvasZoom=zoom;applyCanvasZoom();}
  area.scrollLeft=0;area.scrollTop=0;
}
async function deleteApplication(){
  if(saving||liveBusy)return;
  const item=current();
  if(!confirm(`Delete application “${draft().name}”? Saved run history will be retained.`))return;
  saving=true;
  try{
    if(item.revision)await request(`/api/applications/${encodeURIComponent(item.id)}`,{method:'DELETE',body:JSON.stringify({revision:item.revision})});
    resetRuns();applications=applications.filter(a=>a.id!==item.id);drafts.delete(item.id);liveRuns.delete(item.id);liveHistory.delete(item.id);delete requestInputs.live[item.id];
    saving=false;currentId=sidebarApplications()[0]?.id||applications[0]?.id;
    const url=new URL(location.href);url.searchParams.delete('application');url.searchParams.delete('run');history.replaceState(null,'',url);
    view='plan';buildStep=0;if(!currentId)createApplication('blank');else render();notify('Application deleted.');
  }catch(error){notify(error.message);}finally{saving=false;render();}
}
// Who this page is, as the host reports it. A deployment that authenticates with
// Google answers /api/me; a local host answers with its own operator and no login.
let account={principal:null,login:null};
function signInUrl(next){
  const settings=apiSettingsNow();
  return apiUrl(settings,`${(account.login?.start)||'/auth/google/start'}${next?`?next=${encodeURIComponent(next)}`:''}`);
}
// Only a deployment with a login shows this: a local host has one operator and
// nothing to sign out of.
function accountMarkup(){
  const principal=account.principal,login=account.login;
  if(!principal||!login)return '';
  return `<div class="account"><span class="rail-icon" aria-hidden="true">${uiIcon('person')}</span><span class="rail-label account-name" title="${escape(principal.label)}">${escape(principal.label)}</span><button class="text-button rail-label" data-action="sign-out">Sign out</button></div>`;
}
function signInMarkup(message){
  const settings=apiSettingsNow();
  return `<main class="loading sign-in"><h1>Sign in to Conclave</h1><p>This deployment admits people with a Google account. Your applications and runs stay in this deployment's own storage.</p>${message?`<p class="validation" role="alert">${escape(message)}</p>`:''}<a class="button primary" href="${escape(signInUrl())}">Sign in with Google</a><p class="field-help">You are signing in to use this Studio. Connecting Google Drive, Docs or Sheets to an application is a separate step, in Connections.</p></main>`;
}
function showSignIn(message){
  const failed=new URL(location.href).searchParams.get('signin')==='failed';
  $('app').innerHTML=signInMarkup(message||(failed?'That sign-in did not complete. Try again.':''));
}
async function readAccount(){
  try{account=await request('/api/me');}
  catch(error){account={principal:null,login:error.login?{start:error.login}:null,error:error.message};}
  return account;
}
async function load(){
  // Who the page is comes first: without a session a deployment answers 401 and
  // there is nothing else to fetch.
  const who=await readAccount();
  if(!who.principal){
    showSignIn();
    return;
  }
  if(new URL(location.href).searchParams.has('signin')){
    const url=new URL(location.href);url.searchParams.delete('signin');history.replaceState(null,'',url);
  }
  try{const results=await Promise.allSettled([request('/api/applications'),request('/api/members'),request('/api/runtime'),request('/api/catalogue')]);if(results[0].status==='rejected')throw results[0].reason;const result=results[0].value;if(results[1].status==='fulfilled'){memberLibrary=results[1].value.members;libraryError='';}else libraryError='The member library is unavailable. Your application is still editable.';if(results[2].status==='fulfilled'){runtime=results[2].value;registerHostTools(runtime.tool_descriptors);registerHostConnectors([...(runtime.connectors||[]),...(runtime.integrations||[])]);toolCatalog.splice(0,toolCatalog.length,...toolDescriptors.map(tool=>tool.id));}if(results[3].status==='fulfilled'){workspaceCatalogue=results[3].value;installWorkspaceCatalogue(workspaceCatalogue.catalogue);}applications=result.applications;for(const item of applications)drafts.set(item.id,structuredClone(item.definition));const route=new URL(location.href);currentId=applications.find(item=>item.id===route.searchParams.get('application'))?.id||userApplications()[0]?.id;if(!currentId)createApplication('starter');else{view=current().definition.workflow.length?'plan':'setup';buildStep=0;render();if(runtime.local&&route.searchParams.get('run')){try{await openLiveRun(route.searchParams.get('run'));await refreshLiveHistory();}catch(error){view='plan';liveError=error.message;render();notify(error.message);}}}}
  catch(error){$('app').innerHTML=`<main class="loading"><h1>Applications are unavailable.</h1><p>${escape(error.message)}</p><button class="button" id="retry">Try again</button></main>`;$('retry').addEventListener('click',load);}
}
function field(label,path,value,kind='input',options=[]){
  const id=`field-${path.replaceAll('.','-')}`,attrs=`id="${escape(id)}" data-path="${escape(path)}" ${saving?'disabled':''}`;
  const control=kind==='textarea'?`<textarea ${attrs} rows="3">${escape(value)}</textarea>`:kind==='select'?`<select ${attrs}>${options.map(option=>{const[val,text,disabled]=Array.isArray(option)?option:[option,option];return `<option ${disabled?'disabled':''} value="${escape(val)}" ${val===value?'selected':''}>${escape(text)}</option>`;}).join('')}</select>`:`<input ${attrs} ${kind==='number'?'type="number" step="any" min="0" max="1000000"':`type="text" maxlength="${path==='description'?2000:100}"`} value="${escape(value??'')}">`;
  return `<div class="field"><label for="${escape(id)}">${escape(label)}</label>${control}</div>`;
}
// What runs one member or step. The catalogue is the host's answer about this
// machine; a picker that has none still shows the choice a saved plan carries.
function backendMarkup({path,provider,model,human,inherit='This host’s default',label='Runs on'}){
  if(human)return '';
  const backends=runtime.backends||[],effective=provider||defaultBackend(backends)?.id||'',models=backendModels(backends,effective);
  const modelField=models.length
    ?field('Model',`${path}.model`,model||'','select',[['','Backend default'],...models.map(name=>[name,name])])
    :field('Model',`${path}.model`,model||'');
  return `${field(label,`${path}.provider`,provider||'','select',backendOptions(backends,provider,{inherit}))}${modelField}${backendIssue(backends,provider)?`<p class="validation" role="status">${escape(backendIssue(backends,provider))}</p>`:''}`;
}
function validation(d=draft()){try{validateDefinition(d);return '';}catch(error){return `<div class="validation" role="alert">${escape(error.message)}</div>`;}}
function render(){
  if(!currentId)return;
  // The header's play/stop slot follows the run that is actually going.
  const running=!!preview?.live&&['pending','running','waiting_human'].includes(preview.mode);
  const disclosures=[...$('app').querySelectorAll('details[data-disclosure]')].map(element=>({key:element.dataset.disclosure,open:element.open}));
  const scrolls=['.plan-canvas','.workbench-resources','.workbench-inspector'].map(selector=>({selector,top:$('app').querySelector(selector)?.scrollTop||0,left:$('app').querySelector(selector)?.scrollLeft||0}));
  const active=document.activeElement,key=$('app').contains(active)?['path','action','select','tab','record','request','step','filter','format','tool','outputKind','planDrag','assistantInput','requestField','humanInput','humanField','liveHistory'].find(k=>active.dataset?.[k]):null,val=key?active.dataset[key]:null,start=active?.selectionStart,end=active?.selectionEnd,index=active?.dataset?.index;
  const d=preview?.live?preview.definition:draft();
  // The plan is laid out for the canvas it is drawn in: measured from the canvas
  // on screen, and from the window on the very first render.
  const existing=$('app').querySelector('.plan-canvas');
  const size=existing?{width:Math.max(480,existing.clientWidth-40),height:Math.max(420,existing.clientHeight-40)}
    :{width:Math.max(760,Math.round(window.innerWidth*0.9)),height:Math.max(520,Math.round(window.innerHeight*0.8))};
  if(!(view==='plan'&&(preview?.live?renderedRunId===preview.runId:renderedPlanId===currentId))){let shape;try{shape=planShape(d);}catch{shape={levels:1,columns:d.workflow.length};}setCanvasMetrics({...size,...shape});}
  const markup=`<div class="shell ${view==='plan'?(preview?.live?'canvas-first-run':'canvas-first-plan'):''} ${railOpen?'rail-open':''}"><aside class="rail ${railOpen?'open':''}" aria-label="Your applications"><button class="rail-toggle" data-action="toggle-rail" aria-expanded="${railOpen}" aria-label="${railOpen?'Collapse the sidebar':'Expand the sidebar'}" title="${railOpen?'Collapse the sidebar':'Expand the sidebar'}"><span aria-hidden="true">${railOpen?'«':'»'}</span></button><div class="brand"><svg viewBox="0 0 32 32" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m23 8-7-4-9 5v14l9 5 7-4M7 9l9 6 7-4M16 15v13"/></svg><span class="rail-label">Conclave<small>STUDIO</small></span></div><button class="button rail-action" data-action="new" ${saving?'disabled':''} aria-label="New application" title="New application"><span class="rail-icon" aria-hidden="true">＋</span><span class="rail-label">New application</span></button><button class="button catalogue-open rail-action" data-action="open-catalogue" aria-label="Templates" title="Templates"><span class="rail-icon" aria-hidden="true">▤</span><span class="rail-label">Templates</span></button><div class="rail-apps"><span class="eyebrow rail-label">YOUR APPLICATIONS</span><nav class="nav">${sidebarApplications().map(a=>`<button data-select="${escape(a.id)}" class="${a.id===currentId?'active':''}" ${a.id===currentId?'aria-current="true"':''} ${saving?'disabled':''} title="${escape(drafts.get(a.id).name)}"><span class="app-mark" aria-hidden="true">${escape(drafts.get(a.id).name.slice(0,1).toUpperCase())}</span><span class="rail-label">${escape(drafts.get(a.id).name)}<span class="nav-meta">${drafts.get(a.id).agents.length} roles${!a.revision?' · draft':''}</span></span></button>`).join('')}</nav>${userApplications().length>3?`<button class="text-button rail-label" data-action="toggle-applications">${showAllApplications?'Show fewer':'Show all '+userApplications().length}</button>`:''}</div><div class="rail-foot">${accountMarkup()}<p class="runtime-label rail-label">${runtime.local?'On this computer':'Hosted'+(runtime.tenant&&runtime.tenant!=='local'?' · '+escape(runtime.tenant):'')}</p><details><summary><span class="rail-icon" aria-hidden="true">⚙</span><span class="rail-label">Workspace settings</span></summary>${runtime.local?'<button class="text-button" data-action="source-settings">Source settings</button>':''}<button class="text-button" data-action="edit-catalogue">Custom catalog</button></details></div></aside>
    <main class="workspace"><header class="application-header"><div><span class="crumb">Your applications <span class="breadcrumb-separator">/</span> ${view==='setup'?'Set up':'Canvas'}</span><h1 id="application-name">${escape(d.name)}</h1>${view==='plan'&&!preview?.live?`<p class="application-goal">${escape(d.description)}</p>`:''}</div><div class="header-actions"><div class="actions">${dirty()?`<button class="button" data-action="save" ${saving?'disabled':''}>${saving?'Saving…':'Save'}</button>`:''}${liveHistory.get(currentId)?.length?`<button class="button icon-only" data-action="show-history" title="Run history" aria-label="Run history">${uiIcon('clock')}</button>`:''}${view==='setup'?`<button class="button primary" data-action="run" ${liveBusy?'disabled':''}>Run</button>`:''}${view==='setup'?`<button class="button icon-only" data-action="back-to-plan" title="Back to the canvas" aria-label="Back to the canvas">${uiIcon('fit')}</button>`:''}</div><span class="save-state ${dirty()?'dirty':''}" id="save-state">${saving?'Saving…':dirty()?'Unsaved changes':layoutSaved?'Layout saved':`Saved · version ${current().revision}`}</span></div></header>
    <div class="main ${view==='plan'?'plan-workspace':''}" id="main-panel">${view==='setup'?buildMarkup():preview?.live?runMarkup():planMarkup(draft())}</div></main></div><div id="app-menu" class="canvas-menu-host" hidden></div>`;
  const stableRun=view==='plan'&&(preview?.live?renderedRunId===preview.runId&&$('app').querySelector('[data-run-canvas]'):renderedPlanId===currentId&&$('app').querySelector('[data-canvas-drop]'));
  if(stableRun)updateRunDOM($('app'),markup);else $('app').innerHTML=markup;
  renderedPlanId=!preview?.live&&view==='plan'?currentId:null;
  renderedRunId=preview?.live&&view==='plan'?preview.runId:null;
  for(const disclosure of disclosures){const element=$('app').querySelector(`[data-disclosure="${CSS.escape(disclosure.key)}"]`);if(element)element.open=disclosure.open;}
  $('main-panel').classList.toggle('run-workspace',!!preview?.live&&view!=='setup');
  sizeRunCanvas();sizePlanCanvas();
  if(!stableRun)for(const scroll of scrolls){const element=$('app').querySelector(scroll.selector);if(element){element.scrollTop=scroll.top;element.scrollLeft=scroll.left;}}
  applyDockOffset();
  syncMenu();
  followActiveNode();
  if(!stableRun&&key){const attribute=key.replace(/[A-Z]/g,letter=>'-'+letter.toLowerCase()),next=$('app').querySelector(`[data-${attribute}="${CSS.escape(val)}"]${index!==undefined?`[data-index="${CSS.escape(index)}"]`:''}`);next?.focus({preventScroll:true});if(next&&typeof start==='number'&&['INPUT','TEXTAREA'].includes(next.tagName)&&next.type!=='number')try{next.setSelectionRange(start,end);}catch{}}
}
function buildMarkup(){
  const d=draft(),nav=`<nav class="setup-nav calm-setup" aria-label="Application setup"><button class="setup-step ${buildStep===0&&!advanced?'active':''}" data-step="0">Goal</button><details class="setup-more" data-disclosure="setup-more" ${buildStep!==0||advanced?'open':''}><summary>Settings</summary><div class="settings-menu">${[[1,'Team'],[2,'Connections'],[3,'Results'],[5,'Limits and approvals']].map(([i,label])=>`<button data-step="${i}" class="${buildStep===i&&!advanced?'active':''}">${label}</button>`).join('')}<button data-action="toggle-advanced" aria-expanded="${advanced}" title="Runtime and definition" aria-label="Runtime and definition">${uiIcon('sliders')}</button></div></details></nav>${validation()}`;
  if(advanced)return `${nav}<div class="step-heading"><div><span class="eyebrow">ADVANCED SETTINGS</span><h2>Definitions and runtime integration</h2><p>Detailed control over assignments, state and execution limits.</p></div></div>${advancedMarkup(d,{field,validation,escape,saving})}<details class="advanced-contract"><summary>Runtime integration, exports and definition</summary>${contractMarkup(d,{validation,escape,runtime})}</details>`;
  return `${nav}<div class="build-layout"><div class="build-content">${[purposeMarkup,teamMarkup,accessMarkup,outputsMarkup,null,controlsMarkup][buildStep](d)}</div></div>`;
}
// What the person clarified, editable where the goal is: an answer can change as
// they learn more, and a question they do not want can be removed.
function requirementsMarkup(d){
  const items=d.requirements||[];if(!items.length)return '';
  return `<details class="quiet-details" data-disclosure="requirements" open><summary>Clarified requirements · ${items.length}</summary>${items.map((item,index)=>`<div class="field"><label for="requirement-${index}">${escape(item.question)}</label><textarea id="requirement-${index}" data-requirement-answer="${index}" rows="2" placeholder="Answer, or leave it blank">${escape(item.answer)}</textarea><button type="button" class="text-button" data-remove-requirement="${index}">Remove this question</button></div>`).join('')}<p class="field-help">Your answers are given to the team when the application runs. A question left blank stays open.</p></details>`;
}
function purposeMarkup(d){return `<div class="step-heading"><h2>What should it do?</h2></div><section class="panel purpose-panel">${field('Name','name',d.name)}${field('Goal','description',d.description,'textarea')}${requirementsMarkup(d)}<details class="quiet-details" data-disclosure="goal-details"><summary>Expected result</summary>${field('Include in the final result',`workflow.${d.workflow.length-1}.task`,d.workflow.at(-1).task,'textarea')}</details></section><details class="quiet-details" data-disclosure="request-form"><summary>Request form · ${(d.inputs||[]).length} fields</summary>${inputDesignerMarkup(d)}</details>${d.review_checks?`<details class="quiet-details" data-disclosure="review-checks"><summary>Review checklist</summary><ul>${d.review_checks.map(c=>`<li>${escape(c)}</li>`).join('')}</ul></details>`:''}`;}

function teamMarkup(d){return `<div class="step-heading"><div><span class="eyebrow">02 · TEAM</span><h2>Who does the work?</h2></div><button class="button" data-action="add-role">Add member</button></div><div class="role-grid">${d.agents.map((a,i)=>{const work=d.workflow.filter(w=>w.agent===a.id),outputs=[...new Set(work.map(w=>outputName(d,w.output)))];return `<details class="panel role-card quiet-row" data-disclosure="team-${escape(a.id)}"><summary><span class="role-symbol small">${escape(a.name.slice(0,1))}</span><strong>${escape(a.name)}</strong><span class="field-help">${a.kind==='human'?'Person':'AI'} · ${work.length} steps</span></summary><div class="row-editor"><div class="panel-top"><span class="role-symbol" aria-hidden="true">${escape(a.name.trim().slice(0,1).toUpperCase()||String(i+1))}</span><span class="kind-tag ${a.kind==='human'?'human':'ai'}">${a.kind==='human'?'Human':'AI'}</span><button class="icon-button" data-action="remove-role" data-index="${i}" title="Remove this member" aria-label="Remove ${escape(a.name)}" ${d.agents.length<2?'disabled':''}>×</button></div>${field('Member name',`agents.${i}.name`,a.name)}${field('Member type',`agents.${i}.kind`,a.kind||'ai','select',[['ai','AI member'],['human','Human member']])}${field('Responsibility',`agents.${i}.instructions`,a.instructions,'textarea')}<fieldset class="role-backend"><legend>Runs on</legend>${backendMarkup({path:`agents.${i}`,provider:a.provider,model:a.model,human:a.kind==='human'})||`<p class="field-help">A person completes this member’s steps; nothing runs them.</p>`}</fieldset><div class="role-deliverables"><span class="field-label">Prepares</span>${outputs.length?outputs.map(name=>`<span class="chip">${escape(name)}</span>`).join(''):'<span class="field-help">No steps yet.</span>'}</div></div></details>`;}).join('')}</div><details class="quiet-details"><summary>Team assignments</summary><section class="collaboration"><div class="section-head"><h3>How the team works together</h3><button class="text-button" data-open-plan>Edit visual plan</button></div><ol class="collaboration-list">${d.workflow.map(w=>`<li><strong>${escape(agentName(d,w.agent))}</strong><span>${escape(w.name)}</span><small>${escape(outputName(d,w.output))}</small></li>`).join('')}</ol></section></details>`;}
function accessMarkup(d){return `<div class="step-heading"><h2>Connections</h2><button class="button" data-action="open-catalogue" data-pane="connectors">Add connection</button></div>${connectorSetupMarkup(d)}<details class="quiet-details" data-disclosure="team-tools"><summary>Tools for each team member</summary>${d.agents.filter(a=>a.kind!=='human').map(a=>{const i=d.agents.indexOf(a);return `<details class="panel quiet-row" data-disclosure="tools-${escape(a.id)}"><summary><strong>${escape(a.name)}</strong><span>${a.tools.length} tools</span></summary><div class="row-editor">${[...new Set(accessCatalog.map(t=>t.category))].map(category=>`<details class="tool-group"><summary>${escape(category)}</summary><div class="access-options">${accessCatalog.filter(t=>t.category===category).map(tool=>`<label class="access-option"><input type="checkbox" data-tool="${tool.id}" data-index="${i}" ${a.tools.includes(tool.id)?'checked':''}><span><strong>${escape(tool.name)}</strong><small>${escape(tool.description)}</small></span></label>`).join('')}</div></details>`).join('')}</div></details>`;}).join('')}<button class="text-button" data-action="open-catalogue" data-pane="tools">Browse tools</button></details>`;}

function outputKind(state){return outputTypes.find(type=>type.id===(state.kind||(state.type==='text'?'document':'findings')))||outputTypes[1];}
function outputLayouts(type){return type==='text'?[['prose','Written report']]:[['cards','Cards'],['table','Table'],['board','Status board']];}
function outputsMarkup(d){return `<div class="step-heading"><div><span class="eyebrow">04 · OUTPUTS</span><h2>What should people see?</h2></div><button class="button" data-action="add-output">Add output</button></div><div class="output-grid">${d.views.map((v,i)=>{const state=d.state.find(s=>s.key===v.collection),stageIndex=d.workflow.findIndex(w=>w.output===state.key),kind=outputKind(state);return `<details class="panel output-card quiet-row" data-disclosure="result-${escape(v.id)}"><summary><strong>${escape(v.title)}</strong><span class="field-help">${escape(kind.name)}</span></summary><div class="row-editor"><div class="panel-top"><span class="output-symbol" aria-hidden="true">${state.type==='text'?'¶':'▦'}</span><span class="kind-tag">${escape(kind.name)}</span><button class="icon-button" data-action="remove-output" data-index="${i}" title="Remove this output" aria-label="Remove ${escape(v.title)}" ${d.views.length<2?'disabled':''}>×</button></div>${field('Output name',`views.${i}.title`,v.title)}<div class="field"><label for="output-kind-${i}">Output type</label><select id="output-kind-${i}" data-output-kind="${i}">${outputTypes.map(k=>`<option value="${k.id}" ${kind.id===k.id?'selected':''}>${k.name}</option>`).join('')}</select></div><div class="field"><label for="output-format-${i}">Presentation</label><select id="output-format-${i}" data-format="${i}">${outputLayouts(state.type).map(([key,label])=>`<option value="${key}" ${v.layout===key?'selected':''}>${label}</option>`).join('')}</select></div>${stageIndex>=0?field('Prepared by',`workflow.${stageIndex}.agent`,d.workflow[stageIndex].agent,'select',d.agents.map(a=>[a.id,a.name+(a.kind==='human'?' · Human':'')])):'<p class="field-help">No step writes to this yet.</p>'}<p class="field-help">${escape(kind.description)}</p>${kind.id==='artifact'?'<p class="tool-hint">Shows links to files once file access is connected.</p>':''}</div></details>`;}).join('')}</div>`;}
// The canvas shows the whole plan: one scale, computed from the board and the
// box it is drawn in, recomputed on resize and in full screen.
// Fit to the box, but never so small that the steps stop being readable.
function applyCanvasZoom(){
  const flow=$('app').querySelector('.canvas-flow[data-canvas-drop]');if(!flow)return;
  flow.style.zoom=canvasZoom??1;
  const level=$('app').querySelector('.plan-canvas .canvas-view-level');
  if(level)level.textContent=`${Math.round((canvasZoom??1)*100)}%`;
}
function applyRunZoom(){
  const flow=$('app').querySelector('.canvas-flow[data-run-canvas]');if(!flow)return;
  const area=flow.closest('.canvas-viewport');
  const zoom=runCanvasZoom??canvasFitZoom(area.clientWidth,area.clientHeight,parseFloat(flow.style.width),parseFloat(flow.style.height));
  runCanvasZoom=zoom;flow.style.zoom=zoom;
  const level=$('app').querySelector('.run-workbench .canvas-view-level');
  if(level)level.textContent=`${Math.round(zoom*100)}%`;
}
function sizeRunCanvas(){
  const area=$('app').querySelector('[data-canvas-mode="run"]');if(!area)return;
  const dock=area.closest('.run-workbench').querySelector('.activity-dock');
  area.closest('.run-workbench').style.setProperty('--activity-height',`${dock?.offsetHeight||48}px`);
  area.style.height=`${Math.max(200,window.innerHeight-area.getBoundingClientRect().top-window.scrollY-(dock?.offsetHeight||48)-18)}px`;
  applyRunZoom();
}
function suspendRunFollow(){followActive=false;const button=$('app').querySelector('[data-action="follow-active"]');if(button){button.setAttribute('aria-pressed','false');button.textContent='Follow active step off';}}
// Centre the canvas on a rectangle in screen coordinates: a step, or the middle
// of the two steps a connection joins.
function centreRect(box,area){
  if(!box||!area)return;
  const view=area.getBoundingClientRect();
  area.scrollLeft+=(box.left+box.right-view.left-view.right)/2;
  area.scrollTop+=(box.top+box.bottom-view.top-view.bottom)/2;
}
function stepsArea(...ids){
  for(const id of ids){const node=$('app').querySelector(`[data-plan-node="${CSS.escape(id)}"]`);if(node)return {node,area:node.closest('.canvas-viewport')};}
  return {};
}
function centrePlanStep(id){
  const {node,area}=stepsArea(id);if(node)centreRect(node.getBoundingClientRect(),area);
}
function centreEdge(from,to){
  const {area}=stepsArea(from,to);if(!area)return;
  const ends=[from,to].map(id=>$('app').querySelector(`[data-plan-node="${CSS.escape(id)}"]`)).filter(Boolean).map(node=>node.getBoundingClientRect());
  if(ends.length<1)return;
  const left=Math.min(...ends.map(box=>box.left)),right=Math.max(...ends.map(box=>box.right));
  const top=Math.min(...ends.map(box=>box.top)),bottom=Math.max(...ends.map(box=>box.bottom));
  centreRect({left,right,top,bottom},area);
}
function focusRunNode(id){
  const node=$('app').querySelector(`[data-run-node="${CSS.escape(id)}"]`),area=node?.closest('.canvas-viewport');if(!area)return;
  const box=node.getBoundingClientRect(),view=area.getBoundingClientRect();
  if(box.left<view.left+20||box.right>view.right-20||box.top<view.top+20||box.bottom>view.bottom-60){area.scrollLeft+=(box.left+box.right-view.left-view.right)/2;area.scrollTop+=(box.top+box.bottom-view.top-view.bottom)/2;}
}
function followActiveNode(){
  const id=preview?.definition?.workflow[preview.activeStep]?.id;
  if(!followActive||!id||lastFollowedStep===id||canvasPan)return;
  lastFollowedStep=id;focusRunNode(id);
}
function closeRunInspector(){const id=selectedRunStepId;selectedRunStepId=null;render();$('app').querySelector(`[data-run-step="${CSS.escape(id||'')}"]`)?.focus({preventScroll:true});}
function sizePlanCanvas(){
  const area=$('app').querySelector('[data-canvas-mode="plan"]');if(!area)return;
  area.style.height=`${Math.max(260,window.innerHeight-area.getBoundingClientRect().top-window.scrollY-74)}px`;
  if(canvasZoom===null)fitCanvas('plan');else applyCanvasZoom();
}
window.addEventListener('resize',sizePlanCanvas);
window.addEventListener('resize',sizeRunCanvas);

// The canvas action bar floats over the graph, so it can be moved out of the way
// of whatever is underneath it. The position is kept per application for this
// session, clamped to the canvas, and re-applied after every render because the
// bar is rebuilt with the canvas.
const dockOffsets=new Map();
let dockDrag=null;
function placeDock(dock,offset){
  dock.style.left=`${offset.left}px`;dock.style.top=`${offset.top}px`;
  dock.style.right='auto';dock.style.bottom='auto';dock.style.transform='none';
}
function clampDock(dock,left,top){
  const box=dock.offsetParent?.getBoundingClientRect();
  const width=dock.offsetWidth,height=dock.offsetHeight;
  const limitX=(box?box.width:window.innerWidth)-width-8,limitY=(box?box.height:window.innerHeight)-height-8;
  return {left:Math.round(Math.max(8,Math.min(limitX,left))),top:Math.round(Math.max(8,Math.min(limitY,top)))};
}
function applyDockOffset(){
  const dock=$('app').querySelector('[data-canvas-dock]'),offset=dock&&dockOffsets.get(currentId);
  if(!dock)return;
  if(offset)placeDock(dock,clampDock(dock,offset.left,offset.top));
}
// Pressing the bar's own surface drags it; its buttons keep their meaning.
$('app').addEventListener('pointerdown',event=>{
  const dock=event.target.closest?.('[data-canvas-dock]');
  if(!dock||event.button!==0||event.target.closest('button,input,select,textarea,a'))return;
  const box=dock.getBoundingClientRect(),panel=dock.offsetParent?.getBoundingClientRect();
  if(!panel)return;
  dockDrag={pointer:event.pointerId,dock,x:event.clientX,y:event.clientY,left:box.left-panel.left,top:box.top-panel.top,moved:false};
  dock.setPointerCapture?.(event.pointerId);event.preventDefault?.();
});
$('app').addEventListener('pointermove',event=>{
  const drag=dockDrag;if(!drag||drag.pointer!==event.pointerId)return;
  const next=clampDock(drag.dock,drag.left+(event.clientX-drag.x),drag.top+(event.clientY-drag.y));
  if(!drag.moved&&next.left===drag.left&&next.top===drag.top)return;
  drag.moved=true;drag.next=next;drag.dock.classList.add('dragging');placeDock(drag.dock,next);event.preventDefault?.();
});
function finishDockDrag(commit){
  const drag=dockDrag;if(!drag)return;dockDrag=null;
  drag.dock.releasePointerCapture?.(drag.pointer);drag.dock.classList.remove('dragging');
  if(commit&&drag.moved)dockOffsets.set(currentId,drag.next);
}
$('app').addEventListener('pointerup',event=>{if(dockDrag?.pointer===event.pointerId)finishDockDrag(true);});
$('app').addEventListener('pointercancel',event=>{if(dockDrag?.pointer===event.pointerId)finishDockDrag(false);});
// Double-clicking the bar puts it back where it started.
$('app').addEventListener('dblclick',event=>{
  const dock=event.target.closest?.('[data-canvas-dock]');
  if(!dock||event.target.closest('button,input,select,textarea,a')||!dockOffsets.has(currentId))return;
  dockOffsets.delete(currentId);render();
});

// The canvas context menu: the actions that belong to the step, connection or
// background that was pressed. It is a shortcut, never the only route — every
// item carries the same hook as the control it stands for, and a menu item the
// run cannot act on is disabled rather than missing.
let menu=null,menuFocusPending=false,menuReturnFocus=null,menuPoint=null;
function planNodeMenu(id,label){
  const d=draft(),index=d.workflow.findIndex(step=>step.id===id),step=d.workflow[index];
  if(!step)return null;
  const parents=planConnections(d).filter(edge=>edge.to===id);
  return {label:`Step actions: ${label}`,items:[
    menuItem('Edit step',{ 'data-plan-select':id }),
    menuItem('Advanced limits',{ 'data-menu-panel':'run-limits' }),
    menuSeparator(),
    menuItem('Add step after',{ 'data-add-after':id }),
    menuItem('Add parallel branch',{ 'data-add-parallel':id }),
    menuItem('Move earlier',{ 'data-plan-move':id,'data-direction':'earlier' },{disabled:index===0,why:'This is already the first step'}),
    menuItem('Move later',{ 'data-plan-move':id,'data-direction':'later' },{disabled:index===d.workflow.length-1,why:'This is already the last step'}),
    menuItem('Duplicate step',{ 'data-plan-duplicate':id }),
    menuSeparator(),
    menuItem('New member for this step',{ 'data-action':'new-step-member','data-menu-reveal':'panel' }),
    menuItem('Edit the shared member',{ 'data-action':'edit-shared-member','data-menu-reveal':'panel' }),
    menuItem('Make a copy for this step',{ 'data-action':'isolate-member','data-menu-reveal':'panel' }),
    menuItem('Edit output structure in setup',{ 'data-action':'set-up' }),
    menuSeparator(),
    menuItem('Connect to another step',{ 'data-canvas-port':id }),
    menuItem('Remove what this step depends on',{ 'data-menu-disconnect-all':id },{disabled:!parents.length,why:'This step depends on nothing'}),
    menuItem('Centre in view',{ 'data-menu-centre':id }),
    menuSeparator(),
    menuItem('Remove step',{ 'data-plan-remove':id },{destructive:true,disabled:d.workflow.length<2,why:'An application needs at least one step'})
  ]};
}
function planEdgeMenu(id,from,to,name){
  return {label:`Connection actions: ${name(from)} to ${name(to)}`,items:[
    menuItem('Insert a step between',{ 'data-insert-from':from,'data-insert-to':to }),
    menuItem('Add a parallel branch from here',{ 'data-add-parallel':to }),
    menuItem('Reverse direction',{ 'data-menu-reverse':id }),
    menuSeparator(),
    menuItem(`Show ${name(from)}`,{ 'data-menu-show-step':from }),
    menuItem(`Show ${name(to)}`,{ 'data-menu-show-step':to }),
    menuItem('Centre this connection in view',{ 'data-menu-centre-edge':id }),
    menuSeparator(),
    menuItem('Change where this connection ends',{ 'data-menu-reconnect':from }),
    menuItem('Remove this connection',{ 'data-canvas-disconnect':from,'data-canvas-disconnect-to':to },{destructive:true})
  ]};
}
function planCanvasMenu(){
  return {label:'Canvas actions',items:[
    menuItem('Add a step here',{ 'data-menu-add-step':'at' }),
    menuSeparator(),
    menuItem('Suggest a change',{ 'data-action':'suggest-plan-change' }),
    menuItem('Arrange',{ 'data-action':'tidy-canvas' }),
    menuSeparator(),
    menuItem('Fit to view',{ 'data-canvas-zoom':'fit' }),
    menuItem('Reset to 100%',{ 'data-canvas-zoom':'reset' }),
    menuItem('Full screen',{ 'data-action':'canvas-fullscreen' })
  ]};
}
function runCanvasMenu(){
  return {label:'Canvas actions',items:[
    menuItem(followActive?'Stop following the active step':'Follow the active step',{ 'data-action':'follow-active' }),
    menuItem(activityExpanded?'Hide the activity stream':'Show the activity stream',{ 'data-action':'toggle-activity' }),
    menuSeparator(),
    menuItem('Run information',{ 'data-action':'view-run-info' }),
    menuItem('Fit to view',{ 'data-run-zoom':'fit' }),
    menuItem('Reset to 100%',{ 'data-run-zoom':'reset' })
  ]};
}
function runNodeMenu(id,label){
  const step=preview?.definition.workflow.find(item=>item.id===id);
  if(!step)return null;
  const produced=preview.state?.[step.output];
  const stale=staleRunSteps(preview.definition,draft()).has(id);
  return {label:`Step actions: ${label}`,items:[
    menuItem('Step details',{ 'data-run-step':id }),
    menuItem('Show this step in activity',{ 'data-menu-step-activity':id },{disabled:!preview.events?.length,why:'This run has recorded no activity yet'}),
    menuItem('Copy this step\'s output',{ 'data-menu-copy-output':id },{disabled:produced===undefined||produced==='',why:'This step has not produced anything yet'}),
    ...(stale?[menuSeparator(),menuItem('This result came from an earlier version of the plan',{},{disabled:true,why:'Edit the plan, then run it again'})]:[])
  ]};
}
// Labels are plain here: menuMarkup escapes them once, where they are rendered.
function menuSpec(target){
  if(target.kind==='plan-node')return planNodeMenu(target.id,planStepName(target.id));
  if(target.kind==='plan-edge')return planEdgeMenu(target.id,target.from,target.to,planStepName);
  if(target.kind==='plan-canvas')return planCanvasMenu();
  if(target.kind==='run-canvas')return runCanvasMenu();
  if(target.kind==='run-node')return runNodeMenu(target.id,runStepName(target.id));
  return null;
}
function planStepName(id){return draft().workflow.find(step=>step.id===id)?.name||id;}
function runStepName(id){return preview?.definition.workflow.find(step=>step.id===id)?.name||id;}
function openMenu(target,point){
  const spec=menuSpec({...target,point});
  if(!spec||!spec.items.length)return;
  // The panel's commands read the selected step, so the pressed one becomes it —
  // and an open panel follows, rather than describing a step the menu is not on.
  if(target.kind==='plan-node'&&selectedStepId!==target.id){selectedStepId=target.id;if(showPlanInspector)render();}
  menuPoint=point;
  menu={kind:target.kind,id:target.id,from:target.from,to:target.to,point,items:spec.items,label:spec.label};
  menuFocusPending=true;
  menuReturnFocus=document.activeElement instanceof HTMLElement?document.activeElement:null;
  syncMenu();
}
function closeMenu({restoreFocus=true}={}){
  const host=$('app-menu');
  if(!menu&&!(host&&host.firstChild))return;
  menu=null;menuFocusPending=false;
  if(host){host.hidden=true;host.innerHTML='';}
  if(restoreFocus)menuReturnFocus?.focus?.({preventScroll:true});
  menuReturnFocus=null;
}
// The menu is redrawn from state, so an update that lands while it is open (a
// run reporting progress) does not close it or move it under the pointer.
function syncMenu(){
  const host=$('app-menu');
  if(!host)return;
  if(!menu){host.hidden=true;host.innerHTML='';return;}
  host.hidden=false;
  host.innerHTML=menuMarkup({label:menu.label,items:menu.items,point:menu.point,escape});
  const element=host.firstElementChild,box=element.getBoundingClientRect();
  element.style.left=`${Math.max(8,Math.min(window.innerWidth-box.width-8,menu.point.x))}px`;
  element.style.top=`${Math.max(8,Math.min(window.innerHeight-box.height-8,menu.point.y))}px`;
  if(menuFocusPending){menuFocusPending=false;menuFocusTargets(element)[0]?.focus({preventScroll:true});}
}

// Right-click on the canvas opens the menu for what was pressed. The native menu
// stays available everywhere else, including inside the settings fields.
$('app').addEventListener('contextmenu',event=>{
  const target=menuTarget(event.target);
  if(!target)return;
  event.preventDefault();
  // Canvas items place things, so the point is remembered in canvas coordinates.
  const point=canvasDropPoint(event.clientX,event.clientY)||{x:0,y:0};
  openMenu({...target}, {x:event.clientX,y:event.clientY,canvas:point});
});
// A touch device has no right button: press and hold does the same thing.
let longPress=null;
function cancelLongPress(){clearTimeout(longPress?.timer);longPress=null;}
$('app').addEventListener('pointerdown',event=>{
  if(event.pointerType==='mouse')return;
  const target=menuTarget(event.target);
  if(!target)return;
  longPress={pointer:event.pointerId,x:event.clientX,y:event.clientY,target:{...target}};
  longPress.timer=setTimeout(()=>{
    if(!longPress)return;
    skipPlanClickUntil=Date.now()+600;
    openMenu(longPress.target,{x:longPress.x,y:longPress.y});
    longPress=null;
  },500);
});
$('app').addEventListener('pointermove',event=>{
  if(!longPress||longPress.pointer!==event.pointerId)return;
  if(Math.hypot(event.clientX-longPress.x,event.clientY-longPress.y)>6)cancelLongPress();
});
$('app').addEventListener('pointerup',cancelLongPress);
$('app').addEventListener('pointercancel',cancelLongPress);
// Shift+F10 and the Menu key open the same menu for whatever has focus.
$('app').addEventListener('keydown',event=>{
  if(event.key==='ContextMenu'||event.shiftKey&&event.key==='F10'){
    const element=document.activeElement?.closest?.('[data-plan-node],[data-run-node],[data-canvas-edge],[data-canvas-mode]');
    const target=element?menuTarget(element):null;
    if(!target)return;
    event.preventDefault();
    const box=element.getBoundingClientRect();
    openMenu(target,{x:box.left+12,y:box.top+12});
    return;
  }
  const host=$('app-menu');
  if(!menu||!host?.contains(event.target))return;
  const items=menuFocusTargets(host.firstElementChild||document.body);
  const index=items.indexOf(document.activeElement);
  if(event.key==='Escape'){event.preventDefault();closeMenu();return;}
  if(event.key==='Tab'){closeMenu({restoreFocus:false});return;}
  const step=event.key==='ArrowDown'?1:event.key==='ArrowUp'?-1:event.key==='Home'?-items.length:event.key==='End'?items.length:0;
  if(!step)return;
  event.preventDefault();
  const next=Math.max(0,Math.min(items.length-1,(index<0?(step>0?-1:items.length):index)+step));
  items[next]?.focus({preventScroll:true});
});
// A press anywhere else closes it, before the press can do anything else.
$('app').addEventListener('pointerdown',event=>{
  if(menu&&!event.target.closest('[data-canvas-menu]')&&event.button===0)closeMenu({restoreFocus:false});
},true);
document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu&&!$('app-menu')?.contains(event.target))closeMenu();});

function canvasFullscreen(){
  const plan=$('app').querySelector('.plan-workbench');if(!plan)return;
  if(document.fullscreenElement)document.exitFullscreen?.();else plan.requestFullscreen?.();
}
document.addEventListener('fullscreenchange',()=>{sizePlanCanvas();const button=$('app').querySelector('[data-action="canvas-fullscreen"]');if(button){const expanded=!!document.fullscreenElement,label=expanded?'Exit full screen':'Full screen';button.setAttribute('aria-pressed',String(expanded));button.setAttribute('aria-label',label);button.title=label;button.innerHTML=uiIcon(expanded?'collapse':'expand');}});
function planMarkup(d){
  if(!d.workflow.some(step=>step.id===selectedStepId))selectedStepId=d.workflow[0].id;
  const command=commandState();return workbenchMarkup(d,{escape,field,agentName,outputName,capabilityName,showResources:showPlanResources,showInspector:showPlanInspector,showAssistant:showPlanAssistant,showReadiness:showPlanReadiness,insertion:planInsertion,provider:runtime.provider||'',issues:planReadiness(d,readinessRuntime()),runtime:readinessRuntime(),connectionSettings:memberIndex=>connectionAssignmentMarkup(d.agents[memberIndex],memberIndex,integrationCatalogue,integrationConnections,currentId,escape),selectedId:selectedStepId,pane:resourcePane,connectSource,selectedEdge,canvasZoom,saving,members:memberLibrary,libraryError,memberSaving,proposal:command.proposal,assistantText:command.text,assistantError:command.error,aiAvailable:runtime.available,controls:runControllerMarkup(),toolsDisabled:runInFlight(),backends:runtime.backends||[]});
}

function runMarkup(){
  if(!current().revision)return `<section class="panel"><h2>Save this application first</h2><p>Requests run the saved application through Conclave.</p></section>`;
  return studioLiveMarkup();
}
function stopLivePolling(){clearTimeout(liveTimer);liveTimer=null;}
function stopLiveWatch(){liveWatch?.controller.abort();liveWatch=null;}
// A step can work for minutes on one model call without recording an event, so
// the view is redrawn on a timer as well: how long a step has been working is
// something the page can only keep saying, not be told.
function stopLiveClock(){clearInterval(liveClock);liveClock=null;}
function followLiveClock(){
  const working=preview?.live&&['pending','running'].includes(preview.mode)
    &&(preview.definition?.workflow||[]).some(step=>runStepWorking(preview,step.id));
  if(!working||view!=='plan'){stopLiveClock();return;}
  if(liveClock)return;
  liveClock=setInterval(()=>{if(view!=='plan'||!preview?.live){stopLiveClock();return;}render();},1000);
}
function stopFollowingLiveRun(){stopLivePolling();stopLiveWatch();stopLiveClock();}
// A host that can push is told once; a host that cannot is asked on a timer.
// Both end up calling selectLiveRecord with the same record.
function followLiveRun(){
  followLiveClock();
  const active=preview?.live&&['pending','running','waiting_human'].includes(preview.mode);
  if(view!=='plan'||!active){stopLivePolling();stopLiveWatch();return;}
  if(liveFollowMode(runtime)!=='push'){stopLiveWatch();return pollLiveRun();}
  stopLivePolling();
  if(liveWatch?.runId===preview.runId)return;
  stopLiveWatch();
  const applicationId=currentId,runId=preview.runId,controller=new AbortController();
  liveWatch={runId,controller};
  (async()=>{
    const settings=apiSettingsNow();
    try{
      const response=await fetch(apiUrl(settings,liveStreamPath(runId)),{credentials:settings.credentials,headers:apiHeaders(settings,{headers:{accept:'text/event-stream'}}),signal:controller.signal});
      if(!response.ok||!response.body)throw new Error('The run stream is unavailable.');
      const reader=response.body.getReader(),decoder=new TextDecoder();
      let buffer='';
      while(true){
        const {value,done}=await reader.read();
        if(done)break;
        buffer+=decoder.decode(value,{stream:true});
        const frames=buffer.split('\n\n');buffer=frames.pop();
        for(const frame of frames){
          const data=frame.split('\n').filter(line=>line.startsWith('data: ')).map(line=>line.slice(6)).join('');
          if(!data)continue;
          if(currentId===applicationId&&view==='plan'&&preview?.runId===runId)selectLiveRecord(JSON.parse(data));
        }
      }
    }catch(error){
      // A stream that fails is not a run that failed: ask instead, and say
      // nothing about it if the caller simply stopped watching.
      if(!controller.signal.aborted&&currentId===applicationId&&preview?.runId===runId)pollLiveRun();
    }finally{
      if(liveWatch?.controller===controller)liveWatch=null;
    }
  })();
}
function sourceReferenceMarkup(source){
  try{const url=new URL(source);if(['http:','https:'].includes(url.protocol)&&!url.username&&!url.password)return `<a href="${escape(url.href)}" target="_blank" rel="noopener noreferrer">${escape(source)}</a>`;}catch{}
  return `<span>${escape(source)}</span>`;
}
function downloadResult(collection){
  const value=preview?.state[collection];if(!preview?.live||typeof value!=='string')throw new Error('Choose a completed text result.');
  const url=URL.createObjectURL(new Blob([value],{type:'text/plain;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=collection+'.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
function selectLiveRecord(record){
  const run=projectLiveRun(record),old=liveRuns.get(record.request.application_id);
  if(old?.runId===run.runId&&(old.events.at(-1)?.sequence||0)>(run.events.at(-1)?.sequence||0)){followLiveRun();return;}
  liveRuns.set(record.request.application_id,run);
  const savedRequests=liveHistory.get(record.request.application_id)||[];
  liveHistory.set(record.request.application_id,savedRequests.map(item=>item.id===record.id?{...item,status:record.status}:item));
  if(currentId!==record.request.application_id||view!=='plan')return;
  if(old?.runId!==run.runId){requests.live=run.task;requestInputs.live[currentId]=structuredClone(run.inputs);requestResources[currentId]=record.request.resources?.source||'';}
  if(old?.review?.id!==run.review?.id)humanDraft={decision:'',body:''};
  if(old?.runId!==run.runId){selectedRunStepId=null;activityExpanded=false;activityFilter='';followActive=true;lastFollowedStep=null;runCanvasZoom=renderedPlanId===currentId?canvasZoom:null;}
  preview=run;liveError='';
  if(run.mode==='running'){request(`/api/runs/${run.runId}/tool-approvals`).then(result=>{if(preview?.runId!==run.runId)return;const changed=JSON.stringify(pendingToolApprovals)!==JSON.stringify(result.approvals);pendingToolApprovals=result.approvals;if(changed)render();}).catch(()=>{});}else pendingToolApprovals=[];
  const url=new URL(location.href);url.searchParams.set('application',currentId);url.searchParams.set('run',run.runId);history.replaceState(null,'',url);
  render();followLiveRun();
}
function pollLiveRun(){
  stopLivePolling();
  if(view!=='plan'||!preview?.live||!['pending','running','waiting_human'].includes(preview.mode))return;
  const applicationId=currentId,runId=preview.runId;
  liveTimer=setTimeout(async()=>{
    try{const record=await request(`/api/runs/${runId}`);if(currentId===applicationId&&view==='plan'&&preview?.runId===runId)selectLiveRecord(record);}
    catch(error){if(currentId===applicationId){liveError=error.message;render();pollLiveRun();}}
  },1200);
}
async function openLiveRun(id){
  const record=await request(`/api/runs/${encodeURIComponent(id)}`);
  if(record.request.application_id!==currentId)throw new Error('This request belongs to another application.');
  view='plan';selectLiveRecord(record);
}
async function refreshLiveHistory(){
  if(!runtime.service)return;
  const applicationId=currentId;
  const result=await request(`/api/runs?application_id=${encodeURIComponent(applicationId)}`);
  liveHistory.set(applicationId,result.runs);if(currentId===applicationId&&view==='plan')render();
}
async function liveAction(action){
  if(liveBusy)return;const runId=preview?.runId;liveBusy=true;liveError='';stopFollowingLiveRun();render();
  try{
    let record;
    if(action==='start-live'){
      const task=requests.live.trim(),d=current().definition;
      validateDefinition(d);
      const issues=preflightIssues(d);
      if(issues.some(issue=>issue.severity==='error')||issues.length&&preflightReviewed!==preflightVersion())throw new Error('Resolve errors and review the pre-run warnings before running.');
      if(!task||task.length>20000)throw new Error('Describe what you need the team to do.');
      const inputs=validateRequestInputs(d,requestInputs.live[currentId]||{});
      const source=requestResources[currentId]||'';
      record=await request('/api/runs',{method:'POST',body:JSON.stringify({version:1,application_id:currentId,revision:current().revision,task,inputs,...(source?{resources:{source}}:{})})});
    }else if(action==='pause-live'){
      record=await request(`/api/runs/${runId}/pause`,{method:'POST',body:'{}'});
      notify('Pausing after the current step.');
    }else if(action==='cancel-live'){
      record=await request(`/api/runs/${runId}/cancel`,{method:'POST',body:'{}'});
    }else if(action==='submit-live-review'){
      const review=preview.review,collection=review.request.output;
      if(!humanDraft.body.trim())throw new Error('Record your reason or contribution.');
      const output=collection.type==='text'?{text:humanDraft.body.trim()}:{records:[{id:'human-'+review.stage,title:preview.definition.workflow.find(step=>step.id===review.stage).name,body:humanDraft.body.trim(),status:humanDraft.decision||(!collection.statuses.length?'recorded':''),source_refs:[],...humanRecordValues(collection)}]};
      record=await request(`/api/runs/${runId}/reviews`,{method:'POST',body:JSON.stringify({review_id:review.id,output})});
      selectLiveRecord(record);
      record=await request(`/api/runs/${runId}/resume`,{method:'POST',body:'{}'});
    }else if(action==='resume-live'){
      record=await request(`/api/runs/${runId}/resume`,{method:'POST',body:'{}'});
    }
    selectLiveRecord(record);
    if(action==='start-live')$('run-dialog').close();
    await refreshLiveHistory();
  }catch(error){liveError=error.message;notify(error.message);}
  finally{liveBusy=false;render();if($('run-dialog').open)$('run-configuration').innerHTML=studioLiveMarkup(true);pollLiveRun();}
}
  // Everything a person does to a run, in the canvas's own floating action bar:
// start, pause, stop, and the one thing the run is waiting for. Icons carry
// hovertext; the action that needs a decision keeps its words.
const runInFlight=()=>!!preview?.live&&['pending','running','waiting_human','paused'].includes(preview.mode);
function runControllerMarkup(){
  // Icons only, one per thing a person can do to the run. What the run is waiting
  // for (Review input, View results) stays in words in the status line above the
  // canvas, so no action is offered twice and none is wordless-only.
  const busy=liveBusy?'disabled':'',run=()=>`<button type="button" class="canvas-action primary" data-action="run" ${busy} title="${preview?.live?'Run again':'Run this application'}" aria-label="${preview?.live?'Run again':'Run this application'}">${uiIcon('play')}</button>`;
  const stop=()=>`<button type="button" class="canvas-action stop" data-action="cancel-live" ${busy} title="Stop this run" aria-label="Stop this run">${uiIcon('stop')}</button>`;
  if(!preview?.live||['completed','stopped','failed','cancelled','interrupted'].includes(preview.mode))return run();
  if(preview.mode==='paused'||(preview.mode==='stopped'&&preview.resumable))return `<button type="button" class="canvas-action primary" data-action="resume-live" ${busy||!runtime.available?'disabled':''} title="Continue from the next step" aria-label="Continue from the next step">${uiIcon('play')}</button>${stop()}`;
  // Waiting for a person is not running: there is nothing to pause, and Run
  // comes back once the review is submitted.
  if(preview.mode==='waiting_human')return stop();
  return `<button type="button" class="canvas-action" data-action="pause-live" ${busy} title="Pause after the current step" aria-label="Pause after the current step">${uiIcon('pause')}</button>${stop()}`;
}

function studioLiveMarkup(requestOnly=false){
  const saved=current().definition,d=preview?.live?preview.definition:saved,state=preview?.live?preview.state:initialState(d);
  const requestForm=`<div class="field"><label for="live-request">What do you need the team to do?</label><textarea id="live-request" data-request="live" rows="2" maxlength="20000" required>${escape(requests.live)}</textarea></div><details class="quiet-details" data-disclosure="live-context" open><summary>Context and sources · ${(saved.inputs||[]).length} fields</summary>${generatedRequestMarkup(saved,'live')}</details>`;
  const visible=d.views.filter(view=>Array.isArray(state[view.collection])?state[view.collection].length:!!state[view.collection]);
  const outputs=visible.length?`<details class="run-results" data-disclosure="live-results"><summary>Results · ${visible.length}</summary>${visible.map(view=>`<details class="quiet-details" data-disclosure="live-output-${escape(view.id)}"><summary>${escape(view.title)}</summary>${viewMarkup(view,state,d)}</details>`).join('')}</details>`:'<div class="empty">Results will appear as the team completes its work.</div>';
const approvalPanel=pendingToolApprovals.length?`<section class="tool-approval-panel" role="region" aria-label="External action approval"><h3>Review external action</h3><p>Approval applies to this call only. Check the destination and content before proceeding.</p>${pendingToolApprovals.map(item=>`<article><strong>${escape(item.agent)} · ${escape(item.tool)}</strong><pre class="code">${escape(JSON.stringify(item.arguments,null,2))}</pre><button class="button primary" data-tool-approval="${escape(item.id)}" data-approved="true">Approve this action</button><button class="button remove" data-tool-approval="${escape(item.id)}" data-approved="false">Reject</button></article>`).join('')}</section>`:'';
  const progress=preview?.live?runProgressMarkup(d,preview,selectedRunStepId,{controls:runControllerMarkup(),toolsDisabled:runInFlight(),provider:runtime.provider||'',list:executionPlanMarkup(d,preview,selectedRunStepId,{escape,agentName,outputName,capabilityName}),zoom:runCanvasZoom??1,activityExpanded,activityFilter,followActive,detail:selectedRunStepId?executionDetailMarkup(d,preview,selectedRunStepId,{escape,agentName,outputName,capabilityName,backends:runtime.backends||[]}):'',escape,agentName,outputName,capabilityName,backends:runtime.backends||[]}):'';
  return `${dirty()&&requestOnly?'<p class="version-note">New requests use your saved application. Save edits first.</p>':''}${approvalPanel}${liveRunMarkup(saved,preview?.live?preview:null,{escape,requestForm,outputs,human:humanCheckpointMarkup(d),progress,runtime:readinessRuntime(),busy:liveBusy,error:liveError,source:requestResources[currentId]||'',applicationId:currentId,requestOnly,warningsReviewed:preflightReviewed===preflightVersion()})}`;
}
function recordFieldForm(collection){return (collection.fields||[]).map(f=>{const value=humanDraft.fields?.[f.key]??'',attrs=`id="human-field-${escape(f.key)}" data-human-field="${escape(f.key)}"`;return `<div class="field"><label for="human-field-${escape(f.key)}">${escape(f.label)}${f.required?' · required':''}</label>${['enum','boolean'].includes(f.type)?`<select ${attrs}><option value="">Choose…</option>${(f.type==='boolean'?['true','false']:f.options).map(option=>`<option value="${escape(option)}" ${String(value)===option?'selected':''}>${escape(option)}</option>`).join('')}</select>`:`<input ${attrs} type="${f.type==='number'?'number':f.type==='date'?'date':'text'}" ${f.type==='number'?'step="any"':''} value="${escape(value)}">`}</div>`;}).join('');}
function humanRecordValues(collection){const values={};for(const f of collection.fields||[]){const value=humanDraft.fields?.[f.key];if(value===undefined||value==='')continue;values[f.key]=f.type==='number'?Number(value):f.type==='boolean'?value==='true':value;}return validateRecordValues(values,collection.fields||[]);}
function recordFieldMarkup(collection,record){return (collection.fields||[]).filter(f=>record[f.key]!==undefined).map(f=>`<div><strong>${escape(f.label)}</strong>: ${escape(record[f.key])}</div>`).join('');}
function humanCheckpointMarkup(d){
  if(preview?.mode!=='waiting_human')return '';
  if(preview.live&&preview.review?.status==='submitted')return `<section class="human-checkpoint"><h2>Your review is recorded</h2><p>Recorded as ${escape(preview.review.reviewer)}. Continue when the AI connection is ready.</p><button class="button primary" data-action="resume-live" ${liveBusy||!runtime.available?'disabled':''}>Resume</button></section>`;
  const step=d.workflow[preview.activeStep],output=d.state.find(s=>s.key===step.output),statuses=output.statuses.filter(status=>status!=='proposed');
  return `<section class="human-checkpoint" aria-labelledby="human-checkpoint-title"><span class="kind-tag human">Human input required</span><h2 id="human-checkpoint-title">${escape(step.name)}</h2><p>Assigned to <strong>${escape(agentName(d,step.agent))}</strong> · writes to ${escape(output.label)}</p><p class="checkpoint-task">${escape(step.task)}</p>${output.type==='records'&&statuses.length?`<div class="field"><label for="human-decision">${output.kind==='decision'?'Decision':'Result status'}</label><select id="human-decision" data-human-input="decision"><option value="">Choose explicitly…</option>${statuses.map(status=>`<option value="${escape(status)}" ${humanDraft.decision===status?'selected':''}>${escape(status.replaceAll('_',' '))}</option>`).join('')}</select></div>`:''}<div class="field"><label for="human-response">${output.type==='text'?'Your contribution':'Reason or contribution'}</label><textarea id="human-response" data-human-input="body" rows="3" maxlength="20000" placeholder="Your decision and the reason.">${escape(humanDraft.body)}</textarea></div>${recordFieldForm(output)}<div class="checkpoint-actions"><p class="field-help">This records your contribution as ${escape(runtime.reviewer)}. It does not authorise external tools or actions.</p><button class="button primary" data-action="submit-live-review" ${preview.live&&liveBusy?'disabled':''}>Record and continue</button></div></section>`;
}
// One line is enough to recognise a record; the body is a click away, and the
// sources are named rather than counted.
function excerpt(body){const text=String(body||'').replace(/\s+/g,' ').trim();return text.length>120?`${text.slice(0,120).replace(/[ ,.;:]$/,'')}…`:text;}
function sourceChips(refs){const list=refs||[];return list.length>9?`<span class="source-chip" title="${list.length} sources">${list.length}</span>`:list.map((ref,index)=>`<span class="source-chip" title="${escape(ref)}">${index+1}</span>`).join('');}
// A document reads as paragraphs, not as one escaped block.
function prose(value){return String(value||'').split(/\n{2,}/).map(block=>`<p>${escape(block.trim()).replaceAll('\n','<br>')}</p>`).join('');}
function viewMarkup(view,state,d){
  const value=state[view.collection];let markup;
  if(view.layout==='prose')markup=value?`${preview?.live?`<button class="button" data-download-output="${escape(view.collection)}">Download text</button>`:''}<div class="prose">${prose(value)}</div>${preview?.textOrigins[view.collection]?`<p class="state-meta">Prepared by ${escape(agentName(d,preview.textOrigins[view.collection].agent))}</p>`:''}`:'<div class="empty">The report will appear when its step completes.</div>';
  else if(!value.length)markup='<div class="empty">Findings will appear as work progresses.</div>';
  else if(view.layout==='board'){const stateDef=d.state.find(s=>s.key===view.collection),statuses=[...new Set([...stateDef.statuses,...value.map(item=>item.status||'recorded')])];markup=`<div class="status-board" style="--columns:${statuses.length}">${statuses.map(status=>`<section class="board-column"><h3>${escape(status.replaceAll('_',' '))}<span>${value.filter(item=>(item.status||'recorded')===status).length}</span></h3>${value.map((item,i)=>(item.status||'recorded')===status?`<button class="record" data-record="${escape(view.collection)}" data-index="${i}"><h3>${escape(item.title)}</h3><p class="record-excerpt">${escape(excerpt(item.body))}</p>${recordFieldMarkup(d.state.find(s=>s.key===view.collection),item)}<div class="by">${escape(agentName(d,item.contributions.at(-1)?.agent))}<span class="by-right">${sourceChips(item.source_refs)}</span></div></button>`:'').join('')}</section>`).join('')}</div>`;}
  else if(view.layout==='table')markup=`<div class="table-wrap"><table><thead><tr><th>Finding</th><th>Status</th><th>Contributor</th>${(d.state.find(s=>s.key===view.collection).fields||[]).map(f=>`<th>${escape(f.label)}</th>`).join('')}</tr></thead><tbody>${value.map((item,i)=>`<tr><td><button data-record="${escape(view.collection)}" data-index="${i}">${escape(item.title)}</button></td><td><span class="finding-status ${['supported','refuted','open'].includes(item.status)?item.status:''}">${escape(item.status||'recorded')}</span></td><td>${escape(agentName(d,item.contributions.at(-1)?.agent))}</td>${(d.state.find(s=>s.key===view.collection).fields||[]).map(f=>`<td>${escape(item[f.key]??'')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  else markup=value.map((item,i)=>`<button class="record" data-record="${escape(view.collection)}" data-index="${i}"><div class="record-head"><span class="record-id">${escape(item.id)}</span><span class="finding-status ${['supported','refuted','open'].includes(item.status)?item.status:''}">${escape(item.status||'recorded')}</span></div><h3>${escape(item.title)}</h3><p class="record-excerpt">${escape(excerpt(item.body))}</p>${recordFieldMarkup(d.state.find(s=>s.key===view.collection),item)}<div class="by">${escape(agentName(d,item.contributions.at(-1)?.agent))}<span class="by-right">${sourceChips(item.source_refs)}${item.source_refs.length?`${item.source_refs.length} ${item.source_refs.length===1?'source':'sources'} · `:''}Open finding</span></div></button>`).join('');
  return `<section class="section output-section"><div class="view-header"><h2>${escape(view.title)}</h2>${Array.isArray(value)?`<span class="output-count">${value.length}</span>`:''}</div>${markup}</section>`;
}

function markDirty(){const span=$('save-state');if(span){span.textContent='Unsaved changes';span.classList.add('dirty');}const name=$('application-name');if(name&&!preview?.live)name.textContent=draft().name;}
function updateField(path,value){
  const d=draft(),parts=path.split('.');if(parts[0]==='budget_warnings')d.budget_warnings??={};let object=d;for(const part of parts.slice(0,-1))object=object[part];const key=parts.at(-1),before=object[key];
  if(path.endsWith('.statuses'))value=value.split(',').map(x=>x.trim()).filter(Boolean);if(path.endsWith('.required'))value=value==='true';if(path.startsWith('run_config.')||path.startsWith('run_budget.')||path.startsWith('budget_warnings.'))value=value===''?null:Number(value);object[key]=value;
  if(parts[0]==='workflow'&&key==='id'){for(const edge of d.connections||[]){if(edge.from===before)edge.from=value;if(edge.to===before)edge.to=value;}if(d.canvas?.positions?.[before]){d.canvas.positions[value]=d.canvas.positions[before];delete d.canvas.positions[before];}}
  if(parts[0]==='integrations'&&key==='connector'){const connector=connectorDescriptors.find(c=>c.id===value);object.mode=connector.default_mode;object.scope='';render();}
  if(parts[0]==='agents'&&key==='kind'&&value==='human')object.tools=[];
  // A model belongs to the backend it was chosen for, so changing the backend
  // drops it rather than carrying a name across to a model that may not exist.
  if(key==='provider'&&['agents','workflow'].includes(parts[0]))delete object.model;
  if(parts[0]==='agents'&&key==='id')for(const stage of d.workflow)if(stage.agent===before)stage.agent=value;
  if(parts[0]==='state'&&key==='key'){for(const stage of d.workflow)if(stage.output===before)stage.output=value;for(const view of d.views)if(view.collection===before)view.collection=value;}
  if(parts[0]==='state'&&key==='type'){object.merge=value==='text'?'replace':'upsert';object.kind=value==='text'?'document':'findings';object.statuses=[];if(value==='text')delete object.fields;for(const view of d.views)if(view.collection===object.key)view.layout=value==='text'?'prose':'cards';}
  if(parts[0]==='views'&&key==='collection')object.layout=d.state.find(s=>s.key===value)?.type==='text'?'prose':'cards';
  if(parts[0]==='views'&&key==='title'&&d.views.filter(v=>v.collection===object.collection).length===1)d.state.find(s=>s.key===object.collection).label=value;
  markDirty();if(['type','collection','kind','provider'].includes(key)||(parts[0]==='workflow'&&['agent','output'].includes(key))||(view==='plan'&&['workflow','agents'].includes(parts[0])))render();
}
function setOutputKind(index,id){
  const kind=outputTypes.find(type=>type.id===id);if(!kind)throw new Error('Choose a supported output type.');
  const d=draft(),view=d.views[index],state=d.state.find(s=>s.key===view.collection);state.kind=kind.id;state.type=kind.type;if(kind.type==='text')delete state.fields;state.merge=kind.type==='text'?'replace':'upsert';state.statuses=[...kind.statuses];for(const v of d.views)if(v.collection===state.key)v.layout=kind.layout;markDirty();render();
}
function setOutputFormat(index,layout){
  if(!['cards','table','board','prose'].includes(layout))throw new Error('Choose a supported presentation.');
  const d=draft(),view=d.views[index],state=d.state.find(s=>s.key===view.collection),type=layout==='prose'?'text':'records';
  if(state.type!==type){state.type=type;state.kind=type==='text'?'document':'findings';state.merge=type==='text'?'replace':'upsert';state.statuses=[];if(type==='text')delete state.fields;for(const v of d.views)if(v.collection===state.key)v.layout=layout;}else view.layout=layout;markDirty();render();
}
async function saveApplication(){
  validateDefinition(draft());const target=currentId,submitted=structuredClone(draft()),revision=current().revision;saving=true;render();
  try{const result=await request(revision?`/api/applications/${encodeURIComponent(target)}`:'/api/applications',{method:revision?'PUT':'POST',body:JSON.stringify({definition:submitted,...(revision?{revision}:{})})});applications[applications.findIndex(a=>a.id===target)]=result;drafts.delete(target);drafts.set(result.id,submitted);if(currentId===target){currentId=result.id;layoutSaved=!!revision&&result.revision===revision;}notify(result.revision===revision?'Layout saved.':'Application saved.');return {id:result.id,revision:result.revision};}
  finally{saving=false;render();}
}
// Where a step sits on the canvas is part of the definition, so the arrangement
// is saved on its own: nobody should press Save to keep where they put their
// steps. Only the positions are written — anything else still being edited stays
// in the draft and still waits for Save.
function scheduleLayoutSave(){clearTimeout(layoutSaveTimer);layoutSaveTimer=setTimeout(saveLayout,700);}
async function saveLayout(){
  layoutSaveTimer=null;const target=currentId,application=current();
  if(!application?.revision)return;
  // A save that is already going writes the same definition itself.
  if(saving){layoutSaveTimer=setTimeout(saveLayout,300);return;}
  let definition;
  try{definition=layoutSaveDefinition(application.definition,draft());}catch{return;}
  if(JSON.stringify(definition.canvas)===JSON.stringify(application.definition.canvas))return;
  saving=true;render();
  try{
    const result=await request(`/api/applications/${encodeURIComponent(target)}`,{method:'PUT',body:JSON.stringify({definition,revision:application.revision})});
    const index=applications.findIndex(item=>item.id===target);if(index>=0)applications[index]=result;if(currentId===target)layoutSaved=result.revision===application.revision;notify('Layout saved.');
  }catch(error){notify(error.message);}
  finally{saving=false;render();}
}
function pauseActiveRun(){stopFollowingLiveRun();}
function resetRuns(){layoutSaved=false;clearTimeout(layoutSaveTimer);layoutSaveTimer=null;stopFollowingLiveRun();liveError='';preview=null;requests={live:''};}
function createApplication(templateId='blank',brief={}){
  const definition=composeCatalogApplication(templateId,brief.roles||[],brief.tools||{});
  if(brief.name)definition.name=brief.name.trim();if(brief.purpose)definition.description=brief.purpose.trim();if(brief.result)definition.workflow.at(-1).task=brief.result.trim();validateDefinition(definition);
  resetRuns();showPlanResources=false;showPlanInspector=false;const id='draft-'+crypto.randomUUID();applications.push({id,definition:null,revision:null});drafts.set(id,definition);currentId=id;view='setup';buildStep=0;advanced=false;render();notify('Application draft created. Save it to keep it.');
}
function chooseApplication(id){if(saving||liveBusy||!applications.some(a=>a.id===id))return;resetRuns();const url=new URL(location.href);url.searchParams.delete('application');url.searchParams.delete('run');history.replaceState(null,'',url);showPlanResources=false;showPlanInspector=false;showPlanAssistant=false;showPlanReadiness=false;planInsertion=null;currentId=id;selectedStepId=null;selectedRunStepId=null;view=draft().workflow.length?'plan':'setup';buildStep=0;advanced=false;render();}
// Requests, as a list of their own: the canvas stays the canvas.
function historyMarkup(){
  const runs=liveHistory.get(currentId)||[],revision=current().revision;
  if(!runs.length)return `<div class="section-head"><h2 id="history-title">Requests</h2></div><p class="field-help">No requests yet.</p>`;
  return `<div class="section-head"><h2 id="history-title">Requests</h2></div><table class="history-table"><thead><tr><th>Started</th><th>Status</th><th>Version</th><th>Request</th><th></th></tr></thead><tbody>${runs.map(run=>`<tr><td>${escape(new Date(run.created_at).toLocaleString())}</td><td>${escape(String(run.status).replaceAll('_',' '))}</td><td>${run.revision===revision?`${run.revision} · current`:`${run.revision} · older`}</td><td>${escape(String(run.task||'').slice(0,90))}${String(run.task||'').length>90?'…':''}</td><td><button class="button" data-run-open="${escape(run.id)}">Open</button></td></tr>`).join('')}</tbody></table>`;
}
function showView(next){if(!['plan','setup'].includes(next)||saving||liveBusy)return;stopFollowingLiveRun();view=next;buildStep=next==='setup'?0:buildStep;advanced=false;humanDraft={decision:'',body:''};render();if(next==='plan'&&current().revision){refreshLiveHistory().catch(error=>{liveError=error.message;render();});followLiveRun();}}
function openRunConfiguration(){
  if(saving||liveBusy)return;
  if(!current().revision){notify('Save this application before running it.');return;}
  preflightReviewed=null;
  view='plan';liveError='';render();
  $('run-configuration').innerHTML=studioLiveMarkup(true);
  $('run-dialog-title').textContent=preview?.live?'Run again':'Run configuration';
  if(!$('run-dialog').open)$('run-dialog').showModal();
}
function nextIdentifier(prefix,items,fieldName='id'){let n=items.length+1;while(items.some(x=>x[fieldName]===`${prefix}_${n}`))n++;return `${prefix}_${n}`;}
function insertWork(d,stage){const final=d.workflow.at(-1),finalType=d.state.find(s=>s.key===final.output)?.type;if(d.state.find(s=>s.key===stage.output)?.type==='records'&&finalType==='text')d.workflow.splice(d.workflow.length-1,0,stage);else d.workflow.push(stage);}
function addRole({name,responsibility,output,kind='ai',preset='custom'}){
  const d=structuredClone(draft());if(!name?.trim()||!responsibility?.trim())throw new Error('Give the member a name and responsibility.');if(!['ai','human'].includes(kind))throw new Error('Choose AI or human.');
  if(output==='__new_decision'){const key=nextIdentifier('decision',d.state,'key');d.state.push({key,label:'Human review decision',kind:'decision',type:'records',merge:'upsert',statuses:['proposed','approved','rejected','changes_requested']});d.views.push({id:nextIdentifier('view',d.views),title:'Human review decision',collection:key,layout:'cards'});output=key;}
  if(!d.state.some(state=>state.key===output))throw new Error('Choose an existing output.');
  const id=nextIdentifier('member',d.agents);d.agents.push({id,name:name.trim(),kind,role:preset,instructions:responsibility.trim(),tools:kind==='human'?[]:[...(rolePresets.find(role=>role.id===preset)?.tools||[])]});
  const step={id:nextIdentifier('stage',d.workflow),name:(name.trim()+' contribution').slice(0,100),agent:id,task:responsibility.trim(),output};if(kind==='human')d.workflow.push(step);else insertWork(d,step);validateDefinition(d);drafts.set(currentId,d);render();
}
function addOutput({name,layout,agent,task,kind,recordFields=[]}){
  const d=structuredClone(draft());if(!name?.trim()||!task?.trim())throw new Error('Name the output and describe the work.');if(!['cards','table','board','prose'].includes(layout)||!d.agents.some(a=>a.id===agent))throw new Error('Choose a presentation and member.');
  const selected=outputTypes.find(type=>type.id===kind)||outputTypes.find(type=>type.id===(layout==='prose'?'document':'findings'));
  if((selected.type==='text')!==(layout==='prose'))throw new Error('Choose a presentation that matches the output type.');
  const key=nextIdentifier('output',d.state,'key');d.state.push({key,label:name.trim(),kind:selected.id,type:selected.type,merge:selected.type==='text'?'replace':'upsert',statuses:[...selected.statuses],fields:structuredClone(recordFields)});d.views.push({id:nextIdentifier('view',d.views),title:name.trim(),collection:key,layout});insertWork(d,{id:nextIdentifier('stage',d.workflow),name:('Prepare '+name.trim()).slice(0,100),agent,task:task.trim(),output:key});validateDefinition(d);drafts.set(currentId,d);render();
}
function removeRole(index,reassign){const d=structuredClone(draft()),role=d.agents[index];if(!role||d.agents.length<2)throw new Error('Keep at least one role.');if(d.workflow.some(w=>w.agent===role.id)&&!d.agents.some(a=>a.id===reassign&&a.id!==role.id))throw new Error('Choose who takes over this role’s work.');for(const stage of d.workflow)if(stage.agent===role.id)stage.agent=reassign;d.agents.splice(index,1);validateDefinition(d);drafts.set(currentId,d);render();}
function removeOutput(index){const d=structuredClone(draft()),view=d.views[index];if(!view||d.views.length<2)throw new Error('Keep at least one output.');d.views.splice(index,1);if(!d.views.some(v=>v.collection===view.collection)){d.state=d.state.filter(s=>s.key!==view.collection);d.workflow=d.workflow.filter(w=>w.output!==view.collection);}prunePlanGraph(d);validateDefinition(d);drafts.set(currentId,d);render();}
function mutate(action,index){
  const d=draft();if(saving)return;
  if(action==='remove-agent'){const id=d.agents[index].id;if(d.agents.length===1)throw new Error('Keep at least one agent.');if(d.workflow.some(w=>w.agent===id))throw new Error('Assign this agent’s stages to another role before removing it.');d.agents.splice(index,1);}
  if(action==='add-stage')d.workflow.push({id:nextIdentifier('stage',d.workflow),name:'New stage',agent:d.agents[0].id,task:'Describe the stage task.',output:d.state[0].key});
  if(action==='remove-stage'){if(d.workflow.length===1)throw new Error('Keep at least one stage.');d.workflow.splice(index,1);}
  if(action==='move-up'&&index>0)[d.workflow[index-1],d.workflow[index]]=[d.workflow[index],d.workflow[index-1]];
  if(action==='add-state')d.state.push({key:nextIdentifier('collection',d.state,'key'),label:'New collection',type:'records',merge:'upsert',statuses:[]});
  if(action==='remove-state'){const key=d.state[index].key;if(d.state.length===1)throw new Error('Keep at least one state collection.');if(d.workflow.some(w=>w.output===key)||d.views.some(v=>v.collection===key))throw new Error('Update stages and views that use this collection before removing it.');d.state.splice(index,1);}
  if(action==='add-view')d.views.push({id:nextIdentifier('view',d.views),title:'New view',collection:d.state[0].key,layout:d.state[0].type==='text'?'prose':'cards'});
  if(action==='remove-view'){if(d.views.length===1)throw new Error('Keep at least one view.');d.views.splice(index,1);}prunePlanGraph(d);render();
}
function exportDefinition(){validateDefinition(draft());const blob=new Blob([JSON.stringify(draft(),null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='application.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify('Application definition exported.');}
async function reloadMembers(){try{memberLibrary=(await request('/api/members')).members;libraryError='';}catch{libraryError='The member library is unavailable. Try again.';}render();}
async function saveCurrentMember(){
  const selected=draft().workflow.find(step=>step.id===selectedStepId)||draft().workflow[0],member=structuredClone(draft().agents.find(agent=>agent.id===selected.agent));memberSaving=true;render();
  try{const result=await request('/api/members',{method:'POST',body:JSON.stringify({member})});memberLibrary.unshift(result);libraryError='';resourcePane='members';notify('Member snapshot saved to the library.');}finally{memberSaving=false;render();}
}
function isolateSelectedMember(){updatePlan(copyStepMember(draft(),selectedStepId),selectedStepId);notify('This step now uses an independent member.');}
function createStepMember(){
  const d=structuredClone(draft()),step=d.workflow.find(item=>item.id===selectedStepId);if(!step)throw new Error('Select a step.');
  const original=d.agents.find(member=>member.id===step.agent),human=original?.kind==='human';
  const member={id:nextIdentifier('member',d.agents),name:human?'New reviewer':'New specialist',kind:human?'human':'ai',instructions:'Describe this member’s responsibility.',tools:[]};d.agents.push(member);step.agent=member.id;validateDefinition(d);updatePlan(d,step.id);
  const details=$('app').querySelector(`[data-disclosure="member-${CSS.escape(member.id)}-responsibility"]`);if(details)details.open=true;
}
function openStepPicker(insertion=null){planInsertion=insertion;showPlanResources=true;showPlanAssistant=false;showPlanReadiness=false;showPlanInspector=false;resourcePane='steps';render();$('app').querySelector('[data-plan-template]')?.focus({preventScroll:true});}

async function proposeCommand(text=commandState().text){
  showPlanAssistant=true;showPlanResources=false;
  const state=commandState(),id=currentId,base=JSON.stringify(draft());state.text=text;state.error='';state.proposal=null;
  try{state.proposal=proposePlanChange(draft(),text);}catch(error){
    if(!runtime.available){state.error=error.message;render();return;}
    state.error='Generating a proposal…';render();
    try{const response=await request('/api/proposals',{method:'POST',body:JSON.stringify({prompt:text,definition:JSON.parse(base),catalogue:planningCatalogue()})});validateDefinition(response.definition);state.proposal={prompt:text,base,definition:response.definition,changes:[response.summary,...describeDefinitionChanges(JSON.parse(base),response.definition)]};state.error='';}
    catch(error){state.error=error.message;}
  }
  if(currentId===id)render();
}
function planningCatalogue(){return {roles:rolePresets,outputs:outputTypes,output_presets:outputPresets};}
function applyPlanProposal(){
  const state=commandState(),proposal=state.proposal;if(!proposal)throw new Error('Propose a change first.');
  if(proposal.base!==JSON.stringify(draft())){state.error='The plan changed after this proposal. Propose the change again against the current plan.';render();return;}
  validateDefinition(proposal.definition);const added=proposal.definition.workflow.find(step=>!draft().workflow.some(before=>before.id===step.id));state.proposal=null;state.error='';updatePlan(structuredClone(proposal.definition),added?.id||selectedStepId);notify('Proposed change applied to the draft.');
}
function createImportedApplication(definition){
  validateDefinition(definition);resetRuns();showPlanResources=false;showPlanInspector=false;const id='draft-'+crypto.randomUUID();applications.push({id,definition:null,revision:null});drafts.set(id,structuredClone(definition));currentId=id;selectedStepId=null;selectedRunStepId=null;view='plan';buildStep=0;advanced=false;render();notify('Definition imported as a new draft. Save it to keep it.');
}
function reviewImport(){const state=dialogState;if(state?.kind!=='import')throw new Error('Open the definition importer first.');state.parsed=parseImportedDefinition(state.importText);renderEditor();}
function resourceDrag(handle){
  if(handle.dataset.planDrag)return {type:'step',id:handle.dataset.planDrag};if(handle.dataset.planTemplate)return {type:'template',kind:handle.dataset.planTemplate};
  if(handle.dataset.planMember)return {type:'member',id:handle.dataset.planMember};if(handle.dataset.planPreset)return {type:'preset',id:handle.dataset.planPreset};
  if(handle.dataset.planSaved)return {type:'saved',id:handle.dataset.planSaved};if(handle.dataset.planTool)return {type:'tool',id:handle.dataset.planTool};return null;
}
function insertResource(drag,slot){
  if(drag.type==='template')return insertPlanStep(draft(),drag.kind,slot);
  let member;if(drag.type==='member')member=draft().agents.find(member=>member.id===drag.id);
  if(drag.type==='preset'){const role=rolePresets.find(role=>role.id===drag.id);if(role)member={id:role.id,name:role.name,kind:role.kind,role:role.id,instructions:role.instructions,tools:[...(role.tools||[])]};}
  if(drag.type==='saved')member=memberLibrary.find(entry=>entry.id===drag.id)?.member;
  if(!member)throw new Error('That member is unavailable.');return insertMemberStep(draft(),member,slot,{reuse:drag.type==='member'});
}
function addResource(drag){
  if(drag.type==='tool'){const step=draft().workflow.find(item=>item.id===selectedStepId),uses=draft().workflow.filter(item=>item.agent===step?.agent).length;updatePlan(attachPlanTool(draft(),selectedStepId,drag.id),selectedStepId);notify(`Tool requested for the shared member across ${uses} step${uses===1?'':'s'}.`);}
  else{let next=insertResource(drag,draft().workflow.length);const id=next.workflow.at(-1).id;next=placeInsertedStep(next,id,planInsertion||{});planInsertion=null;showPlanResources=false;showPlanInspector=true;updatePlan(next,id);notify('Step added. Choose its work, member and connections.');}
}

function findRecord(id){for(const[collection,items]of Object.entries(preview?.state||{}))if(Array.isArray(items)){const index=items.findIndex(item=>item.id===id);if(index>=0)return {collection,index};}return null;}
function inspectRecord(collection,index,follow=false,history=null){
  const item=preview?.state[collection]?.[index];if(!item)throw new Error('Run the application before inspecting findings.');if(history)detailHistory=history;else if(!follow)detailHistory=[];else if(dialogState?.record)detailHistory.push(dialogState.record);dialogState={record:{collection,index}};const d=preview.definition;
  $('record-detail').innerHTML=`${detailHistory.length?'<button class="text-button" data-detail-back>Back to finding</button>':''}<span class="eyebrow">FINDING</span><h2 id="detail-title">${escape(item.title)}</h2><span class="finding-status">${escape(item.status)}</span><p class="finding-body">${escape(item.body).replaceAll('\n','<br>')}</p><span class="detail-label">SUPPORTING SOURCES</span>${item.source_refs.length?`<ul class="source-list">${item.source_refs.map(source=>{const target=findRecord(source);return `<li>${target?`<button class="source-link" data-source="${escape(source)}">${escape(source)} · ${escape(preview.state[target.collection][target.index].title)}</button>`:`${sourceReferenceMarkup(source)}`}</li>`;}).join('')}</ul>`:'<p>No supporting source attached.</p>'}<span class="detail-label">CONTRIBUTORS</span><ol class="contributor-list">${item.contributions.map(c=>`<li><strong>${escape(agentName(d,c.agent))}</strong><span>${escape(d.workflow.find(w=>w.id===c.stage)?.name||c.stage)}</span></li>`).join('')}</ol><details class="technical-events"><summary>Technical provenance</summary><p>${escape(item.id)} · ${escape(collection)}</p>${item.contributions.map(c=>`<p>${escape(c.agent)} · ${escape(c.stage)}<br>Run ${escape(c.run_id)}</p>`).join('')}</details><p class="field-help">Contributor metadata records the actual member and completed step. Source references still require checking.</p>`;if(!$('record-dialog').open)$('record-dialog').showModal();
}
function dialogField(label,key,value,kind='input',options=[]){const id='dialog-'+key;return `<div class="field"><label for="${id}">${escape(label)}</label>${kind==='textarea'?`<textarea id="${id}" data-dialog-field="${key}" rows="3" maxlength="${['importText','catalogueText','sourcesText'].includes(key)?200000:key==='purpose'?2000:4000}">${escape(value)}</textarea>`:kind==='select'?`<select id="${id}" data-dialog-field="${key}">${options.map(([val,text])=>`<option value="${escape(val)}" ${val===value?'selected':''}>${escape(text)}</option>`).join('')}</select>`:`<input id="${id}" data-dialog-field="${key}" value="${escape(value)}" maxlength="100">`}</div>`;}
let catalogueAudience='all';
function drawCatalogue(){
  // Everything the catalogue says about connections comes from this host.
  $('catalogue-content').innerHTML=catalogueMarkup(catalogueTab,escape,catalogueAudience,
    {integrations:integrationCatalogue,connections:integrationConnections,enabled:integrationEnabled,application:currentId});
}
async function openCatalogue(section='applications'){
  catalogueTab=section;
  if(!integrationCatalogue.length)await loadIntegrationConnections();
  drawCatalogue();if(!$('catalogue-dialog').open)$('catalogue-dialog').showModal();
}
function openEditor(kind,index=null,templateId='starter'){
  const d=draft();if(kind==='goal'){dialogState={kind,goal:''};}
  else if(kind==='workspace-catalogue'){dialogState={kind,catalogueText:JSON.stringify(workspaceCatalogue.catalogue,null,2)};}
  else if(kind==='import'){dialogState={kind,importText:'',parsed:null,filename:''};}
  else if(kind==='new'){const template=templates.find(t=>t.id===templateId)?.definition||emptyDefinition();dialogState={kind,template:templateId,name:template.name,purpose:template.description,result:template.workflow.at(-1).task,roles:[],tools:{},toolAgent:template.agents.find(a=>a.kind!=='human')?.id||''};}
  else if(kind==='assign-tool'){if(!toolDescriptors.some(t=>t.id===templateId))throw new Error('Choose a catalog tool.');const agent=d.agents.find(a=>a.kind!=='human');if(!agent)throw new Error('Add an AI member before assigning tools.');dialogState={kind,tool:templateId,agent:agent.id};}
  else if(kind==='add-role'){const preset=rolePresets.find(role=>role.id===templateId);dialogState={kind,preset:preset?.id||'custom',memberKind:preset?.kind||'ai',name:preset?.name||'',responsibility:preset?.instructions||'',output:preset?.kind==='human'?'__new_decision':d.state.find(s=>s.type==='records')?.key||d.state[0].key};}
  else if(kind==='add-output'){const preset=outputPresets.find(p=>p.id===templateId),output=outputTypes.find(type=>type.id===(preset?.kind||templateId))||outputTypes[1];dialogState={kind,name:preset?.name||output.name,outputKind:output.id,layout:output.layout,agent:d.agents[0].id,recordFields:structuredClone(preset?.fields||[]),task:preset?.task||'Prepare this output using the request and shared findings.'};}
  else if(kind==='remove-role')dialogState={kind,index,reassign:d.agents.find(a=>a.id!==d.agents[index].id)?.id||''};else if(kind==='remove-output')dialogState={kind,index};renderEditor();$('editor-dialog').showModal();
}
function changeDialogField(key,value){
  const s=dialogState;if(!s)return;s[key]=value;
  if(key==='importText'){s.parsed=null;const submit=$('editor-content').querySelector('button[type=submit]');if(submit)submit.disabled=true;return;}
  if(key==='template'){
    const next=templates.find(t=>t.id===value)?.definition||emptyDefinition();s.name=next.name;s.purpose=next.description;s.result=next.workflow.at(-1).task;s.roles=[];s.tools={};s.toolAgent=next.agents.find(a=>a.kind!=='human')?.id||'';renderEditor();
  }else if(key==='preset'){
    const role=rolePresets.find(role=>role.id===value);if(role){s.name=role.name;s.responsibility=role.instructions;s.memberKind=role.kind;s.output=role.kind==='human'?'__new_decision':draft().state.find(item=>item.type==='records')?.key||draft().state[0].key;}renderEditor();
  }else if(key==='toolAgent'){renderEditor();}else if(key==='memberKind'){if(value==='human')s.output=draft().state.find(item=>item.kind==='decision')?.key||'__new_decision';renderEditor();}
  else if(key==='outputKind'){const output=outputTypes.find(type=>type.id===value);s.layout=output.layout;s.name=output.name;renderEditor();}
}
function renderEditor(){
  const editorDisclosures=[...$('editor-content').querySelectorAll('details[data-editor-disclosure]')].map(el=>({key:el.dataset.editorDisclosure,open:el.open}));
  const s=dialogState,d=draft();let body,title,submit;
  if(s.proposing){title='Choosing your team';submit='Proposing…';body=proposalProgressMarkup(s);}
  else if(s.kind==='goal-questions'){title='A few things worth clarifying';submit=s.asking?'Asking…':'Continue';body=goalQuestionsMarkup(s);}
  else if(s.kind==='goal'){title='What do you want to achieve?';submit='Suggest a team';body=`${dialogField('Your goal','goal',s.goal,'textarea')}${goalRefineMarkup(s)}<button type="button" class="text-button" data-manual-goal>Use manual starter suggestions</button><p class="field-help">Example: Compare three suppliers and recommend one.</p><p class="field-help">${runtime.available?'AI generates a proposal for you to review.':'Choose a manual starter; AI is unavailable on this host.'}</p>`;}
  else if(s.kind==='new'&&s.goalBased){title='Review your suggested application';submit='Create and open canvas';body=goalReviewMarkup(s);}
  else if(s.kind==='proposed-new'){title='Review proposed application';submit='Create this draft';body=`<p>${escape(s.summary)}</p><p>${escape(s.definition.name)} · ${s.definition.agents.length} members · ${s.definition.workflow.length} steps</p><details><summary>Proposed definition</summary><pre class="code">${escape(JSON.stringify(s.definition,null,2))}</pre></details><p>No application has been saved or run. Apply this proposal to create an editable draft.</p>`;}
  else if(s.kind==='source-settings'){title='Source settings';submit='Save sources';body=`<p>Authorize document folders or repositories on this host. Only agents with file or Git tools use them. Paths are local to the backend computer.</p>${dialogField('Authorized sources (JSON)','sourcesText',s.sourcesText,'textarea')}<p>Example: {"documents":{"name":"Documents","path":"/absolute/folder","applications":["${escape(currentId)}"]}}</p>${dialogField('Default source ID for this application (optional)','source',s.source)}`;}
  else if(s.kind==='workspace-catalogue'){title='Custom workspace catalog';submit='Save catalog';body=`<p>Create or edit roles, application starters and output presets. Remove an entry to delete it. Bundled defaults remain available.</p><button type="button" class="button" data-catalogue-add-current>Use current application as a starter</button><button type="button" class="button" data-catalogue-export>Export catalog</button><div class="field"><label for="catalogue-import">Import catalog JSON</label><input id="catalogue-import" type="file" accept=".json,application/json" data-catalogue-import></div>${dialogField('Catalog JSON','catalogueText',s.catalogueText,'textarea')}<p class="field-help">Starters use {id, name, definition}; roles use member definitions; outputs use {id, name, kind, task}. Changes affect future applications; saved applications keep their definitions.</p>`;}
  else if(s.kind==='import'){title='Import an application definition';submit='Create imported draft';body=`<p class="dialog-copy">Import an exported application.json. Review it before creating a new draft.</p><div class="field"><label for="definition-file">Choose a JSON file</label><input id="definition-file" type="file" accept=".json,application/json" data-import-file></div>${s.filename?`<p class="field-help">${escape(s.filename)}</p>`:''}${dialogField('Or paste the definition','importText',s.importText,'textarea')}<button type="button" class="button" data-import-review>Validate and review</button>${s.parsed?`<div class="import-preview"><span class="eyebrow">VALID DEFINITION</span><h3>${escape(s.parsed.name)}</h3><p>${escape(s.parsed.description)}</p><p>${s.parsed.agents.length} members · ${s.parsed.workflow.length} steps · ${s.parsed.views.length} outputs</p><p>${s.parsed.agents.map(member=>escape(member.name)).join(' · ')}</p></div>`:''}`;}
  else if(s.kind==='new'){title='Configure a catalog application';submit='Create application';const template=applicationTypes.find(app=>app.id===s.template);body=`${dialogField('Starting application type','template',s.template,'select',[...applicationTypes.map(app=>[app.id,app.name]),['blank','Custom application']])}${template?`<details class="template-summary quiet-details"><summary>Included in this template</summary><p>${escape(template.example)}</p><strong>Complete default team</strong><p>${template.members.map(member=>escape(member.name)+(rolePresets.find(role=>role.id===member.preset)?.kind==='human'?' (Human)':'')).join(' · ')}</p><strong>Outputs</strong><p>${template.outputs.map(output=>escape(output.name)).join(' · ')}</p><span>${template.tools.length} suggested tools · every role and plan step is editable</span></details>`:''}${dialogField('Application name','name',s.name)}${dialogField('What should it do?','purpose',s.purpose,'textarea')}<details class="quiet-details"><summary>Expected result</summary>${dialogField('What should the final result include?','result',s.result,'textarea')}</details><button type="button" class="button" data-suggest-from-purpose>Suggest options from this goal</button>${creationCatalogMarkup(s)}`;}
  else if(s.kind==='assign-tool'){const tool=toolDescriptors.find(t=>t.id===s.tool);title='Add '+tool.name;submit='Add tool to member';body=`<p>${escape(tool.description)}</p><span class="permission-tag">${escape(tool.permission)}</span><p class="field-help">${tool.available?'Available on this host':'Not available on this host'} · Choose which files and accounts it can use when connecting it.</p>${dialogField('Assign to AI member','agent',s.agent,'select',d.agents.filter(a=>a.kind!=='human').map(a=>[a.id,a.name]))}${['write','send'].includes(tool.permission)?'<p class="field-help">Changes to files and outgoing messages need your approval.</p>':''}`;}
  else if(s.kind==='add-role'){title='Add a team member';submit='Add member';body=`${dialogField('Ready-made role','preset',s.preset,'select',[['custom','Custom member'],...rolePresets.map(role=>[role.id,role.name])])}${dialogField('Member type','memberKind',s.memberKind,'select',[['ai','AI member'],['human','Human member']])}${dialogField('Member name','name',s.name)}${dialogField('Responsibility','responsibility',s.responsibility,'textarea')}${dialogField('Contributes to','output',s.output,'select',[...d.state.map(item=>[item.key,item.label]),...(s.memberKind==='human'?[['__new_decision','Create a human review decision']]:[])])}<p class="field-help">Adds a plan step for this member. ${s.memberKind==='human'?'The step waits for explicit human input.':'Choose its access separately.'}</p>`;}
  else if(s.kind==='add-output'){const output=outputTypes.find(type=>type.id===s.outputKind);title='Add an output';submit='Add output';body=`${dialogField('Output type','outputKind',s.outputKind,'select',outputTypes.map(type=>[type.id,type.name]))}<p class="field-help">${escape(output.description)}</p>${dialogField('Output name','name',s.name)}${dialogField('Presentation','layout',s.layout,'select',outputLayouts(output.type))}${dialogField('Prepared by','agent',s.agent,'select',d.agents.map(a=>[a.id,a.name+(a.kind==='human'?' · Human':'')]))}${dialogField('What work produces this output?','task',s.task,'textarea')}`;}
  else if(s.kind==='remove-role'){title='Remove '+d.agents[s.index].name+'?';submit='Remove member';const assigned=d.workflow.filter(w=>w.agent===d.agents[s.index].id);body=assigned.length?`<p class="dialog-copy">${assigned.length} ${assigned.length===1?'step needs':'steps need'} a new owner.</p>${dialogField('Who takes over this work?','reassign',s.reassign,'select',d.agents.filter(a=>a.id!==d.agents[s.index].id).map(a=>[a.id,a.name]))}`:'<p class="dialog-copy">This member has no assigned work.</p>';}
  else{const view=d.views[s.index],affected=d.views.filter(v=>v.collection===view.collection).length===1?d.workflow.filter(w=>w.output===view.collection):[];title='Remove '+view.title+'?';submit='Remove output';body=`<p class="dialog-copy">${affected.length?`This also removes ${affected.length} work ${affected.length===1?'step':'steps'}: ${escape(affected.map(w=>w.name).join(', '))}.`:'This removes the result view.'} Changes take effect when you save the application.</p>`;}
  $('editor-content').innerHTML=`<span class="eyebrow">${s.kind==='new'?'NEW APPLICATION':'APPLICATION SETUP'}</span><h2 id="editor-title">${escape(title)}</h2>${body}<div id="editor-error" class="dialog-error" role="alert"></div><div class="dialog-actions"><button type="button" class="button" data-dialog-cancel>Cancel</button><button type="submit" ${s.kind==='import'&&!s.parsed?'disabled':''} class="button ${s.kind.startsWith('remove')?'danger':'primary'}">${submit}</button></div>`;
  for(const item of editorDisclosures){const element=$('editor-content').querySelector(`[data-editor-disclosure="${CSS.escape(item.key)}"]`);if(element)element.open=item.open;}
}
async function submitEditor(){const s=dialogState;if(s.kind==='source-settings'){const sources=JSON.parse(s.sourcesText);if(s.source&&!sources[s.source])throw new Error('Choose an authorized source ID.');await request('/api/settings/sources',{method:'PUT',body:JSON.stringify({sources})});if(s.source)draft().resources={source:s.source};else delete draft().resources;markDirty();await saveApplication();runtime=await request('/api/runtime');registerHostTools(runtime.tool_descriptors||[]);dialogState=null;$('editor-dialog').close();render();notify('Source settings saved.');return;}if(s.kind==='workspace-catalogue'){const catalogue=validateWorkspaceCatalogue(JSON.parse(s.catalogueText));workspaceCatalogue=await request('/api/catalogue',{method:'PUT',body:JSON.stringify({catalogue,revision:workspaceCatalogue.revision})});installWorkspaceCatalogue(catalogue);$('editor-dialog').close();dialogState=null;render();notify('Custom catalog saved.');return;}if(s.kind==='goal'){await askGoalQuestions(s);return;}if(s.kind==='goal-questions'){await continueGoalQuestions(s);return;}if(s.kind==='proposed-new'){createImportedApplication(s.definition);$('editor-dialog').close();dialogState=null;return;}if(s.kind==='new'&&s.goalBased){const d=composeGoalDefinition(s);createImportedApplication(d);buildStep=4;render();$('editor-dialog').close();dialogState=null;notify('Application created. Review its steps on the canvas, then save it.');return;}if(s.kind==='import'){if(!s.parsed)throw new Error('Validate and review the definition first.');createImportedApplication(s.parsed);}else if(s.kind==='new'){if(!s.name.trim()||!s.purpose.trim()||!s.result.trim())throw new Error('Give the application a name, purpose and expected result.');createApplication(s.template,{name:s.name,purpose:s.purpose,result:s.result,roles:s.roles,tools:s.tools});}else if(s.kind==='assign-tool')assignCatalogTool(s.tool,s.agent);else if(s.kind==='add-role')addRole({...s,kind:s.memberKind});else if(s.kind==='add-output')addOutput({...s,kind:s.outputKind});else if(s.kind==='remove-role')removeRole(s.index,s.reassign);else if(s.kind==='remove-output')removeOutput(s.index);$('editor-dialog').close();dialogState=null;}
function updatePlan(next,focusId=null){const before=draft();if(!next.canvas&&before?.workflow.some(step=>next.workflow.some(item=>item.id===step.id))){let positions;try{positions=canvasPositions(before);}catch{positions={};}next.canvas={positions:{...canvasPositions(next),...Object.fromEntries(Object.entries(positions).filter(([id])=>next.workflow.some(step=>step.id===id)))}};}drafts.set(currentId,next);if(focusId)selectedStepId=focusId;markDirty();render();if(focusId&&!showPlanInspector){const handle=$('app').querySelector(`[data-plan-node="${CSS.escape(focusId)}"] .step-summary`);handle?.focus({preventScroll:true});handle?.scrollIntoView?.({block:'nearest',inline:'nearest'});}}
// Opening a step's settings. Both ways in land here: the step's own button (a
// click, or Enter on it) and a double-click anywhere on the card.
function editPlanStep(id){
  showPlanInspector=true;showPlanResources=false;showPlanAssistant=false;showPlanReadiness=false;selectedStepId=id;render();
  if(!integrationCatalogue.length)loadIntegrationConnections().then(()=>{if(showPlanInspector&&selectedStepId===id)render();});
  const inspector=$('app').querySelector('.workbench-inspector');
  if(inspector){inspector.scrollTop=0;inspector.querySelector('input[data-path]')?.focus({preventScroll:true});}
}
function movePlanStep(id,direction){
  const d=draft(),index=d.workflow.findIndex(step=>step.id===id);if(index<0)return;
  const slot=direction==='earlier'?index-1:index+2;if(slot<0||slot>d.workflow.length)return;updatePlan(reorderPlan(d,id,slot),id);notify('Step moved '+direction+'.');
}
function clearPlanDrag(){dragState=null;pointerDrag=null;$('app').classList.remove('plan-dragging');for(const element of $('app').querySelectorAll('.over,.drag-source,.tool-drop-over,.canvas-drop-over'))element.classList.remove('over','drag-source','tool-drop-over','canvas-drop-over');}
// Where a drop landed, in the canvas's own coordinates rather than a slot index.
const canvasScale=()=>canvasZoom??1;
function canvasDropPoint(clientX,clientY){const flow=$('app').querySelector('[data-canvas-drop]'),rect=flow?.getBoundingClientRect();return rect?{x:(clientX-rect.left)/canvasScale(),y:(clientY-rect.top)/canvasScale()}:null;}
function finishCanvasDrop(drag,clientX,clientY){
  if(drag.type==='step')return false;
  const point=canvasDropPoint(clientX,clientY);if(!point)return false;
  const next=insertResource(drag,draft().workflow.length),id=next.workflow.at(-1).id;
  updatePlan(moveCanvasStep(next,id,point.x-canvasNodeWidth/2,point.y-60),id);
  notify('Step added where you dropped it.');return true;
}
function dragAutoScroll(clientY){const canvas=$('app').querySelector('.plan-canvas');if(canvas&&canvas.scrollHeight>canvas.clientHeight){const rect=canvas.getBoundingClientRect();if(clientY<rect.top+60)canvas.scrollTop-=16;else if(clientY>rect.bottom-60)canvas.scrollTop+=16;}else if(clientY<80)window.scrollBy(0,-14);else if(clientY>window.innerHeight-80)window.scrollBy(0,14);}
let skipPlanClickUntil=0;
$('app').addEventListener('input',event=>{if(event.target.dataset.path)updateField(event.target.dataset.path,event.target.value);if(event.target.dataset.requestField){const mode=event.target.dataset.inputMode;requestInputs[mode][currentId]??={};requestInputs[mode][currentId][event.target.dataset.requestField]=event.target.value;}if(event.target.dataset.request)requests[event.target.dataset.request]=event.target.value;if(event.target.dataset.humanInput)humanDraft[event.target.dataset.humanInput]=event.target.value;if(event.target.dataset.humanField){humanDraft.fields??={};humanDraft.fields[event.target.dataset.humanField]=event.target.value;}if(event.target.hasAttribute('data-assistant-input'))commandState().text=event.target.value;if(event.target.dataset.requirementAnswer!==undefined){const d=draft();if(d.requirements?.[event.target.dataset.requirementAnswer]){d.requirements[event.target.dataset.requirementAnswer].answer=event.target.value;markDirty();}}});
$('run-form').addEventListener('input',event=>{
  if(event.target.dataset.request)requests[event.target.dataset.request]=event.target.value;
  if(event.target.dataset.requestField){requestInputs.live[currentId]??={};requestInputs.live[currentId][event.target.dataset.requestField]=event.target.value;}
});
$('run-form').addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button||saving||liveBusy)return;
  if(button.hasAttribute('data-preflight-settings')){$('run-dialog').close();view='setup';buildStep=5;advanced=true;render();return;}
  if(button.dataset.preflightEdit){$('run-dialog').close();preview=null;editPlanStep(button.dataset.preflightEdit);return;}
  if(button.dataset.preflightFix===undefined)return;
  const issue=preflightIssues(current().definition)[Number(button.dataset.preflightFix)];if(!issue)return;
  if(dirty()){notify('Save or resolve your unsaved edits before applying this fix.');return;}
  if(!issue.fix)return;
  if(!confirm(`Save this change as a new application version?\n\n${issue.fix.preview}\n\n${issue.fix.kind==='optional'?'Required non-empty values will no longer be enforced for this field.':'This increases the allowed work budget; it does not approve any tools.'}`))return;
  try{drafts.set(currentId,resolvePreflightIssue(current().definition,issue));await saveApplication();}
  catch(error){notify(error.message);}
  $('run-configuration').innerHTML=studioLiveMarkup(true);
});
$('run-form').addEventListener('change',event=>{
  if(event.target.hasAttribute('data-preflight-review')){preflightReviewed=event.target.checked?preflightVersion():null;$('run-configuration').innerHTML=studioLiveMarkup(true);}
  if(event.target.hasAttribute('data-source-resource')){requestResources[currentId]=event.target.value;$('run-configuration').innerHTML=studioLiveMarkup(true);}
});
$('run-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(event.submitter?.disabled||liveBusy)return;
  const button=$('run-form').querySelector('[data-action="start-live"]');
  if(button.disabled)return;
  button.disabled=true;
  await liveAction('start-live');
});
$('close-run').addEventListener('click',()=>$('run-dialog').close());
$('app').addEventListener('change',event=>{try{if(event.target.hasAttribute('data-activity-filter')){activityFilter=event.target.value;render();return;}if(event.target.hasAttribute('data-source-resource')){requestResources[currentId]=event.target.value;render();return;}if(event.target.dataset.stepParent){const {stepParent:from,stepChild:to}=event.target.dataset;updatePlan(event.target.checked?connectPlanSteps(draft(),from,to):disconnectPlanSteps(draft(),from,to),to);return;}if(event.target.dataset.humanField){humanDraft.fields??={};humanDraft.fields[event.target.dataset.humanField]=event.target.value;}if(event.target.dataset.tool){const role=draft().agents[Number(event.target.dataset.index)],tool=event.target.dataset.tool;if(role.kind==='human')throw new Error('Human members do not use agent tools.');if(event.target.checked&&!role.tools.includes(tool))role.tools.push(tool);else if(!event.target.checked)role.tools=role.tools.filter(t=>t!==tool);markDirty();render();}if(event.target.dataset.format!==undefined)setOutputFormat(Number(event.target.dataset.format),event.target.value);if(event.target.dataset.outputKind!==undefined)setOutputKind(Number(event.target.dataset.outputKind),event.target.value);}catch(error){if(event.target.dataset.stepParent)render();notify(error.message);}});
$('app').addEventListener('click',async event=>{
  const button=event.target.closest('button');if(!button||button.disabled||saving||Date.now()<skipPlanClickUntil)return;
  // A menu item closes its menu, then does exactly what the control it stands for does.
  if(button.closest('[data-canvas-menu]'))closeMenu({restoreFocus:false});
  // A command that acts inside the step panel opens it, so its result is visible.
  if(button.dataset.menuReveal==='panel'&&!showPlanInspector){showPlanInspector=true;render();}if(button.hasAttribute('data-apply-schema')){try{const index=Number(button.dataset.applySchema),fields=JSON.parse($('schema-'+index).value);validateRecordFields(fields);draft().state[index].fields=fields;markDirty();render();notify('Record fields applied to the draft.');}catch(error){notify(error.message);}return;}
  try{if(button.dataset.select){chooseApplication(button.dataset.select);return;}if(button.dataset.removeRequirement!==undefined){const d=draft();d.requirements?.splice(Number(button.dataset.removeRequirement),1);if(d.requirements&&!d.requirements.length)delete d.requirements;markDirty();render();return;}if(button.dataset.step!==undefined){buildStep=Number(button.dataset.step);advanced=false;render();return;}if(button.hasAttribute('data-open-plan')){buildStep=4;advanced=false;render();return;}if(button.dataset.newTemplate){openEditor('new',null,button.dataset.newTemplate);return;}if(button.dataset.downloadOutput){downloadResult(button.dataset.downloadOutput);return;}if(button.dataset.record){inspectRecord(button.dataset.record,Number(button.dataset.index));return;}
    if(button.dataset.connectTarget){if(!connectSource)throw new Error('Choose a step to connect from.');updatePlan(connectPlanSteps(draft(),connectSource,button.dataset.connectTarget),button.dataset.connectTarget);connectSource=null;render();return;}if(button.dataset.disconnectFrom){updatePlan(disconnectPlanSteps(draft(),button.dataset.disconnectFrom,button.dataset.disconnectTo));return;}if(button.dataset.resourcePane){resourcePane=button.dataset.resourcePane;render();return;}
    if(button.dataset.menuReverse){const [from,to]=button.dataset.menuReverse.split('|');updatePlan(connectPlanSteps(disconnectPlanSteps(draft(),from,to),to,from));selectedEdge=null;notify('Connection reversed.');return;}
    if(button.dataset.menuAddStep&&menuPoint){openStepPicker({at:menuPoint.canvas||null});return;}
    if(button.dataset.menuPanel){const key=button.dataset.menuPanel;editPlanStep(selectedStepId);
      const section=$('app').querySelector(`[data-disclosure="${CSS.escape(key)}"]`);if(section){section.open=true;section.scrollIntoView?.({block:'nearest'});}
      return;}
    if(button.dataset.menuDisconnectAll){const id=button.dataset.menuDisconnectAll,parents=planConnections(draft()).filter(edge=>edge.to===id);
      if(!parents.length)throw new Error('This step depends on nothing.');
      updatePlan(parents.reduce((next,edge)=>disconnectPlanSteps(next,edge.from,edge.to),draft()),id);return;}
    if(button.dataset.menuCentre){centrePlanStep(button.dataset.menuCentre);return;}
    if(button.dataset.menuShowStep){const id=button.dataset.menuShowStep;selectedStepId=id;if(showPlanInspector)render();centrePlanStep(id);return;}
    if(button.dataset.menuCentreEdge){const[from,to]=button.dataset.menuCentreEdge.split('|');centreEdge(from,to);return;}
    if(button.dataset.menuReconnect){connectSource=button.dataset.menuReconnect;selectedEdge=null;render();notify('Choose the step this connection should end at.');return;}
    if(button.dataset.canvasEdgeMenu){const[from,to]=button.dataset.canvasEdgeMenu.split('|'),box=button.getBoundingClientRect();
      openMenu({kind:'plan-edge',id:`${from}|${to}`,from,to},{x:box.left,y:box.bottom+6,canvas:canvasDropPoint(box.left,box.bottom)||{x:0,y:0}});return;}
    if(button.dataset.menuStepActivity){activityExpanded=true;activityFilter=button.dataset.menuStepActivity;render();return;}
    if(button.dataset.menuCopyOutput){const step=preview?.definition.workflow.find(item=>item.id===button.dataset.menuCopyOutput),value=preview?.state?.[step?.output];
      const text=typeof value==='string'?value:JSON.stringify(value,null,2);
      if(navigator.clipboard?.writeText)navigator.clipboard.writeText(text).then(()=>notify('Step output copied.')).catch(()=>notify('This browser refused the clipboard.'));
      else notify('This browser cannot copy to the clipboard.');
      render();return;}
    if(button.dataset.canvasPort){connectSource=button.dataset.canvasPort;selectedEdge=null;render();return;}
    if(button.dataset.canvasDisconnect){const from=button.dataset.canvasDisconnect,to=button.dataset.canvasDisconnectTo;selectedEdge=null;updatePlan(disconnectPlanSteps(draft(),from,to));notify('Connection removed.');return;}
    if(button.dataset.canvasZoom){const mode=button.dataset.canvasZoom;if(mode==='fit'){fitCanvas('plan');return;}canvasZoom=mode==='reset'?null:Math.max(.4,Math.min(1.6,(canvasZoom??1)+(mode==='in'?.1:-.1)));applyCanvasZoom();return;}
    if(button.dataset.runZoom){suspendRunFollow();const mode=button.dataset.runZoom;if(mode==='fit'){runCanvasZoom=null;applyRunZoom();const area=$('app').querySelector('[data-canvas-mode="run"]');area.scrollLeft=0;area.scrollTop=0;return;}const flow=$('app').querySelector('[data-run-canvas]');runCanvasZoom=mode==='reset'?1:Math.max(.4,Math.min(1.6,Number(flow?.style.zoom||1)+(mode==='in'?.1:-.1)));applyRunZoom();return;}
    if(button.dataset.planSelect){editPlanStep(button.dataset.planSelect);return;}
    if(button.dataset.action==='toggle-activity'){activityExpanded=!activityExpanded;render();return;}
  if(button.dataset.action==='close-run-inspector'){closeRunInspector();return;}
  if(button.dataset.action==='follow-active'){followActive=!followActive;lastFollowedStep=null;render();return;}
  if(button.dataset.action==='view-results'||button.dataset.action==='view-review'||button.dataset.action==='view-run-info'){const selector=button.dataset.action==='view-results'?'[data-disclosure="live-results"]':button.dataset.action==='view-review'?'.human-checkpoint':'[data-disclosure="live-request-details"]';const panel=$('app').querySelector(selector);if(panel){if(panel.tagName==='DETAILS')panel.open=true;panel.scrollIntoView({block:'start'});panel.querySelector('summary,input,textarea,button')?.focus({preventScroll:true});}return;}
  if(button.dataset.runStep){suspendRunFollow();selectedRunStepId=button.dataset.runStep;const fromActivity=!!button.closest('.activity-panel');render();if(fromActivity)focusRunNode(selectedRunStepId);$('app').querySelector('[data-action="close-run-inspector"]')?.focus({preventScroll:true});return;}
    if(button.dataset.assistantExample){proposeCommand(button.dataset.assistantExample);return;}
    if(button.dataset.planTemplate||button.dataset.planMember||button.dataset.planPreset||button.dataset.planSaved||button.dataset.planTool){addResource(resourceDrag(button));return;}
    if(button.dataset.addAfter){openStepPicker({after:button.dataset.addAfter});return;}
    if(button.dataset.addParallel){openStepPicker({parallel:button.dataset.addParallel});return;}
    if(button.dataset.insertFrom){openStepPicker({after:button.dataset.insertFrom,before:button.dataset.insertTo});return;}
    if(button.dataset.planMove){movePlanStep(button.dataset.planMove,button.dataset.direction);return;}
    if(button.dataset.planDuplicate){const index=draft().workflow.findIndex(step=>step.id===button.dataset.planDuplicate),next=duplicatePlanStep(draft(),button.dataset.planDuplicate);updatePlan(next,next.workflow[index+1].id);notify('Step duplicated.');return;}
    if(button.dataset.planRemove){updatePlan(removePlanStep(draft(),button.dataset.planRemove));notify('Step removed.');return;}
    const action=button.dataset.action,index=Number(button.dataset.index);
    if(action==='suggest-plan-change'){showPlanAssistant=!showPlanAssistant;showPlanResources=false;showPlanInspector=false;showPlanReadiness=false;render();$('plan-command')?.focus({preventScroll:true});return;}
    if(action==='close-plan-assistant'){showPlanAssistant=false;render();return;}
    if(action==='toggle-readiness'){showPlanReadiness=!showPlanReadiness;showPlanAssistant=false;showPlanResources=false;showPlanInspector=false;render();return;}
    if(action==='close-plan-resources'){showPlanResources=false;planInsertion=null;render();return;}
    if(action==='new-step-member'){createStepMember();return;}
    if(action==='edit-shared-member'){const member=draft().workflow.find(step=>step.id===selectedStepId)?.agent;const details=$('app').querySelector(`[data-disclosure="member-${CSS.escape(member)}-responsibility"]`);if(details){details.open=true;details.querySelector('input')?.focus({preventScroll:true});}return;}
    if(action==='manage-integrations'){await manageIntegrations();return;}
    if(action==='toggle-rail'){railOpen=!railOpen;render();return;}
    if(action==='show-history'){renderHistory();return;}
    if(action==='run'){openRunConfiguration();return;}
    // The plan tools are in the same bar as the run controller. Using one while a
    // finished run is open leaves the run first: the canvas shows that run's
    // snapshot, so editing underneath it would change something not on screen.
    if(['toggle-plan-resources','suggest-plan-change','tidy-canvas'].includes(action)&&preview?.live)preview=null;
    if(action==='back-to-plan'){showView('plan');return;}
    if(action==='sign-out'){try{await request(account.login.logout,{method:'POST',body:'{}'});}finally{location.reload();}return;}
    if(action==='set-up'){showView('setup');return;}

    if(['start-live','cancel-live','pause-live','submit-live-review','resume-live'].includes(action)){await liveAction(action);return;}if(action==='source-settings'){const result=await request('/api/settings/sources');dialogState={kind:'source-settings',sourcesText:JSON.stringify(result.sources,null,2),source:draft().resources?.source||''};renderEditor();$('editor-dialog').showModal();return;}if(action==='edit-catalogue'){openEditor('workspace-catalogue');return;}if(action==='cancel-connect'){connectSource=null;render();}else if(action==='canvas-fullscreen'){canvasFullscreen();}else if(action==='tidy-canvas'){updatePlan(tidyCanvas(draft()));scheduleLayoutSave();}else if(action==='toggle-applications'){showAllApplications=!showAllApplications;render();}else if(action==='add-input'){const d=draft();d.inputs??=[];if(d.inputs.length>=12)throw new Error('Use at most 12 fields.');let n=1;while(d.inputs.some(f=>f.id==='field_'+n))n++;d.inputs.push({id:'field_'+n,label:'New field',type:'text',required:false});markDirty();render();}else if(action==='remove-input'){draft().inputs.splice(index,1);markDirty();render();}else if(action==='new')openEditor('goal');else if(action==='import')openEditor('import');else if(action==='save-member')await saveCurrentMember();else if(action==='reload-members')await reloadMembers();else if(action==='isolate-member')isolateSelectedMember();else if(action==='propose-plan')proposeCommand();else if(action==='apply-proposal')applyPlanProposal();else if(action==='discard-proposal'){commandState().proposal=null;commandState().error='';render();}else if(action==='open-catalogue')openCatalogue(button.dataset.pane||'applications');else if(action==='toggle-plan-resources'){if(showPlanResources){showPlanResources=false;render();}else openStepPicker();}else if(action==='close-plan-inspector'){showPlanInspector=false;render();$('app').querySelector(`[data-plan-select="${CSS.escape(selectedStepId)}"]`)?.focus({preventScroll:true});}else if(['add-role','add-output','remove-role','remove-output'].includes(action))openEditor(action,index);else if(action==='delete-application')await deleteApplication();else if(action==='save')await saveApplication();else if(action==='export')exportDefinition();else if(action==='toggle-advanced'){advanced=!advanced;render();}else if(action)mutate(action,index);
  }catch(error){notify(error.message);}
});
$('app').addEventListener('click',event=>{
  const edge=event.target.closest('[data-canvas-edge]');if(!edge||saving)return;
  selectedEdge=selectedEdge===edge.dataset.canvasEdge?null:edge.dataset.canvasEdge;render();
});
$('app').addEventListener('keydown',event=>{if(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(event.key)&&!event.target.closest('input,textarea,select,[contenteditable="true"]')){const id=event.target.closest('[data-plan-node]')?.dataset.planNode;if(id){event.preventDefault();const pos=canvasPositions(draft())[id];updatePlan(moveCanvasStep(draft(),id,pos.x+(event.key==='ArrowLeft'?-20:event.key==='ArrowRight'?20:0),pos.y+(event.key==='ArrowUp'?-20:event.key==='ArrowDown'?20:0)),id);scheduleLayoutSave();return;}}
});
$('app').addEventListener('dragstart',event=>{
  const handle=event.target.closest('[data-plan-drag],[data-plan-template],[data-plan-member],[data-plan-preset],[data-plan-saved],[data-plan-tool]');if(!handle||saving)return;
  dragState=resourceDrag(handle);
  event.dataTransfer.effectAllowed=dragState.type==='step'?'move':'copy';event.dataTransfer.setData('text/plain',JSON.stringify(dragState));$('app').classList.add('plan-dragging');handle.closest('[data-plan-node]')?.classList.add('drag-source');
});
function finishToolDrop(target){if(dragState?.type!=='tool')return false;const step=target.closest('[data-plan-tool-target]');if(!step)throw new Error('Drop a tool directly on an AI step or its member settings.');const id=dragState.id;clearPlanDrag();updatePlan(attachPlanTool(draft(),step.dataset.planToolTarget,id),step.dataset.planToolTarget);notify('Tool requested for this step’s member.');return true;}
$('app').addEventListener('dragover',event=>{if(!dragState)return;if(dragState.type==='tool'){const target=event.target.closest('[data-plan-tool-target]');if(!target)return;event.preventDefault();event.dataTransfer.dropEffect='copy';for(const element of $('app').querySelectorAll('.tool-drop-over'))element.classList.remove('tool-drop-over');target.classList.add('tool-drop-over');return;}if(!event.target.closest('[data-canvas-drop]'))return;event.preventDefault();event.dataTransfer.dropEffect='copy';$('app').querySelector('[data-canvas-drop]')?.classList.add('canvas-drop-over');dragAutoScroll(event.clientY);});
$('app').addEventListener('drop',event=>{if(!dragState)return;event.preventDefault();try{if(dragState.type==='tool'){finishToolDrop(event.target);return;}if(event.target.closest('[data-canvas-drop]'))finishCanvasDrop(dragState,event.clientX,event.clientY);clearPlanDrag();}catch(error){clearPlanDrag();notify(error.message);}});
$('app').addEventListener('dragend',clearPlanDrag);
$('app').addEventListener('pointerdown',event=>{
  if(event.target.closest('[data-plan-node]')||event.pointerType==='mouse'||event.button!==0||saving)return;const handle=event.target.closest('[data-plan-template],[data-plan-member],[data-plan-preset],[data-plan-saved],[data-plan-tool]');if(!handle)return;
  pointerDrag={pointerId:event.pointerId,startX:event.clientX,startY:event.clientY,moved:false,handle,drag:resourceDrag(handle)};handle.setPointerCapture(event.pointerId);
});
$('app').addEventListener('pointermove',event=>{
  if(!pointerDrag||pointerDrag.pointerId!==event.pointerId)return;
  if(!pointerDrag.moved&&Math.hypot(event.clientX-pointerDrag.startX,event.clientY-pointerDrag.startY)>6){pointerDrag.moved=true;dragState=pointerDrag.drag;$('app').classList.add('plan-dragging');pointerDrag.handle.closest('[data-plan-node]')?.classList.add('drag-source');}
  if(pointerDrag.moved){event.preventDefault();if(dragState?.type==='tool'){const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-plan-tool-target]');for(const element of $('app').querySelectorAll('.tool-drop-over'))element.classList.remove('tool-drop-over');target?.classList.add('tool-drop-over');}else $('app').querySelector('[data-canvas-drop]')?.classList.add('canvas-drop-over');dragAutoScroll(event.clientY);}
});
$('app').addEventListener('pointerup',event=>{
  if(!pointerDrag||pointerDrag.pointerId!==event.pointerId)return;const moved=pointerDrag.moved;pointerDrag.handle.releasePointerCapture(event.pointerId);
  if(moved){skipPlanClickUntil=Date.now()+400;try{const target=document.elementFromPoint(event.clientX,event.clientY);if(dragState?.type==='tool'){if(!target)throw new Error('Drop a tool on an AI step.');finishToolDrop(target);}else if(target?.closest('[data-canvas-drop]')&&finishCanvasDrop(dragState,event.clientX,event.clientY))clearPlanDrag();else clearPlanDrag();}catch(error){clearPlanDrag();notify(error.message);}}else clearPlanDrag();
});
$('app').addEventListener('pointercancel',clearPlanDrag);
$('editor-form').addEventListener('input',event=>{if(event.target.dataset.dialogField&&event.target.tagName!=='SELECT')changeDialogField(event.target.dataset.dialogField,event.target.value);
  // The editor dialog is a sibling of #app, so what is typed into it arrives here
  // and nowhere else. An answer belongs to the question it was typed under.
  if(event.target.dataset.goalAnswer!==undefined&&dialogState?.requirements?.[event.target.dataset.goalAnswer])dialogState.requirements[event.target.dataset.goalAnswer].answer=event.target.value;});
$('editor-form').addEventListener('change',event=>{if(dialogState?.goalBased&&event.target.dataset.goalRole){const id=event.target.dataset.goalRole;dialogState.roles=event.target.checked?[...new Set([...dialogState.roles,id])]:dialogState.roles.filter(r=>r!==id);if(!event.target.checked)delete dialogState.tools[id];renderEditor();return;}if(dialogState?.goalBased&&event.target.dataset.goalTool){const id=event.target.dataset.goalTool,target=dialogState.toolAgent,list=dialogState.tools[target]||[];dialogState.tools[target]=event.target.checked?[...new Set([...list,id])]:list.filter(t=>t!==id);return;}if(dialogState?.goalBased&&event.target.dataset.goalOutput){const id=event.target.dataset.goalOutput;dialogState.outputs=event.target.checked?[...new Set([...dialogState.outputs,id])]:dialogState.outputs.filter(o=>o!==id);return;}if(dialogState?.kind==='new'&&event.target.dataset.creationRole){const id=event.target.dataset.creationRole;dialogState.roles=event.target.checked?[...new Set([...dialogState.roles,id])]:dialogState.roles.filter(r=>r!==id);if(!event.target.checked)delete dialogState.tools['catalog_'+id];renderEditor();}if(dialogState?.kind==='new'&&event.target.dataset.creationTool){const target=dialogState.toolAgent,id=event.target.dataset.creationTool;const list=dialogState.tools[target]||[];dialogState.tools[target]=event.target.checked?[...new Set([...list,id])]:list.filter(t=>t!==id);}if(event.target.dataset.dialogField&&event.target.tagName==='SELECT')changeDialogField(event.target.dataset.dialogField,event.target.value);});
$('editor-form').addEventListener('click',async event=>{const button=event.target.closest('button');if(button?.hasAttribute('data-refine-goal')){try{await refineGoal();}catch(error){$('editor-error').textContent=error.message;}return;}
  if(button?.hasAttribute('data-stop-questions')){const s=dialogState;if(s?.kind==='goal-questions'&&!s.asking){s.asking=true;renderEditor();await suggestGoalSetup(s.goal,goalAnswers(s));}return;}
  if(button?.hasAttribute('data-manual-goal')){try{await suggestManualGoalSetup(dialogState.goal);}catch(error){$('editor-error').textContent=error.message;}return;}if(button?.hasAttribute('data-suggest-from-purpose')){try{const name=dialogState.name;await suggestGoalSetup(dialogState.purpose);if(dialogState.kind==='new')dialogState.name=name;renderEditor();}catch(error){$('editor-error').textContent=error.message;}return;}if(button?.hasAttribute('data-rethink-goal')){dialogState={kind:'goal',goal:dialogState.goal||''};renderEditor();return;}if(button?.hasAttribute('data-import-review')){try{reviewImport();}catch(error){$('editor-error').textContent=error.message;}}if(button?.hasAttribute('data-dialog-cancel')){$('editor-dialog').close();dialogState=null;}});
$('catalogue-content').addEventListener('click',event=>{const button=event.target.closest('button');if(!button||saving)return;if(button.dataset.catalogueAudience){catalogueAudience=button.dataset.catalogueAudience;openCatalogue('applications');return;}if(button.dataset.catalogueTab){openCatalogue(button.dataset.catalogueTab);return;}if(button.dataset.catalogueConnect){$('catalogue-dialog').close();manageIntegrations('',button.dataset.catalogueConnect);return;}
  if(button.dataset.catalogueConnector){addConnector(button.dataset.catalogueConnector);$('catalogue-dialog').close();buildStep=2;render();return;}if(button.dataset.catalogueCreate){$('catalogue-dialog').close();openEditor('new',null,button.dataset.catalogueCreate);}else if(button.dataset.catalogueTool){$('catalogue-dialog').close();try{openEditor('assign-tool',null,button.dataset.catalogueTool);}catch(error){notify(error.message);}}else if(button.dataset.catalogueBlank){$('catalogue-dialog').close();openEditor('new',null,'blank');}else if(button.dataset.catalogueRole){$('catalogue-dialog').close();openEditor('add-role',null,button.dataset.catalogueRole);}else if(button.dataset.catalogueOutput){$('catalogue-dialog').close();openEditor('add-output',null,button.dataset.catalogueOutput);}});
$('close-catalogue').addEventListener('click',()=>$('catalogue-dialog').close());
function renderHistory(){$('history-content').innerHTML=historyMarkup();if(!$('history-dialog').open)$('history-dialog').showModal();}
$('close-history').addEventListener('click',()=>$('history-dialog').close());
$('history-content').addEventListener('click',event=>{const button=event.target.closest('[data-run-open]');if(!button)return;$('history-dialog').close();openLiveRun(button.dataset.runOpen).catch(error=>notify(error.message));});
$('editor-form').addEventListener('change',async event=>{if(!event.target.hasAttribute('data-import-file'))return;const file=event.target.files?.[0],state=dialogState;if(!file||state?.kind!=='import')return;state.parsed=null;const submit=$('editor-content').querySelector('button[type=submit]');if(submit)submit.disabled=true;try{if(file.size>200000)throw new Error('Use an application JSON file below 200 KB.');const text=await file.text();if(dialogState!==state)return;state.filename=file.name;state.importText=text;state.parsed=parseImportedDefinition(text);renderEditor();}catch(error){if(dialogState===state){state.parsed=null;renderEditor();$('editor-error').textContent=error.message;}}});
$('editor-form').addEventListener('submit',async event=>{event.preventDefault();try{await submitEditor();}catch(error){$('editor-error').textContent=error.message;}});
$('close-editor').addEventListener('click',()=>{$('editor-dialog').close();dialogState=null;});
$('close-detail').addEventListener('click',()=>$('record-dialog').close());
$('record-detail').addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.dataset.source){const target=findRecord(button.dataset.source);if(target)inspectRecord(target.collection,target.index,true);}if(button.hasAttribute('data-detail-back')){const target=detailHistory.pop();if(target)inspectRecord(target.collection,target.index,false,[...detailHistory]);}});
window.addEventListener('pagehide',stopLivePolling);
window.addEventListener('beforeunload',event=>{if(applications.some(a=>!a.revision||JSON.stringify(drafts.get(a.id))!==JSON.stringify(a.definition))){event.preventDefault();event.returnValue='';}});
function registerTools(){
  if(!document.modelContext?.registerTool)return;const lifecycle=new AbortController();const register=tool=>{try{Promise.resolve(document.modelContext.registerTool(tool,{signal:lifecycle.signal})).catch(()=>{});}catch{}};
  register({name:'read_application_definition',title:'Read application definition',description:'Read the selected application draft and saved version. Does not execute agents.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:true,untrustedContentHint:true},execute(input){if(!input||Object.keys(input).length)throw new Error('No arguments expected.');return {id:currentId,revision:current().revision,definition:structuredClone(draft()),unsaved:dirty()};}});
  register({name:'propose_application_definition',title:'Propose an application change',description:'Present a validated definition change in the plan assistant for the user to review and apply. Does not change the draft, save or execute it.',inputSchema:{type:'object',properties:{definition:{type:'object'},summary:{type:'string',minLength:1,maxLength:1000}},required:['definition','summary'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute(input){if(!input||Object.keys(input).some(key=>!['definition','summary'].includes(key))||typeof input.summary!=='string'||!input.summary.trim()||input.summary.length>1000)throw new Error('Provide a definition and bounded change summary.');if(saving)throw new Error('A save is in progress.');validateDefinition(input.definition);const state=commandState();state.error='';state.proposal={prompt:input.summary,base:JSON.stringify(draft()),definition:structuredClone(input.definition),changes:describeDefinitionChanges(draft(),input.definition)};view='plan';buildStep=0;advanced=false;pauseActiveRun();preview=null;render();return {id:currentId,proposed:true,requires_user_apply:true};}});
  register({name:'stage_application_definition',title:'Stage application definition',description:'Replace the selected draft with a validated definition. Does not save or execute it.',inputSchema:{type:'object',properties:{definition:{type:'object'}},required:['definition'],additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute(input){if(!input||Object.keys(input).some(k=>k!=='definition'))throw new Error('Provide a definition.');if(saving)throw new Error('A save is in progress.');validateDefinition(input.definition);drafts.set(currentId,structuredClone(input.definition));showView('plan');return {id:currentId,staged:true};}});
  register({name:'save_application_definition',title:'Save application definition',description:'Save the selected application draft. Does not execute agents.',inputSchema:{type:'object',properties:{},additionalProperties:false},annotations:{readOnlyHint:false,untrustedContentHint:true},execute(input){if(!input||Object.keys(input).length)throw new Error('No arguments expected.');if(saving)throw new Error('A save is in progress.');return saveApplication();}});
  window.addEventListener('pagehide',()=>lifecycle.abort(),{once:true});
}
await load();if(currentId)registerTools();


function inputDesignerMarkup(d){return `<section class="panel"><div class="section-head"><h3>Request form</h3><button class="button" data-action="add-input">Add field</button></div>${(d.inputs||[]).map((f,i)=>`<div class="input-designer">${field('Label',`inputs.${i}.label`,f.label)}<details><summary>Advanced</summary>${field('Field key',`inputs.${i}.id`,f.id)}</details><label>Type<select data-path="inputs.${i}.type">${['text','textarea','number','date'].map(t=>`<option value="${t}" ${t===f.type?'selected':''}>${({text:'Short answer',textarea:'Long answer',number:'Number',date:'Date'})[t]}</option>`).join('')}</select></label><label>Required<select data-path="inputs.${i}.required"><option value="false" ${!f.required?'selected':''}>Optional</option><option value="true" ${f.required?'selected':''}>Required</option></select></label><button class="text-button" data-action="remove-input" data-index="${i}">Remove</button></div>`).join('')}</section>`;}
function generatedRequestMarkup(d,mode){return `<div class="generated-inputs">${(d.inputs||[]).map(f=>{const value=(requestInputs[mode][currentId]||{}||{})[f.id]??'';const attrs=`data-request-field="${escape(f.id)}" data-input-mode="${mode}" id="${mode}-input-${escape(f.id)}" ${f.required?'required':''}`;return `<div class="field"><label for="${mode}-input-${escape(f.id)}">${escape(f.label)}${f.required?' · required':''}</label>${f.type==='textarea'?`<textarea ${attrs} maxlength="2000">${escape(value)}</textarea>`:`<input ${attrs} type="${f.type}" ${f.type==='number'?'step="any"':''} maxlength="2000" value="${escape(value)}">`}</div>`;}).join('')}</div>`;}
// The limits a run will stop at, said once, so the form can stay closed.
function limitsStated(d){
  const set=[['tool_calls','Tool uses'],['wall_clock_seconds','Seconds'],['tokens','AI usage'],['cost_usd','Cost']];
  const stated=set.filter(([key])=>d.run_budget?.[key]!==undefined&&d.run_budget[key]!==null&&d.run_budget[key]!=='').map(([key,label])=>`${label.toLowerCase()} ${d.run_budget[key]}`);
  return stated.length?stated.join(' · '):'no limit';
}
function controlsMarkup(d){const warnings=d.budget_warnings||{};return `<div class="step-heading"><div><span class="eyebrow">06 · CONTROLS</span><h2>Limits and approvals</h2></div></div><section class="panel"><details class="quiet-details" data-disclosure="budget-limits"><summary>Spending and time limits · ${limitsStated(d)}</summary><p class="field-help">Blank means no limit. Limits apply to local AI runs; warnings use recorded usage.</p><div class="budget-grid"><strong>Resource</strong><strong>Warn me</strong><strong>Stop</strong>${[['tool_calls','Tool uses'],['wall_clock_seconds','Seconds'],['tokens','AI usage (text units)'],['cost_usd','Cost · USD']].map(([key,label])=>`<span>${label}</span>${runtime.features?.budget_warnings?field('Warning',`budget_warnings.${key}`,warnings[key]??'','number'):'<span>Unavailable on this host</span>'}${field('Stop work',`run_budget.${key}`,d.run_budget[key]??'','number')}`).join('')}</div></details></section><section class="panel"><h3>Workload limits</h3><p class="field-help">Blank means unlimited. Parallel steps control concurrency only. Tool/report characters do not limit accumulated shared state. Existing saved limits remain in effect until you change and save them.</p><div class="input-grid">${[['max_tool_steps','Agent action steps'],['max_notes','Retained private notes'],['max_note_chars','Characters per note'],['max_tool_output_chars','Characters per tool response / delegated report'],['fan_out_limit','Parallel steps']].map(([key,label])=>field(label,`run_config.${key}`,d.run_config[key]??'','number')).join('')}${[['max_delegations','Total AI step starts and delegations'],['max_per_agent','Starts per AI member'],['max_depth','Agent nesting depth']].map(([key,label])=>field(label,`run_budget.${key}`,d.run_budget[key]??'','number')).join('')}</div></section><section class="panel"><h3>Approvals</h3><p>Changes to files and outgoing messages need approval when tools are connected.</p></section><section class="panel"><h3>Check the results</h3><p>Run the saved application to inspect actual results and their sources.</p></section>`;}

function composeCatalogApplication(templateId,roles=[],tools={}){
  const template=templates.find(t=>t.id===templateId),d=template?structuredClone(template.definition):emptyDefinition();
  for(const id of roles){const role=rolePresets.find(r=>r.id===id);if(!role)throw new Error('Choose a catalog role.');const agentId='catalog_'+id;if(d.agents.some(a=>a.id===agentId))throw new Error('Select each extra role once.');let output=d.state.find(o=>o.type==='records')?.key||d.state[0].key;
    if(role.kind==='human'){output=nextIdentifier('decision',d.state,'key');d.state.push({key:output,label:'Approval decision',kind:'decision',type:'records',merge:'upsert',statuses:['proposed','approved','rejected','changes_requested']});d.views.push({id:nextIdentifier('view',d.views),title:'Approval decision',collection:output,layout:'cards'});}
    d.agents.push({id:agentId,name:role.name,kind:role.kind,role:id,instructions:role.instructions,tools:[]});const step={id:nextIdentifier('stage',d.workflow),name:role.name+' contribution',agent:agentId,task:role.instructions,output};if(role.kind==='human')d.workflow.push(step);else insertWork(d,step);
  }
  for(const [id,selected] of Object.entries(tools)){if(!selected.length)continue;const agent=d.agents.find(a=>a.id===id);if(!agent||agent.kind==='human')throw new Error('Assign tools to an existing AI member.');agent.tools=[...new Set([...agent.tools,...selected])];}
  return validateDefinition(d);
}
function creationCatalogMarkup(s){const d=composeCatalogApplication(s.template,s.roles,s.tools),ai=d.agents.filter(a=>a.kind!=='human');if(!ai.some(a=>a.id===s.toolAgent))s.toolAgent=ai[0]?.id||'';const selected=d.agents.find(a=>a.id===s.toolAgent)?.tools||[];const base=templates.find(t=>t.id===s.template)?.definition||emptyDefinition();
  return `<details class="creation-catalog" data-editor-disclosure="creation-team"><summary>Ready-made roles · add to your team</summary><div class="catalogue-cards">${rolePresets.map(role=>`<label class="access-option"><input type="checkbox" data-creation-role="${role.id}" ${s.roles.includes(role.id)?'checked':''}><span><strong>${escape(role.name)} · ${role.kind==='human'?'Human':'AI'}</strong><small>${escape(role.description)}</small></span></label>`).join('')}</div><p class="field-help">${d.agents.length} members · ${d.workflow.length} plan steps. Each choice adds a team member and a step you can edit.</p></details><details class="creation-catalog" data-editor-disclosure="creation-tools"><summary>Tools · choose what each member can use</summary>${dialogField('Assign tools to','toolAgent',s.toolAgent,'select',ai.map(a=>[a.id,a.name]))}<div class="catalogue-cards">${toolDescriptors.map(tool=>{const included=base.agents.find(a=>a.id===s.toolAgent)?.tools.includes(tool.id);return `<label class="access-option"><input type="checkbox" data-creation-tool="${tool.id}" ${selected.includes(tool.id)?'checked':''} ${included?'disabled':''}><span><strong>${escape(tool.name)}</strong><small>${escape(tool.description)}</small><small>${included?'Included in starter · editable under Access':tool.origin==='bundled'?'Connection needed':'Connection needed'}${['write','send'].includes(tool.permission)?' · approval required':''}</small></span></label>`;}).join('')}</div></details>`;
}
function assignCatalogTool(toolId,agentId){const d=structuredClone(draft()),agent=d.agents.find(a=>a.id===agentId);if(!toolDescriptors.some(t=>t.id===toolId)||!agent||agent.kind==='human')throw new Error('Choose a catalog tool and AI member.');if(!agent.tools.includes(toolId))agent.tools.push(toolId);validateDefinition(d);drafts.set(currentId,d);markDirty();render();notify('Tool assigned. Save the application to keep it.');}

function userApplications(){return applications.filter(a=>!templates.some(t=>t.id===a.id)||a.revision>1);}
function sidebarApplications(){const own=userApplications();if(showAllApplications)return own;const visible=own.slice(0,3),selected=own.find(a=>a.id===currentId);if(selected&&!visible.includes(selected)){if(visible.length===3)visible[2]=selected;else visible.push(selected);}return visible;}
$('app').addEventListener('pointerdown',event=>{const area=event.target.closest('.canvas-viewport');if(!area||event.target.closest('button,input,textarea,select,[data-plan-node],[data-canvas-dock]')||event.button!==0)return;if(area.dataset.canvasMode==='run')suspendRunFollow();canvasPan={area,x:event.clientX,y:event.clientY,left:area.scrollLeft,top:area.scrollTop,pointer:event.pointerId};area.setPointerCapture?.(event.pointerId);area.classList.add('panning');});
$('app').addEventListener('pointermove',event=>{if(!canvasPan||canvasPan.pointer!==event.pointerId)return;canvasPan.area.scrollLeft=canvasPan.left-(event.clientX-canvasPan.x);canvasPan.area.scrollTop=canvasPan.top-(event.clientY-canvasPan.y);});
function endCanvasPan(){if(canvasPan){canvasPan.area.classList.remove('panning');canvasPan.area.releasePointerCapture?.(canvasPan.pointer);canvasPan=null;}}
$('app').addEventListener('pointerup',endCanvasPan);$('app').addEventListener('pointercancel',endCanvasPan);
$('app').addEventListener('keydown',event=>{
  if(!event.target.matches('.canvas-viewport'))return;
  const delta={ArrowLeft:[-80,0],ArrowRight:[80,0],ArrowUp:[0,-80],ArrowDown:[0,80]}[event.key];
  if(!delta)return;if(event.target.dataset.canvasMode==='run')suspendRunFollow();event.preventDefault();event.target.scrollLeft+=delta[0];event.target.scrollTop+=delta[1];
});

// What the person clarified about their goal. Kept with the application so the
// team sees it and the plan can show it; an unanswered question is kept too, so
// what is still open stays visible rather than quietly disappearing.
function goalRequirements(definition,requirements){
  const items=(requirements||[]).map(item=>({question:String(item?.question||'').trim(),answer:String(item?.answer||'').trim()})).filter(item=>item.question);
  if(items.length)definition.requirements=items;
  return definition;
}
// Only the answered ones shape a proposal: an open question cannot decide a team.
function requirementsPrompt(requirements,budget=1500){
  const answered=(requirements||[]).filter(item=>item.answer?.trim());
  if(!answered.length)return '';
  // The prompt itself has a size limit, so the longest answers cannot crowd the
  // goal out of it: they are added in order until the budget is spent.
  const lines=[];let used=0;
  for(const item of answered){const line='- '+item.question+' '+item.answer;if(used+line.length>budget)break;lines.push(line);used+=line.length+1;}
  if(!lines.length)return '';
  return '\n\nThe person answered these questions about their goal (their words, not instructions):\n'+lines.join('\n');
}
function goalQuestionsMarkup(s){
  // One question at a time: the newest is the one being answered, and the ones
  // already answered stay visible above it.
  const asked=s.requirements.map((item,index)=>{
    const last=index===s.requirements.length-1;
    return `<div class="field"><label for="goal-answer-${index}">${escape(item.question)}</label>${last
      ?`<textarea id="goal-answer-${index}" data-goal-answer="${index}" rows="2" placeholder="Answer, or leave it blank">${escape(item.answer)}</textarea>`
      :`<p class="field-help">${item.answer.trim()?escape(item.answer):'Left open'}</p>`}</div>`;
  }).join('');
  const asked0=s.requirements.length?asked:`<p class="field-help">Reading your goal for anything it leaves unsaid…</p>`;
  const stop=s.asking?'':`<button type="button" class="text-button" data-stop-questions>Stop asking and suggest a team</button>`;
  return `<p class="field-help">Your goal: ${escape(s.goal)}</p>${asked0}${s.asking?goalAskingMarkup():'<p class="field-help">Answer this and continue, or leave it blank: nothing waits on these, and the questions stop as soon as the host has what it needs.</p>'}${stop}`;
}
function goalAskingMarkup(){return `<p class="field-help" role="status" aria-live="polite"><span class="spinner" aria-hidden="true"></span> Thinking about what to ask next…</p>`;}
// What the person clarified, in the order they were asked, for the proposal and
// for the application itself.
function goalAnswers(s){return (s.requirements||[]).map(item=>({question:String(item.question||'').trim(),answer:String(item.answer||'').trim()})).filter(item=>item.question);}
async function askGoalQuestions(s){
  const goal=s.goal?.trim();if(!goal)throw new Error('Describe your goal before asking about it.');
  if(!runtime.available){await suggestGoalSetup(goal,[]);return;}
  const state={kind:'goal-questions',goal,requirements:[],asking:true};dialogState=state;renderEditor();
  await nextGoalQuestion(state);
}
// The next question, or none: the host stops asking as soon as the answers settle
// what it would ask, and stops at its own limit whatever the answers say.
async function nextGoalQuestion(s){
  const state=s;let question='';
  // Asking is optional, so a host that cannot answer never stands between the
  // person and their application: the proposal is made from the goal alone.
  try{question=((await request('/api/goals/questions',{method:'POST',body:JSON.stringify({goal:state.goal,requirements:goalAnswers(state)})})).questions||[])[0]||'';}catch{question='';}
  if(dialogState!==state)return;
  state.asking=false;
  if(!question){await suggestGoalSetup(state.goal,goalAnswers(state));return;}
  state.requirements.push({question,answer:''});renderEditor();
}
async function continueGoalQuestions(s){
  if(s.asking)return;
  const index=s.requirements.length-1;
  s.asking=true;renderEditor();
  await nextGoalQuestion(s);
}
// The first call decides only who and what: the plan is the second call's job.
function teamPrompt(goal,requirements){
  return 'Choose the team and the results for this goal, and nothing else: the members who do the work, '
    +'and the results the person needs. Give each member the fewest tools that fit, and add one step per member '
    +'so the definition is complete. Do not design the full workflow; a later request does that. The goal is: '
    +goal+requirementsPrompt(requirements);
}
// The second call designs the plan around the team already chosen.
function planPrompt(goal,requirements,team){
  const keep=team?`Use exactly this team and these results: ${team.agents.map(agent=>agent.name).join(', ')}`
    +`; results: ${team.state.map(item=>item.label).join(', ')}. `:'';
  return 'Design a complete application for this goal: the members, the steps, the connections that order those '
    +'steps, the instructions and the shared state. '+keep+'Give every step a connection, so the plan reads as a '
    +'graph rather than a list of unconnected steps. The goal is: '+goal+requirementsPrompt(requirements);
}
function proposalProgressMarkup(s){
  const seconds=Math.max(0,Math.round((Date.now()-(s.proposedAt||Date.now()))/1000));
  const answered=(s.requirements||[]).filter(item=>item.answer?.trim()).length;
  const waiting=s.stage==='team';
  const chosen=s.team?`<div class="chosen-team"><p class="field-help">Chosen so far:</p><ul>${s.team.agents.map(agent=>`<li><strong>${escape(agent.name)}</strong> · ${escape(agent.kind==='human'?'human step':'AI member')} · ${agent.tools.length} tool${agent.tools.length===1?'':'s'}</li>`).join('')}${s.team.state.map(item=>`<li><strong>${escape(item.label)}</strong> · result</li>`).join('')}</ul></div>`:'';
  return `<div class="proposal-progress" role="status" aria-live="polite"><span class="spinner" aria-hidden="true"></span><p><strong>${waiting?'Choosing the team…':'Writing the plan around them…'}</strong></p><p class="field-help">A model call to ${escape(runtime.provider||'this host’s backend')} is reading your goal${answered?` and ${answered} answer${answered===1?'':'s'}`:''}${waiting?'. The members appear here as soon as they are chosen.':', then designing the steps and the shared state.'} ${seconds}s so far; this usually takes under a minute, and longer when the backend is busy.</p>${chosen}</div>`;
}
let proposalTicker=null;
function stopProposalTicker(){if(proposalTicker){clearInterval(proposalTicker);proposalTicker=null;}}
// The elapsed time is the only progress a single model call can honestly report,
// so it is what moves while the call is out.
function startProposalTicker(s){
  stopProposalTicker();
  proposalTicker=setInterval(()=>{if(dialogState!==s||!s.proposing){stopProposalTicker();return;}renderEditor();},1000);
}
// One control, one job: the sparkle rewords the goal where it stands. No second
// screen and no confirmation, because the wording is the only thing it changes.
function goalRefineMarkup(s){
  if(!runtime.available)return '';
  const label=s.refining?'Rewording…':'Reword this goal with AI';
  return `<p class="field-help">${s.refining?`<span class="spinner" aria-hidden="true"></span> ${label}`:`<button type="button" class="icon-button" data-refine-goal title="${label}" aria-label="${label}">${uiIcon('sparkle')}</button> ${label}`}</p>`;
}
async function refineGoal(){
  const s=dialogState;if(!s||s.kind!=='goal'||s.refining)return;
  if(!s.goal?.trim())throw new Error('Describe your goal before asking for a rewording.');
  if(!runtime.available)throw new Error('This host has no AI provider configured.');
  s.refining=true;renderEditor();
  try{
    const result=await request('/api/goals/refine',{method:'POST',body:JSON.stringify({goal:s.goal.trim()})});
    if(dialogState!==s)return;
    s.goal=result.goal;
    notify(result.summary||'Goal reworded.');
  }finally{s.refining=false;renderEditor();}
}
async function goalIntegrationSuggestions(goal){
  try{const result=await request('/api/integrations/recommend',{method:'POST',body:JSON.stringify({goal,application_id:currentId})});return result.recommendations||[];}catch{return [];}
}
function goalIntegrationMarkup(goal,suggestions){
  if(!suggestions.length)return '<p>No connected service is suggested for this goal yet.</p>';
  return suggestions.map(item=>`<p><strong>${escape(item.name)}</strong> · ${item.status==='connected'?`${item.connections.length} authorized ${item.connections.length===1?'connection':'connections'} you can assign`:'Nothing authorized for this application yet'} · ${escape(item.reason)}</p>`).join('');
}
async function suggestGoalSetup(goal,requirements=[]){
  if(!runtime.available){await suggestManualGoalSetup(goal,requirements);return;}
  const state=dialogState;if(!goal?.trim())throw new Error('Describe your goal.');
  const suggestions=await goalIntegrationSuggestions(goal);
  const waiting={...state,proposing:true,proposedAt:Date.now(),requirements,team:null,stage:'team'};
  dialogState=waiting;renderEditor();startProposalTicker(waiting);
  const proposal=async prompt=>request('/api/proposals',{method:'POST',body:JSON.stringify({prompt,definition:emptyDefinition(),catalogue:planningCatalogue()})});
  let response;
  try{
    // Who does the work is a small, quick decision, and the person can be shown
    // it while the plan around it is still being written. A host that will not
    // answer this first call is not asked twice: the plan is proposed without it.
    let team=null;
    try{
      team=(await proposal(teamPrompt(goal,requirements))).definition;
      goalRequirements(team,requirements);validateDefinition(team);
      if(dialogState===waiting){waiting.team=team;waiting.stage='plan';renderEditor();}
    }catch{team=null;waiting.stage='plan';}
    // The chosen team is named in the request rather than handed over as the
    // base definition: a base is preserved, and a team-only definition preserves
    // the wrong plan.
    response=await proposal(planPrompt(goal,requirements,team));
  }finally{
    stopProposalTicker();
    if(dialogState===waiting)waiting.proposing=false;
  }
  goalRequirements(response.definition,requirements);validateDefinition(response.definition);if(dialogState!==waiting)return;
  dialogState={kind:'proposed-new',goal,definition:response.definition,summary:response.summary,integrationSuggestions:suggestions};renderEditor();
}
async function suggestManualGoalSetup(goal,requirements=[]){const recommended=recommendGoal(goal);dialogState={kind:'new',goalBased:true,requirements,integrationSuggestions:await goalIntegrationSuggestions(goal),goal:goal.trim(),name:applicationTypes.find(a=>a.id===recommended.template).name,purpose:goal.trim(),result:'Produce the selected results that answer this goal, with supporting sources and limitations.',...recommended,toolAgent:recommended.roles.find(id=>rolePresets.find(r=>r.id===id).kind!=='human')||''};renderEditor();}
function goalOutputName(id){return manualPlanning.outputLabels[id]||outputTypes.find(output=>output.id===id)?.name||id;}
function goalReviewMarkup(s){const ai=s.roles.filter(id=>rolePresets.find(r=>r.id===id)?.kind!=='human');if(!ai.includes(s.toolAgent))s.toolAgent=ai[0]||'';return `<section class="goal-summary"><p>${escape(s.goal)}</p><button class="text-button" type="button" data-rethink-goal>Change goal</button></section>${dialogField('Name','name',s.name)}<div class="suggested-summary"><div><span class="field-label">Team</span><p>${s.roles.map(id=>escape(rolePresets.find(r=>r.id===id)?.name||id)).join(', ')}</p></div><div><span class="field-label">Results</span><p>${s.outputs.map(goalOutputName).map(escape).join(', ')}</p></div></div><p class="field-help">${escape(s.reason)}</p><details class="quiet-details" data-editor-disclosure="goal-connections"><summary>Connections this goal may need</summary>${goalIntegrationMarkup(s.goal,s.integrationSuggestions||[])}</details><details class="quiet-details" data-editor-disclosure="goal-team"><summary>Change team</summary><div class="catalogue-cards">${rolePresets.map(role=>`<label class="access-option"><input type="checkbox" data-goal-role="${role.id}" ${s.roles.includes(role.id)?'checked':''}><span><strong>${escape(role.name)}</strong><small>${escape(role.description)}</small></span></label>`).join('')}</div></details><details class="quiet-details" data-editor-disclosure="goal-results"><summary>Change results</summary><div class="catalogue-cards">${outputTypes.map(o=>`<label class="access-option"><input type="checkbox" data-goal-output="${o.id}" ${s.outputs.includes(o.id)?'checked':''}><span>${escape(goalOutputName(o.id))}</span></label>`).join('')}</div></details><details class="quiet-details" data-editor-disclosure="goal-tools"><summary>Choose tools</summary>${ai.length?`${dialogField('Team member','toolAgent',s.toolAgent,'select',ai.map(id=>[id,rolePresets.find(r=>r.id===id).name]))}<div class="catalogue-cards">${toolDescriptors.map(t=>`<label class="access-option"><input type="checkbox" data-goal-tool="${t.id}" ${(s.tools[s.toolAgent]||[]).includes(t.id)?'checked':''}><span><strong>${escape(t.name)}</strong><small>${escape(t.description)}</small></span></label>`).join('')}</div>`:'<p>This needs an AI member.</p>'}</details>`;}

function composeGoalDefinition(s){
  if(!s.name?.trim()||s.name.length>100)throw new Error('Enter an application name of up to 100 characters.');if(!s.roles.length||!s.roles.some(id=>rolePresets.find(r=>r.id===id)?.kind!=='human'))throw new Error('Choose at least one AI team member.');if(!s.outputs.length)throw new Error('Choose at least one result.');
  const starter=applicationTypes.find(a=>a.id===s.template);
  if(starter?.featured&&JSON.stringify([...new Set(starter.members.map(m=>m.preset))].sort())===JSON.stringify([...s.roles].sort())&&JSON.stringify([...new Set(starter.outputs.map(o=>o.kind))].sort())===JSON.stringify([...s.outputs].sort())){const d=structuredClone(templates.find(t=>t.id===s.template).definition);d.name=s.name.trim();d.description=s.goal;for(const a of d.agents){a.instructions+=' Work toward the user goal: '+s.goal;if(a.kind!=='human')a.tools=[...(s.tools[a.role]||[])];}return validateDefinition(goalRequirements(d,s.requirements));}
  const d=emptyDefinition();d.name=s.name.trim();d.description=s.goal;d.catalogue_type=s.template;d.inputs=[{id:'context',label:'Context and source references',type:'textarea',required:false}];d.agents=s.roles.map(id=>{const role=rolePresets.find(r=>r.id===id);if(!role)throw new Error('Choose a ready-made role.');return {id,name:role.name,kind:role.kind,role:id,instructions:role.instructions+' Work toward this goal: '+s.goal,tools:role.kind==='human'?[]:[...(s.tools[id]||[])]};});
  d.state=s.outputs.map(id=>{const o=outputTypes.find(o=>o.id===id);if(!o)throw new Error('Choose a supported result.');return {key:id,label:goalOutputName(id),kind:id,type:o.type,merge:o.type==='text'?'replace':'upsert',statuses:[...o.statuses]};});d.views=d.state.map(o=>({id:o.key+'_view',title:o.label,collection:o.key,layout:outputTypes.find(t=>t.id===o.kind).layout}));d.workflow=[];
  const ai=d.agents.filter(a=>a.kind!=='human'),primary=d.state.find(o=>o.type==='records')||d.state[0];
  for(const a of ai){d.workflow.push({id:'work_'+a.id,name:a.name+' contribution',agent:a.id,output:primary.key,task:a.instructions});}
  for(const o of d.state){const preferred=manualPlanning.outputOwners[o.kind],owner=ai.find(a=>a.role===preferred)||ai.at(-1);d.workflow.push({id:'prepare_'+o.key,name:'Prepare '+o.label.toLowerCase(),agent:owner.id,output:o.key,task:'Produce '+o.label.toLowerCase()+' for this goal: '+s.goal+'. Use earlier findings and include supporting sources and limitations.'});}
  goalRequirements(d,s.requirements);
  for(const a of d.agents.filter(a=>a.kind==='human')){const out=d.state.find(o=>o.kind==='decision')||d.state.at(-1);d.workflow.push({id:'review_'+a.id,name:'Review and approve',agent:a.id,output:out.key,task:'Review the results for this goal: '+s.goal+'. Approve, reject or request changes with a reason.'});}
  return validateDefinition(d);
}

function prunePlanGraph(d){const ids=new Set(d.workflow.map(w=>w.id));if(d.connections!==undefined)d.connections=d.connections.filter(e=>ids.has(e.from)&&ids.has(e.to));if(d.canvas)for(const id of Object.keys(d.canvas.positions))if(!ids.has(id))delete d.canvas.positions[id];}
// Pressing anywhere on a step moves it; its own controls keep their meaning.
$('app').addEventListener('pointerdown',event=>{
  if(event.button!==0||saving)return;
  if(event.target.closest('[data-canvas-port],[data-canvas-disconnect],[data-connect-target],[data-insert-from],input,textarea,select,a'))return;
  const node=event.target.closest('[data-plan-node]');if(!node)return;
  const id=node.dataset.planNode,pos=canvasPositions(draft())[id];
  canvasNodeDrag={id,handle:node,pointer:event.pointerId,x:event.clientX,y:event.clientY,start:pos,next:pos,moved:false,node,zoom:canvasScale()};
  node.setPointerCapture?.(event.pointerId);
  // Focus the step, so the arrow keys work straight after a press or a drag.
  node.querySelector('.step-summary')?.focus?.({preventScroll:true});
});
$('app').addEventListener('pointermove',event=>{const drag=canvasNodeDrag;if(!drag||drag.pointer!==event.pointerId)return;
  if(!drag.moved&&Math.abs(event.clientX-drag.x)<4&&Math.abs(event.clientY-drag.y)<4)return;
  if(!drag.moved){drag.moved=true;drag.node?.classList.add('dragging');}
  event.preventDefault();drag.next={x:Math.round(Math.max(0,Math.min(10000,drag.start.x+(event.clientX-drag.x)/drag.zoom))),y:Math.round(Math.max(0,Math.min(10000,drag.start.y+(event.clientY-drag.y)/drag.zoom)))};if(drag.node){drag.node.style.left=drag.next.x+'px';drag.node.style.top=drag.next.y+'px';}const positions={...canvasPositions(draft()),[drag.id]:drag.next};const edges=$('app').querySelector('[data-canvas-edges]');if(edges)edges.innerHTML=canvasEdgePaths(draft(),positions,escape);});
function finishCanvasNode(commit){const drag=canvasNodeDrag;if(!drag)return;canvasNodeDrag=null;drag.handle.releasePointerCapture?.(drag.pointer);if(commit&&drag.moved&&JSON.stringify(drag.next)!==JSON.stringify(drag.start)){skipPlanClickUntil=Date.now()+400;updatePlan(moveCanvasStep(draft(),drag.id,drag.next.x,drag.next.y),drag.id);scheduleLayoutSave();return;}
  // A stationary press edits; a drag only changes the saved visual position.
  const now=Date.now(),second=commit&&!drag.moved&&lastPlanPress.id===drag.id&&now-lastPlanPress.time<500;
  lastPlanPress=!commit||drag.moved||second?{id:null,time:0}:{id:drag.id,time:now};
  if(commit&&!drag.moved)editPlanStep(drag.id);else render();}
$('app').addEventListener('pointerup',event=>{if(canvasNodeDrag?.pointer===event.pointerId)finishCanvasNode(true);});$('app').addEventListener('pointercancel',event=>{if(canvasNodeDrag?.pointer===event.pointerId)finishCanvasNode(false);});
// Dragging a connection out of a step's port. A press without movement is left
// to the click handler, which starts the keyboard-and-touch flow instead.
function canvasConnectPreview(){
  const edges=$('app').querySelector('[data-canvas-edges]');if(!edges||!canvasConnect)return;
  let path=edges.querySelector('[data-canvas-preview]');
  if(!path){path=document.createElementNS('http://www.w3.org/2000/svg','path');path.setAttribute('data-canvas-preview','');path.setAttribute('class','edge-preview');path.setAttribute('marker-end','url(#flow-arrow)');edges.append(path);}
  const from=canvasPositions(draft())[canvasConnect.from],point=canvasConnect.point;
  path.setAttribute('d',canvasEdgeCurve(from,{x:point.x,y:point.y-canvasNodeHeight/2}));
}
function clearCanvasConnect(){for(const element of $('app').querySelectorAll('.connect-target'))element.classList.remove('connect-target');$('app').querySelector('[data-canvas-preview]')?.remove();}
$('app').addEventListener('pointerdown',event=>{
  const port=event.target.closest('[data-canvas-port]');if(!port||event.button!==0||saving)return;
  canvasConnect={from:port.dataset.canvasPort,pointer:event.pointerId,x:event.clientX,y:event.clientY,moved:false,point:null};
  port.setPointerCapture?.(event.pointerId);event.preventDefault?.();
});
$('app').addEventListener('pointermove',event=>{
  const drag=canvasConnect;if(!drag||drag.pointer!==event.pointerId)return;
  if(!drag.moved&&Math.hypot(event.clientX-drag.x,event.clientY-drag.y)>6)drag.moved=true;
  if(!drag.moved)return;
  event.preventDefault();const rect=$('app').querySelector('[data-canvas-edges]')?.getBoundingClientRect();
  drag.point=rect?{x:(event.clientX-rect.left)/canvasScale(),y:(event.clientY-rect.top)/canvasScale()}:null;
  const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-plan-node]');
  for(const element of $('app').querySelectorAll('.connect-target'))if(element!==target)element.classList.remove('connect-target');
  if(target&&target.dataset.planNode!==drag.from)target.classList.add('connect-target');
  canvasConnectPreview();
});
$('app').addEventListener('pointerup',event=>{
  const drag=canvasConnect;if(!drag||drag.pointer!==event.pointerId)return;
  canvasConnect=null;clearCanvasConnect();
  if(!drag.moved)return;
  skipPlanClickUntil=Date.now()+400;
  const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('[data-plan-node]');
  if(!target||target.dataset.planNode===drag.from)return;
  try{const before=planConnections(draft()).length,next=connectPlanSteps(draft(),drag.from,target.dataset.planNode);updatePlan(next,drag.from);notify(planConnections(next).length>before?'Steps connected.':'Those steps are already connected.');}
  catch(error){render();notify(error.message);}
});
$('app').addEventListener('pointercancel',event=>{if(canvasConnect?.pointer===event.pointerId){canvasConnect=null;clearCanvasConnect();}});

function addConnector(id){const connector=connectorDescriptors.find(c=>c.id===id);if(!connector)throw new Error('Choose a catalog connector.');const d=structuredClone(draft());d.integrations??=[];if(d.integrations.some(c=>c.connector===id)){notify('That connector is already included.');return;}d.integrations.push({connector:id,purpose:connector.description,scope:'',mode:connector.default_mode});validateDefinition(d);drafts.set(currentId,d);notify('Connector setup added. No account is connected.');}
function connectorSetupMarkup(d){return `<section class="panel"><p class="field-help">Connections are configured and checked by this host. Selecting a service alone does not grant access.</p><span class="connection-status">${(d.integrations||[]).length} selected · ${(d.integrations||[]).filter(item=>connectorDescriptors.find(c=>c.id===item.connector)?.available).length} available</span>${d.connector_requirements?.length?`<details><summary>Suggested for this workflow</summary><ul>${d.connector_requirements.map(r=>`<li>${escape(r.purpose)} · ${escape(connectorDescriptors.find(c=>c.id===r.suggested)?.name||r.suggested)}${r.required?'':' · optional'}</li>`).join('')}</ul></details>`:''}${(d.integrations||[]).map((item,i)=>{const c=connectorDescriptors.find(c=>c.id===item.connector);if(!c)return '';return `<details class="connector-row quiet-row" data-disclosure="connector-${i}"><summary><strong>${escape(c.name)}</strong><span class="field-help">${c.available?'Available on this host':'Not connected'}</span></summary><div class="row-editor">${field('Use for',`integrations.${i}.purpose`,item.purpose)}${field('Choose service',`integrations.${i}.connector`,item.connector,'select',connectorDescriptors.filter(other=>other.group===c.group).map(other=>[other.id,other.name]))}${field('Allowed sources or destination',`integrations.${i}.scope`,item.scope)}<p class="field-help">This does not grant access.</p>${field('Allowed actions',`integrations.${i}.mode`,item.mode,'select',connectorModes(c))}<details><summary>Connection setup</summary><p>${c.available?'This adapter supplies '+escape((c.tools||[]).map(capabilityName).join(', '))+'. Assign these tools to the members that need them. Source restrictions and credentials are owned by the host.':'Configure a host adapter for this service. Do not enter passwords or access keys in these fields.'}</p><button class="button" disabled>${c.available?'Configured on host':'Configure on host'}</button></details><button class="icon-button remove" data-remove-connector="${i}" title="Remove this connection from the plan" aria-label="Remove this connection from the plan">${uiIcon('trash')}</button></div></details>`;}).join('')||'<p>No connectors selected. Browse the catalog to add sources and destinations.</p>'}<details class="quiet-details" data-disclosure="start-method"><summary>When to run · ${d.start_mode==='event'?'event':d.start_mode==='schedule'?'schedule':'manually'}</summary>${field('Start method','start_mode',d.start_mode||'manual','select',[['manual','Start manually'],...(runtime.features?.events?[['event','When an event arrives']]:d.start_mode==='event'?[['event','Saved event trigger — unavailable',true]]:[]),...(runtime.features?.schedules?[['schedule','On a schedule']]:d.start_mode==='schedule'?[['schedule','Saved schedule — unavailable',true]]:[])])}<p class="field-help">This host supports manual requests. Saved event or schedule settings require a host that reports those capabilities.</p></details></section>`;}
function connectorModes(c){return c.default_mode==='model'?[['model','Run AI roles']]:c.default_mode==='read'?[['read','Read only']]:[['read','Read only'],['draft','Prepare drafts'],['approved','Take action after approval']];}
$('app').addEventListener('click',event=>{const button=event.target.closest('[data-remove-connector]');if(!button||button.dataset.removeConnector===undefined||saving)return;const d=structuredClone(draft());d.integrations.splice(Number(button.dataset.removeConnector),1);validateDefinition(d);drafts.set(currentId,d);render();});

$('editor-form').addEventListener('click',event=>{const button=event.target.closest('button');if(dialogState?.kind!=='workspace-catalogue'||!button)return;try{if(button.hasAttribute('data-catalogue-add-current')){const value=validateWorkspaceCatalogue(JSON.parse(dialogState.catalogueText));value.starters.push({id:'custom_'+crypto.randomUUID().slice(0,8),name:draft().name,definition:structuredClone(draft())});dialogState.catalogueText=JSON.stringify(value,null,2);renderEditor();}if(button.hasAttribute('data-catalogue-export')){const value=validateWorkspaceCatalogue(JSON.parse(dialogState.catalogueText)),url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='workspace-catalogue.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}}catch(error){$('editor-error').textContent=error.message;}});
$('editor-form').addEventListener('change',async event=>{if(!event.target.hasAttribute('data-catalogue-import'))return;const file=event.target.files?.[0],state=dialogState;if(!file||state?.kind!=='workspace-catalogue')return;try{if(file.size>200000)throw new Error('Use a catalog below 200 KB.');const value=validateWorkspaceCatalogue(JSON.parse(await file.text()));if(state!==dialogState)return;state.catalogueText=JSON.stringify(value,null,2);renderEditor();}catch(error){$('editor-error').textContent=error.message;}});

$('app').addEventListener('wheel',event=>{if(event.target.closest('[data-canvas-mode="run"]'))suspendRunFollow();},{passive:true});
$('app').addEventListener('keydown',event=>{if(event.key==='Escape'&&selectedRunStepId){event.preventDefault();closeRunInspector();}});

$('app').addEventListener('keydown',event=>{
  // The keyboard path to a connection: Enter or Space selects the focused edge,
  // and Delete removes it from either the edge or its handle.
  const connection=event.target.closest?.('[data-canvas-edge-menu],[data-canvas-edge]');
  if(connection&&(event.key==='Delete'||event.key==='Backspace')){
    event.preventDefault();
    const key=connection.dataset.canvasEdgeMenu||connection.dataset.canvasEdge,[from,to]=key.split('|');
    selectedEdge=null;updatePlan(disconnectPlanSteps(draft(),from,to));notify('Connection removed.');return;
  }
  const edge=event.target.closest?.('[data-canvas-edge]');
  if(edge&&(event.key==='Enter'||event.key===' ')){event.preventDefault();selectedEdge=selectedEdge===edge.dataset.canvasEdge?null:edge.dataset.canvasEdge;render();
    $('app').querySelector(`[data-canvas-edge-menu]`)?.focus({preventScroll:true});return;}
  if(event.key==='Escape'&&!preview?.live){showPlanInspector=false;showPlanResources=false;showPlanAssistant=false;showPlanReadiness=false;connectSource=null;render();$('app').querySelector(`[data-plan-select="${CSS.escape(selectedStepId||'')}"]`)?.focus({preventScroll:true});}});

$('app').addEventListener('change',event=>{
  const name=event.target.dataset.connectionTool;if(!name)return;
  const member=draft().agents[Number(event.target.dataset.memberIndex)];if(!member||member.kind==='human')return;
  if(!connectionTools().includes(name)){notify('This connection is not authorized for this application.');return;}
  member.tools=event.target.checked?[...new Set([...member.tools,name])]:member.tools.filter(tool=>tool!==name);markDirty();render();
});
$('record-detail').addEventListener('input',event=>{
  if(!event.target.hasAttribute('data-integration-search'))return;
  for(const card of $('record-detail').querySelectorAll('[data-integration-card]'))card.hidden=!card.dataset.integrationCard.includes(event.target.value.toLowerCase());
});
$('record-detail').addEventListener('submit',async event=>{
  const form=event.target.closest('[data-connection-create]');if(!form)return;event.preventDefault();
  const values=new FormData(form),service=integrationCatalogue.find(item=>item.id===form.dataset.connectionCreate),operations=values.getAll('operations');
  const credentials={};if(values.get('token'))credentials[service.auth==='api_key'?'api_key':service.auth==='google_oauth'?'access_token':'token']=values.get('token');
  if(values.get('email'))credentials.email=values.get('email');if(values.get('api_key'))credentials.api_key=values.get('api_key');
  const config={};if(service.integration)config.instance=service.id;if(values.get('base_url'))config.base_url=values.get('base_url');
  const body={id:values.get('id'),name:values.get('name'),integration:service.integration||service.id,applications:[currentId],operations,scopes:[...new Set(service.operations.filter(op=>operations.includes(op.name)).flatMap(op=>op.scopes))],config,credentials};
  if(event.submitter?.dataset.oauthProvider){
    delete body.credentials;delete body.config;
    try{const result=await request('/api/connections/oauth/begin',{method:'POST',body:JSON.stringify({connection:body,provider:event.submitter.dataset.oauthProvider,redirect_uri:event.submitter.dataset.redirectUri})});
      window.open(result.authorization_url,'_blank','noopener,noreferrer');notify('Authorize in the provider window, then paste the callback URL below.');
    }catch(error){notify(error.message);}return;
  }
  for(const input of form.querySelectorAll('input[type="password"]'))input.value='';
  try{await request('/api/connections',{method:'POST',body:JSON.stringify(body)});await manageIntegrations();render();notify('Connection configured. Assign tools explicitly; health is not yet verified.');}catch(error){await manageIntegrations(error.message);}
});
$('record-detail').addEventListener('click',async event=>{
  const button=event.target.closest('[data-connection-delete],[data-connection-health],[data-connection-revoke]');if(!button)return;
  try{if(button.dataset.connectionRevoke){if(!confirm('Revoke provider access and disconnect locally?'))return;const result=await request('/api/connections/'+encodeURIComponent(button.dataset.connectionRevoke)+'/revoke',{method:'POST',body:'{}'});await manageIntegrations();render();notify(result.provider_revoked?'Provider access revoked':'Disconnected locally; provider does not expose configured revocation.');}
  else if(button.dataset.connectionDelete){if(!confirm('Disconnect and revoke local access? Provider credentials may need separate revocation.'))return;await request('/api/connections/'+encodeURIComponent(button.dataset.connectionDelete),{method:'DELETE'});await manageIntegrations();render();}
  else{const result=await request('/api/connections/'+encodeURIComponent(button.dataset.connectionHealth)+'/health',{method:'POST',body:'{}'});notify(result.verified?'Provider access verified':result.reason);}}
  catch(error){notify(error.message);}
});

$('record-detail').addEventListener('submit',async event=>{
  if(!event.target.matches('[data-oauth-callback]'))return;event.preventDefault();
  const field=event.target.querySelector('input'),value=field.value;field.value='';
  try{const url=new URL(value),state=url.searchParams.get('state'),code=url.searchParams.get('code');
    if(!state||!code)throw new Error('Callback URL must contain state and code.');
    const redirect_uri=url.origin+url.pathname;
    await request('/api/connections/oauth/callback',{method:'POST',body:JSON.stringify({state,code,redirect_uri})});
    await manageIntegrations();render();notify('OAuth connection configured. Assign tools explicitly.');
  }catch(error){notify(error.message);}
});

$('app').addEventListener('click',async event=>{
  const button=event.target.closest('[data-tool-approval]');if(!button||!preview?.runId)return;
  button.disabled=true;
  try{await request(`/api/runs/${preview.runId}/tool-approvals`,{method:'POST',body:JSON.stringify({approval_id:button.dataset.toolApproval,approved:button.dataset.approved==='true'})});pendingToolApprovals=pendingToolApprovals.filter(item=>item.id!==button.dataset.toolApproval);render();}
  catch(error){notify(error.message);button.disabled=false;}
});
