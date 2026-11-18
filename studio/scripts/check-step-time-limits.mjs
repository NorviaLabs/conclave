// A step's own time limit, as Studio sets it and shows a step that ran over.
// The mockups are docs/studio/mockups/step-time-limits.html.
// Offline: no browser, backend or provider.
//
//   node studio/scripts/check-step-time-limits.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const url = path => new URL(path, import.meta.url).href;
const load = async (path, replacements = []) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, target] of replacements) source = source.replaceAll(`'${specifier}'`, JSON.stringify(target));
  return 'data:text/javascript,' + encodeURIComponent(source);
};
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const shared = url('../shared/definition.js'), canvasUi = url('../web/canvas-ui.js');
const parallelUrl = await load('../web/parallel-ui.js', [['/definition.js', shared]]);
const activityUrl = await load('../web/run-activity.js', [['/canvas-ui.js', canvasUi], ['/parallel-ui.js', parallelUrl]]);
const common = [['/definition.js', shared], ['/canvas-ui.js', canvasUi], ['/run-activity.js', activityUrl], ['/parallel-ui.js', parallelUrl]];
const {projectLiveRun, liveRunMarkup} = await import(await load('../web/live.js', common));
const {runActivities} = await import(activityUrl);
const {runStepStatus} = await import(await load('../web/run-canvas.js', common));
const {timedOutNoteMarkup} = await import(parallelUrl);
const {validateDefinition} = await import(shared);
const ui = await import(url('../web/time-limit-ui.js'));

// --- Durations, as read and as heard -----------------------------------------------------------
assert.equal(ui.durationShort(300), '5 m');
assert.equal(ui.durationShort(90), '1 m 30 s');
assert.equal(ui.durationShort(10), '10 s');
assert.equal(ui.durationShort(7200), '2 h');
assert.equal(ui.durationShort(5400), '1 h 30 m');
assert.equal(ui.durationWords(300), '5 minutes');
assert.equal(ui.durationWords(61), '1 minute 1 second');
assert.equal(ui.durationWords(5400), '1 hour 30 minutes');
assert.equal(ui.spokenDuration(300), '<span aria-hidden="true">5 m</span><span class="sr-only">5 minutes</span>', 'never read as "5 meters"');

// --- Setting a limit --------------------------------------------------------------------------------
assert.equal(ui.parseTimeLimit(false, '5', '0'), null, 'unticked: no limit');
assert.equal(ui.parseTimeLimit(true, '5', '0'), 300);
assert.equal(ui.parseTimeLimit(true, '', '45'), 45);
for (const [minutes, seconds] of [['0', '5'], ['1441', '0'], ['x', '0'], ['1', '60'], ['-1', '30'], ['1.5', '0']])
  assert.throws(() => ui.parseTimeLimit(true, minutes, seconds), /at least 10 seconds and at most 24 hours/, `${minutes} min ${seconds} s`);

const step = {id: 'draft', name: 'Draft <the> notes', agent: 'writer', task: 'Draft', output: 'notes', time_limit_seconds: 300};
const field = ui.timeLimitFieldMarkup(step, 1, {escape});
assert.match(field, /^<section class="inspector-section"><h4>Time limit<\/h4><div class="field time-limit-field" role="group" aria-label="Time limit for “Draft &lt;the&gt; notes”" aria-describedby="time-limit-help-1">/);
assert.match(field, /<label class="inline"><input type="checkbox" data-time-limit-enabled="1" checked> Stop this step if it runs longer than<\/label><input type="number" min="0" max="1440" step="1" value="5" data-time-limit-minutes="1" aria-label="Minutes" ><span class="unit">min<\/span><input type="number" min="0" max="59" step="1" value="0" data-time-limit-seconds="1" aria-label="Seconds" ><span class="unit">s<\/span>/);
assert.match(field, /<p class="field-help" id="time-limit-help-1">Counts the time it works — model calls, tools and members it asks — but not time waiting for someone to approve an action\. When it runs over, it is stopped and the run stops after the steps beside it finish; Continue runs it again\.<\/p>/);
const unlimited = ui.timeLimitFieldMarkup({...step, time_limit_seconds: undefined}, 1, {escape});
assert.match(unlimited, /data-time-limit-enabled="1" > Stop/);
assert.match(unlimited, /value="" data-time-limit-minutes="1" aria-label="Minutes" disabled>/);
assert.match(unlimited, /No limit: the step works until it finishes, within the run’s own limits\./);
const wrong = ui.timeLimitFieldMarkup(step, 1, {escape, issue: 'Give a time limit of at least 10 seconds and at most 24 hours.'});
assert.match(wrong, /aria-label="Minutes" aria-invalid="true" aria-describedby="time-limit-error-1" >/);
const typed = ui.timeLimitFieldMarkup(step, 1, {escape, issue: 'Give a time limit of at least 10 seconds and at most 24 hours.', typed: {minutes: '0', seconds: '5'}});
assert.match(typed, /value="0" data-time-limit-minutes="1"/, 'what was typed stays, to be corrected');
assert.match(typed, /value="5" data-time-limit-seconds="1"/);
assert.match(wrong, /<p class="validation" id="time-limit-error-1" role="alert">Give a time limit of at least 10 seconds and at most 24 hours\.<\/p>/);
assert.equal(ui.timeLimitFieldMarkup(step, 1, {escape, human: true}),
  '<section class="inspector-section"><h4>Time limit</h4><p class="field-help">A person’s step has no time limit: it waits for the person.</p></section>');

