// Two runs of one application, compared step by step, as Studio draws them.
// The mockups are docs/studio/mockups/run-compare.html.
// Offline: no browser, backend or provider.
//
//   node studio/scripts/check-run-compare.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const url = path => new URL(path, import.meta.url).href;
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const ui = await import(url('../web/run-compare-ui.js'));
const time = iso => `T(${iso})`;

// --- Choosing two runs in Run history ---------------------------------------------------------
const run = {id: 'run-1', created_at: '2026-10-21T09:12:40Z', status: 'completed'};
assert.equal(ui.historyPickMarkup(run, [], {escape, time}),
  '<td class="history-pick"><input type="checkbox" data-run-choose="run-1" aria-label="Compare the run started T(2026-10-21T09:12:40Z)" ></td>');
assert.match(ui.historyPickMarkup(run, ['run-1'], {escape, time}), /aria-label="[^"]*" checked>/);
assert.match(ui.historyPickMarkup(run, ['run-2', 'run-3'], {escape, time}), /aria-label="[^"]*" disabled>/, 'with two chosen, the others wait');
assert.equal(ui.historyPickMarkup({id: 'x"><b>', created_at: ''}, [], {escape, time}).includes('<b>'), false);
assert.match(ui.compareBarMarkup([], {escape}), /<button class="button primary" data-action="compare-runs" aria-describedby="compare-hint" disabled>Compare 2 runs<\/button><span class="field-help" id="compare-hint" role="status">Choose two runs to compare\.<\/span>/);
assert.match(ui.compareBarMarkup(['a'], {escape}), /disabled>.*One run chosen; choose one more\./s);
assert.match(ui.compareBarMarkup(['a', 'b'], {escape}), /data-action="compare-runs" aria-describedby="compare-hint" >Compare 2 runs<\/button>.*Two runs chosen\. Clear one to choose another\./s);

