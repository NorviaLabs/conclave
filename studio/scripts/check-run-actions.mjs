// Exercise the actual Run / Run again handlers without a browser or backend.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source=await readFile(new URL('../web/studio.js',import.meta.url),'utf8');
const functionSource=(name,next)=>source.slice(source.indexOf(`function ${name}(`),source.indexOf(`function ${next}(`));
const listeners={},elements={};
for(const id of ['run-form','run-dialog','run-configuration','run-dialog-title','close-run'])elements[id]={
  innerHTML:'',textContent:'',open:false,button:{disabled:false},
  addEventListener(type,handler){listeners[`${id}:${type}`]=handler;},
  querySelector(){return this.button;},showModal(){this.open=true;},close(){this.open=false;}
};
const posts=[];
let fail=false,selected=null;
const context=vm.createContext({
  $:id=>elements[id],structuredClone,
  render(){},stopFollowingLiveRun(){},pollLiveRun(){},notify(){},
  studioLiveMarkup:()=>'<textarea>request</textarea>',
  current:()=>({revision:4,definition:{inputs:[]}}),
  validateDefinition(){},preflightIssues:()=>[],
  validateRequestInputs:(_d,inputs)=>inputs,
  request:async(path,options)=>{if(fail)throw new Error('Backend unavailable');posts.push({path,body:JSON.parse(options.body)});return {id:`run_${posts.length}`};},
  selectLiveRecord:record=>{selected=record;},refreshLiveHistory:async()=>{}
});
vm.runInContext(`let saving=false,liveBusy=false,liveError='',view='plan',currentId='app',preview=null;
const requests={live:'Repeat this task'},requestInputs={live:{app:{context:'Prior context'}}},requestResources={app:'source-a'};
${functionSource('openRunConfiguration','nextIdentifier')}
async ${functionSource('liveAction','studioLiveMarkup')}
${source.slice(source.indexOf("$('run-form').addEventListener('input'"),source.indexOf("$('close-run').addEventListener('click'"))}
`,context);
vm.runInContext('openRunConfiguration()',context);
assert.equal(elements['run-dialog'].open,true,'Run opens a modal');
assert.equal(vm.runInContext('view',context),'plan','opening configuration leaves the canvas page in place');
const submit=()=>listeners['run-form:submit']({preventDefault(){},submitter:elements['run-form'].button});
await submit();
assert.equal(posts.length,1);
assert.deepEqual(posts[0],{path:'/api/runs',body:{version:1,application_id:'app',revision:4,task:'Repeat this task',inputs:{context:'Prior context'},resources:{source:'source-a'}}});
assert.equal(elements['run-dialog'].open,false,'successful creation dismisses the modal');
assert.equal(selected.id,'run_1');
vm.runInContext("preview={live:true,runId:'run_1',mode:'completed'};openRunConfiguration()",context);
assert.equal(elements['run-dialog-title'].textContent,'Run again');
listeners['run-form:input']({target:{dataset:{request:'live'},value:'Updated request'}});
listeners['run-form:input']({target:{dataset:{requestField:'context'},value:'Updated context'}});
elements['run-form'].button.disabled=false;
await submit();
assert.equal(posts.length,2,'Run again creates a fresh run rather than resuming the old one');
assert.equal(posts[1].body.task,'Updated request');
assert.deepEqual(posts[1].body.inputs,{context:'Updated context'});
vm.runInContext('openRunConfiguration()',context);
elements['run-form'].button.disabled=false;
fail=true;
await submit();
assert.equal(elements['run-dialog'].open,true,'failure keeps the form available to retry');
assert.equal(vm.runInContext('liveError',context),'Backend unavailable');
assert.equal(vm.runInContext('liveBusy',context),false);
console.log('PASS: Run and Run again open a modal on the canvas, create fresh runs with task/context/source, close on success, and retain errors for retry.');

// The canvas action bar is the run controller. It is executed here, because the
// page's own module cannot be imported: what a person can press while a run is
// open has to follow from the run's state, not from a guess.
const controllerSource = source.slice(source.indexOf('function runControllerMarkup('), source.indexOf('\n}', source.indexOf('function runControllerMarkup('))) + '\n}';
const bar = vm.createContext({
  uiIcon: name => `<svg class="ui-icon" data-icon="${name}"></svg>`,
  liveBusy: false, runtime: {available: true}, preview: null,
  console
});
vm.runInContext(`${controllerSource}`, bar);
const controlsFor = preview => vm.runInContext('preview = ' + JSON.stringify(preview) + '; runControllerMarkup()', bar);
const actions = markup => [...markup.matchAll(/data-action="([^"]+)"/g)].map(match => match[1]);
assert.deepEqual(actions(controlsFor(null)), ['run'], 'a plan offers Run');
assert.deepEqual(actions(controlsFor({live: true, mode: 'running'})), ['pause-live', 'cancel-live'], 'a going run offers Pause and Stop');
assert.deepEqual(actions(controlsFor({live: true, mode: 'pending'})), ['pause-live', 'cancel-live'], 'a starting run too');
assert.deepEqual(actions(controlsFor({live: true, mode: 'paused'})), ['resume-live', 'cancel-live'], 'a paused run offers Run and Stop');
// What a run is waiting for stays in the status line above the canvas, in words,
// so the bar offers it once and never without a label.
assert.deepEqual(actions(controlsFor({live: true, mode: 'waiting_human'})), ['cancel-live'], 'a run that needs a person offers no duplicate in the bar');
assert.deepEqual(actions(controlsFor({live: true, mode: 'completed'})), ['run'], 'a finished run offers another run');
for (const mode of ['stopped', 'failed', 'cancelled', 'interrupted']) {
  assert.deepEqual(actions(controlsFor({live: true, mode})), ['run'], `a ${mode} run offers another run`);
}
// Icons only, and each one named for a screen reader.
for (const mode of [null, {live: true, mode: 'running'}, {live: true, mode: 'paused'}, {live: true, mode: 'completed'}]) {
  const markup = controlsFor(mode);
  assert.ok(!/>\s*(Run|Stop|Pause|Resume|Edit plan|View results|Review input)\s*</.test(markup), 'the bar carries icons, not words');
  for (const match of markup.matchAll(/<button[^>]*data-action="([^"]+)"([^>]*)>/g)) {
    assert.match(match[2], /aria-label="[^"]+"/, `${match[1]} is named for a screen reader`);
    assert.match(match[2], /title="[^"]+"/, `${match[1]} names itself on hover`);
  }
}
console.log('PASS: the canvas action bar offers exactly what the run state allows, as named icons with no words.');
