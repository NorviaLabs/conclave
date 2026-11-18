// What a run used, step by step, and how close it came to its limits. The
// mockups are docs/studio/mockups/usage.html. Counts and times only: the
// record holds no prompt, reply or tool output, and neither does this.
const LIMITS={
  tokens:{label:'Tokens',limit:'token limit',unit:'tokens',after:'Once it is reached, no new step starts.'},
  tool_calls:{label:'Tool uses',limit:'tool-use limit',unit:'tool uses',after:'Once it is reached, further tool uses are refused.'},
  wall_clock_seconds:{label:'Working time',limit:'time limit',unit:'working time',after:'Once it is reached, no new step starts.'},
  cost_usd:{label:'Cost',limit:'cost limit',unit:'',after:'Once it is reached, no new step starts.'},
};
const WARN_ICON='<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M8 1.5l7 12.5H1z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6v4M8 12v.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
const number=value=>Number.isFinite(Number(value))?Number(value):0;
const times=count=>count===1?'once':count===2?'twice':`${count} times`;
const percent=(used,limit)=>Math.round(number(used)/number(limit)*100);
export function countText(value){return Math.round(number(value)).toLocaleString('en-US');}
export function durationText(seconds){
  const value=number(seconds);
  if(value<1)return 'under 1 s';
  const whole=Math.round(value),pad=n=>String(n).padStart(2,'0');
  if(whole>=3600)return `${Math.floor(whole/3600)} h ${pad(Math.floor(whole%3600/60))} m`;
  if(whole>=60)return `${Math.floor(whole/60)} m ${pad(whole%60)} s`;
  return `${whole} s`;
}
// An amount in a limit's own unit: "41,200", "4 m 15 s", "$4.10".
function amountText(dimension,value){
  if(dimension==='wall_clock_seconds')return durationText(value);
  if(dimension==='cost_usd')return `$${number(value).toFixed(2)}`;
  return countText(value);
}
function amountOf(dimension,used,limit){
  const unit=LIMITS[dimension]?.unit||'';
  if(dimension==='wall_clock_seconds')return `${durationText(used)} of ${durationText(limit)} working time`;
  return `${amountText(dimension,used)} of ${amountText(dimension,limit)}${unit&&dimension!=='cost_usd'?' '+unit:''}`;
}
const stepName=(d,id)=>d?.workflow?.find(step=>step.id===id)?.name||id;
const backends=list=>(list||[]).map(name=>name==='default'?'the default backend':name).join(', ');
function toolNotes(item){return [item.tool_failures?`${item.tool_failures} failed`:'',item.tool_denied?`${item.tool_denied} refused`:''].filter(Boolean).join(', ');}
function tokenCells(item,e){
  if(item.model_calls&&!item.measured_calls)return `<td colspan="2">not measured by ${e(backends(item.unmeasured)||'its backend')}</td>`;
  const prefix=item.measured_calls<item.model_calls?'at least ':'';
  return `<td>${prefix}${countText(item.input_tokens)}</td><td>${prefix}${countText(item.output_tokens)}</td>`;
}
function limitRows(usage,e){
  const rows=Object.entries(usage.limits||{}).filter(([dimension,item])=>LIMITS[dimension]&&item.limit!==null&&item.limit!==undefined&&number(item.limit)>0);
  if(!rows.length)return '';
  return `<div class="limit-list" aria-label="Limits">${rows.map(([dimension,item])=>{
    const info=LIMITS[dimension],share=percent(item.used,item.limit),warn=item.warn_at===null||item.warn_at===undefined?null:percent(item.warn_at,item.limit);
    const close=warn!==null&&number(item.used)>=number(item.warn_at);
    const said=`${amountOf(dimension,item.used,item.limit)}, ${share}%${warn===null?'':close?`, warned at ${warn}%`:`, warns at ${warn}%`}`;
    return `<div class="limit-row"><span>${info.label}</span><div class="limit-bar${close?' close':''}" role="img" aria-label="${e(said)}"><span style="width:${Math.min(100,Math.max(0,share))}%"></span>${warn===null?'':`<i style="left:${Math.min(100,Math.max(0,warn))}%"></i>`}</div><span class="limit-value">${e(amountText(dimension,item.used))} / ${e(amountText(dimension,item.limit))}</span></div>`;
  }).join('')}</div>`;
}
// The Usage section of a run's information.
export function usageMarkup(run,{escape:e}){
  const usage=run?.usage;
  if(!usage)return '<p class="field-help">Usage was not reported for this run.</p>';
  const total=usage.total||{};
  if(!usage.recorded){
    const parts=[total.tool_calls!==null&&total.tool_calls!==undefined?`${countText(total.tool_calls)} tool ${total.tool_calls===1?'use':'uses'}`:'',
      total.seconds!==null&&total.seconds!==undefined?`${durationText(total.seconds)} working time`:''].filter(Boolean);
    const tokens=total.tokens!==null&&total.tokens!==undefined?`${countText(total.tokens)} tokens`:'tokens not reported';
    return `<p class="field-help">Per-step usage was not recorded for this run.${parts.length||tokens?` The run as a whole: ${[parts.join(', '),tokens].filter(Boolean).join('; ')}.`:''}</p>`;
  }
  const d=run.definition,steps=Object.entries(usage.steps||{});
  const rows=steps.map(([id,item])=>{
    const ran=item.runs>1?`<span class="note">ran ${times(item.runs)}</span>`:'',members=item.member_calls?`<span class="note">${item.member_calls} by a member it asked</span>`:'',notes=toolNotes(item);
    return `<tr><td>${e(stepName(d,id))}${ran}</td><td>${countText(item.model_calls)}${members}</td>${tokenCells(item,e)}<td>${countText(item.tool_calls)}${notes?`<span class="note">${e(notes)}</span>`:''}</td><td>${durationText(item.seconds)}</td></tr>`;
  }).join('');
  const partial=total.measured_calls<total.model_calls,prefix=partial?'at least ':'';
  const table=steps.length?`<table class="usage-table"><thead><tr><th>Step</th><th>Model calls</th><th>Tokens in</th><th>Tokens out</th><th>Tool calls</th><th>Time</th></tr></thead><tbody>${rows}</tbody><tfoot><tr><td>Run</td><td>${countText(total.model_calls)}</td>${total.model_calls&&!total.measured_calls?`<td colspan="2">not measured</td>`:`<td>${prefix}${countText(total.input_tokens)}</td><td>${prefix}${countText(total.output_tokens)}</td>`}<td>${countText(total.tool_calls)}</td><td>${durationText(total.seconds)}</td></tr></tfoot></table>`:'<p class="field-help">No step has finished yet.</p>';
  let tokens='';
  if(total.model_calls&&!total.measured_calls)tokens=`Tokens were not measured by ${e(backends(usage.unmeasured_backends)||'these backends')}.`;
  else if(total.model_calls)tokens=partial?`Tokens: at least ${countText(total.tokens)} — ${countText(total.measured_calls)} of ${countText(total.model_calls)} calls measured.`:`Tokens: ${countText(total.tokens)} — all ${countText(total.model_calls)} calls measured.`;
  const cost=total.cost_usd===null||total.cost_usd===undefined?'Cost is not reported by these backends.':`Cost: $${number(total.cost_usd).toFixed(2)}.`;
  return `${table}<p class="field-help">${[tokens,cost].filter(Boolean).join(' ')}</p>${limitRows(usage,e)}`;
}
// One step's usage, in its details. `revisions`: how often a reviewer sent it back.
export function stepUsageMarkup(run,stepId,{escape:e,revisions=0}){
  const item=run?.usage?.recorded?run.usage.steps?.[stepId]:null;
  if(!item)return '';
  const tokens=item.model_calls&&!item.measured_calls?{value:'–',note:`not measured by ${backends(item.unmeasured)||'its backend'}`}
    :{value:countText(number(item.input_tokens)+number(item.output_tokens)),note:item.measured_calls<item.model_calls?`at least; ${item.measured_calls} of ${item.model_calls} calls measured`:'all measured'};
  const tiles=[[countText(item.model_calls),'model calls',item.member_calls?`${item.member_calls} by a member it asked`:''],[tokens.value,'tokens',tokens.note],
    [countText(item.tool_calls),'tool calls',toolNotes(item)],[durationText(item.seconds),'working time','']];
  const ran=item.runs>1?`<p class="field-help">Ran ${times(item.runs)}${revisions?`: the reviewer asked for changes ${times(revisions)}.`:'. These numbers add up every time it ran.'}</p>`:'';
  return `<div class="step-usage">${tiles.map(([value,label,note])=>`<div><strong>${e(value)}</strong><span>${label}</span>${note?`<em>${e(note)}</em>`:''}</div>`).join('')}</div>${ran}`;
}
// A limit the run came close to: said once, in the run's overview.
export function usageWarningMarkup(run,{escape:e}){
  const usage=run?.usage;
  if(!usage?.warnings?.length)return '';
  const seen=new Set();
  return usage.warnings.filter(item=>LIMITS[item.dimension]&&!seen.has(item.dimension)&&seen.add(item.dimension)).filter(item=>run.budgetStop?.dimension!==item.dimension).map(item=>{
    const info=LIMITS[item.dimension],current=usage.limits?.[item.dimension],limit=current?.limit??item.limit,used=Math.max(number(current?.used),number(item.used));
    const said=limit?`<strong>Close to the ${info.limit}:</strong> ${e(amountOf(item.dimension,used,limit))} (${percent(used,limit)}%). ${info.after}`
      :`<strong>${info.label} passed its warning level:</strong> ${e(amountText(item.dimension,used))}.`;
    return `<div class="usage-warning" role="status">${WARN_ICON}<span>${said}</span></div>`;
  }).join('');
}
