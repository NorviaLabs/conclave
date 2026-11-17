// Uncertain external actions on a run: what was being sent when it stopped,
// and a person's choice about each one — it happened (mark done), or send it
// again — undoable until Continue. A floating panel over the run, never a modal.
//
// Pure functions of the host's action list (GET /api/runs/{id}/actions), so
// they can be checked without a browser. Nothing here decides anything: the
// host records decisions, and only these words and buttons come from here.

const TAGS={decide:'Needs your decision',check:'Conclave will check','done-you':'Done · you','done-check':'Done · checked',resend:'Will be sent again',unknown:'Outcome unknown'};
const ICONS={
  decide:'<path d="M12 6v8M12 18v.5"/>',check:'<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  'done-you':'<path d="M5 12.5l4.5 4.5L19 7.5"/>','done-check':'<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  resend:'<path d="M4 12h14M13 6l6 6-6 6"/>',unknown:'<circle cx="12" cy="12" r="7"/>'
};
const icon=kind=>`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${ICONS[kind]||ICONS.unknown}</svg>`;
const plural=(count,one,many)=>`${count} ${count===1?one:many}`;
const words=name=>String(name||'').replaceAll('_',' ').replace(/^./,first=>first.toUpperCase());

// "GitHub · Create pull request" when the host named the service; otherwise
// the connection id, or the tool's own name in words.
export function actionLabel(action){
  if(action?.label)return action.label;
  const parts=String(action?.tool||'').split('.');
  return parts.length===3&&parts[0]==='connection'?`${parts[1]} · ${words(parts[2])}`:words(action?.tool);
}

// What the panel shows for one action. A check that already ran and could not
// tell hands the choice to a person; a run that cannot continue only records.
export function actionKind(action,{resumable=true,deciding=[]}={}){
  const state=action?.state;
  if(state==='completed'||state==='failed')return state;
  // Who settled it is the kind the host recorded, never a name: a person
  // called "check" is a person.
  if(state==='done')return (action.settled_kind||(action.settled_by==='check'?'check':'person'))==='check'?'done-check':'done-you';
  if(state==='resend')return 'resend';
  if(state!=='uncertain'||!resumable)return 'unknown';
  return action.check?.available&&!action.check?.result&&!deciding.includes(action.id)?'check':'decide';
}

// What the panel lists. On a run that can no longer continue, only what is
// still uncertain stays on the record; what was settled has been acted on.
const open=(actions,options)=>actions.filter(action=>options?.resumable===false?action.state==='uncertain'
  :!['completed','failed'].includes(actionKind(action,options)));

// Continue waits for every action a person must decide; checks run on Continue.
export function continueGate(actions,options={}){
  const pending=actions.filter(action=>actionKind(action,options)==='decide');
  if(pending.length)return {enabled:false,reason:pending.length===1?`Decide ${actionLabel(pending[0])} to continue.`:`Decide ${pending.length} actions to continue.`,pending:pending.map(action=>action.id)};
  const checking=actions.filter(action=>actionKind(action,options)==='check');
  const reason=!checking.length?'Every action is settled.':checking.length===1?`Conclave checks ${actionLabel(checking[0])} first, then the run carries on.`:`Conclave checks ${checking.length} actions first, then the run carries on.`;
  return {enabled:true,reason,pending:[]};
}

function when(value){
  const date=value?new Date(value):null;
  return date&&!Number.isNaN(date.getTime())?date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';
}