// --- The comparison -----------------------------------------------------------------------------
const statusOf = (a, b, reasons = {}) => ({a: {status: a, reason: reasons.a || ''}, b: {status: b, reason: reasons.b || ''}});
const comparison = {
  application_id: 'release', name: 'Release <notes>',
  runs: {a: {id: 'ra', created_at: '2026-10-18T11:05:17Z', status: 'completed', version: 5, started_by: 'ana@acme.com', trigger: {kind: 'schedule'}, task: 'Notes 4.1', in_progress: false},
         b: {id: 'rb', created_at: '2026-10-21T09:12:40Z', status: 'completed', version: 7, started_by: 'raj@ops.acme.com', trigger: {kind: 'manual'}, task: 'Notes 4.2', in_progress: false}},
  version: {a: 5, b: 7, same: false, link: '/api/applications/release/versions/compare?from=5&to=7',
            changes: {count: 3, truncated: 0, areas: [{area: 'Steps', changes: [{kind: 'added', text: 'Step “Check the changelog” (Writer)'},
              {kind: 'changed', text: 'Step “Approve”: people who must answer was 1, now 3', subject: 'Step “Approve”: people who must answer', was: '1', now: '3'}]}]}},
  request: {task: {a: 'Notes 4.1', b: 'Notes 4.2'}, inputs: [{key: 'milestone', label: 'Milestone', a: '4.1', b: '4.2'}]},
  steps: [
    {id: 'draft', name: 'Draft the notes', names: {a: 'Draft the notes', b: 'Draft the notes'}, agent: 'Writer', presence: 'both', kind: 'ai', status: statusOf('completed', 'completed'),
     output: {kind: 'text', more: 0, lines: [{kind: 'gap', text: '', count: 14}, {kind: 'same', text: '## Highlights'}, {kind: 'del', text: 'Faster sync.'}, {kind: 'add', text: 'Faster <sync> (3×).'}]},
     review: null, usage: {a: {model_calls: 5, tokens: 13700, tool_calls: 6, seconds: 100, measured: true}, b: {model_calls: 6, tokens: 16700, tool_calls: 6, seconds: 125, measured: true},
       delta: {model_calls: 1, tokens: 3000, tokens_percent: 22, tool_calls: 0, seconds: 25}},
     tags: [{kind: 'output_changed', text: 'Output changed'}, {kind: 'usage', text: 'Usage +22%'}], differs: true},
    {id: 'check', name: 'Check the changelog', names: {a: null, b: 'Check the changelog'}, agent: 'Writer', presence: 'only_b', kind: 'ai', status: statusOf('not_in_version', 'completed'),
     output: null, review: null, usage: null, tags: [{kind: 'only_in_b', text: 'Only in B'}], differs: true},
    {id: 'investigate', name: 'Investigate regressions', names: {a: 'Investigate regressions', b: 'Investigate regressions'}, agent: 'Engineer', presence: 'both', kind: 'ai',
     status: statusOf('completed', 'skipped', {b: '“Triage” found no regression'}), output: null, review: null, usage: null, tags: [{kind: 'skipped_only_in_b', text: 'Skipped only in B'}], differs: true},
    {id: 'triage', name: 'Triage', names: {a: 'Triage', b: 'Triage'}, agent: 'Triager', presence: 'both', kind: 'ai', status: statusOf('completed', 'completed'),
     output: {kind: 'records', count: 40, more: 37, changes: [{id: 'issue-212', title: 'Crash', kind: 'added', fields: []}, {id: 'issue-180', title: 'Slow export', kind: 'removed', fields: []},
       {id: 'issue-201', title: 'Login <loop>', kind: 'changed', fields: [{field: 'status', label: 'Status', was: 'open', now: 'fixed'}, {field: 'body', label: 'Body', was: null, now: null}]}]},
     review: null, usage: null, tags: [{kind: 'output_changed', text: 'Output changed'}], differs: true},
    {id: 'approve', name: 'Approve the release', names: {a: 'Approve the release', b: 'Approve the release'}, agent: 'Priya', presence: 'both', kind: 'human', status: statusOf('completed', 'completed'),
     output: null, review: {a: {decision: 'approved', reviewers: ['priya@acme.com'], rounds: 1, changes_requested: false, comment: 'Looks good.'},
                            b: {decision: 'approved', reviewers: ['priya@acme.com', 'omar@acme.com'], rounds: 2, changes_requested: true, comment: 'Good now.', first_comment: 'Mention the <limit>.'}},
     usage: null, tags: [{kind: 'review_differs', text: 'Review differs'}], differs: true},
    {id: 'publish', name: 'Publish', names: {a: 'Publish', b: 'Publish'}, agent: 'Writer', presence: 'both', kind: 'ai', status: statusOf('completed', 'completed'),
     output: null, review: null, usage: {a: null, b: {model_calls: 1, tokens: 0, tool_calls: 0, seconds: 3, measured: false}, delta: null}, tags: [], differs: false},
  ],
  summary: {steps: 6, differing: 5, text: '5 of 6 steps differ: 2 outputs changed, “Check the changelog” ran only in B, “Investigate regressions” was skipped in B, and a review decision differs. B used 18% more tokens.'},
};
const markup = ui.runComparisonMarkup(comparison, {escape, time});
assert.match(markup, /<h2 id="compare-title" tabindex="-1">Compare runs of Release &lt;notes&gt;<\/h2>/, 'focus lands on the heading');
assert.match(markup, /<div class="compare-run"><span class="run-letter" aria-hidden="true">A<\/span><strong>Run A · T\(2026-10-18T11:05:17Z\)<\/strong><p>Completed · version 5 · Scheduled · started by ana@acme\.com<\/p><\/div>/);
assert.match(markup, /<button class="button compare-swap" data-action="swap-compare" aria-label="Swap A and B" title="Swap A and B">.*<span>Swap<\/span><\/button>/s);
assert.match(markup, /<strong>Run B · T\(2026-10-21T09:12:40Z\)<\/strong><p>Completed · version 7 · started by raj@ops\.acme\.com<\/p>/);
assert.match(markup, /<p class="compare-summary" role="status"><strong>5 of 6 steps differ:<\/strong> 2 outputs changed/);
assert.match(markup, /<section class="compare-block"><h3>The runs used different versions<\/h3><ul class="change-list"><li><span class="change-kind added">Added<\/span><span>Step “Check the changelog” \(Writer\)<\/span><\/li><li><span class="change-kind changed">Changed<\/span><span>Step “Approve”: people who must answer: was <span class="was">1<\/span>, now <span class="now">3<\/span><\/span><\/li><\/ul><p class="field-help">3 changes between version 5 and 7 · <button class="text-button" type="button" data-compare-versions="5,7">See every change<\/button><\/p><\/section>/);
assert.match(markup, /<h3>The requests differed<\/h3><div class="compare-columns"><div><span class="side-label">A · Task<\/span>Notes 4\.1<\/div><div><span class="side-label">B · Task<\/span>Notes 4\.2<\/div><\/div><p class="field-help">Request values: “Milestone” was 4\.1, now 4\.2\.<\/p>/);
assert.match(markup, /<label class="compare-filter"><input type="checkbox" data-compare-filter checked> Only steps that differ <span class="field-help">· 1 the same, hidden<\/span><\/label>/);
assert.doesNotMatch(markup, /data-compare-step="publish"/, 'the same step is hidden while filtering');
assert.match(ui.runComparisonMarkup(comparison, {escape, time, onlyDiffering: false}), /data-compare-step="publish"/);

