// P6, P7 — Studio with several steps running at once: held steps, per-step
// outcomes, pausing, a run stopped at a run-wide limit, and Steps at once in
// Limits and approvals. The mockups are docs/studio/mockups/parallel-steps.
// Offline: no browser, backend or provider.
//
//   node studio/scripts/check-parallel.mjs
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
const workbenchUrl = await load('../web/workbench-ui.js', [...common, ['/plan.js', await load('../web/plan.js', [['/definition.js', shared]])],
  ['/catalogue.js', url('../shared/catalogue.js')], ['/run-canvas.js', runCanvasUrl]]);

const {setCanvasMetrics} = await import(shared);
const {projectLiveRun, liveRunMarkup} = await import(await load('../web/live.js', common));
const {runStepStatus, runCanvasMarkup} = await import(runCanvasUrl);
const {runActivities} = await import(activityUrl);
const {executionDetailMarkup} = await import(workbenchUrl);
const ui = await import(parallelUrl);

const agentName = (d, id) => d.agents.find(agent => agent.id === id)?.name || id;
const outputName = (d, key) => d.state.find(item => item.key === key)?.label || key;
const capabilityName = id => id.replaceAll('_', ' ');
const names = {agentName, capabilityName};

// --- The mockups' plan: one step, three side by side, then the brief ---------------
const STEPS = [['scope', 'Frame the question', 'planner', 'Planner', 'question', []],
  ['news', 'Scan the news', 'scout', 'News scout', 'news', ['scope']],
  ['prices', 'Compare prices', 'analyst', 'Price analyst', 'prices', ['scope']],
  ['reviews', 'Read customer reviews', 'reader', 'Review reader', 'reviews', ['scope']],
  ['brief', 'Write the brief', 'writer', 'Writer', 'brief', ['news', 'prices', 'reviews']]];
const d = {version: 1, name: 'Market brief', inputs: [],
  agents: STEPS.map(([, , id, name]) => ({id, name, kind: 'ai', instructions: 'Work.', tools: []})),
  state: STEPS.map(([, , , , key]) => ({key, label: key[0].toUpperCase() + key.slice(1), type: 'text', merge: 'replace', statuses: []})),
  workflow: STEPS.map(([id, name, agent, , output]) => ({id, name, agent, task: 'Work.', output})),
  connections: STEPS.flatMap(([id, , , , , needs]) => needs.map(from => ({from, to: id}))),
  views: [{id: 'brief_view', title: 'Brief', collection: 'brief', layout: 'prose'}],
  run_config: {max_tool_steps: null, max_notes: 12, max_note_chars: 300, max_tool_output_chars: 20000, fan_out_limit: 3},
  run_budget: {max_delegations: null, max_per_agent: null, max_depth: null, tool_calls: null, wall_clock_seconds: null, tokens: 50000, cost_usd: null}};
const TEXT = {question: 'What did Acme launch?', news: 'Nimbus 2 launched on 3 March.', prices: 'Nimbus 2 is $349.', reviews: 'Reviews are warm: 4.3 of 5 across 212 reviews.'};
const empty = {question: '', news: '', prices: '', reviews: '', brief: ''};

function record(status, extra = [], {resumable = false} = {}) {
  let state = {...empty}, version = 0;
  const events = [];
  const at = seconds => new Date(Date.UTC(2026, 10, 16, 10, 30, seconds)).toISOString();
  const push = (seconds, event) => events.push({sequence: events.length + 1, created_at: at(seconds), event});
  const commit = (seconds, stage, agent, key) => { state = {...state, [key]: TEXT[key]}; version += 1;
    push(seconds, {type: 'ApplicationStateChanged', stage, writer: agent, patch: TEXT[key], version, keys: [key], state: {...state}}); };
  push(0, {type: 'ApplicationStarted', state: {...state}, journal: 1});
  push(1, {type: 'StageStarted', stage: 'scope', agent: 'planner', output: 'question'});
  push(2, {type: 'StageFinished', stage: 'scope', agent: 'planner', status: 'completed'});
  commit(2, 'scope', 'planner', 'question');
  for (const [stage, agent] of [['news', 'scout'], ['prices', 'analyst'], ['reviews', 'reader']]) push(3, {type: 'StageStarted', stage, agent, output: stage});
  push(4, {type: 'ToolCalled', stage: 'news', agent: 'scout', tool: 'web_search', ok: true});
  push(5, {type: 'StageFinished', stage: 'reviews', agent: 'reader', status: 'completed', held: true, patch: TEXT.reviews});
  for (const item of extra) {
    if (item.commit) commit(item.at ?? 9, ...item.commit); else push(item.at ?? 9, item);
  }
  return {id: 'run-1', status, resumable, request: {definition: d, task: 'Brief me', inputs: {}, revision: 1}, events, reviews: [], metrics: {}, actions: {to_check: 0, needs_decision: 0, unknown: 0}};
}

