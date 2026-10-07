import {canvasMarkup,canvasStepMarkup} from '/canvas-ui.js';
// The run canvas: the same run the work plan lists, drawn on the plan's canvas,
// with the exchanges between agents on the edges.
//
// Everything here is read from the projection in live.js, which is read from the
// durable run record, so the canvas shows what actually happened rather than a
// second account of it.
import {planConnections,canvasPositions,currentCanvasMetrics,canvasEdgeCurve,canvasEdgeMidpoint,canvasNodeWidth,canvasNodeHeight,staleRunSteps,stageBackend,backendLabel} from '/definition.js';

const STOPPED=['stopped','failed','cancelled','interrupted'];
const statusClasses={'Working':'working','Complete':'complete','Failed':'failed','Needs input':'needs-input','Ready':'ready','Waiting':'waiting','Incomplete':'incomplete'};
function runStatusClass(status){return statusClasses[status]||'waiting';}
const flowArrow='<defs><marker id="flow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor"/></marker></defs>';

// What crossed a handoff, said in the terms the state uses.
export function payloadLabel(patch,type){if(typeof patch==='string')return `${patch.length.toLocaleString('en-US')} characters`;if(Array.isArray(patch))return `${patch.length} ${patch.length===1?'record':'records'}`;if(patch&&typeof patch==='object')return type==='text'?'written':'updated';return 'nothing';}

// The status vocabulary of a run, in the same precedence the work plan uses:
// an active step first, then a completed one, then a run that stopped.
export function runStepStatus(d,run,index){
  if(!run)return 'Waiting';
  const step=d.workflow[index];
  if(!step)return 'Waiting';
  if(run.activeStep===index)return run.mode==='waiting_human'?'Needs input':'Working';
  if(run.completedSteps?run.completedSteps.includes(step.id):index<=run.step)return 'Complete';
  if(run.mode==='failed'&&run.events?.findLast(event=>event.type==='StageStarted')?.stage===step.id)return 'Failed';
  if(STOPPED.includes(run.mode))return 'Incomplete';
  return planConnections(d).filter(edge=>edge.to===step.id).every(edge=>run.completedSteps?.includes(edge.from))?'Ready':'Waiting';
}

// The backend each step started on, as the run recorded it. A step that has
// not started yet has no entry, and shows what the plan declares instead.
export function runBackends(run){
  const started=new Map();
  for(const event of run?.events||[]){
    if(event.type==='StageStarted'&&event.stage)started.set(event.stage,[event.provider||'',event.model||'']);
  }
  return started;
}

// Tool calls, counted per step, in the order they were first used.
export function runToolCalls(run){
  const calls=new Map();
  for(const event of run?.events||[]){
    if(!['ToolCalled','ToolDenied'].includes(event.type)||!event.stage)continue;
    const tools=calls.get(event.stage)||[],known=tools.find(tool=>tool.name===event.tool);
    if(known)known.count+=1;else tools.push({name:event.tool,count:1,denied:event.type==='ToolDenied'});
    calls.set(event.stage,tools);
  }
  return calls;
}

// Agents a step handed work to, and what they were asked to do.
export function runDelegations(run){
  const delegations=new Map();
  for(const event of run?.events||[]){
    if(event.type!=='Delegation'||!event.stage)continue;
    const list=delegations.get(event.stage)||[];
    list.push({agent:event.agent,by:event.delegated_by,task:event.task,status:event.status||'working',depth:event.depth,reason:event.reason||''});
    delegations.set(event.stage,list);
  }
  return delegations;
}

// A message per dependency whose source has finished, labelled with what crossed.
export function runHandoffs(d,run){
  const handoffs=[];
  for(const edge of planConnections(d)){
    const index=d.workflow.findIndex(step=>step.id===edge.from),trace=run?.trace?.[index];
    if(!trace||trace.patch===undefined||runStepStatus(d,run,index)!=='Complete')continue;
    const step=d.workflow[index],collection=d.state.find(item=>item.key===step.output);
    handoffs.push({from:edge.from,to:edge.to,label:`${collection?.label||step.output} · ${payloadLabel(trace.patch,collection?.type)}`});
  }
  return handoffs;
}

