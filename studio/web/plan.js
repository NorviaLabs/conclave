import {validateDefinition,validateMember,planConnections,executionOrder,canvasPositions,preflightIssues,backendIssue,stageBackend} from '/definition.js';

function newId(prefix,items,key='id'){let n=items.length+1;while(items.some(item=>item[key]===prefix+'_'+n))n++;return prefix+'_'+n;}
export function reorderPlan(definition,stepId,slot){
  const d=structuredClone(definition),from=d.workflow.findIndex(step=>step.id===stepId);d.connections=structuredClone(planConnections(definition));
  if(from<0||!Number.isInteger(slot)||slot<0||slot>d.workflow.length)throw new Error('Choose a valid step and drop position.');
  const to=slot>from?slot-1:slot,[step]=d.workflow.splice(from,1);d.workflow.splice(to,0,step);validateDefinition(d);return d;
}
export function duplicatePlanStep(definition,stepId){
  const d=structuredClone(definition),index=d.workflow.findIndex(step=>step.id===stepId);d.connections=structuredClone(planConnections(definition));if(index<0)throw new Error('Choose an existing step.');
  const original=d.workflow[index],copy={...original,id:newId('step',d.workflow),name:(original.name+' copy').slice(0,100)};d.workflow.splice(index+1,0,copy);if(d.connections!==undefined)d.connections.push(...d.connections.filter(e=>e.to===stepId).map(e=>({from:e.from,to:copy.id})));if(d.canvas){const pos=canvasPositions(definition)[stepId];d.canvas.positions[copy.id]={x:pos.x,y:Math.min(10000,pos.y+280)};}validateDefinition(d);return d;
}
export function removePlanStep(definition,stepId){
  const d=structuredClone(definition);d.connections=structuredClone(planConnections(definition));if(d.workflow.length<2)throw new Error('Keep at least one work step.');if(!d.workflow.some(step=>step.id===stepId))throw new Error('Choose an existing step.');d.workflow=d.workflow.filter(step=>step.id!==stepId);d.connections=d.connections.filter(e=>e.from!==stepId&&e.to!==stepId);if(d.canvas)delete d.canvas.positions[stepId];validateDefinition(d);return d;
}
export function insertPlanStep(definition,kind,slot){
  const d=structuredClone(definition);d.connections=structuredClone(planConnections(definition));if(!['ai','human'].includes(kind)||!Number.isInteger(slot)||slot<0||slot>d.workflow.length)throw new Error('Choose a valid step type and position.');
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

// A connection is only accepted if the plan can still be executed in some
// order: the canvas draws every edge, so a loop would leave nothing to render.
export function connectPlanSteps(definition,from,to){const d=structuredClone(definition);d.connections=structuredClone(planConnections(d));if(d.connections.some(e=>e.from===from&&e.to===to))return d;d.connections.push({from,to});executionOrder(d);validateDefinition(d);return d;}
export function disconnectPlanSteps(definition,from,to){const d=structuredClone(definition);d.connections=planConnections(d).filter(e=>e.from!==from||e.to!==to);validateDefinition(d);return d;}
export function moveCanvasStep(definition,id,x,y){const d=structuredClone(definition);d.connections=structuredClone(planConnections(d));d.canvas={positions:structuredClone(canvasPositions(d))};d.canvas.positions[id]={x:Math.round(Math.max(0,Math.min(10000,x))),y:Math.round(Math.max(0,Math.min(10000,y)))};validateDefinition(d);return d;}
export function tidyCanvas(definition){const d=structuredClone(definition);delete d.canvas;d.canvas={positions:canvasPositions(d)};return validateDefinition(d);}

// The arrangement is stored with the definition, so keeping it means writing the
// definition back. Only the positions travel: work the person has not finished
// editing stays in their draft, and an arrangement that places a step the
// application does not hold yet waits for the next save.
export function layoutSaveDefinition(saved,draft){
  if(!draft?.canvas)throw new Error('There is no arrangement to save.');
  return validateDefinition({...structuredClone(saved),canvas:structuredClone(draft.canvas)});
}

// Insertion changes only the chosen dependency, never neighbouring list entries.
export function placeInsertedStep(definition,id,{after=null,before=null,parallel=null}={}){
  const d=structuredClone(definition);d.connections=structuredClone(planConnections(definition));
  if(!d.workflow.some(step=>step.id===id))throw new Error('Choose an existing step.');
  if(parallel){
    for(const edge of [...d.connections])if(edge.to===parallel)d.connections.push({from:edge.from,to:id});
  }else{
    if(after&&before)d.connections=d.connections.filter(edge=>edge.from!==after||edge.to!==before);
    if(after)d.connections.push({from:after,to:id});
    if(before)d.connections.push({from:id,to:before});
  }
  if(d.canvas){const point=canvasPositions(definition)[after||parallel||before];if(point)d.canvas.positions[id]={x:Math.min(10000,point.x+(parallel?0:410)),y:Math.min(10000,point.y+(parallel?280:0))};}
  return validateDefinition(d);
}
export function copyStepMember(definition,id){
  const d=structuredClone(definition),step=d.workflow.find(item=>item.id===id),member=d.agents.find(item=>item.id===step?.agent);
  if(!member)throw new Error('Select a step.');
  const copy={...structuredClone(member),id:newId('member',d.agents),name:(member.name+' copy').slice(0,100)};
  d.agents.push(copy);step.agent=copy.id;return validateDefinition(d);
}
export function planReadiness(d,runtime={}){
  const issues=[];
  for(const step of d.workflow){
    if(!step.name?.trim())issues.push({stage:step.id,severity:'error',message:'Give this step a name.'});
    if(!step.task?.trim())issues.push({stage:step.id,severity:'error',message:'Describe what happens in this step.'});
    if(!d.agents.some(member=>member.id===step.agent))issues.push({stage:step.id,severity:'error',message:'Choose a team member.'});
    if(!d.state.some(output=>output.key===step.output))issues.push({stage:step.id,severity:'error',message:'Choose an expected output.'});
  }
  try{validateDefinition(d);issues.push(...preflightIssues(d));}catch(error){issues.push({severity:'error',message:error.message});}
  for(const step of d.workflow){
    const member=d.agents.find(item=>item.id===step.agent);if(!member||member.kind==='human')continue;
    const missing=Array.isArray(runtime.tools)?member.tools.filter(tool=>!runtime.tools.includes(tool)):[];
    if(missing.length)issues.push({stage:step.id,severity:'error',message:`Required tools unavailable on this host: ${missing.join(', ')}.`});
    if(runtime.backends?.length){const message=backendIssue(runtime.backends,stageBackend(member,step)[0]);if(message)issues.push({stage:step.id,severity:'error',message});}
  }
  if(runtime.available===false&&d.workflow.some(step=>d.agents.find(member=>member.id===step.agent)?.kind!=='human'))issues.push({severity:'error',message:runtime.reason||'No AI provider is available on this host. Configure a provider before running.'});
  return issues;
}
