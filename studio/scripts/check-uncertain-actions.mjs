// T12 — Studio shows uncertain external actions on the run, in a floating
// panel, with the person's choice: mark done or send again, undoable until
// Continue. Offline: no browser, backend or provider.
//
//   node studio/scripts/check-uncertain-actions.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const url = path => new URL(path, import.meta.url).href;
const load = async (path, replacements = []) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, target] of replacements) source = source.replaceAll(`'${specifier}'`, JSON.stringify(target));
  return 'data:text/javascript,' + encodeURIComponent(source);
};
const escape = value => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');

const {actionLabel, actionKind, continueGate, actionPanelMarkup, actionPillMarkup, runAttention, historyBadgeMarkup, uncertainStages} =
  await import(await load('../web/run-actions.js'));

// --- The vocabulary -----------------------------------------------------------
const decide = {id: 'a2', tool: 'connection.team_slack.send_message', label: 'Slack · Send message', stage: 'announce', agent: 'announcer',
  state: 'uncertain', started_at: '2026-11-16T10:41:00Z', reason: 'The host stopped while this was being sent',
  check: {available: false, unavailable_reason: 'The team_slack connection is not granted read conversation', description: '', result: null, summary: null},
  arguments: {channel: 'C04ENGRELEASES', text: 'Heads up <b>now</b>'}, undoable: false, final: false};
const willCheck = {id: 'a1', tool: 'connection.forge.create_pull_request', label: 'GitHub · Create pull request', stage: 'build', agent: 'builder',
  state: 'uncertain', started_at: '2026-11-16T10:40:00Z', reason: 'The host stopped while this was being sent',
  check: {available: true, description: 'Will look for a pull request from fix/x into main', result: null, summary: null},
  arguments: {head: 'fix/x', base: 'main'}, undoable: false, final: false};
const doneByYou = {...decide, state: 'done', settled_by: 'Ana Ruiz', settled_at: '2026-11-16T10:58:00Z', note: 'Posted <at> 10:41', undoable: true, arguments: undefined};
const resendByYou = {...decide, state: 'resend', settled_by: 'Ana Ruiz', settled_at: '2026-11-16T10:58:00Z', undoable: true};
const inconclusive = {...willCheck, check: {...willCheck.check, result: 'inconclusive', summary: 'GitHub did not respond to the check', checked_at: '2026-11-16T10:59:00Z'}};
const doneByCheck = {...willCheck, state: 'done', settled_by: 'check', settled_kind: 'check', undoable: false, check: {...willCheck.check, result: 'found', summary: 'Found pull request #866'}};
const completed = {...willCheck, id: 'a9', state: 'completed', arguments: undefined};

assert.equal(actionLabel({tool: 'send_note'}), 'Send note');
assert.equal(actionLabel(willCheck), 'GitHub · Create pull request');
assert.equal(actionLabel({tool: 'connection.forge.create_pull_request'}), 'forge · Create pull request');

assert.equal(actionKind(decide), 'decide');
assert.equal(actionKind(willCheck), 'check');
assert.equal(actionKind(inconclusive), 'decide', 'a check that could not tell hands the choice to a person');
assert.equal(actionKind(doneByYou), 'done-you');
assert.equal(actionKind(doneByCheck), 'done-check');
assert.equal(actionKind(resendByYou), 'resend');
assert.equal(actionKind({...doneByCheck, settled_by: 'check', settled_kind: 'person'}), 'done-you',
  'a person whose name is "check" is still a person: the kind says who settled it, not the name');
assert.equal(actionKind(completed), 'completed');
assert.equal(actionKind(decide, {resumable: false}), 'unknown');

// --- Continue is gated on what still needs a person --------------------------
assert.deepEqual(continueGate([decide, willCheck]), {enabled: false, reason: 'Decide Slack · Send message to continue.', pending: ['a2']});
assert.deepEqual(continueGate([decide, inconclusive]).reason, 'Decide 2 actions to continue.');
assert.equal(continueGate([doneByYou, willCheck]).enabled, true);
assert.equal(continueGate([doneByYou, willCheck]).reason, 'Conclave checks GitHub · Create pull request first, then the run carries on.');
assert.equal(continueGate([doneByYou]).enabled, true);
assert.equal(continueGate([]).enabled, true);

