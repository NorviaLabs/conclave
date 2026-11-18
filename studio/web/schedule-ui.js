// Scheduled applications in Studio: the editor under "When to run", the
// warnings shown before saving, the header line that says when it runs next
// (and why the last time did not run), and run history with the times that
// did not start a run.
//
// Pure functions of the definition, the host's schedule status
// (GET /api/applications/{id}/schedule) and its preview
// (POST /api/schedules/preview). No time arithmetic happens here: the host
// works out every scheduled time, so a clock change has one implementation.

const DAYS=[['mon','Mon','Monday'],['tue','Tue','Tuesday'],['wed','Wed','Wednesday'],['thu','Thu','Thursday'],['fri','Fri','Friday'],['sat','Sat','Saturday'],['sun','Sun','Sunday']];
const KINDS=[['interval','Every'],['daily','Daily'],['weekly','Weekly']];
const CLOCK='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>';
const PAUSE='<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M9 6v12M15 6v12"/></svg>';
const PLAY='<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M8 5.5v13l10-6.5z"/></svg>';
const LIMITS=['tool_calls','wall_clock_seconds','tokens','cost_usd'];

export const viewerZone=()=>{try{return Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';}catch{return 'UTC';}};

// A new schedule: weekdays at nine, in the viewer's own zone.
export function defaultSchedule(zone=viewerZone()){
  return {kind:'weekly',times:['09:00'],days:['mon','tue','wed','thu','fri'],time_zone:zone,catch_up:'skip',request:{task:'',inputs:{}}};
}

// A schedule switched to another kind keeps what still applies to it.
export function changeKind(schedule,kind){
  const next={...structuredClone(schedule),kind};
  if(kind==='interval'){delete next.times;delete next.days;next.every_minutes=next.every_minutes||60;}
  else{delete next.every_minutes;next.times=next.times?.length?next.times:['09:00'];if(kind==='weekly')next.days=next.days?.length?next.days:['mon'];else delete next.days;}
  return next;
}

function toolName(tool){const parts=String(tool).split('.');return (parts.length===3&&parts[0]==='connection'?`${parts[1]} · ${parts[2]}`:String(tool)).replaceAll('_',' ');}

// What would go wrong unattended, said before the schedule is saved.
export function scheduleWarnings(definition,runtime={}){
  const warnings=[];
  if(!definition.autonomous_execution){
    const operations=(runtime.integrations||[]).flatMap(item=>item.operations||[]);
    const writes=tool=>{
      const descriptor=(runtime.tool_descriptors||[]).find(item=>item.id===tool);
      if(descriptor)return !!descriptor.requires_approval;
      const parts=String(tool).split('.');
      if(parts.length===3&&parts[0]==='connection'){const operation=operations.find(item=>item.name===parts[2]);return !operation||operation.capability!=='read';}
      return false;
    };
    const byAgent=(definition.agents||[]).filter(agent=>(agent.kind||'ai')==='ai').map(agent=>({agent:agent.name||agent.id,tools:(agent.tools||[]).filter(writes)})).filter(item=>item.tools.length);
    if(byAgent.length)warnings.push({kind:'writes',tools:byAgent.flatMap(item=>item.tools),byAgent,
      text:'Writes will be refused. Nobody approves writes on a schedule. Turn on autonomous execution in Limits and approvals, or remove those tools.'});
  }
  if(LIMITS.every(key=>definition.run_budget?.[key]===null||definition.run_budget?.[key]===undefined))
    warnings.push({kind:'limits',text:'No limits. This runs unattended with no spending or time limits. Set them in Limits and approvals.'});
  return warnings;
}

function warningMarkup(warning,e){
  if(warning.kind==='writes')return `<div class="schedule-warning" role="note"><strong>Writes will be refused.</strong> Nobody approves writes on a schedule: ${warning.byAgent.map(item=>`${e(item.agent)}’s ${item.tools.map(tool=>`<em>${e(toolName(tool))}</em>`).join(' and ')}`).join('; ')} would be refused. Turn on autonomous execution in Limits and approvals, or remove those tools.</div>`;
  return `<div class="schedule-warning" role="note"><strong>No limits.</strong> This runs unattended with no spending or time limits. Set them in Limits and approvals.</div>`;
}

function viewerTime(at,zone){
  try{return new Date(at).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:zone});}catch{return '';}
}

