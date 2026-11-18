// Application versions, as Studio lists, compares and restores them. The mockups
// are docs/studio/mockups/versions.html. Offline: no browser, backend or provider.
//
//   node studio/scripts/check-versions.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const url = path => new URL(path, import.meta.url).href;
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const ui = await import(url('../web/versions-ui.js'));

// --- The list -------------------------------------------------------------------------
const data = {current: 7, versions: [
  {version: 7, current: true, restored_from: 5, saved_by: 'ana@acme.com', saved_at: '2026-10-20T14:12:00Z', runs: 2, summary: 'Step “Review”: people who must answer was 1, now 3 · 1 more change'},
  {version: 6, saved_by: 'raj@ops.acme.com', saved_at: '2026-10-19T09:40:00Z', runs: 5, summary: 'Added step'},
  {version: 5, saved_by: 'ana@acme.com', saved_at: '2026-10-17T16:03:00Z', runs: 0, summary: 'Member “writer”: instructions changed'},
  {version: 3, recovered_from_run: 'run-9', recovered_started_by: 'eve@acme.com', saved_by: null, saved_at: null, runs: 1, summary: 'Recovered'},
]};
assert.deepEqual(ui.versionGaps(data.versions), [{after: 5, missing: [4]}, {after: 3, missing: [1, 2]}], 'gaps sit where they are');
const list = ui.versionsListMarkup(data, {escape, name: 'Release <notes>', chosen: [7, 6]});
assert.match(list, /<h2 id="versions-title">Versions of Release &lt;notes&gt;<\/h2>/);
assert.match(list, /<li class="current"><input type="checkbox" data-version-choose="7" aria-label="Compare version 7" checked >/);
assert.match(list, /<span class="version-tag current">Current<\/span><span class="version-tag">Restored from 5<\/span>/);
assert.match(list, /data-version-choose="5" aria-label="Compare version 5"  disabled>/, 'with two chosen, the others wait');
assert.match(list, /data-version-runs="6">5 runs<\/button>/);
assert.match(list, /<span class="version-runs">No runs<\/span>/);
assert.match(list, /Version 5<\/span><\/span>.*<li class="version-gap">.*Version 4 was not kept.*Version 3/s, 'the gap between 5 and 3');
assert.match(list, /Versions 1 and 2 were not kept/);
assert.match(list, /Recovered from a run<\/span>.*Saved before versions were kept · recovered from a run started by eve@acme\.com/s, 'where a recovered version came from, and nothing more');
assert.match(list, /data-version-compare >Compare version 6 with 7<\/button>/);
const one = ui.versionsListMarkup(data, {escape, name: 'x', chosen: [7]});
assert.match(one, /data-version-compare disabled>Choose two versions to compare</);
assert.match(one, /One version chosen; choose one more\./);
assert.doesNotMatch(one, /disabled>\s*<span><span class="version-title">Version 6/, 'with one chosen, every box can be chosen');

// --- Two compared ----------------------------------------------------------------------------
const comparison = {from: 5, to: 7, count: 3, truncated: 0, layout_changed: true, areas: [
  {area: 'Steps', changes: [{kind: 'added', text: 'Step “Check <the> changelog” (Writer)', item: 'check'},
                            {kind: 'changed', text: 'Step “Review”: people who must answer was 1, now 3', subject: 'Step “Review”: people who must answer', was: '1', now: '3'}]},
  {area: 'Review policy', changes: [{kind: 'changed', text: 'The person who started a run may answer its reviews: was yes, now no'}]},
]};
const compared = ui.comparisonMarkup(comparison, {escape, current: 7, definitions: {from: {name: 'Five'}, to: {name: 'Seven'}}});
assert.match(compared, /<h2 id="versions-title">Version 5 → version 7<\/h2><p>3 changes in 2 areas\. Card positions also changed\.<\/p>/);
assert.match(compared, /<span class="change-kind added">Added<\/span><span>Step “Check &lt;the&gt; changelog” \(Writer\)<\/span>/);
assert.match(compared, /Step “Review”: people who must answer: was <span class="was">1<\/span>, now <span class="now">3<\/span>/, 'was and now in words, not colour alone');
assert.match(compared, /data-version-restore="5">Restore version 5 as a new version</);
assert.match(compared, /<pre class="code">\{\n  &quot;name&quot;: &quot;Five&quot;\n\}<\/pre>/, 'both definitions are one disclosure away');
assert.doesNotMatch(ui.comparisonMarkup({...comparison, from: 6, to: 7}, {escape, current: 6}), /data-version-restore/, 'the current version is not restored onto itself');
assert.match(ui.comparisonMarkup({from: 6, to: 7, count: 0, truncated: 0, layout_changed: true, areas: []}, {escape, current: 7}), /No change to what it does\. Card positions changed\./);
assert.match(ui.comparisonMarkup({...comparison, count: 250, truncated: 50}, {escape, current: 7}), /The first 200 are shown\./);

