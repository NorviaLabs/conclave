// Several steps of one run at once, in words: steps that finished and wait
// their turn to join the results, a pause that lets running steps finish, a
// run stopped at a run-wide limit, and the Steps at once setting.
//
// Everything is read from the projection in live.js (projectLiveRun). This
// module imports nothing, so any page or check can load it on its own.

const plural = (count, one, many) => `${count} ${count === 1 ? one : many}`;
const identity = value => String(value ?? '');
const stepName = (d, id) => d.workflow.find(step => step.id === id)?.name || id;
const memberName = (d, id) => d.agents.find(member => member.id === id)?.name || id;
export const LIMIT_WORDS = {tokens: 'token limit', wall_clock_seconds: 'time limit', cost_usd: 'cost limit'};
// Where each run-wide limit is set, as Limits and approvals names it.
const LIMIT_FIELDS = {tokens: 'AI usage', wall_clock_seconds: 'Working seconds', cost_usd: 'Cost · USD'};
const LIMIT_UNITS = {tokens: ' tokens', wall_clock_seconds: ' seconds', cost_usd: ' USD'};
const number = value => Number(value).toLocaleString('en-US', {maximumFractionDigits: 2});

// "A", "A and B", "A, B and 2 more".
export function joinNames(names) {
  if (names.length <= 1) return names[0] || '';
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names[0]}, ${names[1]} and ${names.length - 2} more`;
}

// The earlier steps, in plan order, that a finished step waits for before its
// result joins the shared results: those not finished yet.
export function heldWaitingFor(d, run, stepId) {
  const order = run?.executionOrder || d.workflow.map(step => step.id);
  const done = new Set([...(run?.completedSteps || []), ...(run?.finishedSteps || [])]);
  return order.slice(0, Math.max(0, order.indexOf(stepId))).filter(id => !done.has(id)).map(id => stepName(d, id));
}

// The card line of a held step: "15 words · joins the results after A and B".
export function heldCardText(d, run, stepId, produced) {
  const names = heldWaitingFor(d, run, stepId);
  const joins = names.length ? `joins the results after ${joinNames(names)}` : 'joins the results next';
  return produced ? `${produced} · ${joins}` : joins.replace(/^j/, 'J');
}

// The words a card adds when a run-wide limit cut its step short.
export function cutShortText(dimension) {
  return LIMIT_WORDS[dimension] ? `cut short at the ${LIMIT_WORDS[dimension]}` : '';
}

// The run's progress line, after "N of M steps complete".
export function runProgressText(d, run, {agentName = memberName} = {}) {
  const total = (run.definition || d).workflow.length, active = run.activeSteps || [];
  let text = `${(run.completedSteps || []).length} of ${total} steps complete`;
  // Steps a condition skipped are done too, and said so.
  const skippedCount = Object.keys(run.skippedSteps || {}).length;
  if (skippedCount) text += ` · ${skippedCount} skipped`;
  if (run.mode === 'stopped' && run.budgetStop) return `${text} · stopped at the ${LIMIT_WORDS[run.budgetStop.dimension] || 'limit'}`;
  if (run.pausing && ['pending', 'running'].includes(run.mode))
    return `${text} · ${plural(run.pausing.running, 'step finishing', 'steps finishing')} · no new step will start`;
  if (['running', 'waiting_human'].includes(run.mode)) {
    if (active.length > 1) text += ` · ${active.length} steps running`;
    else if (active.length === 1) {
      const step = d.workflow[active[0]];
      if (step) text += ` · ${agentName(d, step.agent)} ${run.mode === 'waiting_human' ? 'needs your input' : 'is working'}`;
    }
    const held = (run.finishedSteps || []).length;
    if (held) text += ` · ${held} finished, waiting ${held === 1 ? 'its' : 'their'} turn`;
    return text;
  }
  const last = run.events?.findLast(event => event.type === 'ApplicationFinished');
  if (run.mode === 'paused' && last?.next_stage) text += ` · paused before ${stepName(d, last.next_stage)}`;
  return text;
}

// The pause control, named for what pressing it does, or for what it is doing.
export function pauseControl(run) {
  if (run?.pausing) return {label: `Pausing · ${plural(run.pausing.running, 'running step', 'running steps')} will finish first`, disabled: true};
  return {label: 'Pause: no new step starts; running steps finish', disabled: false};
}

// What a person is told when they press pause.
export function pauseNotice(run) {
  const running = (run?.activeSteps || []).length;
  return running ? `Pausing: no new step will start. ${plural(running, 'running step', 'running steps')} will finish first.` : 'Pausing: no new step will start.';
}

// A run stopped at a run-wide limit: the numbers, what happened, and where to
// raise the limit. There is no Continue: the limit is fixed for this run.
export function budgetStopNoteMarkup(run, {escape: e = identity} = {}) {
  const {dimension, limit, used} = run.budgetStop, words = LIMIT_WORDS[dimension] || 'limit', unit = LIMIT_UNITS[dimension] || '';
  const numbers = used !== undefined && used !== null && limit !== undefined && limit !== null ? `: <strong>${e(number(used))} of ${e(number(limit))}</strong>${e(unit)}` : '';
  return `<strong>Completed work is saved.</strong><p>${plural((run.completedSteps || []).length, 'completed step remains', 'completed steps remain')} in this run.</p>`
    + `<p>This run reached its ${e(words)}${numbers}. No new step started once it was reached, and the steps already running wrote up what they had. It cannot be continued with the same limit.</p>`
    + `<p>To go further, raise <em>${e(LIMIT_FIELDS[dimension] || 'the limit')}</em> in Limits and approvals, then start a new run.</p>`
    + `<div class="note-actions"><button class="button" data-action="view-results">View saved work</button><button class="button" type="button" data-step="5">Open Limits and approvals</button></div>`;
}

// The steps whose work joined the results after a step failed: they were
// working beside it, or had finished and were waiting their turn.
export function finishedBesideFailure(d, run) {
  const events = run.events || [];
  const failedAt = events.findIndex(event => event.type === 'StageFinished' && event.status === 'failed');
  if (failedAt < 0) return [];
  const failed = events[failedAt].stage;
  return [...new Set(events.slice(failedAt + 1).filter(event => event.type === 'ApplicationStateChanged' && event.stage !== failed).map(event => event.stage))];
}

// A failed run: why, and which work beside the failure was kept.
// A step stopped by its own time limit: what happened, and how to go on.
// Durations are shown short and heard in words, as in time-limit-ui.js.
const parts = seconds => { const whole = Math.max(0, Math.round(Number(seconds) || 0)); return [Math.floor(whole / 3600), Math.floor(whole % 3600 / 60), whole % 60]; };
const words = seconds => { const [h, m, s] = parts(seconds); return [h ? plural(h, 'hour', 'hours') : '', m ? plural(m, 'minute', 'minutes') : '', s ? plural(s, 'second', 'seconds') : ''].filter(Boolean).join(' ') || '0 seconds'; };
const short = seconds => { const [h, m, s] = parts(seconds); return [h ? `${h} h` : '', m ? `${m} m` : '', s ? `${s} s` : ''].filter(Boolean).join(' ') || '0 s'; };
const clock = seconds => { const [h, m, s] = parts(seconds), pad = value => String(value).padStart(2, '0'); return h ? `${h} h ${pad(m)} m` : m ? `${m} m ${pad(s)} s` : `${s} s`; };
const spoken = (shown, seconds) => `<span aria-hidden="true">${shown}</span><span class="sr-only">${words(seconds)}</span>`;
export function timedOutNoteMarkup(d, run, {escape: e = identity} = {}) {
  const [stageId, outcome] = Object.entries(run.timedOut || {})[0] || [];
  if (!stageId) return '';
  const done = (run.completedSteps || []).length;
  const cont = run.resumable ? '<button class="button primary" data-action="resume-live">Continue</button>' : '';
  return `<strong>“${e(stepName(d, stageId))}” ran past its time limit (${spoken(short(outcome.limit), outcome.limit)}).</strong>`
    + `<p>It was stopped after ${spoken(clock(outcome.elapsed), outcome.elapsed)}, while it worked. ${plural(done, 'completed step remains', 'completed steps remain')} in this run.${run.resumable ? ' Continue runs it again with its full limit.' : ''}</p>`
    + `<div class="note-actions">${cont}<button class="button" data-action="view-results">View saved work</button></div>`;
}

export function failedRunNoteMarkup(d, run, {escape: e = identity} = {}) {
  const last = run.events?.findLast(event => event.type === 'ApplicationFinished');
  const beside = finishedBesideFailure(d, run).map(id => `<em>${e(stepName(d, id))}</em>`);
  return `<strong>Completed work is saved.</strong><p>${plural((run.completedSteps || []).length, 'completed step remains', 'completed steps remain')} in this run. This run cannot be continued. Check the saved work, then start a new run.</p>`
    + (last?.reason ? `<p>${last.stage ? `<strong>${e(stepName(d, last.stage))} failed.</strong> ` : ''}${e(last.reason)}</p>` : '')
    + (beside.length ? `<p>${joinNames(beside)} finished beside it, and their work is saved.</p>` : '')
    + '<div class="note-actions"><button class="button" data-action="view-results">View saved work</button></div>';
}

// The step details of a held step: why its result is not in the results yet.
export function heldDetailNoteMarkup(d, run, stepId, {escape: e = identity} = {}) {
  const names = heldWaitingFor(d, run, stepId).map(name => `<em>${e(name)}</em>`);
  const after = names.length ? `It joins them after ${joinNames(names)} ${names.length === 1 ? 'finishes' : 'finish'}` : 'It joins them next';
  return `<p class="held-note" role="note"><strong>Not in the shared results yet.</strong> ${after}, so the results always build up in the plan’s order. Steps that depend on it can already use it.</p>`;
}

// Steps at once, in Limits and approvals: the application's own number, under
// the host's cap. Blank means as many as the host allows.
export function stepsAtOnceMarkup(value, {cap = 8, escape: e = identity, disabled = false} = {}) {
  return `<div class="field steps-at-once"><label for="field-run_config-fan_out_limit">Steps at once</label>`
    + `<input id="field-run_config-fan_out_limit" data-path="run_config.fan_out_limit" type="number" step="1" min="1" max="${e(cap)}" value="${e(value ?? '')}" placeholder="As many as this host allows (${e(cap)})" aria-describedby="steps-at-once-help"${disabled ? ' disabled' : ''}>`
    + `<p class="field-help" id="steps-at-once-help">How many steps may work at the same time when they do not depend on each other. <strong>1</strong> runs them one at a time. Leave blank for as many as this host allows (${e(cap)}). Results still join in the plan’s order, whichever step finishes first.</p>`
    + `<p class="validation-inline" id="steps-at-once-issue" role="status"></p></div>`;
}

// Why a value cannot be Steps at once, or '' when it can.
export function stepsAtOnceIssue(value, cap = 8) {
  if (value === null || value === undefined || value === '') return '';
  return Number.isInteger(value) && value >= 1 && value <= cap ? '' : `Use 1 to ${cap}, or leave blank.`;
}