export function scheduleEditorMarkup(definition,{escape:e,preview=null,previewError='',warnings=[],zones=[],sources=[],viewer=''}={}){
  const schedule=definition.schedule||defaultSchedule(),request=schedule.request||{task:'',inputs:{}},kind=schedule.kind;
  const kinds=KINDS.map(([value,label])=>`<button type="button" data-schedule-kind="${value}" aria-pressed="${value===kind}">${label}</button>`).join('');
  let when='';
  if(kind==='interval'){
    // A number is written as a number, whatever the definition carries.
    const minutes=Math.max(0,Math.trunc(Number(schedule.every_minutes)))||60,hours=minutes%60===0;
    when=`<div class="schedule-grid"><div class="field"><label for="schedule-every">Run every</label><input id="schedule-every" type="number" min="1" data-schedule-every value="${hours?minutes/60:minutes}"></div><div class="field"><label for="schedule-unit">Unit</label><select id="schedule-unit" data-schedule-unit><option value="minutes" ${hours?'':'selected'}>minutes</option><option value="hours" ${hours?'selected':''}>hours</option></select></div><div class="field"><label for="schedule-starts">Counting from · optional</label><input id="schedule-starts" type="datetime-local" data-schedule-starts value="${e(schedule.starts_at||'')}"></div></div>`;
  }else{
    const days=kind==='weekly'?`<div class="field"><span class="field-label">On</span><div class="day-toggles" role="group" aria-label="Days">${DAYS.map(([value,label,name])=>`<button type="button" data-schedule-day="${value}" aria-pressed="${(schedule.days||[]).includes(value)}" aria-label="${name}">${label}</button>`).join('')}</div></div>`:'';
    const times=(schedule.times||[]).map((time,index)=>`<span class="schedule-time"><input type="time" data-schedule-time="${index}" value="${e(time)}" aria-label="Time ${index+1}"><button class="icon-button" type="button" data-schedule-remove-time="${index}" title="Remove ${e(time)}" aria-label="Remove ${e(time)}" ${(schedule.times||[]).length<2?'disabled':''}>×</button></span>`).join('');
    when=`${days}<div class="field"><span class="field-label">At</span><div class="schedule-times">${times}${(schedule.times||[]).length<24?'<button class="text-button" type="button" data-schedule-add-time>Add a time</button>':''}</div></div><div class="field"><label for="schedule-starts">Starting · optional</label><input id="schedule-starts" type="datetime-local" data-schedule-starts value="${e(schedule.starts_at||'')}"></div>`;
  }
  const zoneList=[...new Set([schedule.time_zone,...zones].filter(Boolean))];
  const zone=`<div class="field"><label for="schedule-zone">Time zone</label><select id="schedule-zone" data-schedule-zone>${zoneList.map(item=>`<option value="${e(item)}" ${item===schedule.time_zone?'selected':''}>${e(item)}</option>`).join('')}</select><p class="field-help">Times are in this zone, whatever the host’s clock says.</p></div>`;
  const catchUp=schedule.catch_up==='latest'?'latest':'skip';
  const missed=`<div class="field"><span class="field-label">If the host was off at a scheduled time</span><label class="choice"><input type="radio" name="schedule-catch-up" data-schedule-catch-up="skip" ${catchUp==='skip'?'checked':''}> Skip it, and report it</label><label class="choice"><input type="radio" name="schedule-catch-up" data-schedule-catch-up="latest" ${catchUp==='latest'?'checked':''}> Run the latest missed time once</label></div>`;
  const inputs=(definition.inputs||[]).map(field=>`<div class="field"><label for="schedule-input-${e(field.id)}">${e(field.label)}${field.required?' · required':''}</label><input data-schedule-input="${e(field.id)}" value="${e(request.inputs?.[field.id]??'')}" id="schedule-input-${e(field.id)}" type="${field.type==='number'?'number':field.type==='date'?'date':'text'}"></div>`).join('');
  const source=sources.length?`<div class="field"><label for="schedule-source">Source documents</label><select id="schedule-source" data-schedule-source><option value="">No source</option>${sources.map(item=>`<option value="${e(item.id)}" ${request.resources?.source===item.id?'selected':''}>${e(item.name||item.id)}</option>`).join('')}</select></div>`:'';
  const requestMarkup=`<fieldset class="schedule-request"><legend>Each run starts with</legend><div class="field"><label for="schedule-task">What the team should do</label><textarea id="schedule-task" data-schedule-task rows="2" maxlength="20000">${e(request.task||'')}</textarea></div>${inputs}${source}<p class="field-help">This is saved with the application and used for every scheduled run. Do not put passwords or access tokens here.</p></fieldset>`;
  return `<div class="schedule-editor" role="group" aria-labelledby="schedule-title"><h3 id="schedule-title">Schedule</h3><div class="field"><span class="field-label">How often</span><div class="segmented" role="group" aria-label="How often">${kinds}</div></div>${when}<div class="schedule-grid">${zone}${missed}</div>${requestMarkup}${warnings.map(item=>warningMarkup(item,e)).join('')}<div data-schedule-preview-slot>${schedulePreviewMarkup({escape:e,preview,previewError,viewer})}</div></div>`;
}

