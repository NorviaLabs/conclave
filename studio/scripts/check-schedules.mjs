// C8 — Studio shows when a scheduled application runs next, lets a person
// edit, pause, resume or turn on its schedule, and validates a schedule the
// same way the host does. Offline: no browser, backend or provider.
//
//   node studio/scripts/check-schedules.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const cases = JSON.parse(await readFile(new URL('../../tests/data/schedule_cases.json', import.meta.url), 'utf8'));

// --- The browser validates a schedule as the host does ---------------------------
const {validateSchedule, validateDefinition, emptyDefinition} = await import('../shared/definition.js');
const application = {...emptyDefinition(), inputs: [{id: 'team', label: 'Team', type: 'text', required: true}]};
const block = change => {
  const value = structuredClone(cases.base);
  for (const [key, item] of Object.entries(change)) { if (item === null) delete value[key]; else value[key] = item; }
  return value;
};
for (const item of cases.valid) {
  assert.doesNotThrow(() => validateSchedule(block(item.change), application), item.name);
  assert.doesNotThrow(() => validateDefinition({...structuredClone(application), start_mode: 'schedule', schedule: block(item.change)}), item.name);
}
for (const item of cases.invalid) {
  assert.throws(() => validateSchedule(block(item.change), application), error => error.message.includes(item.message), `${item.name}: ${item.message}`);
}
assert.throws(() => validateDefinition({...structuredClone(application), start_mode: 'manual', schedule: block({})}), /start method/);
console.log(`PASS: Studio and the host agree on ${cases.valid.length + cases.invalid.length} schedule cases.`);

// --- The editor ----------------------------------------------------------------
const {scheduleEditorMarkup, scheduleWarnings, scheduleLineMarkup, scheduleActionMarkup, scheduleIncompleteMarkup, triggerText, ownerChangeNotice, statusRefreshDelay, historyRows} = await import('../web/schedule-ui.js');
const weekly = {...structuredClone(application), start_mode: 'schedule', schedule: block({})};
const preview = {times: [{at: '2026-11-18T09:00:00+00:00', local: 'Wed 18 Nov, 09:00', time_zone: 'Europe/London'},
                         {at: '2026-11-18T17:30:00+00:00', local: 'Wed 18 Nov, 17:30', time_zone: 'Europe/London'},
                         {at: '2026-11-20T09:00:00+00:00', local: 'Fri 20 Nov, 09:00', time_zone: 'Europe/London'}]};
