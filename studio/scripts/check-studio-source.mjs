// studio.js builds the whole page from one template literal in render(), and it
// needs a DOM, so no other check can execute it. A partial edit there can leave
// an identifier interpolated into the markup but never declared, which only
// shows up as a blank page in a browser. This scans that template for it.
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';

const source = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const bootstrap = await readFile(new URL('../web/bootstrap.js', import.meta.url), 'utf8');
// A page that fails to load must not be reported as a backend that is down.
assert.match(bootstrap, /Studio failed to load/, 'a module failure says so');
assert.match(bootstrap, /apiUrl\(settings,'\/api\/health'\)/, 'and asks the address the page actually uses');
assert.match(bootstrap, /status<500/, 'counting any answer below a server error as an answer: sign-in replies 401, a proxy that cannot reach its API replies 503');
assert.match(bootstrap, /Nothing answered at/, 'naming where it looked');
assert.match(bootstrap, /CONCLAVE_UI_ORIGINS=/, 'and the setting that would explain a silent refusal');
assert.match(bootstrap, /\$\{escaped\(detail\)\}/, 'with what the page itself reported, escaped');
assert.match(bootstrap, /\/api\/config\/defaults','\/api\/config\/catalogue'/, 'and the status of the modules the page imports over REST, which is what a failed import is usually about');

// Both functions build page-level markup that no other check can execute.
const body = name => {
  const start = source.indexOf(`function ${name}(`);
  assert.ok(start > 0, `${name}() is where page markup is built`);
  const end = source.indexOf('\n}', start);
  assert.ok(end > start, `${name}() ends`);
  return source.slice(start, end);
};
const template = body('render') + body('historyMarkup');

const globals = new Set(['String', 'Number', 'Math', 'JSON', 'Date', 'Object', 'Array', 'Boolean', 'Set', 'Map', 'CSS', 'URL', 'crypto', 'document', 'window', 'setTimeout', 'clearTimeout']);
// Declaration shapes only. An earlier version of this accepted any `{name}`,
// which matched the interpolation it was meant to catch and hid the bug.
const declared = name => {
  if (globals.has(name)) return true;
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return [
    // An imported helper is as available to page markup as a local function.
    new RegExp(`import\\s*\\{[^}]*\\b${escaped}\\b[^}]*\\}\\s*from`),
    new RegExp(`(?:let|const|var)[^;\\n]*\\b${escaped}\\b\\s*=`),
    new RegExp(`[,{]\\s*${escaped}\\s*=`),
    new RegExp(`^\\s*${escaped}\\s*[,=]`, 'm'),
    new RegExp(`function\\s+${escaped}\\s*\\(`),
    new RegExp(`function\\s*[A-Za-z_$\\w]*\\s*\\([^)]*\\b${escaped}\\b`),
    new RegExp(`(?:const|let|var)\\s*\\{[^}]*\\b${escaped}\\b`),
    new RegExp(`[(,]\\s*${escaped}\\s*[,)]\\s*=>`),
  ].some(pattern => pattern.test(source));
};

const used = [...new Set([...template.matchAll(/\$\{([A-Za-z_$][\w$]*)/g)].map(match => match[1]))];
assert.ok(used.length > 8, 'the page markup interpolates state');
// One identifier only the page markup uses, and one only the run list uses.
assert.ok(used.includes('railOpen') && used.includes('runs'), 'both builders are covered');
const missing = used.filter(name => !declared(name));
assert.deepEqual(missing, [], `the page markup interpolates an undeclared identifier: ${missing.join(', ')}`);

// studio.js cannot be executed by any check, so a call to a function that no
// longer exists only shows up as a blank page in a browser. Scan for those too.
const defined = new Set([
  ...[...source.matchAll(/(?:function|class)\s+([A-Za-z_$][\w$]*)/g)].map(match => match[1]),
  // Every name in a declaration list, not just the first: `let a=1,b=2`.
  ...[...source.matchAll(/(?:let|const|var)\s+([^;\n]*)/g)].flatMap(match => match[1].split(',').map(part => part.trim().split(/\s*=/)[0].replace(/[{}[\]]/g, '').trim())),
  // Parameters of declared functions and arrows.
  ...[...source.matchAll(/function\s*[A-Za-z_$\w]*\s*\(([^)]*)\)/g)].flatMap(match => match[1].split(',').map(part => part.trim().split(/[\s=]/)[0])),
  ...[...source.matchAll(/\(([^)]*)\)\s*=>/g)].flatMap(match => match[1].split(',').map(part => part.trim().split(/[\s=]/)[0])),
  ...[...source.matchAll(/import\s*\{([^}]*)\}/g)].flatMap(match => match[1].split(',').map(name => name.trim().split(/\s+as\s+/).pop())),
  ...[...source.matchAll(/import\s+([A-Za-z_$][\w$]*)\s+from/g)].map(match => match[1]),
]);
const builtins = new Set(['fetch', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'requestAnimationFrame', 'parseInt', 'parseFloat', 'isNaN', 'structuredClone', 'encodeURIComponent', 'decodeURIComponent', 'atob', 'btoa', 'alert', 'confirm', 'prompt',
  'if', 'else', 'for', 'while', 'do', 'switch', 'catch', 'return', 'typeof', 'await', 'new', 'function', 'async', 'delete', 'void', 'yield']);
