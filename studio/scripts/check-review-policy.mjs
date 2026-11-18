// Who may answer reviews, and reviews that need several people, as Studio
// validates, edits and draws them. The mockups are docs/studio/mockups/review-policy.html.
// Offline: no browser, backend or provider.
//
//   node studio/scripts/check-review-policy.mjs
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
const defs = await import(shared);
const rounds = await import(url('../web/review-rounds.js'));
const {projectLiveRun} = await import(await load('../web/live.js', common));
const workbench = await import(await load('../web/workbench-ui.js', [...common, ['/plan.js', await load('../web/plan.js', [['/definition.js', shared]])],
  ['/catalogue.js', url('../shared/catalogue.js')], ['/run-canvas.js', runCanvasUrl]]));

const definition = (policy, required) => {
  const d = {
    version: 1, name: 'Release', description: '',
    agents: [{id: 'writer', name: 'Writer', role: 'custom', kind: 'ai', instructions: 'Write', tools: []},
             {id: 'approvers', name: 'Release approvers', role: 'custom', kind: 'human', instructions: 'Approve', tools: []}],
    state: [{key: 'notes', label: 'Notes', type: 'text', merge: 'replace', statuses: []},
            {key: 'decision', label: 'Release decision', kind: 'decision', type: 'records', merge: 'upsert', statuses: ['approved', 'rejected', 'changes_requested']}],
    workflow: [{id: 'draft', name: 'Draft the notes', agent: 'writer', task: 'Draft', output: 'notes'},
               {id: 'approve', name: 'Approve the release', agent: 'approvers', task: 'Approve', output: 'decision'}],
    views: [{id: 'notes_view', title: 'Notes', collection: 'notes', layout: 'prose'}],
    run_config: {max_tool_steps: 8, max_notes: 12, max_note_chars: 300, max_tool_output_chars: 20000, fan_out_limit: 4},
    run_budget: {max_delegations: null, max_per_agent: null, max_depth: null, tool_calls: null, wall_clock_seconds: null, tokens: null, cost_usd: null},
  };
  if (policy) d.review_policy = policy;
  if (required !== undefined && required !== null) d.workflow[1].answers_required = required;
  return d;
};

// --- Definitions ---------------------------------------------------------------------
defs.validateDefinition(definition({reviewers: ['ana@acme.com', '@ops.acme.com']}, 12));
for (const [policy, required, message] of [
  [{reviewers: ['ana@acme.com', 'ANA@acme.com']}, undefined, /different people/],
  [{reviewers: ['@acme']}, undefined, /whole domains/],
  [{reviewers: []}, undefined, /1 to 50/],
  [{allow_starter: 'no'}, undefined, /true or false/],
  [{reviewers: ['ana@acme.com', 'ben@acme.com']}, 3, /names only 2 people/],
  [null, 51, /1 to 50/],
  [null, 0, /1 to 50/],
]) assert.throws(() => defs.validateDefinition(definition(policy, required)), message, 'the browser refuses what the host refuses');
assert.equal(defs.namedReviewers({reviewers: ['a@x.io', 'b@x.io']}), 2);
assert.equal(defs.namedReviewers({reviewers: ['a@x.io', '@x.io']}), null, 'a domain leaves room for more');
assert.equal(defs.reviewersText(['ana@acme.com', '@ops.acme.com']), 'ana@acme.com or anyone at @ops.acme.com');
assert.match(defs.answersRequiredText(definition(null, 3), definition(null, 3).workflow[1]), /^All 3 must approve.*first rejection stops it at once/);

// --- "People who must answer" ------------------------------------------------------------
let d = definition({reviewers: ['ana@acme.com', 'ben@acme.com', 'cai@acme.com']}, 3);
let markup = workbench.answersRequiredMarkup(d, d.workflow[1], {escape});
assert.match(markup, /<label for="answers-required">People who must answer<\/label>/);
assert.match(markup, /type="number" min="1" max="3" step="1" value="3" data-answers-required="approve"/, 'no more than the people named');
assert.match(markup, /All 3 must approve/);
assert.match(markup, /names 3 people, so at most 3/);
d = definition();
d.workflow[1].output = 'notes';
assert.match(workbench.answersRequiredMarkup(d, d.workflow[1], {escape}), /disabled>.*More than one person needs a decision result/s);

