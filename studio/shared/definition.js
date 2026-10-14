import defaultDefinition from '../../conclave/application/data/defaults.json' with {type:'json'};
import {toolDescriptors,rolePresets,outputTypes,outputPresets,applicationTypes,makeCatalogueDefinition,connectorDescriptors} from './catalogue.js';
export const toolCatalog = toolDescriptors.map(tool=>tool.id);
export const runtimeConfig = structuredClone(defaultDefinition.run_config);
export const runBudget = structuredClone(defaultDefinition.run_budget);
export function emptyDefinition(){return structuredClone(defaultDefinition);}
export const templates=applicationTypes.map(application=>({id:application.id,definition:makeCatalogueDefinition(application,emptyDefinition)}));
const reserved=new Set(['constructor','prototype','__proto__']);
function fail(message){throw new Error(message)}
function str(value,label,max=4000,required=true){if(typeof value!=='string'||value.length>max||(required&&!value.trim()))fail(`${label} must be ${required?'nonempty ':''}text, at most ${max} characters.`)}
function identifier(value,label){str(value,label,64);if(!/^[a-z][a-z0-9_-]*$/.test(value)||reserved.has(value))fail(`${label} must start with a lowercase letter and contain letters, digits, underscores or hyphens.`)}
function array(value,label,max=24){if(!Array.isArray(value)||value.length<1||value.length>max)fail(`${label} needs 1–${max} entries.`)}
function unique(items,field,label){const values=items.map(x=>x[field]);if(new Set(values).size!==values.length)fail(`${label} must be unique.`)}
export function validateDefinition(d){
 if(d?.autonomous_execution!==undefined&&typeof d.autonomous_execution!=='boolean')fail('Autonomous execution must be a boolean.');
 if(!d||typeof d!=='object'||Array.isArray(d)||d.version!==1)fail('Unsupported application definition.');
 if(d.resources!==undefined){if(!d.resources||typeof d.resources!=='object'||Array.isArray(d.resources)||Object.keys(d.resources).some(key=>key!=='source'))fail('Application resources must select a named source.');if(d.resources.source!==undefined)identifier(d.resources.source,'Source resource');}
 str(d.name,'Application name',100);str(d.description,'Purpose',2000,false);array(d.agents,'Agents',16);array(d.state,'State collections',12);array(d.workflow,'Workflow stages',24);array(d.views,'Views',12);
 unique(d.agents,'id','Agent identifiers');unique(d.state,'key','State keys');unique(d.workflow,'id','Stage identifiers');unique(d.views,'id','View identifiers');
 for(const a of d.agents){identifier(a.id,'Agent identifier');if(a.kind!==undefined&&!['ai','human'].includes(a.kind))fail('Choose AI or human members.');if(a.kind==='human'&&Array.isArray(a.tools)&&a.tools.length)fail('Human members do not execute agent tools.');if(a.kind==='human'&&(a.provider||a.model))fail('Human members do not run on a model backend.');backend(a,'Member');str(a.name,'Agent name',100);str(a.instructions,'Agent instructions',8000);if(!Array.isArray(a.tools)||a.tools.length>100||a.tools.some(t=>typeof t!=='string'||! /^[a-zA-Z][a-zA-Z0-9_.-]{0,99}$/.test(t))||new Set(a.tools).size!==a.tools.length)fail('Use unique valid tool identifiers.');}
 for(const s of d.state){validateRecordFields(s.fields||[]);if(s.type==='text'&&s.fields?.length)fail('Text collections do not accept record fields.');identifier(s.key,'State key');str(s.label,'State label',100);if(s.kind!==undefined&&!outputTypes.some(k=>k.id===s.kind))fail('Choose a supported output type.');if(s.kind!==undefined&&(s.kind==='document')!==(s.type==='text'))fail('Documents require text; other output types require records.');if(!['records','text'].includes(s.type))fail('Choose records or text state.');if(!['upsert','append','replace'].includes(s.merge)||(s.type==='text'&&s.merge!=='replace'))fail('Text state requires replace; records support upsert, append or replace.');if(!Array.isArray(s.statuses)||s.statuses.length>12||s.statuses.some(x=>typeof x!=='string'||!x.trim()||x.length>40)||new Set(s.statuses).size!==s.statuses.length)fail('Statuses must be unique nonempty labels.');}
 for(const w of d.workflow){identifier(w.id,'Stage identifier');str(w.name,'Stage name',100);str(w.task,'Stage task',4000);if(!d.agents.some(a=>a.id===w.agent))fail(`Stage “${w.name}” needs an existing agent.`);if(!d.state.some(s=>s.key===w.output))fail(`Stage “${w.name}” needs an existing output collection.`);if(d.agents.some(a=>a.id===w.agent&&a.kind==='human')&&(w.provider||w.model))fail('Human steps do not run on a model backend.');backend(w,`Step “${w.name}”`);}
 for(const v of d.views){identifier(v.id,'View identifier');str(v.title,'View title',100);const s=d.state.find(s=>s.key===v.collection);if(!s)fail(`View “${v.title}” needs an existing state collection.`);if(!['cards','table','board','prose'].includes(v.layout)||(s.type==='text')!==(v.layout==='prose'))fail(`View “${v.title}” must use prose for text, or cards/table/board for records.`);}
 const rc=d.run_config,rb=d.run_budget;if(!rc||!rb)fail('Explicit runtime limits are required.');if(Object.keys(rc).sort().join(',')!==Object.keys(runtimeConfig).sort().join(',')||Object.keys(rb).sort().join(',')!==Object.keys(runBudget).sort().join(','))fail('Use the supported runtime configuration and budget fields.');
 for(const key of Object.keys(runtimeConfig)){const value=rc[key];if(value!==null&&(!Number.isInteger(value)||value<1))fail(`Runtime limit ${key} must be a positive integer or null (unlimited).`);}
 for(const key of ['max_delegations','max_per_agent','max_depth','tool_calls']){if(rb[key]!==null&&(!Number.isInteger(rb[key])||rb[key]<0||rb[key]>1000000))fail(`Budget ${key} must be nonnegative or null.`);}
 for(const key of ['wall_clock_seconds','tokens','cost_usd'])if(rb[key]!==null&&(!Number.isFinite(rb[key])||rb[key]<0))fail(`Budget ${key} must be nonnegative or null.`);
 if(d.inputs!==undefined){if(!Array.isArray(d.inputs)||d.inputs.length>12)fail('Use at most 12 request fields.');unique(d.inputs,'id','Request field identifiers');for(const f of d.inputs){identifier(f.id,'Request field identifier');str(f.label,'Request field label',100);if(!['text','textarea','number','date'].includes(f.type)||typeof f.required!=='boolean')fail('Choose a supported request field type and required setting.');}}
 if(d.budget_warnings!==undefined){if(!d.budget_warnings||typeof d.budget_warnings!=='object'||Array.isArray(d.budget_warnings))fail('Provide budget warnings.');for(const [key,value] of Object.entries(d.budget_warnings)){if(!['tool_calls','wall_clock_seconds','tokens','cost_usd'].includes(key)||value!==null&&(!Number.isFinite(value)||value<0))fail('Invalid warning budget.');if(value!==null&&rb[key]!==null&&value>=rb[key])fail('Warning thresholds must be below hard stops.');}}
 if(d.connections!==undefined){if(!Array.isArray(d.connections)||d.connections.length>552)fail('Use valid step connections.');const seen=new Set();for(const edge of d.connections){if(!edge||!d.workflow.some(w=>w.id===edge.from)||!d.workflow.some(w=>w.id===edge.to)||edge.from===edge.to)fail('Connect two different existing steps.');const key=edge.from+'>'+edge.to;if(seen.has(key))fail('This connection already exists.');seen.add(key);}executionOrder(d);}
 if(d.canvas!==undefined){if(!d.canvas||typeof d.canvas!=='object'||!d.canvas.positions||typeof d.canvas.positions!=='object'||Array.isArray(d.canvas.positions))fail('Use valid canvas positions.');for(const [id,pos] of Object.entries(d.canvas.positions)){if(!d.workflow.some(w=>w.id===id)||!pos||!Number.isFinite(pos.x)||!Number.isFinite(pos.y)||pos.x<0||pos.y<0||pos.x>10000||pos.y>10000)fail('Place steps within the canvas.');}}
 if(d.canvas!==undefined&&d.canvas.groups!==undefined){const groups=d.canvas.groups;if(!Array.isArray(groups)||groups.length>20)fail('Use up to 20 canvas groups.');const ids=new Set();for(const group of groups){if(!group||typeof group.id!=='string'||!/^[a-z][a-z0-9_-]{0,63}$/.test(group.id)||ids.has(group.id))fail('Use a valid group identifier.');ids.add(group.id);if(typeof group.name!=='string'||!group.name.trim()||group.name.length>100||(group.collapsed!==undefined&&typeof group.collapsed!=='boolean'))fail('Name each group.');}for(const step of d.workflow)if(step.group!==undefined&&!ids.has(step.group))fail('A step can only belong to a group the plan declares.');}
 if(d.start_mode!==undefined&&!['manual','event','schedule'].includes(d.start_mode))fail('Choose a supported start method.');
 if(d.integrations!==undefined){if(!Array.isArray(d.integrations)||d.integrations.length>40)fail('Choose up to 40 connectors.');unique(d.integrations,'connector','Connector selections');for(const item of d.integrations){if(!item||Object.keys(item).some(k=>!['connector','purpose','scope','mode'].includes(k)))fail('Connector setup accepts choices only, not account credentials.');const connector=connectorDescriptors.find(c=>c.id===item.connector);identifier(item.connector,'Connector identifier');str(item.purpose,'Connector purpose',1000,false);str(item.scope,'Connector source or destination',1000,false);const modes=connector?.default_mode==='model'?['model']:connector?.default_mode==='read'?['read']:['read','draft','approved','model'];if(!modes.includes(item.mode))fail('Choose supported actions for this connector.');}}
 return d;
}
// What runs one member or one step. The host answers which backends this
// machine can reach; a name is only ever offered or refused, never invented.
const backendPattern=/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/,modelPattern=/^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,119}$/;
function backend(entry,what){
  if(entry.provider!==undefined&&(typeof entry.provider!=='string'||entry.provider!==entry.provider.trim()||entry.provider&&!backendPattern.test(entry.provider)))fail(`${what} has an invalid backend: use a plain name.`);
  if(entry.model!==undefined&&(typeof entry.model!=='string'||entry.model!==entry.model.trim()||entry.model&&!modelPattern.test(entry.model)))fail(`${what} has an invalid model: use a plain name, not a sentence.`);
}
// The backend one step runs on: its own choice, else its member's. Mirrors the
// host's rule, including that a model never follows a backend it was not
// chosen for.
export function stageBackend(member,step={}){
  const provider=step.provider||member.provider||'';
  const inherited=provider===(member.provider||'')?(member.model||''):'';
  return [provider,step.model||inherited];
}
export function backendOptions(backends,current,{inherit='This host’s default'}={}){
  const options=inherit===null?[]:[['',inherit]];
  for(const backend of backends||[])options.push([backend.id,backend.available?backend.name:`${backend.name} · unavailable`,!backend.available]);
  // A choice made on another machine is shown as itself, never as the first
  // option in the list: the picker must not misreport what the step runs on.
  if(current&&!options.some(([value])=>value===current))options.push([current,`${current} · not offered by this host`]);
  return options;
}
export function defaultBackend(backends){return (backends||[]).find(backend=>backend.default)||null;}
export function backendModels(backends,provider){return (backends||[]).find(backend=>backend.id===provider)?.models||[];}
export function backendIssue(backends,provider){
  if(!provider)return '';
  const known=(backends||[]).find(backend=>backend.id===provider);
  if(!known)return 'This host does not offer that backend.';
  return known.available?'':(known.reason||'This host cannot run that backend.');
}
export function backendLabel(backends,provider,model){
  const known=(backends||[]).find(backend=>backend.id===provider);
  const name=known?known.name:(provider||'This host’s default');
  return model?`${name} · ${model}`:name;
}
// Which backend a step runs on, in the words this host uses, or what the host
// reports as its own default when neither the step nor its member chose one. The
// choice itself is `stageBackend`'s, so the card cannot disagree with the run.
export function stepBackendLabel(backends,hostProvider,step,member){
  const [provider,model]=stageBackend(member||{},step||{});
  return provider||model?backendLabel(backends,provider,model):backendLabel(backends,hostProvider||'','');
}
export function initialState(definition){return Object.fromEntries(definition.state.map(s=>[s.key,s.type==='text'?'':[]]));}
export function validateMember(member){
 if(!member||typeof member!=='object'||Array.isArray(member))fail('Provide a member definition.');
 const d=emptyDefinition();d.agents=[member];for(const step of d.workflow)step.agent=member.id;validateDefinition(d);
 return {id:member.id,name:member.name,kind:member.kind||'ai',role:typeof member.role==='string'?member.role.slice(0,100):'custom',instructions:member.instructions,tools:[...member.tools]};
}
export function mergeRecords(current,patch,mode){if(mode==='replace')return structuredClone(patch);if(mode==='append')return [...current,...structuredClone(patch)];const map=new Map(current.map(item=>[item.id,structuredClone(item)]));for(const item of patch){const before=map.get(item.id);const contributions=[...(before?.contributions||[]),...(item.contributions||[])];map.set(item.id,{...before,...structuredClone(item),contributions});}return [...map.values()];}
export function applyStage(state,definition,stage,payload,runId){const collection=definition.state.find(s=>s.key===stage.output);const snapshot=structuredClone(state);if(collection.type==='text'){if(typeof payload!=='string')fail('Text stage needs a string.');snapshot[stage.output]=payload;}else{if(!Array.isArray(payload))fail('Record stage needs an array.');for(const r of payload){if(!r||typeof r.id!=='string'||typeof r.title!=='string'||typeof r.body!=='string'||typeof r.status!=='string'||!Array.isArray(r.source_refs))fail('Invalid record output.');validateRecordValues(r,collection.fields||[]);if(collection.statuses.length&&!collection.statuses.includes(r.status))fail('Record status is not allowed by the collection.');}snapshot[stage.output]=mergeRecords(snapshot[stage.output],payload.map(r=>({...r,contributions:[{agent:stage.agent,stage:stage.id,run_id:runId}]})),collection.merge);}return snapshot;}

