// Revision rounds, automatic retry and source checks, as Studio draws them.
// The mockups are docs/studio/mockups/revision-retry-sources.html.
// Offline: no browser, backend or provider.
//
//   node studio/scripts/check-review-retry-sources.mjs
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
const {projectLiveRun, liveRunMarkup, retryNoteMarkup, livePollDelay} = await import(await load('../web/live.js', common));
const {runActivities} = await import(activityUrl);
const rounds = await import(url('../web/review-rounds.js'));
const sources = await import(url('../web/source-checks.js'));
const {validateDefinition} = await import(shared);

const definition = {
  version: 1, name: 'Brief', description: '',
  agents: [{id: 'writer', name: 'Writer', role: 'custom', kind: 'ai', instructions: 'Write', tools: []},
           {id: 'priya', name: 'Priya', role: 'custom', kind: 'human', instructions: 'Review', tools: []}],
  state: [{key: 'brief', label: 'Brief', type: 'text', merge: 'replace', statuses: []},
          {key: 'decision', label: 'Decision', kind: 'decision', type: 'records', merge: 'upsert', statuses: ['proposed', 'approved', 'rejected', 'changes_requested']}],
  workflow: [{id: 'draft', name: 'Draft the brief', agent: 'writer', task: 'Draft', output: 'brief'},
             {id: 'review', name: 'Review the brief', agent: 'priya', task: 'Review', output: 'decision', max_revisions: 2}],
  views: [{id: 'brief_view', title: 'Brief', collection: 'brief', layout: 'prose'}],
  run_config: {max_tool_steps: 8, max_notes: 12, max_note_chars: 300, max_tool_output_chars: 20000, fan_out_limit: 4},
  run_budget: {max_delegations: null, max_per_agent: null, max_depth: null, tool_calls: null, wall_clock_seconds: null, tokens: null, cost_usd: null},
};
const record = (status, events, extra = {}) => ({id: 'run-1', status, request: {definition, task: 'Write', inputs: {}, revision: 1},
  events: events.map((event, index) => ({sequence: index + 1, event})), reviews: [], ...extra});

// --- 1. Revision rounds ------------------------------------------------------------
validateDefinition(structuredClone(definition));
for (const [change, message] of [[{max_revisions: -1}, /max_revisions/], [{revises: ['review']}, /revises/], [{revises: []}, /revises/]]) {
  const bad = structuredClone(definition);
  Object.assign(bad.workflow[1], change);
  assert.throws(() => validateDefinition(bad), message, 'the browser refuses what the host refuses');
}
const aiRevises = structuredClone(definition);
aiRevises.workflow[0].max_revisions = 1;
assert.throws(() => validateDefinition(aiRevises), /only a human review/);

const revision = {round: 2, max: 2, history: [{round: 1, comment: 'Add the <2025> price list', revised: ['draft']}]};
assert.match(rounds.revisionRoundMarkup(revision, {escape}), /Round 2 of 3/, 'max 2 changes means 3 rounds at most');
assert.match(rounds.revisionRoundMarkup({round: 2, max: null, history: []}, {escape}), /Round 2</, 'no limit, no "of"');
const history = rounds.revisionHistoryMarkup(definition, revision, {escape});
assert.match(history, /You asked for changes in round 1 · Draft the brief ran again/);
assert.match(history, /Add the &lt;2025&gt; price list/, 'the comment is escaped');
assert.doesNotMatch(history, /Earlier rounds/, 'one round has nothing earlier');
const three = rounds.revisionHistoryMarkup(definition, {round: 4, history: [1, 2, 3].map(round => ({round, comment: `Round ${round}`, revised: ['draft']}))}, {escape});
assert.match(three, /Round 3<\/blockquote>.*Earlier rounds · 2/s, 'the latest round first, the rest folded');
const step = definition.workflow[1];
assert.match(rounds.revisionConsequenceMarkup(definition, step, revision, {escape}), /Draft the brief runs again with your reason.*last request for changes/);
assert.match(rounds.revisionConsequenceMarkup(definition, step, {round: 3}, {escape}), /used its 2 rounds of changes, so requesting changes stops the run/);
assert.match(rounds.revisionConsequenceMarkup(definition, {...step, max_revisions: undefined}, null, {escape}), /comes back to you\.<\/p>/);
assert.deepEqual(rounds.revisionTargets(definition, step), ['draft']);
assert.match(rounds.revisionConsequenceMarkup({...definition, workflow: [step]}, step, null, {escape}), /no earlier step to send back/);

