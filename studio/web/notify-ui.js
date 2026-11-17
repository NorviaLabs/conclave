// "When a run waits for someone" in Studio: the settings section, its preview
// and warnings, and how a send is reported on the run.
//
// Pure functions of the definition, the viewer's own connections
// (GET /api/connections), the host's notification status
// (GET /api/applications/{id}/notifications) and its preview
// (POST /api/notifications/preview). The preview is built by the host with the
// same function it sends with, so what is shown is what goes out.

const CHANNELS=[['off','Off'],['slack','Slack'],['email','Email'],['webhook','Webhook']];
const SERVICE={slack:'slack',email:'gmail',webhook:'webhooks'};
const OPERATION={slack:'send_message',email:'send_email',webhook:'send'};
export const CHANNEL_LABEL={slack:'Slack',email:'Email',webhook:'Webhook'};
const TICK='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const BANG='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M12 6v8M12 18v.5"/></svg>';
const RING='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="7"/></svg>';
const BELL='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20.5a2 2 0 0 0 4 0"/></svg>';

const waitingBlock=definition=>definition?.notifications?.waiting||null;
const ownOf=(connections,via)=>(connections||[]).filter(item=>item.integration===SERVICE[via]);
const granted=(connection,via,applicationId)=>!!connection&&(connection.applications||[]).includes(applicationId)&&(connection.operations||[]).includes(OPERATION[via]);

// A new setting for a channel: the first of your own connections of that service.
export function defaultNotification(via,connections=[]){
  const first=ownOf(connections,via)[0]?.id||'';
  return via==='slack'?{via,connection:first,channel:''}:via==='email'?{via,connection:first,address:''}:{via,connection:first};
}

const warning=(kind,title,body)=>({kind,title,body,text:`${title} ${body}`});
// What would stop the message, said before saving.
export function notifyWarnings(definition,{applicationId='',connections=[],status=null,viewer='',runtime={}}={}){
  const block=waitingBlock(definition),warnings=[];
  if(!block)return warnings;
  const chosen=ownOf(connections,block.via).find(item=>item.id===block.connection);
  if(!chosen&&status?.owner&&status.owner!==viewer)
    warnings.push(warning('owner','Saving makes notifications yours.',`They are set up with ${block.connection}, which is ${status.owner}'s. After you save, messages are sent as you, and ${block.connection} cannot be used. Choose one of your own connections, or turn notifications off.`));
  else if(chosen&&!granted(chosen,block.via,applicationId))
    warnings.push(warning('grant','Not granted yet.',`${chosen.name||chosen.id} is not granted to this application, so no message can be sent until it is. Messages that cannot be sent are recorded on the run.`));
  if(runtime?.features?.studio_url===false)
    warnings.push(warning('address','This host has no Studio address.','Messages need a link to the run, and this host does not know where Studio is served from. Whoever runs the host sets CONCLAVE_STUDIO_URL. Until then, nothing is sent, and each waiting point records why.'));
  return warnings;
}

// The preview as Studio shows it: Slack's own markup read the way Slack
// reads it, and links shown as text — a preview is not somewhere to go.
function slackPreview(text,e){
  const unslack=value=>value.replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
  return String(text||'').split(/<(https?:[^>|]+)>/).map((part,index)=>index%2
    ?`<span class="notify-link">${e(part)}</span>`
    :e(unslack(part)).replace(/\*([^*]+)\*/g,'<strong>$1</strong>')).join('');
}
const linkText=(text,e)=>e(text).replace(/(https?:\/\/[^\s<]+)/g,'<span class="notify-link">$1</span>');

export function notifyPreviewMarkup({preview=null,previewError='',escape:e}={}){
  if(previewError)return `<p class="notify-error" role="alert">${e(previewError)}</p>`;
  if(!preview)return '';
  let body='';
  if(preview.via==='slack')body=`<div class="notify-message">${slackPreview(preview.text,e)}</div>`;
  else if(preview.via==='email')body=`<div class="notify-message email"><p class="notify-subject"><span>Subject</span> ${e(preview.subject)}</p><p>${linkText(preview.text||'',e)}</p></div>`;
  else body=`<pre class="code notify-json">${e(JSON.stringify(preview.payload||{},null,2))}</pre><p class="field-help">Sent with <code>Idempotency-Key: notice-&lt;run&gt;-&lt;event&gt;</code>, so a receiver can drop a repeat.</p>`;
  return `<section class="notify-preview" aria-live="polite"><h4>The message</h4>${body}<p class="field-help">Nothing from the run is included: no output, request values or credentials. A pause names the next step: “is paused before …”.</p></section>`;
}

export function notifySummary(definition){
  const block=waitingBlock(definition);
  if(!block)return 'off';
  return `${CHANNEL_LABEL[block.via]||block.via}${block.channel?` · ${block.channel}`:block.address?` · ${block.address}`:''}`;
}