const editor = scheduleEditorMarkup(weekly, {escape, preview, zones: ['Europe/London', 'Europe/Berlin'], sources: [{id: 'support-exports', name: 'Support exports'}]});
assert.match(editor, /role="group" aria-labelledby="schedule-title"/);
assert.match(editor, /data-schedule-kind="weekly" aria-pressed="true"/);
assert.match(editor, /data-schedule-kind="daily" aria-pressed="false"/);
assert.match(editor, /aria-label="Days"/);
assert.match(editor, /data-schedule-day="mon" aria-pressed="true"/);
assert.match(editor, /data-schedule-day="tue" aria-pressed="false"/);
assert.match(editor, /data-schedule-time="0" value="09:00" aria-label="Time 1"/);
assert.match(editor, /data-schedule-remove-time="1"[^>]*aria-label="Remove 17:30"/);
assert.match(editor, /data-schedule-add-time/);
assert.match(editor, /data-schedule-zone[^>]*>[\s\S]*<option value="Europe\/London" selected>/);
assert.match(editor, /data-schedule-catch-up="skip" checked/);
assert.match(editor, /data-schedule-catch-up="latest"(?! checked)/);
assert.match(editor, /data-schedule-task[^>]*>Summarise yesterday&#39;s support tickets<|data-schedule-task[^>]*>Summarise yesterday's support tickets</);
assert.match(editor, /Team · required/);
assert.match(editor, /data-schedule-input="team" value="billing"/);
assert.match(editor, /data-schedule-source/);
assert.match(editor, /Do not put passwords or access tokens here/);
assert.match(editor, /Next three runs/);
assert.match(editor, /aria-live="polite"/);
assert.match(editor, /Wed 18 Nov, 09:00[\s\S]*Europe\/London/);
assert.doesNotMatch(editor, /data-schedule-every/);

const interval = {...weekly, schedule: block({kind: 'interval', times: null, days: null, every_minutes: 120, starts_at: '2026-11-17T09:00'})};
const intervalEditor = scheduleEditorMarkup(interval, {escape, zones: ['Europe/London']});
assert.match(intervalEditor, /data-schedule-every[^>]*value="2"/);
assert.match(intervalEditor, /data-schedule-unit[\s\S]*<option value="hours" selected>/);
assert.match(intervalEditor, /data-schedule-starts[^>]*value="2026-11-17T09:00"/);
assert.doesNotMatch(intervalEditor, /data-schedule-day=/);
const invalid = scheduleEditorMarkup(interval, {escape, previewError: 'Run at most every 5 minutes.'});
assert.match(invalid, /role="alert"[^>]*>Run at most every 5 minutes\./);
assert.doesNotMatch(invalid, /Next three runs/);
const hostile = scheduleEditorMarkup({...weekly, schedule: block({request: {task: '<script>x</script>', inputs: {team: '<b>'}}})}, {escape});
assert.doesNotMatch(hostile, /<script>|<b>/);
// Numbers are written as numbers, whatever a definition carries (security review S6).
const strangeEvery = scheduleEditorMarkup({...weekly, schedule: block({kind: 'interval', times: null, days: null, every_minutes: '"><img src=x onerror=alert(1)>'})}, {escape});
assert.doesNotMatch(strangeEvery, /<img/);

// --- Warnings before saving ----------------------------------------------------
const runtime = {tool_descriptors: [{id: 'push_branch', requires_approval: true}, {id: 'read_file', requires_approval: false}],
                 integrations: [{id: 'github', operations: [{name: 'create_pull_request', capability: 'write'}, {name: 'read_issue', capability: 'read'}]}]};
const writer = {...weekly, agents: [{id: 'builder', name: 'Builder', instructions: 'x', tools: ['push_branch', 'read_file', 'connection.forge.create_pull_request', 'connection.forge.read_issue']}]};
const warnings = scheduleWarnings(writer, runtime);
assert.deepEqual(warnings.map(item => item.kind), ['writes', 'limits']);
assert.deepEqual(warnings[0].tools, ['push_branch', 'connection.forge.create_pull_request']);
assert.match(warnings[0].text, /Writes will be refused/);
assert.match(warnings[1].text, /no spending or time limits/);
assert.deepEqual(scheduleWarnings({...writer, autonomous_execution: true, run_budget: {...writer.run_budget, tool_calls: 50}}, runtime), []);
assert.match(scheduleEditorMarkup(writer, {escape, warnings}), /class="schedule-warning"[\s\S]*Builder’s <em>push branch<\/em>/);
console.log('PASS: the editor, its preview and errors, and the warnings shown before saving.');

// --- The header line -----------------------------------------------------------
const base = {application_id: 'digest', active: true, paused: false, waiting: false, owner: 'Ana Ruiz', time_zone: 'Europe/London',
  next_at: '2026-11-18T09:00:00+00:00', next_local: 'Wed 18 Nov, 09:00', last: null, recent: []};
const next = scheduleLineMarkup(base, {escape});
assert.match(next, /role="status"/);
assert.match(next, /Scheduled/);
assert.match(next, /Next run <time datetime="2026-11-18T09:00:00\+00:00"[^>]*>Wed 18 Nov, 09:00<\/time> Europe\/London/);
assert.match(next, /data-schedule-action="pause"[^>]*>[\s\S]*Pause schedule/);
assert.doesNotMatch(next, /schedule-note/);
const paused = scheduleLineMarkup({...base, paused: true, next_at: null, next_local: null, paused_by: 'Ana Ruiz'}, {escape});
assert.match(paused, /Schedule paused/);
assert.match(paused, /data-schedule-action="resume"/);
assert.doesNotMatch(paused, /Next run/);
const waiting = scheduleLineMarkup({...base, owner: null, waiting: true, next_at: null, next_local: null}, {escape});
assert.match(waiting, /Schedule waiting/);
assert.match(waiting, /data-schedule-action="turn_on"[^>]*>[\s\S]*Turn on schedule/);
const skipped = scheduleLineMarkup({...base, last: {outcome: 'skipped', scheduled_local: 'Mon 16 Nov, 17:30', run_id: 'r1',
  detail: {reason: 'previous_run_active', run_id: 'r1'}}}, {escape});
assert.match(skipped, /Last time skipped: the previous run was still going at Mon 16 Nov, 17:30/);
assert.match(skipped, /data-run-open="r1"/);
const missed = scheduleLineMarkup({...base, last: {outcome: 'missed', scheduled_local: 'Fri 13 Nov, 17:30',
  detail: {count: 3, first_local: 'Fri 13 Nov, 17:30', last_local: 'Mon 16 Nov, 17:30'}}}, {escape});
assert.match(missed, /class="schedule-note missed"/);
assert.match(missed, /Missed 3 times while the host was off \(Fri 13 Nov, 17:30 – Mon 16 Nov, 17:30\)/);
const failed = scheduleLineMarkup({...base, last: {outcome: 'failed', scheduled_local: 'Mon 16 Nov, 17:30',
  detail: {reason: 'Enter <Team>'}}}, {escape});
assert.match(failed, /class="schedule-note failed"/);
assert.match(failed, /Enter &lt;Team&gt;/);
assert.match(failed, /data-action="edit-schedule"/);
assert.equal(scheduleLineMarkup({...base, active: false}, {escape}), '');
assert.equal(scheduleLineMarkup(null, {escape}), '');
// The Pause / Resume / Turn on button sits with the header's other actions.
assert.doesNotMatch(scheduleLineMarkup(base, {escape, actions: false}), /data-schedule-action/);
assert.match(scheduleActionMarkup(base), /data-schedule-action="pause"/);
assert.match(scheduleActionMarkup({...base, paused: true}), /data-schedule-action="resume"/);
assert.match(scheduleActionMarkup({...base, owner: null, waiting: true}), /data-schedule-action="turn_on"/);
assert.equal(scheduleActionMarkup({...base, active: false}), '');
assert.match(scheduleLineMarkup(base, {escape, now: Date.parse('2026-11-17T18:00:00Z')}), /· in 15 hours/);
// "On a schedule" saved without saying when: it does not run, and says so.
assert.match(scheduleIncompleteMarkup({start_mode: 'schedule'}), /Schedule incomplete[\s\S]*data-action="edit-schedule"/);
assert.equal(scheduleIncompleteMarkup({start_mode: 'schedule', schedule: {}}), '');
assert.equal(scheduleIncompleteMarkup({start_mode: 'manual'}), '');
// A run in history says how it started, in the schedule's own zone.
assert.equal(triggerText({trigger: {kind: 'schedule', scheduled_for: '2026-11-16T09:00:00+00:00'}}, 'America/New_York'), 'Scheduled · Mon 16 Nov, 04:00');
assert.equal(triggerText({trigger: {kind: 'manual'}}, 'Europe/London'), 'Started manually');
// Whose schedule it is, and when that changes hands (security review S1, S6).
assert.match(scheduleLineMarkup(base, {escape}), /runs as Ana Ruiz/);
assert.doesNotMatch(scheduleLineMarkup({...base, owner: '<b>Ana</b>'}, {escape}), /<b>/);
const strangeCount = scheduleLineMarkup({...base, last: {outcome: 'missed', scheduled_local: 'x', detail: {count: '<img src=x onerror=alert(2)>'}}}, {escape});
assert.doesNotMatch(strangeCount, /<img/);
assert.equal(ownerChangeNotice({...base, owner: 'Ana Ruiz'}, {...base, owner: 'Bea Lund'}), 'This schedule now runs as Bea Lund.');
assert.equal(ownerChangeNotice({...base, owner: 'Ana Ruiz'}, {...base, owner: 'Ana Ruiz'}), '');
assert.equal(ownerChangeNotice(null, {...base, owner: 'Bea Lund'}), '');
// The status is read again once the next run has passed, not on a timer loop (performance review P5).
const at = Date.parse(base.next_at);
assert.equal(statusRefreshDelay(base, at - 60000), 65000, 'a few seconds after the next run');
assert.equal(statusRefreshDelay(base, at + 60000), 5000, 'soon, when it has already passed');
assert.equal(statusRefreshDelay({...base, paused: true}, at), null);
assert.equal(statusRefreshDelay({...base, waiting: true}, at), null);
assert.equal(statusRefreshDelay({...base, active: false}, at), null);
assert.equal(statusRefreshDelay(null, at), null);
console.log('PASS: the header says when it runs next, and why the last time did not run.');

// --- Run history -----------------------------------------------------------------
const rows = historyRows(
  [{id: 'r2', status: 'completed', created_at: '2026-11-16T09:00:04+00:00', trigger: {kind: 'schedule', scheduled_for: '2026-11-16T09:00:00+00:00'}},
   {id: 'r1', status: 'completed', created_at: '2026-11-12T14:14:40+00:00', trigger: {kind: 'manual'}}],
  [{scheduled_for: '2026-11-16T17:30:00+00:00', outcome: 'skipped', detail: {reason: 'previous_run_active'}},
   {scheduled_for: '2026-11-16T09:00:00+00:00', outcome: 'started', run_id: 'r2'},
   {scheduled_for: '2026-11-13T17:30:00+00:00', outcome: 'missed', detail: {count: 1}}]);
assert.deepEqual(rows.map(row => row.kind === 'run' ? row.id : row.outcome), ['skipped', 'r2', 'missed', 'r1'],
  'newest first, and a started time is shown as its run, not twice');
console.log('PASS: run history merges the times that did not run, newest first.');

// --- studio.js wiring --------------------------------------------------------------
const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
assert.match(studio, /from '\/schedule-ui\.js'/);
assert.match(studio, /\/api\/applications\/\$\{[^}]+\}\/schedule`/);
assert.match(studio, /\/api\/schedules\/preview/);
assert.match(studio, /scheduleLineMarkup\(/);
assert.match(studio, /scheduleEditorMarkup\(/);
assert.match(studio, /scheduleAction|data-schedule-action/);
assert.match(studio, /delete \w+\.schedule/, 'switching to a manual start drops the schedule from the draft');
assert.match(studio, /ownerChangeNotice\(/, 'a save that hands the schedule to someone says so');
assert.match(studio, /setTimeout\([^;]*statusRefreshDelay\(|statusRefreshDelay\([^;]*\)[\s\S]{0,200}setTimeout\(/, 'the status is read again once the next run has passed');
assert.match(studio, /visibilitychange/, 'and again when the page is looked at again');
const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
for (const rule of ['.schedule-editor', '.schedule-line', '.schedule-chip', '.schedule-note', '.schedule-warning', '.schedule-preview', '.segmented', '.day-toggles', '.run-trigger'])
  assert.ok(css.includes(rule), `studio.css styles ${rule}`);
console.log('PASS: studio.js wires the schedule to the host, and studio.css carries its styles.');