assert.match(ui.timeLimitBadgeMarkup(step), /^<span class="time-limit-badge" title="Time limit: 5 minutes"><svg[^>]*>.*<\/svg><span aria-hidden="true">5 m<\/span><span class="sr-only">Time limit: 5 minutes<\/span><\/span>$/s);
assert.equal(ui.timeLimitBadgeMarkup({...step, time_limit_seconds: undefined}), '');

// The definition refuses what the host refuses.
const definition = {
  version: 1, name: 'Release', description: '',
  agents: [{id: 'writer', name: 'Writer', role: 'custom', kind: 'ai', instructions: 'Write', tools: []},
           {id: 'priya', name: 'Priya', role: 'custom', kind: 'human', instructions: 'Review', tools: []}],
  state: [{key: 'notes', label: 'Notes', type: 'text', merge: 'replace', statuses: []}],
  workflow: [{...step, name: 'Draft the notes'}, {id: 'review', name: 'Review', agent: 'priya', task: 'Review', output: 'notes'}],
  connections: [{from: 'draft', to: 'review'}], views: [{id: 'notes', collection: 'notes', title: 'Notes', layout: 'prose'}], inputs: [],
  run_config: {max_tool_steps: 8, max_notes: 12, max_note_chars: 300, max_tool_output_chars: 20000, fan_out_limit: 4},
  run_budget: {max_delegations: null, max_per_agent: null, max_depth: null, tool_calls: null, wall_clock_seconds: null, tokens: null, cost_usd: null},
};
validateDefinition(definition);
for (const value of [9, 86401, 12.5, '60', null, true]) {
  const copy = structuredClone(definition); copy.workflow[0].time_limit_seconds = value;
  assert.throws(() => validateDefinition(copy), /time limit/, `time limit ${JSON.stringify(value)} is refused`);
}
const person = structuredClone(definition); person.workflow[1].time_limit_seconds = 60;
assert.throws(() => validateDefinition(person), /person/);

// --- A step that ran over -------------------------------------------------------------------------
const stop = {type: 'StageFinished', stage: 'draft', agent: 'writer', status: 'timed_out', reason: '“Draft the notes” ran past its time limit (5 m)',
  time_limit_seconds: 300, elapsed_seconds: 300.4};
const record = {id: 'run-1', status: 'stopped', request: {definition, task: 'Release', inputs: {}, revision: 2}, reviews: [], resumable: true,
  events: [{sequence: 1, event: {type: 'ApplicationStarted'}}, {sequence: 2, event: {type: 'StageStarted', stage: 'draft', agent: 'writer'}},
           {sequence: 3, event: stop},
           {sequence: 4, event: {type: 'ApplicationFinished', status: 'stopped', stage: 'draft', reason: stop.reason, timed_out: true, time_limit_seconds: 300}}]};
