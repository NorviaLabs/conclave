// Two runs of one application, compared step by step. The mockups are
// docs/studio/mockups/run-compare.html. Everything here comes from
// GET /api/runs/compare; this module only draws it, and loads on its own.
const SWAP_ICON='<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M7 7h12l-3-3M17 17H5l3 3"></path></svg>';
const WARN_ICON='<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M8 1.5l7 12.5H1z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6v4M8 12v.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
const RUN_STATUS={completed:'Completed',waiting_for_human:'Waiting for a person',running:'Running',pending:'Starting',paused:'Paused',
  stopped:'Stopped',failed:'Failed',interrupted:'Interrupted',cancelled:'Cancelled'};
const STEP_STATUS={completed:'Completed',skipped:'Skipped',failed:'Failed',stopped:'Stopped',running:'Running',waiting:'Waiting for a person',
  interrupted:'Interrupted',cancelled:'Cancelled',not_in_version:'Not in this version',not_reached:'Not reached',timed_out:'Timed out'};
const DECISIONS={approved:'Approved',rejected:'Rejected',changes_requested:'Changes requested',answered:'Answered'};
const UNFINISHED={running:'is still running',pending:'is still starting',waiting_for_human:'is waiting for a person',paused:'is paused'};
const TAG_CLASS={skipped_only_in_a:' skipped',skipped_only_in_b:' skipped',not_reached_in_a:' skipped',not_reached_in_b:' skipped',status_changed:' failed'};
const KINDS={added:'Added',removed:'Removed',changed:'Changed'};
const plural=(count,word,many=word+'s')=>`${count} ${count===1?word:many}`;
const number=value=>Number.isFinite(Number(value))?Number(value):0;
// The same words usage-ui.js uses; copied so this module loads on its own.
const countText=value=>Math.round(number(value)).toLocaleString('en-US');
function durationText(seconds){
  const value=number(seconds);
  if(value<1)return 'under 1 s';
  const whole=Math.round(value),pad=n=>String(n).padStart(2,'0');
  if(whole>=3600)return `${Math.floor(whole/3600)} h ${pad(Math.floor(whole%3600/60))} m`;
  if(whole>=60)return `${Math.floor(whole/60)} m ${pad(whole%60)} s`;
  return `${whole} s`;
}
const defaultTime=iso=>{const date=iso?new Date(iso):null;return date&&!Number.isNaN(date.getTime())?date.toLocaleString([],{dateStyle:'medium',timeStyle:'short'}):'an unknown time';};

// --- Choosing two runs in Run history -------------------------------------------------------------
export function historyPickMarkup(run,chosen,{escape:e,time=defaultTime}){
  const checked=chosen.includes(run.id),state=checked?'checked':chosen.length>=2?'disabled':'';
  return `<td class="history-pick"><input type="checkbox" data-run-choose="${e(run.id)}" aria-label="Compare the run started ${e(time(run.created_at))}" ${state}></td>`;
}
export function compareBarMarkup(chosen,{escape:e}){
  const hint=chosen.length>=2?'Two runs chosen. Clear one to choose another.':chosen.length===1?'One run chosen; choose one more.':'Choose two runs to compare.';
  return `<div class="compare-bar"><button class="button primary" data-action="compare-runs" aria-describedby="compare-hint" ${chosen.length===2?'':'disabled'}>Compare 2 runs</button><span class="field-help" id="compare-hint" role="status">${e(hint)}</span></div>`;
}