export function validateRequestInputs(definition,values={}){const result={};for(const f of definition.inputs||[]){const v=values[f.id]??'';if(f.required&&(v===''||v===null||typeof v==='string'&&!v.trim()))fail(`Enter ${f.label}.`);if(v==='')continue;if(f.type==='number'){if(!Number.isFinite(Number(v)))fail(`${f.label} needs a number.`);result[f.id]=Number(v);}else{if(typeof v!=='string'||v.length>2000)fail(`${f.label} must be text of at most 2000 characters.`);if(f.type==='date'&&(!/^\d{4}-\d{2}-\d{2}$/.test(v)||!Number.isFinite(Date.parse(v))||new Date(v).toISOString().slice(0,10)!==v))fail(`${f.label} needs a valid date.`);result[f.id]=v;}}return result;}

export function planConnections(d){return d.connections===undefined?d.workflow.slice(1).map((w,i)=>({from:d.workflow[i].id,to:w.id})):d.connections;}
export function executionOrder(d){const links=planConnections(d),pending=new Set(d.workflow.map(w=>w.id)),order=[];while(pending.size){const id=d.workflow.find(w=>pending.has(w.id)&&!links.some(e=>e.to===w.id&&pending.has(e.from)))?.id;if(!id)throw new Error('That connection creates a loop. Remove it so work can finish.');pending.delete(id);order.push(id);}return order;}
// Which steps' recorded results no longer describe the plan in front of you.
// A step is stale when its own definition changed since the run, when it is new
// since the run, or when anything it runs after changed: its input came from
// that older step. Editing one step therefore marks the work downstream of it,
// which is the honest reading of "this result is out of date".
export function staleRunSteps(runDefinition,currentDefinition){
  const before=new Map((runDefinition?.workflow||[]).map(step=>[step.id,step]));
  const sources=definition=>{
    const map=new Map(definition.workflow.map(step=>[step.id,[]]));
    for(const link of planConnections(definition))map.get(link.to)?.push(link.from);
    return new Map([...map].map(([id,list])=>[id,[...list].sort().join(',')]));
  };
  const beforeSources=sources(runDefinition||{workflow:[],connections:[]}),afterSources=sources(currentDefinition);
  const changed=new Set();
  for(const step of currentDefinition.workflow){
    const previous=before.get(step.id);
    // A step is out of date when it changed, when it is new, or when what it
    // runs after changed: adding, removing or rewiring a step changes the input
    // of the step on the other end of that connection.
    if(!previous||['name','task','agent','output'].some(field=>previous[field]!==step[field])||beforeSources.get(step.id)!==afterSources.get(step.id))changed.add(step.id);
  }
  const links=planConnections(currentDefinition);
  let grew=true;
  while(grew){
    grew=false;
    for(const link of links){
      if(changed.has(link.from)&&!changed.has(link.to)){changed.add(link.to);grew=true;}
    }
  }
  return changed;
}
export function ancestorSteps(d,id){const ancestors=new Set(),links=planConnections(d);const visit=node=>{for(const edge of links.filter(e=>e.to===node)){if(!ancestors.has(edge.from)){ancestors.add(edge.from);visit(edge.from);}}};visit(id);return ancestors;}
export function dependencyState(d,id,traces,runId){let state=initialState(d);const ancestors=ancestorSteps(d,id);for(const sid of executionOrder(d)){if(!ancestors.has(sid))continue;const index=d.workflow.findIndex(w=>w.id===sid),trace=traces[index];if(trace?.patch!==undefined)state=applyStage(state,d,d.workflow[index],trace.patch,runId);}return state;}
// How many dependency levels the plan has, and how many steps the widest one
// holds: the canvas needs both to size itself before anything is drawn.
export function planShape(d){
  const levels={},counts={};
  for(const id of executionOrder(d)){
    const parents=planConnections(d).filter(edge=>edge.to===id).map(edge=>edge.from);
    const level=parents.length?Math.max(...parents.map(parent=>levels[parent]))+1:0;
    levels[id]=level;counts[level]=(counts[level]||0)+1;
  }
  const rows=Object.keys(counts).length?Math.max(...Object.values(counts)):1;
  const depth=Object.keys(counts).length?Math.max(...Object.keys(counts).map(Number))+1:1;
  return {levels:depth,columns:rows};
}
export function canvasPositions(d){const positions={},levels={},counts={};for(const id of executionOrder(d)){const parents=planConnections(d).filter(e=>e.to===id).map(e=>e.from);const level=parents.length?Math.max(...parents.map(p=>levels[p]))+1:0;levels[id]=level;const column=counts[level]||0;counts[level]=column+1;const saved=d.canvas?.positions?.[id];positions[id]=saved?{x:Number(saved.x)||0,y:Number(saved.y)||0}:{x:canvasMetrics.padX+level*(canvasMetrics.nodeWidth+canvasMetrics.gapX),y:canvasMetrics.padY+column*(canvasMetrics.nodeHeight+canvasMetrics.gapY)};}return positions;}
// Both canvases draw dependencies from the right of one node to the left of
// the next, so geometry lives here rather than in either view.
export let canvasNodeWidth=350,canvasNodeHeight=240;
// Keep cards readable; the board grows and scrolls when the plan outgrows it.
// The card height is what a step card's own content costs: card padding, the
// head, a two-line title, then the facts — a two-line output, the member row
// and its badge, a two-line backend. 220 cut the last line of the title.
let canvasMetrics={nodeWidth:350,nodeHeight:240,gapX:60,gapY:60,padX:60,padY:50};
export function currentCanvasMetrics(){return canvasMetrics;}
export function setCanvasMetrics({width,height,levels,columns}){
  const rows=Math.max(1,columns),cols=Math.max(1,levels);
  const nodeWidth=350,nodeHeight=240,gapX=60,gapY=60;
  // Centre what there is, so a short plan looks placed rather than abandoned.
  const spanX=nodeWidth*cols+gapX*(cols-1),spanY=nodeHeight*rows+gapY*(rows-1);
  canvasMetrics={nodeWidth,nodeHeight,gapX,gapY,padX:Math.max(40,Math.round((width-spanX)/2)),padY:Math.max(50,Math.round((height-spanY)/2)),width,height};
  canvasNodeWidth=canvasMetrics.nodeWidth;canvasNodeHeight=canvasMetrics.nodeHeight;
  return canvasMetrics;
}
export function canvasEdgeCurve(from,to){if(!from||!to)return '';const x1=from.x+canvasNodeWidth,y1=from.y+canvasNodeHeight/2,x2=to.x,y2=to.y+canvasNodeHeight/2,bend=Math.max(40,Math.abs(x2-x1)/2);return `M ${x1} ${y1} C ${x1+bend} ${y1}, ${x2-bend} ${y2}, ${x2} ${y2}`;}
// The point halfway along that curve, where a handle on the edge belongs.
export function canvasEdgeMidpoint(from,to){if(!from||!to)return null;const x1=from.x+canvasNodeWidth,y1=from.y+canvasNodeHeight/2,x2=to.x,y2=to.y+canvasNodeHeight/2;return {x:(x1+x2)/2,y:(y1+y2)/2};}

