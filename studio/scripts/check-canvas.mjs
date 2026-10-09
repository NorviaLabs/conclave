// Offline checks for the two canvases: the plan canvas markup (ports, selectable
// edges, the drop target) and the run canvas (statuses, tool calls, delegations,
// labelled handoffs), driven by a projected run rather than a hand-written one.
// No browser and no provider calls.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {canvasFitZoom, canvasDockMarkup} from '../web/canvas-ui.js';
// A web module loads in node once its absolute browser specifiers point at the
// file they are served from. Modules that import each other become data: URLs,
// so each one carries its own rewritten specifiers.
const url = path => new URL(path, import.meta.url).href;
const load = async (path, replacements = []) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, target] of replacements) source = source.replaceAll(`'${specifier}'`, JSON.stringify(target));
  return 'data:text/javascript,' + encodeURIComponent(source);
};
const shared = url('../shared/definition.js'), catalogue = url('../shared/catalogue.js');
const activityUrl = await load('../web/run-activity.js', [['/canvas-ui.js', url('../web/canvas-ui.js')]]);
const runCanvasUrl = await load('../web/run-canvas.js', [['/definition.js', shared], ['/canvas-ui.js', url('../web/canvas-ui.js')], ['/run-activity.js', activityUrl]]);
const planUrl=await load('../web/plan.js', [['/definition.js', shared]]);
const workbenchUrl = await load('../web/workbench-ui.js', [['/plan.js',planUrl], ['/catalogue.js', catalogue], ['/definition.js', shared], ['/run-canvas.js', runCanvasUrl], ['/canvas-ui.js', url('../web/canvas-ui.js')], ['/run-activity.js', activityUrl]]);

const {emptyDefinition, initialState, validateDefinition, canvasPositions, planShape, setCanvasMetrics, staleRunSteps, stageBackend, backendOptions, backendModels, backendIssue, backendLabel, defaultBackend} = await import(shared);
const {projectLiveRun} = await import(await load('../web/live.js', [['/definition.js', shared], ['/canvas-ui.js', url('../web/canvas-ui.js')]]));
const {runCanvasMarkup, runProgressMarkup, runStepStatus, runToolCalls, runDelegations, runHandoffs, payloadLabel, runBackends} = await import(runCanvasUrl);
const {workbenchMarkup, executionPlanMarkup, executionDetailMarkup, canvasEdgePaths, canvasEdgeHandle} = await import(workbenchUrl);
const {connectPlanSteps, disconnectPlanSteps, insertPlanStep, moveCanvasStep, layoutSaveDefinition} = await import(await load('../web/plan.js', [['/definition.js', shared]]));
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const agentName = (d, id) => d.agents.find(agent => agent.id === id)?.name || id;
const outputName = (d, key) => d.state.find(item => item.key === key)?.label || key;
const capabilityName = id => id.replaceAll('_', ' ');

// Exercise the browser sizing functions without requiring a browser or API.
const studioSource = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const runSizing = studioSource.slice(studioSource.indexOf('function applyRunZoom(){'), studioSource.indexOf("window.addEventListener('resize',sizeRunCanvas);"));
// The canvas moves rather than scrolls, so sizing the run canvas needs the pan too.
const runPanning = studioSource.slice(studioSource.indexOf('const canvasOffsets='), studioSource.indexOf('function canvasDropPoint('));
const area = {closest:()=>({querySelector:()=>null,style:{setProperty(){}}}),clientWidth: 1800, clientHeight: 0, style: {},
  // The canvas is moved, not scrolled, so it is asked for its board and its box.
  querySelector: selector => selector.includes('canvas-flow') ? flow : null, dataset: {canvasMode: 'run'},
  getBoundingClientRect: () => ({top: 240, left: 0})};
Object.defineProperty(area.style, 'height', {set(value){area.clientHeight = parseFloat(value);}});
const flow = {style: {width: '2800px', height: '1200px'}, closest: () => area};
// The dock has a fixed CSS anchor; there is no pointer-driven position state.
assert.doesNotMatch(studioSource, /dockOffsets|dockDrag|applyDockOffset/);
const level = {};
const app = {querySelector: selector => selector.includes('canvas-flow') ? flow : selector.includes('canvas-view-level') ? level : area};
const sizing = {$: () => app, window: {innerHeight: 1000, scrollY: 0,addEventListener(){}}, canvasFitZoom, runCanvasZoom: null};
runInNewContext(`${runPanning}\n${runSizing}\nsizeRunCanvas();`, sizing);
assert.equal(area.clientHeight, 694, 'the run canvas fills the remaining browser height');
assert.equal(flow.style.zoom, canvasFitZoom(1800, 694, 2800, 1200), 'the whole run board fits on first render');
assert.equal(level.textContent, `${Math.round(flow.style.zoom * 100)}%`, 'the level reports the fitted scale');
sizing.window.innerHeight = 800;
runInNewContext('sizeRunCanvas();', sizing);
assert.equal(area.clientHeight, 494, 'resize uses the new available height');
assert.equal(flow.style.zoom, canvasFitZoom(1800, 694, 2800, 1200), 'resize preserves the viewport zoom');
sizing.runCanvasZoom = 1.2;
runInNewContext('sizeRunCanvas();', sizing);
assert.equal(flow.style.zoom, 1.2, 'manual zoom survives sizing and rerenders');

// =========================================================================
// The plan canvas
// =========================================================================

const plan = emptyDefinition();
plan.name = 'Plan canvas check';
plan.state = [{key: 'findings', label: 'Findings', kind: 'findings', type: 'records', merge: 'append', statuses: ['proposed', 'supported']}, {key: 'brief', label: 'Brief', kind: 'document', type: 'text', merge: 'replace', statuses: []}];
plan.agents = [{id: 'researcher', name: 'Researcher', kind: 'ai', instructions: 'Read', tools: ['read_file']}, {id: 'writer', name: 'Writer', kind: 'ai', instructions: 'Write', tools: []}, {id: 'reviewer', name: 'Reviewer', kind: 'human', instructions: 'Review', tools: []}];
plan.workflow = [{id: 'step_1', name: 'Read the sources', agent: 'researcher', output: 'findings', task: 'Read'}, {id: 'step_2', name: 'Draft the brief', agent: 'writer', output: 'brief', task: 'Draft'}, {id: 'step_3', name: 'Review the brief', agent: 'reviewer', output: 'brief', task: 'Review'}];
plan.views = [{id: 'findings_view', title: 'Findings', collection: 'findings', layout: 'cards'}, {id: 'brief_view', title: 'Brief', collection: 'brief', layout: 'prose'}];
plan.connections = [{from: 'step_1', to: 'step_2'}, {from: 'step_2', to: 'step_3'}];
validateDefinition(plan);

const context = {escape, field: () => '', agentName, outputName, capabilityName, selectedId: 'step_2', pane: 'steps', members: [], showResources: false, showInspector: false};
const planMarkup = workbenchMarkup(plan, {...context, connectSource: 'step_1', selectedEdge: 'step_1|step_2'});
assert.match(planMarkup, /class="canvas-view-level" data-canvas-zoom="reset" aria-label="Reset to 100%" title="Reset to 100%">100%/, 'the level starts at 100%');
assert.match(workbenchMarkup(plan, {...context, canvasZoom: 1.2}), />120%/, 'and reports the zoom you set');