const revised = projectLiveRun(record('running', [
  {type: 'ApplicationStarted'}, {type: 'StageStarted', stage: 'draft'}, {type: 'StageFinished', stage: 'draft', status: 'completed'},
  {type: 'ApplicationStateChanged', stage: 'draft', patch: 'One', state: {brief: 'One', decision: []}, version: 1},
  {type: 'HumanInputRecorded', stage: 'review', agent: 'priya'},
  {type: 'RevisionRequested', stage: 'review', agent: 'priya', round: 1, revises: ['draft'], reset: ['draft', 'review'], comment: 'More', state: {brief: '', decision: []}},
  {type: 'StageStarted', stage: 'draft'},
]));
assert.deepEqual(revised.completedSteps, [], 'a step sent back is not complete any more');
assert.equal(revised.state.brief, '', 'and its output left the results');
assert.deepEqual(revised.activeSteps, [0], 'it is working again');
const activity = runActivities(definition, revised, {agentName: (d, id) => id, capabilityName: name => name});
const row = activity.find(item => item.text.startsWith('Changes requested'));
assert.equal(row.text, 'Changes requested · round 1');
assert.match(row.detail, /Draft the brief runs again with the reviewer’s reason; Review the brief waits for it/);

// --- 2. Automatic retry ------------------------------------------------------------
const stopped = [{type: 'ApplicationStarted'}, {type: 'StageStarted', stage: 'draft'},
  {type: 'StageFinished', stage: 'draft', status: 'stopped', reason: 'The model backend did not respond in time'},
  {type: 'ApplicationFinished', status: 'stopped', transient: 'timeout', reason: 'The model backend did not respond in time'}];
const waiting = projectLiveRun(record('stopped', [...stopped, {type: 'RetryScheduled', attempt: 2, attempts: 5, retry_at: '2026-10-20T14:32:00+00:00', reason: 'timeout'}],
  {resumable: true, retry: {at: '2026-10-20T14:32:00+00:00', attempt: 2, attempts: 5, reason: 'timeout'}}));
assert.equal(waiting.retry.attempt, 2);
const note = retryNoteMarkup(waiting, {escape});
assert.match(note, /Trying again automatically at \d{1,2}[:.]\d\d(?:\s?[AP]M)? \(attempt 2 of 5\)\. Continue starts it now\./i);
assert.match(note, /data-action="cancel-retry"[^>]*>Don’t retry</);
assert.doesNotMatch(note, /data-action="resume-live"/, 'Continue stays in the canvas action bar, once');
const runtime = {available: true, profile: 'local', tools: []};
const page = liveRunMarkup(definition, waiting, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime, busy: false, error: ''});
assert.match(page, /<span class="run-status retrying">Waiting to retry<\/span>/);
assert.match(page, /class="retry-line"/);
const gaveUp = projectLiveRun(record('stopped', [...stopped.slice(0, 3), {type: 'RetryScheduled', attempt: 1, attempts: 1}, {type: 'ApplicationResumed'}, ...stopped.slice(1)], {resumable: true, retry: null}));
assert.equal(gaveUp.retry, null);
const after = liveRunMarkup(definition, gaveUp, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime, busy: false, error: ''});
assert.match(after, /Run stopped/);
assert.match(after, /did not respond in time\. Tried again automatically 1 time\./);
assert.doesNotMatch(after, /cancel-retry/);
const retryRows = runActivities(definition, waiting, {agentName: (d, id) => id, capabilityName: name => name});
assert.ok(retryRows.some(item => item.text === 'Trying again automatically · attempt 2 of 5' && /^at \d{1,2}[:.]\d\d(?:\s?[AP]M)?$/i.test(item.detail)));
const cancelledRows = runActivities(definition, projectLiveRun(record('stopped', [...stopped, {type: 'RetryCancelled', reason: 'cancelled', by: 'ana'}])),
  {agentName: (d, id) => id, capabilityName: name => name});