// Code positions only, so prose and keywords cannot make noise: a name called at
// the start of a statement, passed to a timer, or registered as a handler. A
// function deleted while such a reference remained is what blanked the page.
const referenced = new Set([
  ...[...source.matchAll(/(?:^|[;{}])\s*([a-z][\w$]*)\s*\(/gm)].map(match => match[1]),
  ...[...source.matchAll(/(?:setTimeout|setInterval|requestAnimationFrame)\(\s*([a-z][\w$]*)/g)].map(match => match[1]),
  // A handler passed by name, not an inline arrow function's parameter.
  ...[...source.matchAll(/addEventListener\('[^']*',\s*([a-z][\w$]*)\s*[,)]/g)].map(match => match[1]),
]);
const undeclared = [...referenced].filter(name => !defined.has(name) && !builtins.has(name));
assert.deepEqual(undeclared, [], `studio.js refers to a function that does not exist: ${undeclared.join(', ')}`);

// Canvas interaction: the whole step drags, and the arrow keys follow the step
// that has focus rather than an icon inside it.
const dragStart = source.indexOf('// Pressing anywhere on a step moves it');
assert.ok(dragStart > 0, 'the canvas drag handler is where the page is built');
const canvasDrag = source.slice(dragStart, dragStart + 900);
assert.match(canvasDrag, /event\.target\.closest\('\[data-plan-node\]'\)/, 'pressing a step starts a drag');
assert.match(canvasDrag, /data-canvas-port/, 'while its port and buttons keep their own meaning');
// The arrow-key handler that moves a focused step, found by what it does rather
// than by being the first keydown listener on the page (the menu has its own).
const keydownStart = source.indexOf("const id=event.target.closest('[data-plan-node]')");
assert.ok(keydownStart > 0, 'the keyboard handler is where the canvas is');
const keydown = source.slice(keydownStart - 200, keydownStart + 420);
assert.match(keydown, /ArrowUp[\s\S]{0,200}closest\('\[data-plan-node\]'\)/, 'the arrow keys move the focused step');
assert.match(source, /data-plan-node="\$\{CSS\.escape\(focusId\)\}"\] \.step-summary/, 'and focus lands on something that exists, so they keep working');
assert.match(source, /event\.target\.closest\('\[data-plan-node\]'\)\?\.dataset\.planNode/, 'and the step it belongs to is what decides');
// Editing a step is a canvas gesture: a press that moves drags the card, and a
// second press that did not move opens the step's settings. The press is
// decided from the pointer events the canvas already handles, because opening
// the settings re-renders the card the pointer is over.
assert.match(source, /function editPlanStep\(id\)\{[\s\S]{0,110}showPlanInspector=true;[\s\S]{0,180}selectedStepId=id;render\(\)/, 'opening a step has one implementation');
assert.equal((source.match(/showPlanInspector=true;[\s\S]{0,180}selectedStepId=id/g) || []).length, 1, 'and every way in lands there');
assert.match(source, /button\.dataset\.planSelect\)\{editPlanStep\(button\.dataset\.planSelect\)/, 'the step button opens it, for a keyboard or a screen reader');
assert.match(source, /second=commit&&!drag\.moved&&lastPlanPress\.id===drag\.id&&now-lastPlanPress\.time<500/, 'a double-click is a second press that did not move');
assert.match(source, /if\(commit&&!drag\.moved\)editPlanStep\(drag\.id\)/, 'and that press opens the step for editing');
// Where a step sits is stored with the definition, so the arrangement is written
// on its own: a drag, an arrow key or Tidy layout must not need the Save button.
assert.match(source, /updatePlan\(moveCanvasStep\(draft\(\),drag\.id[\s\S]{0,80}scheduleLayoutSave\(\)/, 'a dragged step saves its place without pressing Save');
assert.match(source, /action==='tidy-canvas'\)\{updatePlan\(tidyCanvas\(draft\(\)\)\);scheduleLayoutSave\(\)/, 'so does Tidy layout');
assert.match(source, /layoutSaveDefinition\(application\.definition,draft\(\)\)/, 'only the arrangement travels, so an unfinished edit is not saved behind the person');
assert.match(source, /dirty\(\)\?'Unsaved changes':`Saved/, 'unsaved content takes precedence over saved version');
assert.doesNotMatch(source, /'Layout saved'/, 'layout autosave adds no success notice');
// Autosaving the layout announces nothing: the canvas shows the arrangement, and
// the status says it is saved. A save that fails is still reported.
{
  const layout = source.slice(source.indexOf('async function saveLayout('), source.indexOf('function resetRuns('));
  assert.ok(layout.length > 100, 'the layout autosave is present');
  assert.ok(!/notify\('Layout saved/.test(layout), 'and says nothing when it works');
  assert.match(layout, /catch\(error\)\{notify\(error\.message\);\}/, 'while a failure still says so');
}
assert.match(source, /layoutSaved=!!revision&&result\.revision===revision/, 'a save without a content-version advance reports layout success');
assert.match(source, /if\(currentId===target\)layoutSaved=result\.revision===application\.revision/, 'layout success belongs to the application that was saved');
assert.match(source, /function resetRuns\(\)\{workspaceMode='build';stepExamples=null;stepExamplesApp=null;layoutSaved=false;clearTimeout\(layoutSaveTimer\)/, 'switching applications clears layout success and pending debounce');

// One way to do one thing: a control per action, a handler per action, and no
// second name for something that already has one.
const web = await Promise.all(['studio.js', 'live.js', 'workbench-ui.js', 'integrations-ui.js', 'run-canvas.js', 'run-activity.js', 'canvas-ui.js', 'advanced.js', 'plan.js', 'catalogue-ui.js', 'menu-ui.js']
  .map(name => readFile(new URL(`../web/${name}`, import.meta.url), 'utf8')));
const all = web.join('\n');
// An icon-only control has no visible label, so it must carry both a hovertext
// and an accessible name: a mouse user and a screen reader each need one.
const iconOnly = [], unnamed = [];
for (const [index, name] of ['studio.js', 'live.js', 'workbench-ui.js', 'integrations-ui.js', 'run-canvas.js', 'run-activity.js', 'canvas-ui.js', 'advanced.js', 'plan.js', 'catalogue-ui.js'].entries()) {
  for (const match of web[index].matchAll(/<button([^>]*?)>((?:[^<]|<span[^>]*>)*?)<\/button>/g)) {
    const [, attrs, body] = match;
    const iconOnlyBody = (body.includes('uiIcon(') || ['×', '⨯'].includes(body.trim())) && !/>[^<]*[A-Za-z]{2}/.test(body);
    if (!iconOnlyBody) continue;
    iconOnly.push(`${name}: ${body.trim().slice(0, 20)}`);
    if (!attrs.includes('title="') || !attrs.includes('aria-label="')) unnamed.push(iconOnly.at(-1));
  }
}
assert.deepEqual(unnamed, [], `icon-only controls need a title and an aria-label: ${unnamed.join(', ')}`);
assert.ok(iconOnly.length >= 10, 'the icon-only controls this rule covers are actually found');
console.log(`PASS: ${iconOnly.length} icon-only controls, each naming itself on hover and to a screen reader.`);

const controls = [...all.matchAll(/data-action="([^"]+)"/g)].map(match => match[1]);
const handlers = new Set([...all.matchAll(/action==='([^']+)'/g)].map(match => match[1]));
const counted = name => controls.filter(value => value === name).length;

// Run lives in the canvas's floating action bar. The setup screen has no canvas,
// so its header carries the one Run button instead: never two on one screen.
assert.equal(counted('run'), 3, 'canvas, setup and empty Run mode each offer a run entry point, never on the same screen');
assert.match(source, /\$\{view==='setup'\?`<button class="button primary" data-action="run"/, 'the header Run belongs to the setup screen only');
// One page for the plan and the run: Run is also Run again, and what a run needs
// is configured in a modal rather than a second screen.
assert.equal(counted('show-request-form'), 0, 'no second control opens the request form');
// There is no Edit plan: the plan tools are in the same bar and using one leaves
// the run, so a separate mode switch would be a second name for the same thing.
assert.ok(!/edit-plan/.test(all), 'no Edit plan control survives anywhere');
assert.match(source, /function openRunConfiguration\(\)[\s\S]{0,400}\$\('run-dialog'\)\.open[\s\S]{0,40}showModal\(\)/, 'Run and Run again open one configuration modal');
assert.match(source, /studioLiveMarkup\(true\)/, 'the modal is the request form, not a second run view');
assert.ok(!/view==='run'|view='run'/.test(source), 'the canvas is one page rather than a plan view and a run view');
assert.equal(counted('save'), 1, 'one control saves');
assert.equal(counted('export'), 1, 'one control exports');
assert.equal(counted('source-settings'), 1, 'one control reaches source settings');
assert.equal(counted('open-catalogue'), 3, 'the catalogue opens from the rail and its two panes');

// Names that used to be a second name for an action that already had one.
for (const retired of ['save-use', 'tool-catalogue', 'connector-catalogue', 'close-advanced']) {
  assert.equal(counted(retired), 0, `${retired} is gone: one action has one name`);
}
assert.doesNotMatch(all, /data-action="catalogue"/, 'opening the catalogue is open-catalogue');
assert.doesNotMatch(all, /data-action="advanced"/, 'the advanced view toggles by one name');

// Every control has a handler, and every handler a control: the add-* and
// remove-* families share one branch.
const unhandled = [...new Set(controls)].filter(name => !handlers.has(name) && !/^(add|remove)-/.test(name));
assert.deepEqual(unhandled, [], `controls with no handler: ${unhandled.join(', ')}`);
// No exemptions here: an action name nothing offers is dead vocabulary.
const orphaned = [...handlers].filter(name => !controls.includes(name));
assert.deepEqual(orphaned, [], `handlers with no control: ${orphaned.join(', ')}`);

// The tabs are gone: one screen, and the header's slot starts a run.
assert.ok(!/data-tab|role="tab"|tablist/.test(source), 'no view switch survives in the page');
assert.match(source, /data-action="run"[^>]*title="\$\{preview\?\.live\?'Run again':'Run this application'\}"/, 'the canvas starts a run with one named play icon');
assert.match(source, /data-action="back-to-plan"/, 'and a way back to the plan');
// Set up is an application-level action, so it lives in the canvas's
// Application menu rather than as a second header button.
assert.ok(!/data-action="set-up"/.test(source), 'the header keeps no Set up button');
assert.match(all, /<details class="advanced-file-actions"><summary>Application<\/summary>[\s\S]{0,600}?<button class="button" data-action="set-up">Set up<\/button>/, 'Set up opens the setup questions from the Application menu');
// Requests live in a list of their own, not on the canvas and not in a picker.
assert.match(source, /data-action="show-history"/, 'the header reaches the request list');
// The sidebar is icons when collapsed, with a control that opens it.
assert.match(source, /data-action="toggle-rail"/, 'the sidebar has a toggle');
assert.match(source, /aria-expanded="\$\{railOpen\}"/, 'and says whether it is open');
assert.match(source, /class="rail-label"/, 'labels are hidden when collapsed, not clipped');
assert.match(source, /class="app-mark"/, 'a collapsed application shows its initial');
assert.match(source, /function historyMarkup\(\)/, 'the list is built in one place');
assert.match(source, /data-run-open=/, 'a row opens its run');
assert.ok(!/data-live-history/.test(source), 'no request picker survives in the page');
for (const action of ['run', 'back-to-plan', 'set-up']) {
  assert.match(source, new RegExp(`action==='${action}'`), `${action} is handled`);
}
console.log(`PASS: the page markup interpolates ${used.length} identifiers, all declared, with one screen, one play/stop control and no view switch.`);
