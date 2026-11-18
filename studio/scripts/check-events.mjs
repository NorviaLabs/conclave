// Runs started by an event, as Studio sets them up and shows them. The mockups
// are docs/studio/mockups/events.html. Offline: no browser, backend or provider.
//
//   node studio/scripts/check-events.mjs
import assert from 'node:assert/strict';

const url = path => new URL(path, import.meta.url).href;
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const ui = await import(url('../web/event-ui.js'));
const schedule = await import(url('../web/schedule-ui.js'));

const definition = {start_mode: 'event', event: {task: 'Triage <this>'}, inputs: [
  {id: 'title', label: 'Title', type: 'text', required: true}, {id: 'labels', label: 'Labels', type: 'text', required: false}]};

// --- Set up ----------------------------------------------------------------------------
const settings = ui.eventSettingsMarkup(definition, {escape});
assert.match(settings, /<textarea id="event-task" data-path="event\.task" rows="2" maxlength="20000">Triage &lt;this&gt;<\/textarea>/);
assert.match(settings, /<input type="checkbox" data-event-override >/, 'an event does not replace the task unless allowed');
assert.match(settings, /<ul class="event-inputs" aria-label="Request fields an event fills"><li>title · required<\/li><li>labels<\/li><\/ul>/);
assert.match(ui.eventSettingsMarkup({...definition, inputs: []}, {escape}), /declares no request fields; an event only starts it/);
// Letting an event replace the task lets whoever holds the secret instruct the team: said where it is chosen.
assert.match(settings, /<span>Let an event replace the task with its own <code>task<\/code> field<span class="field-help">Whoever holds the secret can then tell the team what to do, with its owner’s accounts\. Leave it off unless the sender is as trusted as you\.<\/span><\/span>/);

// --- The address and its secret -------------------------------------------------------------
const status = {enabled: true, address: '/api/events/ev_abc', owner: 'ana@acme.com', recent: [
  {event_id: 'gh-1433', received_at: '2026-10-20T14:32:00Z', outcome: 'started', run_id: 'r2'},
  {event_id: 'gh-1432', received_at: '2026-10-20T14:31:00Z', outcome: 'duplicate', run_id: 'r1'},
  {event_id: 'sha256:' + 'f'.repeat(64), received_at: '2026-10-20T14:12:00Z', outcome: 'refused', reason: 'Missing required field “title”'},
]};
assert.match(ui.eventPanelMarkup({saved: false}, {escape}), /Save this application with the start method “When an event arrives” to turn events on/);
assert.match(ui.eventPanelMarkup({saved: true, status: {enabled: false, recent: []}}, {escape}), /data-event-create>Turn on events</);
const panel = ui.eventPanelMarkup({saved: true, status, secret: 'ces_SECRET', origin: 'https://studio.acme.com'}, {escape});
assert.match(panel, /<code>https:\/\/studio\.acme\.com\/api\/events\/ev_abc<\/code>/);
assert.match(panel, /class="event-secret" role="alert"><strong>Copy the signing secret now\. It will not be shown again\.<\/strong><code data-event-secret>ces_SECRET<\/code>/);
assert.equal((panel.match(/ces_SECRET/g) || []).length, 2, 'the secret is in its box (and its Copy button), never in the example');
assert.match(panel, /-hmac &quot;\$SECRET&quot;/, 'the example signs with $SECRET, never the secret itself');
assert.match(panel, /Runs started by events act as ana@acme\.com, who turned events on/);
assert.match(panel, /data-event-rotate>Rotate secret…<\/button>/);
assert.match(panel, /<span class="event-outcome started">Started<\/span> · <button type="button" class="text-button" data-run-open="r2">Open run<\/button>/);
assert.match(panel, /<span class="event-outcome duplicate">Duplicate<\/span> · <button type="button" class="text-button" data-run-open="r1">already started this run<\/button>/);
assert.match(panel, /<span class="event-outcome refused">Refused<\/span> · Missing required field “title”/);
assert.match(panel, /sha256:f{32}…/, 'a long identifier is shortened');
assert.doesNotMatch(ui.eventPanelMarkup({saved: true, status}, {escape}), /event-secret/, 'once stored, the secret is gone');
const confirm = ui.eventPanelMarkup({saved: true, status, confirmRotate: true}, {escape});
assert.match(confirm, /role="alertdialog" aria-labelledby="rotate-title"><strong id="rotate-title">Rotate the signing secret\?<\/strong><p>A new secret replaces the old one at once/);
assert.match(ui.eventPanelMarkup({saved: true, status, local: true}, {escape}), /reachable only from this computer/);
assert.match(ui.eventPanelMarkup({saved: true, status: {...status, enabled: false}}, {escape}), /Events are off/);

const owned = ui.eventPanelMarkup({saved: true, status: {...status, recent: [{event_id: '', received_at: '2026-10-20T15:00:00Z', outcome: 'owner',
  reason: 'Owner is now bob@acme.com (rotated the secret)'}]}}, {escape});
assert.match(owned, /<span class="event-outcome owner">Owner changed<\/span> · Owner is now bob@acme\.com \(rotated the secret\)/, 'who runs act as is never changed quietly');

// --- In run history -------------------------------------------------------------------------------
assert.equal(ui.eventTriggerText({kind: 'event', event_id: 'gh-1432'}), 'Event · gh-1432');
assert.equal(schedule.triggerText({trigger: {kind: 'event', event_id: 'gh-1432', received_at: '2026-10-20T14:30:00Z'}}), 'Event · gh-1432',
  'a run an event started is not said to have started manually');
assert.equal(schedule.triggerText({trigger: {kind: 'manual'}}), 'Started manually');

console.log('Event-started runs set up and read as designed.');