// --- The floating panel -------------------------------------------------------
const open = actionPanelMarkup([willCheck, decide], {escape});
assert.match(open, /class="action-check floating"/);
assert.match(open, /role="region"/);
assert.match(open, /aria-labelledby="action-check-title"/);
assert.match(open, /Check 2 actions before continuing/);
assert.ok(open.indexOf('Slack · Send message') < open.indexOf('GitHub · Create pull request'), 'decisions come first');
assert.match(open, /Needs your decision/);
assert.match(open, /Conclave will check/);
assert.match(open, /Will look for a pull request from fix\/x into main/);
assert.match(open, /The team_slack connection is not granted read conversation/);
assert.match(open, /data-action-choose="done" data-action-id="a2"/);
assert.match(open, /It happened — mark done/);
assert.match(open, /data-action-choose="resend" data-action-id="a2"/);
assert.match(open, /Send it again/);
assert.match(open, /data-action-decide-self="a1"/);
assert.match(open, /Decide yourself instead/);
assert.match(open, /What was sent/);
assert.match(open, /Heads up &lt;b&gt;now&lt;\/b&gt;/, 'arguments are escaped');
assert.doesNotMatch(open, /<b>now/);
assert.match(open, /data-action="resume-live" disabled/);
assert.match(open, /Decide Slack · Send message to continue\./);
assert.match(open, /data-action-minimise aria-expanded="true"/);
assert.doesNotMatch(open, /data-action-undo/);

const confirmDone = actionPanelMarkup([decide], {escape, confirm: {id: 'a2', decision: 'done'}});
assert.match(confirmDone, /Mark as done\./);
assert.match(confirmDone, /data-action-note maxlength="500"/);
assert.match(confirmDone, /data-action-confirm="done" data-action-id="a2"/);
assert.match(confirmDone, /Record as done/);
assert.match(confirmDone, /data-action-back/);
assert.match(confirmDone, /not as Slack’s reply/);
assert.match(confirmDone, /You can undo this until you press Continue/);

const confirmResend = actionPanelMarkup([decide], {escape, confirm: {id: 'a2', decision: 'resend'}});
assert.match(confirmResend, /data-action-confirm="resend" data-action-id="a2"/);
assert.match(confirmResend, /Send it again when I continue/);
assert.match(confirmResend, /Choosing this is your approval for that one call/);
assert.match(confirmResend, /You can undo this until you press Continue/);

const decided = actionPanelMarkup([doneByYou, willCheck], {escape});
assert.match(decided, /class="action-check floating ready"/);
assert.match(decided, /Ready to continue/);
assert.match(decided, /Done · you/);
assert.match(decided, /Posted &lt;at&gt; 10:41/);
assert.match(decided, /data-action-undo="a2"/);
assert.match(decided, /data-action="resume-live"(?! disabled)/);
assert.match(actionPanelMarkup([resendByYou], {escape}), /Will be sent again/);
assert.match(actionPanelMarkup([resendByYou], {escape}), /data-action-undo="a2"/);

const stillOpen = actionPanelMarkup([inconclusive, doneByYou], {escape, notice: 'Conclave checked GitHub. It could not tell, so the run is waiting for you.'});
assert.match(stillOpen, /1 action needs your decision/);
assert.match(stillOpen, /role="status"[^>]*>Conclave checked GitHub/);
assert.match(stillOpen, /GitHub did not respond to the check/);
assert.match(stillOpen, /data-action-check="a1"/);
assert.match(stillOpen, /Check again/);

assert.match(actionPanelMarkup([doneByCheck, doneByYou], {escape}), /Done · checked/);
assert.doesNotMatch(actionPanelMarkup([doneByCheck], {escape}), /data-action-undo/, 'a check is evidence, not a choice');
assert.match(actionPanelMarkup([decide], {escape, busy: true}), /data-action-choose="done" data-action-id="a2" disabled/);

const record = actionPanelMarkup([decide], {escape, resumable: false});
assert.match(record, /class="action-check floating informational"/);
assert.match(record, /1 action has an unknown outcome/);
assert.match(record, /Outcome unknown/);
assert.doesNotMatch(record, /data-action-choose|data-action="resume-live"/);

assert.equal(actionPanelMarkup([doneByYou, resendByYou, completed], {escape, resumable: false}), '',
  'a finished run lists only what stayed uncertain: settled actions were acted on');