export function emptyWorkspaceCatalogue(){return {version:1,roles:[],starters:[],outputs:[]};}
export function validateWorkspaceCatalogue(value){
 if(!value||typeof value!=='object'||Object.keys(value).sort().join(',')!=='outputs,roles,starters,version'||value.version!==1)fail('Use version 1 workspace catalog fields.');
 for(const key of ['roles','starters','outputs']){if(!Array.isArray(value[key])||value[key].length>100)fail('Use up to 100 '+key+'.');unique(value[key],'id',key);for(const entry of value[key])identifier(entry.id,'Catalog identifier');}
 for(const role of value.roles)validateMember({...role,tools:role.tools||[]});
 for(const starter of value.starters){str(starter.name,'Starter name',100);validateDefinition(starter.definition);}
 for(const output of value.outputs){validateRecordFields(output.fields||[]);if(output.kind==='document'&&output.fields?.length)fail('Documents do not accept record fields.');str(output.name,'Output name',100);str(output.task,'Output task',4000);if(!outputTypes.some(type=>type.id===output.kind))fail('Choose a supported output type.');}
 for(const [key,bundled]of [['roles',rolePresets],['starters',applicationTypes],['outputs',outputPresets]])for(const item of value[key])if(bundled.some(entry=>entry.id===item.id&&!entry.workspace))fail('Use a custom identifier distinct from bundled '+key+'.');
 return structuredClone(value);
}
export function installWorkspaceCatalogue(value){
 validateWorkspaceCatalogue(value);
 for(const list of [rolePresets,applicationTypes,outputPresets,templates])for(let index=list.length-1;index>=0;index--)if(list[index].workspace)list.splice(index,1);
 rolePresets.push(...value.roles.map(role=>({...role,kind:role.kind||'ai',description:role.description||role.instructions,workspace:true})));
 outputPresets.push(...value.outputs.map(output=>({...output,workspace:true})));
 for(const starter of value.starters){const d=structuredClone(starter.definition);templates.push({id:starter.id,definition:d,workspace:true});applicationTypes.push({id:starter.id,name:starter.name,purpose:d.description,example:d.description,definition:d,workspace:true,featured:true,members:d.agents.map(a=>({id:a.id,name:a.name,preset:a.role})),tools:[...new Set(d.agents.flatMap(a=>a.tools))],outputs:d.state.map(s=>({key:s.key,name:s.label,kind:s.kind||'findings'})),plan:d.workflow});}
}

