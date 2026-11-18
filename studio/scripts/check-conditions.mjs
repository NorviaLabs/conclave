// Conditional steps, as Studio validates, edits and draws them. The mockups are
// docs/studio/mockups/conditional-steps.html. Offline: no browser, backend or provider.
//
//   node studio/scripts/check-conditions.mjs
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
const runCanvasUrl = await load('../web/run-canvas.js', common);
const {projectLiveRun} = await import(await load('../web/live.js', common));
const {runStepStatus, runCanvasMarkup} = await import(runCanvasUrl);
const {runActivities} = await import(activityUrl);
const {runProgressText} = await import(parallelUrl);
const defs = await import(shared);
defs.setCanvasMetrics?.({width: 1200, height: 700, nodeWidth: 350, nodeHeight: 240});

const TYPES = ['bug', 'feature', 'question', 'regression'];
const agent = id => ({id, name: id[0].toUpperCase() + id.slice(1), role: 'custom', kind: 'ai', instructions: 'Work', tools: []});
const definition = () => ({
  version: 1, name: 'Issue routing', description: '',
  agents: ['triager', 'engineer', 'writer', 'fixer', 'summarizer'].map(agent),
  state: [{key: 'triage', label: 'Triage', type: 'records', merge: 'upsert', statuses: ['open', 'closed'],
           fields: [{key: 'type', label: 'type', type: 'enum', required: true, options: TYPES}, {key: 'area', label: 'area', type: 'string', required: false}]},
          {key: 'notes', label: 'Notes', type: 'text', merge: 'replace', statuses: []}],
  workflow: [
    {id: 'triage', name: 'Triage the issue', agent: 'triager', task: 'Triage', output: 'triage'},
    {id: 'reproduce', name: 'Reproduce the bug', agent: 'engineer', task: 'Reproduce', output: 'notes',
     condition: {step: 'triage', field: 'type', operator: 'in', value: ['bug', 'regression']}},
    {id: 'proposal', name: 'Draft a proposal', agent: 'writer', task: 'Propose', output: 'notes',
     condition: {step: 'triage', field: 'type', operator: 'equals', value: 'feature'}},
    {id: 'fix', name: 'Write the fix', agent: 'fixer', task: 'Fix', output: 'notes'},
    {id: 'summary', name: 'Summarize', agent: 'summarizer', task: 'Summarize', output: 'notes'}],
  connections: [{from: 'triage', to: 'reproduce'}, {from: 'triage', to: 'proposal'}, {from: 'reproduce', to: 'fix'},
                {from: 'fix', to: 'summary'}, {from: 'proposal', to: 'summary'}],
  views: [{id: 'triage_view', title: 'Triage', collection: 'triage', layout: 'cards'}],
  run_config: {max_tool_steps: 8, max_notes: 12, max_note_chars: 300, max_tool_output_chars: 20000, fan_out_limit: 4},
  run_budget: {max_delegations: null, max_per_agent: null, max_depth: null, tool_calls: null, wall_clock_seconds: null, tokens: null, cost_usd: null},
});

// --- The definition -----------------------------------------------------------------
const d = definition();
defs.validateDefinition(structuredClone(d));
for (const [change, message] of [
  [{step: 'proposal', field: 'text', operator: 'equals', value: 'x'}, /depends on/],
  [{step: 'triage', field: 'colour', operator: 'equals', value: 'red'}, /field/],
  [{step: 'triage', field: 'type', operator: 'equals', value: 'crash'}, /option/],
  [{step: 'triage', field: 'type', operator: 'in', value: 'bug'}, /value/],
  [{step: 'triage', field: 'type', operator: 'matches', value: 'bug'}, /operator/],
]) {
  const bad = definition();
  bad.workflow[1].condition = change;
  assert.throws(() => defs.validateDefinition(bad), message, 'the browser refuses what the host refuses');
}
assert.ok(defs.conditionHolds(d.workflow[1].condition, [{type: 'Bug'}]), 'case is ignored');
assert.ok(!defs.conditionHolds(d.workflow[1].condition, [{type: 'feature'}]));
assert.ok(!defs.conditionHolds(d.workflow[1].condition, null), 'no output never holds');
assert.equal(defs.conditionBadgeText(d, d.workflow[1]), 'Only when Triage the issue: type is bug or regression');
assert.equal(defs.conditionBadgeText(d, d.workflow[0]), '');
assert.equal(defs.conditionSentence(d, d.workflow[1].condition), 'Runs when any Triage the issue record’s type is bug or regression.'.replace('’', "'"));
assert.deepEqual(defs.conditionFields(d, 'triage').map(field => field.key), ['status', 'type', 'area']);
assert.deepEqual(defs.conditionFields(d, 'triage')[1].options, TYPES);
assert.deepEqual(defs.conditionFields(d, 'reproduce').map(field => field.key), ['text']);

