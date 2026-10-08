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
assert.match(bootstrap, /\/api\/health/, 'and checks whether the backend answered before blaming it');

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
assert.ok(used.includes('running') && used.includes('runs'), 'both builders are covered');
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
const keydownStart = source.indexOf("addEventListener('keydown'");
assert.ok(keydownStart > 0, 'the keyboard handler is where the page is built');
const keydown = source.slice(keydownStart, keydownStart + 420);
assert.match(keydown, /ArrowUp[\s\S]{0,200}closest\('\[data-plan-node\]'\)/, 'the arrow keys move the focused step');
assert.match(source, /data-plan-node="\$\{CSS\.escape\(focusId\)\}"\] \.step-summary/, 'and focus lands on something that exists, so they keep working');
assert.match(source, /event\.target\.closest\('\[data-plan-node\]'\)\?\.dataset\.planNode/, 'and the step it belongs to is what decides');
// Editing a step is a canvas gesture: a press that moves drags the card, and a
// second press that did not move opens the step's settings. The press is
// decided from the pointer events the canvas already handles, because opening
// the settings re-renders the card the pointer is over.
assert.match(source, /function editPlanStep\(id\)\{\s*showPlanInspector=true;[\s\S]{0,180}selectedStepId=id;render\(\)/, 'opening a step has one implementation');
assert.equal((source.match(/showPlanInspector=true;[\s\S]{0,180}selectedStepId=id/g) || []).length, 1, 'and every way in lands there');
assert.match(source, /button\.dataset\.planSelect\)\{editPlanStep\(button\.dataset\.planSelect\)/, 'the step button opens it, for a keyboard or a screen reader');
assert.match(source, /second=commit&&!drag\.moved&&lastPlanPress\.id===drag\.id&&now-lastPlanPress\.time<500/, 'a double-click is a second press that did not move');
assert.match(source, /if\(commit&&!drag\.moved\)editPlanStep\(drag\.id\)/, 'and that press opens the step for editing');
// Where a step sits is stored with the definition, so the arrangement is written
// on its own: a drag, an arrow key or Tidy layout must not need the Save button.
assert.match(source, /updatePlan\(moveCanvasStep\(draft\(\),drag\.id[\s\S]{0,80}scheduleLayoutSave\(\)/, 'a dragged step saves its place without pressing Save');
assert.match(source, /action==='tidy-canvas'\)\{updatePlan\(tidyCanvas\(draft\(\)\)\);scheduleLayoutSave\(\)/, 'so does Tidy layout');
assert.match(source, /layoutSaveDefinition\(application\.definition,draft\(\)\)/, 'only the arrangement travels, so an unfinished edit is not saved behind the person');
assert.match(source, /dirty\(\)\?'Unsaved changes':layoutSaved\?'Layout saved'/, 'unsaved content takes precedence over layout success');
assert.match(source, /layoutSaved=!!revision&&result\.revision===revision/, 'a save without a content-version advance reports layout success');
assert.match(source, /if\(currentId===target\)layoutSaved=result\.revision===application\.revision/, 'layout success belongs to the application that was saved');
assert.match(source, /function resetRuns\(\)\{layoutSaved=false;clearTimeout\(layoutSaveTimer\)/, 'switching applications clears layout success and pending debounce');

// One way to do one thing: a control per action, a handler per action, and no
// second name for something that already has one.
const web = await Promise.all(['studio.js', 'live.js', 'workbench-ui.js', 'run-canvas.js', 'run-activity.js', 'canvas-ui.js', 'advanced.js', 'plan.js', 'catalogue-ui.js']
  .map(name => readFile(new URL(`../web/${name}`, import.meta.url), 'utf8')));
const all = web.join('\n');
const controls = [...all.matchAll(/data-action="([^"]+)"/g)].map(match => match[1]);
const handlers = new Set([...all.matchAll(/action==='([^']+)'/g)].map(match => match[1]));
const counted = name => controls.filter(value => value === name).length;

// Both modes use the same application-header Run control.
assert.equal(counted('run'), 1, 'one Run control in the application header');
// One page for the plan and the run: Run is also Run again, and what a run needs
// is configured in a modal rather than a second screen.
assert.equal(counted('show-request-form'), 0, 'no second control opens the request form');
assert.match(source, /data-action="edit-plan"/, 'a finished run leaves the plan one control away');
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

// The tabs are gone: one screen, and the header's slot plays and stops a run.
assert.ok(!/data-tab|role="tab"|tablist/.test(source), 'no view switch survives in the page');
assert.match(source, /data-action="run"/, 'the header offers Run');
assert.match(source, /data-action="cancel-live" \$\{liveBusy\?'disabled':''\}>Stop/, 'and Stop while a run is going');
assert.match(source, /data-action="back-to-plan"/, 'and a way back to the plan');
assert.match(source, /data-action="set-up"/, 'and a way into the setup questions');
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