const run = projectLiveRun(record);
assert.deepEqual(run.timedOut, {draft: {limit: 300, elapsed: 300.4}}, 'the live run knows which step timed out');
assert.equal(runStepStatus(definition, run, 0), 'Timed out');
assert.match(ui.timedOutNoticeMarkup(run.timedOut.draft),
  /^<div class="timed-out-note" role="status"><svg[^>]*>.*<\/svg><span><strong>Stopped after <span aria-hidden="true">5 m 00 s<\/span><span class="sr-only">5 minutes<\/span>:<\/strong> its time limit is <span aria-hidden="true">5 m<\/span><span class="sr-only">5 minutes<\/span>\. The model call it was making was stopped, and nothing it had half-written was kept\.<\/span><\/div>$/s);
const note = timedOutNoteMarkup(definition, run, {escape});
assert.match(note, /<strong>“Draft the notes” ran past its time limit \(<span aria-hidden="true">5 m<\/span><span class="sr-only">5 minutes<\/span>\)\.<\/strong><p>It was stopped after <span aria-hidden="true">5 m 00 s<\/span><span class="sr-only">5 minutes<\/span>, while it worked\. 0 completed steps remain in this run\. Continue runs it again with its full limit\.<\/p>/);
assert.match(note, /<div class="note-actions"><button class="button primary" data-action="resume-live">Continue<\/button><button class="button" data-action="view-results">View saved work<\/button><\/div>/);
assert.doesNotMatch(timedOutNoteMarkup(definition, {...run, resumable: false}, {escape}), /resume-live/, 'nothing to continue, no Continue');
const markup = liveRunMarkup(definition, run, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime: {}, busy: false, error: ''});
assert.match(markup, /<section class="saved-work-note" role="status"><strong>“Draft the notes” ran past its time limit/);
const [row] = runActivities(definition, {events: [stop]}, {agentName: (d, id) => id, capabilityName: id => id});
assert.equal(row.text, '“Draft the notes” timed out · 5 m limit');
assert.equal(row.detail, 'Stopped while it worked; the run stops once the steps beside it finish');
assert.equal(row.important, true);

// A run comparison says it too.
const compare = await import(url('../web/run-compare-ui.js'));
const compared = compare.runComparisonMarkup({name: 'Release', runs: {a: {id: 'a', version: 2, status: 'completed'}, b: {id: 'b', version: 2, status: 'stopped'}},
  version: {same: true}, request: {task: null, inputs: []}, summary: {text: '1 of 2 steps differ.'},
  steps: [{id: 'draft', name: 'Draft', agent: 'Writer', presence: 'both', kind: 'ai', status: {a: {status: 'completed', reason: ''}, b: {status: 'timed_out', reason: stop.reason}},
           tags: [{kind: 'status_changed', text: 'Ended differently'}], differs: true}]}, {escape, time: iso => iso});
assert.match(compared, /<span class="side-label">B<\/span>Timed out<p>“Draft the notes” ran past its time limit \(5 m\)<\/p>/);

// --- Nothing a definition holds is drawn as markup --------------------------------------------------
const hostile = '<img src=x onerror=alert(1)>';
assert.doesNotMatch(ui.timeLimitFieldMarkup({...step, name: hostile}, 1, {escape, issue: hostile}), /<img/);
assert.doesNotMatch(timedOutNoteMarkup({...definition, workflow: [{...definition.workflow[0], name: hostile}, definition.workflow[1]]}, run, {escape}), /<img/);

// --- Studio wires it in --------------------------------------------------------------------------------
const [studio, workbench, canvas, css] = await Promise.all(['../web/studio.js', '../web/workbench-ui.js', '../web/run-canvas.js', '../web/studio.css']
  .map(path => readFile(new URL(path, import.meta.url), 'utf8')));
assert.match(studio, /import \{[^}]*parseTimeLimit[^}]*\} from '\/time-limit-ui\.js';/);
assert.match(studio, /timeLimitFieldMarkup\(/);
assert.match(studio, /data-time-limit-enabled|timeLimitEnabled/);
assert.match(workbench, /timeLimitField\(/, 'the inspector shows the field');
assert.match(workbench, /timeLimitBadge\(/, 'and the plan card its limit');
assert.match(canvas, /'Timed out'/);
for (const rule of ['.time-limit-field', '.time-limit-badge', '.run-node[data-status="timed-out"]', '.timed-out-note'])
  assert.ok(css.includes(rule + '{') || css.includes(rule + ' ') || css.includes(rule + ','), `studio.css has ${rule}`);
console.log('step time limit checks passed');