export function validateRecordFields(fields){
 if(!Array.isArray(fields)||fields.length>24)fail('Use up to 24 record fields.');unique(fields,'key','Record fields');
 for(const f of fields){if(!f||Object.keys(f).some(key=>!['key','label','type','required','options'].includes(key)))fail('Invalid record field.');identifier(f.key,'Record field');if(['id','title','body','status','source_refs','contributions'].includes(f.key))fail('Record field conflicts with protocol metadata.');str(f.label,'Record field label',100);if(!['string','number','boolean','date','enum'].includes(f.type)||typeof f.required!=='boolean')fail('Choose a record field type and requirement.');if(f.type==='enum'){if(!Array.isArray(f.options)||!f.options.length||f.options.length>50||f.options.some(value=>typeof value!=='string'||!value||value.length>100)||new Set(f.options).size!==f.options.length)fail('Enum fields need unique bounded choices.');}else if(f.options!==undefined)fail('Only enum fields accept choices.');}
 return fields;
}
export function validateRecordValues(record,fields){
 validateRecordFields(fields);const values={};
 for(const f of fields){const value=record[f.key];if(value===undefined||value===null||value===''){if(f.required)fail('Provide record field: '+f.label);continue;}if(f.type==='number'?(typeof value!=='number'||!Number.isFinite(value)):f.type==='boolean'?typeof value!=='boolean':typeof value!=='string'||value.length>20000)fail('Invalid record field: '+f.label);if(f.type==='enum'&&!f.options.includes(value))fail('Invalid choice: '+f.label);if(f.type==='date'&&(!/^\d{4}-\d{2}-\d{2}$/.test(value)||!Number.isFinite(Date.parse(value))||new Date(value).toISOString().slice(0,10)!==value))fail('Invalid date: '+f.label);values[f.key]=value;}
 return values;
}

