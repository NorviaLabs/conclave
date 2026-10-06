import {initialState,executionOrder,planConnections,mergeRecords} from '/definition.js';

function inputState(definition,stageId,patches){
  const ancestors=new Set(),links=planConnections(definition);
  function visit(id){for(const edge of links)if(edge.to===id&&!ancestors.has(edge.from)){ancestors.add(edge.from);visit(edge.from);}}
  visit(stageId);const state=initialState(definition);
  for(const id of executionOrder(definition)){
    if(!ancestors.has(id)||!patches.has(id))continue;
    if(patches.get(id)===undefined)return null;
    const step=definition.workflow.find(step=>step.id===id),collection=definition.state.find(item=>item.key===step.output),patch=patches.get(id);
    state[step.output]=collection.type==='text'?patch:mergeRecords(state[step.output],patch,collection.merge);
  }
  return state;
}

export function projectLiveRun(record){
  const d=record.request.definition,patches=new Map(),completedSteps=[],trace=[],textOrigins={};
  let state=initialState(d),activeStep=null,version=0;
  const events=record.events.map(item=>({...item.event,sequence:item.sequence}));
  for(const event of events){
    const index=d.workflow.findIndex(step=>step.id===event.stage),step=d.workflow[index];
    if(event.state)state=event.state;
    if(event.type==='StageStarted')activeStep=index;
    if(event.type==='ApplicationStateChanged'&&step){
      trace[index]={input:inputState(d,step.id,patches),patch:event.patch,output:event.state[step.output]};
      patches.set(step.id,event.patch);completedSteps.push(step.id);version=event.version;activeStep=null;
      if(d.state.find(item=>item.key===step.output).type==='text')textOrigins[step.output]={agent:step.agent,stage:step.id};
    }
  }
  const review=record.reviews.find(item=>['pending','submitted'].includes(item.status));
  if(record.status==='waiting_for_human'&&review){activeStep=d.workflow.findIndex(step=>step.id===review.stage);trace[activeStep]={input:review.request.state,output:null};}
  if(!['running','waiting_for_human'].includes(record.status))activeStep=null;
  return {live:true,definition:d,task:record.request.task,inputs:record.request.inputs,revision:record.request.revision,
    runId:record.id,resources:record.request.resources||{},mode:record.status==='waiting_for_human'?'waiting_human':record.status,state,activeStep,version,
    completedSteps,executionOrder:executionOrder(d),trace,textOrigins,events,review,metrics:record.metrics||{}};
}