// The next three times, as the host worked them out, or why it could not.
// Repainted on its own while someone types, so the field keeps its caret.
export function schedulePreviewMarkup({escape:e,preview=null,previewError='',viewer=''}={}){
  if(previewError)return `<p class="schedule-error" role="alert">${e(previewError)}</p>`;
  if(!preview?.times?.length)return '';
  return `<section class="schedule-preview" aria-live="polite"><h4>Next three runs</h4><ol>${preview.times.map(item=>`<li>${e(item.local)} <small>· ${e(item.time_zone)}${viewer&&viewer!==item.time_zone?` · ${e(viewerTime(item.at,viewer))} your time (${e(viewer)})`:''}</small></li>`).join('')}</ol></section>`;
}

// The line under the application's goal: when it runs next, paused, or
// waiting for an owner; and, when the last time did not run, why.
// A save by someone else hands the schedule to them: its runs act as them
// from now on, and the person who saved is told so.
export function ownerChangeNotice(before,after){
  if(!before?.active||!after?.active||!before.owner||!after.owner||before.owner===after.owner)return '';
  return `This schedule now runs as ${after.owner}.`;
}

// When to read a schedule's status again: a few seconds after its next run,
// when what the header says stops being true. Nothing to wait for when it is
// paused, waiting for an owner, or off.
export function statusRefreshDelay(status,now=Date.now()){
  if(!status?.active||status.paused||status.waiting||!status.next_at)return null;
  const at=Date.parse(status.next_at);
  if(!Number.isFinite(at))return null;
  return Math.max(at-now,0)+5000;
}

// How far away a time is, roughly: "in 15 hours". Empty once it has passed.
export function relativeTime(at,now=Date.now()){
  const minutes=Math.round((Date.parse(at)-now)/60000);
  if(!Number.isFinite(minutes)||minutes<0)return '';
  if(minutes<1)return 'in under a minute';
  if(minutes<60)return `in ${minutes} minute${minutes===1?'':'s'}`;
  const hours=Math.round(minutes/60);
  if(hours<48)return `in ${hours} hour${hours===1?'':'s'}`;
  return `in ${Math.round(hours/24)} days`;
}

// The button that pauses, resumes or turns on the schedule. The header shows
// it with the other application actions; the line carries it when asked to.
export function scheduleActionMarkup(status){
  if(!status||!status.active)return '';
  if(status.waiting)return `<button class="button primary" type="button" data-schedule-action="turn_on">${PLAY}<span>Turn on schedule</span></button>`;
  if(status.paused)return `<button class="button" type="button" data-schedule-action="resume">${PLAY}<span>Resume schedule</span></button>`;
  return `<button class="button" type="button" data-schedule-action="pause">${PAUSE}<span>Pause schedule</span></button>`;
}

export function scheduleLineMarkup(status,{escape:e,actions=true,now=Date.now()}={}){
  if(!status||!status.active)return '';
  let chip,text;
  if(status.waiting){
    chip=`<span class="schedule-chip waiting">${CLOCK}Schedule waiting</span>`;
    text='This schedule is not running on this host yet: it has no owner. Turning it on makes runs act as you.';
  }else if(status.paused){
    chip=`<span class="schedule-chip paused">${CLOCK}Schedule paused</span>`;
    text=`Paused${status.paused_by?` by ${e(status.paused_by)}`:''}${status.paused_at?` on ${e(localText(status.paused_at,status.time_zone).split(',')[0])}`:''}. Nothing runs until it is resumed; times passed while paused are not run.`;
  }else{
    chip=`<span class="schedule-chip">${CLOCK}Scheduled</span>`;
    const soon=status.next_at?relativeTime(status.next_at,now):'';
    text=`${status.next_at?`Next run <time datetime="${e(status.next_at)}" title="${e(viewerTime(status.next_at,viewerZone()))} your time (${e(viewerZone())})">${e(status.next_local)}</time> ${e(status.time_zone)}${soon?` · ${soon}`:''}`:'Working out the next run'}${status.owner?` · runs as ${e(status.owner)}`:''}`;
  }
  return `<div class="schedule-line" role="status">${chip}<span>${text}</span>${actions?scheduleActionMarkup(status):''}</div>${lastNote(status.last,e)}`;
}

