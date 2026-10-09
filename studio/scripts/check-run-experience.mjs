import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
// run-activity.js shares the icon set, so its browser specifier is rewritten.
const activitySource=(await readFile(new URL('../web/run-activity.js',import.meta.url),'utf8'))
  .replace("'/canvas-ui.js'",JSON.stringify(new URL('../web/canvas-ui.js',import.meta.url).href));
const {runActivities,activityDockMarkup}=await import('data:text/javascript,'+encodeURIComponent(activitySource));
const d={workflow:[{id:'research',name:'Market analysis',agent:'researcher',output:'findings'},{id:'analysis',name:'Pricing',agent:'analyst',output:'findings'}],state:[{key:'findings',label:'Findings'}]};
const ctx={escape:value=>String(value??'').replaceAll('<','&lt;'),agentName:(_d,id)=>({researcher:'Researcher',analyst:'Analyst'}[id]||id),capabilityName:id=>id.replaceAll('_',' ')};
const events=[{type:'ApplicationStarted',sequence:1}, {type:'StageStarted',stage:'research',agent:'researcher',sequence:2,timestamp:'2026-10-01T12:21:00Z'},...Array.from({length:100},(_,i)=>({type:'ToolCalled',stage:'research',agent:'researcher',tool:'web_search',ok:true,sequence:i+3})),{type:'Delegation',stage:'research',agent:'analyst',delegated_by:'researcher',task:'Compare <prices>',sequence:103},{type:'ApplicationStateChanged',stage:'research',patch:[{title:'Finding'}],sequence:104},{type:'HumanInputRequested',stage:'analysis',agent:'analyst',sequence:105},{type:'ToolCalled',stage:'analysis',agent:'analyst',tool:'read_file',ok:false,sequence:106},{type:'UnknownEvent',sequence:107}];
const run={events},rows=runActivities(d,run,ctx);
assert.equal(rows.length,7,'unknown events are omitted and routine calls grouped');
assert.equal(rows[0].text,'Analyst: read file failed');
assert.equal(rows[0].important,true);
assert.equal(rows[1].text,'Analyst needs your input');
assert.equal(rows[2].detail,'1 findings');
assert.equal(rows.find(row=>row.routine).count,100);
assert.equal(rows.find(row=>row.routine).events.length,100,'technical details retain every recorded call');
const collapsed=activityDockMarkup(d,run,ctx);
assert.match(collapsed,/aria-expanded="false"/);assert.doesNotMatch(collapsed,/class="activity-panel"/);
assert.match(collapsed,/Analyst: read file failed/);
const expanded=activityDockMarkup(d,run,{...ctx,expanded:true,filter:'research'});
assert.match(expanded,/data-activity-filter/);assert.match(expanded,/data-run-step="research"/);
assert.doesNotMatch(expanded,/data-run-step="analysis"/);
assert.match(expanded,/datetime="2026-10-01T12:21:00Z"/);
assert.match(expanded,/Compare &lt;prices>/);assert.match(expanded,/Execution details/);
assert.equal(runActivities(d,{events:[]},ctx).length,0);
console.log('PASS: truthful, attributed, newest-first activity, grouped tool calls, failures, human input, timestamps, filtering and collapsed default.');

// A step that is working says so: how long it has been working, and the last
// thing the record shows it doing. The clock comes from the run's own event
// timestamps, so a reload does not restart it.
{
  const {runInNewContext} = await import('node:vm');
  const canvas = await readFile(new URL('../web/run-canvas.js', import.meta.url), 'utf8');
  const body = canvas.slice(canvas.indexOf('export function runStepWorking('), canvas.indexOf('export function runCanvasMarkup('))
    .replaceAll('export function ', 'function ');
  assert(body.length > 100, 'the working-step functions are present');
  const sandbox = {};
  runInNewContext(`${body}\nglobalThis.__api = {runStepWorking, runStepWorkingText};`, sandbox);
  const {runStepWorking, runStepWorkingText} = sandbox.__api;

  const start = '2026-10-09T09:40:00.000Z';
  const at = seconds => new Date(Date.parse(start) + seconds * 1000).toISOString();
  const capabilityName = id => ({web_search: 'Web search', web_fetch: 'Web fetch'}[id] || id);
  const working = (events, mode = 'running', nowSeconds = 80) => runStepWorking({mode, events: events.map((event, index) => ({sequence: index + 1, stage: 'research', ...event}))},
                                                                                  'research', Date.parse(at(nowSeconds)));

  const running = working([{type: 'StageStarted', timestamp: start},
                           {type: 'ToolCalled', tool: 'web_search', ok: true, timestamp: at(20)},
                           {type: 'ToolCalled', tool: 'web_fetch', ok: true, timestamp: at(70)}]);
  assert.equal(running.seconds, 80, 'the elapsed time is measured from the record, not from the page');
  assert.equal(running.tools, 2);
  assert.equal(running.last.tool, 'web_fetch');
  assert.equal(runStepWorkingText(running, capabilityName), 'Working for 1m 20s · 2 tool calls · last Web fetch');

  const failed = working([{type: 'StageStarted', timestamp: start}, {type: 'ToolCalled', tool: 'web_fetch', ok: false, timestamp: at(5)}], 'running', 5);
  assert.equal(runStepWorkingText(failed, capabilityName), 'Working for 5s · 1 tool call (1 failed) · last Web fetch failed');

  // A step that has finished, or a run that is not running, is not working.
  assert.equal(working([{type: 'StageStarted', timestamp: start}, {type: 'RunFinished', timestamp: at(30)}]), null, 'a finished step is not working');
  assert.equal(working([{type: 'StageStarted', timestamp: start}, {type: 'ApplicationStateChanged', timestamp: at(30)}]), null, 'nor one that produced its output');
  assert.equal(working([{type: 'ToolCalled', tool: 'web_search', ok: true}]), null, 'nor a step that never started');
  assert.equal(working([{type: 'StageStarted', timestamp: start}], 'completed'), null, 'nor a step in a run that has stopped');
  assert.equal(runStepWorkingText(null, capabilityName), '', 'and it draws no line at all');

  // The state the node already shows carries the detail, in one place: the same
  // words twice on one card is noise.
  assert.match(canvas, /const state=outOfDate\?'Out of date':workingText\|\|status;/, 'the status shows the detail while the step works');
  assert.match(canvas, /<span class="run-node-status" data-status="\$\{e\(runStatusClass\(status\)\)\}"\$\{note\?` title="\$\{e\(note\)\}"`:''\}><i aria-hidden="true"><\/i>\$\{e\(state\)\}<\/span>/,
               'in the status at the top of the card, with the same words on hover');
  assert.ok(!/step-working/.test(canvas), 'and nowhere else on the card');
  assert.ok(!/aria-live/.test(canvas.slice(canvas.indexOf('const note='), canvas.indexOf('const note=') + 400)),
            'not as a live region: it changes every second');
  const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
  assert.match(studio, /liveClock=setInterval\(\(\)=>\{if\(view!=='plan'\|\|!preview\?\.live\)\{stopLiveClock\(\);return;\}render\(\);\},1000\)/,
               'and the view is redrawn every second while it works');
  assert.match(studio, /function followLiveClock\(\)\{[\s\S]{0,300}?runStepWorking\(preview,step\.id\)/, 'started by the run record that says a step is working');
}
