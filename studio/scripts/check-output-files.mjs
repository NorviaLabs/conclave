// Stored output files, as Studio shows them. The mockups are
// docs/studio/mockups/output-files.html. Offline: no browser, backend or provider.
//
//   node studio/scripts/check-output-files.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const url = path => new URL(path, import.meta.url).href;
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const ui = await import(url('../web/output-files-ui.js'));
const {validateDefinition} = await import(url('../shared/definition.js'));

// --- Words and names ------------------------------------------------------------------------------
assert.equal(ui.fileSizeText(46), '46 B');
assert.equal(ui.fileSizeText(12700), '12.4 KB');
assert.equal(ui.fileSizeText(3.2 * 1024 * 1024), '3.2 MB');
const notes = {key: 'notes', label: 'Release notes: 4.2 / final!!', kind: 'document', type: 'text'};
const evidence = {key: 'evidence', label: 'Test and CI evidence', kind: 'dataset', type: 'records'};
const build = {key: 'implementation', label: 'Implementation', kind: 'artifact', type: 'records'};
assert.equal(ui.fileNameFor(notes), 'release-notes-4-2-final.md', 'the same name the host gives the file');
assert.equal(ui.fileNameFor({...notes, label: 'Éclair café'}), 'eclair-cafe.md');
assert.equal(ui.fileNameFor({...notes, label: '¿¡!!'}), 'notes.md');
assert.equal(ui.fileNameFor({...notes, label: 'x'.repeat(100)}), 'x'.repeat(60) + '.md');
assert.equal(ui.fileNameFor(evidence), 'test-and-ci-evidence.csv');
assert.equal(ui.fileNameFor({...evidence, file: {format: 'json'}}), 'test-and-ci-evidence.json');
assert.equal(ui.fileFormatFor(build), 'json');
assert.equal(ui.fileFormatFor({...evidence, file: false}), null);
assert.equal(ui.fileFormatFor({key: 'f', kind: 'findings', type: 'records'}), null);

// --- The definition says which file an output becomes -------------------------------------------------
const definition = {
  version: 1, name: 'Release', description: '',
  agents: [{id: 'writer', name: 'Writer', role: 'custom', kind: 'ai', instructions: 'Write', tools: []}],
  state: [{...notes, merge: 'replace', statuses: []}, {...evidence, merge: 'upsert', statuses: []}],
  workflow: [{id: 'draft', name: 'Draft the notes', agent: 'writer', task: 'Draft', output: 'notes'},
             {id: 'collect', name: 'Collect', agent: 'writer', task: 'Collect', output: 'evidence'}],
  connections: [{from: 'draft', to: 'collect'}],
  views: [{id: 'notes', collection: 'notes', title: 'Release notes', layout: 'prose'}, {id: 'evidence', collection: 'evidence', title: 'Evidence', layout: 'table'}],
  inputs: [], run_config: {max_tool_steps: 8, max_notes: 12, max_note_chars: 300, max_tool_output_chars: 20000, fan_out_limit: 4},
  run_budget: {max_delegations: null, max_per_agent: null, max_depth: null, tool_calls: null, wall_clock_seconds: null, tokens: null, cost_usd: null},
};
const withFile = (index, file) => { const copy = structuredClone(definition); copy.state[index].file = file; return copy; };
validateDefinition(withFile(1, {format: 'json'}));
validateDefinition(withFile(1, false));
for (const [index, file] of [[0, {format: 'csv'}], [1, {format: 'xlsx'}], [1, {format: 'csv', name: 'x'}], [1, true], [1, null]])
  assert.throws(() => validateDefinition(withFile(index, file)), /file/i, `state ${index} file ${JSON.stringify(file)} is refused`);

// --- A stored file -------------------------------------------------------------------------------------
const file = {id: 'f1', stage: 'draft', output: 'notes', round: 2, name: 'release-notes.md', format: 'markdown', media_type: 'text/markdown; charset=utf-8',
  size: 12700, created_at: '2026-10-21T09:12:40Z', preview: '# Release notes\n\n- Faster <sync>', lines: 214, link: '/api/runs/r1/files/f1', available: true};
