// Optional real-browser check: a pending approval's preview is filled once and
// left alone by later renders, so polling never re-parses or re-diffs it.
//   STUDIO_PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/check-approval-browser.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';

assert(process.env.STUDIO_PLAYWRIGHT_MODULE, 'Set STUDIO_PLAYWRIGHT_MODULE to an external Playwright index.mjs path.');
const {chromium} = await import(pathToFileURL(process.env.STUDIO_PLAYWRIGHT_MODULE).href);
const root = new URL('../web/', import.meta.url);
const browser = await chromium.launch({headless: true});
const context = await browser.newContext();
await context.route('**/*', async route => {
  const url = new URL(route.request().url());
  if (url.host !== 'studio.local') return route.abort();
  if (url.pathname === '/') return route.fulfill({status: 200, contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><div id="app"></div>'});
  return route.fulfill({status: 200, contentType: 'text/javascript', body: await readFile(new URL('.' + url.pathname, root), 'utf8')});
});
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.goto('http://studio.local/');
const result = await page.evaluate(async () => {
  const {toolApprovalMarkup, fillApprovalPreviews} = await import('/approval-ui.js');
  const {updateRunDOM} = await import('/run-dom.js');
  const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const run = text => ({type: 'text', text: {content: text}, annotations: {}});
  const blocks = Array.from({length: 990}, (_, n) => n % 3 ? {type: 'paragraph', paragraph: {rich_text: [run(`Paragraph ${n}`)]}}
    : {type: 'heading_2', heading_2: {rich_text: [run(`Heading ${n}`)]}});
  const summary = {kind: 'notion_publish', operation: 'append_content', destination: {page_id: 'p', name: 'Runbook', allowed: true},
    title: null, total_blocks: 990, requests: 10, notes: []};
  const items = [{id: 'ap1', agent: 'writer', tool: 'connection.wiki.append_content', capabilities: ['write'], arguments: {target_page_id: 'p'}, preview: summary, detail: true}];
  const details = {ap1: {id: 'ap1', arguments: {target_page_id: 'p', markdown: '## Heading 0'}, preview: {...summary, blocks}}};
  const views = {};
  const app = document.getElementById('app');
  const draw = () => { updateRunDOM(app, `<main>${toolApprovalMarkup(items, {escape, views})}<p>poll ${performance.now()}</p></main>`); fillApprovalPreviews(app, items, {escape, details, views}); };
  draw();
  const filled = app.querySelectorAll('.notion-preview h2').length;
  const probe = app.querySelector('.notion-preview h2');
  probe.setAttribute('data-probe', 'kept');
  const times = [];
  for (let poll = 0; poll < 10; poll++) { const start = performance.now(); draw(); times.push(performance.now() - start); }
  const kept = app.querySelector('.notion-preview h2') === probe && probe.getAttribute('data-probe') === 'kept';
  views.ap1 = 'markdown';
  draw();
  const switched = app.querySelector('.notion-preview').textContent.includes('## Heading 0') && !app.querySelector('.notion-preview h2');
  return {filled, kept, switched, poll: times.reduce((a, b) => a + b) / times.length};
});
await browser.close();
assert.deepEqual(errors, []);
assert.equal(result.filled, 330, 'the preview is filled from the detail');
assert.ok(result.kept, 'later renders leave the filled preview untouched');
assert.ok(result.switched, 'choosing another view refills it');
assert.ok(result.poll < 60, `a poll's render with a 990-block preview pending takes ${result.poll.toFixed(1)} ms`);
console.log(`PASS: a pending approval's preview is filled once and left alone by polling (${result.poll.toFixed(1)} ms a render).`);