// --- Running, with a held step (mockup 01, 02) ---------------------------------------
const running = projectLiveRun(record('running'));
assert.deepEqual(running.activeSteps.map(index => d.workflow[index].id).sort(), ['news', 'prices']);
assert.deepEqual(running.finishedSteps, ['reviews'], 'a held step is finished, not working');
assert.deepEqual(running.completedSteps, ['scope'], 'and has not joined the results yet');
const status = id => runStepStatus(d, running, d.workflow.findIndex(step => step.id === id));
assert.equal(status('reviews'), 'Finished');
assert.equal(status('news'), 'Working');
assert.equal(status('scope'), 'Complete');
assert.equal(status('brief'), 'Waiting');
assert.deepEqual(ui.heldWaitingFor(d, running, 'reviews'), ['Scan the news', 'Compare prices'], 'in plan wording and order');
assert.equal(ui.joinNames(['A']), 'A');
assert.equal(ui.joinNames(['A', 'B']), 'A and B');
assert.equal(ui.joinNames(['A', 'B', 'C', 'D']), 'A, B and 2 more');
assert.equal(ui.runProgressText(d, running, names), '1 of 5 steps complete · 2 steps running · 1 finished, waiting its turn');

setCanvasMetrics({width: 1400, height: 900});
const canvas = runCanvasMarkup(d, running, null, {escape, agentName, outputName, capabilityName, zoom: 1});
assert.match(canvas, /data-run-node="reviews" data-status="finished"/);
assert.match(canvas, /<i aria-hidden="true"><\/i>Finished<\/span>/, 'the status is said in words');
assert.match(canvas, /\d+ words · joins the results after Scan the news and Compare prices/);
const overview = liveRunMarkup(d, running, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime: {available: true, tools: [], profile: 'local'}, busy: false, error: ''});
assert.match(overview, /1 of 5 steps complete · 2 steps running · 1 finished, waiting its turn/);
assert.doesNotMatch(overview, /Review reader is working/, 'the held step is not called working');

const detail = executionDetailMarkup(d, running, 'reviews', {escape, agentName, outputName, capabilityName});
assert.match(detail, /STEP 4 · FINISHED/);
assert.match(detail, /Reviews are warm: 4\.3 of 5 across 212 reviews\./, 'the held result is shown from StageFinished');
assert.match(detail, /class="held-note" role="note"/);
assert.match(detail, /Not in the shared results yet\.[\s\S]*Scan the news[\s\S]*Compare prices[\s\S]*plan’s order/);

const rows = runActivities(d, running, names);
assert.ok(rows.some(row => row.text === 'Review reader finished Read customer reviews' && !row.important));
// One working step keeps today's words.
const single = projectLiveRun({...record('running'), events: record('running').events.slice(0, 5)});
assert.equal(ui.runProgressText(d, single, names), '1 of 5 steps complete · News scout is working');

// --- The held step joins in its turn -------------------------------------------------
const joined = projectLiveRun(record('running', [{commit: ['news', 'scout', 'news']}, {commit: ['prices', 'analyst', 'prices']}, {commit: ['reviews', 'reader', 'reviews']}]));
assert.deepEqual(joined.finishedSteps, [], 'once committed, it is complete');
assert.equal(runStepStatus(d, joined, 3), 'Complete');

