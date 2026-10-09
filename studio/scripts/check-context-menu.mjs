// Offline checks for the canvas context menu: which surfaces answer a press,
// what each menu offers, that the items reuse the page's own hooks, and that the
// run controls are deliberately not in it. No browser and no provider calls.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {menuItem, menuSeparator, menuMarkup, menuTarget, menuFocusTargets} from '../web/menu-ui.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const source = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');

// --- Where a press lands -----------------------------------------------------
// A stand-in element: selectors are the keys it answers to, so `closest` and
// `getAttribute` behave the way the real ones do for the selectors we use.
const element = (selectors = {}, parent = null) => ({
  selectors,
  closest(list) {
    for (const selector of String(list).split(',')) if (selector.trim() in this.selectors) return this;
    return parent?.closest?.(list) ?? null;
  },
  getAttribute(name) {
    const key = `[${name}]`;
    return key in this.selectors ? this.selectors[key] : null;
  },
  hasAttribute(name) { return `[${name}]` in this.selectors; }
});
const planCanvas = element({'.canvas-viewport': '', '[data-canvas-mode]': 'plan'});
const runCanvas = element({'.canvas-viewport': '', '[data-canvas-mode]': 'run'});
const planNode = element({'[data-plan-node]': 'draft'}, planCanvas);
const runCard = element({'[data-run-node]': 'draft'}, runCanvas);
const connection = element({'[data-canvas-edge]': 'draft|review'}, planCanvas);

assert.deepEqual(menuTarget({closest: () => null}), null, 'a press outside the canvas has no menu');
assert.deepEqual(menuTarget(planNode), {kind: 'plan-node', id: 'draft'}, 'a step answers with its own actions');
assert.deepEqual(menuTarget(runCard), {kind: 'run-node', id: 'draft'}, 'a run card too');
assert.deepEqual(menuTarget(connection), {kind: 'plan-edge', id: 'draft|review', from: 'draft', to: 'review'}, 'a connection answers with its own');
assert.equal(menuTarget(planCanvas).kind, 'plan-canvas', 'the plan background answers');
assert.equal(menuTarget(runCanvas).kind, 'run-canvas', 'the run background answers');
// The settings fields are not canvas: the native menu stays available there.
assert.equal(menuTarget(element({input: ''}, planCanvas)), null, 'an input keeps the browser menu');
assert.equal(menuTarget(element({}, element({'.shell': ''}))), null, 'so does anything outside a canvas');
assert.equal(menuTarget(planCanvas).kind, 'plan-canvas', 'while the canvas surface itself is the background');

// --- Markup ------------------------------------------------------------------
const marked = menuMarkup({label: 'Step actions: Draft', escape, point: {x: 120, y: 80}, items: [
  menuItem('Edit step', {'data-plan-select': 'draft'}),
  menuItem('Move earlier', {'data-plan-move': 'draft', 'data-direction': 'earlier'}, {disabled: true, why: 'This is already the first step'}),
  menuSeparator(),
  menuItem('Remove step', {'data-plan-remove': 'draft'}, {destructive: true})
]});
assert.match(marked, /role="menu" aria-label="Step actions: Draft"/, 'the menu names itself');
assert.match(marked, /style="left:120px;top:80px"/, 'and is placed where the press was');
assert.equal((marked.match(/role="menuitem"/g) || []).length, 3, 'each item is a menu item');
assert.equal((marked.match(/role="separator"/g) || []).length, 1, 'separators group them');
assert.match(marked, /data-plan-select="draft"/, 'an item carries the same hook as the control it stands for');
assert.match(marked, /disabled aria-disabled="true" title="This is already the first step" data-plan-move="draft" data-direction="earlier"/, 'an unusable item is disabled and explains why');
assert.match(marked, /class="canvas-menu-item destructive"[^>]*data-plan-remove/, 'the destructive one is last and marked');
assert.ok(!/onclick/i.test(marked), 'no inline handlers');
assert.match(menuMarkup({label: 'x', escape, point: {x: 0, y: 0}, items: [menuItem('a & b', {'data-plan-select': 'a"b'})]}), /a &amp; b/, 'labels are escaped');

