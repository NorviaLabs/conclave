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
const form='<textarea id="live-request" data-live-input="task"></textarea>';
const runOptions={escape,requestForm:form,outputs:'',human:'',progress:'',runtime:{available:true,tools:[]},busy:false,error:'<script>untrusted</script>'};
// With a run open the screen is the canvas: the form and its messages stay out
// of the way. Play in the page header brings them back; the form carries its own
// way out. Requests are listed in the page's History dialog, not here.
const markup=liveRunMarkup(d,projected,runOptions);
assert.match(markup,/LOCAL RUN/);assert.match(markup,/Actual|Results are ready/);
assert.ok(!markup.includes('<script>untrusted</script>'));
assert.ok(!markup.includes('id="live-request"'),'an open run does not repeat the request form');
assert.match(markup,/&lt;script&gt;/,'run errors remain visible and escaped');
assert.ok(!/data-live-history|id="live-history"/.test(markup),'the run screen holds no request list');
assert.ok(!markup.includes('Sample complete'));assert.ok(markup.includes('Not reported'));
const reopened=liveRunMarkup(d,projected,{...runOptions,requestOnly:true});
assert.ok(reopened.includes('id="live-request"'),'the form comes back when asked');
// The header owns that toggle now: one control dismisses the form, not two.
assert.doesNotMatch(reopened,/data-action="show-request-form"/,'the form offers no second way to dismiss itself');
assert.match(reopened,/data-action="start-live"/,'and still starts the request');
assert.ok(!reopened.includes('<script>untrusted</script>'));assert.match(reopened,/&lt;script&gt;/,'the form escapes what it renders');
// Without a run there is nothing to look at, so the form is the screen.
const start=liveRunMarkup(d,null,{...runOptions,requestOnly:true});
assert.match(start,/data-action="start-live"/);assert.match(start,/>Run</);
assert.ok(!start.includes('data-action="show-request-form"'),'nothing to cancel before a run exists');
console.log('PASS: live UI restores actual state/provenance, branch isolation, review inputs, final results, escaped messages and unreported metrics; an open run shows the canvas with the request form behind one button.');
const sourced=structuredClone(d);sourced.agents[0].tools=['read_file'];
const options={escape,requestOnly:true,requestForm:'',outputs:'',human:'',progress:'',busy:false,error:'',applicationId:'app',runtime:{available:true,tools:['read_file'],resource_tools:['read_file'],resources:[{id:'case-a',name:'<Case A>',applications:['app']},{id:'private',name:'Private',applications:['another-app']}]}};
const unbound=liveRunMarkup(sourced,null,options);
assert.match(unbound,/data-source-resource/);assert.match(unbound,/&lt;Case A&gt;/);assert.ok(!unbound.includes('Private'));
assert.match(unbound,/data-action="start-live" disabled/);
assert.ok(!liveRunMarkup(sourced,null,{...options,source:'case-a'}).includes('data-action="start-live" disabled'));
sourced.resources={source:'case-a'};
assert.ok(!liveRunMarkup(sourced,null,options).includes('data-action="start-live" disabled'));
assert.match(liveRunMarkup(sourced,null,options),/Use configured source/);
const webOnly=structuredClone(sourced);webOnly.agents[0].tools=['web_search'];delete webOnly.resources;
const webMarkup=liveRunMarkup(webOnly,null,{...options,runtime:{available:true,tools:['web_search'],resource_tools:['read_file'],resources:[]}});
assert.ok(!webMarkup.includes('data-source-resource'));
assert.ok(!webMarkup.includes('data-action="start-live" disabled'));
run.request.resources={source:'case-a'};
assert.deepEqual(projectLiveRun(run).resources,{source:'case-a'});
assert.match(liveRunMarkup(sourced,projectLiveRun(run),{...options,source:'case-a',requestOnly:false}),/Source resource/);
console.log('PASS: named resource selection, authorization filtering, escaping, start validation and saved resource display.');

// The page must not claim to be running on this computer when the host says
// otherwise, and a host that reports no profile is local by default.
const hostedRuntime={available:true,tools:[],profile:'hosted'};
const hosted=liveRunMarkup(d,null,{escape,requestForm:'',outputs:'',human:'',progress:'',busy:false,error:'',runtime:hostedRuntime});
assert.match(hosted,/HOSTED RUN/);assert.match(hosted,/configured for this workspace/);
assert.ok(!hosted.includes('on this computer'));
assert.match(liveRunMarkup(d,projected,{escape,requestForm:'',outputs:'',human:'',progress:'',busy:false,error:'',runtime:hostedRuntime}),/HOSTED RUN/,'an open run still states the profile it runs under');
assert.ok(!liveRunMarkup(d,projected,{escape,requestForm:'',outputs:'',human:'',progress:'',busy:false,error:'',runtime:hostedRuntime}).includes('on this computer'));
const unprofiled=liveRunMarkup(d,null,{escape,requestForm:'',outputs:'',human:'',progress:'',busy:false,error:'',runtime:{available:false,tools:[],reason:'No model backend is configured for this host.'}});
assert.match(unprofiled,/LOCAL RUN/);assert.match(unprofiled,/No AI provider was found on this computer/);
console.log('PASS: the page renders from the reported host profile instead of assuming a local machine.');
