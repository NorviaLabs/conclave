// Optional real-browser check for the two canvases. It needs no Conclave server,
// no model provider and no network: the page, its modules and the API are served
// from this repository through request interception, so it verifies the plan
// canvas's drag, connect and drop gestures and the run canvas's rendering.
//
//   STUDIO_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/check-canvas-browser.mjs
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {templates} from '../shared/definition.js';

assert(process.env.STUDIO_PLAYWRIGHT_MODULE, 'Set STUDIO_PLAYWRIGHT_MODULE to an external Playwright index.mjs path.');
const {chromium} = await import(pathToFileURL(process.env.STUDIO_PLAYWRIGHT_MODULE).href);
const artifacts = process.env.STUDIO_BROWSER_ARTIFACTS || join(process.env.TMPDIR, 'conclave-studio-canvas');
await mkdir(artifacts, {recursive: true});

const root = new URL('..', import.meta.url).pathname.replace(/\/$/, '');
const base = 'http://studio.local';
const types = {'.html': 'text/html;charset=utf-8', '.js': 'text/javascript;charset=utf-8', '.css': 'text/css;charset=utf-8', '.json': 'application/json;charset=utf-8'};
// The build rewrites the bundled JSON imports to API paths; do the same here.
const browserImports = source => source.replace("'../../conclave/application/data/defaults.json'", "'/api/config/defaults'").replace("'../../conclave/application/data/catalogue.json'", "'/api/config/catalogue'");

// The app boots from the same bundled configuration the build serves.
const defaults = JSON.parse(await readFile(new URL('../../conclave/application/data/defaults.json', import.meta.url), 'utf8'));
const bundledCatalogue = JSON.parse(await readFile(new URL('../../conclave/application/data/catalogue.json', import.meta.url), 'utf8'));

const definition = structuredClone(templates.find(template => template.id === 'research-brief').definition);
definition.name = 'Canvas check';
const workflow = definition.workflow.map(step => step.id);
const applicationId = 'app_canvas_check', runId = 'run_canvas_check';
const record = (id, title, status) => ({id, title, body: `${title} body`, status, source_refs: ['source.md:1'], contributions: []});
const event = (sequence, body) => ({sequence, event: body});

// A run shaped exactly like a durable one: tool calls, a refused tool, a
// delegation, handoffs and a human checkpoint.
const run = {
  id: runId, status: 'waiting_for_human', created_at: '2026-10-06T15:10:48.059348+00:00',
  request: {version: 1, application_id: applicationId, revision: 3, task: 'Build the brief from the supplied sources.', inputs: {sources: 'Q3 field notes'}, definition, resources: {source: 'default'}},
  metrics: {tokens: 4820, wall_clock_seconds: 96},
  reviews: [{id: 'review_1', status: 'pending', stage: 'approval', request: {state: {}}}],
  events: [
    event(1, {type: 'ApplicationStarted', name: definition.name}),
    event(2, {type: 'StageStarted', stage: workflow[0], agent: definition.workflow[0].agent}),
    event(3, {type: 'ToolCalled', stage: workflow[0], agent: definition.workflow[0].agent, tool: 'read_file', ok: true, output_chars: 1840}),
    event(4, {type: 'ToolCalled', stage: workflow[0], agent: definition.workflow[0].agent, tool: 'read_file', ok: true, output_chars: 980}),
    event(5, {type: 'ToolDenied', stage: workflow[0], agent: definition.workflow[0].agent, tool: 'web_search', ok: false}),
    event(6, {type: 'Delegation', stage: workflow[0], agent: definition.workflow[2].agent, delegated_by: definition.workflow[0].agent, depth: 2, task: 'Check the quoted figures against the notes.', status: 'completed'}),
    event(7, {type: 'ApplicationStateChanged', stage: workflow[0], patch: [record('f1', 'Margins fell', 'supported')], state: {}, version: 1}),
    ...workflow.slice(1).map((stage, index) => event(8 + index, {type: 'ApplicationStateChanged', stage, patch: [{id: `p${index}`, title: `${stage} result`, status: 'supported'}], state: {}, version: index + 2})),
    event(8 + workflow.length, {type: 'HumanInputRequested', stage: workflow.at(-1), agent: definition.workflow.at(-1).agent, review_id: 'review_1'}),
  ],
};

