import {initialState,executionOrder,planConnections,mergeRecords,preflightIssues} from '/definition.js';

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

// A host that reports it can push is told about a run; one that cannot is asked
// on a timer. The decision is here so it can be checked without a browser.
export const liveFollowMode=runtime=>runtime?.features?.events===true?'push':'poll';
export const liveStreamPath=runId=>`/api/runs/${encodeURIComponent(runId)}/stream`;

export function projectLiveRun(record){
  const d=record.request.definition,patches=new Map(),completedSteps=[],trace=[],textOrigins={},working=new Set();
  let state=initialState(d),activeStep=null,version=0;
  const events=record.events.map(item=>({...item.event,sequence:item.sequence,timestamp:item.created_at||item.event.timestamp}));
  for(const event of events){
    const index=d.workflow.findIndex(step=>step.id===event.stage),step=d.workflow[index];
    if(event.state)state=event.state;
    if(event.type==='StageStarted'){
      working.add(index);activeStep=index;
      if(step)trace[index]={input:inputState(d,step.id,patches),output:null};
    }
    if(event.type==='ApplicationStateChanged'&&step){
      trace[index]={input:inputState(d,step.id,patches),patch:event.patch,output:event.state[step.output]};
      patches.set(step.id,event.patch);completedSteps.push(step.id);version=event.version;working.delete(index);activeStep=[...working].at(-1)??null;
      if(d.state.find(item=>item.key===step.output).type==='text')textOrigins[step.output]={agent:step.agent,stage:step.id};
    }
  }
  const review=record.reviews.find(item=>['pending','submitted'].includes(item.status));
  if(record.status==='waiting_for_human'&&review){activeStep=d.workflow.findIndex(step=>step.id===review.stage);working.add(activeStep);trace[activeStep]={input:review.request.state,output:null};}
  if(!['running','waiting_for_human'].includes(record.status)){activeStep=null;working.clear();}
  return {live:true,definition:d,task:record.request.task,inputs:record.request.inputs,revision:record.request.revision,
    runId:record.id,resources:record.request.resources||{},mode:record.status==='waiting_for_human'?'waiting_human':record.status,state,activeStep,version,
    activeSteps:[...working],completedSteps,executionOrder:executionOrder(d),trace,textOrigins,events,review,metrics:record.metrics||{}};
}