// --- A run that skipped a branch --------------------------------------------------------
const events = [
  {type: 'ApplicationStarted'},
  {type: 'StageStarted', stage: 'triage'}, {type: 'StageFinished', stage: 'triage', status: 'completed'},
  {type: 'ApplicationStateChanged', stage: 'triage', patch: [{id: 't1', title: 'I', body: '', status: 'open', type: 'feature', source_refs: []}],
   state: {triage: [{id: 't1', type: 'feature'}], notes: ''}, version: 1},
  {type: 'StageSkipped', stage: 'reproduce', agent: 'engineer', reason: 'Triage the issue: type was “feature”, not bug or regression',
   condition: d.workflow[1].condition},
  {type: 'StageSkipped', stage: 'fix', agent: 'fixer', reason: 'Only Reproduce the bug leads here, and it was skipped'},
  {type: 'StageStarted', stage: 'proposal'},
];
const record = status => ({id: 'run-1', status, request: {definition: d, task: 'Route', inputs: {}, revision: 1},
  events: events.map((event, index) => ({sequence: index + 1, event})), reviews: []});
const run = projectLiveRun(record('running'));
assert.deepEqual(Object.keys(run.skippedSteps), ['reproduce', 'fix']);
assert.equal(runStepStatus(d, run, 1), 'Skipped');
assert.equal(runStepStatus(d, run, 3), 'Skipped');
assert.equal(runStepStatus(d, run, 2), 'Working');
assert.equal(runProgressText(d, run), '1 of 5 steps complete · 2 skipped · Writer is working');
const canvas = runCanvasMarkup(d, run, null, {escape, agentName: (x, id) => d.agents.find(item => item.id === id)?.name || id,
  outputName: (x, key) => key, capabilityName: name => name, now: Date.now()});
assert.match(canvas, /data-run-node="reproduce" data-status="skipped"/);
assert.match(canvas, /data-status="skipped"><i aria-hidden="true"><\/i>Skipped</, 'a marker and a word');
assert.match(canvas, /<span class="skip-reason">Triage the issue: type was “feature”, not bug or regression<\/span>/);
assert.match(canvas, /class="canvas-edge run-edge skipped-edge"/, 'what leaves a skipped step is dashed');
const rows = runActivities(d, run, {agentName: (x, id) => id, capabilityName: name => name});
assert.ok(rows.some(row => row.text === 'Skipped · Reproduce the bug' && /not bug or regression/.test(row.detail)));
// A revision that resets a skipped step takes the skip away.
const revised = projectLiveRun({...record('running'), events: [...events, {type: 'RevisionRequested', stage: 'summary', round: 1,
  revises: ['triage'], reset: ['triage', 'reproduce', 'fix'], comment: 'x', state: {triage: [], notes: ''}}].map((event, index) => ({sequence: index + 1, event}))});
assert.deepEqual(Object.keys(revised.skippedSteps), []);

// --- Editing: the badge, "Run only when…", and what each choice resets -------------------
const workbench = await import(await load('../web/workbench-ui.js', [...common, ['/plan.js', await load('../web/plan.js', [['/definition.js', shared]])],
  ['/catalogue.js', url('../shared/catalogue.js')], ['/run-canvas.js', runCanvasUrl]]));
const badge = workbench.conditionBadgeMarkup(d, d.workflow[1], escape);
assert.match(badge, /^<span class="condition-badge" title="Only when Triage the issue: type is bug or regression"><svg[^>]*aria-hidden="true"/);
assert.equal(workbench.conditionBadgeMarkup(d, d.workflow[0], escape), '', 'no condition, no badge');
const hostile = definition();
hostile.workflow[0].name = '<b>Triage</b>';
assert.match(workbench.conditionBadgeMarkup(hostile, hostile.workflow[1], escape), /&lt;b&gt;Triage&lt;\/b&gt;/, 'names are escaped');