// --- Restoring ---------------------------------------------------------------------------------
const confirm = ui.restoreConfirmMarkup({version: 5, current: 7, changes: 3, unsaved: true, scheduled: true}, {escape});
assert.match(confirm, /role="alertdialog" aria-labelledby="restore-title"><strong id="restore-title">Restore version 5 as version 8\?<\/strong>/);
assert.match(confirm, /Version 8 will have version 5’s team, steps and settings \(3 changes from the current version 7\)\. Versions 6 and 7 stay in the history\./);
assert.match(confirm, /the schedule uses version 8 from its next run/);
assert.match(confirm, /<strong>You have unsaved edits\.<\/strong> Restoring replaces them\. Save them first to keep them as version 8, and restore as version 9\./);
assert.match(confirm, /data-version-save-first>Save my edits first/);
const plain = ui.restoreConfirmMarkup({version: 6, current: 7, changes: 1, unsaved: false, scheduled: false}, {escape});
assert.match(plain, /Version 7 stays in the history\. Runs already going keep the version they started with\.<\/p>/);
assert.doesNotMatch(plain, /unsaved|schedule/);
assert.match(ui.restoreConfirmMarkup({version: 2, current: 7, changes: 9, unsaved: false}, {escape}), /Versions 3 to 7 stay in the history\./);
const recovered = ui.restoreConfirmMarkup({version: 3, current: 7, changes: 4, unsaved: false, recovered: true, startedBy: 'eve@acme.com'}, {escape});
assert.match(recovered, /<strong>Conclave cannot confirm version 3 was ever saved here\.<\/strong> It was recovered from a run started by eve@acme\.com; check what it holds before you restore it\./);
assert.match(recovered, /data-version-restore-confirm="3" data-version-recovered>Restore this recovered version as version 8</);
assert.doesNotMatch(plain, /cannot confirm|data-version-recovered/);

// What a restore would loosen or tighten is named before anyone confirms it.
const restoring = {from: 7, to: 4, count: 5, truncated: 0, layout_changed: false, areas: [
  {area: 'Steps', changes: [{kind: 'changed', text: 'Step “Approve”: condition was “…”, now none'}, {kind: 'changed', text: 'Step “Draft”: task changed'}]},
  {area: 'Limits and approvals', changes: [{kind: 'changed', text: 'Autonomous execution was off, now on'}]},
  {area: 'Review policy', changes: [{kind: 'removed', text: 'Review policy removed: anyone may answer, including the person who started a run'}]},
  {area: 'Team', changes: [{kind: 'changed', text: 'Member “Writer”: tools: added git_commit'}]},
]};
assert.deepEqual(ui.sensitiveChanges(restoring), ['Step “Approve”: condition was “…”, now none', 'Autonomous execution was off, now on',
  'Review policy removed: anyone may answer, including the person who started a run', 'Member “Writer”: tools: added git_commit']);
const warned = ui.restoreConfirmMarkup({version: 4, current: 7, changes: 5, unsaved: false, sensitive: ui.sensitiveChanges(restoring)}, {escape});
assert.match(warned, /<div class="restore-sensitive" role="note"><strong>This restore also changes what keeps the team in check:<\/strong><ul><li>Step “Approve”: condition was “…”, now none<\/li><li>Autonomous execution was off, now on<\/li>/);
assert.doesNotMatch(plain, /restore-sensitive/, 'nothing to name, nothing said');

// --- What a run ran --------------------------------------------------------------------------------
assert.equal(ui.runVersionMarkup({revision: 7}, 7, {escape}), '<p>Ran saved version 7 · <button type="button" class="text-button" data-version-open="7">View this version</button></p>');
assert.match(ui.runVersionMarkup({revision: 6}, 7, {escape}), /Not the current version: the application is at version 7\. <button type="button" class="text-button" data-version-compare-pair="6,7">Compare 6 with 7<\/button>/);
assert.equal(ui.runVersionMarkup({}, 7, {escape}), '');

// Studio wires it: the Application menu opens Versions, and a run's information names its version.
const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const workbench = await readFile(new URL('../web/workbench-ui.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../web/index.html', import.meta.url), 'utf8');
assert.match(workbench, /data-action="versions">Versions<\/button>/);
assert.match(studio, /if\(action==='versions'\)\{await openVersions\(\);return;\}/);
assert.match(studio, /versionMarkup:run=>runVersionMarkup\(run,current\(\)\.revision,\{escape\}\)/);
assert.match(html, /<dialog id="versions-dialog" aria-labelledby="versions-title"><button id="close-versions" class="icon-button" aria-label="Close versions">/);

console.log('Versions list, compare, restore and link runs as designed.');