export function notifyEditorMarkup(definition,{applicationId='',connections=[],status=null,preview=null,previewError='',viewer='',warnings=[],escape:e}={}){
  const block=waitingBlock(definition),via=block?.via||'off';
  const choice=`<div class="field"><span class="field-label" id="notify-via-label">Send a message through</span><div class="segmented" role="group" aria-labelledby="notify-via-label">${CHANNELS.map(([value,label])=>`<button type="button" data-notify-via="${value}" aria-pressed="${value===via}">${label}</button>`).join('')}</div></div>`;
  const intro='<p class="field-help" id="notify-title">When a run stops for a review or a pause, one message goes out: the application, the step that waits and a link to the run.</p>';
  if(!block)return `<div class="notify-editor" role="group" aria-labelledby="notify-title">${intro}${choice}</div>`;
  const label=CHANNEL_LABEL[via],own=ownOf(connections,via),chosen=own.find(item=>item.id===block.connection);
  let connection;
  if(!own.length&&!block.connection)connection=`<p class="notify-none">You have no ${label} connection. <button class="text-button" type="button" data-action="manage-integrations">Connect an account</button></p>`;
  else if(!own.length)connection=`<p class="notify-none">You have no ${label} connection, and ${e(block.connection)} is someone else's. <button class="text-button" type="button" data-action="manage-integrations">Connect an account</button></p>`;
  else{
    const options=own.map(item=>`<option value="${e(item.id)}"${item.id===block.connection?' selected':''}>${e(item.name||item.id)} · ${e(item.id)} — ${granted(item,via,applicationId)?'granted to this application':'not granted to this application yet'}</option>`).join('');
    const foreign=chosen||!block.connection?'':`<option value="${e(block.connection)}" selected>${e(block.connection)} — not one of your connections</option>`;
    const state=chosen?(granted(chosen,via,applicationId)
      ?`<span class="grant ok">${TICK}Granted to this application</span>`
      :`<span class="grant missing">${BANG}Not granted to this application yet</span><button class="text-button" type="button" data-action="manage-integrations">Grant it</button>`):'';
    connection=`<div class="field"><label for="notify-connection">Connection</label><select id="notify-connection" aria-describedby="notify-grant notify-connection-help" data-notify-connection>${foreign}${!chosen&&!block.connection?'<option value="" selected>Choose a connection</option>':''}${options}</select><p class="notify-grant" id="notify-grant">${state}</p><p class="field-help" id="notify-connection-help">Only your own connections are listed: the message is sent from your account.</p></div>`;
  }
  const target=via==='slack'
    ?`<div class="field"><label for="notify-target">Channel</label><input id="notify-target" aria-describedby="notify-target-help" maxlength="80" data-notify-target value="${e(block.channel||'')}"><p class="field-help" id="notify-target-help">A channel name such as #reviews, or a channel ID. It is saved with the application, so anyone who can open it can see it.</p></div>`
    :via==='email'
    ?`<div class="field"><label for="notify-target">Address</label><input id="notify-target" type="email" aria-describedby="notify-target-help" maxlength="254" data-notify-target value="${e(block.address||'')}"><p class="field-help" id="notify-target-help">One address. The message comes from the connection's mailbox. The address is saved with the application, so anyone who can open it can see it.</p></div>`
    :`<p class="field-help">The message goes to the address the <em>${e(chosen?.name||block.connection)}</em> connection was set up with.</p>`;
  const warningMarkup=warnings.map(item=>`<div class="notify-warning" role="note"><strong>${e(item.title)}</strong> ${e(item.body)}</div>`).join('');
  const owner=status?.owner||viewer;
  const ownerLine=owner?`<p class="notify-owner">${BELL}<span>Sent as <strong>${e(owner)}</strong>${viewer&&owner!==viewer?' until you save; saving makes it yours':''}, whoever starts the run.</span></p>`:'';
  return `<div class="notify-editor" role="group" aria-labelledby="notify-title">${intro}${choice}${connection}${target}${warningMarkup}<div data-notify-preview-slot>${notifyPreviewMarkup({preview,previewError,escape:e})}</div>${ownerLine}</div>`;
}

// One send, in words: what the activity stream and the run's line say.
// run-activity.js keeps its own copy, so that module loads on its own.
export function noticeRowText(event){
  const target=event.target||event.connection||'the channel',step=event.step?.name,via=CHANNEL_LABEL[event.via]||event.via||'';
  const period=text=>/[.!?]$/.test(text)?text:`${text}.`;
  if(event.type==='NotificationFailed')return {tone:'failed',title:`Could not tell ${target}`,detail:`${period(event.reason||'The message could not be sent')} The run is not affected; nothing is sent again.`};
  if(event.type==='NotificationUncertain')return {tone:'unknown',title:`${target} may not have been told`,detail:`${period(event.reason||'The host stopped while sending')} It is not sent again, so nobody is told twice.`};
  const what=!step?'':event.waiting==='pause'?` that the run is paused before ${step}`:` that “${step}” is waiting`;
  return {tone:'sent',title:`Told ${target}${what}`,detail:[via,event.connection,event.sent_as?`sent as ${event.sent_as}`:''].filter(Boolean).join(' · ')};
}

// The small line on the run: whether the others were told.
export function runNoticeMarkup(events=[],{escape:e}={}){
  // From the end back to the latest send: a live run redraws this often, and
  // its list only grows.
  let event=null;
  for(let index=events.length-1;index>=0&&!event;index--){const raw=events[index],item=raw?.event||raw;if(String(item?.type||'').startsWith('Notification'))event=item;}
  if(!event)return '';
  const {tone}=noticeRowText(event),target=e(event.target||event.connection||'the channel');
  if(tone==='failed')return `<span class="run-notice failed">${BANG}Could not tell ${target}</span>`;
  if(tone==='unknown')return `<span class="run-notice unknown">${RING}${target} may not have been told</span>`;
  const date=event.timestamp?new Date(event.timestamp):null,time=date&&!Number.isNaN(date.getTime())?date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';
  return `<span class="run-notice sent">${TICK}Told ${target}${time?` at ${e(time)}`:''}</span>`;
}
