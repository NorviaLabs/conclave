import {initialState,executionOrder,planConnections,mergeRecords,preflightIssues} from '/definition.js';
import {uiIcon} from '/canvas-ui.js';
import {runProgressText,budgetStopNoteMarkup,failedRunNoteMarkup,timedOutNoteMarkup} from '/parallel-ui.js';

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

// Uncertain actions, as the run's own events and counts say (run-actions.js
// draws the panel; these stay here so this module loads on its own).
function uncertainSteps(events){
  const stages=new Map(),open=new Set();
  for(const event of events){
    if(event.type==='ActionUncertain'){stages.set(event.action_id,event.stage);open.add(event.action_id);}
    else if(event.type==='ActionSettled')open.delete(event.action_id);
    else if(event.type==='ActionDecisionUndone'&&stages.has(event.action_id))open.add(event.action_id);
  }
  return [...new Set([...open].map(id=>stages.get(id)))];
}
const plural=(count,one,many)=>`${count} ${count===1?one:many}`;
// When a retry is due, on this computer's clock (run-activity.js says the same;
// a copy here keeps this module loading on its own).
const retryTime=at=>{const date=at?new Date(at):null;return date&&!Number.isNaN(date.getTime())?`at ${date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}`:'';};
function attentionText(counts){
  const {to_check:toCheck=0,needs_decision:needs=0,unknown=0}=counts||{};
  if(toCheck)return plural(toCheck+needs,'action','actions')+' to check';
  if(needs)return plural(needs,'action needs','actions need')+' your decision';
  if(unknown)return plural(unknown,'outcome','outcomes')+' unknown';
  return '';
}

// A host that reports it can push is told about a run; one that cannot is asked
// on a timer. The decision is here so it can be checked without a browser.
export const liveFollowMode=runtime=>runtime?.features?.events===true?'push':'poll';
export const liveStreamPath=runId=>`/api/runs/${encodeURIComponent(runId)}/stream`;
// How long a page that polls waits before asking again. A run waiting for its
// automatic retry changes only when the retry is due, so it is asked then: no
// sooner than the usual 1.2 seconds, and at least every 30.
export function livePollDelay(run,now=Date.now()){
  if(!run?.retry)return 1200;
  const due=Date.parse(run.retry.at);
  return Number.isNaN(due)?30000:Math.min(30000,Math.max(1200,due-now));
}

