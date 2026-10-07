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

const browser = await chromium.launch({headless: true, args: ['--no-proxy-server']});
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
  await page.locator('[data-action="back-to-plan"]').click();
  await page.locator('[data-step="4"]').click();
  await page.locator('[data-canvas-port]').first().waitFor();
  assert.equal(await page.locator('[data-plan-node]').count(), workflow.length, 'a node per step');
  assert.equal(await page.locator('[data-canvas-port]').count(), workflow.length, 'every step offers a port to connect from');
  const drawn = await connections();
  assert.equal(drawn, definition.connections.length, 'every connection is drawn');
  await shot('plan');

  // Double-clicking a step opens that step's settings, and closing them puts
  // the canvas back the way the rest of this check expects to find it.
  await page.locator(`[data-plan-node="${workflow[0]}"]`).dblclick();
  await page.locator('.workbench-inspector').waitFor();
  await page.waitForTimeout(120);
  assert.equal((await page.locator('#inspector-title').innerText()).trim(), definition.workflow[0].name, 'double-clicking a step opens that step');
  await shot('plan-editing');
  await page.locator('[data-action="close-plan-inspector"]').click();
  await page.locator('.workbench-inspector').waitFor({state: 'hidden'});

  // Drag from a port to a step that is not already connected.
  const [from, to] = [workflow[0], workflow.at(-2)];
  assert(!definition.connections.some(edge => edge.from === from && edge.to === to), 'the fixture starts without that connection');
  const release = await drag(`[data-canvas-port="${from}"]`, `[data-plan-node="${to}"]`);
  assert.equal(await page.locator('[data-canvas-preview]').count(), 1, 'a preview curve follows the pointer');
  assert.equal(await page.locator('.plan-node.connect-target').count(), 1, 'the step under the pointer is highlighted');
  await shot('plan-dragging');
  await release();
  assert.equal(await connections(), drawn + 1, 'dropping on a step connects them');
  await shot('plan-connected');

  // A connection that would make a loop is refused, with the reason on screen.
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

  // -------------------------------------------------------------------------
  // The run canvas
  // -------------------------------------------------------------------------
  await page.goto(`${base}/?application=${applicationId}&run=${runId}`);
  await page.locator('[data-run-node]').first().waitFor();
  assert.equal(await page.locator('[data-run-mode="canvas"]').getAttribute('aria-pressed'), 'true', 'the run opens on the canvas');
  assert.equal(await page.locator('[data-run-node]').count(), workflow.length, 'a node per step in the run');
  assert.equal(await page.locator('.run-node-status[data-status="needs-input"]').count(), 1, 'the human checkpoint is marked');
  assert.match(await page.locator('.run-workbench .canvas-heading span').first().innerText(), /steps complete/);
  assert.equal(await page.locator('.run-node .run-chip').count(), 3, 'tool calls are counted per step');
  assert.match(await page.locator('.run-node .run-chip').first().innerText(), /×2/);
  assert.equal(await page.locator('.run-chip.denied').count(), 1, 'a refused tool is marked');
  assert.equal(await page.locator('.run-subagent').count(), 1, 'a delegation becomes a sub-agent node');
  assert.match(await page.locator('.run-subagent').innerText(), /Check the quoted figures/);
  assert.ok(await page.locator('.run-edge .edge-label').count() > 0, 'handed-on dependencies are labelled');
  assert.match(await page.locator('.run-edge .edge-label').first().innerText(), /record|character/);
  await shot('run-canvas');

  // The work plan is still one click away, and the choice holds.
  await page.locator('[data-run-mode="list"]').click();
  await page.locator('.execution-plan').waitFor();
  await page.locator('[data-run-mode="canvas"]').click();
  await page.locator('[data-run-node]').first().waitFor();
  await page.locator('[data-run-zoom="in"]').click();
  assert.match(await page.locator('[data-run-zoom="reset"]').innerText(), /110%/);

  // Selecting a step on the canvas opens its inputs and output.
  await page.locator('[data-run-node]').first().locator('[data-run-step]').click();
  await page.locator('.execution-data').first().waitFor();

  assert.deepEqual(errors, [], 'the page reported no errors');
  console.log('PASS: the plan canvas previews and builds a connection, refuses a loop, removes a connection and lands a dropped step at the pointer; the run canvas draws statuses, tool counts, a delegation and labelled handoffs, and keeps the work plan.');
  console.log(`Screenshots: ${artifacts}`);
} catch (error) {
  await page.screenshot({path: `${artifacts}/canvas-failure.png`}).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
