// An application's kept versions: the list, two compared, a restore, and the
// version a run ran. Everything here is read from the host's own answers
// (/api/applications/{id}/versions…), so nothing is said that it did not record.
// Imports nothing, so any page or check can load it on its own.
const plural=(count,one,many)=>`${count} ${count===1?one:many}`;
const when=at=>{const date=at?new Date(at):null;return date&&!Number.isNaN(date.getTime())?date.toLocaleString([], {day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}):'';};
const numbers=items=>items.length<2?String(items[0]):`${items.slice(0,-1).join(', ')} and ${items.at(-1)}`;

// The versions that were not kept, between and below the kept ones: [[from, to], …], newest first.
export function versionGaps(list){
  const kept=list.map(item=>item.version).sort((a,b)=>b-a),gaps=[];
  kept.forEach((number,index)=>{const below=kept[index+1]??0;if(number-below>1)gaps.push({after:number,missing:Array.from({length:number-below-1},(_,n)=>number-1-n).reverse()});});
  return gaps;
}

// The list. `chosen` holds up to two version numbers to compare.
export function versionsListMarkup(data,{escape:e,name='',chosen=[]}){
  const gaps=new Map(versionGaps(data.versions).map(gap=>[gap.after,gap.missing])),two=chosen.length>=2;
  const gapRow=missing=>`<li class="version-gap"><span></span><span class="version-meta">${missing.length===1?`Version ${missing[0]} was not kept`:`Versions ${numbers(missing)} were not kept`}</span><span></span></li>`;
  const rows=data.versions.map(item=>{
    const picked=chosen.includes(item.version),tags=`${item.current?'<span class="version-tag current">Current</span>':''}${item.restored_from?`<span class="version-tag">Restored from ${e(item.restored_from)}</span>`:''}${item.recovered_from_run?'<span class="version-tag">Recovered from a run</span>':''}`;
    const meta=item.recovered_from_run?`Saved before versions were kept · recovered from a run ${item.recovered_started_by?`started by ${e(item.recovered_started_by)}`:'whose starter is not known'}`:`${item.saved_by?e(item.saved_by):'Who saved it is not known'}${item.saved_at?` · ${e(when(item.saved_at))}`:''}`;
    const runs=item.runs?`<button type="button" class="text-button version-runs" data-version-runs="${e(item.version)}">${e(plural(item.runs,'run','runs'))}</button>`:'<span class="version-runs">No runs</span>';
    return `<li class="${item.current?'current':''}"><input type="checkbox" data-version-choose="${e(item.version)}" aria-label="Compare version ${e(item.version)}" ${picked?'checked':''} ${two&&!picked?'disabled':''}><span><span class="version-title">Version ${e(item.version)}</span>${tags}</span>${runs}<span class="version-meta">${meta}</span><span class="version-summary">${e(item.summary)}</span></li>${gaps.has(item.version)?gapRow(gaps.get(item.version)):''}`;
  }).join('');
  const [a,b]=[...chosen].sort((x,y)=>x-y);
  return `<h2 id="versions-title">Versions of ${e(name)}</h2><p>Moving cards on the canvas does not make a new version; every other saved change does.</p><ul class="version-list" aria-label="Saved versions">${rows}</ul><div class="compare-bar"><button type="button" class="button primary" data-version-compare ${two?'':'disabled'}>${two?`Compare version ${e(a)} with ${e(b)}`:'Choose two versions to compare'}</button><span class="field-help" role="status">${two?'Two versions chosen. Clear one to choose another; the other boxes wait until then.':chosen.length===1?'One version chosen; choose one more.':'Choose two versions.'}</span></div>`;
}

