// Offline checks for the actual UI projection. No browser or provider calls.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {emptyDefinition,initialState} from '../shared/definition.js';
const source=(await readFile(new URL('../web/live.js',import.meta.url),'utf8')).replace("'/definition.js'",JSON.stringify(new URL('../shared/definition.js',import.meta.url).href));
const {projectLiveRun,liveRunMarkup}=await import('data:text/javascript,'+encodeURIComponent(source));
const d=emptyDefinition();d.state[0].merge='append';
d.agents.push({id:'reviewer',name:'Reviewer',kind:'human',tools:[],instructions:'Review'});
d.workflow=[
  {id:'left',agent:'researcher',task:'Left',output:'findings',name:'Left'},
  {id:'right',agent:'researcher',task:'Right',output:'findings',name:'Right'},
  {id:'review',agent:'reviewer',task:'Review',output:'summary',name:'Review'},
  {id:'final',agent:'researcher',task:'Finalize',output:'summary',name:'Final'}
];
d.connections=[{from:'left',to:'review'},{from:'review',to:'final'},{from:'right',to:'final'}];
const record=id=>({id,title:id,body:id,status:'recorded',source_refs:['actual-source.md:1'],contributions:[{agent:'researcher',stage:id,run_id:'actual-'+id}]});
const a=record('left'),b=record('right'),initial=initialState(d),left={...initial,findings:[a]},joined={...initial,findings:[a,b]};
const event=(sequence,event)=>({sequence,event});
const run={id:'actual-run',status:'waiting_for_human',request:{definition:d,revision:2,task:'Actual request',inputs:{sources:'Actual source'}},metrics:{tokens:24},
  events:[event(1,{type:'ApplicationStarted',state:initial}),event(2,{type:'ApplicationStateChanged',stage:'left',patch:[a],state:left,version:1}),event(3,{type:'ApplicationStateChanged',stage:'right',patch:[b],state:joined,version:2})],
  reviews:[{id:'actual-review',status:'pending',stage:'review',request:{state:left}}]};
let projected=projectLiveRun(run);
assert.equal(projected.mode,'waiting_human');assert.equal(projected.activeStep,2);
assert.deepEqual(projected.completedSteps,['left','right']);
assert.deepEqual(projected.trace[1].input.findings,[]);
assert.deepEqual(projected.trace[2].input.findings,[a]);
assert.deepEqual(projected.state.findings.map(record=>record.contributions[0].run_id),['actual-left','actual-right']);
run.status='completed';run.reviews[0].status='consumed';
run.events.push(event(4,{type:'ApplicationStateChanged',stage:'review',patch:'Human contribution',state:{...joined,summary:'Human contribution'},version:3}),
  event(5,{type:'ApplicationStateChanged',stage:'final',patch:'Actual final text',state:{...joined,summary:'Actual final text'},version:4}));
projected=projectLiveRun(run);
assert.deepEqual(projected.trace[3].input.findings,[a,b]);
assert.equal(projected.trace[3].input.summary,'Human contribution');
assert.equal(projected.state.summary,'Actual final text');assert.equal(projected.activeStep,null);
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const markup=liveRunMarkup(d,projected,{escape,requestForm:'',outputs:'',human:'',progress:'',history:[],runtime:{available:true,tools:[]},busy:false,error:'<script>untrusted</script>'});
assert.match(markup,/LOCAL RUN/);assert.match(markup,/Actual|Results are ready/);
assert.ok(!markup.includes('<script>untrusted</script>'));assert.match(markup,/&lt;script&gt;/);
assert.ok(!markup.includes('Sample complete'));assert.ok(markup.includes('Not reported'));
console.log('PASS: live UI restores actual state/provenance, branch isolation, review inputs, final results, escaped messages and unreported metrics.');
const sourced=structuredClone(d);sourced.agents[0].tools=['read_file'];
const options={escape,requestForm:'',outputs:'',human:'',progress:'',history:[],busy:false,error:'',applicationId:'app',runtime:{available:true,tools:['read_file'],resource_tools:['read_file'],resources:[{id:'case-a',name:'<Case A>',applications:['app']},{id:'private',name:'Private',applications:['another-app']}]}};
const unbound=liveRunMarkup(sourced,null,options);
assert.match(unbound,/data-source-resource/);assert.match(unbound,/&lt;Case A&gt;/);assert.ok(!unbound.includes('Private'));
assert.match(unbound,/data-action="start-live" disabled/);
assert.ok(!liveRunMarkup(sourced,null,{...options,source:'case-a'}).includes('data-action="start-live" disabled'));
run.request.resources={source:'case-a'};
assert.deepEqual(projectLiveRun(run).resources,{source:'case-a'});
assert.match(liveRunMarkup(sourced,projectLiveRun(run),{...options,source:'case-a'}),/Source resource/);
console.log('PASS: named resource selection, authorization filtering, escaping, start validation and saved resource display.');