// A definition saved before schedules had a block says it starts on a
// schedule but never says when: it does not run, and the header says so.
export function scheduleIncompleteMarkup(definition){
  if(definition?.start_mode!=='schedule'||definition.schedule)return '';
  return `<div class="schedule-line" role="status"><span class="schedule-chip waiting">${CLOCK}Schedule incomplete</span><span>This application starts on a schedule but does not say when, so it does not run.</span><button class="text-button" type="button" data-action="edit-schedule">Edit the schedule</button></div>`;
}

function lastNote(last,e){
  if(!last||last.outcome==='started')return '';
  const detail=last.detail||{};
  if(last.outcome==='skipped')return `<p class="schedule-note">Last time skipped: the previous run was still going at ${e(last.scheduled_local)}.${detail.run_id?` <button class="text-button" type="button" data-run-open="${e(detail.run_id)}">Open that run</button>`:''}</p>`;
  if(last.outcome==='missed'){
    const count=Math.max(1,Math.trunc(Number(detail.count))||1),range=count>1?`${e(detail.first_local)} – ${e(detail.last_local)}`:e(detail.first_local||last.scheduled_local);
    return `<p class="schedule-note missed">Missed ${count===1?'once':`${count} times`} while the host was off (${range}). ${count===1?'It was':'They were'} skipped, not run late.</p>`;
  }
  return `<p class="schedule-note failed">Last time failed (${e(last.scheduled_local)}): ${e(detail.reason||'the run could not start')}. <button class="text-button" type="button" data-action="edit-schedule">Edit the schedule</button></p>`;
}

// Runs and the scheduled times that did not start one, newest first. A time
// that started a run is that run, and is not listed twice.
export function historyRows(runs=[],slots=[]){
  const rows=[...runs.map(run=>({kind:'run',...run,at:run.created_at})),
    ...slots.filter(slot=>slot.outcome!=='started').map(slot=>({kind:'slot',...slot,at:slot.scheduled_for}))];
  return rows.sort((a,b)=>Date.parse(b.at)-Date.parse(a.at));
}

// How a run in history was started: "Scheduled · Mon 16 Nov, 09:00", shown
// in the schedule's own zone, or "Started manually".
export function triggerText(run,zone){
  const trigger=run?.trigger;
  if(!trigger)return '';
  // An event says which one: the identifier its sender gave, or its body's hash.
  if(trigger.kind==='event')return `Event · ${trigger.event_id}`;
  if(trigger.kind!=='schedule')return 'Started manually';
  return `Scheduled · ${localText(trigger.scheduled_for,zone)}`;
}

// "Mon 16 Nov, 09:00" in a zone, the host's own format.
export function localText(at,zone){
  try{
    const parts=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{weekday:'short',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:zone||undefined}).formatToParts(new Date(at)).map(part=>[part.type,part.value]));
    return `${parts.weekday} ${parts.day} ${parts.month}, ${parts.hour}:${parts.minute}`;
  }catch{return String(at||'');}
}

export function slotText(slot){
  const detail=slot.detail||{};
  if(slot.outcome==='skipped')return {tag:`Skipped · ${slot.scheduled_local||''}`,detail:'The previous run was still going'};
  if(slot.outcome==='missed')return {tag:detail.count>1?`Missed ${detail.count} times`:`Missed once · ${slot.scheduled_local||''}`,detail:`The host was off${detail.first_local?` from ${detail.first_local}`:''}${detail.last_local&&detail.count>1?` to ${detail.last_local}`:''}`};
  return {tag:`Failed · ${slot.scheduled_local||''}`,detail:detail.reason||'The run could not start'};
}