export function liveRunMarkup(d,run,{escape:e,requestForm,outputs,human,progress,history,runtime,busy,error,source='',applicationId=''}){
  const labels={pending:'Starting',running:'Team at work',waiting_human:'Your review is needed',completed:'Results are ready',stopped:'Work stopped',cancelled:'Request cancelled',interrupted:'Work interrupted',failed:'Request failed'};
  const active=run&&['pending','running'].includes(run.mode),last=run?.events.findLast(event=>event.type==='ApplicationFinished');
  const missing=[...new Set(d.agents.flatMap(member=>member.tools))].filter(name=>!runtime.tools?.includes(name));
  const needsSource=d.agents.some(member=>member.tools.some(name=>runtime.resource_tools?.includes(name)));
  const resources=(runtime.resources||[]).filter(item=>!item.applications||item.applications.includes(applicationId));
  const bound=resources.some(item=>item.id===source||!source&&item.id==='default');
  const resourceForm=needsSource?`<div class="field"><label for="live-source">Source documents</label><select id="live-source" data-source-resource><option value="">${resources.some(item=>item.id==='default')?'Use default source':'Choose a source resource'}</option>${resources.map(item=>`<option value="${e(item.id)}" ${item.id===source?'selected':''}>${e(item.name)}</option>`).join('')}</select>${!resources.length?'<p class="validation">No source resource is authorized for this application. Add one to the shared host resource catalogue.</p>':''}</div>`:'';
  return `<div class="run-heading"><div><span class="eyebrow">USE THE APPLICATION</span><h2>${run?'Your request':'Start a request'}</h2></div></div>
  <div class="runtime-banner"><span class="runtime-pill">LOCAL RUN</span><p>${runtime.available?'Uses the AI provider configured on this computer.':'Live runs are unavailable on this host.'}</p></div>
  ${!runtime.available?`<p class="validation" role="status">AI is not configured on this computer.</p><details class="quiet-details"><summary>Connection details</summary><p>${e(runtime.reason||'Configure the local AI host to start a request.')}</p></details>`:''}
  ${missing.length?`<details class="quiet-details"><summary>Some requested tools are unavailable</summary><p>${missing.map(e).join(', ')}</p><p>Adjust tool access in the Plan or configure the host before running.</p></details>`:''}
  ${error?`<p class="validation" role="alert">${e(error)}</p>`:''}
  ${history.length?`<div class="field"><label for="live-history">Saved requests</label><select id="live-history" data-live-history><option value="">Choose a request</option>${history.map(item=>`<option value="${e(item.id)}" ${item.id===run?.runId?'selected':''}>${e(new Date(item.created_at).toLocaleString())} · ${e(item.status.replaceAll('_',' '))} · version ${item.revision}</option>`).join('')}</select></div>`:''}
  ${run?`<p>${e(run.task.slice(0,280))}${run.task.length>280?'…':''}</p><details class="quiet-details" data-disclosure="live-request-details"><summary>Original request and context</summary><p>${e(run.task)}</p>${run.resources?.source?`<p><strong>Source resource</strong><br>${e(run.resources.source)}</p>`:''}${Object.entries(run.inputs).map(([key,value])=>`<p><strong>${e(run.definition.inputs?.find(field=>field.id===key)?.label||key)}</strong><br>${e(value)}</p>`).join('')}</details><details class="quiet-details" data-disclosure="live-new-request"><summary>Start another request</summary>`:''}
  <section class="request-panel">${requestForm}${resourceForm}<div class="request-actions"><button class="button ${run?.mode==='waiting_human'?'':'primary'}" data-action="start-live" ${busy||active||!runtime.available||missing.length||needsSource&&!bound?'disabled':''}>${busy?'Working…':run?'Start a new request':'Start request'}</button></div></section>${run?'</details>':''}
  ${active||run?.mode==='waiting_human'?`<button class="button" data-action="cancel-live" ${busy?'disabled':''}>Stop request</button>`:''}
  ${run?`<div class="run-overview" role="status"><span class="run-status ${run.mode==='completed'?'complete':''}">${e(labels[run.mode]||run.mode)}</span><span>${run.completedSteps.length} of ${run.definition.workflow.length} steps complete</span><span>Saved version ${run.revision}</span></div>${last?.reason&&run.mode!=='waiting_human'?`<p class="version-note">${e(last.reason)}</p>`:''}${run.events.filter(event=>event.type==='BudgetWarning').map(event=>`<p class="version-note" role="status">${e(event.dimension.replaceAll('_',' '))} warning: ${e(event.used)} used (threshold ${e(event.threshold)}).</p>`).join('')}${human}${progress}${outputs}<details class="quiet-details" data-disclosure="live-details"><summary>Activity and usage</summary><p>Recorded through completed steps. Unreported measurements remain unavailable.</p><div class="metrics-grid">${[['wall_clock_seconds','AI execution seconds'],['tokens','AI text units'],['tool_calls','Tool attempts'],['cost_usd','Cost · USD']].map(([key,label])=>`<div><strong>${label}</strong><p>${e(run.metrics[key]??'Not reported')}</p></div>`).join('')}</div><ol>${run.events.filter(event=>['StageStarted','HumanInputRequested','HumanReviewSubmitted','ApplicationFinished'].includes(event.type)).map(event=>`<li>${e(event.type==='StageStarted'?'Work started':event.type==='HumanInputRequested'?'Review requested':event.type==='HumanReviewSubmitted'?'Review recorded':'Request '+(event.status||'').replaceAll('_',' '))}${event.stage?' · '+e(run.definition.workflow.find(step=>step.id===event.stage)?.name||event.stage):''}</li>`).join('')}</ol></details>`:''}`;
}