const earlier = {...file, id: 'f0', round: 1, size: 12200};
const card = ui.fileCardMarkup(file, {escape, label: 'Release notes', earlier: [earlier]});
assert.match(card, /^<article class="file-card" aria-labelledby="file-f1"><div class="file-head"><span class="file-icon" aria-hidden="true">MD<\/span><span><span class="file-name" id="file-f1">release-notes\.md<\/span><span class="file-meta">Release notes · Markdown · 12\.4 KB · round 2<\/span><\/span><button class="button primary" data-file-download="f1" data-file-run="r1" aria-label="Download release-notes\.md" >/);
assert.match(card, /<span>Download<\/span><\/button><\/div><pre class="file-preview" tabindex="0" aria-label="First lines of release-notes\.md"># Release notes\n\n- Faster &lt;sync&gt;<\/pre><span class="file-preview-note">First 3 of 214 lines\. Download the file to read all of it\.<\/span>/);
assert.match(card, /<details class="file-versions"><summary>Earlier versions · 1<\/summary><ul><li><span>release-notes\.md · round 1 · 11\.9 KB<\/span><button class="text-button" data-file-download="f0" data-file-run="r1" aria-label="Download release-notes\.md, round 1">Download round 1<\/button><\/li><\/ul><\/details><\/article>$/);
assert.doesNotMatch(ui.fileCardMarkup({...file, lines: 3}, {escape, label: 'Release notes'}), /file-preview-note/, 'nothing more to read, nothing said');
assert.match(ui.fileCardMarkup(file, {escape, label: 'Release notes', busy: true}), /aria-label="Download release-notes\.md" disabled>.*<span>Downloading…<\/span>/s);
const refused = ui.fileCardMarkup(file, {escape, label: 'Release notes', error: true});
assert.match(refused, /disabled>/);
assert.match(refused, /<p class="validation" role="alert">The download was refused: this file is missing or was changed since the run stored it\.<\/p>/);
const missing = ui.fileCardMarkup({...file, available: false}, {escape, label: 'Release notes'});
assert.doesNotMatch(missing, /data-file-download="f1"/, 'a file that fails its check is not offered');
assert.match(missing, /<div class="file-problem" role="status"><svg[^>]*>.*<\/svg><span><strong>This file is missing or was changed<\/strong> since the run stored it, so it is not offered for download\. Run the step again to make a new one\.<\/span><\/div>/s);
assert.equal(ui.noFileMarkup('Implementation <and> PR', {escape}),
  '<article class="file-card" aria-labelledby="file-none-implementation-and-pr"><div class="file-head"><span class="file-icon" aria-hidden="true">—</span><span><span class="file-name" id="file-none-implementation-and-pr">Implementation &lt;and&gt; PR</span><span class="file-meta">No stored file for this run: it ran before results were stored as files.</span></span><span></span></div></article>');
// The step's own details: one compact card, its download named.
assert.match(ui.stepFileMarkup([earlier, file], 'draft', {escape}), /<h4>File<\/h4><article class="file-card" aria-labelledby="file-f1">.*<button class="button" data-file-download="f1" data-file-run="r1" aria-label="Download release-notes\.md" title="Download release-notes\.md" >/s);
assert.equal(ui.stepFileMarkup([file], 'other', {escape}), '');

// The latest file per step, and the rounds before it.
assert.deepEqual(ui.filesByStage([file, earlier, {...file, id: 'g', stage: 'collect', round: 1}]), {
  draft: {latest: file, earlier: [earlier]}, collect: {latest: {...file, id: 'g', stage: 'collect', round: 1}, earlier: []}});

// --- Setting it up, and testing a step ---------------------------------------------------------------
const field = ui.storedFileFieldMarkup(evidence, 1, {escape});
assert.match(field, /<div class="field"><label for="output-file-1">Stored file<\/label><select id="output-file-1" data-file-format="1" aria-describedby="output-file-1-help"><option value="csv" selected>CSV \(\.csv\)<\/option><option value="json" >JSON \(\.json\)<\/option><option value="none" >No file<\/option><\/select>/);
assert.match(field, /<p class="field-help" id="output-file-1-help">Each run stores this result as <strong>test-and-ci-evidence\.csv<\/strong>, made by Conclave from the recorded rows\. Agents never choose the file or its address\.<\/p>/);
assert.match(ui.storedFileFieldMarkup({...evidence, file: false}, 1, {escape}), /<option value="none" selected>No file<\/option>.*This result is not stored as a file\./s);
assert.match(ui.storedFileFieldMarkup(notes, 0, {escape}), /<option value="markdown" selected>Markdown \(\.md\)<\/option><option value="none" >No file<\/option>/);
assert.equal(ui.storedFileFieldMarkup({key: 'f', kind: 'findings', type: 'records'}, 2, {escape}), '', 'findings are not stored as files');
assert.deepEqual(ui.fileSetting(evidence, 'json'), {format: 'json'});
assert.equal(ui.fileSetting(evidence, 'csv'), undefined, 'the usual file needs no setting');
assert.equal(ui.fileSetting(evidence, 'none'), false);
assert.match(ui.wouldStoreMarkup(notes, {escape}), /<div class="file-would"><svg[^>]*>.*<\/svg><span>A run would store this result as <strong>release-notes-4-2-final\.md<\/strong>\. Tests do not store files\.<\/span><\/div>/s);
assert.equal(ui.wouldStoreMarkup({...notes, file: false}, {escape}), '');

// --- Nothing a run holds is drawn as markup ------------------------------------------------------------
const hostile = '<img src=x onerror=alert(1)>';
assert.doesNotMatch(ui.fileCardMarkup({...file, id: hostile, name: hostile, preview: hostile, link: `/api/runs/${hostile}/files/x`}, {escape, label: hostile, earlier: [{...earlier, name: hostile}]}), /<img/);

// --- Studio wires it in ------------------------------------------------------------------------------
const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
assert.match(studio, /import \{[^}]*fileCardMarkup[^}]*\} from '\/output-files-ui\.js';/);
assert.match(studio, /\/api\/runs\/\$\{encodeURIComponent\([^)]*\)\}\/files\/\$\{encodeURIComponent\([^)]*\)\}/, 'downloads use the checked link');
assert.match(studio, /storedFileFieldMarkup\(/);
assert.match(studio, /wouldStoreMarkup\(/);
assert.match(studio, /stepFileMarkup\(/);
assert.doesNotMatch(studio, /Shows links to files once file access is connected/);
assert.doesNotMatch(studio, /data-download-output/, 'a document is downloaded as its stored file');
assert.match(studio, /function sourceReferenceMarkup\(source,\{linkable=true\}=\{\}\)/, 'an artifact’s addresses are text');
const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
for (const rule of ['.file-card', '.file-head', '.file-icon', '.file-preview', '.file-versions', '.file-problem', '.file-would'])
  assert.ok(css.includes(rule + '{') || css.includes(rule + ' ') || css.includes(rule + ','), `studio.css has ${rule}`);
console.log('output file checks passed');
