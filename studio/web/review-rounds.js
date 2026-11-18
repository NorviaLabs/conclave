// A review that asked for changes comes back to its reviewer as a new round.
// These say which round it is, what was asked before, and what "changes
// requested" will do, from the review request the run recorded.
const stepName=(d,id)=>d.workflow.find(step=>step.id===id)?.name||id;
const names=(d,ids)=>(ids||[]).map(id=>stepName(d,id));
const list=items=>items.length<2?items.join(''):`${items.slice(0,-1).join(', ')} and ${items.at(-1)}`;

export function revisionRoundMarkup(revision,{escape:e}){
  if(!revision?.round)return '';
  const of=Number.isInteger(revision.max)?` of ${revision.max+1}`:'';
  return ` <span class="revision-round">Round ${e(revision.round)}${e(of)}</span>`;
}

function roundMarkup(d,item,{escape:e},latest){
  const revised=names(d,item.revised);
  return `<span class="detail-label">${latest?'You asked for changes':'Changes asked for'} in round ${e(item.round)} · ${e(list(revised))} ran again</span><blockquote>${e(item.comment)}</blockquote>`;
}

export function revisionHistoryMarkup(d,revision,options){
  const history=revision?.history||[];
  if(!history.length)return '';
  const latest=history.at(-1),earlier=history.slice(0,-1).reverse();
  return `<div class="revision-history" role="note">${roundMarkup(d,latest,options,true)}${earlier.length?`<details class="quiet-details"><summary>Earlier rounds · ${earlier.length}</summary>${earlier.map(item=>roundMarkup(d,item,options,false)).join('')}</details>`:''}</div>`;
}

// What choosing "changes requested" will do, before it is recorded. Mirrors
// the host: the steps named in `revises`, or the AI steps feeding the review.
export function revisionTargets(d,step){
  if(Array.isArray(step.revises))return step.revises;
  const humans=new Set(d.agents.filter(member=>member.kind==='human').map(member=>member.id));
  const links=d.connections||d.workflow.slice(1).map((after,index)=>({from:d.workflow[index].id,to:after.id}));
  const direct=new Set(links.filter(edge=>edge.to===step.id).map(edge=>edge.from));
  return d.workflow.filter(item=>direct.has(item.id)&&!humans.has(item.agent)).map(item=>item.id);
}

export function revisionConsequenceMarkup(d,step,revision,{escape:e}){
  const targets=names(d,revisionTargets(d,step)),done=(revision?.round||1)-1,limit=Number.isInteger(step.max_revisions)?step.max_revisions:null;
  let text;
  if(!targets.length)text='There is no earlier step to send back, so requesting changes stops the run.';
  else if(limit!==null&&done>=limit)text=`This review has used its ${limit} ${limit===1?'round':'rounds'} of changes, so requesting changes stops the run.`;
  else{const after=limit===null?null:limit-done-1,left=after===null?'':` ${after===0?'This is the last request for changes this review allows; at the next round, requesting changes stops the run.':`${after} more ${after===1?'request':'requests'} for changes allowed after this one.`}`;
    text=`${list(targets)} ${targets.length===1?'runs':'run'} again with your reason, then this review comes back to you.${left}`;}
  return `<p class="revision-consequence" role="status">${e(text)}</p>`;
}

