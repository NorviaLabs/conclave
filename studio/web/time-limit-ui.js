// A step's own time limit: set in its settings, shown on its card, and said
// wherever a run that ran over is read. The mockups are
// docs/studio/mockups/step-time-limits.html. Loads on its own.
const CLOCK_ICON='<svg aria-hidden="true" viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 4.6V8l2.4 1.6" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';
export const MIN_TIME_LIMIT=10,MAX_TIME_LIMIT=86400;
const LIMIT_RULE='Give a time limit of at least 10 seconds and at most 24 hours.';
const split=seconds=>{const whole=Math.max(0,Math.round(Number(seconds)||0));return [Math.floor(whole/3600),Math.floor(whole%3600/60),whole%60];};
const count=(value,word)=>`${value} ${word}${value===1?'':'s'}`;
// "5 m", "1 m 30 s", "2 h", "1 h 30 m": the same as the host's version comparison.
export function durationShort(seconds){
  const [hours,minutes,rest]=split(seconds);
  return [hours?`${hours} h`:'',minutes?`${minutes} m`:'',rest?`${rest} s`:''].filter(Boolean).join(' ')||'0 s';
}
// "5 minutes", "1 minute 1 second": what a screen reader says.
export function durationWords(seconds){
  const [hours,minutes,rest]=split(seconds);
  return [hours?count(hours,'hour'):'',minutes?count(minutes,'minute'):'',rest?count(rest,'second'):''].filter(Boolean).join(' ')||'0 seconds';
}
// How long a step worked, as a clock reads: "5 m 00 s".
const elapsedShort=seconds=>{const [hours,minutes,rest]=split(seconds),pad=value=>String(value).padStart(2,'0');return hours?`${hours} h ${pad(minutes)} m`:minutes?`${minutes} m ${pad(rest)} s`:`${rest} s`;};
// Shown short, heard in words: "5 m" is never read as "5 meters".
export function spokenDuration(seconds,{clock=false}={}){
  return `<span aria-hidden="true">${clock?elapsedShort(seconds):durationShort(seconds)}</span><span class="sr-only">${durationWords(seconds)}</span>`;
}
// The limit the inspector's boxes say: null for none, or whole seconds; refused when out of range.
export function parseTimeLimit(enabled,minutes,seconds){
  if(!enabled)return null;
  const whole=text=>String(text??'').trim()===''?0:Number(text);
  const m=whole(minutes),s=whole(seconds);
  if(![m,s].every(Number.isInteger)||m<0||s<0||s>59)throw new Error(LIMIT_RULE);
  const total=m*60+s;
  if(total<MIN_TIME_LIMIT||total>MAX_TIME_LIMIT)throw new Error(LIMIT_RULE);
  return total;
}
// The inspector's field. `issue` is what was wrong with the last value typed,
// and `typed` that value, kept in the boxes so it can be corrected.
export function timeLimitFieldMarkup(step,index,{escape:e,human=false,issue='',typed=null}){
  if(human)return '<section class="inspector-section"><h4>Time limit</h4><p class="field-help">A person’s step has no time limit: it waits for the person.</p></section>';
  const limit=step.time_limit_seconds,on=Number.isInteger(limit),[hours,minutes,seconds]=split(limit);
  const invalid=issue?` aria-invalid="true" aria-describedby="time-limit-error-${index}"`:'';
  const box=(kind,value,max,label)=>`<input type="number" min="0" max="${max}" step="1" value="${typed?e(typed[kind]??''):on?value:''}" data-time-limit-${kind}="${index}" aria-label="${label}"${invalid} ${on?'':'disabled'}>`;
  const help=on?'Counts the time it works — model calls, tools and members it asks — but not time waiting for someone to approve an action. When it runs over, it is stopped and the run stops after the steps beside it finish; Continue runs it again.'
    :'No limit: the step works until it finishes, within the run’s own limits.';
  return `<section class="inspector-section"><h4>Time limit</h4><div class="field time-limit-field" role="group" aria-label="Time limit for “${e(step.name||step.id)}”" aria-describedby="time-limit-help-${index}"><div class="time-limit-inputs"><label class="inline"><input type="checkbox" data-time-limit-enabled="${index}" ${on?'checked':''}> Stop this step if it runs longer than</label>${box('minutes',hours*60+minutes,1440,'Minutes')}<span class="unit">min</span>${box('seconds',seconds,59,'Seconds')}<span class="unit">s</span></div>${issue?`<p class="validation" id="time-limit-error-${index}" role="alert">${e(issue)}</p>`:''}<p class="field-help" id="time-limit-help-${index}">${help}</p></div></section>`;
}
// On the plan card: the limit at a glance.
export function timeLimitBadgeMarkup(step){
  const limit=step?.time_limit_seconds;
  if(!Number.isInteger(limit))return '';
  return `<span class="time-limit-badge" title="Time limit: ${durationWords(limit)}">${CLOCK_ICON}<span aria-hidden="true">${durationShort(limit)}</span><span class="sr-only">Time limit: ${durationWords(limit)}</span></span>`;
}
// In a timed-out step's details.
export function timedOutNoticeMarkup(outcome){
  return `<div class="timed-out-note" role="status">${CLOCK_ICON}<span><strong>Stopped after ${spokenDuration(outcome.elapsed,{clock:true})}:</strong> its time limit is ${spokenDuration(outcome.limit)}. The model call it was making was stopped, and nothing it had half-written was kept.</span></div>`;
}
// The run's note when a step ran over is timedOutNoteMarkup in parallel-ui.js,
// beside the run's other stop notes, which live.js draws.