assert.ok(cancelledRows.some(item => item.text === 'ana stopped the automatic retry'));

// A page that polls asks again when the retry is due, not every 1.2 seconds.
const now = Date.parse('2026-10-20T14:30:00Z');
assert.equal(livePollDelay({mode: 'running'}, now), 1200);
assert.equal(livePollDelay({mode: 'stopped', retry: {at: '2026-10-20T14:30:10Z'}}, now), 10000);
assert.equal(livePollDelay({mode: 'stopped', retry: {at: '2026-10-20T14:45:00Z'}}, now), 30000, 'at most 30 seconds apart');
assert.equal(livePollDelay({mode: 'stopped', retry: {at: '2026-10-20T14:29:00Z'}}, now), 1200, 'overdue: soon');
assert.equal(livePollDelay({mode: 'stopped', retry: {at: 'soon'}}, now), 30000, 'an unreadable time is not a fast poll');

// --- 3. Source checks --------------------------------------------------------------
const cited = {id: 'f1', source_refs: ['docs/a.md', 'https://example.com/<x>', 'sales notes'], source_checks: ['found', 'not_found', 'not_checkable']};
const chips = sources.sourceChipsMarkup(cited, {escape});
assert.equal((chips.match(/class="source-chip"/g) || []).length, 3);
assert.match(chips, /data-check="found"[^>]*><svg[^>]*aria-hidden="true".*1<span class="sr-only"> found<\/span>/s, 'a shape and a word, never colour alone');
assert.match(chips, /title="Not found: https:\/\/example.com\/&lt;x&gt;"/, 'the source is escaped');
assert.match(chips, /<span class="sr-only"> not checkable<\/span>/);
assert.equal(sources.sourceSummaryText(cited), '1 of 3 found');
const unchecked = {source_refs: ['docs/a.md']};
assert.equal(sources.sourceChipsMarkup(unchecked, {escape}), '<span class="source-chip" title="docs/a.md">1</span>', 'an older record draws as before');
assert.equal(sources.sourceSummaryText(unchecked), '1 source');
assert.equal(sources.sourceChecks({source_refs: ['a'], source_checks: ['found', 'found']}), null, 'checks that do not line up are not shown');
assert.equal(sources.sourceChecks({source_refs: ['a'], source_checks: ['<b>']}), null, 'nor unknown words');
const many = {source_refs: Array.from({length: 12}, (_, index) => `docs/${index}.md`), source_checks: Array(12).fill('found')};
assert.match(sources.sourceChipsMarkup(many, {escape}), /title="12 of 12 sources found">12</);
const link = ref => `<span>${escape(ref)}</span>`;
const list = sources.sourceListMarkup(cited, {escape, link, stepName: 'Research <pricing>'});
assert.match(list, /checked against what Research &lt;pricing&gt; read/);
assert.match(list, /class="source-list checked"/);
assert.match(list, /Found: the step read or fetched this/);
assert.match(list, /Not found: the step did not read or fetch this</);
assert.match(list, /Not checkable:/);
assert.match(sources.sourceListMarkup(unchecked, {escape, link}), /not checked in this run/);
assert.match(sources.sourceListMarkup({source_refs: []}, {escape, link}), /No supporting source attached/);

console.log('Revision rounds, automatic retry and source checks draw as designed.');
