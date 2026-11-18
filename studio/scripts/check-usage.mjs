// What a run used, step by step, and warnings before a limit, as Studio draws
// them. The mockups are docs/studio/mockups/usage.html.
// Offline: no browser, backend or provider.
//
//   node studio/scripts/check-usage.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const url = path => new URL(path, import.meta.url).href;
const load = async (path, replacements = []) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, target] of replacements) source = source.replaceAll(`'${specifier}'`, JSON.stringify(target));
  return 'data:text/javascript,' + encodeURIComponent(source);
};
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const shared = url('../shared/definition.js'), canvasUi = url('../web/canvas-ui.js'), catalogue = url('../shared/catalogue.js');
const parallelUrl = await load('../web/parallel-ui.js', [['/definition.js', shared]]);
const activityUrl = await load('../web/run-activity.js', [['/canvas-ui.js', canvasUi], ['/parallel-ui.js', parallelUrl]]);
const common = [['/definition.js', shared], ['/canvas-ui.js', canvasUi], ['/run-activity.js', activityUrl], ['/parallel-ui.js', parallelUrl]];
const {projectLiveRun, liveRunMarkup} = await import(await load('../web/live.js', common));
const {runActivities} = await import(activityUrl);
const planUrl = await load('../web/plan.js', [['/definition.js', shared]]);
const runCanvasUrl = await load('../web/run-canvas.js', [...common]);
const {executionDetailMarkup} = await import(await load('../web/workbench-ui.js', [['/plan.js', planUrl], ['/catalogue.js', catalogue], ['/run-canvas.js', runCanvasUrl], ...common]));
const ui = await import(url('../web/usage-ui.js'));

const definition = {
  version: 1, name: 'Fix', description: '',
  agents: [{id: 'researcher', name: 'Researcher', role: 'custom', kind: 'ai', instructions: 'Research', tools: []},
           {id: 'writer', name: 'Writer', role: 'custom', kind: 'ai', instructions: 'Write', tools: []}],
  state: [{key: 'notes', label: 'Notes', type: 'text', merge: 'replace', statuses: []}],
  workflow: [{id: 'research', name: 'Research the <issue>', agent: 'researcher', task: 'Research', output: 'notes'},
             {id: 'draft', name: 'Draft the fix', agent: 'writer', task: 'Draft', output: 'notes'},
             {id: 'summary', name: 'Summarize', agent: 'writer', task: 'Summarize', output: 'notes'}],
  connections: [{from: 'research', to: 'draft'}, {from: 'draft', to: 'summary'}],
  views: [], inputs: [], run_config: {max_tool_steps: 8, max_notes: 12, max_note_chars: 300, max_tool_output_chars: 20000, fan_out_limit: 4},
  run_budget: {max_delegations: null, max_per_agent: null, max_depth: null, tool_calls: 60, wall_clock_seconds: null, tokens: 50000, cost_usd: null},
};
const step = (model_calls, extra = {}) => ({model_calls, measured_calls: model_calls, member_calls: 0, input_tokens: 0, output_tokens: 0,
  tool_calls: 0, tool_failures: 0, tool_denied: 0, seconds: 0, runs: 1, unmeasured: [], ...extra});
const usage = {
  recorded: true,
  steps: {
    research: step(7, {input_tokens: 21400, output_tokens: 3100, tool_calls: 12, tool_failures: 1, seconds: 108}),
    draft: step(5, {member_calls: 2, input_tokens: 12900, output_tokens: 3800, tool_calls: 6, tool_denied: 1, seconds: 125, runs: 2}),
    summary: step(2, {measured_calls: 0, seconds: 22, unmeasured: ['opencode']}),
  },
  total: {model_calls: 14, measured_calls: 12, member_calls: 2, input_tokens: 34300, output_tokens: 6900, tokens: 41200,
          tool_calls: 18, tool_failures: 1, tool_denied: 1, seconds: 255, cost_usd: null},
  unmeasured_backends: ['opencode'],
  limits: {tokens: {limit: 50000, used: 41200, warn_at: 40000}, tool_calls: {limit: 60, used: 18, warn_at: 48}},
  warnings: [{dimension: 'tokens', used: 41200, threshold: 40000, limit: 50000, automatic: true, stage: 'draft'}],
};

