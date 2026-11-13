import {runStepStatus} from '/run-canvas.js';

// Before the person reviewing decides, each AI member's standing is read from
// the run record alone: done when every step they own has completed, stopped
// when one failed, still working otherwise.
export function crewPollMarkup(d,run,{escape:e}){
  const members=new Map();
  d.workflow.forEach((step,index)=>{const agent=d.agents.find(a=>a.id===step.agent);if(!agent||agent.kind==='human')return;const entry=members.get(agent.id)||{agent,indexes:[]};entry.indexes.push(index);members.set(agent.id,entry);});
  const labels={go:'Done',hold:'Stopped',standby:'Still working'},why={go:'Every step this member owns has completed',hold:'A step this member owns failed',standby:'This member has work still to finish'};
  const entries=[...members.values()].map(({agent,indexes},i)=>{const statuses=indexes.map(index=>runStepStatus(d,run,index)),call=statuses.includes('Failed')?'hold':statuses.every(status=>status==='Complete')?'go':'standby';
    return `<li class="poll-entry" data-call="${call}" style="--i:${i}" title="${why[call]}"><span class="poll-who"><strong>${e(agent.name)}</strong></span><span class="poll-call">${labels[call]}</span></li>`;});
  if(!entries.length)return '';
  return `<ol class="crew-poll" aria-label="Team status from the run record">${entries.join('')}<li class="poll-entry flight" data-call="yours" style="--i:${entries.length}"><span class="poll-who"><strong>You</strong></span><span class="poll-call">Your decision</span></li></ol>`;
}
