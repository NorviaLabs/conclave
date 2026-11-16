// Offline checks for how Studio shows a read that followed pages: the activity
// row, the card in the step's "Tools used" list and the "follows pages" tag in
// the connection manager. The events are shaped exactly as the backend
// publishes them: a paging summary, never the cursor or any returned item.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {integrationManagerMarkup} from '../web/integrations-ui.js';

const url = path => new URL(path, import.meta.url).href;
const load = async (path, replacements = []) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, target] of replacements) source = source.replaceAll(`'${specifier}'`, JSON.stringify(target));
  return 'data:text/javascript,' + encodeURIComponent(source);
};
const shared = url('../shared/definition.js'), catalogue = url('../shared/catalogue.js');
const activityUrl = await load('../web/run-activity.js', [['/canvas-ui.js', url('../web/canvas-ui.js')]]);
const runCanvasUrl = await load('../web/run-canvas.js', [['/definition.js', shared], ['/canvas-ui.js', url('../web/canvas-ui.js')], ['/run-activity.js', activityUrl]]);
const planUrl = await load('../web/plan.js', [['/definition.js', shared]]);
const workbenchUrl = await load('../web/workbench-ui.js', [['/plan.js', planUrl], ['/catalogue.js', catalogue], ['/definition.js', shared], ['/run-canvas.js', runCanvasUrl], ['/canvas-ui.js', url('../web/canvas-ui.js')], ['/run-activity.js', activityUrl]]);
const {runActivities, activityDockMarkup} = await import(activityUrl);
const {executionDetailMarkup} = await import(workbenchUrl);

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const agentName = (d, id) => d.agents.find(agent => agent.id === id)?.name || id;
const outputName = (d, key) => d.state.find(item => item.key === key)?.label || key;
const capabilityName = id => id.replaceAll('_', ' ');
const ctx = {escape, agentName, outputName, capabilityName};
const d = {
  workflow: [{id: 'gather', name: 'Gather evidence', agent: 'researcher', output: 'findings', task: 'Collect every ticket.'}],
  agents: [{id: 'researcher', name: 'Support researcher', kind: 'ai', tools: ['connection.mail.search_messages'], instructions: ''}],
  state: [{key: 'findings', label: 'Findings', type: 'records'}],
};
const tool = 'connection.mail.search_messages';
const summary = overrides => ({pages: 3, items: 250, page_items: [100, 100, 50], max_pages: 5, max_items: 250,
  truncated: true, stopped_because: 'max_items', repeats: 50, cursor_argument: 'page_token', seconds: 4.2, ...overrides});
const called = (sequence, pagination) => ({type: 'ToolCalled', stage: 'gather', agent: 'researcher', tool, ok: true,
  connection_id: 'mail', operation: 'search_messages', output_chars: 15221, truncated: false, sequence,
  timestamp: '2026-11-14T09:41:00Z', ...(pagination ? {pagination} : {})});
const detail = events => executionDetailMarkup(d, {events}, 'gather', ctx);
const cards = html => [...html.matchAll(/<section class="tool-paging"[\s\S]*?<\/section>/g)].map(match => match[0]);

// ---------------------------------------------------------------------------
// The activity row: one row per paginated call, saying how it went
// ---------------------------------------------------------------------------
{
  const events = [called(1, summary()), called(2, summary({stopped_because: 'exhausted', truncated: false, repeats: 0, items: 180, page_items: [100, 80], pages: 2})),
                  called(3), called(4)];
  const rows = runActivities(d, {events}, ctx);
  const paged = rows.filter(row => /pages? ·/.test(row.detail));
  assert.equal(paged.length, 2, 'a paginated call is never folded into a neighbour');
  assert.match(paged.find(row => row.id === 1).detail, /3 pages · 250 items · cut short at the item limit/);
  assert.match(paged.find(row => row.id === 2).detail, /2 pages · 180 items · all pages read/);
  assert.equal(rows.find(row => row.routine && row.count === 2)?.count, 2, 'plain calls are still grouped');
  const dock = activityDockMarkup(d, {events}, {...ctx, expanded: true});
  assert.match(dock, /3 pages · 250 items · cut short at the item limit/);
  const reasons = {max_pages: 'cut short at the page limit', output_size: 'cut short at the output limit',
    time: 'cut short at the time limit', error: 'stopped by a provider error'};
  for (const [reason, text] of Object.entries(reasons)) {
    const [row] = runActivities(d, {events: [called(9, summary({stopped_because: reason, error: reason === 'error' ? 'Provider rate limit reached; retry later' : undefined}))]}, ctx);
    assert.match(row.detail, new RegExp(text), reason);
  }
  const [error] = runActivities(d, {events: [called(9, summary({stopped_because: 'error', error: 'Provider rate limit reached; retry later'}))]}, ctx);
  assert.equal(error.important, true, 'a partial result needs attention');
}