// --- Pausing (mockup 03) -------------------------------------------------------------
const pausing = projectLiveRun(record('running', [{type: 'PauseRequested', running: ['news', 'prices']}]));
assert.deepEqual(pausing.pausing, {running: 2});
assert.equal(ui.runProgressText(d, pausing, names), '1 of 5 steps complete · 2 steps finishing · no new step will start');
assert.deepEqual(ui.pauseControl(pausing), {label: 'Pausing · 2 running steps will finish first', disabled: true});
assert.deepEqual(ui.pauseControl(running), {label: 'Pause: no new step starts; running steps finish', disabled: false});
assert.equal(ui.pauseNotice(running), 'Pausing: no new step will start. 2 running steps will finish first.');
assert.equal(ui.pauseNotice(single), 'Pausing: no new step will start. 1 running step will finish first.');
assert.match(liveRunMarkup(d, pausing, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime: {available: true, tools: []}, busy: false, error: ''}),
  /<span class="run-status pausing">Pausing<\/span>/);
assert.ok(runActivities(d, pausing, names).some(row => row.text === 'Pausing · 2 running steps will finish first'));
const resumedAfterPause = projectLiveRun(record('running', [{type: 'PauseRequested', running: ['news', 'prices']},
  {type: 'ApplicationFinished', status: 'paused', next_stage: 'brief', reason: 'Paused: no new step started; running steps finished'},
  {type: 'ApplicationResumed', state: {}}]));
assert.equal(resumedAfterPause.pausing, null, 'a resume clears the pause');

// --- Stopped at the token limit (mockup 05) ------------------------------------------
const budgetRecord = record('stopped', [
  {type: 'BudgetExceeded', stage: 'news', agent: 'scout', dimension: 'tokens', limit: 50000, used: 50410},
  {type: 'StageFinished', stage: 'news', agent: 'scout', status: 'completed', cut_short: 'tokens'},
  {commit: ['news', 'scout', 'news']},
  {type: 'StageFinished', stage: 'prices', agent: 'analyst', status: 'completed', cut_short: 'tokens'},
  {commit: ['prices', 'analyst', 'prices']}, {commit: ['reviews', 'reader', 'reviews']},
  {type: 'ApplicationFinished', status: 'stopped', budget: 'tokens', reason: 'Stopped: the run reached its token limit (50,000)'}]);
const budget = projectLiveRun(budgetRecord);
assert.deepEqual(budget.budgetStop, {dimension: 'tokens', limit: 50000, used: 50410});
assert.equal(ui.runProgressText(d, budget, names), '4 of 5 steps complete · stopped at the token limit');
const budgetNote = ui.budgetStopNoteMarkup(budget, {escape});
assert.match(budgetNote, /50,410 of 50,000/);
assert.match(budgetNote, /No new step started once it was reached/);
assert.match(budgetNote, /data-step="5"[^>]*>Open Limits and approvals<\/button>/);
assert.doesNotMatch(budgetNote, /resume-live|Continue from/, 'there is no Continue');
const budgetView = liveRunMarkup(d, budget, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime: {available: true, tools: []}, busy: false, error: ''});
assert.match(budgetView, /Open Limits and approvals/);
assert.doesNotMatch(budgetView, /Continue from the next unfinished step/);
const budgetCanvas = runCanvasMarkup(d, budget, null, {escape, agentName, outputName, capabilityName, zoom: 1});
assert.match(budgetCanvas, /data-run-node="news"[\s\S]*?cut short at the token limit/);
assert.equal(runStepStatus(d, budget, 4), 'Incomplete');

// --- One step failed, two finished after it (mockup 06) ------------------------------
const failed = projectLiveRun(record('failed', [
  {type: 'StageFinished', stage: 'prices', agent: 'analyst', status: 'failed', reason: 'Its output did not match Prices after 2 repair attempts'},
  {type: 'StageFinished', stage: 'news', agent: 'scout', status: 'completed'},
  {commit: ['news', 'scout', 'news']}, {commit: ['reviews', 'reader', 'reviews']},
  {type: 'ApplicationFinished', status: 'failed', stage: 'prices', reason: 'Its output did not match Prices after 2 repair attempts'}]));