assert.equal(actionPillMarkup([doneByYou], {escape, resumable: false}), '');

const hostile = actionPanelMarkup([{...decide, label: '<img src=x onerror=alert(1)>', reason: '<script>x</script>'}], {escape});
assert.doesNotMatch(hostile, /<img|<script/);
assert.equal(actionPanelMarkup([], {escape}), '');
assert.equal(actionPanelMarkup([completed], {escape}), '', 'completed writes are history, not a question');

// --- Minimised, the status line and history -----------------------------------
const pill = actionPillMarkup([willCheck, decide], {escape});
assert.match(pill, /class="action-pill decide"/);
assert.match(pill, /aria-expanded="false"/);
assert.match(pill, /data-action-reopen/);
assert.match(pill, /2 actions to check · 1 needs your decision/);
assert.match(actionPillMarkup([decide], {escape, resumable: false}), /class="action-pill unknown"/);

assert.deepEqual(runAttention({to_check: 1, needs_decision: 1, unknown: 0}), {text: '2 actions to check', level: 'decide'});
assert.deepEqual(runAttention({to_check: 0, needs_decision: 1, unknown: 0}), {text: '1 action needs your decision', level: 'decide'});
assert.deepEqual(runAttention({to_check: 0, needs_decision: 0, unknown: 2}), {text: '2 outcomes unknown', level: 'unknown'});
assert.equal(runAttention({to_check: 0, needs_decision: 0, unknown: 0}), null);
assert.equal(runAttention(undefined), null);
assert.match(historyBadgeMarkup({to_check: 1, needs_decision: 1, unknown: 0}, escape), /class="run-attention">Needs your check · 2</);
assert.match(historyBadgeMarkup({to_check: 0, needs_decision: 0, unknown: 1}, escape), /1 outcome unknown/);
assert.equal(historyBadgeMarkup({to_check: 0, needs_decision: 0, unknown: 0}, escape), '');

assert.deepEqual(uncertainStages([
  {type: 'ActionUncertain', action_id: 'a1', stage: 'build'}, {type: 'ActionUncertain', action_id: 'a2', stage: 'announce'},
  {type: 'ActionSettled', action_id: 'a2', decision: 'done', settled_by: 'Ana Ruiz'},
]), ['build']);
assert.deepEqual(uncertainStages([
  {type: 'ActionUncertain', action_id: 'a2', stage: 'announce'}, {type: 'ActionSettled', action_id: 'a2'},
  {type: 'ActionDecisionUndone', action_id: 'a2', undone_by: 'Ana Ruiz'},
]), ['announce'], 'an undone decision reopens the step');
console.log('PASS: the floating panel, its choices, confirmations, undo, Continue gate, pill, badges and stages.');

// --- The activity feed names each step of the story ---------------------------
const activityUrl = await load('../web/run-activity.js', [['/canvas-ui.js', url('../web/canvas-ui.js')]]);
const {runActivities} = await import(activityUrl);
const d = {workflow: [{id: 'announce', name: 'Tell the team', agent: 'announcer', output: 'notice'}], state: [{key: 'notice', label: 'Notice'}]};
const ctx = {escape, agentName: (_d, id) => ({announcer: 'Announcer'}[id] || id), capabilityName: id => id.replaceAll('_', ' ')};
const rows = runActivities(d, {events: [
  {type: 'ActionUncertain', action_id: 'a2', stage: 'announce', agent: 'announcer', tool: 'send_note', reason: 'The host stopped', sequence: 1},
  {type: 'ActionChecked', action_id: 'a2', stage: 'announce', tool: 'send_note', result: 'inconclusive', summary: 'No answer', sequence: 2},
  {type: 'ActionSettled', action_id: 'a2', stage: 'announce', tool: 'send_note', decision: 'done', settled_by: 'Ana Ruiz', sequence: 3},
  {type: 'ActionDecisionUndone', action_id: 'a2', stage: 'announce', tool: 'send_note', undone_by: 'Ana Ruiz', sequence: 4},
  {type: 'ActionSettled', action_id: 'a2', stage: 'announce', tool: 'send_note', decision: 'resend', settled_by: 'check', sequence: 5},
]}, ctx);
assert.deepEqual(rows.map(row => row.text), [
  'Send note will be sent again after its check',
  'Ana Ruiz undid the decision on Send note',
  'Ana Ruiz marked Send note as done',
  'Conclave checked Send note',
  'Outcome of Send note is uncertain',
]);
assert.equal(rows.at(-1).important, true);
assert.equal(rows.at(-1).detail, 'The host stopped');
assert.equal(rows.at(-2).detail, 'No answer');
assert.equal(rows.at(-2).important, true, 'a check that could not tell needs the person');
console.log('PASS: activity entries for uncertain outcomes, checks, decisions and undo.');