function cardMarkup(action,kind,{escape:e,confirm,busy,agentName}){
  // The service by name when the host gave one ("Slack · Send message");
  // a local tool has none, so the words stay general.
  const label=actionLabel(action),service=label.includes(' · ')?label.split(' · ')[0]:'',agent=agentName?agentName(action.agent):action.agent;
  const lookAt=service?`Look at ${e(service)}, then choose.`:'Check whether it happened, then choose.';
  const disabled=busy?' disabled':'';
  const connection=String(action.tool||'').split('.');
  const facts=[action.stage,agent,when(action.started_at)&&`started ${when(action.started_at)}`,
    connection.length===3&&connection[0]==='connection'?`connection <code>${e(connection[1])}</code>`:''].filter(Boolean)
    .map(item=>item.startsWith('connection <code>')?item:e(item)).join(' · ');
  const check=action.check||{};
  const why={
    decide:`${e(action.reason||'Conclave does not know whether this happened')}.${check.result?' Conclave checked and could not tell.':check.available?'':` ${e(check.unavailable_reason||'It cannot be checked automatically')}.`} ${lookAt}`,
    check:`${e(action.reason||'Conclave does not know whether this happened')}. ${e(check.description||'Conclave will check with the service')} when you continue. If it is there, it is marked done; if the service shows it never happened, it is sent once, under the approval already given.`,
    'done-you':`${e(action.settled_by)} marked this as done${when(action.settled_at)?` at ${e(when(action.settled_at))}`:''}. ${e(agent)} will be told it already happened.`,
    'done-check':'Conclave checked and found it, so it will not be sent again.',
    resend:`${e((action.settled_kind||(action.settled_by==='check'?'check':'person'))==='check'?'Conclave’s check showed it never happened, so it':`${action.settled_by} chose to send this again. It`)} is sent once, exactly as shown, when the run continues.`,
    unknown:`${e(action.reason||'It was being sent when the run ended')}. Conclave did not check it and will not send it again.${service?` Look at ${e(service)} if you need to know.`:''}`,
  }[kind];
  const evidence=kind==='done-you'&&action.note?`<p class="action-evidence found">Note: “${e(action.note)}”</p>`
    :check.summary&&kind!=='unknown'?`<p class="action-evidence ${e(check.result||'')}">${e(check.summary)}</p>`:'';
  const details=action.arguments!==undefined&&action.arguments!==null&&kind!=='done-you'&&kind!=='done-check'
    ?`<details ${kind==='decide'?'open':''}><summary>What was sent</summary><pre class="code">${e(JSON.stringify(action.arguments,null,2))}</pre></details>`:'';
  let choices='';
  const confirming=confirm?.id===action.id&&kind==='decide'?confirm.decision:null;
  if(confirming==='done')choices=`<div class="action-confirm"><p><strong>Mark as done.</strong> ${e(agent)} will be told you confirmed it happened. Nothing is sent ${service?`to ${e(service)}`:'again'}.</p><label>Note for ${e(agent)} · optional<textarea data-action-note maxlength="500" rows="2"></textarea></label><div class="action-choices"><button class="button decision" type="button" data-action-confirm="done" data-action-id="${e(action.id)}"${disabled}>Record as done</button><button class="button" type="button" data-action-back>Back</button></div><p class="field-help">Your note is passed on as your words, not as ${service?`${e(service)}’s`:'the service’s'} reply. You can undo this until you press Continue.</p></div>`;
  else if(confirming==='resend')choices=`<div class="action-confirm"><p><strong>Send this again?</strong> It is sent once, exactly as shown above, when the run continues. Choosing this is your approval for that one call.</p><div class="action-choices"><button class="button decision" type="button" data-action-confirm="resend" data-action-id="${e(action.id)}"${disabled}>Send it again when I continue</button><button class="button" type="button" data-action-back>Back</button></div><p class="field-help">If it already happened, it will happen twice. You can undo this until you press Continue.</p></div>`;
  else if(kind==='decide')choices=`<div class="action-choices">${check.available&&check.result?`<button class="button" type="button" data-action-check="${e(action.id)}"${disabled}>Check again</button>`:''}<button class="button" type="button" data-action-choose="done" data-action-id="${e(action.id)}"${disabled}>It happened — mark done</button><button class="button" type="button" data-action-choose="resend" data-action-id="${e(action.id)}"${disabled}>Send it again</button></div>`;
  else if(kind==='check')choices=`<div class="action-choices"><button class="text-button" type="button" data-action-decide-self="${e(action.id)}">Decide yourself instead</button></div>`;
  else if(action.undoable)choices=`<div class="action-choices"><button class="text-button" type="button" data-action-undo="${e(action.id)}"${disabled}>Undo</button><p class="field-help">You can undo this until you press Continue.</p></div>`;
  const state=kind==='decide'?'decide':kind==='check'?'check':kind==='unknown'?'unknown':'decided';
  const tag=kind.startsWith('done')?'done':kind;
  return `<li class="action-card" data-state="${state}" data-action-card="${e(action.id)}"><div class="action-card-head"><strong>${e(label)}</strong><span class="action-state ${tag}">${icon(kind)}${TAGS[kind]}</span></div><p class="action-facts">${facts}</p><p class="action-why">${why}</p>${evidence}${details}${choices}</li>`;
}

const ORDER={decide:0,check:1,resend:2,'done-you':3,'done-check':4,unknown:5};