assert.deepEqual(failed.stepOutcomes.prices, {status: 'failed', reason: 'Its output did not match Prices after 2 repair attempts'});
assert.equal(runStepStatus(d, failed, 2), 'Failed');
assert.equal(runStepStatus(d, failed, 1), 'Complete');
assert.equal(runStepStatus(d, failed, 3), 'Complete');
const failedNote = ui.failedRunNoteMarkup(d, failed, {escape});
// The steps whose work joined the results after the failure: one was still
// working, one had finished and was waiting its turn.
assert.match(failedNote, /<em>Scan the news<\/em> and <em>Read customer reviews<\/em> finished beside it, and their work is saved\./);
assert.match(failedNote, /<strong>Compare prices failed\.<\/strong> Its output did not match Prices after 2 repair attempts/, 'the note names the step that failed');
const failedCanvas = runCanvasMarkup(d, failed, null, {escape, agentName, outputName, capabilityName, zoom: 1});
assert.match(failedCanvas, /data-run-node="prices" data-status="failed"[\s\S]*?Its output did not match Prices after 2 repair attempts/);
// A step that stopped (not failed) is Incomplete, whichever step the run names.
const stopped = projectLiveRun(record('stopped', [{type: 'StageFinished', stage: 'news', agent: 'scout', status: 'stopped', reason: 'The backend did not answer in time'},
  {type: 'ApplicationFinished', status: 'stopped', stage: 'prices', reason: 'x'}]));
assert.equal(runStepStatus(d, stopped, 1), 'Incomplete');
assert.ok(runActivities(d, failed, names).some(row => row.text === 'Compare prices failed' && row.important && row.detail.includes('repair attempts')));

// --- Escaping --------------------------------------------------------------------------
const hostile = structuredClone(d);
hostile.workflow[1].name = 'News <b>&</b>';
const hostileRun = projectLiveRun({...record('running'), request: {definition: hostile, task: 'x', inputs: {}, revision: 1}});
const hostileCanvas = runCanvasMarkup(hostile, hostileRun, null, {escape, agentName, outputName, capabilityName, zoom: 1});
assert.doesNotMatch(hostileCanvas, /<b>&<\/b>/);
assert.match(hostileCanvas, /joins the results after News &lt;b&gt;&amp;&lt;\/b&gt; and Compare prices/);
assert.doesNotMatch(ui.heldDetailNoteMarkup(hostile, hostileRun, 'reviews', {escape}), /<b>&<\/b>/);

// --- Steps at once (mockup 07) ----------------------------------------------------------
const field = ui.stepsAtOnceMarkup(3, {cap: 8, escape});
assert.match(field, /<label for="field-run_config-fan_out_limit">Steps at once<\/label>/);
assert.match(field, /type="number" step="1" min="1" max="8"/);
assert.match(field, /data-path="run_config.fan_out_limit"/);
assert.match(field, /aria-describedby="steps-at-once-help"/);
assert.match(field, /placeholder="As many as this host allows \(8\)"/);
assert.match(field, /id="steps-at-once-help"[^>]*>How many steps may work at the same time when they do not depend on each other\. <strong>1<\/strong> runs them one at a time\. Leave blank for as many as this host allows \(8\)\. Results still join in the plan’s order, whichever step finishes first\./);
assert.match(ui.stepsAtOnceMarkup(null, {cap: 4, escape}), /value=""[\s\S]*max="4"|max="4"[\s\S]*value=""/);
assert.equal(ui.stepsAtOnceIssue(3, 8), '');
assert.equal(ui.stepsAtOnceIssue(null, 8), '');
for (const value of [0, -1, 9, 2.5]) assert.equal(ui.stepsAtOnceIssue(value, 8), 'Use 1 to 8, or leave blank.');
const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
assert.doesNotMatch(studio, /Parallel steps/, 'the old field name is gone');
assert.match(studio, /\['wall_clock_seconds','Working seconds'\]/);
assert.match(studio, /A <strong>Stop<\/strong> limit is firm: once it is reached, no new step starts, and steps already working stop asking the AI and write up what they have\. <strong>Working seconds<\/strong> add up the time of every step: two steps working together for 10 seconds count 20\./);
assert.match(studio, /stepsAtOnceMarkup\(/, 'Limits and approvals uses the field');
assert.match(studio, /pauseControl\(/, 'the pause control uses the new names');
assert.match(studio, /pauseNotice\(/, 'and the toast says how many steps will finish');

console.log('PASS: several steps at once — held, pausing, per-step outcomes, the token-limit stop, and Steps at once.');