// Conservative advisory checks: prose is not a schema, so fixes need consent.
export function preflightIssues(d){
 const issues=[];
 const mention=(text,name)=>new RegExp(`(?<![a-z0-9_])${name.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g,'\\$&')}(?![a-z0-9_])`).test(text.toLowerCase());
 const warning=(step,code,label,message,evidence)=>issues.push({stage:step.id,code,severity:'warning',label,message,evidence});
 for(const step of d.workflow){
  const collection=d.state.find(item=>item.key===step.output),member=d.agents.find(item=>item.id===step.agent);
  const sentences=`${step.task}\n${member?.instructions||''}`.split(/[.!?\n]/);
  for(const field of collection?.fields||[]){
   const evidence=sentences.find(sentence=>{
    const text=sentence.toLowerCase();
    const mentioned=[field.key,field.label].some(name=>mention(text,name));
    return mentioned&&!/\b(?:do not|don't|never|must not|cannot)\b/.test(text)&&/empty string|leave\b.{0,40}\bempty|omit\b|optional\b|blank\b/.test(text);
   });
   if(field.required&&evidence)issues.push({stage:step.id,code:'empty-required',severity:'warning',collection:collection.key,field:field.key,label:field.label,evidence:evidence.trim(),fix:{kind:'optional',label:'Make optional and save',preview:`${collection.key}.${field.key}: required true → false`},message:`${field.label} is required, but the instructions allow an empty or omitted value. Required fields reject missing, null and empty-string values.`});
   else if(field.required&&!sentences.some(sentence=>mention(sentence,field.key)||mention(sentence,field.label)))warning(step,'field-coverage',field.label,'Required field is not explained in the step or member instructions. The runtime supplies its type, but not its intended meaning.',`${collection.key}.${field.key}: ${field.type}, required`);
   const arraySentence=sentences.find(sentence=>mention(sentence,field.key)&&/\b(?:array|list)\b/i.test(sentence)&&! /comma[- ]separated/i.test(sentence));
   if(arraySentence&&field.type!=='string')warning(step,'field-type',field.label,`Instructions mention a list/array, but this field expects ${field.type}. Review the representation.`,arraySentence.trim());
   if(arraySentence&&field.type==='string'&&/\b(?:json array|array of|as an? (?:array|list))\b/i.test(arraySentence))warning(step,'field-type',field.label,'Instructions request an array, but the schema expects a string.',arraySentence.trim());
   const typed=sentences.find(sentence=>mention(sentence,field.key)&&/\bas (?:a |an )?(?:string|number|boolean)\b/i.test(sentence));
   const requestedType=typed?.match(/\bas (?:a |an )?(string|number|boolean)\b/i)?.[1].toLowerCase();
   if(requestedType&&requestedType!==field.type&&!(['enum','date'].includes(field.type)&&requestedType==='string'))warning(step,'field-type',field.label,`Instructions request ${requestedType}, but the schema expects ${field.type}.`,typed.trim());
  }
  for(const sentence of sentences){
   const status=sentence.match(/\b(?:set|mark|use)\s+(?:the\s+)?status\s+(?:to|as)\s+["'`]?([a-z][a-z0-9_-]*)/i)?.[1];
   if(status&&!/\b(?:do not|don't|never|must not)\b/i.test(sentence)&&collection?.statuses?.length&&!collection.statuses.includes(status))warning(step,'status-contract','Output status',`The requested status “${status}” is not allowed: ${collection.statuses.join(', ')}.`,sentence.trim());
  }
  if(member?.kind!=='human'){
   const tools=member?.tools||[];
   const executable=tools.some(id=>['run_tests','run_code','execute_command','shell','bash'].includes(id)||/\b(?:execute commands|execute code|run tests)\b/i.test(toolDescriptors.find(tool=>tool.id===id)?.description||''));
   const writable=tools.some(id=>['write_file','edit_file','apply_patch'].includes(id)||/\b(?:create or edit files|apply patches)\b/i.test(toolDescriptors.find(tool=>tool.id===id)?.description||''));
   const positive=sentences.filter(sentence=>! /\b(?:do not|don't|never|cannot|can't|proposed|propose|unverified|without executing)\b/i.test(sentence));
   const execute=positive.find(sentence=>/\b(?:run|execute)\s+(?:the\s+)?(?:tests|test suite|commands|test command)\b/i.test(sentence));
   const write=positive.find(sentence=>/\b(?:apply|write|modify|edit)\s+(?:the\s+)?(?:patch|code|files|source files)\b/i.test(sentence));
   if(execute&&!executable)warning(step,'execution-tool','Execution capability','The task requests execution, but no declared tool is known to execute commands/tests. Add a host-approved tool or change the task to propose commands.',execute.trim());
   if(write&&!writable)warning(step,'write-tool','File modification capability','The task requests file changes, but no declared tool is known to apply them. Add a host-approved tool or request a draft patch.',write.trim());
  }
  const ancestors=ancestorSteps(d,step.id);
  for(const input of d.state){
   if(input.key===step.output)continue;
   const evidence=sentences.find(sentence=>! /\b(?:do not|don't|never|must not|cannot)\b/i.test(sentence)&&/\b(?:read|use|consume|inspect|requires?)\b/i.test(sentence)&&(mention(sentence,input.key)||input.label&&mention(sentence,input.label)));
   const producers=d.workflow.filter(other=>other.output===input.key&&other.id!==step.id);
   if(evidence&&producers.length&&!producers.some(other=>ancestors.has(other.id)))warning(step,'input-dependency',input.label||input.key,'Instructions reference an output with no producing ancestor. Add a dependency if this step needs that output; external context may make this warning irrelevant.',evidence.trim());
  }
  if(/acceptance criter/i.test(`${step.task} ${collection?.label||''}`)&&! /\b(?:verify|verification|tests?|checks?|validate)\b/i.test(`${step.name||''} ${step.task}`)&&!d.workflow.some(other=>ancestorSteps(d,other.id).has(step.id)&&/\b(?:verify|verification|tests?|checks?|validate)\b/i.test(`${other.name||''} ${other.task}`)))warning(step,'verification-coverage','Acceptance verification','No downstream step explicitly mentions verification, tests, checks or validation. Review acceptance-criteria coverage.',step.task);
 }
 const ai=d.workflow.filter(step=>d.agents.find(member=>member.id===step.agent)?.kind!=='human');
 const budget=d.run_budget;
 const budgetIssue=(key,value,stage,label)=>issues.push({stage,code:`budget-${key}-${stage}`,severity:'error',label,message:`${key} is ${budget[key]}, but the planned AI steps require at least ${value}.`,evidence:`Planned minimum: ${value}; configured: ${budget[key]}`,fix:{kind:'budget',key,value,label:'Adjust budget and save',preview:`run_budget.${key}: ${budget[key]} → ${value}`}});
 if(budget.max_delegations!==null&&budget.max_delegations<ai.length)budgetIssue('max_delegations',ai.length,ai[0]?.id,'Delegation budget');
 if(ai.length&&budget.max_depth!==null&&budget.max_depth<1)budgetIssue('max_depth',1,ai[0].id,'Agent depth budget');
 for(const member of d.agents){const steps=ai.filter(step=>step.agent===member.id);if(steps.length&&budget.max_per_agent!==null&&budget.max_per_agent<steps.length)budgetIssue('max_per_agent',steps.length,steps[0].id,'Per-agent budget');}
 for(const key of ['tokens','wall_clock_seconds'])if(ai.length&&budget[key]===0)issues.push({stage:ai[0].id,code:`zero-${key}`,severity:'error',label:'Execution budget',message:`The ${key} budget is zero; model execution cannot proceed. Set a positive limit or unlimited.`,evidence:`run_budget.${key}: 0`});
 return issues;
}

export function resolvePreflightIssue(d,issue){
 if(!issue.fix||!preflightIssues(d).some(item=>JSON.stringify(item)===JSON.stringify(issue)))throw new Error('This issue changed. Check the application again.');
 const updated=structuredClone(d);
 if(issue.fix.kind==='budget')updated.run_budget[issue.fix.key]=issue.fix.value;
 else updated.state.find(item=>item.key===issue.collection).fields.find(item=>item.key===issue.field).required=false;
 validateDefinition(updated);
 return updated;
}