// How many people a review needs, who has answered and how, and why the person
// looking may not answer (the host's reason). One pattern for any number.
const DECISION_WORDS={approved:'Approved',rejected:'Rejected',changes_requested:'Changes requested',answered:'Answered'};
const DECISION_MARKS={
  approved:'<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M4.6 8.3l2.2 2.2 4.6-4.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  rejected:'<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  changes_requested:'<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5 8h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
};
DECISION_MARKS.answered=DECISION_MARKS.changes_requested;
const WAITING_MARK='<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.6 2.2"/></svg>';
const decisionOf=output=>{const statuses=(output?.records||[]).map(record=>record?.status);return ['rejected','changes_requested','approved'].find(status=>statuses.includes(status))||'answered';};
const bodyOf=output=>typeof output?.text==='string'?output.text:(output?.records||[]).map(record=>record?.body||'').filter(Boolean).join(' · ');
const timeOf=at=>{const date=at?new Date(at):null;return date&&!Number.isNaN(date.getTime())?date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';};

export function answersProgressMarkup(review,{escape:e}){
  const required=review?.required||1,given=(review?.answers||[]).length;
  if(required<2&&!given)return '';
  return ` <span class="answers-progress">${e(given)} of ${e(required)} answers</span>`;
}
export function answersMarkup(review,{escape:e,viewer='',shown=2}){
  const required=review?.required||1,answers=review?.answers||[];
  if(required<2&&!answers.length)return '';
  const row=item=>{const decision=decisionOf(item.output),body=bodyOf(item.output),mine=viewer&&item.reviewer.toLowerCase()===viewer.toLowerCase();
    return `<li><span class="check-${decision==='approved'?'found':decision==='rejected'?'not_found':'not_checkable'}">${DECISION_MARKS[decision]}</span><span><strong>${mine?'You':e(item.reviewer)}</strong> ${decision==='changes_requested'?'asked for changes':decision==='answered'?'answered':e(decision)}${body?`: “${e(body.length>160?body.slice(0,159)+'…':body)}”`:''}</span><span class="decision ${decision}">${e(DECISION_WORDS[decision])}</span>${timeOf(item.submitted_at)?`<time datetime="${e(item.submitted_at)}">${e(timeOf(item.submitted_at))}</time>`:''}</li>`;};
  const latest=answers.slice(-shown).reverse(),earlier=answers.slice(0,-shown).reverse(),left=Math.max(0,required-answers.length);
  const meter=required>1?`<div class="answers-meter" role="img" aria-label="${e(answers.length)} of ${e(required)} answers recorded"><span style="width:${Math.min(100,Math.round(answers.length/required*1000)/10)}%"></span></div>`:'';
  return `${meter}${latest.length?`<ul class="answer-list" aria-label="Answers so far">${latest.map(row).join('')}</ul>`:''}${earlier.length?`<details class="quiet-details"><summary>${e(earlier.length)} more ${earlier.length===1?'answer':'answers'}</summary><ul class="answer-list">${earlier.map(row).join('')}</ul></details>`:''}${left?`<ul class="answer-list"><li class="waiting">${WAITING_MARK}<span>${e(left)} more ${left===1?'person':'people'} needed</span><span></span></li></ul>`:''}`;
}
// The host's reason the person looking may not answer, in place of the form.
const whoMay=list=>{const words=list.map(entry=>entry.startsWith('@')?`anyone at ${entry}`:entry);return words.length<2?words.join(''):`${words.slice(0,-1).join(', ')} or ${words.at(-1)}`;};
export function answerBlockedMarkup(standing,review,policy,{escape:e}){
  if(!standing||standing.allowed!==false)return '';
  const left=Math.max(0,(review?.required||1)-(review?.answers||[]).length);
  const next=standing.reason.startsWith('You already')
    ?`The run goes on by itself once ${left} more ${left===1?'person answers':'people answer'}.`
    :`Who may: ${policy?.reviewers?.length?whoMay(policy.reviewers):'anyone else who can open this run'}. Share this run with them; Studio does not tell them.`;
  return `<div class="review-blocked" role="note"><strong>${e(standing.reason)}.</strong><p>${e(next)}</p></div>`;
}
// The policy a run is held to, from the saved version it ran (a later edit does
// not change a run), and, where the host's names are not verified, that they are not.
export function policyInForceMarkup(policy,{escape:e,revision,verified}){
  if(!policy)return '';
  const who=policy.reviewers?.length?whoMay(policy.reviewers):'anyone who can open this run';
  const starter=policy.allow_starter?'The person who started this run may answer too.':'The person who started this run may not answer.';
  const note=verified?'':'<p class="identity-note" role="note">Names on this host are not verified: anyone using this computer can answer under another name. Sign-in makes these rules hold.</p>';
  return `<p class="policy-in-force">Who may answer: ${e(who)}. ${e(starter)} Set by saved version ${e(revision)}.</p>${note}`;
}
