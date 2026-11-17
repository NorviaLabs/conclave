// N8 — Studio's "When a run waits for someone" section, the message preview and
// its warnings, and how a send is reported on the run. Offline: no browser,
// backend or provider.
//
//   node studio/scripts/check-notifications.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const read = path => readFile(new URL(path, import.meta.url), 'utf8');
const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const cases = JSON.parse(await read('../../tests/data/notification_cases.json'));

// --- The browser validates the setting as the host does ---------------------------
const {validateNotifications, validateDefinition, emptyDefinition} = await import('../shared/definition.js');
for (const item of cases.valid) {
  assert.doesNotThrow(() => validateNotifications(structuredClone(item.notifications)), item.name);
  assert.doesNotThrow(() => validateDefinition({...emptyDefinition(), notifications: structuredClone(item.notifications)}), item.name);
}
for (const item of cases.invalid) {
  assert.throws(() => validateNotifications(structuredClone(item.notifications)), error => error.message.includes(item.message), `${item.name}: ${item.message}`);
  assert.throws(() => validateDefinition({...emptyDefinition(), notifications: structuredClone(item.notifications)}), error => error.message.includes(item.message), item.name);
}
assert.doesNotThrow(() => validateDefinition(emptyDefinition()), 'a definition without the setting is unchanged');
console.log(`PASS: Studio and the host agree on ${cases.valid.length + cases.invalid.length} notification cases.`);

// --- The section ---------------------------------------------------------------------
const source = (await read('../web/notify-ui.js')).replace("'/canvas-ui.js'", JSON.stringify(new URL('../web/canvas-ui.js', import.meta.url).href));
const {notifyEditorMarkup, notifyWarnings, noticeRowText, runNoticeMarkup} = await import('data:text/javascript,' + encodeURIComponent(source));
const ANA = 'ana@acme.test', BEA = 'bea@acme.test';
const digest = block => ({...emptyDefinition(), name: 'Support digest', ...(block ? {notifications: {waiting: block}} : {})});
const slack = {via: 'slack', connection: 'team_slack', channel: '#reviews'};
const mine = [
  {id: 'team_slack', integration: 'slack', name: 'Team Slack', applications: ['digest'], operations: ['send_message']},
  {id: 'ops_slack', integration: 'slack', name: 'Ops Slack', applications: [], operations: ['send_message']},
  {id: 'ana_gmail', integration: 'gmail', name: "Ana's Gmail", applications: [], operations: ['send_email']},
  {id: 'ops_hook', integration: 'webhooks', name: 'Ops incident hook', applications: ['digest'], operations: ['send']}];
const ownStatus = {configured: true, owner: ANA, via: 'slack', connection: 'team_slack', target: '#reviews', usable: true, reason: ''};
const preview = {via: 'slack', target: '#reviews', text: '⏸ *Support digest* is waiting for you at “Review the digest”. Open the run: <https://studio.acme.example/?application=digest&run=example>',
  link: 'https://studio.acme.example/?application=digest&run=example'};
const base = {applicationId: 'digest', connections: mine, status: ownStatus, preview, viewer: ANA, warnings: [], escape};

const off = notifyEditorMarkup(digest(null), {...base, status: null, preview: null});
assert.match(off, /role="group" aria-labelledby="notify-title"/);
assert.match(off, /data-notify-via="off" aria-pressed="true"/);
assert.doesNotMatch(off, /notify-connection|notify-preview/, 'off shows the choice and nothing else');

const editor = notifyEditorMarkup(digest(slack), base);
assert.match(editor, /data-notify-via="slack" aria-pressed="true"/);
for (const via of ['off', 'email', 'webhook']) assert.match(editor, new RegExp(`data-notify-via="${via}" aria-pressed="false"`));
assert.match(editor, /<label for="notify-connection">Connection<\/label><select id="notify-connection"[^>]*data-notify-connection/);
assert.match(editor, /<option value="team_slack" selected>Team Slack · team_slack — granted to this application<\/option>/);
assert.match(editor, /<option value="ops_slack" >Ops Slack · ops_slack — not granted to this application yet<\/option>|<option value="ops_slack">Ops Slack · ops_slack — not granted to this application yet<\/option>/);
assert.doesNotMatch(editor, /ana_gmail|ops_hook/, 'only connections of the chosen service are offered');
assert.match(editor, /class="grant ok"[\s\S]*Granted to this application/);
assert.match(editor, /<label for="notify-target">Channel<\/label><input id="notify-target"[^>]*data-notify-target[^>]*value="#reviews"/);
assert.match(editor, /class="notify-preview" aria-live="polite"/);
assert.match(editor, /&lt;https:\/\/studio\.acme\.example\/\?application=digest&amp;run=example&gt;|class="notify-link"/);
assert.doesNotMatch(editor, /<a /, 'the preview link is text, not a link to a made-up address');
assert.match(editor, /Sent as <strong>ana@acme\.test<\/strong>/);
// What is typed here is saved with the application, where others can read it (security review NS5).
assert.match(editor, /saved with the application/);

