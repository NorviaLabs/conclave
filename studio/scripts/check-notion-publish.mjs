// Offline checks for publishing a report to Notion: the approval panel's
// preview, the "Tools used" card, the activity row and the allowed pages in
// the connection setup. Inputs are shaped exactly as the backend sends them:
// the approval's preview carries Notion blocks, and the run record carries an
// allow-listed publication summary, never the report.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {integrationManagerMarkup, pagesConfig} from '../web/integrations-ui.js';
import {toolApprovalMarkup, approvalPreviewContent} from '../web/approval-ui.js';

const url = path => new URL(path, import.meta.url).href;
const load = async (path, replacements = []) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, target] of replacements) source = source.replaceAll(`'${specifier}'`, JSON.stringify(target));
  return 'data:text/javascript,' + encodeURIComponent(source);
};
const shared = url('../shared/definition.js'), catalogue = url('../shared/catalogue.js');
const activityUrl = await load('../web/run-activity.js', [['/canvas-ui.js', url('../web/canvas-ui.js')]]);
const runCanvasUrl = await load('../web/run-canvas.js', [['/parallel-ui.js', url('../web/parallel-ui.js')], ['/definition.js', shared], ['/canvas-ui.js', url('../web/canvas-ui.js')], ['/run-activity.js', activityUrl]]);
const planUrl = await load('../web/plan.js', [['/definition.js', shared]]);
const workbenchUrl = await load('../web/workbench-ui.js', [['/plan.js', planUrl], ['/catalogue.js', catalogue], ['/definition.js', shared], ['/run-canvas.js', runCanvasUrl], ['/canvas-ui.js', url('../web/canvas-ui.js')], ['/run-activity.js', activityUrl]]);
const {runActivities} = await import(activityUrl);
const {executionDetailMarkup} = await import(workbenchUrl);

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const ctx = {escape, agentName: (d, id) => d.agents.find(a => a.id === id)?.name || id,
  outputName: (d, key) => key, capabilityName: id => id.replaceAll('_', ' ')};
const d = {workflow: [{id: 'publish', name: 'Publish to the wiki', agent: 'researcher', output: 'findings', task: 'Publish.'}],
  agents: [{id: 'researcher', name: 'Support researcher', kind: 'ai', tools: ['connection.wiki.create_page'], instructions: ''}],
  state: [{key: 'findings', label: 'Findings', type: 'records'}]};

const run = (content, marks = {}, link) => ({type: 'text', text: {content, ...(link ? {link: {url: link}} : {})},
  annotations: {bold: false, italic: false, strikethrough: false, underline: false, code: false, color: 'default', ...marks}});
const block = (type, body) => ({object: 'block', type, [type]: body});
const blocks = [
  block('heading_1', {rich_text: [run('Top themes')]}),
  block('paragraph', {rich_text: [run('Customers raised '), run('214', {bold: true}), run(' conversations <script>alert(1)</script>')]}),
  block('bulleted_list_item', {rich_text: [run('Export timeouts')], children: [block('bulleted_list_item', {rich_text: [run('Pro plan')]})]}),
  block('numbered_list_item', {rich_text: [run('First')]}),
  block('to_do', {rich_text: [run('Schedule the fix')], checked: false}),
  block('quote', {rich_text: [run('Engineering has a fix ready')]}),
  block('divider', {}),
  block('code', {rich_text: [run('SELECT 1;')], language: 'sql'}),
  block('table', {table_width: 2, has_column_header: true, has_row_header: false, children: [
    block('table_row', {cells: [[run('Queue')], [run('Count')]]}), block('table_row', {cells: [[run('EU')], [run('118')]]})]}),
  block('paragraph', {rich_text: [run('incident', {}, 'https://status.example.com/418'), run(' and '), run('bad', {}, 'javascript:alert(1)')]}),
];
const preview = {kind: 'notion_publish', operation: 'create_page',
  destination: {page_id: '1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6', name: 'Support › Weekly digests', allowed: true},
  title: 'Weekly digest', total_blocks: 23, requests: 1,
  notes: ['Images are published as links, never embedded.', 'A level-4 heading became a level-3 heading.'], blocks};