// --- The review panel ---------------------------------------------------------------------
const answers = [
  {reviewer: 'ana@acme.com', output: {records: [{status: 'approved', body: 'Checks pass'}]}, submitted_at: '2026-10-20T14:05:00Z'},
  {reviewer: 'raj@ops.acme.com', output: {records: [{status: 'changes_requested', body: 'Name <the> migration'}]}, submitted_at: '2026-10-20T14:11:00Z'},
];
const review = {id: 'r1', stage: 'approve', status: 'pending', required: 3, answers, request: {state: {}}};
assert.equal(rounds.answersProgressMarkup(review, {escape}), ' <span class="answers-progress">2 of 3 answers</span>');
assert.equal(rounds.answersProgressMarkup({required: 1, answers: []}, {escape}), '', 'a one-person review reads as before');
const panel = rounds.answersMarkup(review, {escape, viewer: 'ANA@acme.com'});
assert.match(panel, /role="img" aria-label="2 of 3 answers recorded"><span style="width:66.7%">/, 'the bar says itself in words');
assert.match(panel, /<strong>raj@ops.acme.com<\/strong> asked for changes: “Name &lt;the&gt; migration”/, 'newest first, escaped');
assert.match(panel, /<strong>You<\/strong> approved: “Checks pass”.*class="decision approved">Approved/s);
assert.match(panel, /class="check-not_checkable"><svg/, 'changes requested has its own mark, not a tick');
assert.match(panel, /1 more person needed/);
const many = rounds.answersMarkup({required: 12, answers: Array.from({length: 7}, (_, n) => ({reviewer: `p${n}@acme.com`, output: {records: [{status: 'approved'}]}}))}, {escape});
assert.match(many, /<summary>5 more answers<\/summary>/, 'past the two latest, answers fold away');
assert.match(many, /5 more people needed/);
const starter = rounds.answerBlockedMarkup({allowed: false, reason: 'You started this run, so someone else must answer its review'}, review,
  {reviewers: ['ana@acme.com', '@ops.acme.com']}, {escape});
assert.match(starter, /role="note"><strong>You started this run, so someone else must answer its review\.<\/strong><p>Who may: ana@acme.com or anyone at @ops.acme.com\. Share this run with them; Studio does not tell them\.<\/p>/);
const twice = rounds.answerBlockedMarkup({allowed: false, reason: 'You already answered this review; it needs a different person'}, review, null, {escape});
assert.match(twice, /The run goes on by itself once 1 more person answers\./);
assert.equal(rounds.answerBlockedMarkup({allowed: true, reason: '', completes: true}, review, null, {escape}), '');

// The live run carries the host's answer and who started it.
const run = projectLiveRun({id: 'x', status: 'waiting_for_human', started_by: 'sam@acme.com',
  can_answer: {allowed: false, reason: 'You started this run, so someone else must answer its review'},
  request: {definition: definition(), task: 't', inputs: {}, revision: 1}, events: [], reviews: [review]});
assert.equal(run.startedBy, 'sam@acme.com');
assert.equal(run.canAnswer.allowed, false);

const {runActivities} = await import(activityUrl);
const withheld = runActivities(definition(), {events: [{type: 'ContinueWithheld', reason: 'owner_not_admitted', sequence: 1}]}, {agentName: (x, id) => id, capabilityName: name => name});
assert.equal(withheld[0].text, 'The review is complete, but the run did not continue');
assert.match(withheld[0].detail, /can no longer sign in here/);
assert.equal(withheld[0].important, true);

// The policy this run is held to, from the version it ran; and, where names are
// not verified, that they are not.
const inForce = rounds.policyInForceMarkup({reviewers: ['ana@acme.com', '@ops.acme.com']}, {escape, revision: 4, verified: true});
assert.match(inForce, /^<p class="policy-in-force">Who may answer: ana@acme.com or anyone at @ops\.acme\.com\. The person who started this run may not answer\. Set by saved version 4\.<\/p>$/);
assert.match(rounds.policyInForceMarkup({allow_starter: true}, {escape, revision: 2, verified: true}), /Who may answer: anyone who can open this run\. The person who started this run may answer too\./);
assert.equal(rounds.policyInForceMarkup(undefined, {escape, revision: 1, verified: false}), '', 'no policy, nothing to say');
assert.match(rounds.policyInForceMarkup({reviewers: ['ana@acme.com']}, {escape, revision: 1, verified: false}),
  /<p class="identity-note" role="note">Names on this host are not verified: anyone using this computer can answer under another name\. Sign-in makes these rules hold\.<\/p>/);

console.log('Review policies validate, edit and draw as designed.');