const notGranted = notifyEditorMarkup(digest({...slack, connection: 'ops_slack'}), {...base, status: {...ownStatus, connection: 'ops_slack', usable: false}});
assert.match(notGranted, /class="grant missing"[\s\S]*Not granted to this application yet[\s\S]*data-action="manage-integrations"/);

const mail = notifyEditorMarkup(digest({via: 'email', connection: 'ana_gmail', address: 'billing-leads@acme.example'}), {...base, preview: {via: 'email', target: 'billing-leads@acme.example', subject: 'Support digest is waiting for you', text: 'x', link: 'y'}});
assert.match(mail, /<label for="notify-target">Address<\/label><input id="notify-target"[^>]*type="email"/);
assert.match(mail, /Support digest is waiting for you/);

const hook = notifyEditorMarkup(digest({via: 'webhook', connection: 'ops_hook'}), {...base, preview: {via: 'webhook', target: 'Ops incident hook', payload: {type: 'run.waiting'}, link: 'y'}});
assert.doesNotMatch(hook, /notify-target/, 'a webhook has no target field');
assert.match(hook, /Idempotency-Key/);

const nothing = notifyEditorMarkup(digest({via: 'email', connection: 'x', address: 'a@acme.example'}), {...base, connections: mine.filter(item => item.integration !== 'gmail')});
assert.match(nothing, /You have no Email connection/);

const hostile = notifyEditorMarkup(digest({...slack, channel: '<b>x</b>'}), {...base, preview: {...preview, text: '<img src=x onerror=alert(1)>'}});
assert.doesNotMatch(hostile, /<b>x<\/b>|<img/);
console.log('PASS: the section offers only your own connections, shows the exact message, and says whose it is.');

// --- Warnings before saving -------------------------------------------------------------
const kinds = options => notifyWarnings(digest(options.block || slack), {applicationId: 'digest', connections: mine, status: ownStatus, viewer: ANA,
  runtime: {features: {notifications: true, studio_url: true}}, ...options}).map(item => item.kind);
assert.deepEqual(kinds({}), []);
assert.deepEqual(kinds({viewer: BEA, connections: []}), ['owner']);
assert.match(notifyWarnings(digest(slack), {applicationId: 'digest', connections: [], status: ownStatus, viewer: BEA, runtime: {features: {studio_url: true}}})[0].text,
  /Saving makes notifications yours[\s\S]*team_slack[\s\S]*ana@acme\.test/);
assert.deepEqual(kinds({block: {...slack, connection: 'ops_slack'}}), ['grant']);
assert.deepEqual(kinds({runtime: {features: {notifications: true, studio_url: false}}}), ['address']);
assert.deepEqual(kinds({block: null}), [], 'off has nothing to warn about');
console.log('PASS: before saving, Studio warns about someone else\'s connection, a missing grant and a missing Studio address.');

// --- On the run ---------------------------------------------------------------------------
const sent = {type: 'NotificationSent', waiting_sequence: 5, via: 'slack', target: '#reviews', connection: 'team_slack', sent_as: ANA,
  step: {id: 'review', name: 'Review the digest'}, timestamp: '2026-11-16T10:39:20Z', sequence: 6};
