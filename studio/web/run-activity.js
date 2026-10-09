import {uiIcon} from '/canvas-ui.js';
// Descriptions use only durable events; routine repeated tool calls share a row.
export function runActivities(d,run,{agentName,capabilityName}){
  const rows=[];
  for(const event of run?.events||[]){
    const step=d.workflow.find(item=>item.id===event.stage),agent=agentName(d,event.agent||step?.agent||event.delegated_by||'Team');
    let text='',detail='',important=false,routine=false;
    switch(event.type){
      case 'ApplicationStarted':text='Team started the request';break;
      case 'StageStarted':text=`${agent} started ${step?.name||event.stage}`;break;
      case 'ApplicationStateChanged':{
        const value=event.patch,label=d.state.find(item=>item.key===step?.output)?.label||'output';
        text=`${agent} completed ${step?.name||event.stage}`;
        detail=Array.isArray(value)?`${value.length} ${label.toLowerCase()}`:typeof value==='string'?`${value.trim().split(/\s+/).filter(Boolean).length} words produced`:'Output recorded';break;
      }
      case 'Delegation':text=`${agentName(d,event.delegated_by||step?.agent)} delegated work to ${agent}`;detail=event.task||event.reason||'';break;
      case 'ToolCalled':text=`${agent} used ${capabilityName(event.tool)}`;routine=event.ok!==false;important=event.ok===false;if(important)text=`${agent}: ${capabilityName(event.tool)} failed`;break;
      case 'ToolApprovalRequested':text=`${agent} is waiting for approval`;detail=capabilityName(event.tool);important=true;break;
      case 'ToolApprovalResolved':text=event.approved?'External action approved':'External action rejected';detail=event.reason||'';important=true;break;
      case 'ToolDenied':text=`${agent}: ${capabilityName(event.tool)} needs attention`;detail=event.reason||'';important=true;break;
      case 'NoteRecorded':text=`${agent} recorded a finding`;detail=event.note||'';break;
      case 'HumanInputRequested':text=`${agent} needs your input`;important=true;break;
      case 'HumanReviewSubmitted':text=`${event.reviewer||agent} submitted a review`;break;
      case 'ApplicationFinished':text=`Request ${String(event.status||'finished').replaceAll('_',' ')}`;detail=event.reason||'';important=event.status!=='completed';break;
      case 'BudgetWarning':case 'BudgetExceeded':text=`${agent}: ${String(event.dimension||'execution').replaceAll('_',' ')} ${event.type==='BudgetExceeded'?'limit reached':'warning'}`;important=true;break;
      default:continue;
    }
    if(event.connection_id)detail=`Connection ${event.connection_id} · ${String(event.operation||'').replaceAll('_',' ')}${detail?' · '+detail:''}`;
    const previous=rows.at(-1);
    if(routine&&previous?.routine&&previous.stage===event.stage&&previous.agent===event.agent&&previous.tool===event.tool){previous.count++;previous.text=`${agent} used ${capabilityName(event.tool)} · ${previous.count} calls`;previous.events.push(event);previous.timestamp=event.timestamp||previous.timestamp;continue;}
    rows.push({id:event.sequence??rows.length,stage:event.stage,agent:event.agent,tool:event.tool,text,detail,important,routine,count:1,timestamp:event.timestamp||event.submitted_at,events:[event]});
  }
  return rows.reverse();
}
export function activityDockMarkup(d,run,{escape:e,agentName,capabilityName,expanded=false,filter=''}){
  const all=runActivities(d,run,{agentName,capabilityName}),rows=filter?all.filter(item=>item.stage===filter):all,latest=all.find(item=>!item.routine)||all[0];
  return `<section class="activity-dock ${expanded?'expanded':''}" aria-label="Run activity"><div class="activity-latest"><span class="${latest?.important?'activity-important':''}" role="status">● ${e(latest?.text||'Waiting for recorded activity')}${latest?.detail?` · ${e(latest.detail.slice(0,100))}`:''}</span><button type="button" class="text-button" data-action="toggle-activity" aria-expanded="${expanded}" aria-controls="run-activity-stream" title="${expanded?'Hide the activity stream':'Show the activity stream'}" aria-label="${expanded?'Hide the activity stream':'Show the activity stream'}">Activity ${uiIcon('chevron')}</button></div>${expanded?`<div class="activity-panel" id="run-activity-stream"><label>Filter by step <select data-activity-filter><option value="">All steps</option>${d.workflow.map(step=>`<option value="${e(step.id)}" ${filter===step.id?'selected':''}>${e(step.name)} · ${e(agentName(d,step.agent))}</option>`).join('')}</select></label><ol>${rows.map(row=>{
    const date=row.timestamp?new Date(row.timestamp):null,time=date&&!Number.isNaN(date.getTime())?date.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';
    return `<li data-activity-id="${e(row.id)}" class="${row.important?'activity-important':''} ${row.routine?'activity-routine':''}">${time?`<time datetime="${e(row.timestamp)}">${e(time)}</time>`:''}<div>${row.stage?`<button class="text-button" data-run-step="${e(row.stage)}">${e(row.text)}</button>`:`<strong>${e(row.text)}</strong>`}${row.detail?`<p>${e(row.detail)}</p>`:''}<details data-disclosure="activity-${e(row.id)}"><summary>Execution details</summary><pre class="code">${e(JSON.stringify(row.events,null,2))}</pre></details></div></li>`;
  }).join('')}</ol>${!rows.length?'<p>No recorded activity for this step.</p>':''}</div>`:''}</section>`;
}
