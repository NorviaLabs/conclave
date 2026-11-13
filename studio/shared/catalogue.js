import bundledCatalogue from '../../conclave/application/data/catalogue.json' with {type:'json'};
export const applicationTypes = structuredClone(bundledCatalogue.applicationTypes);
export const audiences = structuredClone(bundledCatalogue.audiences||[]);
export const connectorDescriptors = structuredClone(bundledCatalogue.connectorDescriptors);
export const featuredApplications = structuredClone(bundledCatalogue.featuredApplications);
export const outputPresets = structuredClone(bundledCatalogue.outputPresets);
export const outputTypes = structuredClone(bundledCatalogue.outputTypes);
export const rolePresets = structuredClone(bundledCatalogue.rolePresets);
export const toolDescriptors = structuredClone(bundledCatalogue.toolDescriptors);
export const workflowConnectorRequirements = structuredClone(bundledCatalogue.workflowConnectorRequirements);
export const manualPlanning = structuredClone(bundledCatalogue.manualPlanning);

export function recommendGoal(goal){
  if(typeof goal!=='string'||!goal.trim()||goal.length>2000)throw new Error('Describe your goal in up to 2,000 characters.');
  const scored=manualPlanning.goalRules.map(rule=>({...rule,score:(goal.match(new RegExp(rule.pattern,rule.flags))||[]).length*rule.weight}));
  const best=scored.find(rule=>rule.priority&&rule.score)||scored.filter(rule=>!rule.priority).sort((a,b)=>b.score-a.score)[0];
  const id=best?.score?best.application:manualPlanning.fallback,app=applicationTypes.find(item=>item.id===id);
  if(!app)throw new Error('The configured goal suggestion references a missing starter.');
  const roles=[...new Set(app.members.map(member=>member.preset))],outputs=[...new Set(app.outputs.map(output=>output.kind))],tools={};
  for(const rule of manualPlanning.outputRules)if(new RegExp(rule.pattern,'i').test(goal)&&!outputs.includes(rule.output))outputs.push(rule.output);
  for(const role of roles){
    const preset=rolePresets.find(item=>item.id===role),rule=manualPlanning.roleTools[role];
    tools[role]=preset.kind==='human'?[]:app.featured?[...new Set(app.members.filter(member=>member.preset===role).flatMap(member=>member.tools||[]))]:rule&&!roles.includes(rule.unlessRole)?app.tools.filter(id=>rule.ids?.includes(id)||rule.permissions?.includes(toolDescriptors.find(tool=>tool.id===id)?.permission)):[...(preset.tools||[])];
  }
  return {template:id,roles,outputs,tools,reason:best?.score?`Suggested from the ${app.name.toLowerCase()} example.`:'No close example found. Start with the default team and adjust it below.'};
}

export function makeCatalogueDefinition(application,base){
  if(application.definition)return structuredClone(application.definition);
  const d=base();d.name=application.name;d.description=application.purpose;d.catalogue_type=application.id;
  d.inputs=[{id:'context',label:'Context and source references',type:'textarea',required:false},{id:'constraints',label:'Constraints and acceptance criteria',type:'text',required:false}];
  d.agents=application.members.map(member=>{
    const preset=rolePresets.find(role=>role.id===member.preset);
    return {id:member.id,name:member.name,kind:preset.kind,role:member.preset,instructions:member.instructions||preset.instructions,tools:preset.kind==='human'?[]:(member.tools||preset.tools||[])};
  });
  d.state=application.outputs.map(output=>{const kind=outputTypes.find(type=>type.id===output.kind);return {key:output.key,label:output.name,kind:kind.id,type:kind.type,merge:kind.type==='text'?'replace':'upsert',statuses:[...kind.statuses]};});
  for(const collection of d.state)if(application.output_statuses?.[collection.key])collection.statuses=[...application.output_statuses[collection.key]];
  d.workflow=application.plan?application.plan.map(s=>({...s})):application.outputs.map((output,i)=>({id:'step_'+(i+1),name:output.name,agent:output.owner,output:output.key,task:output.task}));
  d.views=application.outputs.map(output=>{const kind=outputTypes.find(type=>type.id===output.kind);return {id:output.key+'_view',title:output.name,collection:output.key,layout:kind.layout};});
  if(application.inputs)d.inputs=structuredClone(application.inputs);
  for(const key of ['connections','review_checks','research','connector_requirements','integrations'])if(application[key])d[key]=structuredClone(application[key]);
  return d;
}

export function registerHostTools(descriptors=[]){
  for(const descriptor of descriptors){
    if(!descriptor||typeof descriptor.id!=='string'||! /^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$/.test(descriptor.id))throw new Error('Invalid host tool identifier.');
    const existing=toolDescriptors.find(tool=>tool.id===descriptor.id);
    if(existing)Object.assign(existing,descriptor);else toolDescriptors.push({...descriptor});
  }
}

export function registerHostConnectors(descriptors=[]){
  for(const descriptor of descriptors){
    if(!descriptor||typeof descriptor.id!=='string'||! /^[a-z][a-z0-9_-]{0,63}$/.test(descriptor.id))throw new Error('Invalid host connector identifier.');
    const existing=connectorDescriptors.find(connector=>connector.id===descriptor.id);
    if(existing)Object.assign(existing,descriptor);else connectorDescriptors.push({...descriptor});
  }
}
