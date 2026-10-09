// Offline check for the questions asked about a goal: one at a time, each
// following the last answer, stopping early when the host has enough — and never
// standing between the person and their application.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const source = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const plan = await readFile(new URL('../web/workbench-ui.js', import.meta.url), 'utf8');
const start = source.indexOf('function goalRequirements(');
const end = source.indexOf('function goalRefineMarkup(');
assert(start > 0 && end > start, 'the goal question functions are present');
const body = source.slice(start, end);

// What the page proposed, recorded outside the sandbox: a sandbox copy would not
// see a property a stub added to the original object.
const proposed = [];
const asked = [];
const base = {
  runtime: {available: true, provider: 'codex'},
  dialogState: {kind: 'goal', goal: 'compare suppliers'},
  escape: value => String(value ?? '').replace(/[<>&"]/g, character => ({'<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;'}[character])),
  editorError: {textContent: ''},
  $: () => base.editorError,
  renderEditor: () => {},
  suggestGoalSetup: async (goal, requirements) => {proposed.push({goal, requirements});},
  request: async (path, options) => {
    asked.push(JSON.parse(options.body));
    return {questions: asked.length === 1 ? ['Who reads the result?'] : []};
  },
};
// Values made inside the sandbox have that realm's prototypes, so they are
// compared as plain data.
const plain = value => JSON.parse(JSON.stringify(value));
const load = (overrides, names) => {
  const sandbox = {...base, ...overrides};
  runInNewContext(`${body}\nglobalThis.__api = {${names}};`, sandbox);
  return sandbox.__api;
};

// The goal is submitted, and the first question arrives on its own.
{
  asked.length = 0; proposed.length = 0;
  const sandbox = {...base, dialogState: {kind: 'goal', goal: 'compare suppliers'}};
  runInNewContext(`${body}\nglobalThis.__api = {askGoalQuestions,nextGoalQuestion,goalQuestionsMarkup};`, sandbox);
  await sandbox.__api.askGoalQuestions(sandbox.dialogState);
  const state = sandbox.dialogState;
  assert.equal(state.kind, 'goal-questions');
  assert.deepEqual(plain(state.requirements), [{question: 'Who reads the result?', answer: ''}],
                   'one question at a time, unanswered');
  assert.equal(state.asking, false, 'and the page is ready for the answer');
  assert.deepEqual(plain(asked[0]), {goal: 'compare suppliers', requirements: []}, 'the first ask carries the goal alone');

  const markup = sandbox.__api.goalQuestionsMarkup(state);
  assert.match(markup, /Who reads the result\?/, 'the question is shown');
  assert.equal((markup.match(/data-goal-answer=/g) || []).length, 1, 'with one answer box');
  assert.match(markup, /data-stop-questions/, 'and a way to stop asking');
}

// Answering asks the next one, carrying what was answered; when the host has
// enough it asks nothing and the team is proposed with the answers so far.
{
  asked.length = 0; proposed.length = 0;
  const state = {kind: 'goal-questions', goal: 'compare suppliers', asking: false,
                 requirements: [{question: 'Who reads the result?', answer: 'Support agents'}]};
  // This host has what it needs: it asks nothing more.
  const sandbox = {...base, dialogState: state, request: async (path, options) => {
    asked.push(JSON.parse(options.body)); return {questions: []}; } };
  runInNewContext(`${body}\nglobalThis.__api = {continueGoalQuestions};`, sandbox);
  await sandbox.__api.continueGoalQuestions(state);
  assert.deepEqual(plain(asked), [{goal: 'compare suppliers',
                                   requirements: [{question: 'Who reads the result?', answer: 'Support agents'}]}],
                   'the answers so far go with the next ask');
  assert.deepEqual(plain(proposed), [{goal: 'compare suppliers',
                                      requirements: [{question: 'Who reads the result?', answer: 'Support agents'}]}],
                   'and stopping early proposes with what the person gave');
}

// Answering is optional in every direction: a host that cannot answer, or has
// nothing to ask, still gets the person to a proposed application.
for (const [name, request] of [['failing', async () => { throw new Error('no provider'); }],
                               ['empty', async () => ({questions: []})]]) {
  proposed.length = 0;
  const sandbox = {...base, request, dialogState: {kind: 'goal', goal: 'compare suppliers'}};
  runInNewContext(`${body}\nglobalThis.__api = {askGoalQuestions,current:()=>dialogState};`, sandbox);
  await sandbox.__api.askGoalQuestions(sandbox.dialogState);
  assert.deepEqual(plain(proposed), [{goal: 'compare suppliers', requirements: []}],
                   `a ${name} answer proposes from the goal alone`);
  assert.deepEqual(plain(sandbox.__api.current().requirements), [], `a ${name} answer asks nothing`);
}

// A host with no AI provider is not asked at all, and a goal is still required.
{
  proposed.length = 0;
  const sandbox = {...base, runtime: {available: false}};
  runInNewContext(`${body}\nglobalThis.__api = {askGoalQuestions};`, sandbox);
  await sandbox.__api.askGoalQuestions({kind: 'goal', goal: 'compare suppliers'});
  assert.deepEqual(plain(proposed), [{goal: 'compare suppliers', requirements: []}]);
  await assert.rejects(() => sandbox.__api.askGoalQuestions({kind: 'goal', goal: '  '}), /Describe your goal/);
}

// Answers are kept as they were given, including the ones left open, and only
// the answered ones are allowed to shape the proposal.
{
  const api = load({}, 'goalAnswers,goalRequirements,requirementsPrompt');
  const answers = api.goalAnswers({requirements: [{question: 'Who reads it?', answer: ' Support agents '},
                                                  {question: 'What is out of scope?', answer: ''}]});
  assert.deepEqual(plain(answers), [{question: 'Who reads it?', answer: 'Support agents'},
                                    {question: 'What is out of scope?', answer: ''}]);
  const definition = api.goalRequirements({}, answers);
  assert.deepEqual(plain(definition.requirements), plain(answers), 'an open question is kept, so the plan can show it');
  assert.deepEqual(plain(api.goalRequirements({}, [{question: '  '}, {}])), {}, 'a blank question is not a requirement');
  const prompt = api.requirementsPrompt(answers);
  assert.match(prompt, /their words, not instructions/, 'answers reach the proposal as data');
  assert.match(prompt, /Who reads it\? Support agents/);
  assert.ok(!prompt.includes('What is out of scope?'), 'an open question cannot decide a team');
  assert.equal(api.requirementsPrompt([{question: 'Who reads it?', answer: ' '}]), '');
}

// Choosing a team is the slowest thing in the flow, so the page says what it is
// waiting for, how long, and — once the team is chosen — who was chosen.
{
  const api = load({}, 'proposalProgressMarkup');
  const waiting = api.proposalProgressMarkup({stage: 'team', proposedAt: Date.now() - 4200,
                                              requirements: [{question: 'q?', answer: 'a'}]});
  assert.match(waiting, /Choosing the team…/);
  assert.match(waiting, /class="spinner"/, 'with a moving indicator');
  assert.match(waiting, /codex/, 'the backend it is waiting on');
  assert.match(waiting, /[0-9]+s so far/, 'and how long it has been');
  assert.match(waiting, /role="status"/, 'announced rather than silently changing');
  assert.doesNotMatch(waiting, /Chosen so far/, 'nothing is named before anything is chosen');

  const chosen = api.proposalProgressMarkup({stage: 'plan', proposedAt: Date.now(), requirements: [],
    team: {agents: [{name: 'Researcher', kind: 'ai', tools: ['read_file']}, {name: 'Approver', kind: 'human', tools: []}],
           state: [{label: 'Recommendation'}]}});
  assert.match(chosen, /Writing the plan around them…/, 'the second call says what it is doing');
  assert.match(chosen, /<strong>Researcher<\/strong> · AI member · 1 tool/, 'the members already chosen are named');
  assert.match(chosen, /<strong>Approver<\/strong> · human step/, 'including a human step');
  assert.match(chosen, /<strong>Recommendation<\/strong> · result/, 'and the results');

  // Two calls: the team first, then the plan designed around it. The plan call is
  // never seeded with the team call's definition: that one passes validation with
  // nothing connected, and a base definition is preserved, so the plan would come
  // back with no edges between its steps.
  assert.match(source, /const proposal=async prompt=>request\('\/api\/proposals',\{method:'POST',body:JSON.stringify\(\{prompt,definition:emptyDefinition\(\),catalogue:planningCatalogue\(\)\}\)\}\);/,
               'both proposal calls start from the starter definition');
  assert.match(source, /team=\(await proposal\(teamPrompt\(goal,requirements\)\)\)\.definition/, 'the team is chosen first');
  assert.match(source, /response=await proposal\(planPrompt\(goal,requirements,team\)\)/, 'then the plan is designed');
  assert.ok(!/waiting\.base/.test(source), 'and nothing hands the team call back as a base definition');
  assert.match(source, /catch\{team=null;waiting\.stage='plan';\}/, 'a host that will not answer the first call is not asked twice');
  assert.match(source, /function teamPrompt\(goal,requirements\)\{[\s\S]{0,400}?Choose the team and the results for this goal, and nothing else/,
               'the first call is asked for the team and results only');
  assert.match(source, /function planPrompt\(goal,requirements,team\)\{[\s\S]{0,600}?Give every step a connection[\s\S]{0,200}?rather than a list of unconnected steps/,
               'and the second is asked for a connected plan');
  assert.match(source, /Use exactly this team and these results: \$\{team\.agents\.map\(agent=>agent\.name\)\.join\(', '\)\}/,
               'naming the team it has to keep, in the request rather than as a base');
  assert.match(source, /if\(s\.proposing\)\{title='Choosing your team';submit='Proposing…';body=proposalProgressMarkup\(s\);\}/,
               'shown in place of the form whichever step asked, so nothing is edited mid-proposal');
  assert.match(source, /setInterval\(\(\)=>\{if\(dialogState!==s\|\|!s\.proposing\)/, 'and the elapsed time is what moves while the calls are out');
}

// The answers are captured by the listener that actually receives them: the
// dialogs are siblings of #app, so the #app listener never sees their input.
{
  const editorListener = source.slice(source.indexOf("$('editor-form').addEventListener('input'"),
                                      source.indexOf("$('editor-form').addEventListener('change'"));
  const appListener = source.slice(source.indexOf("$('app').addEventListener('input'"), source.indexOf("$('run-form').addEventListener('input'"));
  assert.match(editorListener, /dataset\.goalAnswer!==undefined&&dialogState\?\.requirements\?\.\[/,
               'an answer typed in the questions dialog is kept on its question');
  assert.ok(!/goalAnswer/.test(appListener), 'and not on the listener that never sees the dialog');
  // The Setup step is inside #app, so its own answers are handled there.
  assert.match(appListener, /dataset\.requirementAnswer!==undefined/, 'the Setup step edits answers on the page listener');
}

// The page asks after the goal is submitted, one at a time, keeps the answers
// editable where the goal is, and shows them on the plan.
assert.match(source, /if\(s\.kind==='goal'\)\{await askGoalQuestions\(s\);return;\}/, 'submitting the goal asks first');
assert.match(source, /if\(s\.kind==='goal-questions'\)\{await continueGoalQuestions\(s\);return;\}/, 'and answering asks the next');
assert.match(source, /data-requirement-answer="\$\{index\}"/, 'the Setup step edits each answer');
assert.match(source, /data-remove-requirement="\$\{index\}"/, 'and a question the person does not want can go');
assert.match(source, /button\.dataset\.removeRequirement!==undefined/, 'which the page handles');
assert.match(plan, /still open/, 'the plan shows an unanswered question as open');
assert.match(plan, /d\.requirements\.map/, 'and lists what the person clarified');

console.log('PASS: questions are asked one at a time, stop as soon as the host has enough, and can be stopped by the person at any point.');