export function projectLiveRun(record){
  const d=record.request.definition,patches=new Map(),completedSteps=[],trace=[],textOrigins={},working=new Set();
  // Steps run side by side: one may finish before an earlier one, and wait
  // (held) until its result joins the results in plan order. Each step's own
  // end is on its StageFinished, so a failed or stopped step is marked even
  // when the run names another one.
  const held=new Map(),stepOutcomes={},cutShort={},budgetExceeded={},skippedSteps={},timedOut={};
  let state=initialState(d),activeStep=null,version=0,pauseRequested=false;
  // Each model call is added up in record.usage; nothing else here reads one,
  // so they are dropped once rather than passed over by every view.
  const events=[];
  for(const item of record.events)if(item.event.type!=='ModelCalled')events.push({...item.event,sequence:item.sequence,timestamp:item.created_at||item.event.timestamp});
  for(const event of events){
    const index=d.workflow.findIndex(step=>step.id===event.stage),step=d.workflow[index];
    if(event.state)state=event.state;
    if(['ApplicationStarted','ApplicationResumed','ApplicationFinished'].includes(event.type))pauseRequested=false;
    if(event.type==='PauseRequested')pauseRequested=true;
    if(event.type==='BudgetExceeded')budgetExceeded[event.dimension]={limit:event.limit,used:event.used};
    if(event.type==='StageStarted'){
      working.add(index);activeStep=index;delete stepOutcomes[event.stage];delete timedOut[event.stage];
      if(step)trace[index]={input:inputState(d,step.id,patches),output:null};
    }
    if(event.type==='StageFinished'&&step){
      working.delete(index);activeStep=[...working].at(-1)??null;
      if(event.status==='completed'){
        if(event.cut_short)cutShort[step.id]=event.cut_short;
        if(event.held){held.set(step.id,event.patch);trace[index]={input:trace[index]?.input??inputState(d,step.id,patches),patch:event.patch,output:event.patch};}
      }else{stepOutcomes[step.id]={status:event.status,reason:event.reason||''};
        // Its own time limit stopped it: how long it had, and how long it worked.
        if(event.status==='timed_out')timedOut[step.id]={limit:event.time_limit_seconds,elapsed:event.elapsed_seconds};}
    }
    // A condition skipped this step: done, with no output, for the reason given.
    if(event.type==='StageSkipped'&&step){skippedSteps[step.id]=event.reason||'';working.delete(index);}
    // A reviewer asked for changes: the steps it sends back are not done any more.
    if(event.type==='RevisionRequested'){
      for(const id of event.reset||[]){delete skippedSteps[id];const at=d.workflow.findIndex(item=>item.id===id);patches.delete(id);held.delete(id);delete stepOutcomes[id];delete trace[at];
        const done=completedSteps.indexOf(id);if(done>=0)completedSteps.splice(done,1);}
    }
    if(event.type==='ApplicationStateChanged'&&step){
      trace[index]={input:trace[index]?.input??inputState(d,step.id,patches),patch:event.patch,output:event.state[step.output]};
      patches.set(step.id,event.patch);completedSteps.push(step.id);version=event.version;working.delete(index);held.delete(step.id);activeStep=[...working].at(-1)??null;
      if(d.state.find(item=>item.key===step.output).type==='text')textOrigins[step.output]={agent:step.agent,stage:step.id};
    }
  }
  const review=record.reviews.find(item=>['pending','submitted'].includes(item.status));
  if(record.status==='waiting_for_human'&&review){activeStep=d.workflow.findIndex(step=>step.id===review.stage);working.add(activeStep);trace[activeStep]={input:review.request.state,output:null};}
  if(!['running','waiting_for_human'].includes(record.status)){activeStep=null;working.clear();}
  const last=events.findLast(event=>event.type==='ApplicationFinished');
  // Automatic retries since a step last completed: the host's own count.
  let retriesUsed=0;
  for(let index=events.length-1;index>=0;index--){const event=events[index];if(event.type==='StageFinished'&&event.status==='completed')break;if(event.type==='RetryScheduled')retriesUsed++;}
  const budgetStop=record.status==='stopped'&&last?.budget?{dimension:last.budget,
    limit:budgetExceeded[last.budget]?.limit??d.run_budget?.[last.budget]??null,used:budgetExceeded[last.budget]?.used??record.metrics?.[last.budget]??null}:null;
  return {live:true,definition:d,task:record.request.task,inputs:record.request.inputs,revision:record.request.revision,
    runId:record.id,resources:record.request.resources||{},mode:record.status==='waiting_for_human'?'waiting_human':record.status,state,activeStep,version,
    activeSteps:[...working],completedSteps,skippedSteps,finishedSteps:[...held.keys()],heldOutputs:Object.fromEntries(held),stepOutcomes,cutShort,
    pausing:pauseRequested&&['pending','running'].includes(record.status)?{running:working.size}:null,budgetStop,
    executionOrder:executionOrder(d),trace,textOrigins,events,review,metrics:record.metrics||{},resumable:!!record.resumable,
    actions:record.actions||{to_check:0,needs_decision:0,unknown:0},needsCheck:uncertainSteps(events),
    retry:record.status==='stopped'&&record.retry?record.retry:null,retriesUsed,
    // Whether the person looking may answer the waiting review (the host decides), and who started the run.
    canAnswer:record.can_answer||null,startedBy:record.started_by||null,
    // What the run used, step by step, as the host added it up.
    usage:record.usage||null,
    // The files its steps stored, each with its checked download link.
    files:record.files||[],timedOut};
}

