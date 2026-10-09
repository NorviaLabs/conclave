// Offline check for goal refinement: one sparkle, one job — clicking it rewords
// the goal where it stands, and a host without AI offers no sparkle at all.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
const source = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const start = source.indexOf('function goalRefineMarkup(');
const end = source.indexOf('async function goalIntegrationSuggestions(');
assert(start > 0 && end > start, 'goal refinement functions are present');
const body = source.slice(start, end);

const calls = [];
const base = {
  runtime: {available: true},
  dialogState: {kind: 'goal', goal: 'compare suppliers'},
  escape: value => String(value ?? '').replace(/[<>&"]/g, character => ({'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;'}[character])),
  uiIcon: name => `<svg data-icon="${name}"></svg>`,
  renderEditor: () => calls.push('render'),
  notify: message => calls.push({notify: message}),
  request: async (path, options) => {
    calls.push({path, body: JSON.parse(options.body)});
    return {goal: 'Compare three suppliers on price and lead time, then recommend one.', summary: 'Made the criteria explicit.'};
  },
};
// The sandbox keeps the functions the VM defined, so overrides are per case.
const load = (overrides, names) => {
  const sandbox = {...base, ...overrides};
  runInNewContext(`${body}\nglobalThis.__api = {${names}};`, sandbox);
  return sandbox.__api;
};

// The control is one icon, named for a pointer and a screen reader, and it says
// what it is doing while it does it.
const offered = load({}, 'goalRefineMarkup').goalRefineMarkup({goal: 'compare suppliers'});
assert.match(offered, /data-refine-goal/, 'the sparkle is the whole control');
assert.match(offered, /data-icon="sparkle"/);
assert.match(offered, /title="Reword this goal with AI"/, 'named on hover');
assert.match(offered, /aria-label="Reword this goal with AI"/, 'and to a screen reader');
assert.doesNotMatch(offered, /data-use-refined|data-discard-refined/, 'nothing to accept or discard');
assert.doesNotMatch(offered, /data-dialog-field/, 'and no second screen to fill in');
assert.doesNotMatch(load({runtime: {available: false}}, 'goalRefineMarkup').goalRefineMarkup({goal: 'x'}), /data-refine-goal/,
  'a host without AI offers no refinement');
const refining = load({}, 'goalRefineMarkup').goalRefineMarkup({goal: 'x', refining: true});
assert.match(refining, /Rewording…/, 'and says so while the call is out');
assert.match(refining, /class="spinner"/, 'with a moving indicator');

// Clicking it reads the goal and puts the reworded text in its place.
const state = {kind: 'goal', goal: 'compare suppliers'};
await load({dialogState: state}, 'refineGoal').refineGoal();
assert.deepEqual(calls.find(call => typeof call === 'object' && call.path),
                 {path: '/api/goals/refine', body: {goal: 'compare suppliers'}});
assert.equal(state.goal, 'Compare three suppliers on price and lead time, then recommend one.',
             'the goal is reworded where it stands');
assert.deepEqual(calls.find(call => call.notify), {notify: 'Made the criteria explicit.'},
                 'and what changed is reported rather than asked about');
assert.equal(state.refinement, undefined, 'no suggestion is kept: it was applied');

await assert.rejects(() => load({dialogState: {kind: 'goal', goal: '  '}}, 'refineGoal').refineGoal(), /Describe your goal/);
await assert.rejects(() => load({runtime: {available: false}}, 'refineGoal').refineGoal(), /no AI provider/);

// Refining is wording only, so it is offered where the goal is written and
// nowhere else, and the old accept/discard handlers are gone with the panel.
assert.match(source, /class="icon-button" data-refine-goal/, 'the sparkle sits beside the goal');
assert.ok(!/data-discard-refined|data-use-refined/.test(source), 'no accept/discard handlers survive');
assert.ok(!/s\.refinement/.test(source), 'and no stale suggestion state');

console.log('PASS: refinement is one sparkle that rewords the goal in place, with no second screen and no host without AI offering it.');