// A step can start a connection from the canvas, and the port is a real button.
assert.equal((planMarkup.match(/data-canvas-port="/g) || []).length, 3, 'every step offers a port to connect from');
assert.equal((planMarkup.match(/data-plan-move="/g) || []).length, 2, 'reordering appears once, in the step settings');
assert.match(planMarkup, /<div class="inspector-move"><button class="text-button" data-plan-move="step_2" data-direction="earlier"/);
const nodeBlocks = planMarkup.split('<section class="plan-node').slice(1).map(block => block.slice(0, block.indexOf('</section>')));
assert.ok(nodeBlocks.length === 3 && nodeBlocks.every(block => !block.includes('data-plan-move')), 'a node carries no reordering controls');
assert.equal((planMarkup.match(/data-connect-target="/g) || []).length, 2, 'while connecting, the other steps are the target');
assert.ok(!/Connect here/.test(planMarkup), 'the card carries no Connect here button');
const quiet = workbenchMarkup(plan, {...context, connectSource: null, selectedEdge: null});
assert.equal((quiet.match(/data-connect-target="/g) || []).length, 0);
// A step is moved and connected on the canvas itself, so it offers neither a
// drag handle nor a Connect button.
assert.ok(!/data-connect-source|plan-handle|data-canvas-move/.test(planMarkup), 'a step carries no Connect button and no drag handle');

assert.match(planMarkup, /<button type="button" class="plan-port out" data-canvas-port="step_1"/);
assert.match(planMarkup, /class="plan-port in"/, 'the incoming edge has somewhere to land');
assert.match(planMarkup, /data-canvas-drop="plan"/, 'the canvas is a drop target');
// No instructions: the canvas is meant to be obvious, and what is left states
// what a thing is or what the host enforces.
assert.ok(!/canvas-tip/.test(planMarkup), 'the canvas carries no instruction line');
assert.ok(!/Click or drag into the plan|Drop a tool on an AI step|Add a step for this member|Add a separate member and step/.test(planMarkup), 'the palette lists what things are, not what to do with them');
assert.ok(!/title="Drag onto another step/.test(planMarkup), 'the port is an affordance, not a tooltip lesson');
assert.match(planMarkup, /An AI member completes it/);
assert.match(planMarkup, /A person answers it/);
assert.match(planMarkup, /Enter on a connect port starts a connection/);
assert.match(planMarkup, /Connecting from/, 'the click flow is still offered');
assert.ok(!/Choose “Connect here”/.test(planMarkup), 'the prompt says what is happening, not what to click');

// Edges are selectable, and only the selected one carries the focusable handle
// that opens the connection's actions.
assert.equal((planMarkup.match(/data-canvas-edge="/g) || []).length, 2, 'every connection is drawn as a selectable edge');
assert.equal((planMarkup.match(/class="edge-hit"/g) || []).length, 2, 'each edge has a wide hit area');
assert.equal((planMarkup.match(/class="canvas-edge[^"]*\bselected\b[^"]*"/g) || []).length, 1, 'the selected edge is marked');
// A connection takes focus, so its actions are reachable without a mouse.
assert.equal((planMarkup.match(/<g class="canvas-edge[^"]*" data-canvas-edge="[^"]+" tabindex="0" role="button" aria-pressed="(true|false)" aria-label="Connection from /g) || []).length, 2, 'each connection is a focusable, named button');
assert.equal((planMarkup.match(/data-canvas-edge-menu="/g) || []).length, 1, 'the selected edge offers one handle');
assert.match(planMarkup, /<button type="button" class="canvas-edge-handle" data-canvas-edge-menu="step_1|step_2"[^>]*title="Connection actions" aria-label="Connection actions from Read the sources to Draft the brief"/, 'and the handle is a real, named button');
assert.ok(!/canvas-edge-remove|canvas-edge-insert|data-canvas-disconnect=/.test(planMarkup), 'the old floating remove and insert buttons are gone');
assert.equal(canvasEdgeHandle(plan, {}, escape, null), '', 'no handle without a selection');
const unselected = workbenchMarkup(plan, {...context, connectSource: null, selectedEdge: null});
assert.ok(!unselected.includes('canvas-edge-handle'), 'an unselected canvas shows no handle');
assert.equal(canvasEdgeHandle(plan, canvasPositions(plan), escape, 'step_1|step_2'), canvasEdgeHandle(plan, canvasPositions(plan), escape, 'step_1|step_2'), 'the handle is placed at the midpoint');
assert.equal((unselected.match(/data-canvas-port="/g) || []).length, 3, 'the ports stay available while nothing is selected');
assert.match(planMarkup, /data-canvas-mode="plan"/, 'the shared canvas renders planning mode');
assert.ok(!/data-plan-slot|plan-drop-slot/.test(planMarkup), 'the plan is one canvas with no drop slots');
// The header is a caption; the view controls live inside the canvas.
const headerStart = planMarkup.indexOf('canvas-heading');
const header = planMarkup.slice(headerStart, planMarkup.indexOf('</div>', headerStart));
assert.match(header,/toggle-readiness/, 'readiness is actionable');
assert.match(header, /3 members · 3 steps/);
assert.match(planMarkup, /id="plan-keyboard-help"[^>]*>Arrow keys move a focused step\. Enter on a step opens its settings;/, 'the step help names the way in for a keyboard or a screen reader');
const viewStart = planMarkup.indexOf('class="canvas-view"');
const view = planMarkup.slice(viewStart, planMarkup.indexOf('</div>', viewStart));
assert.match(view, /data-canvas-zoom="out"/);
assert.match(view, /data-canvas-zoom="reset"/);
assert.match(view, /data-canvas-zoom="in"/);
assert.match(view, /class="canvas-view-level" data-canvas-zoom="reset"/, 'the level doubles as reset');
assert.match(view, /100%/, 'the level starts at 100%');
assert.ok(!/fit|fullscreen/.test(view), 'and the stepper is only a stepper: fit and full screen moved to the dock');

// The dock: the actions a person reaches for while looking at the canvas, in one
// bar. Fit is there because the canvas has no edge; full screen stays on the plan.
const planDock = planMarkup.slice(planMarkup.indexOf('data-canvas-dock'), planMarkup.indexOf('class="canvas-view"'));
assert.match(planDock, /data-canvas-zoom="fit"[^>]*title="Fit entire graph"/, 'fit is on the dock');
assert.equal((planDock.match(/data-action="canvas-fullscreen"/g) || []).length, 1, 'so is full screen');
assert.equal((planMarkup.match(/data-canvas-zoom="fit"/g) || []).length, 1, 'and neither is offered twice');
assert.equal((planMarkup.match(/data-action="canvas-fullscreen"/g) || []).length, 1, 'in either direction');

// A selection is what the canvas is about at that moment, so the dock says so.
const dockFor = selection => canvasDockMarkup({selection, view: 'plan', escape});
const emptyDock = dockFor('');
assert.ok(!/data-plan-select/.test(emptyDock), 'nothing selected, nothing about a step');
const selectedDock = dockFor('step_2');
for (const [attribute, label] of [['data-plan-select="step_2"', 'Edit step'], ['data-plan-duplicate="step_2"', 'Duplicate step'],
  ['data-canvas-port="step_2"', 'Connect to another step'], ['data-menu-centre="step_2"', 'Centre in view']]) {
  assert.ok(selectedDock.includes(attribute) && selectedDock.includes(`title="${label}"`), `${label} is offered for the selection`);
}
assert.ok(!/data-plan-remove/.test(selectedDock), 'removing a step stays behind the right-click, where a stray click cannot delete it');
assert.match(selectedDock, /aria-label="Selected step"/, 'and the group names itself for a screen reader');

// A run's steps are not the plan's: what you can do to one you are watching is
// read it, find it in the activity, and take its output. Plan edits wait.
const runDock = canvasDockMarkup({selection: 'step_2', view: 'run', escape});
assert.match(runDock, /data-run-step="step_2"[^>]*title="Step details"/, 'a run step offers its details');
assert.match(runDock, /data-menu-step-activity="step_2"/, 'and its place in the activity');
assert.match(runDock, /data-menu-copy-output="step_2"/, 'and its output');
assert.ok(!/data-plan-select|data-plan-duplicate|data-canvas-port|data-menu-centre/.test(runDock),
          'but not the plan edits, which wait until the run is over');
assert.ok(!/canvas-fullscreen/.test(runDock), 'and full screen belongs to the plan canvas');
// The plan's own actions live on the canvas the person is working on, so the
// page header above it stays a caption and a menu of application actions.
// Both canvas actions are icon-only, so their hovertext is the label.
assert.match(planMarkup, /data-action="tidy-canvas"[^>]*title="Arrange positions only; keep connections"[^>]*aria-label="Arrange positions only; keep connections"/, 'Tidy layout is on the canvas, named on hover and to a screen reader');
assert.match(planMarkup, /data-action="toggle-plan-resources" aria-expanded="false"[^>]*title="Add step"[^>]*aria-label="Add step"/, 'so is adding a step');
const heading = planMarkup.slice(0, planMarkup.indexOf('class="plan-workbench'));
assert.ok(!/tidy-canvas|toggle-plan-resources/.test(heading), 'the plan heading carries no canvas controls');
assert.match(heading, /data-action="import"/, 'the application menu still holds the file actions');
// Set up is an application-level action too, so it is in that menu, not the header.
assert.match(heading, /<details class="advanced-file-actions"><summary>Application<\/summary><button class="button" data-action="set-up">Set up<\/button>/, 'and the way into the setup questions');
assert.match(planMarkup, /<summary>Application<\/summary>/, 'the application menu is named for what it holds');
assert.match(planMarkup, /data-action="delete-application"/, 'deleting an application sits with the other application actions');

// A result recorded against a plan that has since changed is marked, not hidden.
const edited = structuredClone(plan);
edited.workflow[0].task = 'Read the sources again';
assert.deepEqual([...staleRunSteps(plan, edited)].sort(), ['step_1', 'step_2', 'step_3'], 'an edit marks the step and everything downstream of it');
assert.equal(staleRunSteps(plan, structuredClone(plan)).size, 0, 'an unchanged plan marks nothing');
const grown = structuredClone(plan);
grown.workflow.push({id: 'step_4', name: 'Publish', agent: 'writer', output: 'brief', task: 'Publish'});
grown.connections.push({from: 'step_3', to: 'step_4'});
assert.deepEqual([...staleRunSteps(plan, grown)], ['step_4'], 'a step added since the run has no result yet');
const rewired = structuredClone(plan);
rewired.connections = [{from: 'step_1', to: 'step_3'}];
// Rewiring step_3 to run after step_1 also takes step_1 away from step_2, so
// both steps now describe a plan that no longer exists.
assert.deepEqual([...staleRunSteps(plan, rewired)].sort(), ['step_2', 'step_3'], 'rewiring marks both ends whose input changed');
// The curve and the handle agree on where the middle of an edge is.
assert.match(canvasEdgePaths(plan, {step_1: {x: 60, y: 50}, step_2: {x: 60, y: 450}, step_3: {x: 60, y: 850}}, escape, 'step_1|step_2'), /M 410 170 C 585 170, -115 570, 60 570/);
// A connection the canvas cannot draw must be refused before it is saved: the
// plan is drawn from its own edges, so a loop would leave nothing to render.
assert.throws(() => connectPlanSteps(plan, 'step_3', 'step_1'), /That connection creates a loop. Remove it so work can finish./);
assert.throws(() => connectPlanSteps(plan, 'step_2', 'step_1'), /creates a loop/);
assert.deepEqual(connectPlanSteps(plan, 'step_1', 'step_2').connections, plan.connections, 'a duplicate connection changes nothing');
assert.deepEqual(connectPlanSteps(plan, 'step_1', 'step_3').connections, [...plan.connections, {from: 'step_1', to: 'step_3'}], 'a branching connection is kept');
assert.deepEqual(disconnectPlanSteps(plan, 'step_1', 'step_2').connections, [{from: 'step_2', to: 'step_3'}]);
const dropped = insertPlanStep(plan, 'ai', plan.workflow.length);
const placed = moveCanvasStep(dropped, dropped.workflow.at(-1).id, 900, 1200);
assert.deepEqual(canvasPositions(placed)[dropped.workflow.at(-1).id], {x: 900, y: 1200}, 'a step dropped on the canvas keeps the position it was dropped at');

// The arrangement is saved by itself, and it is only the arrangement that
// travels: a step the application does not hold yet has no saved place to move
// to, and an edit still being made waits for the person to save it.
const savedPlan = {...structuredClone(plan), canvas: {positions: canvasPositions(plan)}};
const rearranged = moveCanvasStep(savedPlan, 'step_2', 900, 1200);
const editedAndRearranged = {...rearranged, workflow: rearranged.workflow.map(step => step.id === 'step_2' ? {...step, name: 'Draft the brief carefully'} : step)};
const untouched = structuredClone(savedPlan);
const layoutSave = layoutSaveDefinition(savedPlan, editedAndRearranged);
assert.deepEqual(layoutSave.canvas.positions.step_2, {x: 900, y: 1200}, 'the arrangement is what is written');
assert.equal(layoutSave.workflow[1].name, 'Draft the brief', 'an unfinished edit stays in the draft');
assert.deepEqual(savedPlan, untouched, 'and the saved definition is not changed in place');
assert.ok(layoutSave.workflow.every((step, index) => step.output === savedPlan.workflow[index].output), 'nothing else travels with the arrangement');
assert.throws(() => layoutSaveDefinition(savedPlan, moveCanvasStep(insertPlanStep(plan, 'ai', plan.workflow.length), 'step_4', 40, 40)), /Place steps within the canvas\./, 'a step that is not saved yet waits for the next save');
assert.throws(() => layoutSaveDefinition(savedPlan, structuredClone(plan)), /no arrangement to save/, 'a draft that has never been arranged has nothing to write');
console.log('PASS: a connection that would make a loop is refused before it is saved, a dropped step keeps its position, and the arrangement saves on its own.');

// One pane per kind of thing: saved members are members, not a fourth tab.
const panes = workbenchMarkup(plan, {escape, field: () => '', agentName, outputName, capabilityName, selectedId: 'step_2', pane: 'members', canvasMode: 'canvas', canvasZoom: 1, connectSource: null, selectedEdge: null, members: [{id: 'saved_1', member: {name: 'Saved analyst', kind: 'ai', tools: ['read_file']}}], showResources: true, showInspector: false});
assert.deepEqual([...panes.matchAll(/data-resource-pane="([a-z]+)"/g)].map(match => match[1]), ['steps', 'members', 'tools'], 'three panes, not four');
assert.match(panes, /Saved members/, 'saved members sit with the other members');
assert.match(panes, /data-plan-saved="saved_1"/, 'a saved member is still draggable into the plan');
assert.ok(!/data-resource-pane="saved"/.test(panes));
console.log('PASS: the plan palette offers three panes, with saved members beside the rest.');

console.log('PASS: the plan is one fitted canvas with its own zoom and full screen inside it: a port per step, selectable edges with a keyboard-reachable remove handle, a drop target, and reordering in the step settings.');

// =========================================================================
// The run canvas
// =========================================================================

const record = (id, title, status) => ({id, title, body: title, status, source_refs: ['source.md:1'], contributions: []});
const initial = initialState(plan), one = {...initial, findings: [record('f1', 'Margins fell', 'supported')]};
const two = {...one, findings: [...one.findings, record('f2', 'Churn rose', 'supported')]};
const event = (sequence, body) => ({sequence, event: body});
const run = {
  id: 'canvas-run', status: 'completed',
  request: {definition: plan, revision: 2, task: 'Read the sources and draft the brief.', inputs: {sources: 'Notes'}, resources: {}},
  metrics: {tokens: 4820},
  reviews: [],
  events: [
    event(1, {type: 'ApplicationStarted', state: initial}),
    event(2, {type: 'StageStarted', stage: 'step_1', agent: 'researcher'}),
    event(3, {type: 'ToolCalled', stage: 'step_1', agent: 'researcher', tool: 'read_file', ok: true, output_chars: 1840}),
    event(4, {type: 'ToolCalled', stage: 'step_1', agent: 'researcher', tool: 'read_file', ok: true, output_chars: 980}),
    event(5, {type: 'ToolDenied', stage: 'step_1', agent: 'researcher', tool: 'web_search', ok: false}),
    // A delegation: the analyst hands the numbers to the fact checker.
    event(6, {type: 'Delegation', stage: 'step_1', agent: 'reviewer', delegated_by: 'researcher', depth: 2, task: 'Check the quoted figures <script>alert(1)</script>', status: 'completed'}),
    event(6.5, {type: 'Delegation', stage: 'step_1', agent: 'writer', delegated_by: 'researcher', depth: 2, task: 'Draft the section on margins.', status: 'running'}),
    event(7, {type: 'ApplicationStateChanged', stage: 'step_1', patch: one.findings, state: one, version: 1}),
    event(8, {type: 'StageStarted', stage: 'step_2', agent: 'writer'}),
    event(9, {type: 'ApplicationStateChanged', stage: 'step_2', patch: 'A drafted brief.', state: {...two, brief: 'A drafted brief.'}, version: 2}),
    event(10, {type: 'StageStarted', stage: 'step_3', agent: 'reviewer'}),
    event(11, {type: 'HumanInputRequested', stage: 'step_3', agent: 'reviewer', review_id: 'review_1'}),
  ],
};

// The status vocabulary, including the precedence the work plan uses.
assert.equal(runStepStatus(plan, null, 0), 'Waiting');
assert.equal(runStepStatus(plan, projectLiveRun({...run, status: 'running', events: run.events.slice(0, 2)}), 0), 'Working');
assert.equal(runStepStatus(plan, projectLiveRun({...run, status: 'running', events: run.events.slice(0, 1)}), 0), 'Ready', 'a step with no prerequisites is ready');
assert.equal(runStepStatus(plan, projectLiveRun({...run, status: 'running', events: run.events.slice(0, 1)}), 1), 'Waiting');
assert.equal(runStepStatus(plan, projectLiveRun(run), 0), 'Complete');
assert.equal(runStepStatus(plan, projectLiveRun({...run, status: 'stopped'}), 2), 'Incomplete', 'a run that stopped leaves the rest unfinished');
assert.equal(runStepStatus(plan, projectLiveRun({...run, status: 'waiting_for_human', reviews: [{id: 'review_1', status: 'pending', stage: 'step_3', request: {state: two}}]}), 2), 'Needs input');

// Tool calls, delegations and handoffs come from the record, not from markup.
const projected = projectLiveRun({...run, status: 'waiting_for_human', reviews: [{id: 'review_1', status: 'pending', stage: 'step_3', request: {state: two}}]});
assert.deepEqual(runToolCalls(projected).get('step_1'), [{name: 'read_file', count: 2, denied: false}, {name: 'web_search', count: 1, denied: true}]);
assert.deepEqual(runDelegations(projected).get('step_1').map(item => [item.agent, item.by, item.status]), [['reviewer', 'researcher', 'completed'], ['writer', 'researcher', 'running']]);
assert.deepEqual(runHandoffs(plan, projected).map(item => [item.from, item.to, item.label]), [['step_1', 'step_2', 'Findings · 1 record'], ['step_2', 'step_3', 'Brief · 16 characters']]);
assert.equal(payloadLabel('A drafted brief.', 'text'), '16 characters');
assert.equal(payloadLabel([record('a', 'A', 'open')], 'records'), '1 record');
assert.equal(payloadLabel({}, 'text'), 'written');

const runContext = {escape, agentName, outputName, capabilityName, zoom: 1};
const canvas = runCanvasMarkup(plan, projected, 'step_2', runContext);
assert.equal((canvas.match(/data-run-node="/g) || []).length, 3, 'a node per step');
const canvasBody = canvas.slice(0, canvas.indexOf('data-canvas-dock'));
assert.equal((canvasBody.match(/data-run-step="/g) || []).length, 5, 'steps and delegations are inspectable');
assert.match(canvas.slice(canvas.indexOf('data-canvas-dock')), /data-run-step="step_2"[^>]*title="Step details"/,
             'and the dock offers the selected step as well');
assert.equal((canvas.match(/class="run-node-status" data-status="complete"/g) || []).length, 2, 'two steps finished before the review');
assert.equal((canvas.match(/class="run-node-status" data-status="needs-input"/g) || []).length, 1, 'the human gate is marked');
assert.equal((canvas.match(/class="run-node-status"/g) || []).length, 3, 'every step carries its status');
// The run overview above the canvas already counts the steps and the version.
// The caption counts the collaboration rather than describing it.
assert.match(canvas, /3 agents/);
assert.match(canvas, /handed/);
assert.ok(!/steps complete/.test(canvas), 'the canvas does not repeat the run overview');
assert.ok(!/saved version/.test(canvas), 'the canvas does not repeat the saved version');
// What runs a step is on the card: the person choosing a plan needs to see it,
// and the run needs to show which backend it actually used.
assert.equal((canvas.match(/class="step-backend"/g) || []).length, 3, 'every step names what runs it');
assert.match(canvas, /class="step-backend" title="Runs on">[^<]+<\/span>/, 'with the host\'s own name for that backend');
assert.match(executionDetailMarkup(plan, projected, 'step_1', runContext), /Not approved/, 'tool denials remain inspectable');
assert.match(canvas, /class="run-subagent" data-status="complete"/, 'a delegation becomes a sub-agent node');
assert.match(canvas, /delegated by Researcher · depth 2 · completed/);
assert.ok(!canvas.includes('<script>alert(1)</script>'), 'a delegated task is escaped');
assert.match(canvas, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
assert.equal((canvas.match(/class="canvas-edge delegation-edge"/g) || []).length, 2, 'each delegation has its own edge');
// Two delegations from one step stack below each other, each edge ending at its node.
const delegated = [...canvas.matchAll(/class="run-subagent" data-status="[a-z]+" style="left:(\d+)px;top:(\d+)px"/g)].map(match => ({x: Number(match[1]), y: Number(match[2])}));
assert.equal(delegated.length, 2);
assert.equal(delegated[0].x, delegated[1].x, 'stacked delegations share a column');
assert.equal(delegated[1].y - delegated[0].y, 110, 'the second delegation sits below the first');
for (const [index, point] of delegated.entries()) {
  const curve = [...canvas.matchAll(/delegation-edge"><path class="edge-line" d="([^"]+)"/g)][index][1];
  assert.match(curve, new RegExp(` ${point.x} ${point.y + 30}$`), `the edge for delegation ${index + 1} ends at its node`);
}
assert.match(canvas, /class="canvas-edge run-edge handed"[\s\S]*Findings · 1 record/, 'a handoff edge is labelled with what crossed');
assert.match(canvas, /produced/, 'completed steps summarize output');
// A step that has not started says what it waits for, from the connections.
const early = runCanvasMarkup(plan, projectLiveRun({...run, status: 'running', events: run.events.slice(0, 1)}), 'step_1', runContext);
assert.match(early, /Waiting/, 'waiting remains visible');
assert.match(early, /class="run-node-status" data-status="ready"/, 'a step with no prerequisites is ready');
assert.match(early, /data-run-node="step_1" data-status="ready"/, 'ready styling is attached to the card');
assert.match(early, /data-run-node="step_2" data-status="waiting"/, 'unstarted cards have waiting styling');
assert.match(canvas, /data-run-node="step_1" data-status="complete"/, 'completed styling is attached to the card');
assert.match(canvas, /data-run-node="step_3" data-status="needs-input"/, 'review styling is attached to the card');
const workingRecord = {...run, status: 'running', events: run.events.slice(0, 2)};
assert.match(runCanvasMarkup(plan, projectLiveRun(workingRecord), 'step_1', runContext), /data-run-node="step_1" data-status="working"/, 'active cards trigger the working animation');
const parallel=projectLiveRun({...run,status:'running',events:[run.events[0],run.events[1],event(3,{type:'StageStarted',stage:'step_2',agent:'writer'})]});
assert.deepEqual(parallel.activeSteps,[0,1]);
assert.equal(runStepStatus(plan,parallel,0),'Working');assert.equal(runStepStatus(plan,parallel,1),'Working');
const sibling=projectLiveRun({...run,status:'running',events:[...run.events.slice(0,2),event(3,{type:'StageStarted',stage:'step_2',agent:'writer'}),run.events[9]]});
assert.equal(sibling.activeStep,0,'a completed parallel sibling does not clear the other active step');
const followCode=studioSource.slice(studioSource.indexOf('function followActiveNode(){'),studioSource.indexOf('function closeRunInspector(){'));
let focused=[];const follow={preview:{definition:plan,activeStep:0},followActive:true,lastFollowedStep:null,canvasPan:null,focusRunNode:id=>focused.push(id)};
runInNewContext(followCode+'followActiveNode();followActiveNode();',follow);assert.deepEqual(focused,['step_1'],'follow centers only once per active step');
follow.followActive=false;follow.preview.activeStep=1;runInNewContext('followActiveNode();',follow);assert.deepEqual(focused,['step_1'],'manual exploration suspends following');
follow.followActive=true;runInNewContext('followActiveNode();',follow);assert.deepEqual(focused,['step_1','step_2'],'re-enabling follows the current step');
const failedRun = projectLiveRun({...workingRecord, status: 'failed'});
assert.equal(runStepStatus(plan, failedRun, 0), 'Failed', 'the unfinished step that was running when the run failed is marked failed');
assert.equal(runStepStatus(plan, failedRun, 1), 'Incomplete', 'downstream steps are not falsely marked failed');
assert.match(runCanvasMarkup(plan, failedRun, 'step_1', runContext), /data-run-node="step_1" data-status="failed"/, 'failed styling is attached to the card');
assert.equal(runStepStatus(plan, projectLiveRun({...run, status: 'failed'}), 0), 'Complete', 'failure does not change completed steps');
const diagnosticRun={...failedRun,events:[
  {type:'StageStarted',stage:'step_1',sequence:1},
  {type:'StageStarted',stage:'step_2',sequence:2},
  {type:'ToolDenied',stage:'step_1',tool:'write_file',reason:'Approval <refused>',sequence:3,arguments:{secret:'hidden'}},
  {type:'ToolCalled',stage:'step_1',tool:'read_file',ok:false,output_chars:42,truncated:true,sequence:4,observation:'private output'},
  {type:'BudgetExceeded',stage:'step_1',dimension:'tokens',used:101,limit:100,sequence:5},
  {type:'ApplicationFinished',stage:'step_1',status:'failed',reason:'Invalid <output>',sequence:6}
]};
assert.equal(runStepStatus(plan,diagnosticRun,0),'Failed','terminal stage attribution wins over the last parallel start');
assert.equal(runStepStatus(plan,diagnosticRun,1),'Incomplete','a parallel sibling is not blamed');
const diagnostics=executionDetailMarkup(plan,diagnosticRun,null,{escape,agentName,outputName,capabilityName});
assert.match(diagnostics,/<h3>Read the sources<\/h3>/,'the failed step is the default selection');
assert.match(diagnostics,/Step failed/);
assert.match(diagnostics,/Invalid &lt;output&gt;/);
assert.match(diagnostics,/activity" open/,'diagnostics expand on failure');
assert.match(diagnostics,/#3 · ToolDenied/);
assert.match(diagnostics,/Approval &lt;refused&gt;/);
assert.match(diagnostics,/Tool: read file/);
assert.match(diagnostics,/Output: 42 characters \(truncated\)/);
assert.match(diagnostics,/Budget: tokens/);
assert.match(diagnostics,/Used: 101/);
assert.match(diagnostics,/Limit: 100/);
assert.match(diagnostics,/Inputs available when this step started/,'failed steps retain their dependency inputs');
assert.match(diagnostics,/Arguments and execution details/,'recorded arguments remain behind disclosure');
assert.doesNotMatch(executionDetailMarkup(plan,diagnosticRun,'step_2',{escape,agentName,outputName,capabilityName}),/Invalid &lt;output&gt;/,'step failures stay scoped');
const unattributed={...diagnosticRun,events:diagnosticRun.events.map(event=>event.type==='ApplicationFinished'?{...event,stage:undefined}:event)};
assert.equal(runStepStatus(plan,unattributed,1),'Incomplete','run-level failures do not guess which parallel step failed');
assert.match(executionDetailMarkup(plan,unattributed,'step_2',{escape,agentName,outputName,capabilityName}),/did not identify a responsible step/);
assert.match(executionDetailMarkup(plan,{...diagnosticRun,mode:'stopped',events:diagnosticRun.events.map(event=>event.type==='ApplicationFinished'?{...event,status:'stopped'}:event)},null,{escape,agentName,outputName,capabilityName}),/Step stopped/);
const selectionCode=studioSource.slice(studioSource.indexOf("  if(old?.runId!==run.runId){"),studioSource.indexOf("  preview=run;liveError='';"));
const selection={preview:{runId:'same'},workspaceMode:'run',old:{runId:'same',mode:'running'},run:{runId:'same',mode:'failed',events:diagnosticRun.events},selectedRunStepId:'step_2'};
runInNewContext(selectionCode,selection);
assert.equal(selection.selectedRunStepId,'step_2','failure does not interrupt manual inspection');
selection.old.mode='failed';selection.selectedRunStepId='step_2';
runInNewContext(selectionCode,selection);
assert.equal(selection.selectedRunStepId,'step_2','subsequent refreshes preserve a user-selected sibling');
// The run canvas carries the same view controls, inside itself.
assert.match(canvas, /class="canvas-view"/);
assert.match(canvas, /data-run-zoom="out"/);
assert.match(canvas, /data-run-zoom="reset"/);
assert.match(canvas, /data-run-zoom="in"/);
assert.match(canvas, /class="canvas-view-level" data-run-zoom="reset"/, 'the level doubles as fit');
const staleCanvas = runCanvasMarkup(edited, {...projected, definition: plan}, 'step_1', runContext);
assert.equal((staleCanvas.match(/data-run-stale="true"/g) || []).length, 3, 'every stale step is marked on the run canvas');
assert.match(staleCanvas, /Out of date/);
assert.match(staleCanvas, /3 results out of date/);
assert.ok(!runCanvasMarkup(plan, projected, 'step_1', runContext).includes('data-run-stale'), 'a current run marks nothing');
// A finished step says what it produced, and the caption counts the collaboration.
const producedCanvas = runCanvasMarkup(plan, projected, 'step_2', runContext);
assert.match(producedCanvas, /2 findings/, 'a step that wrote two records says so');
assert.match(producedCanvas, /title="Margins fell · Churn rose"/, 'the titles are one hover away');
assert.match(producedCanvas, /title="A drafted brief.">3 words/, 'a text output reports its size, not its content');
assert.match(producedCanvas, /3 agents/, 'the caption counts steps and agents');
assert.match(producedCanvas, /2 delegations/, 'and the work handed to sub-agents');
assert.match(producedCanvas, /run-edge handed/, 'handoff edges remain visible');
assert.match(runCanvasMarkup(plan, {...projected, state: {...projected.state, findings: []}}, 'step_2', runContext), /0 findings/, 'a completed step that produced nothing says so rather than hiding it');
assert.match(producedCanvas, /Produces Brief/, 'a step that has not run yet still says what it will produce');
const runHeader = canvas.slice(canvas.indexOf('canvas-heading'), canvas.indexOf('</div>', canvas.indexOf('canvas-heading')));
assert.match(runHeader,/follow-active/, 'following is explicitly controllable');

// Run, Pause and Stop are icons on the canvas surface, named on hover.
const controlled = runProgressMarkup(plan, projected, 'step_2', {...runContext, toolsDisabled: true, controls:
  '<button type="button" class="canvas-action" data-action="pause-live" title="Pause after the current step" aria-label="Pause after the current step"></button>'});
assert.match(controlled, /class="canvas-dock" role="group" aria-label="Canvas controls"/, 'the canvas action bar floats in the canvas');
assert.match(controlled, /class="canvas-dock-group" role="group" aria-label="Run controls"|<button type="button" class="canvas-action" data-action="pause-live"/, 'Pause sits on the canvas');
// A run in flight disables the plan tools rather than taking them away, so the
// bar keeps its shape and a person can see why they cannot edit yet.
const tools = ['toggle-plan-resources', 'suggest-plan-change', 'tidy-canvas'];
for (const action of tools) {
  assert.match(controlled, new RegExp(`data-action="${action}"[^>]*disabled`), `${action} is still there, disabled, during a run`);
}
assert.match(controlled, /title="Add step · finish or stop the run to change the plan"/, 'and says why in the hovertext');
assert.match(controlled, /data-action="toggle-plan-resources"[^>]*aria-label="Add step"/, 'while the name still says what it does');
const idle = runProgressMarkup(plan, projected, 'step_2', {...runContext, controls: '<button data-action="edit-plan">Edit plan</button>'});
for (const action of tools) {
  assert.ok(!new RegExp(`data-action="${action}"[^>]*disabled`).test(idle), `${action} is usable once the run is over`);
}
assert.match(idle, /data-action="edit-plan"/, 'a finished run still offers the way back to the plan');

// The canvas and the work plan are two views of one run.
const list = executionPlanMarkup(plan, projected, 'step_2', {escape, agentName, outputName, capabilityName});
const detail = executionDetailMarkup(plan, projected, 'step_2', {escape, agentName, outputName, capabilityName});
const progress = runProgressMarkup(plan, projected, 'step_2', {mode: 'canvas', list, detail, zoom: 1, escape, agentName, outputName, capabilityName});
assert.match(progress, /data-run-node="/);
assert.ok(!/data-run-mode/.test(progress), 'the run screen is one canvas, with no view switch');
assert.match(progress, /run-inspector-drawer/, 'selection opens an overlay inspector');
assert.doesNotMatch(runProgressMarkup(plan,projected,null,{...runContext,detail}),/run-inspector-drawer/, 'no drawer without selection');
assert.match(progress, /Results after this step/, 'the record is the one the run stored');
assert.ok(!progress.includes('execution-plan'), 'canvas mode does not also render the list');
assert.match(list, /Work plan/, 'the work plan is still rendered for the fallback');

// The run canvas tells the same story as the list, step for step.
for (const [index, step] of plan.workflow.entries()) {
  const status = runStepStatus(plan, projected, index);
  assert.match(list, new RegExp(`>${status}<`), `the work plan also reports ${status} for ${step.id}`);
}
const looped = structuredClone(plan);
looped.connections = [...plan.connections, {from: 'step_3', to: 'step_1'}];
const fallback = runProgressMarkup(looped, projected, 'step_1', {list, zoom: 1, escape, agentName, outputName, capabilityName});
assert.match(fallback, /class="execution-plan"/, 'a plan the canvas cannot draw falls back to the work plan');
assert.match(fallback, /The canvas cannot draw this plan: That connection creates a loop/);
console.log('PASS: a saved plan the canvas cannot lay out falls back to the work plan instead of rendering nothing.');

console.log('PASS: the run canvas draws statuses, tool counts, delegations and labelled handoffs from the durable record, marks the results an edit invalidated, and the work plan still lists the same run.');

// =========================================================================
// What runs a member, and what runs one step
// =========================================================================

// The host reports which backends this machine can reach; nothing else may be
// offered, and a choice saved elsewhere is shown as itself rather than as the
// first entry in the list.
const backends = [{id: 'codex', name: 'Codex', available: true, models: [], default_model: null, reason: null, default: true},
  {id: 'ollama', name: 'Ollama', available: true, models: ['local-a:8b', 'local-b:70b'], default_model: 'local-a:8b', reason: null, default: false},
  {id: 'opencode', name: 'OpenCode', available: false, models: [], default_model: null, reason: 'The opencode command is not installed on this host', default: false}];
assert.deepEqual(backendOptions(backends, '').map(option => option[0]), ['', 'codex', 'ollama', 'opencode']);
assert.equal(backendOptions(backends, 'opencode')[3][2], true, 'a backend this host cannot run is offered but not selectable');
assert.deepEqual(backendOptions(backends, 'gemini').at(-1), ['gemini', 'gemini · not offered by this host'], 'a choice from another machine is shown as itself');
assert.deepEqual(backendOptions(backends, '', {inherit: null}).map(option => option[0]), ['codex', 'ollama', 'opencode'], 'a step that inherits offers no empty entry');
assert.deepEqual(backendModels(backends, 'ollama'), ['local-a:8b', 'local-b:70b']);
assert.deepEqual(backendModels(backends, 'codex'), [], 'a backend that reports no models offers none to pick');
assert.equal(defaultBackend(backends).id, 'codex');
assert.equal(backendIssue(backends, 'opencode'), 'The opencode command is not installed on this host');
assert.equal(backendIssue(backends, 'gemini'), 'This host does not offer that backend.');
assert.equal(backendIssue(backends, 'codex'), '', 'a backend this host runs has nothing to report');
assert.equal(backendLabel(backends, 'ollama', 'local-b:70b'), 'Ollama · local-b:70b');
assert.equal(backendLabel(backends, '', ''), 'This host’s default');

// The same precedence the host applies: a step's own choice, else its member's,
// and a model never follows a backend it was not chosen for.
const owner = {id: 'writer', name: 'Writer', kind: 'ai', provider: 'codex', model: 'gpt-5'};
assert.deepEqual(stageBackend(owner, {}), ['codex', 'gpt-5']);
assert.deepEqual(stageBackend(owner, {model: 'gpt-5-mini'}), ['codex', 'gpt-5-mini'], 'a step may change only the model');
assert.deepEqual(stageBackend(owner, {provider: 'ollama'}), ['ollama', ''], 'another backend uses its own default');
assert.deepEqual(stageBackend(owner, {provider: 'ollama', model: 'local-a:8b'}), ['ollama', 'local-a:8b']);
assert.deepEqual(stageBackend(owner, {provider: 'codex'}), ['codex', 'gpt-5'], 'the same backend keeps the member’s model');
assert.deepEqual(stageBackend({id: 'x', name: 'X'}, {}), ['', ''], 'a member that chose nothing runs on this host’s default');

// The plan canvas says what runs each step, and the settings offer both halves.
const owning = structuredClone(plan);
owning.agents[0].provider = 'codex';
owning.agents[0].model = 'gpt-5';
owning.agents[1].provider = 'ollama';
owning.agents[1].model = 'local-a:8b';
validateDefinition(owning);
const fields = (label, path, value, kind = 'input', options = []) => `${label}|${path}|${value}|${kind}|${options.map(option => option[0]).join(',')}`;
const owningCanvas = workbenchMarkup(owning, {...context, backends, field: fields});
// The card says what runs the step, and the settings still offer both halves.
assert.match(owningCanvas, /<dt>Runs on<\/dt><dd class="step-backend" title="Codex · gpt-5">Codex · gpt-5<\/dd>/, 'the step card names the backend its member runs on');
assert.match(owningCanvas, /<dt>Runs on<\/dt><dd class="step-backend" title="Ollama · local-a:8b">Ollama · local-a:8b<\/dd>/, 'including a member on another backend');
assert.match(owningCanvas,/Ollama · local-a:8b/, 'and the settings still name it where it is chosen');
// The settings panel is showing step_2, whose member runs on Ollama.
assert.match(owningCanvas, /This step runs on\|workflow\.1\.provider\|\|select\|,codex,ollama,opencode/, 'the step settings offer this host’s backends, with inheriting first');
assert.match(owningCanvas, /This step’s model\|workflow\.1\.model\|\|select\|,local-a:8b,local-b:70b/, 'the step’s model is picked from what its backend reports');
assert.match(owningCanvas, /Member model\|agents\.1\.model\|local-a:8b\|select\|,local-a:8b,local-b:70b/, 'the member’s model offers the same list');
const overridden = structuredClone(owning);
overridden.workflow[1].provider = 'codex';
validateDefinition(overridden);
const overriddenCanvas = workbenchMarkup(overridden, {...context, backends, field: fields});
assert.match(overriddenCanvas, /<dt>Runs on<\/dt><dd class="step-backend" title="Codex">Codex<\/dd>/, 'a step that names its own backend says so on the card');
assert.match(overriddenCanvas, /This step’s model\|workflow\.1\.model\|\|input\|/, 'a backend that reports no models takes a name instead of a list');
const unofferable = structuredClone(plan);
unofferable.agents[0].provider = 'gemini';
const warned = workbenchMarkup(unofferable, {...context, selectedId: 'step_1', backends, field: () => ''});
assert.match(warned, /This host does not offer that backend\./, 'a choice this host cannot serve is reported, not hidden');
assert.match(warned,/gemini/, 'requested backend remains in settings');

// The run canvas shows what actually ran the step, once the record says so.
const startedRun = projectLiveRun({...run, status: 'running', events: run.events.slice(0, 2).concat([event(2.5, {type: 'StageStarted', stage: 'step_1', agent: 'researcher', provider: 'ollama', model: 'local-b:70b'})])});
assert.deepEqual([...runBackends(startedRun).entries()], [['step_1', ['ollama', 'local-b:70b']]]);
const startedCanvas = runCanvasMarkup(owning, startedRun, 'step_1', {...runContext, backends});
assert.match(startedCanvas, /class="step-backend" title="Runs on Ollama · local-b:70b">Ollama · local-b:70b<\/span>/,
             'the step that started on another backend shows what it actually ran on');
assert.match(executionDetailMarkup(owning, startedRun, 'step_2', {...runContext, backends}), /Ollama · local-a:8b/, 'unstarted backend remains inspectable');
assert.match(executionDetailMarkup(owning, startedRun, 'step_1', {...runContext, backends}), /<dt>Runs on<\/dt><dd>Ollama · local-b:70b<\/dd>/, 'the step detail reports the backend that ran it');

// The browser-side rules match the host's: a name, not a sentence.
for (const [path, value] of [['provider', 'not a backend!'], ['provider', 'a'.repeat(65)], ['model', 'with space'], ['model', 7], ['provider', ' backend']]) {
  const broken = structuredClone(plan);
  broken.agents[0][path] = value;
  assert.throws(() => validateDefinition(broken), /invalid (backend|model)/, `${path}=${JSON.stringify(value)} is refused`);
}
const humanMember = structuredClone(plan);
humanMember.agents[2].provider = 'codex';
humanMember.agents[2].model = 'gpt-5';
assert.throws(() => validateDefinition(humanMember), /Human members do not run on a model backend/);
const humanStep = structuredClone(plan);
humanStep.workflow[2].provider = 'codex';
assert.throws(() => validateDefinition(humanStep), /Human steps do not run on a model backend/);
const declared = structuredClone(plan);
declared.agents[0].provider = 'codex';
declared.agents[0].model = 'gpt-5';
declared.workflow[0].model = 'gpt-5-mini';
declared.agents[0].provider = '';
declared.agents[0].model = '';
validateDefinition(declared);
console.log('PASS: a member and a step each say what runs them, the canvases show it, and a choice this host cannot serve is refused rather than replaced.');

// =========================================================================
// Styling: every class the canvases use has a rule, or the canvas renders bare.
// =========================================================================

const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
const used = new Set([...`${canvas}${planMarkup}${progress}`.matchAll(/class="([^"]+)"/g)].flatMap(match => match[1].split(/\s+/)).filter(name => /^(canvas-edge|canvas-view|edge-hit|edge-line|edge-label|edge-preview|canvas-edge-remove|plan-port|connect-target|canvas-drop-over|run-|plan-node)/.test(name)));
for (const name of used) assert.ok(css.includes('.' + name), `studio.css styles .${name}`);
// The canvas is the primary view; everything else is a line or an overlay.
assert.match(css, /\.shell\{display:grid;grid-template-columns:60px minmax\(0,1fr\)/, 'the rail is slim, so the canvas owns the width');
// The plan canvas is the page, so it is handed every pixel the header leaves:
// the shell fills the window and each wrapper passes the height down, rather
// than the canvas guessing with a fixed height.
assert.match(css, /\.canvas-first-plan \.shell\{height:100dvh;min-height:0\}/, 'the page fills the window');
assert.match(css, /\.canvas-first-plan \.main\{flex:1;min-height:0;display:flex;flex-direction:column\}/, 'the canvas column takes what the header leaves');
assert.match(css, /\.canvas-first-plan \.workbench-plan\{flex:1;min-height:0\}/, 'and the canvas panel takes that');
assert.match(css, /\.canvas-first-plan \.workbench-plan \.canvas-viewport\{flex:1;height:100%\}/, 'so the viewport itself is 100% of the space');
assert.match(css, /\.canvas-first-plan \.canvas-dock\{bottom:16px/, 'the plan action bar floats in the canvas rather than hanging below it');
// Panning the canvas must not start from a press on the bar, and the bar says it
// can be moved by the pointer.
assert.match(studioSource, /closest\('button,input,textarea,select,\[data-plan-node\],\[data-canvas-dock\]'\)/, 'the bar is not treated as the canvas background');
assert.match(css, /\.canvas-dock\{cursor:default;touch-action:auto;/, 'the dock stays anchored and supports native touch scrolling');
assert.doesNotMatch(css, /\.canvas-dock\.dragging/, 'there is no draggable dock state');
assert.match(css, /--plan-height:clamp\(520px,calc\(100vh - 132px\),1400px\)/, 'the run canvas keeps the height it was given');
assert.match(css, /\.plan-canvas\{[^}]*overflow:auto/, 'and scrolls once the plan outgrows it');
assert.match(css, /\.shell\.rail-open\{grid-template-columns:232px minmax\(0,1fr\)\}/, 'the sidebar opens when you ask it to');
assert.match(css, /\.rail:not\(\.open\) \.rail-label\{display:none\}/, 'collapsed shows icons, never half a label');
assert.ok(!/\.rail:hover/.test(css), 'nothing expands on hover');
assert.match(css, /\.calm-workbench \.workbench-inspector\{position:absolute/, 'the plan inspector floats over the canvas');
assert.match(css, /\.calm-workbench \.workbench-resources\{position:absolute/, 'so does the palette');
assert.match(css, /\.run-workbench-layout>\.execution-inspector\{position:absolute/, 'and the step record');
assert.match(css, /\.run-workbench-layout\{grid-template-columns:minmax\(0,1fr\)\}/, 'the run canvas is the whole row');
assert.match(css, /\.application-header\{padding:12px 24px;align-items:center\}/, 'one header bar, not two');
assert.ok(!/\.topbar\{/.test(css), 'the second bar is gone');

// The plan is sized for the canvas it is drawn in, and centred in it.
const filled = setCanvasMetrics({width: 1200, height: 760, ...planShape(plan)});
const filledPositions = Object.values(canvasPositions(plan));
const usedWidth = Math.max(...filledPositions.map(p => p.x)) + filled.nodeWidth - Math.min(...filledPositions.map(p => p.x));
assert.ok(usedWidth / 1200 > 0.6, `the plan uses the width it is given (${Math.round(100 * usedWidth / 1200)}%)`);
assert.ok(filled.nodeWidth >= 300, 'a step stays readable rather than shrinking to fit');
const leftGap = Math.min(...filledPositions.map(p => p.x)), rightGap = 1200 - (Math.max(...filledPositions.map(p => p.x)) + filled.nodeWidth);
assert.ok(Math.abs(leftGap - rightGap) <= filled.gapX + 1, 'the plan is centred, not left in a corner');

// A step's drawn size must be the size the layout, the edges and the drop point
// are computed from, or every arrow lands off its box.
assert.match(planMarkup, /--node-width:\d+px;--node-height:\d+px/, 'the board publishes the measured step size');
assert.match(css, /\.graph-canvas \.plan-node\{[^}]*width:var\(--node-width/, 'and a step is drawn at that size');
assert.doesNotMatch(css, /\.graph-canvas \.plan-node\{[^}]*width:350px/, 'never at a size of its own');
assert.match(canvas, /--node-width:\d+px;--node-height:\d+px/, 'the run canvas draws the same steps the same size');

console.log(`PASS: the canvas is the primary view (slim rail, one header bar, side panels floating over it), studio.css styles every canvas class the markup uses (${used.size} checked).`);

assert.match(planMarkup,/class="canvas-dock"/);
assert.match(planMarkup,/data-action="suggest-plan-change"/);
const auto=canvasPositions(plan);
assert(auto.step_2.x>auto.step_1.x);
assert.equal(auto.step_2.y,auto.step_1.y);

// Both modes must use the same surface, card frame, and view controls.
const {canvasMarkup,canvasStepMarkup}=await import(url('../web/canvas-ui.js'));
for(const mode of ['plan','run']){
  const frame=canvasMarkup({mode,width:900,height:600,metrics:{nodeWidth:350,nodeHeight:220},edges:'',nodes:'',zoom:1.25});
  assert.equal((frame.match(/class="plan-canvas canvas-viewport graph-canvas"/g)||[]).length,1);
  assert.match(frame,/125%/);
  const card=canvasStepMarkup({mode,id:'step',index:0,human:true,selected:true,position:{x:10,y:20},escape});
  assert.match(card,/class="plan-node compact/);
  assert.match(card,/>Human<\/span>/);
  if(mode==='run')assert.doesNotMatch(card,/data-plan-node|data-plan-tool-target|data-canvas-port/);
}
assert.match(canvas,/data-canvas-mode="run"/);
assert.equal(canvasFitZoom(1000,700,400,220),1);
assert.equal(canvasFitZoom(1000,700,12000,600),936/12000,'wide saved layouts fit below the manual zoom minimum');
assert.equal(canvasFitZoom(1000,700,600,10000),588/10000,'tall layouts and delegations fit');
for(const mode of ['plan','run']){
  const attribute=mode==='run'?'data-run-zoom':'data-canvas-zoom';
  const frame=canvasMarkup({mode,width:12000,height:10000,metrics:{nodeWidth:350,nodeHeight:220},edges:'',nodes:'',
    dock:canvasDockMarkup({view:mode==='run'?'run':'plan',escape})});
  assert.match(frame,/tabindex="0"/);
  assert.match(frame,new RegExp(`${attribute}="fit" title="Fit entire graph"`),`fit is on the ${mode} dock`);
  assert.match(frame,new RegExp(`${attribute}="out"`),'and the stepper stays in the corner');
  assert.equal((frame.match(new RegExp(`${attribute}="fit"`,'g'))||[]).length,1,`and the ${mode} canvas offers fit once`);
}
const canvasStyles=await readFile(new URL('../web/studio.css',import.meta.url),'utf8');
assert.match(canvasStyles,/\.run-workbench \.plan-canvas\{[^}]*max-width:none/,'the run canvas overrides the legacy 850px canvas width cap');
// An infinite canvas: the board is moved, not scrolled, so the viewport never
// scrolls and nothing bounds the pan.
assert.match(canvasStyles,/\.canvas-viewport\{scrollbar-width:none;overflow:hidden;overscroll-behavior:contain;cursor:grab\}/,'the viewport is a window, not a scrolled box');
assert.match(canvasStyles,/\.canvas-viewport::-webkit-scrollbar\{display:none\}/);
{
  const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
  assert.match(studio, /flow\.style\.transform=`translate\(\$\{offset\.x\}px, \$\{offset\.y\}px\)`/, 'the board is translated, so it has no edge');
  assert.match(studio, /offset\.x\+=dx\/zoom;offset\.y\+=dy\/zoom;/, 'a screen delta is divided by the zoom, so panning tracks the pointer');
  assert.match(studio, /const point=\{x:\(clientX-view\.left\)\/current-offset\.x,y:\(clientY-view\.top\)\/current-offset\.y\};/,
               'zoom is anchored at the pointer, which is what makes it feel like the canvas');
  assert.match(studio, /\{passive:false\}\);/, 'and the wheel is ours rather than the browser\'s');
  assert.ok(!/area\.scrollLeft/.test(studio) && !/canvasPan\.area\.scroll/.test(studio), 'nothing pans by scrolling any more');

  // The dock is stitched to the view, not to the canvas. It is a sibling of the
  // board, and the board is the only thing that moves, so panning and zooming
  // cannot carry the controls away — which is what a scrolling canvas used to do.
  const docked = canvasMarkup({mode: 'plan', width: 900, height: 600, metrics: {nodeWidth: 350, nodeHeight: 220},
    edges: '', nodes: '<b>step</b>', dock: '<div class="canvas-dock" data-canvas-dock>controls</div>'});
  const board = docked.slice(docked.indexOf('<div class="canvas-flow"'), docked.indexOf('<div class="canvas-dock"'));
  assert.ok(board.length > 100, 'the board is drawn');
  assert.ok(!/canvas-dock/.test(board), 'and the dock is not inside it');
  assert.match(docked, /<\/div><div class="canvas-dock" data-canvas-dock>controls<\/div>/, 'the dock is a sibling of the board');
  assert.match(studio, /flow\.style\.transform=`translate/, 'only the board is transformed');
  const canvasMoves = studio.slice(studio.indexOf('function applyCanvasOffset('), studio.indexOf('function canvasDropPoint('));
  assert.ok(canvasMoves.length > 500 && !/dock/.test(canvasMoves), 'and nothing that moves the canvas touches the dock');
  assert.match(css, /\.canvas-dock\{position:absolute;bottom:16px;left:50%/, 'the dock is anchored to the viewport, not draggable');

  // The maths, exercised rather than described: the board moves in its own
  // coordinates, and the point under the pointer stays under it while zooming.
  const moving = {runCanvasZoom: 2, canvasZoom: 2, flow: {style: {}}, applyRunZoom() {}, applyCanvasZoom() {}};
  moving.board = {querySelector: () => moving.flow, dataset: {canvasMode: 'run'}, getBoundingClientRect: () => ({left: 100, top: 50})};
  runInNewContext(`${runPanning}
panCanvas(board,100,0);globalThis.moved={...canvasOffsets.run};
zoomCanvasAt(board,300,250,1.25);globalThis.zoomed={...canvasOffsets.run};
zoomCanvasAt(board,300,250,2);globalThis.capped=runCanvasZoom;`, moving);
  assert.deepEqual({...moving.moved}, {x: 50, y: 0}, 'a 100px drag at 200% moves the board 50 of its own pixels');
  // The pointer sits at board point ((300-100)/2 - 50, (250-50)/2 - 0) = (50, 100) and
  // must sit there at 250% too, so the offset becomes (200/2.5 - 50, 200/2.5 - 100).
  assert.deepEqual({...moving.zoomed}, {x: 30, y: -20}, 'the point under the pointer is still under it at the new zoom');
  assert.equal(moving.runCanvasZoom, 2.5, 'and the zoom is the one asked for, up to the ceiling');
  assert.equal(moving.capped, 2.5, 'and a larger step cannot pass the ceiling');
}

// Fit, centring and following are *run*, not described: they were the only part
// of the canvas no check executed, which is how a missing declaration reached a
// person as "width not defined" in the notice, and a reversed centring with it.
{
  const fitSource = studioSource.slice(studioSource.indexOf('function fitCanvas('), studioSource.indexOf('async function deleteApplication('));
  const centreSource = studioSource.slice(studioSource.indexOf('function centreRect('), studioSource.indexOf('function stepsArea('));
  const focusSource = studioSource.slice(studioSource.indexOf('function focusRunNode('), studioSource.indexOf('function followActiveNode('));
  const view = {left: 0, top: 0, right: 1000, bottom: 800};
  const board = {style: {width: '400px', height: '200px', zoom: 1}};
  const areaFor = mode => ({clientWidth: 1000, clientHeight: 800, dataset: {canvasMode: mode}, querySelector: () => board,
    getBoundingClientRect: () => view});
  const planArea = areaFor('plan'), runArea = areaFor('run');
  const node = {getBoundingClientRect: () => ({left: 4000, right: 4400, top: 3000, bottom: 3200}), closest: () => runArea};
  const app = {querySelector: selector => selector.includes('data-run-node') ? node : selector.includes('"run"') ? runArea : planArea};
  const fitting = {$: () => app, board, planArea, runArea, CSS: {escape: value => value}, canvasFitZoom, canvasZoom: null, runCanvasZoom: null, rendered: [],
    applyCanvasZoom() { fitting.rendered.push('plan'); }, applyRunZoom() { fitting.rendered.push('run'); }};
  runInNewContext(`${runPanning}\n${fitSource}\n${centreSource}\n${focusSource}
fitCanvas('plan');globalThis.plan={...canvasOffsets.plan,zoom:canvasZoom};
fitCanvas('run');globalThis.run={...canvasOffsets.run,zoom:runCanvasZoom};
centreRect({left:100,right:300,top:100,bottom:200},planArea);globalThis.centred={...canvasOffsets.plan};
canvasOffsets.run={x:0,y:0};focusRunNode('step_1');globalThis.followed={...canvasOffsets.run};`, fitting);

  const zoom = canvasFitZoom(1000, 800, 400, 200);
  assert.equal(fitting.plan.zoom, zoom, 'fit uses the board it measured');
  assert.equal(Math.round((fitting.plan.x + 200) * zoom), 500, 'and puts the board in the middle of the view, horizontally');
  assert.equal(Math.round((fitting.plan.y + 100) * zoom), 400, 'and vertically');
  assert.equal(fitting.run.zoom, zoom, 'the run canvas fits the same way');
  assert.deepEqual(fitting.rendered, ['plan', 'run'], 'and each fit redraws its own canvas');
  // A box whose middle is at (200,150) has to end up at the view's middle (500,400).
  assert.equal(Math.round(200 + (fitting.centred.x - fitting.plan.x) * zoom), 500, 'centring brings the box to the middle');
  assert.equal(Math.round(150 + (fitting.centred.y - fitting.plan.y) * zoom), 400, 'in both directions');
  // The step is off-view, so following it brings its middle (4200,3100) to (500,400).
  assert.equal(Math.round(4200 + fitting.followed.x * zoom), 500, 'following an off-view step brings it to the middle');
  assert.equal(Math.round(3100 + fitting.followed.y * zoom), 400, 'vertically too');
}
for(const mode of ['plan','run']){
  const frame=canvasMarkup({mode,width:900,height:600,metrics:{nodeWidth:350,nodeHeight:220},edges:'',nodes:''});
  for(const button of frame.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)){
    assert.match(button[1],/aria-label="[^"]+"/);
    assert.match(button[1],/title="[^"]+"/,'view controls explain themselves on hover');
  }
}
for(const label of ['Close step settings','Move earlier','Move later','Duplicate step','Remove step']){
  assert.match(planMarkup,new RegExp(`aria-label="${label}" title="${label}"`));
}

{
  const planned = { ...plan, agents: plan.agents.map((agent, index) => index ? agent : { ...agent, provider: 'codex', model: 'gpt-5-codex' }) };
  const declared = runCanvasMarkup(planned, projectLiveRun({ ...run, status: 'running', events: run.events.slice(0, 2) }), 'step_1',
                                   { ...runContext, backends: [{ id: 'codex', name: 'Codex' }] });
  assert.match(declared, /class="step-backend" title="Runs on">Codex · gpt-5-codex<\/span>/, 'a step that names a backend shows it');

  const ran = runCanvasMarkup(plan, projectLiveRun({ ...run, status: 'running',
    events: [...run.events.slice(0, 2), event(3, { type: 'StageStarted', stage: 'step_1', agent: 'researcher', provider: 'ollama', model: 'qwen3' })] }),
    'step_1', { ...runContext, backends: [{ id: 'ollama', name: 'Ollama' }], provider: 'codex' });
  assert.match(ran, /class="step-backend" title="Runs on Ollama · qwen3">Ollama · qwen3<\/span>/, 'and the run shows what it actually used');

  const hostDefault = runCanvasMarkup(plan, projectLiveRun({ ...run, status: 'running', events: run.events.slice(0, 2) }), 'step_1',
                                      { ...runContext, provider: 'codex' });
  assert.match(hostDefault, /class="step-backend" title="Runs on">codex<\/span>/, 'a step on the host default is named as that default, never guessed');
}