// --- Numbers, in words ------------------------------------------------------------------
assert.equal(ui.durationText(108), '1 m 48 s');
assert.equal(ui.durationText(125), '2 m 05 s');
assert.equal(ui.durationText(22.4), '22 s');
assert.equal(ui.durationText(0.004), 'under 1 s');
assert.equal(ui.durationText(3725), '1 h 02 m');
assert.equal(ui.countText(41200), '41,200');

// --- The run's usage, step by step --------------------------------------------------------
const run = {definition, usage, metrics: {tokens: 41200, tool_calls: 18}};
const section = ui.usageMarkup(run, {escape});
assert.match(section, /<table class="usage-table">/);
assert.match(section, /<th>Step<\/th><th>Model calls<\/th><th>Tokens in<\/th><th>Tokens out<\/th><th>Tool calls<\/th><th>Time<\/th>/);
assert.match(section, /<tr><td>Research the &lt;issue&gt;<\/td><td>7<\/td><td>21,400<\/td><td>3,100<\/td><td>12<span class="note">1 failed<\/span><\/td><td>1 m 48 s<\/td><\/tr>/, 'names are escaped; failures noted');
assert.match(section, /<td>Draft the fix<span class="note">ran twice<\/span><\/td><td>5<span class="note">2 by a member it asked<\/span><\/td>/);
assert.match(section, /<td>6<span class="note">1 refused<\/span><\/td>/);
assert.match(section, /<td colspan="2">not measured by opencode<\/td>/, 'a backend that did not measure is named');
assert.match(section, /<tfoot><tr><td>Run<\/td><td>14<\/td><td>at least 34,300<\/td><td>at least 6,900<\/td><td>18<\/td><td>4 m 15 s<\/td><\/tr><\/tfoot>/);
assert.match(section, /Tokens: at least 41,200 — 12 of 14 calls measured\. Cost is not reported by these backends\./);
assert.match(section, /<div class="limit-list" aria-label="Limits">/);
assert.match(section, /<div class="limit-bar close" role="img" aria-label="41,200 of 50,000 tokens, 82%, warned at 80%"><span style="width:82%"><\/span><i style="left:80%"><\/i><\/div><span class="limit-value">41,200 \/ 50,000<\/span>/);
assert.match(section, /<span>Tool uses<\/span><div class="limit-bar" role="img" aria-label="18 of 60 tool uses, 30%, warns at 80%"><span style="width:30%"><\/span><i style="left:80%"><\/i><\/div>/);
assert.doesNotMatch(section, /Working time<\/span>/, 'only limits the application set');
const over = ui.usageMarkup({...run, usage: {...usage, limits: {tokens: {limit: 50000, used: 61000, warn_at: null}}}}, {escape});
assert.match(over, /aria-label="61,000 of 50,000 tokens, 122%"><span style="width:100%"><\/span><\/div>/, 'a bar never overflows, and has no tick without a warning');

const measured = ui.usageMarkup({...run, usage: {...usage, total: {...usage.total, measured_calls: 14}, steps: {research: usage.steps.research}}}, {escape});
assert.match(measured, /Tokens: 41,200 — all 14 calls measured\./);
assert.doesNotMatch(measured, /at least/);
const none = ui.usageMarkup({...run, usage: {...usage, total: {...usage.total, measured_calls: 0, tokens: 0}, steps: {summary: usage.steps.summary}}}, {escape});
assert.match(none, /Tokens were not measured by opencode\./);
const costed = ui.usageMarkup({...run, usage: {...usage, total: {...usage.total, cost_usd: 1.5}}}, {escape});
assert.match(costed, /Cost: \$1\.50\./);

// --- A run from before usage was recorded -------------------------------------------------
const old = ui.usageMarkup({definition, usage: {recorded: false, steps: {}, total: {tool_calls: 9, seconds: 182, tokens: null, model_calls: null}, limits: {}, warnings: [], unmeasured_backends: []}}, {escape});
assert.equal(old, '<p class="field-help">Per-step usage was not recorded for this run. The run as a whole: 9 tool uses, 3 m 02 s working time; tokens not reported.</p>');
assert.match(ui.usageMarkup({definition, usage: null, metrics: {}}, {escape}), /Usage was not reported for this run\./);