// Two versions compared, area by area, in words.
export function comparisonMarkup(comparison,{escape:e,current,definitions=null}){
  const change=item=>`<li><span class="change-kind ${e(item.kind)}">${e({added:'Added',removed:'Removed',changed:'Changed'}[item.kind]||item.kind)}</span><span>${item.was!==undefined?`${e(item.subject)}: was <span class="was">${e(item.was)}</span>, now <span class="now">${e(item.now)}</span>`:e(item.text)}</span></li>`;
  const areas=comparison.areas.length;
  const lead=!comparison.count?`No change to what it does.${comparison.layout_changed?' Card positions changed.':''}`
    :`${plural(comparison.count,'change','changes')} in ${plural(areas,'area','areas')}.${comparison.layout_changed?' Card positions also changed.':''}${comparison.truncated?` The first ${comparison.count-comparison.truncated} are shown.`:''}`;
  const older=Math.min(comparison.from,comparison.to);
  const restore=older!==current?`<button type="button" class="button" data-version-restore="${e(older)}">Restore version ${e(older)} as a new version</button>`:'';
  return `<button type="button" class="text-button" data-version-back>Back to versions</button><h2 id="versions-title">Version ${e(comparison.from)} → version ${e(comparison.to)}</h2><p>${e(lead)}</p>${comparison.areas.map(area=>`<section class="change-area"><h3>${e(area.area)}</h3><ul class="change-list">${area.changes.map(change).join('')}</ul></section>`).join('')}<details class="quiet-details"><summary>Advanced · both definitions as JSON</summary>${definitions?`<h4>Version ${e(comparison.from)}</h4><pre class="code">${e(JSON.stringify(definitions.from,null,2))}</pre><h4>Version ${e(comparison.to)}</h4><pre class="code">${e(JSON.stringify(definitions.to,null,2))}</pre>`:'<p class="field-help">Loading…</p>'}</details><div class="actions">${restore}<button type="button" class="text-button" data-version-back>Close comparison</button></div>`;
}

// The changes that loosen or tighten what keeps a team in check: who may answer
// reviews, limits and autonomous execution, conditions and answers on steps,
// members' tools and kinds, and what connectors may do.
const GUARD_AREAS=new Set(['Review policy','Limits and approvals','Connectors']);
const GUARD_WORDS=/\bcondition\b|people who must answer|steps it revises|revision rounds|: tools\b|: kind\b/;
export function sensitiveChanges(comparison){
  return (comparison?.areas||[]).flatMap(area=>area.changes.filter(change=>GUARD_AREAS.has(area.area)||GUARD_WORDS.test(change.text)).map(change=>change.text));
}

// Asked before a restore: what the new version will hold, what stays, and unsaved edits by name.
export function restoreConfirmMarkup({version,current,changes,unsaved,scheduled,recovered=false,startedBy=null,sensitive=[]},{escape:e}){
  const next=current+1;
  // A version recovered from a run was never seen saved: say so before it is restored.
  const doubt=recovered?`<p><strong>Conclave cannot confirm version ${e(version)} was ever saved here.</strong> It was recovered from a run${startedBy?` started by ${e(startedBy)}`:''}; check what it holds before you restore it.</p>`:'';
  return `<div class="restore-confirm" role="alertdialog" aria-labelledby="restore-title"><strong id="restore-title">Restore version ${e(version)} as version ${e(next)}?</strong><p>Version ${e(next)} will have version ${e(version)}’s team, steps and settings (${e(plural(changes,'change','changes'))} from the current version ${e(current)}). ${current-version===1?`Version ${e(current)} stays`:current-version===2?`Versions ${e(version+1)} and ${e(current)} stay`:`Versions ${e(version+1)} to ${e(current)} stay`} in the history. Runs already going keep the version they started with${scheduled?`; the schedule uses version ${e(next)} from its next run`:''}.</p>${doubt}${sensitive.length?`<div class="restore-sensitive" role="note"><strong>This restore also changes what keeps the team in check:</strong><ul>${sensitive.map(text=>`<li>${e(text)}</li>`).join('')}</ul></div>`:''}${unsaved?`<p><strong>You have unsaved edits.</strong> Restoring replaces them. Save them first to keep them as version ${e(next)}, and restore as version ${e(next+1)}.</p>`:''}<div class="actions"><button type="button" class="button primary" data-version-restore-confirm="${e(version)}"${recovered?' data-version-recovered':''}>${recovered?`Restore this recovered version as version ${e(next)}`:`Restore as version ${e(next)}`}</button>${unsaved?'<button type="button" class="button" data-version-save-first>Save my edits first</button>':''}<button type="button" class="text-button" data-version-back>Cancel</button></div></div>`;
}

// In a run's information: the version it ran, and whether that is still the current one.
export function runVersionMarkup(run,current,{escape:e}){
  const number=run?.revision;
  if(!number)return '';
  const newer=current&&current>number;
  return `<p>Ran saved version ${e(number)} · <button type="button" class="text-button" data-version-open="${e(number)}">View this version</button></p>${newer?`<p class="field-help">Not the current version: the application is at version ${e(current)}. <button type="button" class="text-button" data-version-compare-pair="${e(number)},${e(current)}">Compare ${e(number)} with ${e(current)}</button></p>`:''}`;
}