const api = path => {
  if (path === '/api/applications') return {applications: [{id: applicationId, name: definition.name, definition, revision: 3, created_at: '2026-10-06T15:10:48.059348+00:00', updated_at: '2026-10-06T15:10:48.059348+00:00'}]};
  if (path.startsWith('/api/applications/')) return {id: applicationId, definition, revision: 3};
  if (path === '/api/members') return {members: []};
  // No provider: the canvases must render from the record alone.
  if (path === '/api/runtime') return {available: false, local: true, profile: 'local', service: true, provider_source: 'detected', reason: 'No model backend is configured for this host.', tools: [], tool_descriptors: [], connectors: [], resource_tools: [], resources: [], tenant: 'local'};
  if (path === '/api/catalogue') return {catalogue: {version: 1, roles: [], starters: [], outputs: []}};
  if (path === '/api/config/defaults') return defaults;
  if (path === '/api/config/catalogue') return bundledCatalogue;
  if (path.startsWith('/api/runs?')) return {runs: [{id: runId, status: run.status, created_at: run.created_at, revision: 3}]};
  if (path.startsWith('/api/runs/')) return run;
  if (path === '/api/settings/sources') return {sources: {}};
  return {};
};

const browser = await chromium.launch({channel: process.env.STUDIO_BROWSER_CHANNEL||undefined,headless: true, args: ['--no-proxy-server']});
const context = await browser.newContext({viewport: {width: 1500, height: 1000}});
await context.route('**/*', async route => {
  const url = new URL(route.request().url());
  if (url.host !== 'studio.local') return route.abort();
  if (url.pathname.startsWith('/api/')) return route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(api(url.pathname))});
  const file = url.pathname === '/' ? `${root}/web/index.html` : url.pathname === '/definition.js' ? `${root}/shared/definition.js` : url.pathname === '/catalogue.js' ? `${root}/shared/catalogue.js` : `${root}/web${url.pathname}`;
  const source = await readFile(file, 'utf8');
  return route.fulfill({status: 200, contentType: types[url.pathname.substring(url.pathname.lastIndexOf('.'))] || 'text/plain', body: url.pathname === '/definition.js' || url.pathname === '/catalogue.js' ? browserImports(source) : source});
});

const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
page.setDefaultTimeout(15000);
const shot = name => page.screenshot({path: `${artifacts}/canvas-${name}.png`});
const connections = () => page.locator('[data-canvas-edge]').count();
const centre = async selector => { const box = await page.locator(selector).first().boundingBox(); return {x: box.x + box.width / 2, y: box.y + box.height / 2}; };
const drag = async (from, to) => {
  const start = await centre(from), end = await centre(to);
  await page.mouse.move(start.x, start.y); await page.mouse.down();
  await page.mouse.move(end.x, end.y, {steps: 12});
  return async () => { await page.mouse.up(); };
};