// --- One step, in its details -------------------------------------------------------------
const draft = ui.stepUsageMarkup(run, 'draft', {escape, revisions: 1});
assert.match(draft, /<div class="step-usage"><div><strong>5<\/strong><span>model calls<\/span><em>2 by a member it asked<\/em><\/div><div><strong>16,700<\/strong><span>tokens<\/span><em>all measured<\/em><\/div><div><strong>6<\/strong><span>tool calls<\/span><em>1 refused<\/em><\/div><div><strong>2 m 05 s<\/strong><span>working time<\/span><\/div><\/div>/);
assert.match(draft, /<p class="field-help">Ran twice: the reviewer asked for changes once\.<\/p>/);
assert.match(ui.stepUsageMarkup(run, 'draft', {escape}), /Ran twice\. These numbers add up every time it ran\./);
assert.match(ui.stepUsageMarkup(run, 'summary', {escape}), /<strong>–<\/strong><span>tokens<\/span><em>not measured by opencode<\/em>/);
assert.equal(ui.stepUsageMarkup(run, 'missing', {escape}), '', 'a step that has not run has no usage');
assert.equal(ui.stepUsageMarkup({definition, usage: {recorded: false, steps: {}}}, 'draft', {escape}), '');

// --- A warning, once ------------------------------------------------------------------------
const warning = ui.usageWarningMarkup(run, {escape});
assert.match(warning, /<div class="usage-warning" role="status"><svg aria-hidden="true"[^>]*>.*<\/svg><span><strong>Close to the token limit:<\/strong> 41,200 of 50,000 tokens \(82%\)\. Once it is reached, no new step starts\.<\/span><\/div>/s);
assert.equal(warning.match(/usage-warning/g).length, 1);
const tools = ui.usageWarningMarkup({...run, usage: {...usage, warnings: [{dimension: 'tool_calls', used: 48, threshold: 48, limit: 60, automatic: true}], limits: {tool_calls: {limit: 60, used: 50, warn_at: 48}}}}, {escape});
assert.match(tools, /Close to the tool-use limit:<\/strong> 50 of 60 tool uses \(83%\)\. Once it is reached, further tool uses are refused\./);
assert.equal(ui.usageWarningMarkup({...run, budgetStop: {dimension: 'tokens'}}, {escape}), '', 'a run the limit stopped says so elsewhere');
assert.equal(ui.usageWarningMarkup({definition, usage: null}, {escape}), '');

// --- Activity -------------------------------------------------------------------------------
const activity = runActivities(definition, {events: [
  {type: 'ModelCalled', stage: 'draft', agent: 'writer', provider: 'opencode', model: 'm', input_tokens: 10, output_tokens: 2, seconds: 1, sequence: 1},
  {type: 'BudgetWarning', stage: 'draft', agent: 'writer', dimension: 'tokens', used: 41200, threshold: 40000, limit: 50000, automatic: true, sequence: 2},
  {type: 'BudgetWarning', stage: 'draft', dimension: 'tokens', used: 45, threshold: 40, limit: null, automatic: false, sequence: 3},
]}, {agentName: (d, id) => id, capabilityName: id => id});
assert.equal(activity.length, 2, 'each model call is not a row of its own');
assert.equal(activity[1].text, 'Close to the token limit · 82%');
assert.equal(activity[1].detail, '41,200 of 50,000 tokens');
assert.equal(activity[0].text, 'Token use passed its warning level');
assert.equal(activity[0].detail, '45 tokens; warns at 40');

// --- In the run information and the step details --------------------------------------------
const record = {id: 'run-1', status: 'completed', request: {definition, task: 'Fix it', inputs: {}, revision: 2}, reviews: [], usage,
  events: [{sequence: 1, event: {type: 'StageStarted', stage: 'research', agent: 'researcher'}},
           {sequence: 2, event: {type: 'ModelCalled', stage: 'research', agent: 'researcher', provider: '', model: '', input_tokens: 1, output_tokens: 1, seconds: 1}},
           {sequence: 3, event: {type: 'StageFinished', stage: 'research', agent: 'researcher', status: 'completed', usage: usage.steps.research}},
           {sequence: 4, event: {type: 'ApplicationFinished', status: 'completed'}}], metrics: {tokens: 41200}};
const projected = projectLiveRun(record);
assert.deepEqual(projected.usage, usage, 'the live run keeps the record’s usage');
assert.ok(!projected.events.some(event => event.type === 'ModelCalled'), 'model calls are in usage, so the live run does not carry them');