// The floating panel. `confirm` is the choice being confirmed ({id, decision});
// `deciding` the checkable actions a person chose to decide themselves.
export function actionPanelMarkup(actions,options={}){
  const {escape:e,resumable=true,minimised=false,notice='',busy=false,deciding=[]}=options;
  const kinds={resumable,deciding},visible=open(actions||[],kinds);
  if(!visible.length)return '';
  if(minimised)return actionPillMarkup(visible,options);
  const kindOf=action=>actionKind(action,kinds);
  const sorted=[...visible].sort((a,b)=>ORDER[kindOf(a)]-ORDER[kindOf(b)]);
  const cards=sorted.map(action=>cardMarkup(action,kindOf(action),{...options,escape:e})).join('');
  const minimise=`<button class="icon-button action-minimise" type="button" data-action-minimise aria-expanded="true" aria-controls="action-check-list" title="Minimise" aria-label="Minimise the action panel"><svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M6 12h12"/></svg></button>`;
  const noticeMarkup=notice?`<p class="action-notice" role="status">${e(notice)}</p>`:'';
  if(!resumable){
    const title=`${plural(visible.length,'action has','actions have')} an unknown outcome`;
    return `<section class="action-check floating informational" role="region" aria-labelledby="action-check-title"><header><div class="action-check-title"><h2 id="action-check-title" tabindex="-1">${e(title)}</h2>${minimise}</div><p>This run cannot be continued. This is shown so the record says what may have happened.</p></header><ol class="action-list" id="action-check-list">${cards}</ol></section>`;
  }
  const decides=visible.filter(action=>kindOf(action)==='decide').length,checks=visible.filter(action=>kindOf(action)==='check').length;
  const [title,intro]=!decides?['Ready to continue','Every action is decided or will be checked. Your choices can be undone until you press Continue.']
    :checks?[`Check ${decides+checks} actions before continuing`,'Conclave does not know whether these happened. Nothing is sent again until each one is settled: Conclave checks the ones it can, and you decide the rest.']
    :[`${plural(decides,'action needs','actions need')} your decision`,decides===1?'Conclave does not know whether this happened, and cannot settle it on its own. Nothing is sent again until you decide.'
      :'Conclave does not know whether these happened, and cannot settle them on its own. Nothing is sent again until you decide.'];
  const gate=continueGate(visible,kinds);
  return `<section class="action-check floating${decides?'':' ready'}" role="region" aria-labelledby="action-check-title"><header><div class="action-check-title"><h2 id="action-check-title" tabindex="-1">${e(title)}</h2>${minimise}</div><p>${e(intro)}</p></header>${noticeMarkup}<ol class="action-list" id="action-check-list">${cards}</ol><div class="action-footer"><button class="button primary" type="button" data-action="resume-live"${gate.enabled&&!busy?'':' disabled'}>Continue</button><p>${e(gate.reason)}</p></div></section>`;
}

export function actionPillMarkup(actions,{escape:e,resumable=true,deciding=[]}={}){
  const kinds={resumable,deciding},visible=open(actions||[],kinds);
  if(!visible.length)return '';
  const decides=visible.filter(action=>actionKind(action,kinds)==='decide').length,checks=visible.filter(action=>actionKind(action,kinds)==='check').length;
  const text=!resumable?`${plural(visible.length,'outcome','outcomes')} unknown`
    :checks?`${plural(decides+checks,'action','actions')} to check${decides?` · ${decides} need${decides===1?'s':''} your decision`:''}`
    :decides?`${plural(decides,'action needs','actions need')} your decision`:'Ready to continue';
  const kind=resumable?'decide':'unknown';
  return `<button class="action-pill ${kind}" type="button" data-action-reopen aria-expanded="false" aria-controls="action-check-list">${icon(kind)}<span>${e(text)}</span></button>`;
}

// The run's status line: one short phrase for what is waiting, or nothing.
export function runAttention(counts){
  if(!counts)return null;
  const {to_check:toCheck=0,needs_decision:needs=0,unknown=0}=counts;
  if(toCheck)return {text:`${plural(toCheck+needs,'action','actions')} to check`,level:'decide'};
  if(needs)return {text:`${plural(needs,'action needs','actions need')} your decision`,level:'decide'};
  if(unknown)return {text:`${plural(unknown,'outcome','outcomes')} unknown`,level:'unknown'};
  return null;
}

export function historyBadgeMarkup(counts,escape){
  const attention=runAttention(counts);
  if(!attention)return '';
  if(attention.level==='unknown')return `<span class="action-state unknown">${escape(attention.text)}</span>`;
  return `<span class="run-attention">Needs your check · ${escape(String((counts.to_check||0)+(counts.needs_decision||0)))}</span>`;
}

// The steps whose actions are still uncertain, read from the run's own events:
// a decision settles one, and undoing the decision opens it again.
export function uncertainStages(events){
  const stages=new Map(),open=new Set();
  for(const event of events||[]){
    if(event.type==='ActionUncertain'){stages.set(event.action_id,event.stage);open.add(event.action_id);}
    else if(event.type==='ActionSettled')open.delete(event.action_id);
    else if(event.type==='ActionDecisionUndone'&&stages.has(event.action_id))open.add(event.action_id);
  }
  return [...new Set([...open].map(id=>stages.get(id)))];
}