// --- The comparison ----------------------------------------------------------------------------
function runCard(letter,run,e,time){
  const trigger=run.trigger?.kind==='schedule'?'Scheduled':run.trigger?.kind==='event'?'Started by an event':'';
  const facts=[RUN_STATUS[run.status]||run.status,`version ${run.version}`,trigger,run.started_by?`started by ${run.started_by}`:''].filter(Boolean);
  return `<div class="compare-run"><span class="run-letter" aria-hidden="true">${letter}</span><strong>Run ${letter} · ${e(time(run.created_at))}</strong><p>${e(facts.join(' · '))}</p></div>`;
}
function changeText(change,e){
  if(change.kind==='changed'&&change.subject!==undefined&&change.was!==undefined)
    return `${e(change.subject)}: was <span class="was">${e(change.was)}</span>, now <span class="now">${e(change.now)}</span>`;
  return e(change.text);
}
function versionsBlock(version,e){
  if(!version||version.same)return '';
  const changes=(version.changes?.areas||[]).flatMap(area=>area.changes||[]),shown=changes.slice(0,5),count=version.changes?.count??changes.length;
  return `<section class="compare-block"><h3>The runs used different versions</h3>${shown.length?`<ul class="change-list">${shown.map(change=>`<li><span class="change-kind ${e(KINDS[change.kind]?change.kind:'changed')}">${KINDS[change.kind]||'Changed'}</span><span>${changeText(change,e)}</span></li>`).join('')}</ul>`:''}<p class="field-help">${e(plural(count,'change'))} between version ${e(version.a)} and ${e(version.b)} · <button class="text-button" type="button" data-compare-versions="${e(version.a)},${e(version.b)}">See every change</button></p></section>`;
}
const valueText=value=>value===null||value===undefined||value===''?'empty':typeof value==='object'?JSON.stringify(value):String(value);
function requestBlock(request,e){
  if(!request||(!request.task&&!request.inputs?.length))return '';
  const task=request.task?`<div class="compare-columns"><div><span class="side-label">A · Task</span>${e(request.task.a)}</div><div><span class="side-label">B · Task</span>${e(request.task.b)}</div></div>`:'';
  const values=request.inputs?.length?`<p class="field-help">Request values: ${request.inputs.map(item=>`“${e(item.label)}” was ${e(valueText(item.a))}, now ${e(valueText(item.b))}`).join('; ')}.</p>`:'';
  return `<section class="compare-block"><h3>The requests differed</h3>${task}${values}</section>`;
}
function unfinishedNotes(runs,e){
  return ['a','b'].filter(side=>runs[side]?.in_progress).map(side=>{
    const letter=side.toUpperCase(),how=UNFINISHED[runs[side].status]||'has not finished';
    return `<div class="usage-warning" role="status">${WARN_ICON}<span><strong>Run ${letter} ${e(how)}.</strong> It is compared as it stands now; steps it has not reached say so.</span></div>`;
  }).join('');
}
function sideText(item,side,runs,e){
  const state=item.status?.[side]||{status:'not_reached',reason:''},review=item.review?.[side];
  if(item.kind==='human'&&state.status==='completed'&&review?.decision){
    const decided=DECISIONS[review.decision]||review.decision;
    const said=review.changes_requested&&review.decision!=='changes_requested'?`Changes requested, then ${decided.toLowerCase()}`:decided;
    return `${e(said)}${review.rounds>1?`<p>${e(plural(review.rounds,'round'))}</p>`:''}`;
  }
  const word=state.status==='not_reached'&&runs[side]?.in_progress?'Not reached yet':STEP_STATUS[state.status]||state.status;
  return `${e(word)}${state.reason&&['skipped','failed','stopped','interrupted','cancelled','timed_out'].includes(state.status)?`<p>${e(state.reason)}</p>`:''}`;
}
function textDiff(output,e){
  const lines=output.lines.map(line=>line.kind==='gap'?`<span class="gap">  … ${e(plural(line.count,'unchanged line'))}</span>`
    :line.kind==='del'?`<span class="del"><span class="sr-only">Only in A: </span>− ${e(line.text)}</span>`
    :line.kind==='add'?`<span class="add"><span class="sr-only">Only in B: </span>+ ${e(line.text)}</span>`
    :`<span class="same">  ${e(line.text)}</span>`).join('');
  const coarse=output.coarse?'<p class="field-help">Too long to compare line by line; shown as one changed block.</p>':'';
  return `<section><h4>Output</h4>${coarse}<pre class="text-diff" aria-label="Changes from A to B; − lines are only in A, + lines only in B">${lines}</pre>${output.more?`<p class="compare-more">and ${e(plural(output.more,'more line'))}, not shown</p>`:''}</section>`;
}
function recordChanges(output,e){
  const items=output.changes.map(change=>{
    const named=`“${e(change.title)}” (${e(change.id)})`;
    const fields=change.kind==='changed'&&change.fields?.length?': '+change.fields.map(field=>field.was===null&&field.now===null?`${e(field.label)} changed`
      :`${e(field.label)} was <span class="was">${e(valueText(field.was))}</span>, now <span class="now">${e(valueText(field.now))}</span>`).join(' · '):'';
    return `<li><span class="change-kind ${KINDS[change.kind]?change.kind:'changed'}">${KINDS[change.kind]||'Changed'}</span><span>${named}${fields}</span></li>`;
  }).join('');
  return `<section><h4>Output · ${e(plural(output.count,'record'))} changed</h4><ul class="change-list">${items}</ul>${output.more?`<p class="compare-more">and ${e(output.more)} more changed records, not listed</p>`:''}</section>`;
}
function reviewSide(review,e){
  if(!review)return 'No review';
  if(!review.decision)return '<strong>Not answered yet</strong>';
  const facts=[plural(review.rounds,'round')];
  if(review.changes_requested&&review.first_comment)facts.push(`first asked for changes: “${review.first_comment}”`);
  if(review.comment)facts.push(review.changes_requested?`last: “${review.comment}”`:`“${review.comment}”`);
  return `<strong>${e(DECISIONS[review.decision]||review.decision)}</strong>${review.reviewers?.length?` by ${e(review.reviewers.join(', '))}`:''}<p>${e(facts.join(' · '))}</p>`;
}
const signed=(value,text)=>value===0?'same':`${value>0?'+':'−'}${text}`;
function usageRow(letter,item){
  if(!item)return `<tr><td>${letter}</td><td colspan="4">Not recorded for this run</td></tr>`;
  return `<tr><td>${letter}</td><td>${countText(item.model_calls)}</td><td>${item.measured?countText(item.tokens):'not measured'}</td><td>${countText(item.tool_calls)}</td><td>${durationText(item.seconds)}</td></tr>`;
}
function usageSection(usage){
  if(!usage||(!usage.a&&!usage.b))return '';
  const d=usage.delta;
  const delta=d?`<tr class="delta"><td>Difference</td><td>${signed(d.model_calls,countText(Math.abs(d.model_calls)))}</td><td>${d.tokens_percent===null||d.tokens_percent===undefined?(d.tokens===0?'same':'–'):signed(d.tokens,`${countText(Math.abs(d.tokens))} (${d.tokens_percent>=0?'+':'−'}${Math.abs(d.tokens_percent)}%)`)}</td><td>${signed(d.tool_calls,countText(Math.abs(d.tool_calls)))}</td><td>${Math.abs(number(d.seconds))<1?'same':signed(number(d.seconds),durationText(Math.abs(number(d.seconds))))}</td></tr>`:'';
  return `<section><h4>Usage</h4><table class="usage-table"><thead><tr><th><span class="sr-only">Run</span></th><th>Model calls</th><th>Tokens</th><th>Tool calls</th><th>Time</th></tr></thead><tbody>${usageRow('A',usage.a)}${usageRow('B',usage.b)}${delta}</tbody></table></section>`;
}
function stepRow(item,comparison,e,open){
  const runs=comparison.runs,where=item.presence==='only_b'?` · only in version ${runs.b.version}`:item.presence==='only_a'?` · only in version ${runs.a.version}`:'';
  const small=`${item.agent||''}${item.kind==='human'?' · person’s review':''}${where}`;
  const tags=(item.tags||[]).map(tag=>`<span class="compare-tag${TAG_CLASS[tag.kind]||''}">${e(tag.text)}</span>`).join('');
  // A step's details are drawn when it is opened, so a long comparison stays one row per step.
  const body=open.has(item.id)?bodyMarkup(item,e):'';
  return `<li><details class="step-compare${item.differs?'':' same'}" data-compare-step="${e(item.id)}" ${open.has(item.id)?'open':''}><summary><span class="step-title">${e(item.name)}<small>${e(small)}</small></span><span class="side"><span class="side-label">A</span>${sideText(item,'a',runs,e)}</span><span class="side"><span class="side-label">B</span>${sideText(item,'b',runs,e)}</span>${tags?`<span class="compare-tags">${tags}</span>`:''}</summary>${body}</details></li>`;
}
function bodyMarkup(item,e){
  const output=!item.output?'':item.output.kind==='text'?textDiff(item.output,e):item.output.kind==='records'?recordChanges(item.output,e):'<section><h4>Output</h4><p>The output changed.</p></section>';
  const review=item.review?`<section><h4>Review</h4><div class="compare-columns"><div><span class="side-label">A</span>${reviewSide(item.review.a,e)}</div><div><span class="side-label">B</span>${reviewSide(item.review.b,e)}</div></div></section>`:'';
  const body=output+review+usageSection(item.usage);
  return body?`<div class="step-compare-body">${body}</div>`:'';
}
// One step's details, drawn when a person opens it.
export function stepBodyMarkup(comparison,stepId,{escape:e}){
  const item=(comparison?.steps||[]).find(step=>step.id===stepId);
  return item?bodyMarkup(item,e):'';
}
function summaryMarkup(text,e){
  const at=String(text||'').indexOf(':');
  return at<0?e(text):`<strong>${e(text.slice(0,at+1))}</strong>${e(text.slice(at+1))}`;
}
export function runComparisonMarkup(comparison,{escape:e,time=defaultTime,onlyDiffering=true,open=new Set()}){
  const steps=comparison.steps||[],same=steps.filter(item=>!item.differs).length,visible=onlyDiffering?steps.filter(item=>item.differs):steps;
  const list=visible.length?`<ul class="step-compare-list">${visible.map(item=>stepRow(item,comparison,e,open)).join('')}</ul>`
    :onlyDiffering?'<p class="field-help">Every step did the same in both runs. <button class="text-button" type="button" data-compare-filter-off>Show all steps</button></p>':'<p class="field-help">These runs have no steps.</p>';
  return `<h2 id="compare-title" tabindex="-1">Compare runs of ${e(comparison.name)}</h2>${unfinishedNotes(comparison.runs,e)}<div class="compare-runs">${runCard('A',comparison.runs.a,e,time)}<button class="button compare-swap" data-action="swap-compare" aria-label="Swap A and B" title="Swap A and B">${SWAP_ICON}<span>Swap</span></button>${runCard('B',comparison.runs.b,e,time)}</div><p class="compare-summary" role="status">${summaryMarkup(comparison.summary?.text,e)}</p>${versionsBlock(comparison.version,e)}${requestBlock(comparison.request,e)}<label class="compare-filter"><input type="checkbox" data-compare-filter ${onlyDiffering?'checked':''}> Only steps that differ ${same?`<span class="field-help">· ${same} the same${onlyDiffering?', hidden':''}</span>`:''}</label>${list}`;
}
export function compareErrorMarkup(message,{escape:e}){
  return `<h2 id="compare-title" tabindex="-1">Compare runs</h2><p class="validation" role="alert">${e(message)}</p><div class="actions"><button class="button" data-action="show-history">Back to Run history</button></div>`;
}