try {
  // -------------------------------------------------------------------------
  // The plan canvas
  // -------------------------------------------------------------------------
  await page.goto(`${base}/?application=${applicationId}`);
  await page.locator('.shell').waitFor();
  if(await page.locator('[data-action="back-to-plan"]').count())await page.locator('[data-action="back-to-plan"]').click();
  await page.locator('[data-canvas-port]').first().waitFor();
  assert.equal(await page.locator('[data-plan-node]').count(), workflow.length, 'a node per step');
  assert.equal(await page.locator('[data-canvas-port]').count(), workflow.length, 'every step offers a port to connect from');
  const drawn = await connections();
  assert.equal(drawn, definition.connections.length, 'every connection is drawn');
  await shot('plan');

  // Double-clicking a step opens that step's settings, and closing them puts
  // the canvas back the way the rest of this check expects to find it.
  await page.locator(`[data-plan-node="${workflow[0]}"]`).click();
  await page.locator('.workbench-inspector').waitFor();
  await page.waitForTimeout(120);
  assert.equal((await page.locator('#inspector-title').innerText()).trim(), definition.workflow[0].name, 'double-clicking a step opens that step');
  await shot('plan-editing');
  await page.locator('[data-action="close-plan-inspector"]').click();
  await page.locator('.workbench-inspector').waitFor({state: 'hidden'});

  // Drag from a port to a step that is not already connected.
  const [from, to] = [workflow[0], workflow.at(-2)];
  assert(!definition.connections.some(edge => edge.from === from && edge.to === to), 'the fixture starts without that connection');
  await page.locator(`[data-plan-node="${from}"]`).hover();
  const release = await drag(`[data-canvas-port="${from}"]`, `[data-plan-node="${to}"]`);
  assert.equal(await page.locator('[data-canvas-preview]').count(), 1, 'a preview curve follows the pointer');
  assert.equal(await page.locator('.plan-node.connect-target').count(), 1, 'the step under the pointer is highlighted');
  await shot('plan-dragging');
  await release();
  assert.equal(await connections(), drawn + 1, 'dropping on a step connects them');
  await shot('plan-connected');

  // A connection that would make a loop is refused, with the reason on screen.
  await page.locator(`[data-plan-node="${workflow.at(-1)}"]`).hover();
  const loopRelease = await drag(`[data-canvas-port="${workflow.at(-1)}"]`, `[data-plan-node="${from}"]`);
  await loopRelease();
  assert.equal(await connections(), drawn + 1, 'a refused connection is not drawn');
  await page.locator('#notice').filter({hasText: 'loop'}).waitFor();

  // Select the connection and remove it with its handle.
  const curve = await page.locator(`[data-canvas-edge="${from}|${to}"] .edge-line`).evaluate(path => {
    const point = path.getPointAtLength(path.getTotalLength() / 2), box = path.ownerSVGElement.getBoundingClientRect();
    return {x: box.left + point.x, y: box.top + point.y};
  });
  await page.mouse.click(curve.x, curve.y);
  await page.locator('[data-canvas-disconnect]').waitFor();
  await shot('plan-selected');
  await page.locator('[data-canvas-disconnect]').click();
  await page.locator('[data-canvas-disconnect]').waitFor({state: 'detached'});
  assert.equal(await connections(), drawn, 'the handle removes the connection');

  // A palette step dropped on the canvas lands where it was dropped.
  await page.locator('[data-action="toggle-plan-resources"]').click();
  const flow = await page.locator('[data-canvas-drop]').boundingBox();
  const target = {x: flow.width - 240, y: flow.height - 140};
  await page.locator('[data-plan-template="ai"]').first().dragTo(page.locator('[data-canvas-drop]'), {targetPosition: target});
  await page.waitForTimeout(200);
  assert.equal(await page.locator('[data-plan-node]').count(), workflow.length + 1, 'dropping a step on the canvas adds it');
  const placed = await page.locator('[data-plan-node]').last().boundingBox();
  const zoom = Number(await page.locator('[data-canvas-drop]').evaluate(element => element.style.zoom || 1));
  assert.ok(Math.abs((placed.y - flow.y) / zoom - target.y / zoom) < 150, 'the step lands at the pointer rather than at the end of the list');
  await shot('plan-dropped');

  // Ordinary editing keeps both the input and canvas mounted.
  await page.locator('[data-action="close-plan-resources"]').click().catch(()=>{});
  await page.locator('[data-plan-node]').first().locator('.step-summary').click();
  await page.evaluate(()=>{window.planSurface=document.querySelector('[data-canvas-drop]');window.planInput=document.querySelector('.workbench-inspector input[data-path]');});
  const nameField=page.locator('.workbench-inspector input[data-path]').first();
  await nameField.fill('Edited research step');
  assert.ok(await page.evaluate(()=>window.planInput===document.activeElement&&window.planSurface===document.querySelector('[data-canvas-drop]')),'editing retains focus and surface');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.workbench-inspector').isVisible(),false);
  await page.locator('[data-action="suggest-plan-change"]').click();
  await page.locator('#plan-command').fill('Add a human checkpoint');
  await page.locator('[data-action="propose-plan"]').click();
  await page.locator('[data-action="apply-proposal"]').waitFor();
  const countBefore=await page.locator('[data-plan-node]').count();
  await page.locator('[data-action="discard-proposal"]').click();
  assert.equal(await page.locator('[data-plan-node]').count(),countBefore,'discard does not change graph');
  await page.locator('[data-action="propose-plan"]').click();
  await page.locator('[data-action="apply-proposal"]').click();
  assert.equal(await page.locator('[data-plan-node]').count(),countBefore+1,'apply explicitly changes draft');
  for(const width of [1500,1024,390]){
    await page.setViewportSize({width,height:1000});
    await page.locator('[data-action="close-plan-assistant"]').click().catch(()=>{});
    await page.locator('[data-canvas-zoom="fit"]').click();
    await page.locator('[data-plan-node]').first().locator('.step-summary').click();
    const box=await page.locator('.workbench-inspector').boundingBox();
    assert.ok(box.width>300&&box.x>=0&&box.x+box.width<=width,'plan editor readable and contained');
    await shot(`plan-editor-${width}`);await page.keyboard.press('Escape');
  }
  await page.setViewportSize({width:1500,height:1000});

  // -------------------------------------------------------------------------
  // The run canvas
  // -------------------------------------------------------------------------
  await page.goto(`${base}/?application=${applicationId}&run=${runId}`);
  await page.locator('[data-run-node]').first().waitFor();
  const bounds=()=>page.locator('[data-canvas-mode="run"]').evaluate(area=>({width:area.clientWidth,top:area.getBoundingClientRect().top,bottom:area.getBoundingClientRect().bottom,height:innerHeight,dock:area.closest('.run-workbench').querySelector('.activity-dock').offsetHeight}));
  const runBounds=await bounds();
  assert.ok(runBounds.width>1200,'canvas dominates desktop');
  assert.ok(Math.abs(runBounds.bottom+runBounds.dock-(runBounds.height-18))<3,'dock and canvas fill remaining height');
  assert.equal(await page.locator('.run-inspector-drawer').count(),0,'no inspector on entry');
  assert.equal(await page.locator('.activity-panel').count(),0,'activity starts collapsed');
  assert.equal(await page.locator('[data-run-node]').count(),workflow.length);
  assert.equal(await page.locator('.run-node-status[data-status="needs-input"]').count(),1);
  assert.equal(await page.locator('.run-subagent').count(),1);
  assert.ok(await page.locator('.run-edge .edge-label').count()>0);
  await shot('run-canvas');
  await page.locator('[data-action="toggle-activity"]').click();
  assert.ok(await page.locator('.activity-panel').isVisible());
  await page.locator('.activity-panel [data-run-step]').first().click();
  assert.ok(await page.locator('.run-inspector-drawer').isVisible());
  assert.equal(await page.locator('[data-action="follow-active"]').getAttribute('aria-pressed'),'false');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('.run-inspector-drawer').count(),0);
  await page.locator('[data-run-zoom="reset"]').click();
  await page.locator('[data-run-zoom="in"]').click();
  assert.match(await page.locator('[data-run-zoom="reset"]').innerText(),/110%/);
  await page.locator('[data-run-zoom="fit"]').click();
  await page.locator('[data-run-node]').first().locator('[data-run-step]').click();
  const selected=await page.locator('.run-node.selected').getAttribute('data-run-node');
  await page.locator('.activity-panel').evaluate(panel=>panel.scrollTop=50);
  await page.evaluate(()=>{window.keptCanvas=document.querySelector('[data-run-canvas]');window.keptInspector=document.querySelector('.run-inspector-drawer');});
  run.events.push(event(100,{type:'ToolCalled',stage:workflow[0],agent:definition.workflow[0].agent,tool:'read_file',ok:true,observation:'Recorded source result'}));
  await page.waitForTimeout(1500);
  assert.ok(await page.evaluate(()=>window.keptCanvas===document.querySelector('[data-run-canvas]')&&window.keptInspector===document.querySelector('.run-inspector-drawer')),'live updates retain DOM identity');
  assert.equal(await page.locator('.run-node.selected').getAttribute('data-run-node'),selected);
  assert.equal(await page.locator('.activity-panel').evaluate(panel=>panel.scrollTop),50);
  await shot('run-inspecting');
  await page.keyboard.press('Escape');
  await page.locator('[data-action="toggle-activity"]').click();
  await page.locator('[data-action="follow-active"]').click();
  assert.equal(await page.locator('[data-action="follow-active"]').getAttribute('aria-pressed'),'true');
  await page.locator('[data-canvas-mode="run"]').focus();await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('[data-action="follow-active"]').getAttribute('aria-pressed'),'false');
  for(const width of [1024,390]){
    await page.setViewportSize({width,height:844});
    await page.locator('[data-run-zoom="fit"]').click();
    await page.locator('[data-run-node]').first().locator('[data-run-step]').click();
    const drawer=await page.locator('.run-inspector-drawer').boundingBox();
    assert.ok(drawer.width>300&&drawer.x>=0&&drawer.x+drawer.width<=width,'inspector remains readable and contained');
    await shot(`run-${width}`);await page.keyboard.press('Escape');
  }
  run.status='failed';run.events.push(event(101,{type:'ApplicationFinished',stage:workflow[0],status:'failed',reason:'Recorded failure'}));
  await page.waitForTimeout(1500);assert.match(await page.locator('.run-overview').innerText(),/Request failed/);
  await shot('run-failed-mobile');
  assert.deepEqual(errors, [], 'the page reported no errors');
  console.log('PASS: plan gestures; canvas-first run, delegation, handoffs, on-demand inspector, activity dock, stable live DOM/scroll/selection, follow suspension, tablet/mobile and failure status.');
  console.log(`Screenshots: ${artifacts}`);
} catch (error) {
  await page.screenshot({path: `${artifacts}/canvas-failure.png`}).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