const fullArguments = {parent_page_id: '1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6', title: 'Weekly digest', markdown: '# Top themes\n\n<b>raw</b>'};
// Performance PN1: the polled list carries the preview's summary; its blocks
// and the report come once, from the approval's own address.
const {blocks: _blocks, ...summary} = preview;
const {markdown: _markdown, ...listedArguments} = fullArguments;
const item = {id: 'ap1', agent: 'writer', tool: 'connection.wiki.create_page', capabilities: ['network', 'write'],
  arguments: listedArguments, preview: summary, detail: true};
const detailOf = {id: 'ap1', arguments: fullArguments, preview};

// ---------------------------------------------------------------------------
// The approval panel
// ---------------------------------------------------------------------------
{
  const html = toolApprovalMarkup([item], {escape});
  assert.match(html, /class="tool-approval-panel"/);
  assert.match(html, /data-approval-preview="notion_publish"/);
  assert.match(html, /Support › Weekly digests/);
  assert.match(html, /allowed page/);
  assert.match(html, /1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6/);
  assert.match(html, /Weekly digest/);
  assert.match(html, /23 Notion blocks/);
  assert.doesNotMatch(html, /requests? to Notion/, 'one request is not worth mentioning');
  assert.match(html, /class="publish-notes"[\s\S]*Images are published as links/);
  for (const view of ['preview', 'markdown', 'raw']) assert.match(html, new RegExp(`data-approval-view="${view}" data-approval-id="ap1"`));
  assert.match(html, /<div class="notion-preview" data-render-key="ap1:preview"><\/div>/, 'an empty placeholder, filled once');
  const page = approvalPreviewContent(item, detailOf, 'preview', escape);
  assert.ok(page, 'the preview is drawn from the blocks');
  for (const element of ['<h1', '<strong>214</strong>', '<ul', '<ol', 'class="to-do', '<blockquote', '<hr', '<table', '<th']) assert.ok(page.includes(element), element);
  assert.match(page, /<pre[^>]*>[\s\S]*SELECT 1;/);
  assert.match(page, /sql/);
  assert.match(page, /<ul[\s\S]*Export timeouts[\s\S]*<ul[\s\S]*Pro plan/, 'nested items are nested');
  assert.match(page, /<a href="https:\/\/status\.example\.com\/418" target="_blank" rel="noopener noreferrer">incident<\/a>/);
  assert.doesNotMatch(page, /javascript:/, 'only web and mail links become links');
  // Security N2: where each link goes is shown beside it.
  assert.match(page, /incident<\/a><span class="link-host" title="https:\/\/status\.example\.com\/418">↗ status\.example\.com<\/span>/);
  assert.match(page, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html + page, /<script>/);
  assert.match(approvalPreviewContent(item, undefined, 'preview', escape), /Loading the preview/);
  assert.match(html, /data-tool-approval="ap1" data-approved="true"/);
  assert.match(html, /data-tool-approval="ap1" data-approved="false"/);
  assert.match(html, /Nothing is written to Notion until you approve/);
}
{
  assert.match(toolApprovalMarkup([item], {escape, views: {ap1: 'markdown'}}), /data-render-key="ap1:markdown"/);
  const markdown = approvalPreviewContent(item, detailOf, 'markdown', escape);
  assert.match(markdown, /# Top themes/);
  assert.match(markdown, /&lt;b&gt;raw&lt;\/b&gt;/);
  assert.doesNotMatch(markdown, /<h1/);
  const raw = approvalPreviewContent(item, detailOf, 'raw', escape);
  assert.match(raw, /&quot;parent_page_id&quot;/);
  assert.match(raw, /&quot;markdown&quot;/, 'the raw request is the whole request');
  const many = toolApprovalMarkup([{...item, preview: {...preview, requests: 3, destination: {...preview.destination, name: null, allowed: null}}}], {escape});
  assert.match(many, /3 requests to Notion/);
  assert.doesNotMatch(many, /allowed page/);
  const outside = toolApprovalMarkup([{...item, preview: {...preview, destination: {...preview.destination, allowed: false}}}], {escape});
  assert.match(outside, /not an allowed page/);
}
{
  // Any other write keeps today's view of its arguments.
  const other = toolApprovalMarkup([{id: 'ap2', agent: 'writer', tool: 'connection.team.send_message', capabilities: ['write'],
    arguments: {channel: 'C1', text: '<hi>'}}], {escape});
  assert.match(other, /<pre class="code">[\s\S]*&quot;channel&quot;/);
  assert.doesNotMatch(other, /notion-preview|data-approval-view/);
  assert.equal(toolApprovalMarkup([], {escape}), '');
}

// ---------------------------------------------------------------------------
// "Tools used" and activity
// ---------------------------------------------------------------------------
const NOTION_URL = 'https://www.notion.so/Weekly-digest-5c6d7e8f90ab4cdeb1f2a3b4c5d6e7f8';
const called = (sequence, publication, extra = {}) => ({type: 'ToolCalled', stage: 'publish', agent: 'researcher', tool: 'connection.wiki.create_page',
  connection_id: 'wiki', operation: 'create_page', ok: true, sequence, timestamp: '2026-11-14T10:02:00Z', ...(publication ? {publication} : {}), ...extra});
const detail = events => executionDetailMarkup(d, {events}, 'publish', ctx);
const card = html => html.match(/<section class="tool-publication"[\s\S]*?<\/section>/)?.[0];
{
  const created = card(detail([called(1, {status: 'created', blocks_written: 23, total_blocks: 23, requests: 1, url: NOTION_URL})]));
  assert.ok(created, 'a publish gets its own card');
  assert.match(created, /data-publication-status="created"/);
  assert.match(created, /Published to Notion/);
  assert.match(created, /23 blocks written/);
  assert.ok(created.includes(`<a class="open-in-notion" href="${NOTION_URL}" target="_blank" rel="noopener noreferrer">Open in Notion`));
  const appended = card(detail([{...called(1, {status: 'appended', blocks_written: 4, total_blocks: 4, requests: 1, url: NOTION_URL}), operation: 'append_content', tool: 'connection.wiki.append_content'}]));
  assert.match(appended, /Appended in Notion/);
  const partial = card(detail([called(1, {status: 'partial', blocks_written: 100, total_blocks: 250, requests: 2, url: NOTION_URL})]));
  assert.match(partial, /data-publication-status="partial"/);
  assert.match(partial, /Partly written/);
  assert.match(partial, /100 of 250 blocks written/);
  assert.match(partial, /nothing was retried/);
  assert.match(partial, /Open the partly written page/);
  const foreign = card(detail([called(1, {status: 'created', blocks_written: 1, total_blocks: 1, requests: 1, url: 'https://evil.example/x'})]));
  assert.doesNotMatch(foreign, /<a /, 'only a Notion page is linked');
  assert.equal(card(detail([called(1)])), undefined, 'any other call keeps today\'s entry');
}
{
  const refusal = {type: 'ToolDenied', stage: 'publish', agent: 'researcher', tool: 'connection.wiki.create_page', reason: 'refused',
    detail: 'Requested resource is outside the authorized <connection> scope', sequence: 2, connection_id: 'wiki', operation: 'create_page'};
  const html = detail([refusal]);
  assert.match(html, /Refused before approval/);
  assert.match(html, /outside the authorized &lt;connection&gt; scope/);
  const [row] = runActivities(d, {events: [refusal]}, ctx);
  assert.match(row.detail, /outside the authorized <connection> scope/);
  assert.equal(row.important, true);
}
{
  const rows = runActivities(d, {events: [called(1, {status: 'created', blocks_written: 23, total_blocks: 23, requests: 1, url: NOTION_URL}),
    called(2, {status: 'partial', blocks_written: 100, total_blocks: 250, requests: 2})]}, ctx);
  const created = rows.find(row => row.id === 1), partial = rows.find(row => row.id === 2);
  assert.match(created.detail, /23 blocks · published to Notion/);
  assert.equal(created.important, false);
  assert.match(partial.detail, /100 of 250 blocks · partly written/);
  assert.equal(partial.important, true);
  assert.equal(rows.length, 2, 'each publish keeps its own row');
}

// ---------------------------------------------------------------------------
// Allowed pages in the Notion connection setup
// ---------------------------------------------------------------------------
{
  const op = (name, description, capability) => ({name, description, capability, scopes: [], parameters: {type: 'object', properties: {}}});
  const notion = {id: 'notion', name: 'Notion', base_url: 'https://api.notion.com', auth: 'bearer', operations: [
    op('search', 'Search accessible pages and databases', 'read'), op('create_page', 'Create report page', 'write'), op('append_content', 'Append to page', 'write')]};
  const slack = {id: 'slack', name: 'Slack', base_url: 'https://slack.com', auth: 'bearer', operations: [op('send_message', 'Send message', 'write')]};
  const manager = integrationManagerMarkup([notion, slack], [], 'app', escape, true);
  const notionForm = manager.match(/data-connection-create="notion"[\s\S]*?<\/form>/)[0];
  const slackForm = manager.match(/data-connection-create="slack"[\s\S]*?<\/form>/)[0];
  assert.match(notionForm, /data-notion-pages/);
  assert.match(notionForm, /Pages this connection may publish to/);
  assert.match(notionForm, /name="page_name"/);
  assert.match(notionForm, /name="page_id"/);
  assert.match(notionForm, /data-action="add-notion-page"/);
  assert.match(notionForm, /Create report page \(Approval required\)/);
  assert.doesNotMatch(slackForm, /data-notion-pages/);
  assert.deepEqual(pagesConfig(['  Weekly digests ', '', 'Runbook'], ['1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6', '', ' https://www.notion.so/x-9f8e7d6c5b4a43219f8e7d6c5b4a4321 ']),
    [{id: '1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6', name: 'Weekly digests'}, {id: 'https://www.notion.so/x-9f8e7d6c5b4a43219f8e7d6c5b4a4321', name: 'Runbook'}],
    'blank rows are dropped; the backend normalises and validates');
}

// ---------------------------------------------------------------------------
// Wiring and styles
// ---------------------------------------------------------------------------
{
  const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
  assert.match(studio, /import \{toolApprovalMarkup,fillApprovalPreviews\} from '\/approval-ui\.js'/);
  assert.match(studio, /fillApprovalPreviews\(\$\('app'\)/, 'placeholders are filled after each render');
  assert.match(studio, /\/tool-approvals\/\$\{encodeURIComponent\(/, 'each approval\'s detail is fetched from its own address');
  assert.match(studio, /pendingToolApprovals\.map\(item=>item\.id\)/, 'a poll compares approval IDs, not the whole payload');
  const runDom = await readFile(new URL('../web/run-dom.js', import.meta.url), 'utf8');
  assert.match(runDom, /renderKey/, 'a keyed element is left alone while its key is unchanged');
  assert.match(studio, /toolApprovalMarkup\(pendingToolApprovals/);
  assert.match(studio, /data-approval-view/);
  assert.match(studio, /pagesConfig\(/);
  const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
  for (const selector of ['.notion-preview', '.publish-notes', '.tool-publication', '.notion-pages']) assert.ok(css.includes(selector), selector);
}

console.log('PASS: Notion publishing shows where and what before approval, each outcome after it, and which pages a connection may publish to.');