// Reading a long run costs no more for recording each model call: the live
// run drops them once, rather than every view passing over them.
{
  const long = calls => {
    const events = [];
    let sequence = 0;
    for (let index = 0; index < 20000; index++) {
      if (calls) events.push({sequence: sequence++, event: {type: 'ModelCalled', stage: 'research', agent: 'researcher', provider: 'codex', model: 'm', input_tokens: 1, output_tokens: 1, seconds: 1}});
      events.push({sequence: sequence++, event: {type: 'ToolCalled', stage: 'research', agent: 'researcher', tool: 'look', ok: true}});
    }
    return {...record, status: 'running', events};
  };
  const median = run => { const times = []; for (let index = 0; index < 7; index++) { const start = performance.now(); run(); times.push(performance.now() - start); } return times.sort((a, b) => a - b)[3]; };
  const without = long(false), withCalls = long(true);
  median(() => projectLiveRun(without)); median(() => projectLiveRun(withCalls));
  const plain = median(() => projectLiveRun(without)), recorded = median(() => projectLiveRun(withCalls));
  assert.ok(recorded < plain * 1.6 + 5, `projecting 20,000 model calls took ${recorded.toFixed(1)} ms against ${plain.toFixed(1)} ms without them`);
}
const markup = liveRunMarkup(definition, projected, {escape, requestForm: '', outputs: '', human: '', progress: '', runtime: {}, busy: false, error: '',
  usageMarkup: value => `<p>USAGE ${value.usage.total.model_calls}</p>`});
assert.match(markup, /<summary>Usage<\/summary><p>USAGE 14<\/p>/);
assert.doesNotMatch(markup, /Not reported/);
const details = executionDetailMarkup(definition, projected, 'research', {escape, agentName: (d, id) => id, outputName: (d, id) => id, capabilityName: id => id,
  usageMarkup: (value, stepId) => `<p>STEP USAGE ${stepId}</p>`});
assert.match(details, /<p>STEP USAGE research<\/p>/);
assert.match(details, /Step activity · 2</, 'model calls are counted in usage, not listed as activity');

// --- Nothing a record holds is drawn as markup --------------------------------------------
const hostile = '<img src=x onerror=alert(1)>';
const attacked = {definition: {workflow: [{id: 'x', name: hostile}]}, usage: {...usage,
  steps: {x: step(2, {measured_calls: 0, unmeasured: [hostile], tool_failures: 1})},
  total: {...usage.total, measured_calls: 0}, unmeasured_backends: [hostile],
  limits: {[hostile]: {limit: 1, used: 1, warn_at: 0}, tokens: {limit: 100, used: '"><script>', warn_at: 80}},
  warnings: [{dimension: hostile, used: 1, limit: 2}, {dimension: 'tokens', used: '<b>', limit: 100, threshold: 80}]}};
for (const markup of [ui.usageMarkup(attacked, {escape}), ui.stepUsageMarkup(attacked, 'x', {escape}), ui.usageWarningMarkup(attacked, {escape})]) {
  assert.doesNotMatch(markup, /<img|<script|<b>|onerror=alert\(1\)>/, 'names, backends and numbers are escaped or coerced');
}
assert.match(ui.usageMarkup(attacked, {escape}), /not measured by &lt;img src=x onerror=alert\(1\)&gt;/);
assert.equal(ui.usageWarningMarkup({...attacked, usage: {...attacked.usage, warnings: [{dimension: hostile, used: 1, limit: 2}]}}, {escape}), '', 'an unknown limit is not shown');

// --- Studio wires it in --------------------------------------------------------------------
const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
assert.match(studio, /import \{[^}]*usageMarkup[^}]*\} from '\/usage-ui\.js';/);
assert.match(studio, /usageMarkup:run=>usageMarkup\(run,\{escape\}\)/);
assert.match(studio, /usageWarningMarkup\(/);
assert.match(studio, /stepUsageMarkup\(/);
const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
for (const rule of ['.usage-table', '.limit-list', '.limit-row', '.limit-bar', '.limit-bar.close', '.usage-warning', '.step-usage'])
  assert.ok(css.includes(rule + '{') || css.includes(rule + ' ') || css.includes(rule + ','), `studio.css has ${rule}`);
console.log('usage checks passed');