// --- What each menu offers ---------------------------------------------------
const menuCode = source.slice(source.indexOf('// The canvas context menu: the actions'), source.indexOf('// Right-click on the canvas opens the menu'));
assert.ok(menuCode.length > 800, 'the menu builders were found');
const menuContext = {
  escape, console, Math, menuItem, menuSeparator,
  // The connection list of the stand-in plan: one dependency into the second step.
  planConnections: () => [{from: 'draft', to: 'review'}],
  draft: () => ({workflow: [{id: 'draft', name: 'Draft the brief', output: 'brief', agent: 'writer'}, {id: 'review', name: 'Review', output: 'brief', agent: 'writer'}],
                 state: [{key: 'brief', label: 'Brief', type: 'text'}]}),
  followActive: false, activityExpanded: true,
  preview: {definition: {workflow: [{id: 'draft', name: 'Draft the brief', output: 'brief'}]}, state: {brief: 'Written'}, events: [{type: 'StageStarted'}]},
  staleRunSteps: () => new Set()
};
runInNewContext(`${menuCode}\nglobalThis.specs = {planNodeMenu,planEdgeMenu,planCanvasMenu,runCanvasMenu,runNodeMenu,menuSpec};`, menuContext);
// Spread into this realm: the builders run inside the sandbox, whose arrays have
// their own prototype and would fail an identity comparison.
const labels = spec => [...spec.items.filter(item => !item.separator).map(item => item.label)];
const actions = spec => [...spec.items.filter(item => !item.separator).map(item => `${Object.keys(item.attrs)[0]}=${Object.values(item.attrs)[0]}`)];

const node = menuContext.specs.planNodeMenu('draft', 'Draft the brief');
assert.deepEqual(labels(node), ['Edit step', 'Advanced limits', 'Add step after', 'Add parallel branch', 'Move earlier', 'Move later', 'Duplicate step',
  'New member for this step', 'Edit the shared member', 'Make a copy for this step', 'Edit output structure in setup',
  'Connect to another step', 'Remove what this step depends on', 'Centre in view', 'Remove step'], 'the step menu holds the step commands');
assert.equal(node.items.find(item => item.label === 'Move earlier').disabled, true, 'a first step cannot move earlier');
assert.equal(node.items.at(-1).destructive, true, 'removal is the destructive last item');
// The items are grouped rather than one flat list.
assert.ok(node.items.filter(item => item.separator).length >= 3, 'the items are grouped');
assert.deepEqual(actions(node).filter(value => value.startsWith('data-action')), ['data-action=new-step-member', 'data-action=edit-shared-member', 'data-action=isolate-member', 'data-action=set-up'], 'the panel commands reuse their own hooks');
// The three that act inside the panel ask for it, so the result is visible.
assert.deepEqual([...node.items.filter(item => !item.separator && item.attrs['data-menu-reveal'] === 'panel').map(item => item.attrs['data-action'])],
  ['new-step-member', 'edit-shared-member', 'isolate-member'], 'the commands that act in the panel open it');
// Nothing that is a field moves into a menu: it could not show a value.
for (const item of node.items.filter(item => !item.separator)) {
  assert.ok(!/^(Name|What happens|What does it produce|Team member|Member name|Member type|Responsibility|Output label|Tool uses|Seconds)\b/.test(item.label), `${item.label} is a field, which belongs in the panel`);
}
assert.ok(!labels(node).some(label => /run|pause|stop/i.test(label)), 'no run control sneaks into a step menu');
// Every plan action reuses the hook the on-screen control already has.
for (const item of node.items.filter(item => !item.separator)) {
  const hook = Object.keys(item.attrs)[0];
  assert.match(source, new RegExp(`dataset\\.${hook.replace(/^data-/, '').replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())}\\b`), `${hook} is handled by the page's own click handler`);
}
const edgeMenu = menuContext.specs.planEdgeMenu('draft|review', 'draft', 'review', name => name);
assert.deepEqual(labels(edgeMenu), ['Insert a step between', 'Add a parallel branch from here', 'Reverse direction',
  'Show draft', 'Show review', 'Centre this connection in view',
  'Change where this connection ends', 'Remove this connection'], 'the connection menu holds the connection actions');