export function liveRunMarkup(d,run,{escape:e,requestForm,outputs,human,progress,runtime,busy,error,source='',applicationId='',requestOnly=false,warningsReviewed=false,runNoticeMarkup=()=>'',versionMarkup=()=>'',usageMarkup=null,usageWarningMarkup=()=>''}){
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
  const attention=attentionText(run.actions),unsettled=(run.actions?.to_check||0)+(run.actions?.needs_decision||0);
  // Several steps may be working at once, so the line counts them rather than
  // naming whichever member started last.
  const runStatus=run.pausing?'<span class="run-status pausing">Pausing</span>':run.retry?'<span class="run-status retrying">Waiting to retry</span>':`<span class="run-status ${run.mode==='completed'?'complete':''}">${e(labels[run.mode]||run.mode)}</span>`;
  const savedWork=run.budgetStop?budgetStopNoteMarkup(run,{escape:e}):Object.keys(run.timedOut||{}).length&&run.mode==='stopped'?timedOutNoteMarkup(d,run,{escape:e}):run.mode==='failed'&&!run.resumable?failedRunNoteMarkup(d,run,{escape:e})
    :run.retry?retryNoteMarkup(run,{escape:e,busy})
    :`<strong>Completed work is saved.</strong><p>${run.completedSteps.length} completed steps remain in this run. ${run.resumable?(unsettled?`Continue after checking ${plural(unsettled,'action','actions')} that ${unsettled===1?'was':'were'} being sent.`:'Continue from the next unfinished step.'):'This run cannot be continued. Check the saved work, then start a new run.'}</p>${last?.reason?`<p>${e(last.reason)}${last.transient&&run.retriesUsed?`${/[.!?]$/.test(last.reason)?'':'.'} Tried again automatically ${plural(run.retriesUsed,'time','times')}.`:''}</p>`:''}<button class="button" data-action="view-results">View saved work</button>`;
  return `<div class="run-overview" role="status">${runStatus}${attention?`<span class="run-attention${run.actions?.unknown&&!unsettled?' unknown':''}">${e(attention)}</span>`:''}<span>${e(runProgressText(d,run))}</span>${runNoticeMarkup(run.events||[],{escape:e})}<div class="run-secondary-actions">${run.mode==='completed'?'<button class="button" data-action="view-results">View results</button>':''}${run.mode==='waiting_human'?'<button class="button" data-action="view-review">Review input</button>':''}<button class="text-button" data-action="view-run-info" title="Run information" aria-label="Run information">${uiIcon('info')}</button></div></div>
  ${usageWarningMarkup(run)}${['stopped','failed','interrupted','paused'].includes(run.mode)?`<section class="saved-work-note" role="status">${savedWork}</section>`:''}
  ${error?`<p class="validation" role="alert">${e(error)}</p>`:''}${progress}
  <details class="quiet-details run-information" data-disclosure="live-request-details"><summary>Request, context and run information</summary><p>${e(run.task)}</p><p>${local?'LOCAL RUN':hostedPill} · Saved version ${e(run.revision)}</p>${versionMarkup(run)}${run.resources?.source?`<p>Source resource: ${e(run.resources.source)}</p>`:''}${Object.entries(run.inputs).map(([key,value])=>`<p><strong>${e(run.definition.inputs?.find(field=>field.id===key)?.label||key)}</strong><br>${e(value)}</p>`).join('')}${last?.reason?`<p>${e(last.reason)}</p>`:''}<details data-disclosure="live-details"><summary>Usage</summary>${usageMarkup?usageMarkup(run):`<p>Shown only where the AI provider reports it.</p><div class="metrics-grid">${[['wall_clock_seconds','Time · seconds'],['tokens','AI usage · tokens'],['tool_calls','Tool uses'],['cost_usd','Cost · USD']].map(([key,label])=>`<div><strong>${label}</strong><p>${e(run.metrics[key]??'Not reported')}</p></div>`).join('')}</div>`}</details></details>
  ${human}${outputs}`;
}

// A run a model backend stopped, waiting to be tried again by the host. Continue
// in the canvas action bar starts it now; Don't retry leaves it for a person.
export function retryNoteMarkup(run,{escape:e,busy=false}){
  const retry=run.retry,last=run.events.findLast(event=>event.type==='ApplicationFinished'),when=retryTime(retry.at);
  const clock='<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M8 4.2V8l2.6 1.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  return `<strong>Completed work is saved.</strong><p>${plural(run.completedSteps.length,'completed step remains','completed steps remain')} in this run.</p>${last?.reason?`<p>${e(last.reason)}</p>`:''}`
    +`<p class="retry-line">${clock}<span>Trying again automatically${when?` ${e(when)}`:' soon'} (attempt ${e(retry.attempt)} of ${e(retry.attempts)}). Continue starts it now.</span></p>`
    +`<div class="note-actions"><button class="button" data-action="cancel-retry" ${busy?'disabled':''}>Don’t retry</button><button class="button" data-action="view-results">View saved work</button></div>`;
}
