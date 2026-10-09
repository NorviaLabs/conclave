import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const read=path=>readFile(new URL(path,import.meta.url),'utf8');
const [source,canvas,inspector,css,live]=await Promise.all(['../web/studio.js','../web/canvas-ui.js','../web/workbench-ui.js','../web/studio.css','../web/live.js'].map(read));
const canvasModule=canvas;
const stepTests=await readFile(new URL('../web/step-tests.js',import.meta.url),'utf8');
// Execute mode switching: navigating never discards the draft or historical run.
const start=source.indexOf('  if(button.dataset.workspaceMode){');
const end=source.indexOf('  // A menu item closes',start);
const handler=source.slice(start,end);
const draft={name:'Unsaved draft'},record={live:true,runId:'historical'};
const context={button:{dataset:{workspaceMode:'test'}},workspaceMode:'run',view:'plan',showPlanInspector:false,preview:record,draft,render(){},followLiveRun(){},openRunConfiguration(){throw Error('Not required');}};
for(const mode of ['test','build','results','run']){
  context.button.dataset.workspaceMode=mode;
  runInNewContext(`(function(){${handler}})()`,context);
  assert.equal(context.workspaceMode,mode);
  assert.equal(context.preview,record);
  assert.equal(context.draft,draft);
}
assert.equal(context.showPlanInspector,true);
assert.doesNotMatch(source,/dockOffsets|dockDrag|applyDockOffset/);
assert.match(canvas,/>Test step<\/span>/);
assert.match(canvas,/Select a step to test/);
assert.match(canvas,/>Add step<\/span>/);
assert.match(source,/>Run workflow<\/span>/);
assert.match(inspector,/inspector-title[\s\S]{0,190}>Test this step/);
assert.match(inspector,/<summary>Depends on · edit dependencies<\/summary>/);
assert.match(inspector,/<h4>Inputs<\/h4>/);
assert.match(inspector,/<h4>Output<\/h4>/);
assert.match(inspector,/Advanced · inputs as JSON/);
assert.match(inspector,/Advanced · output as JSON/);
assert.match(inspector,/Open test results/);
assert.match(source,/function resultsWorkspaceMarkup\(\)/);
assert.match(source,/These are run outputs, not test outputs/);
assert.match(live,/Completed work is saved/);
assert.match(css,/\.workspace-modes button\[aria-pressed="true"\]/);
// Readable values: recognise a value without printing the payload, and never
// inject it into the page unescaped.
const io=inspector.slice(inspector.indexOf('const preview=value=>'),inspector.indexOf('let resources;'));
const previewFn=new Function('return '+io.slice(io.indexOf('const preview=value=>')+6,io.indexOf('const empty=value=>')).replace(/;$/,''))();
assert.equal(previewFn('  A short brief.  '),'A short brief.');
assert.equal(previewFn([{id:'a',title:'One'},{id:'b',title:'Two'}]),'2 entries · One, Two');
assert.equal(previewFn([]),'No entries');
assert.equal(previewFn(null),'');
assert.ok(previewFn('x'.repeat(400)).length<=201);
assert.match(inspector,/e\(preview\(/);
assert.doesNotMatch(inspector,/innerHTML=\$\{preview/);

// A connection says what it means: order first, then the shared collection, and
// never a private hand-off between two steps that does not exist.
const meaningSource=inspector.slice(inspector.indexOf('function connectionMeaning('),inspector.indexOf('export function canvasEdgePaths('));
const meaning=new Function('return '+meaningSource.trim().replace(/\}$/,'}'))();
const escapeText=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sample={workflow:[{id:'a',name:'Research',output:'findings'},{id:'b',name:'Draft',output:'brief'}],state:[{key:'findings',label:'Findings'},{key:'brief',label:'Brief'}]};
assert.equal(meaning(sample,'a','b',escapeText),'Draft waits for Research; Findings is shared with Draft and every step after it.');
const named={workflow:[{id:'a',name:'<script>',output:'findings'},{id:'b',name:'Draft',output:'brief'}],state:sample.state};
assert.doesNotMatch(meaning(named,'a','b',escapeText),/<script>/);
// The chain behind the selected connection is drawn, and every connection
// describes itself where it is, not only in a menu.
assert.match(inspector,/const chain=new Set\(\)/);
assert.match(inspector,/branch:chain\.has\(step\.id\)/);
assert.match(inspector,/canvasEdgePaths\(d,positions,e,ctx\.selectedEdge,chain,hidden\)/);
// Groups arrange the plan and nothing else: they are drawn, folded and edited,
// and the runtime never reads them.
assert.match(inspector,/export function canvasGroupBoxes\(d,positions,e,\{hidden\}=\{\}\)/);
assert.match(inspector,/data-group-toggle="\$\{e\(group\.id\)\}"/);
assert.match(inspector,/still runs/);
assert.match(inspector,/filter\(step=>!hidden\.has\(step\.id\)\)/,'a folded group hides its cards');
assert.match(inspector,/filter\(edge=>!hidden\?\.has\(edge\.from\)&&!hidden\?\.has\(edge\.to\)\)/,'and the connections that would dangle');
assert.match(inspector,/data-step-group="\$\{e\(selected\.id\)\}"/,'a step can be put in a group where the step is edited');
assert.match(inspector,/data-add-group="\$\{e\(selected\.id\)\}"/);
assert.match(source,/function togglePlanGroup\(id\)/);
assert.match(source,/function addPlanGroup\(stepId,name\)/);
assert.match(source,/function setStepGroup\(stepId,id\)/);
assert.match(source,/use up to 20 canvas groups/i);
assert.match(css,/\.canvas-group\.collapsed\{/);
assert.match(inspector,/class="canvas-edge-note" role="status"/);
assert.match(inspector,/aria-label="\$\{label\}\. \$\{meaning\}"/);
assert.match(css,/\.canvas-edge-note\{position:absolute/);
assert.match(css,/\.canvas-edge\.branch \.edge-line/);
assert.match(canvasModule,/branch=false/);

// A saved example is visible on the card, and says when it no longer matches the
// plan rather than letting the refusal arrive on the next test.
assert.match(source,/exampleBadge:stepExample/);
assert.match(source,/function ensureStepExamples\(\)\{/);
assert.match(source,/if\(stepExamplesApp===currentId\)return;/);
assert.match(source,/outdated=JSON\.stringify\(fixture\.definition\.state\)/);
assert.match(source,/onTestsChanged:\(\)=>\{stepExamplesApp=null;ensureStepExamples\(\);\}/);
assert.match(inspector,/node-example \$\{example\.outdated\?'outdated':''\}/);
assert.match(inspector,/Example out of date/);
assert.match(inspector,/pinned from a run/);
assert.match(inspector,/written by hand/);
assert.match(css,/\.graph-canvas \.node-example\.outdated\{border-color:var\(--red\)/);
assert.match(stepTests,/onTestsChanged=\(\)=>\{\}/,'the controller accepts a change callback');
assert.equal((stepTests.match(/onTestsChanged\(\)/g)||[]).length,3,'every saved example and started test refreshes the cards');

// Finding a step: the match rule is executed, and the field keeps its caret
// across a re-render like every other text field on the page.
const matchSource=source.slice(source.indexOf('function planSearchMatches('),source.indexOf('// Marking is done on the drawn canvas'));
const planDraft={workflow:[{id:'s1',name:'Research the market',agent:'a1',output:'o1'},{id:'s2',name:'Draft the brief',agent:'a2',output:'o2'},{id:'s3',name:'Approve it',agent:'a1',output:'o3'}],agents:[{id:'a1',name:'Analyst'},{id:'a2',name:'Writer'}],state:[{key:'o1',label:'Findings'},{key:'o2',label:'Brief'},{key:'o3',label:'Approval'}]};
const matchContext={planSearch:'',draft:()=>planDraft,agentName:(d,id)=>d.agents.find(a=>a.id===id)?.name||id,outputName:(d,key)=>d.state.find(s=>s.key===key)?.label||key};
runInNewContext(matchSource+'\nglobalThis.find=planSearchMatches;',matchContext);
assert.equal(matchContext.find().join(','),'','an empty query matches nothing');
matchContext.planSearch='brief';assert.equal(matchContext.find().join(','),'s2','a step answers to what it produces, and two steps sharing one output both match');
matchContext.planSearch='analyst';assert.equal(matchContext.find().join(','),'s1,s3','and to the member who does it');
matchContext.planSearch='RESEARCH';assert.equal(matchContext.find().join(','),'s1','matching ignores case');
matchContext.planSearch='nothing here';assert.equal(matchContext.find().join(','),'');
assert.match(source,/'planSearch'\]/,'the field keeps focus and caret across a re-render');
assert.match(source,/node\.classList\.toggle\('match',query&&hit\)/,'matches are marked on the drawn canvas');
assert.match(source,/node\.classList\.toggle\('muted',query&&!hit\)/,'and the rest are dimmed rather than hidden');
assert.match(source,/if\(event\.key==='Enter'\)\{event\.preventDefault\(\);stepThroughSearch\(event\.shiftKey\?-1:1\)/,'Enter steps through the matches');
assert.match(source,/if\(button\.hasAttribute\('data-plan-search-next'\)\)\{stepThroughSearch\(\);return;\}/,'and the button stands for the same thing');
assert.match(inspector,/data-plan-search value="\$\{e\(ctx\.search\|\|''\)\}"/);
assert.match(inspector,/placeholder="Find a step by name, member or output"/);
assert.match(inspector,/data-plan-search-count/);
assert.match(css,/\.graph-canvas \.plan-node\.match\{border-color:var\(--mint\)/);

const groupSource=inspector.slice(inspector.indexOf('export function canvasGroupBoxes('),inspector.indexOf('// What a connection means'));
const groupFn=new Function('canvasNodeWidth','canvasNodeHeight','return '+groupSource.slice(groupSource.indexOf('export ')+7).trim().replace(/\}$/,'}'))(350,220);
const groupDraft={canvas:{groups:[{id:'research',name:'Research',collapsed:false},{id:'delivery',name:'Delivery',collapsed:true}]},workflow:[{id:'s1',group:'research'},{id:'s2',group:'research'},{id:'s3',group:'delivery'},{id:'s4'}]};
const boxes=groupFn(groupDraft,{s1:{x:100,y:100},s2:{x:600,y:100},s3:{x:1100,y:100},s4:{x:1600,y:100}},escapeText);
assert.match(boxes,/data-canvas-group="research"/);
assert.match(boxes,/data-canvas-group="delivery"/);
assert.match(boxes,/data-group-toggle="research" aria-expanded="true"/);
assert.match(boxes,/data-group-toggle="delivery" aria-expanded="false"/,'a folded group says it is folded');
assert.match(boxes,/2 steps/);
assert.match(boxes,/1 step hidden · still runs/,'and says the plan still runs them');
assert.equal((boxes.match(/class="canvas-group /g)||[]).length,2,'a box per group');
const research=boxes.slice(boxes.indexOf('data-canvas-group="research"'),boxes.indexOf('data-canvas-group="delivery"'));
const delivery=boxes.slice(boxes.indexOf('data-canvas-group="delivery"'));
assert.match(research,/width:902px/,'a box spans its steps: 600+350+26 minus 100-26');
assert.match(delivery,/width:260px/,'a folded one shrinks to its header');
const escaped=groupFn({canvas:{groups:[{id:'g',name:'<script>',collapsed:false}]},workflow:[{id:'s1',group:'g'}]},{s1:{x:0,y:0}},escapeText);
assert.doesNotMatch(escaped,/<script>/);
assert.equal(groupFn({canvas:{groups:[{id:'g',name:'Empty'}]},workflow:[]},{},escapeText),'','a group with no placed step draws nothing');

// The step card is read, not scrolled: no nested scroll container, no reordering
// that hides a row, and every value wraps or clamps instead of disappearing.
assert.match(css,/\.graph-canvas \.plan-node\{overflow:hidden\}/);
assert.match(css,/\.graph-canvas \.step-summary\{overflow:hidden/);
assert.match(css,/\.graph-canvas \.step-facts div\{display:grid;grid-template-columns:64px minmax\(0,1fr\)/);
assert.match(css,/\.graph-canvas \.step-facts dd\.step-output\{display:-webkit-box;-webkit-line-clamp:2/);
assert.match(css,/\.graph-canvas \.step-backend\{display:-webkit-box;-webkit-line-clamp:2/);
assert.match(css,/\.graph-canvas \.step-summary>strong\{flex:none;display:-webkit-box;-webkit-line-clamp:2/,'a clamped title keeps its two lines; it is never the flex item that pays for an overflowing card');
assert.doesNotMatch(css,/\.graph-canvas \.plan-node \.step-output\{/,'the old card-body output rule no longer reaches into the facts grid and inflates a row');
assert.match(css,/\.graph-canvas \.step-summary>\.step-output\{[^}]*margin:0\}/,'the run card spaces its output with the column gap, not with margins');
assert.doesNotMatch(css,/\.canvas-first-plan \.plan-node \.step-member\{order:1\}/,'the card no longer shuffles its rows');
assert.doesNotMatch(css,/\.canvas-first-plan \.plan-node \.step-output\{order:2\}/);
// The card has to be tall enough to hold what the card is made of; a card that
// is short by a few pixels takes them out of the title's second line.
const cardHeight=Number(/canvasNodeHeight=(\d+)/.exec(await read('../shared/definition.js'))[1]);
assert.ok(cardHeight>=240,`the step card has room for a two-line title and three facts (currently ${cardHeight}px)`);
assert.match(inspector,/<dl class="step-facts">/);
const cardFacts=inspector.slice(inspector.indexOf('<dl class="step-facts">'),inspector.indexOf('</dl></button>'));
for(const label of ['Produces','Member','Runs on'])assert.equal((cardFacts.match(new RegExp(`<dt>${label}</dt>`,'g'))||[]).length,1,`the card labels ${label} once`);
assert.equal((cardFacts.match(/<dt>/g)||[]).length,3,'and shows exactly three facts');
assert.match(inspector,/aria-describedby="plan-keyboard-help"><strong title=/);
assert.doesNotMatch(inspector,/>Connect here</,'the card carries no Connect here button');
assert.match(inspector,/\$\{connectTo\?`data-connect-target="\$\{e\(step\.id\)\}"`:""\}/,'the card itself is the connection target');
assert.match(canvasModule,/connectTarget=false/);
assert.match(canvasModule,/\$\{connectTarget\?'connect-target':''\}/);
assert.match(css,/\.graph-canvas \.plan-node\.connect-target\{cursor:crosshair\}/);
console.log('PASS: workspace modes retain drafts/history, the dock is fixed, primary actions are labelled, a step shows readable inputs and output, a connection explains the order it enforces and the collection it shares, saved test data is visible on the card, a large workflow can be searched, groups arrange the canvas without changing what runs, and the step card is readable rather than scrollable, with a title that keeps both of its lines.');
