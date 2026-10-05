import {validateDefinition,validateMember,planConnections,canvasPositions} from '/definition.js';

function newId(prefix,items,key='id'){let n=items.length+1;while(items.some(item=>item[key]===prefix+'_'+n))n++;return prefix+'_'+n;}
export function reorderPlan(definition,stepId,slot){
  const d=structuredClone(definition),from=d.workflow.findIndex(step=>step.id===stepId);
  if(from<0||!Number.isInteger(slot)||slot<0||slot>d.workflow.length)throw new Error('Choose a valid step and drop position.');
  const to=slot>from?slot-1:slot,[step]=d.workflow.splice(from,1);d.workflow.splice(to,0,step);validateDefinition(d);return d;
}
export function duplicatePlanStep(definition,stepId){
  const d=structuredClone(definition),index=d.workflow.findIndex(step=>step.id===stepId);if(index<0)throw new Error('Choose an existing step.');
  const original=d.workflow[index],copy={...original,id:newId('step',d.workflow),name:(original.name+' copy').slice(0,100)};d.workflow.splice(index+1,0,copy);if(d.connections!==undefined)d.connections.push(...d.connections.filter(e=>e.to===stepId).map(e=>({from:e.from,to:copy.id})));if(d.canvas){const pos=canvasPositions(definition)[stepId];d.canvas.positions[copy.id]={x:Math.min(10000,pos.x+410),y:pos.y};}validateDefinition(d);return d;
}
export function removePlanStep(definition,stepId){
  const d=structuredClone(definition);if(d.workflow.length<2)throw new Error('Keep at least one work step.');if(!d.workflow.some(step=>step.id===stepId))throw new Error('Choose an existing step.');d.workflow=d.workflow.filter(step=>step.id!==stepId);if(d.connections!==undefined)d.connections=d.connections.filter(e=>e.from!==stepId&&e.to!==stepId);if(d.canvas)delete d.canvas.positions[stepId];validateDefinition(d);return d;
}
export function insertPlanStep(definition,kind,slot){
  const d=structuredClone(definition);if(!['ai','human'].includes(kind)||!Number.isInteger(slot)||slot<0||slot>d.workflow.length)throw new Error('Choose a valid step type and position.');
  let member=d.agents.find(agent=>(agent.kind||'ai')===kind),output=d.state.find(item=>kind==='human'?item.kind==='decision':item.type==='records');
  if(!member){member={id:newId('member',d.agents),name:kind==='human'?'Human reviewer':'Specialist',kind,role:kind==='human'?'approver':'analyst',instructions:kind==='human'?'Review the available findings and record an explicit decision with a reason.':'Complete the assigned work using the available findings.',tools:[]};d.agents.push(member);}
  if(!output&&kind==='human'){
    output={key:newId('decision',d.state,'key'),label:'Human review decision',kind:'decision',type:'records',merge:'upsert',statuses:['proposed','approved','rejected','changes_requested']};d.state.push(output);d.views.push({id:newId('view',d.views),title:output.label,collection:output.key,layout:'cards'});
  }
  if(!output)output=d.state[0];
  const step={id:newId('step',d.workflow),name:kind==='human'?'Human review':'New work step',agent:member.id,output:output.key,task:kind==='human'?'Accept, reject or request changes to the available results. Record the reason.':'Describe the work this step should complete.'};
  d.workflow.splice(slot,0,step);validateDefinition(d);return d;
}
export function insertMemberStep(definition,member,slot,{reuse=false}={}){
  const d=structuredClone(definition),normalized=validateMember(member);
  if(reuse){if(!d.agents.some(agent=>agent.id===normalized.id))throw new Error('Choose a member in this application.');}
  else{normalized.id=newId('member',d.agents);d.agents.push(normalized);}
  const next=insertPlanStep(d,normalized.kind,slot),step=next.workflow[slot];step.agent=normalized.id;step.name=(normalized.name+' contribution').slice(0,100);step.task=normalized.instructions.slice(0,4000);validateDefinition(next);return next;
}
export function attachPlanTool(definition,stepId,toolId){
  const d=structuredClone(definition),step=d.workflow.find(item=>item.id===stepId);if(!step)throw new Error('Select a step for this tool.');
  const member=d.agents.find(item=>item.id===step.agent);if(member.kind==='human')throw new Error('Human checkpoints do not execute agent tools.');
  if(!member.tools.includes(toolId))member.tools.push(toolId);validateDefinition(d);return d;
}

export function connectPlanSteps(definition,from,to){const d=structuredClone(definition);d.connections=structuredClone(planConnections(d));if(d.connections.some(e=>e.from===from&&e.to===to))return d;d.connections.push({from,to});validateDefinition(d);return d;}
export function disconnectPlanSteps(definition,from,to){const d=structuredClone(definition);d.connections=planConnections(d).filter(e=>e.from!==from||e.to!==to);validateDefinition(d);return d;}
export function moveCanvasStep(definition,id,x,y){const d=structuredClone(definition);d.connections=structuredClone(planConnections(d));d.canvas={positions:structuredClone(canvasPositions(d))};d.canvas.positions[id]={x:Math.round(Math.max(0,Math.min(10000,x))),y:Math.round(Math.max(0,Math.min(10000,y)))};validateDefinition(d);return d;}
export function tidyCanvas(definition){const d=structuredClone(definition);delete d.canvas;d.canvas={positions:canvasPositions(d)};return validateDefinition(d);}
