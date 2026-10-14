import {initialState,executionOrder,planConnections,mergeRecords,preflightIssues} from '/definition.js';
import {uiIcon} from '/canvas-ui.js';

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
    activeSteps:[...working],completedSteps,executionOrder:executionOrder(d),trace,textOrigins,events,review,metrics:record.metrics||{},resumable:!!record.resumable};
}

export function liveRunMarkup(d,run,{escape:e,requestForm,outputs,human,progress,runtime,busy,error,source='',applicationId='',requestOnly=false,warningsReviewed=false}){
  // A host that reports no profile is local, which is the server's default.
  const local=runtime.profile?runtime.profile==='local':true;
  const hostedPill=`HOSTED RUN${runtime.tenant&&runtime.tenant!=='local'?` · ${e(runtime.tenant)}`:''}`;
  const labels={pending:'Starting',running:'Team at work',waiting_human:'Your review is needed',completed:'Results are ready',stopped:'Run stopped',cancelled:'Run cancelled',interrupted:'Run interrupted',failed:'Run failed',paused:'Paused between steps'};
  const active=run&&['pending','running'].includes(run.mode),last=run?.events.findLast(event=>event.type==='ApplicationFinished');
  const missing=[...new Set(d.agents.flatMap(member=>member.tools))].filter(name=>!runtime.tools?.includes(name));
  const needsSource=d.agents.some(member=>member.tools.some(name=>runtime.resource_tools?.includes(name)));
  const resources=(runtime.resources||[]).filter(item=>!item.applications||item.applications.includes(applicationId));
  const bound=resources.some(item=>item.id===source||!source&&item.id===(d.resources?.source||'default'));
  const issues=requestOnly?preflightIssues(d):[];
  const blocked=issues.some(issue=>issue.severity==='error')||issues.length&&!warningsReviewed;
  const resourceForm=needsSource?`<div class="field"><label for="live-source">Source documents</label><select id="live-source" data-source-resource><option value="">${d.resources?.source?'Use configured source':resources.some(item=>item.id==='default')?'Use default source':'Choose source documents'}</option>${resources.map(item=>`<option value="${e(item.id)}" ${item.id===source?'selected':''}>${e(item.name)}</option>`).join('')}</select>${!resources.length?`<p class="validation">No source documents are available to this application.${local?' Add a source in Workspace settings › Source settings.':' Ask whoever runs this workspace to give this application a source.'}</p>`:''}</div>`:'';
  const configuration=`${!runtime.available?`<p class="validation" role="status">${!local?'No AI provider is configured for this workspace.':'No AI provider was found on this computer.'}</p><details class="quiet-details"><summary>Connection details</summary><p>${e(runtime.reason||(local?'Conclave did not say why. Check the terminal where it is running.':'Ask whoever runs this workspace to configure an AI provider.'))}</p></details>`:''}
    ${missing.length?`<details class="quiet-details"><summary>${missing.length===1?'1 tool this application uses is':`${missing.length} tools this application uses are`} not available here, so it cannot run</summary><p>${missing.map(e).join(', ')}</p><p>Remove ${missing.length===1?'it':'them'} from the members that use ${missing.length===1?'it':'them'} in Settings › Connections, or run on a host that provides ${missing.length===1?'it':'them'}.</p></details>`:''}
    ${error?`<p class="validation" role="alert">${e(error)}</p>`:''}
    <section class="request-panel">${requestForm}${resourceForm}<div class="request-actions"><button type="submit" class="button primary" data-action="start-live" ${busy||active||blocked||!runtime.available||missing.length||needsSource&&!bound?'disabled':''}>${busy?'Working…':'Run'}</button></div></section>`;
  if(requestOnly){
    const checks=`<section class="panel" aria-label="Pre-run checks"><h3>Pre-run checks</h3>${issues.length?`<p>${issues.length} ${issues.length===1?'issue':'issues'} found. Fix every error before running; warnings can be accepted below.</p>${issues.map((issue,index)=>`<div class="validation"><strong>${e(issue.severity.toUpperCase())} · ${e(d.workflow.find(step=>step.id===issue.stage)?.name||issue.stage)} · ${e(issue.label)}</strong><p>${e(issue.message)}</p><blockquote>${e(issue.evidence)}</blockquote>${issue.fix?`<pre class="code">${e(issue.fix.preview)}</pre><button type="button" class="button" data-preflight-fix="${index}">${e(issue.fix.label)}</button> `:''}<button type="button" class="button" data-preflight-edit="${e(issue.stage)}">Edit step instructions</button> <button type="button" class="button" data-preflight-settings>Open settings</button></div>`).join('')}<label><input type="checkbox" data-preflight-review ${warningsReviewed?'checked':''}> I have read the warnings and want to run this saved version anyway.</label>`:'<p>No problems found.</p>'}<details class="quiet-details"><summary>What these checks cover</summary><p class="field-help">They read the saved plan for conflicting instructions, tools a step needs but lacks, missing inputs, unchecked results and limits too low to finish. They can miss problems or flag something that is fine, and they cannot guarantee what the AI produces.</p></details></section>`;
    return checks+configuration;
  }
  if(!run)return `<div class="runtime-banner"><span class="runtime-pill">${local?'LOCAL RUN':hostedPill}</span><p>${local?'Runs with the AI provider on this computer.':'Runs with this workspace’s AI provider.'}</p>${configuration}</div>`;
  const current=d.workflow[run.activeStep],status=current?`${d.agents.find(agent=>agent.id===current.agent)?.name||current.agent} ${run.mode==='waiting_human'?'needs your input':'is working'}`:labels[run.mode]||run.mode;
  return `<div class="run-overview" role="status"><span class="run-status ${run.mode==='completed'?'complete':''}">${e(labels[run.mode]||run.mode)}</span><span>${run.completedSteps.length} of ${run.definition.workflow.length} steps complete${current&&['running','waiting_human'].includes(run.mode)?` · ${e(status)}`:''}</span><div class="run-secondary-actions">${run.mode==='completed'?'<button class="button" data-action="view-results">View results</button>':''}${run.mode==='waiting_human'?'<button class="button" data-action="view-review">Review input</button>':''}<button class="text-button" data-action="view-run-info" title="Run information" aria-label="Run information">${uiIcon('info')}</button></div></div>
  ${['stopped','failed','interrupted','paused'].includes(run.mode)?`<section class="saved-work-note" role="status"><strong>Completed work is saved.</strong><p>${run.completedSteps.length} completed steps remain in this run. ${run.resumable?'Continue from the next unfinished step.':'This run cannot be continued. Check the saved work, then start a new run.'}</p>${last?.reason?`<p>${e(last.reason)}</p>`:''}<button class="button" data-action="view-results">View saved work</button></section>`:''}
  ${error?`<p class="validation" role="alert">${e(error)}</p>`:''}${progress}
  <details class="quiet-details run-information" data-disclosure="live-request-details"><summary>Request, context and run information</summary><p>${e(run.task)}</p><p>${local?'LOCAL RUN':hostedPill} · Saved version ${e(run.revision)}</p>${run.resources?.source?`<p>Source resource: ${e(run.resources.source)}</p>`:''}${Object.entries(run.inputs).map(([key,value])=>`<p><strong>${e(run.definition.inputs?.find(field=>field.id===key)?.label||key)}</strong><br>${e(value)}</p>`).join('')}${last?.reason?`<p>${e(last.reason)}</p>`:''}<details data-disclosure="live-details"><summary>Usage</summary><p>Shown only where the AI provider reports it.</p><div class="metrics-grid">${[['wall_clock_seconds','Time · seconds'],['tokens','AI usage · tokens'],['tool_calls','Tool uses'],['cost_usd','Cost · USD']].map(([key,label])=>`<div><strong>${label}</strong><p>${e(run.metrics[key]??'Not reported')}</p></div>`).join('')}</div></details></details>
  ${human}${outputs}`;
}
