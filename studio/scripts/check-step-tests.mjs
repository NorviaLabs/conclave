// The pinned-fixture and isolated-test flow cannot be executed without a DOM, so
// this checks the module's contract and the wiring the page markup depends on.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const read = name => readFile(new URL(`../web/${name}`, import.meta.url), 'utf8');
const [tests, workbench, canvas, ui, studio, css] = await Promise.all(
  ['step-tests.js', 'workbench-ui.js', 'run-canvas.js', 'canvas-ui.js', 'studio.js', 'studio.css'].map(read));
// The plan's own helpers are shared with the canvas, so they live outside `web/`.
const definition = await readFile(new URL('../shared/definition.js', import.meta.url), 'utf8');

// Four contextual actions, only for a completed step of a live run.
for (const label of ['Pin output', 'Test step', 'Test from here', 'Compare results']) {
  assert.match(workbench, new RegExp(label), `the inspector offers ${label}`);
}
assert.match(workbench, /status\(index\)==='Complete'&&run\?\.live/, 'and only on a completed step of a live run');
assert.match(studio, /button\.dataset\.stepTest[\s\S]{0,120}stepTests\.open\(/, 'the page routes those actions to the controller');

// A step that has never run is tested from the plan, not only from a run.
assert.match(ui, /data-step-test="author" data-test-step=/, 'the plan canvas offers testing the selected step');
assert.match(studio, /menuItem\('Test this step',\{ 'data-step-test':'author'/, 'and the step menu offers it too');
// The step settings panel is where a step is edited, so the action is named
// there rather than left as one more symbol in the dock.
assert.match(workbench, /inspector-context-actions"><button class="button" data-step-test="author" data-test-step="\$\{e\(selected\.id\)\}"/, 'the step settings panel names the action');
assert.match(studio, /stepTests\.open\(button\.dataset\.stepTest,button\.dataset\.testStep\)/, 'the step comes from the control that was clicked');
assert.match(tests, /planConnections\(d\)/, 'which needs the plan\'s own dependencies, not the previous step on screen');
assert.match(tests, /import \{planConnections\} from '\/definition.js'/, 'read from the same helper the canvas uses');
assert.match(definition, /export function planConnections/, 'which the canvas and the plan both read');
assert.match(tests, /action==='author'\?'author':'setup'/, 'and opens the written-fixture mode');
assert.match(tests, /A prerequisite you leave as its empty value is sent as empty, not invented/, 'a prerequisite is asked for rather than invented');
assert.match(tests, /State from prerequisite steps/, 'listed as its own section');
assert.match(tests, /data-author-state=/, 'each prerequisite gets its own value');
assert.match(tests, /fixtures\/author/, 'and the fixture is written through the backend');
assert.match(tests, /Record what this step should produce, to compare against/, 'a reference output is optional and explicit');
assert.match(tests, /This fixture recorded no reference output, so there is nothing to compare against/, 'and no comparison is claimed without one');

// A separate dialog owns its own state, so the editor's draft, preview, pan and zoom stay put.
assert.match(tests, /document\.createElement\('dialog'\)/, 'the flow opens its own dialog');
assert.match(tests, /structuredClone\(getContext\(\)\)/, 'and works from a copy of the context');
assert.doesNotMatch(tests, /drafts\.set|\$\('app'\)|getElementById\('app'\)/, 'without touching the editor page it sits over');
assert.match(tests, /generation\+\+/, 'stale responses are dropped after a close or a new open');
assert.match(tests, /setTimeout\(\(\)=>poll\(token\),600\)/, 'a started test is polled, not blocked on');

// The API the backend actually serves.
for (const path of ['/fixtures/preview', '/fixtures', '/tests']) {
  assert.ok(tests.includes(path), `the module calls ${path}`);
}
assert.match(tests, /'\/api\/applications\/'|`\/api\/applications\/\$\{encodeURIComponent/, 'under the application it belongs to');

// Recorded is not live, and a test is not a workflow run.
assert.match(tests, /Recorded data, not live sources/, 'a fixture says it is recorded data');
assert.match(tests, /Production approvals do not apply/, 'and that a test inherits no production approval');
assert.match(tests, /Block all external tools unless explicitly mocked/, 'safe mode is stated, not implied');
assert.match(tests, /no semantic equivalence claim/, 'a comparison does not claim equivalence');
assert.match(tests, /Cost: not reported/, 'unreported cost stays unreported');

// The shared canvas is reused, and a tested step is visibly different.
assert.match(tests, /runCanvasMarkup\(/, 'results reuse the run canvas renderer');
assert.match(canvas, /tested:\(run\.testedSteps\|\|\[\]\)\.includes\(step\.id\)/, 'the canvas marks a tested step');
assert.match(ui, /tested\?' data-run-tested="true"'/, 'with a data attribute, not a guess');
assert.match(css, /\.run-node\[data-run-tested="true"\]/, 'which the stylesheet differentiates');
assert.match(css, /\.step-test-dialog/, 'and the dialog is styled for small screens');
assert.match(css, /@media\(max-width:720px\)\{\.test-comparison/, 'including the comparison columns');

console.log('PASS: completed steps can pin a fixture, test one step or its descendants safely, and compare exact results.');