// One step row: status in words for each run, and tags.
assert.match(markup, /<li><details class="step-compare" data-compare-step="draft" ><summary><span class="step-title">Draft the notes<small>Writer<\/small><\/span><span class="side"><span class="side-label">A<\/span>Completed<\/span><span class="side"><span class="side-label">B<\/span>Completed<\/span><span class="compare-tags"><span class="compare-tag">Output changed<\/span><span class="compare-tag">Usage \+22%<\/span><\/span><\/summary>/);
assert.match(markup, /<span class="step-title">Check the changelog<small>Writer · only in version 7<\/small><\/span><span class="side"><span class="side-label">A<\/span>Not in this version<\/span>/);
assert.match(markup, /<span class="side-label">B<\/span>Skipped<p>“Triage” found no regression<\/p><\/span><span class="compare-tags"><span class="compare-tag skipped">Skipped only in B<\/span>/);
assert.match(markup, /<span class="side-label">B<\/span>Changes requested, then approved<p>2 rounds<\/p><\/span>/);

// A step's details are drawn only once it is opened: closed steps are one row each.
assert.doesNotMatch(markup, /step-compare-body/, 'no step is open, so no details are drawn');
const everyStep = new Set(comparison.steps.map(item => item.id));
const opened = ui.runComparisonMarkup(comparison, {escape, time, open: everyStep});
assert.equal(ui.runComparisonMarkup(comparison, {escape, time, open: new Set(['draft'])}).match(/step-compare-body/g).length, 1, 'only the open step');
assert.equal(ui.stepBodyMarkup(comparison, 'draft', {escape}), opened.match(/<div class="step-compare-body">.*?<\/section><\/div>(?=<\/details>)/s)[0], 'opening a step draws just its details');
assert.equal(ui.stepBodyMarkup(comparison, 'check', {escape}), '', 'a step with nothing more to show has no details');
assert.equal(ui.stepBodyMarkup(comparison, 'missing', {escape}), '');
// Inside a step: the text diff, marked in words as well as colour.
assert.match(opened, /<h4>Output<\/h4><pre class="text-diff" aria-label="Changes from A to B; − lines are only in A, \+ lines only in B"><span class="gap">  … 14 unchanged lines<\/span><span class="same">  ## Highlights<\/span><span class="del"><span class="sr-only">Only in A: <\/span>− Faster sync\.<\/span><span class="add"><span class="sr-only">Only in B: <\/span>\+ Faster &lt;sync&gt; \(3×\)\.<\/span><\/pre>/);
const coarse = ui.runComparisonMarkup({...comparison, steps: [{...comparison.steps[0], output: {...comparison.steps[0].output, coarse: true}}]}, {escape, time, open: everyStep});
assert.match(coarse, /<h4>Output<\/h4><p class="field-help">Too long to compare line by line; shown as one changed block\.<\/p><pre class="text-diff"/);
assert.doesNotMatch(opened, /Too long to compare/);
// Records, by id.
assert.match(opened, /<h4>Output · 40 records changed<\/h4><ul class="change-list"><li><span class="change-kind added">Added<\/span><span>“Crash” \(issue-212\)<\/span><\/li><li><span class="change-kind removed">Removed<\/span><span>“Slow export” \(issue-180\)<\/span><\/li><li><span class="change-kind changed">Changed<\/span><span>“Login &lt;loop&gt;” \(issue-201\): Status was <span class="was">open<\/span>, now <span class="now">fixed<\/span> · Body changed<\/span><\/li><\/ul><p class="compare-more">and 37 more changed records, not listed<\/p>/);
// A review, side by side.
assert.match(opened, /<h4>Review<\/h4><div class="compare-columns"><div><span class="side-label">A<\/span><strong>Approved<\/strong> by priya@acme\.com<p>1 round · “Looks good\.”<\/p><\/div><div><span class="side-label">B<\/span><strong>Approved<\/strong> by priya@acme\.com, omar@acme\.com<p>2 rounds · first asked for changes: “Mention the &lt;limit&gt;\.” · last: “Good now\.”<\/p><\/div><\/div>/);
// Usage, with the difference.
assert.match(opened, /<h4>Usage<\/h4><table class="usage-table"><thead><tr><th><span class="sr-only">Run<\/span><\/th><th>Model calls<\/th><th>Tokens<\/th><th>Tool calls<\/th><th>Time<\/th><\/tr><\/thead><tbody><tr><td>A<\/td><td>5<\/td><td>13,700<\/td><td>6<\/td><td>1 m 40 s<\/td><\/tr><tr><td>B<\/td><td>6<\/td><td>16,700<\/td><td>6<\/td><td>2 m 05 s<\/td><\/tr><tr class="delta"><td>Difference<\/td><td>\+1<\/td><td>\+3,000 \(\+22%\)<\/td><td>same<\/td><td>\+25 s<\/td><\/tr><\/tbody><\/table>/);
const all = ui.runComparisonMarkup(comparison, {escape, time, onlyDiffering: false, open: everyStep});
assert.match(all, /<tr><td>A<\/td><td colspan="4">Not recorded for this run<\/td><\/tr><tr><td>B<\/td><td>1<\/td><td>not measured<\/td>/, 'a run without usage says so, and no difference is made up');
assert.doesNotMatch(all.split('data-compare-step="publish"')[1], /Difference/);
// Steps that were open stay open when the comparison is drawn again.
assert.match(ui.runComparisonMarkup(comparison, {escape, time, open: new Set(['draft'])}), /data-compare-step="draft" open>/);

// The largest comparison draws quickly while its steps are closed.
{
  const lines = Array.from({length: 400}, (_, index) => ({kind: index % 2 ? 'add' : 'del', text: 'x'.repeat(120)}));
  const changes = Array.from({length: 200}, (_, index) => ({id: 'r' + index, title: 'R' + index, kind: 'changed', fields: [{field: 'status', label: 'Status', was: 'a', now: 'b'}]}));
  const steps = Array.from({length: 24}, (_, index) => ({...comparison.steps[0], id: 's' + index,
    output: index % 2 ? {kind: 'text', lines, more: 100, coarse: false} : {kind: 'records', count: 250, more: 50, changes}}));
  const largest = {...comparison, steps};
  for (let index = 0; index < 5; index++) ui.runComparisonMarkup(largest, {escape, time});
  const start = performance.now();
  for (let index = 0; index < 20; index++) ui.runComparisonMarkup(largest, {escape, time});
  const each = (performance.now() - start) / 20;
  assert.ok(each < 10, `drawing 24 closed steps took ${each.toFixed(1)} ms`);
  assert.ok(ui.runComparisonMarkup(largest, {escape, time}).length < 40000, 'closed steps are one row each');
}

// --- A run still going, and runs that cannot be compared ---------------------------------------------
const going = {...comparison, runs: {...comparison.runs, b: {...comparison.runs.b, status: 'running', in_progress: true}},
  steps: [{...comparison.steps[5], status: statusOf('completed', 'not_reached'), tags: [{kind: 'not_reached_in_b', text: 'Not reached in B'}], differs: true}]};
const goingMarkup = ui.runComparisonMarkup(going, {escape, time});
assert.match(goingMarkup, /<div class="usage-warning" role="status">.*<span><strong>Run B is still running\.<\/strong> It is compared as it stands now; steps it has not reached say so\.<\/span><\/div>/s);
assert.match(goingMarkup, /<span class="side-label">B<\/span>Not reached yet<\/span>/);
assert.match(goingMarkup, /<span class="compare-tag skipped">Not reached in B<\/span>/);
const same = ui.runComparisonMarkup({...comparison, version: {a: 7, b: 7, same: true, changes: null, link: null}, request: {task: null, inputs: []}, steps: [comparison.steps[5]],
  summary: {steps: 1, differing: 0, text: 'No step differs between the two runs.'}}, {escape, time});
assert.doesNotMatch(same, /different versions|requests differed/);
assert.match(same, /No step differs between the two runs\./);
assert.match(same, /<p class="field-help">Every step did the same in both runs\. <button class="text-button" type="button" data-compare-filter-off>Show all steps<\/button><\/p>/);
assert.equal(ui.compareErrorMarkup('These runs can’t be compared: they belong to different applications.', {escape}),
  '<h2 id="compare-title" tabindex="-1">Compare runs</h2><p class="validation" role="alert">These runs can’t be compared: they belong to different applications.</p><div class="actions"><button class="button" data-action="show-history">Back to Run history</button></div>');

// --- Nothing a run holds is drawn as markup ----------------------------------------------------------
const hostile = '<img src=x onerror=alert(1)>';
const attacked = {...comparison, name: hostile, runs: {a: {...comparison.runs.a, started_by: hostile, status: hostile}, b: comparison.runs.b},
  request: {task: {a: hostile, b: 'x'}, inputs: [{key: 'k', label: hostile, a: hostile, b: hostile}]},
  steps: comparison.steps.map(item => ({...item, name: hostile, agent: hostile, status: statusOf(hostile, 'skipped', {b: hostile}),
    tags: [{kind: hostile, text: hostile}]})), summary: {steps: 1, differing: 1, text: hostile}};
assert.doesNotMatch(ui.runComparisonMarkup(attacked, {escape, time}), /<img/);

// --- Studio wires it in -------------------------------------------------------------------------------
const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
assert.match(studio, /import \{[^}]*runComparisonMarkup[^}]*\} from '\/run-compare-ui\.js';/);
assert.match(studio, /\/api\/runs\/compare\?a=/);
for (const name of ['compare-runs', 'swap-compare']) assert.match(studio, new RegExp(`action==='${name}'`));
assert.match(studio, /historyPickMarkup\(/);
assert.match(studio, /stepBodyMarkup\(/, 'opening a step draws its details');
assert.match(studio, /compareBarMarkup\(/);
const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
assert.match(html, /<dialog id="compare-dialog" class="compare-dialog" aria-labelledby="compare-title">/);
const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
for (const rule of ['.history-pick', 'dialog.compare-dialog', '.compare-runs', '.compare-summary', '.step-compare', '.compare-tag', '.text-diff', '.compare-columns', '.compare-filter'])
  assert.ok(css.includes(rule + '{') || css.includes(rule + ' ') || css.includes(rule + ','), `studio.css has ${rule}`);
console.log('run comparison checks passed');