assert.equal(edgeMenu.items.at(-1).destructive, true, 'removing the connection is destructive and last');
// Rewiring reuses the port flow, and a parallel branch reuses the picker hook.
const item = label => edgeMenu.items.find(entry => !entry.separator && entry.label === label);
assert.equal(item('Change where this connection ends').attrs['data-menu-reconnect'], 'draft', "rewiring starts from this connection's source");
assert.equal(item('Add a parallel branch from here').attrs['data-add-parallel'], 'review', "a parallel branch joins the target's inputs");
assert.deepEqual(actions(edgeMenu).slice(0, 1), ['data-insert-from=draft'], 'and reuses the insertion hook');
assert.equal(edgeMenu.items.at(-1).destructive, true, 'removing a connection is destructive');

const canvasMenu = menuContext.specs.planCanvasMenu();
assert.deepEqual(labels(canvasMenu), ['Add a step here', 'Suggest a change', 'Arrange', 'Fit to view', 'Reset to 100%', 'Full screen'], 'the background menu holds the canvas actions');
// The pressed point is kept by the page, not the builder, because the item that
// uses it closes the menu before it runs.
assert.match(source, /menuPoint=point;/, 'the pressed point outlives the menu');
// The panel's commands read the selected step, so the pressed step becomes it.
assert.match(source, /target\.kind==='plan-node'&&selectedStepId!==target\.id\)\{selectedStepId=target\.id/, 'opening a step menu selects that step');
assert.match(source, /dataset\.menuPanel\b[\s\S]{0,220}section\.open=true/, 'Advanced limits opens the panel at that section');
assert.match(source, /dataset\.menuReveal==='panel'&&!showPlanInspector\)\{showPlanInspector=true;render\(\)/, 'a panel command opens the panel first');
assert.match(source, /dataset\.menuDisconnectAll[\s\S]{0,260}disconnectPlanSteps/, 'removing what a step depends on goes through the same connection code');
assert.match(source, /dataset\.menuCentre[\s\S]{0,60}centrePlanStep/, 'and centring only scrolls the canvas');
// The connection's actions live on one focusable handle, not on two floating
// buttons: this is the first keyboard route to them.
assert.match(source, /dataset\.canvasEdgeMenu[\s\S]{0,200}openMenu\(\{kind:'plan-edge'/, 'the handle opens the connection menu');
assert.match(source, /event\.key==='Delete'\|\|event\.key==='Backspace'[\s\S]{0,220}disconnectPlanSteps/, 'Delete on the handle removes the connection');
assert.ok(!/canvas-edge-remove|canvas-edge-insert/.test(source + css), 'the old floating buttons are gone');
assert.match(css, /\.canvas-edge-handle\{position:absolute[^}]*\.canvas-edge-handle:focus-visible|[\s\S]*\.canvas-edge-handle/, 'the handle is styled');
assert.match(css, /\.canvas-edge-handle:hover,\.canvas-edge-handle:focus-visible/, 'and shows focus');
// The edge itself is focusable, so a connection can be reached without a mouse.
assert.ok(source.includes("event.key==='Enter'||event.key===' '"), 'Enter selects the focused connection');
assert.match(source, /const edge=event\.target\.closest\?\.\('\[data-canvas-edge\]'\);[\s\S]{0,200}selectedEdge=selectedEdge===edge\.dataset\.canvasEdge/, 'and selecting it is a keyboard action too');
assert.match(css, /\.graph-canvas \.canvas-edge:focus-visible \.edge-line/, 'and the focused connection is visible');
assert.ok(!/canvas-edge-remove|canvas-edge-insert/.test(source + css), 'the old floating buttons are gone');
assert.match(source, /openStepPicker\(\{at:menuPoint\.canvas/, 'and places a new step where the menu was opened');
const runCanvasMenu = menuContext.specs.runCanvasMenu();
assert.deepEqual(labels(runCanvasMenu), ['Follow the active step', 'Hide the activity stream', 'Run information', 'Fit to view', 'Reset to 100%'], 'the run background menu holds the view and inspection actions');
const runMenu = menuContext.specs.runNodeMenu('draft', 'Draft the brief');
assert.deepEqual(labels(runMenu), ['Step details', 'Show this step in activity', "Copy this step's output"], 'the run card offers inspection, not control');
// The one rule that matters most: the menu never becomes a hidden run control.
for (const spec of [node, edgeMenu, canvasMenu, runCanvasMenu, runMenu]) {
  for (const item of spec.items.filter(item => !item.separator)) {
    const action = item.attrs['data-action'];
    assert.ok(!['run', 'start-live', 'pause-live', 'cancel-live', 'resume-live'].includes(action), `${item.label} is not a run control`);
    assert.ok(!/^(Run|Stop|Pause|Resume)(\b|\s)/.test(item.label) || item.label === 'Run information', `${item.label} does not name a run control`);
  }
}
// Every `data-menu-*` hook the builders emit has a handler in the page: this is
// the same rule the page applies to its own controls, for the menu's vocabulary.
const menuHooks = new Set();
for (const spec of [node, edgeMenu, canvasMenu, runCanvasMenu, runMenu]) {
  for (const item of spec.items.filter(item => !item.separator)) for (const name of Object.keys(item.attrs)) if (name.startsWith('data-menu-')) menuHooks.add(name);
}
assert.ok(menuHooks.size >= 6, 'the menu vocabulary is exercised');
for (const hook of menuHooks) {
  // `data-menu-panel` is reachable as dataset.menuPanel, the same way the page reads it.
  const property = hook.replace(/^data-/, '').replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
  assert.ok(source.includes(`dataset.${property}`), `${hook} has a handler`);
  assert.ok(source.includes(`'${hook}'`) || source.includes(`"${hook}"`), `${hook} is emitted by the menu`);
}

// A step that has produced nothing cannot offer its output, and a stale result says so.
const empty = runInNewContext(`preview={definition:{workflow:[{id:'draft',name:'Draft',output:'brief'}]},state:{brief:''},events:[]};runNodeMenu('draft','Draft')`, menuContext);
assert.deepEqual([...empty.items.filter(item => !item.separator).map(item => !!item.disabled)], [false, true, true], 'what cannot act yet is disabled, not missing');
const stale = runInNewContext(`preview={definition:{workflow:[{id:'draft',name:'Draft',output:'brief'}]},state:{brief:'x'},events:[{type:'StageStarted'}]};staleRunSteps=()=>new Set(['draft']);runNodeMenu('draft','Draft')`, menuContext);
assert.match(stale.items.at(-1).label, /earlier version of the plan/, 'a result an edit invalidated says so');

// --- Wiring ------------------------------------------------------------------
assert.match(source, /addEventListener\('contextmenu'[\s\S]{0,220}event\.preventDefault\(\)/, 'a canvas press opens ours instead of the browser menu');
assert.match(source, /if\(!target\)return;[\s\S]{0,120}openMenu/, 'and only on a surface we own');
assert.match(source, /pointerType==='mouse'\)return;[\s\S]{0,600}setTimeout/, 'a touch device gets press-and-hold');
assert.match(source, /event\.key==='Escape'[\s\S]{0,60}closeMenu/, 'Escape closes the menu');
assert.match(source, /const items=menuFocusTargets\([\s\S]{0,400}event\.key==='ArrowDown'\?1/, 'and the arrow keys move through it');
assert.match(source, /'ContextMenu'\|\|event\.shiftKey&&event\.key==='F10'/, 'Shift+F10 and the Menu key open it for the focused step');
assert.match(source, /if\(menu&&!event\.target\.closest\('\[data-canvas-menu\]'\)/, 'a press elsewhere closes it');
assert.ok(source.includes('menuFocusTargets(element)[0]?.focus'), 'opening moves focus into the menu');
assert.match(source, /if\(button\.closest\('\[data-canvas-menu\]'\)\)closeMenu/, 'choosing an item closes the menu first');
assert.match(css, /\.canvas-menu\{position:fixed/, 'the menu floats above the page');
assert.match(css, /\.canvas-menu-item\[disabled\]\{color:var\(--dim\)/, 'a disabled item looks disabled');
assert.match(css, /\.canvas-menu-separator\{/, 'separators are styled');
console.log('PASS: the canvas context menu answers for steps, connections and backgrounds, reuses the page hooks, and never hides a run control.');