// A finished step says what it produced, so the canvas reads as work rather than
// as a diagram. The titles are one hover away, and stay off the canvas.
function runProduced(d,run,step,index){
  if(runStepStatus(d,run,index)!=='Complete')return null;
  const value=run?.state?.[step.output],label=(d.state.find(item=>item.key===step.output)?.label||step.output).toLowerCase();
  if(Array.isArray(value)){
    const titles=value.map(item=>item.title).filter(Boolean).slice(0,3);
    return {text:`${value.length} ${value.length===1?label.replace(/s$/,''):label}`,title:titles.join(' · ')};
  }
  const words=String(value??'').trim().split(/\s+/).filter(Boolean).length;
  return words?{text:`${words} words`,title:String(value).slice(0,140)}:null;
}
function delegationCurve(from,to){const x1=from.x+canvasNodeWidth,y1=from.y+canvasNodeHeight-60,x2=to.x,y2=to.y+30;return `M ${x1} ${y1} C ${x1+60} ${y1}, ${x2-40} ${y2}, ${x2} ${y2}`;}

// The canvas itself: a node per step, a sub-agent per delegation, an edge per
// handoff and per delegation. Edges are SVG; nodes are HTML, because a <section>
// inside an <svg> would not render.
export function runCanvasMarkup(d,run,selectedId,{escape:e,agentName,outputName,capabilityName,zoom=1,backends=[]}){
  const positions=canvasPositions(d),links=planConnections(d),tools=runToolCalls(run),delegations=runDelegations(run),handoffs=runHandoffs(d,run),started=runBackends(run);
  // Results recorded against a plan that has since changed are marked, not hidden.
  const stale=staleRunSteps(run.definition,d);
  const agents=new Set(d.workflow.map(step=>step.agent)).size;
  const delegated=[...delegations.values()].reduce((total,list)=>total+list.length,0);
  const metrics=currentCanvasMetrics();
  const boardWidth=Math.max(metrics.width,...Object.values(positions).map(point=>point.x+canvasNodeWidth+360)),boardHeight=Math.max(metrics.height,...Object.values(positions).map(point=>point.y+canvasNodeHeight+80))+delegated*110;
  const edges=links.map(edge=>{const from=positions[edge.from],to=positions[edge.to];if(!from||!to)return '';const handoff=handoffs.find(item=>item.from===edge.from&&item.to===edge.to),mid=canvasEdgeMidpoint(from,to);return `<g class="canvas-edge run-edge ${handoff?'handed':''}"><path class="edge-line" d="${canvasEdgeCurve(from,to)}" marker-end="url(#flow-arrow)"></path>${handoff&&mid?`<text class="edge-label" x="${mid.x}" y="${mid.y}">${e(handoff.label)}</text>`:''}</g>`;}).join('');
  const delegationEdges=[],subagentNodes=[];
  for(const[stage,list]of delegations){const point=positions[stage];if(!point)continue;list.forEach((sub,index)=>{const x=point.x,y=point.y+canvasNodeHeight+40+index*110;delegationEdges.push(`<g class="canvas-edge delegation-edge"><path class="edge-line" d="${delegationCurve(point,{x,y})}" marker-end="url(#flow-arrow)"></path></g>`);subagentNodes.push(`<section class="run-subagent" data-status="${e(runStatusClass(sub.status==='completed'?'Complete':'Working'))}" style="left:${x}px;top:${y}px"><strong>${e(agentName(d,sub.agent))}</strong><small>delegated by ${e(agentName(d,sub.by))}${sub.depth!==undefined?` · depth ${e(sub.depth)}`:''} · ${e(sub.status)}</small><small>${e(sub.task||'')}</small>${sub.reason?`<small class="run-subagent-reason">${e(sub.reason)}</small>`:''}</section>`);});}
  const nodes=d.workflow.map((step,index)=>{
    const agent=d.agents.find(item=>item.id===step.agent)||{name:step.agent,tools:[],kind:'ai'},human=agent.kind==='human',status=runStepStatus(d,run,index),calls=tools.get(step.id)||[],subs=delegations.get(step.id)||[];
    const waiting=links.filter(edge=>edge.to===step.id&&runStepStatus(d,run,d.workflow.findIndex(item=>item.id===edge.from))!=='Complete').map(edge=>d.workflow.find(item=>item.id===edge.from)?.name||edge.from);
    const handedOn=run?.trace?.[index]?.patch!==undefined&&status==='Complete';
    const outOfDate=stale.has(step.id),produced=runProduced(d,run,step,index);
    return canvasStepMarkup({id:step.id,index,human,selected:selectedId===step.id,position:positions[step.id],escape:e,mode:'run',status:runStatusClass(status),stale:outOfDate,header:`<span class="run-node-status" data-status="${e(runStatusClass(status))}"${outOfDate?' title="This result came from an earlier version of the plan"':''}><i></i>${outOfDate?'Out of date':e(status)}</span>`,content:`<button class="step-summary" data-run-step="${e(step.id)}" aria-pressed="${selectedId===step.id}"><strong>${e(step.name)}</strong><span class="step-output ${produced?'produced':''}"${produced?.title?` title="${e(produced.title)}"`:''}>${produced?e(produced.text):`Produces ${e(outputName(d,step.output))}`}</span><span class="step-member"><span class="role-symbol small" aria-hidden="true">${e(agent.name.slice(0,1))}</span><span>${e(agentName(d,step.agent))}</span></span>${human?'':`<span class="step-backend">${e(backendLabel(backends,...(started.get(step.id)||stageBackend(d.agents.find(item=>item.id===step.agent)||{},step))))}</span>`}${calls.length?`<span class="step-tool-chips">${calls.map(call=>`<span class="run-chip ${call.denied?'denied':''}">${e(capabilityName(call.name))} ×${call.count}</span>`).join('')}</span>`:''}${subs.length?`<span class="run-delegated">Handed work to ${e(subs.map(sub=>agentName(d,sub.agent)).join(', '))}</span>`:''}${!calls.length&&!subs.length&&waiting.length&&status==='Waiting'?`<span class="run-waiting">Waiting for ${e(waiting.join(', '))}</span>`:''}${handedOn?`<span class="run-handed">Handed on ${e(outputName(d,step.output))}</span>`:''}</button>`});
  }).join('');
  return `<section class="run-workbench"><div class="canvas-heading"><span>${d.workflow.length} steps · ${agents} agents${delegated?` · ${delegated} delegation${delegated===1?'':'s'}`:''}${handoffs.length?` · ${handoffs.length} handoff${handoffs.length===1?'':'s'}`:''}${stale.size?` · ${stale.size} result${stale.size===1?'':'s'} out of date`:''}</span></div>${canvasMarkup({mode:'run',width:boardWidth,height:boardHeight,metrics,edges:flowArrow+edges+delegationEdges.join(''),overlay:subagentNodes.join(''),nodes,zoom})}</section>`;
}

// The run workbench: the canvas, with the selected step's record beside it. A
// definition that cannot be laid out (a loop saved before connections were
// checked) falls back to the work plan list rather than showing nothing.
export function runProgressMarkup(d,run,selectedId,{list='',detail='',zoom=1,escape,agentName,outputName,capabilityName,backends=[]}){
  try{
    return `<div class="run-workbench-layout">${runCanvasMarkup(d,run,selectedId,{escape,agentName,outputName,capabilityName,zoom,backends})}${detail}</div>`;
  }catch(error){
    return `${list}<p class="version-note">The canvas cannot draw this plan: ${escape(error.message)}</p>`;
  }
}