const editor = workbench.conditionEditorMarkup(d, d.workflow[1], {escape});
assert.match(editor, /Run only when…/);
assert.match(editor, /<option value="triage" selected>Triage the issue<\/option>/);
assert.match(editor, /<legend class="field-label">Values<\/legend>/, 'options are a labelled group of checkboxes');
assert.match(editor, /data-condition-option="bug"[^>]*checked>bug/);
assert.doesNotMatch(editor, /data-condition-option="feature"[^>]*checked/);
assert.doesNotMatch(editor, /<option value="contains"/, 'contains is for free text, not fixed options');
assert.match(editor, /role="status">Runs when any Triage the issue record&#39;s type is bug or regression\.</);
assert.match(editor, /data-condition-remove="reproduce"/);
const none = workbench.conditionEditorMarkup(d, d.workflow[3], {escape});
assert.match(none, /always runs once the steps before it finish.*data-condition-add="fix"/s);
assert.match(workbench.conditionEditorMarkup(d, d.workflow[0], {escape}), /Connect an earlier step first/, 'a first step has nothing to check');
const pending = workbench.conditionEditorMarkup(d, d.workflow[3], {escape, draft: {step: 'reproduce', field: 'text', operator: 'contains', value: ''},
  issue: 'Step “Write the fix”: the condition\'s value must be one nonblank text value of at most 200 characters.'});
assert.match(pending, /<label for="condition-value">Value<\/label><input id="condition-value"/, 'free text is typed');
assert.match(pending, /role="alert">Step “Write the fix”: the condition&#39;s value must be one/, 'what it still needs, in place of the sentence');

const fresh = defs.defaultCondition(d, 'fix');
assert.deepEqual(fresh, {step: 'reproduce', field: 'text', operator: 'contains', value: ''}, 'the nearest earlier step');
assert.deepEqual(defs.defaultCondition(d, 'summary').step, 'fix');
assert.equal(defs.defaultCondition(d, 'triage'), null);
let c = defs.conditionWithPart(d, fresh, 'step', 'triage');
assert.deepEqual(c, {step: 'triage', field: 'type', operator: 'in', value: ['bug']}, 'another step chooses its field and first option again');
c = defs.conditionWithOption(d, c, 'regression', true);
assert.deepEqual(c.value, ['bug', 'regression']);
c = defs.conditionWithOption(d, c, 'bug', false);
assert.deepEqual(c.value, ['regression']);
c = defs.conditionWithPart(d, c, 'operator', 'equals');
assert.equal(c.value, 'regression', 'a list becomes its first value');
c = defs.conditionWithPart(d, c, 'operator', 'not_in');
assert.deepEqual(c.value, ['regression'], 'one value becomes a list of it');
c = defs.conditionWithPart(d, c, 'field', 'area');
assert.deepEqual(c, {step: 'triage', field: 'area', operator: 'equals', value: ''});
c = defs.conditionWithPart(d, {...c, operator: 'in', value: []}, 'value', 'billing, login ,');
assert.deepEqual(c.value, ['billing', 'login'], 'a typed list is split on commas');
const applied = definition();
applied.workflow[3].condition = defs.conditionWithPart(d, defs.defaultCondition(d, 'fix'), 'value', 'crash');
defs.validateDefinition(applied);

// A condition that decides whether a person reviews is named before a run.
const gated = definition();
gated.agents.push({id: 'lead', name: 'Lead', role: 'custom', kind: 'human', instructions: 'Approve', tools: []});
gated.workflow[3] = {...gated.workflow[3], agent: 'lead', condition: {step: 'triage', field: 'type', operator: 'equals', value: 'bug'}};
const warned = defs.preflightIssues(gated).filter(issue => issue.code === 'conditional-review');
assert.equal(warned.length, 1);
assert.equal(warned[0].severity, 'warning');
assert.match(warned[0].message, /Whether Write the fix happens depends on what Triage the issue produced\. When it is skipped, every step after it is skipped too\./);
assert.equal(defs.preflightIssues(definition()).filter(issue => issue.code === 'conditional-review').length, 0, 'AI steps with conditions are not flagged');

console.log('Conditional steps validate, edit and draw as designed.');