const failed = {...sent, type: 'NotificationFailed', reason: 'Slack rejected the operation'};
const unknown = {...sent, type: 'NotificationUncertain', reason: 'The host stopped while sending; it may not have been sent'};
assert.deepEqual(Object.keys(noticeRowText(sent)).sort(), ['detail', 'title', 'tone']);
assert.equal(noticeRowText(sent).tone, 'sent');
assert.match(noticeRowText(sent).title, /^Told #reviews/);
assert.match(noticeRowText(sent).detail, /Slack · team_slack · sent as ana@acme\.test/);
assert.equal(noticeRowText(failed).tone, 'failed');
assert.match(noticeRowText(failed).title, /^Could not tell #reviews/);
assert.match(noticeRowText(failed).detail, /Slack rejected the operation[\s\S]*nothing is sent again/);
assert.equal(noticeRowText(unknown).tone, 'unknown');
assert.match(noticeRowText(unknown).title, /#reviews may not have been told/);
assert.match(noticeRowText({...sent, step: {id: 'write', name: 'Write the digest'}, reason_kind: 'pause'}).title, /Told #reviews/);
assert.match(runNoticeMarkup([sent], {escape}), /class="run-notice sent"[\s\S]*Told #reviews at/);
assert.match(runNoticeMarkup([sent, failed], {escape}), /class="run-notice failed"[\s\S]*Could not tell #reviews/);
assert.match(runNoticeMarkup([unknown], {escape}), /class="run-notice unknown"[\s\S]*#reviews may not have been told/);
assert.equal(runNoticeMarkup([{type: 'ApplicationFinished'}], {escape}), '');
assert.doesNotMatch(runNoticeMarkup([{...sent, target: '<b>x</b>'}], {escape}), /<b>/);
// On a long live run the line is redrawn often: it reads back from the end to
// the latest send, not the whole list (performance review P3).
let reads = 0;
const long = [...Array.from({length: 10000}, (_, index) => ({type: 'ToolCalled', sequence: index})), sent, {type: 'StageStarted'}, {type: 'ToolCalled'}];
const counted = new Proxy(long, {get(target, key) { if (typeof key === 'string' && /^\d+$/.test(key)) reads++; return Reflect.get(target, key); }});
assert.match(runNoticeMarkup(counted, {escape}), /Told #reviews/);
assert.ok(reads <= 5, `reads ${reads} events to find the latest send`);

const activity = (await read('../web/run-activity.js')).replace("'/canvas-ui.js'", JSON.stringify(new URL('../web/canvas-ui.js', import.meta.url).href));
const {runActivities, activityDockMarkup} = await import('data:text/javascript,' + encodeURIComponent(activity));
const d = {workflow: [{id: 'review', name: 'Review the digest', agent: 'lead', output: 'review'}], state: [{key: 'review', label: 'Review'}]};
const ctx = {escape, agentName: (_d, id) => id, capabilityName: id => id};
const waiting = {type: 'ApplicationFinished', status: 'waiting_for_human', stage: 'review', reason: 'Host human input handler is required', sequence: 5, timestamp: '2026-11-16T10:39:12Z'};
const rows = runActivities(d, {events: [waiting, failed]}, ctx);
assert.match(rows[0].text, /^Could not tell #reviews/, 'a send is a row of its own');
// run-activity.js keeps its own copy of the wording, so it loads on its own: the two must agree.
for (const event of [sent, failed, unknown, {...sent, waiting: 'pause', step: {id: 'write', name: 'Write the digest'}}]) {
  const [row] = runActivities(d, {events: [event]}, ctx);
  assert.deepEqual([row.text, row.detail], [noticeRowText(event).title, noticeRowText(event).detail], event.type);
}
const dock = activityDockMarkup(d, {events: [waiting, failed]}, {...ctx, expanded: true});
assert.match(dock, /class="activity-latest"><span[^>]*>● Request waiting for human/, 'the latest line still says what the run waits for');
assert.match(dock, /class="[^"]*activity-notice failed/);
console.log('PASS: a send is one row in the run\'s activity and a small line on the run, amber when it did not go out.');

// --- Wiring and styles ------------------------------------------------------------------------
const studio = await read('../web/studio.js');
assert.match(studio, /from '\/notify-ui\.js'/);
assert.match(studio, /\/api\/notifications\/preview/);
assert.match(studio, /\/api\/applications\/\$\{[^}]+\}\/notifications`/);
assert.match(studio, /notifyEditorMarkup\(/);
assert.match(studio, /features\?\.notifications/);
assert.match(studio, /delete \w+\.notifications/, 'Off drops the setting from the draft');
const live = await read('../web/live.js');
assert.match(live, /runNoticeMarkup\(/);
const css = await read('../web/studio.css');
for (const rule of ['.notify-editor', '.notify-preview', '.notify-message', '.notify-warning', '.notify-owner', '.grant.ok', '.grant.missing',
  '.run-overview>span.run-notice', '.activity-notice'])
  assert.ok(css.includes(rule), `studio.css styles ${rule}`);
console.log('PASS: studio.js wires the section to the host, and studio.css carries its styles.');