// ---------------------------------------------------------------------------
// The card in "Tools used"
// ---------------------------------------------------------------------------
{
  const html = detail([called(1, summary())]);
  const [card] = cards(html);
  assert.ok(card, 'a paginated call gets a paging card');
  assert.match(card, /data-paging-outcome="cut"/);
  assert.match(card, /Cut short · item limit/);
  assert.match(card, /3 pages · 250 items/);
  assert.match(card, /Asked for 5 pages \/ 250 items/);
  const strip = card.match(/<ol class="page-strip"[\s\S]*?<\/ol>/)[0];
  assert.deepEqual([...strip.matchAll(/<li class="([a-z]+)"/g)].map(match => match[1]), ['read', 'read', 'cut', 'unread', 'unread'],
    'each page read, the cut page, and the pages the limit left unread');
  assert.match(card, /Stopped at the 250-item limit partway through page 3; continuing repeats 50 items/);
  assert.match(card, /The agent received a <code>page_token<\/code> to continue/);
  assert.doesNotMatch(card, /tok-|next_cursor/, 'Studio has no cursor to show');
}
{
  // A cut first page hands the agent no cursor: it starts again and skips.
  const [card] = cards(detail([called(1, summary({pages: 1, items: 30, page_items: [30], max_items: 30, repeats: 30, resumable: false}))]));
  assert.match(card, /starting again and skipping the first 30 items/);
  assert.doesNotMatch(card, /received a/);
}
{
  const [card] = cards(detail([called(1, summary({stopped_because: 'exhausted', truncated: false, repeats: 0, items: 250, page_items: [100, 100, 50]}))]));
  assert.match(card, /data-paging-outcome="complete"/);
  assert.match(card, /All pages read/);
  assert.doesNotMatch(card, /to continue/, 'nothing to continue from');
  assert.doesNotMatch(card, /class="unread"/, 'no unread pages when the service had no more');
}
{
  const labels = {max_pages: 'Cut short · page limit', output_size: 'Cut short · output limit', time: 'Cut short · time limit'};
  for (const [reason, label] of Object.entries(labels)) {
    const [card] = cards(detail([called(1, summary({stopped_because: reason, repeats: 0, page_items: [100, 100, 100], items: 300}))]));
    assert.match(card, new RegExp(label), reason);
    assert.match(card, /data-paging-outcome="cut"/, reason);
  }
  const [output] = cards(detail([called(1, summary({stopped_because: 'output_size', repeats: 12, page_items: [100, 12], items: 112, pages: 2}))]));
  assert.match(output, /output limit for one call/);
  assert.doesNotMatch(output, /100,000|100000/, 'the host owns its limits; Studio names them');
}
{
  const [card] = cards(detail([called(1, summary({stopped_because: 'error', pages: 2, items: 200, page_items: [100, 100], repeats: 0,
    error: '<script>alert(1)</script> rate limit'}))]));
  assert.match(card, /data-paging-outcome="partial"/);
  assert.match(card, /Partial · provider error/);
  assert.match(card, /Page 3 failed/);
  assert.match(card, /&lt;script&gt;alert\(1\)&lt;\/script&gt; rate limit/, 'provider error text is escaped');
  assert.doesNotMatch(card, /<script>/);
  const strip = card.match(/<ol class="page-strip"[\s\S]*?<\/ol>/)[0];
  assert.deepEqual([...strip.matchAll(/<li class="([a-z]+)"/g)].map(match => match[1]), ['read', 'read', 'error', 'unread', 'unread']);
}
{
  const html = detail([called(1)]);
  assert.equal(cards(html).length, 0, 'a single-page call keeps today\'s entry');
  const many = detail([called(1, summary({max_pages: 20, pages: 2, page_items: [100, 100], items: 200, stopped_because: 'time', repeats: 0}))]);
  const strip = cards(many)[0].match(/<ol class="page-strip"[\s\S]*?<\/ol>/)[0];
  assert.ok((strip.match(/<li class="unread"/g) || []).length <= 6, 'a long tail of unread pages is summarised');
  assert.match(strip, /\+\d+/);
}

// ---------------------------------------------------------------------------
// The connection manager says which reads follow pages
// ---------------------------------------------------------------------------
{
  const paged = {type: 'object', properties: {query: {type: 'string'}, page_token: {type: 'string'}, paginate: {type: 'boolean'},
    max_pages: {type: 'integer'}, max_items: {type: 'integer'}}};
  const services = [{id: 'gmail', name: 'Gmail', base_url: 'https://gmail.googleapis.com', auth: 'google_oauth', operations: [
    {name: 'search_messages', description: 'Search messages', capability: 'read', scopes: [], parameters: paged},
    {name: 'read_message', description: 'Read message', capability: 'read', scopes: [], parameters: {type: 'object', properties: {message_id: {type: 'string'}}}},
    {name: 'send_email', description: 'Send email', capability: 'write', scopes: [], parameters: {type: 'object', properties: {raw: {type: 'string'}}}}]}];
  const manager = integrationManagerMarkup(services, [], 'app', escape, true);
  assert.equal((manager.match(/class="follows-pages"/g) || []).length, 2, 'the read in the catalogue and in the grant list');
  assert.match(manager, /Search messages[^<]*<span class="follows-pages"/);
  assert.doesNotMatch(manager, /Read message[^<]*<span class="follows-pages"/);
  assert.doesNotMatch(manager, /Send email[^<]*<span class="follows-pages"/);
  assert.match(manager, /Changes never follow pages/);
  const plain = integrationManagerMarkup([{...services[0], operations: services[0].operations.slice(1)}], [], 'app', escape, true);
  assert.doesNotMatch(plain, /follows-pages|never follow pages/, 'no note where nothing pages');
}

// ---------------------------------------------------------------------------
// Styles use the existing tokens
// ---------------------------------------------------------------------------
{
  const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
  for (const selector of ['.tool-paging', '.page-strip', '.page-strip li.cut', '.page-strip li.error', '.page-strip li.unread', '.follows-pages'])
    assert.ok(css.includes(selector), `${selector} is styled`);
}

console.log('PASS: paginated reads show their pages, outcome and limits in activity and step details, with no cursor, and the connection manager marks which reads follow pages.');
