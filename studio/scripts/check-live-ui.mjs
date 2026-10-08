// Offline checks for the actual UI projection. No browser or provider calls.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {emptyDefinition,initialState,preflightIssues,resolvePreflightIssue,validateRecordValues} from '../shared/definition.js';
const source=(await readFile(new URL('../web/live.js',import.meta.url),'utf8')).replace("'/definition.js'",JSON.stringify(new URL('../shared/definition.js',import.meta.url).href));
const {projectLiveRun,liveRunMarkup}=await import('data:text/javascript,'+encodeURIComponent(source));
const d=emptyDefinition();d.state[0].merge='append';
const conflicting=emptyDefinition();
conflicting.state[0].fields=[{key:'dependency_ids',label:'Dependency IDs',type:'string',required:true}];
conflicting.workflow[0].task='Use comma-separated stable IDs for dependency_ids, and an empty string when none.';
const issues=preflightIssues(conflicting);
assert.equal(issues.length,1,'the Software Factory contradiction is detected before execution');
assert.equal(issues[0].field,'dependency_ids');
const resolved=resolvePreflightIssue(conflicting,issues[0]);
assert.equal(preflightIssues(resolved).length,0);
assert.equal(conflicting.state[0].fields[0].required,true,'fixes do not mutate the saved definition');
assert.deepEqual(validateRecordValues({dependency_ids:''},resolved.state[0].fields),{});
assert.throws(()=>resolvePreflightIssue(resolved,issues[0]),/issue changed/);
for(const task of ['Never leave dependency_ids empty.','Do not omit dependency_ids.','Use an empty string for other_dependency_ids.','Set dependency_ids to stable IDs.']){
 const candidate=structuredClone(conflicting);candidate.workflow[0].task=task;
 assert.equal(preflightIssues(candidate).filter(issue=>issue.code==='empty-required').length,0,`no false conflict for: ${task}`);
}
const preflightContext={escape:value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),requestForm:'',outputs:'',human:'',progress:'',runtime:{available:true,tools:[]},busy:false,error:'',requestOnly:true};
const blocked=liveRunMarkup(conflicting,null,preflightContext);
assert.match(blocked,/Pre-run checks/);
assert.match(blocked,/Make optional and save/);
assert.match(blocked,/Edit step instructions/);
assert.match(blocked,/data-action="start-live" disabled/,'conflicts block the UI run button');
assert.match(liveRunMarkup(resolved,null,preflightContext),/No known output-contract conflicts found/);
assert.doesNotMatch(liveRunMarkup(resolved,null,preflightContext),/data-action="start-live" disabled/);
assert.doesNotMatch(liveRunMarkup(conflicting,null,{...preflightContext,warningsReviewed:true}),/data-action="start-live" disabled/,'reviewed heuristics do not block Run');
const budgetPlan=emptyDefinition();budgetPlan.run_budget.max_delegations=0;budgetPlan.run_budget.max_per_agent=0;
const budgetErrors=preflightIssues(budgetPlan).filter(issue=>issue.severity==='error');
assert.equal(budgetErrors.length,2);
assert.match(liveRunMarkup(budgetPlan,null,{...preflightContext,warningsReviewed:true}),/data-action="start-live" disabled/,'errors cannot be acknowledged away');
let funded=resolvePreflightIssue(budgetPlan,budgetErrors[0]);
funded=resolvePreflightIssue(funded,preflightIssues(funded).find(issue=>issue.severity==='error'));
assert.equal(preflightIssues(funded).filter(issue=>issue.severity==='error').length,0);
assert.equal(budgetPlan.run_budget.max_delegations,0,'budget fixes leave the original unchanged');
const depthPlan=emptyDefinition();depthPlan.run_budget.max_depth=0;
assert.ok(preflightIssues(depthPlan).some(issue=>issue.fix?.key==='max_depth'));
assert.equal(preflightIssues(emptyDefinition()).filter(issue=>issue.severity==='error').length,0,'unlimited budgets are valid');
const humanPlan=emptyDefinition();humanPlan.agents.forEach(member=>member.kind='human');humanPlan.run_budget.max_delegations=0;humanPlan.run_budget.max_per_agent=0;humanPlan.run_budget.max_depth=0;
assert.equal(preflightIssues(humanPlan).filter(issue=>issue.severity==='error').length,0,'human steps do not consume delegation budget');
for(const key of ['tokens','wall_clock_seconds']){const candidate=emptyDefinition();candidate.run_budget[key]=0;assert.ok(preflightIssues(candidate).some(issue=>issue.code===`zero-${key}`));}
const capability=emptyDefinition();capability.agents[0].tools=[];capability.agents[0].instructions='Perform the assigned work.';capability.workflow[0].task='Run tests. Apply the patch.';
assert.ok(preflightIssues(capability).some(issue=>issue.code==='execution-tool'));
assert.ok(preflightIssues(capability).some(issue=>issue.code==='write-tool'));
capability.agents[0].tools=['run_tests','write_file'];
assert.ok(!preflightIssues(capability).some(issue=>['execution-tool','write-tool'].includes(issue.code)));
assert.throws(()=>resolvePreflightIssue(capability,{}),/issue changed/);
capability.agents[0].tools=[];capability.workflow[0].task='Do not run tests. Propose commands. Never apply the patch.';
assert.ok(!preflightIssues(capability).some(issue=>['execution-tool','write-tool'].includes(issue.code)));
capability.agents[0].kind='human';capability.workflow[0].task='Run tests. Apply the patch.';
assert.ok(!preflightIssues(capability).some(issue=>['execution-tool','write-tool'].includes(issue.code)));
const contract=emptyDefinition();contract.state[0].statuses=['todo','done'];contract.state[0].fields=[{key:'score',label:'Score',type:'number',required:true}];contract.workflow[0].task='Set score as an array. Set status to imaginary.';
assert.ok(preflightIssues(contract).some(issue=>issue.code==='field-type'));
assert.ok(preflightIssues(contract).some(issue=>issue.code==='status-contract'));
contract.workflow[0].task='Produce findings.';
assert.ok(preflightIssues(contract).some(issue=>issue.code==='field-coverage'));
contract.workflow[0].task='Set score as a string.';
assert.ok(preflightIssues(contract).some(issue=>issue.code==='field-type'));
contract.state[0].fields[0].required=false;
assert.ok(preflightIssues(contract).some(issue=>issue.code==='field-type'),'optional fields still have types');
contract.workflow[0].task='Never set status to imaginary.';
assert.ok(!preflightIssues(contract).some(issue=>issue.code==='status-contract'));
const dependency=emptyDefinition();dependency.connections=[];dependency.workflow[1].task='Read findings and summarize.';
assert.ok(preflightIssues(dependency).some(issue=>issue.code==='input-dependency'));
dependency.workflow[1].task='Do not require findings to exist.';
assert.ok(!preflightIssues(dependency).some(issue=>issue.code==='input-dependency'),'negated requirements are not dependencies');
dependency.workflow[1].task='Read findings and summarize.';
dependency.connections=[{from:dependency.workflow[0].id,to:dependency.workflow[1].id}];
assert.ok(!preflightIssues(dependency).some(issue=>issue.code==='input-dependency'));
dependency.workflow[0].task='Produce acceptance criteria.';dependency.workflow[1].task='Summarize.';
assert.ok(preflightIssues(dependency).some(issue=>issue.code==='verification-coverage'));
dependency.workflow[1].task='Verify acceptance criteria with tests.';
assert.ok(!preflightIssues(dependency).some(issue=>issue.code==='verification-coverage'&&issue.stage===dependency.workflow[0].id));
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
