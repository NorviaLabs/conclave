import {validateDefinition,planConnections} from '/definition.js';
import {rolePresets,toolDescriptors,manualPlanning} from '/catalogue.js';
import {insertPlanStep,insertMemberStep,reorderPlan,attachPlanTool,placeInsertedStep} from '/plan.js';

const normal=value=>String(value).toLowerCase().trim().replace(/[.!?]+$/,'').replace(/\s+/g,' ');
function oneMatch(items,label,describe){
  const exact=items.filter(item=>[item.id,item.name].some(value=>normal(value)===normal(label)));
  const matches=exact.length?exact:items.filter(item=>normal(item.name).includes(normal(label)));
  if(matches.length!==1)throw new Error(matches.length?'Use the full '+describe+' name; that request matches several.':'No '+describe+' matches “'+label+'”.');return matches[0];
}
function targetStep(d,label){
  if(manualPlanning.approvalAliases.includes(normal(label))){const matches=d.workflow.filter(step=>d.agents.find(member=>member.id===step.agent)?.kind==='human');if(matches.length===1)return matches[0];}
  return oneMatch(d.workflow,label,'step');
}
export function proposePlanChange(definition,prompt){
  validateDefinition(definition);if(typeof prompt!=='string'||!prompt.trim()||prompt.length>1000)throw new Error('Enter a plan command of 1–1000 characters.');
  const text=normal(prompt);let next,changes=[],match;
  if((match=text.match(new RegExp(manualPlanning.commands.move)))){
    const step=targetStep(definition,match[1]),target=targetStep(definition,match[3]);if(step.id===target.id)throw new Error('Choose two different steps.');
    const slot=definition.workflow.findIndex(item=>item.id===target.id)+(match[2]==='after'?1:0);next=reorderPlan(definition,step.id,slot);changes.push('Move the list entry “'+step.name+'” '+match[2]+' “'+target.name+'” (connections stay unchanged).');
  }else if((match=text.match(new RegExp(manualPlanning.commands.tool)))){
    const tool=oneMatch(toolDescriptors,match[1],'tool'),member=oneMatch(definition.agents,match[2],'member'),step=definition.workflow.find(item=>item.agent===member.id);if(!step)throw new Error('Assign this member a step before adding a tool.');
    next=attachPlanTool(definition,step.id,tool.id);const uses=definition.workflow.filter(item=>item.agent===member.id).length;changes.push('Request '+tool.name+' for '+member.name+' across '+uses+' step'+(uses===1?'':'s')+'.');
  }else if((match=text.match(new RegExp(manualPlanning.commands.insert)))){
    const name=match[1].replace(/ member$/,''),target=match[3]?targetStep(definition,match[3]):null,slot=target?definition.workflow.findIndex(item=>item.id===target.id)+(match[2]==='after'?1:0):definition.workflow.length;
    if(manualPlanning.humanAliases.includes(name)){next=insertPlanStep(definition,'human',slot);changes.push('Add a human checkpoint'+(target?' '+match[2]+' “'+target.name+'”':' at the end')+'.');}
    else if(manualPlanning.aiAliases.includes(name)){next=insertPlanStep(definition,'ai',slot);changes.push('Add an AI work step'+(target?' '+match[2]+' “'+target.name+'”':' at the end')+'.');}
    else{const role=oneMatch(rolePresets,name,'role');next=insertMemberStep(definition,{id:role.id,name:role.name,kind:role.kind,role:role.id,instructions:role.instructions,tools:[...(role.tools||[])]},slot);changes.push('Add a separate '+role.name+' member and work step'+(target?' '+match[2]+' “'+target.name+'”':' at the end')+'.');changes.push('Use the '+role.name+' preset instructions; edit them in the inspector.');}
  }else throw new Error('Supported commands: Add a verifier before approval; Add a human checkpoint; Move [step] before/after [step]; Add [tool] to [member].');
  if(next&&match&&text.match(new RegExp(manualPlanning.commands.insert))){
    const inserted=next.workflow.find(step=>!definition.workflow.some(old=>old.id===step.id)),target=match[3]?targetStep(definition,match[3]):null;
    if(inserted&&target){
      const before=match[2]==='before',adjacent=planConnections(definition).filter(edge=>before?edge.to===target.id:edge.from===target.id);
      next=placeInsertedStep(next,inserted.id,before?{before:target.id}:{after:target.id});
      for(const edge of adjacent){next.connections=next.connections.filter(item=>item.from!==edge.from||item.to!==edge.to);next.connections.push(before?{from:edge.from,to:inserted.id}:{from:inserted.id,to:edge.to});}
    }
  }
  changes.push(...describeDefinitionChanges(definition,next).filter(change=>change.startsWith('Connect')||change.startsWith('Disconnect')));
  validateDefinition(next);if(JSON.stringify(next)===JSON.stringify(definition))throw new Error('The plan already has that configuration.');return {prompt:prompt.trim(),base:JSON.stringify(definition),definition:next,changes};
}
export function parseImportedDefinition(text){
  if(typeof text!=='string'||new TextEncoder().encode(text).length>200000)throw new Error('Use an application JSON file below 200 KB.');
  let definition;try{definition=JSON.parse(text);}catch{throw new Error('The definition is not valid JSON.');}validateDefinition(definition);return structuredClone(definition);
}
export function describeDefinitionChanges(before,next){
  validateDefinition(before);validateDefinition(next);const changes=[];
  for(const field of ['name','description','run_config','run_budget'])if(JSON.stringify(before[field])!==JSON.stringify(next[field]))changes.push('Change '+({name:'application name',description:'purpose',run_config:'runtime configuration',run_budget:'run budgets'}[field])+'.');
  for(const [section,key,label]of [['agents','id','member'],['state','key','output collection'],['workflow','id','step'],['views','id','view']]){
    for(const entry of next[section]){const previous=before[section].find(item=>item[key]===entry[key]),name=entry.name||entry.label||entry.title||entry[key];if(!previous)changes.push('Add '+label+' “'+name+'”.');else if(JSON.stringify(previous)!==JSON.stringify(entry))changes.push('Edit '+label+' “'+name+'”.');}
    for(const entry of before[section])if(!next[section].some(item=>item[key]===entry[key]))changes.push('Remove '+label+' “'+(entry.name||entry.label||entry.title||entry[key])+'”.');
  }
  if(before.workflow.map(step=>step.id).join(',')!==next.workflow.map(step=>step.id).join(','))changes.push('Update the step list order (dependencies are shown separately).');
  const name=(d,id)=>d.workflow.find(step=>step.id===id)?.name||id;
  const beforeEdges=planConnections(before),nextEdges=planConnections(next);
  for(const edge of nextEdges)if(!beforeEdges.some(old=>old.from===edge.from&&old.to===edge.to))changes.push(`Connect “${name(next,edge.from)}” → “${name(next,edge.to)}”.`);
  for(const edge of beforeEdges)if(!nextEdges.some(newEdge=>newEdge.from===edge.from&&newEdge.to===edge.to))changes.push(`Disconnect “${name(before,edge.from)}” → “${name(before,edge.to)}”.`);
  for(const member of next.agents){const previous=before.agents.find(item=>item.id===member.id);if(JSON.stringify(previous?.tools||[])!==JSON.stringify(member.tools))changes.push(`Tool access for ${member.name}: ${member.tools.join(', ')||'none'}. Runtime approvals still apply.`);if(previous&&previous.kind!==member.kind)changes.push(`Change who has authority for ${member.name}: ${previous.kind||'ai'} → ${member.kind||'ai'}.`);}
  return changes.length?changes:['Update definition metadata.'];
}