export function liveRunMarkup(d,run,{escape:e,requestForm,outputs,human,progress,runtime,busy,error,source='',applicationId='',requestOnly=false,warningsReviewed=false}){
  // A host that reports no profile is local, which is the server's default.
  const local=runtime.profile?runtime.profile==='local':true;
  const hostedPill=`HOSTED RUN${runtime.tenant&&runtime.tenant!=='local'?` · ${e(runtime.tenant)}`:''}`;
  const labels={pending:'Starting',running:'Team at work',waiting_human:'Your review is needed',completed:'Results are ready',stopped:'Work stopped',cancelled:'Request cancelled',interrupted:'Work interrupted',failed:'Request failed'};
  const active=run&&['pending','running'].includes(run.mode),last=run?.events.findLast(event=>event.type==='ApplicationFinished');
  const missing=[...new Set(d.agents.flatMap(member=>member.tools))].filter(name=>!runtime.tools?.includes(name));
  const needsSource=d.agents.some(member=>member.tools.some(name=>runtime.resource_tools?.includes(name)));
  const resources=(runtime.resources||[]).filter(item=>!item.applications||item.applications.includes(applicationId));
  const bound=resources.some(item=>item.id===source||!source&&item.id===(d.resources?.source||'default'));
  const issues=requestOnly?preflightIssues(d):[];
  const blocked=issues.some(issue=>issue.severity==='error')||issues.length&&!warningsReviewed;
  const resourceForm=needsSource?`<div class="field"><label for="live-source">Source documents</label><select id="live-source" data-source-resource><option value="">${d.resources?.source?'Use configured source':resources.some(item=>item.id==='default')?'Use default source':'Choose a source resource'}</option>${resources.map(item=>`<option value="${e(item.id)}" ${item.id===source?'selected':''}>${e(item.name)}</option>`).join('')}</select>${!resources.length?`<p class="validation">No source resource is authorized for this application.${local?' Add one to the shared host resource catalogue.':' Ask the deployment to authorize a source for this application.'}</p>`:''}</div>`:'';
  const configuration=`${!runtime.available?`<p class="validation" role="status">${!local?'No AI provider is configured for this workspace.':'No AI provider was found on this computer.'}</p><details class="quiet-details"><summary>Connection details</summary><p>${e(runtime.reason||'Configure the local AI host to start a request.')}</p></details>`:''}
    ${missing.length?`<details class="quiet-details"><summary>Some requested tools are unavailable</summary><p>${missing.map(e).join(', ')}</p></details>`:''}
    ${error?`<p class="validation" role="alert">${e(error)}</p>`:''}
    <section class="request-panel">${requestForm}${resourceForm}<div class="request-actions"><button type="submit" class="button primary" data-action="start-live" ${busy||active||blocked||!runtime.available||missing.length||needsSource&&!bound?'disabled':''}>${busy?'Working…':'Run'}</button></div></section>`;
  if(requestOnly){
    const checks=`<section class="panel" aria-label="Pre-run checks"><h3>Validate application</h3><h4>Pre-run checks</h4>${issues.length?`<p>Review ${issues.length} finding${issues.length===1?'':'s'} before running. Errors block Run; warnings require review.</p>${issues.map((issue,index)=>`<div class="validation"><strong>${e(issue.severity.toUpperCase())} · ${e(d.workflow.find(step=>step.id===issue.stage)?.name||issue.stage)} · ${e(issue.label)}</strong><p>${e(issue.message)}</p><blockquote>${e(issue.evidence)}</blockquote>${issue.fix?`<pre class="code">${e(issue.fix.preview)}</pre><button type="button" class="button" data-preflight-fix="${index}">${e(issue.fix.label)}</button> `:''}<button type="button" class="button" data-preflight-edit="${e(issue.stage)}">Edit step instructions</button> <button type="button" class="button" data-preflight-settings>Open schema, tools and limits</button></div>`).join('')}<label><input type="checkbox" data-preflight-review ${warningsReviewed?'checked':''}> I reviewed the warnings and accept their risks for this saved version.</label>`:'<p>No known output-contract conflicts found.</p>'}<p class="field-help">Static checks cover explicit contract conflicts, tool/task compatibility, output dependencies, verification coverage and minimum budgets. Heuristics may miss issues or flag valid external inputs; they cannot guarantee valid model output. Source authorization and tool availability are checked below; document existence is checked by the host during execution.</p></section>`;
    return checks+configuration;
  }
  if(!run)return `<div class="runtime-banner"><span class="runtime-pill">${local?'LOCAL RUN':hostedPill}</span><p>${local?'Uses the AI provider configured on this computer.':'Uses the AI provider configured for this workspace.'}</p>${configuration}</div>`;
  const current=d.workflow[run.activeStep],status=current?`${d.agents.find(agent=>agent.id===current.agent)?.name||current.agent} ${run.mode==='waiting_human'?'needs your input':'is working'}`:labels[run.mode]||run.mode;
  return `<div class="run-overview" role="status"><span class="run-status ${run.mode==='completed'?'complete':''}">${e(labels[run.mode]||run.mode)}</span><span>${run.completedSteps.length} of ${run.definition.workflow.length} steps complete${current?` · ${e(status)}`:''}</span><div class="run-secondary-actions">${run.mode==='completed'?'<button class="button" data-action="view-results">View results</button>':''}${run.mode==='waiting_human'?'<button class="button" data-action="view-review">Review input</button>':''}<button class="text-button" data-action="view-run-info">Run information</button></div></div>
  ${error?`<p class="validation" role="alert">${e(error)}</p>`:''}${progress}
  <details class="quiet-details run-information" data-disclosure="live-request-details"><summary>Request, context and run information</summary><p>${e(run.task)}</p><p>${local?'LOCAL RUN':hostedPill} · Saved version ${e(run.revision)}</p>${run.resources?.source?`<p>Source resource: ${e(run.resources.source)}</p>`:''}${Object.entries(run.inputs).map(([key,value])=>`<p><strong>${e(run.definition.inputs?.find(field=>field.id===key)?.label||key)}</strong><br>${e(value)}</p>`).join('')}${last?.reason?`<p>${e(last.reason)}</p>`:''}<details data-disclosure="live-details"><summary>Usage and execution metrics</summary><p>Unreported measurements remain unavailable.</p><div class="metrics-grid">${[['wall_clock_seconds','AI execution seconds'],['tokens','AI text units'],['tool_calls','Tool attempts'],['cost_usd','Cost · USD']].map(([key,label])=>`<div><strong>${label}</strong><p>${e(run.metrics[key]??'Not reported')}</p></div>`).join('')}</div></details></details>
  ${human}${outputs}`;
}