// --- The run projection, the saved-work note and the canvas -------------------
const shared = url('../shared/definition.js');
const liveUrl = await load('../web/live.js', [['/parallel-ui.js', url('../web/parallel-ui.js')], ['/definition.js', shared], ['/canvas-ui.js', url('../web/canvas-ui.js')]]);
const {projectLiveRun, liveRunMarkup} = await import(liveUrl);
const definition = {version: 1, name: 'Announce', agents: [{id: 'writer', name: 'Writer', instructions: 'x', tools: []}],
  state: [{key: 'notice', label: 'Notice', type: 'text', merge: 'replace', statuses: []}],
  workflow: [{id: 'announce', name: 'Tell the team', agent: 'writer', task: 'x', output: 'notice'}], views: [], inputs: [],
  run_config: {}, run_budget: {}};
const interrupted = {id: 'r1', status: 'interrupted', resumable: true, actions: {to_check: 1, needs_decision: 1, unknown: 0},
  request: {definition, task: 'Tell them', inputs: {}, revision: 1}, reviews: [], metrics: {},
  events: [{sequence: 1, event: {type: 'ApplicationStarted', journal: 1}},
           {sequence: 2, event: {type: 'ActionUncertain', action_id: 'a2', stage: 'announce', tool: 'send_note'}},
           {sequence: 3, event: {type: 'ApplicationFinished', status: 'interrupted', reason: 'The host stopped while this run was working'}}]};
const projected = projectLiveRun(interrupted);
assert.deepEqual(projected.actions, {to_check: 1, needs_decision: 1, unknown: 0});
assert.deepEqual(projected.needsCheck, ['announce']);
const markup = liveRunMarkup(definition, projected, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime: {available: true, tools: []}, busy: false, error: ''});
assert.match(markup, /Continue after checking 2 actions/);
assert.doesNotMatch(markup, /This run cannot be continued/);
assert.match(markup, /class="run-attention">2 actions to check</);

const activityForCanvas = await load('../web/run-activity.js', [['/canvas-ui.js', url('../web/canvas-ui.js')]]);
const runCanvasUrl = await load('../web/run-canvas.js', [['/parallel-ui.js', url('../web/parallel-ui.js')], ['/definition.js', shared], ['/canvas-ui.js', url('../web/canvas-ui.js')], ['/run-activity.js', activityForCanvas]]);
const {runStepStatus} = await import(runCanvasUrl);
assert.equal(runStepStatus(definition, projected, 0), 'Needs check');
console.log('PASS: the run projection carries uncertain steps, the saved-work note says how to continue, the canvas marks the step.');

// --- studio.js wires the panel to the host ------------------------------------
const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
assert.match(studio, /from '\/run-actions\.js'/);
assert.match(studio, /\/api\/runs\/\$\{[^}]+\}\/actions`/, 'the panel reads the owner-only actions endpoint');
assert.match(studio, /\/actions\/check`/);
for (const hook of ['data-action-choose', 'data-action-confirm', 'data-action-back', 'data-action-undo', 'data-action-check',
                    'data-action-decide-self', 'data-action-minimise', 'data-action-reopen']) {
  assert.ok(studio.includes(hook.replace('data-', '').replace(/-([a-z])/g, (_, c) => c.toUpperCase())) || studio.includes(hook), `studio.js handles ${hook}`);
}
assert.match(studio, /decision:\s*'undo'|decision:'undo'/);
assert.match(studio, /continueGate\(/, 'the dock and the panel share one gate');
const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
for (const rule of ['.action-check.floating', '.action-pill', '.action-card', '.action-state.decide', '.action-confirm', '.button.decision', '.run-attention'])
  assert.ok(css.includes(rule), `studio.css styles ${rule}`);
assert.match(css, /\.button\.decision\{background:var\(--decision\)/);
console.log('PASS: studio.js wires the panel to the actions endpoints, and studio.css carries its styles.');
