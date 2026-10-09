import {projectLiveRun} from '/live.js';
import {planConnections} from '/definition.js';
import {runCanvasMarkup} from '/run-canvas.js';

export function createStepTestsController({request,getContext,escape:e,agentName,outputName,capabilityName}){
  const dialog=document.createElement('dialog');dialog.className='step-test-dialog';document.body.append(dialog);
  let context,fixtures=[],record=null,mode='setup',scope='step',error='',timer=null,generation=0,preview=null;
  const base=()=>`/api/applications/${encodeURIComponent(context.applicationId)}`;
  const post=(path,value)=>request(path,{method:'POST',body:JSON.stringify(value)});
  const text=value=>typeof value==='string'?value:JSON.stringify(value,null,2);
  // The steps this one depends on, in the order the runtime would run them. A
  // test needs their outputs because that is what the step actually receives.
  const prerequisites=(d,stepId)=>{
    const found=new Set(),pending=[stepId],links=planConnections(d);
    while(pending.length){const node=pending.shift();for(const edge of links)if(edge.to===node&&!found.has(edge.from)){found.add(edge.from);pending.push(edge.from);}}
    return d.workflow.filter(step=>found.has(step.id));
  };
  const collectionOf=(d,key)=>d.state.find(item=>item.key===key)||{type:'text'};
  const emptyFor=collection=>collection.type==='records'?'[]':'';
  // The values a person typed, read back exactly as they will be sent.
  function authoredValues(){
    const d=context.draft,field=selector=>dialog.querySelector(selector);
    const inputs={};
    for(const input of d.inputs||[]){
      const raw=field(`[data-author-input="${CSS.escape(input.id)}"]`).value;
      if(raw==='')continue;
      inputs[input.id]=input.type==='number'?Number(raw):raw;
    }
    const upstream={};
    for(const step of prerequisites(d,context.stepId)){
      const collection=collectionOf(d,step.output),raw=field(`[data-author-state="${CSS.escape(step.id)}"]`).value;
      upstream[step.id]=collection.type==='records'?(raw.trim()?JSON.parse(raw):[]):raw;
    }
    const referenceOn=field('[data-author-reference-on]')?.checked;
    const rawReference=field('[data-author-reference]')?.value??'';
    return {inputs,upstream,reference_recorded:!!referenceOn,reference:referenceOn?rawReference:null};
  }
  function authorMarkup(){
    const d=context.draft,step=d.workflow.find(item=>item.id===context.stepId);
    const needs=prerequisites(d,context.stepId);
    const inputs=(d.inputs||[]).map(input=>`<label>${e(input.label)}${input.required?' · required':''}${input.type==='textarea'?`<textarea data-author-input="${e(input.id)}" rows="2"></textarea>`:`<input data-author-input="${e(input.id)}" type="${e(input.type==='date'?'date':input.type==='number'?'number':'text')}">`}</label>`).join('')||'<p>This application declares no request fields.</p>';
    const state=needs.map(item=>{const collection=collectionOf(d,item.output);return `<label>${e(item.name||item.id)} · produces ${e(collection.type==='records'?'records':'text')}<textarea data-author-state="${e(item.id)}" rows="${collection.type==='records'?4:2}">${e(emptyFor(collection))}</textarea></label>`;}).join('')||'<p>This step depends on nothing, so it starts from an empty state.</p>';
    return `<p>Nothing was recorded: you supply what this step receives. A prerequisite you leave as its empty value is sent as empty, not invented.</p><p>Testing ${e(step?.name||context.stepId)} in safe mode. Production approvals do not apply; provider calls are real.</p><label>Fixture name<input data-fixture-name maxlength="100" value="${e(step?.name||context.stepId)} fixture"></label><label>Scope<select data-author-scope><option value="step">This step only</option><option value="downstream">This step and its descendants</option></select></label><h3>Request inputs</h3>${inputs}<h3>State from prerequisite steps</h3>${state}<h3>Reference output · optional</h3><label><input type="checkbox" data-author-reference-on> Record what this step should produce, to compare against</label><label>Reference output<textarea data-author-reference rows="4" placeholder="Text, or a JSON array of records"></textarea></label><details><summary>Advanced settings</summary><label>Optional model ID<input data-test-model placeholder="Use definition model"></label><label>Explicit mock tool results (JSON object of tool names to text)<textarea data-test-mocks rows="4">{}</textarea></label></details><details open><summary>What will be saved as the fixture</summary><pre data-author-preview>{}</pre></details><button data-author-start>Create fixture and test</button>`;
  }
  function refreshPreview(){
    const preview=dialog.querySelector('[data-author-preview]');
    if(!preview)return;
    try{preview.textContent=JSON.stringify({step_id:context.stepId,name:dialog.querySelector('[data-fixture-name]').value,...authoredValues()},null,2);}
    catch(error){preview.textContent=error.message;}
  }
  function render(){
    const title=mode==='pin'?'Pin output':mode==='author'?'Test a step that has not run':mode==='results'?'Test results':'Test setup';
    dialog.innerHTML=`<header><h2>${title}</h2><button data-test-close aria-label="Return to editing">Return to editing</button></header><p role="status">${e(error)}</p>${mode==='pin'?`<p>Recorded data, not live sources. Inspect and redact before saving. Automatic redaction is conservative; review all text for sensitive data.</p><label>Fixture name<input data-fixture-name maxlength="100" value="${e(context.stepId)} fixture"></label><label>Recorded fixture JSON<textarea data-fixture-data rows="14">${e(JSON.stringify(preview,null,2))}</textarea></label><button data-fixture-save>Save fixture</button>`:mode==='author'?authorMarkup():mode==='setup'?`<label>Recorded fixture<select data-fixture-choice>${fixtures.filter(f=>f.step_id===context.stepId).map(f=>`<option value="${e(f.id)}">${e(f.name)} · revision ${e(f.revision)}</option>`).join('')}</select></label><label>Application version<select data-test-version><option value="draft">Current draft</option><option value="saved">Saved version</option></select></label><p>Step: ${e(context.stepId)} · ${scope==='downstream'?'selected step and descendants':'selected step only'}</p><label>Safe tool mode<select disabled><option>Block all external tools unless explicitly mocked</option></select></label><p>Production approvals do not apply. Provider calls are real.</p><details><summary>Advanced settings</summary><label>Optional model ID<input data-test-model placeholder="Use definition model"></label><label>Explicit mock tool results (JSON object of tool names to text)<textarea data-test-mocks rows="4">{}</textarea></label></details><button data-test-start ${fixtures.some(f=>f.step_id===context.stepId)?'':'disabled'}>Start test</button>${context.runId?'<button data-pin-open>Pin completed output first</button>':''}`:resultsMarkup()}`;
  }
  function resultsMarkup(){
    if(!record)return '<p>No test selected.</p>';
    const events=record.events.map(item=>({sequence:item.sequence,event:{...item.event,type:item.event.type==='TestStarted'?'ApplicationStarted':item.event.type==='TestFinished'?'ApplicationFinished':item.event.type}}));
    const projected=projectLiveRun({...record,events,reviews:record.reviews||[]});
    projected.testedSteps=record.tested_steps;
    const canvas=runCanvasMarkup(record.request.definition,projected,context.stepId,{escape:e,agentName,outputName,capabilityName,zoom:.6,backends:context.backends||[],followActive:false,toolsDisabled:true});
    // Recorded and written fixtures are told apart everywhere they are read, so
    // a comparison is never shown against a reference that does not exist.
    const provenance=record.authored?'Written for this test; no source run.':'Pinned from run '+e(record.source_run);
    const comparison=record.reference_recorded?`<section class="test-comparison"><div><h3>Reference output</h3><pre>${e(text(record.reference))}</pre></div><div><h3>New output</h3><pre>${e(text(record.output??'Not produced yet'))}</pre></div></section><h3>Comparison</h3><p>Exact data differences only; no semantic equivalence claim.</p><pre>${e(record.comparison?.diff||JSON.stringify(record.comparison||{},null,2))}</pre>`:`<section class="test-comparison"><div><h3>New output</h3><pre>${e(text(record.output??'Not produced yet'))}</pre></div></section><h3>Comparison</h3><p>This fixture recorded no reference output, so there is nothing to compare against. Read the result on its own merits.</p>`;
    return `<strong>TEST · ${e(record.status)}</strong><p>${provenance} · Tested: ${e(record.tested_steps.join(', '))} · Prerequisites supplied: ${e(record.seeded_steps.join(', ')||'none')}</p>${record.error?`<p class="validation">${e(record.error)}</p>`:''}${(record.warnings||[]).map(w=>`<p class="validation">${e(w)}</p>`).join('')}<div class="test-canvas">${canvas}</div>${comparison}<details><summary>Validation, tools, provider and usage</summary><pre>${e(JSON.stringify(record.events,null,2))}</pre><p>Cost: not reported. Tokens: ${e(record.metrics?.tokens??'not reported')}</p></details><button data-test-again>Test again</button>`;
  }
  async function poll(token){
    try{const result=await request(`${base()}/tests/${record.id}`);if(token!==generation||!dialog.open)return;record=result;error='';render();if(['pending','running'].includes(record.status))timer=setTimeout(()=>poll(token),600);}catch(err){if(token===generation){error=err.message;render();}}
  }
  async function open(action,stepId){
    generation++;clearTimeout(timer);context=structuredClone(getContext());if(stepId)context.stepId=stepId;record=null;error='';scope=action==='downstream'?'downstream':'step';mode=action==='pin'?'pin':action==='author'?'author':'setup';dialog.showModal();
    try{if(mode==='author'){if(!context.draft)throw new Error('Open the plan before testing a step.');}else if(mode==='pin')preview=await post(`${base()}/fixtures/preview`,{run_id:context.runId,step_id:context.stepId});else{fixtures=(await request(`${base()}/fixtures`)).fixtures;if(action==='compare'){const tests=(await request(`${base()}/tests`)).tests;record=tests.find(test=>fixtures.some(f=>f.id===test.fixture_id&&f.step_id===context.stepId));if(record)mode='results';else error='No test results for this step yet.';}}render();if(mode==='author')refreshPreview();}catch(err){error=err.message;render();}
  }
  dialog.addEventListener('close',()=>{generation++;clearTimeout(timer);});
  // The envelope is shown as it is typed, so what will be saved is never a
  // surprise and can be corrected before the fixture exists.
  dialog.addEventListener('input',()=>{if(mode==='author')refreshPreview();});
  dialog.addEventListener('change',()=>{if(mode==='author')refreshPreview();});
  dialog.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.hasAttribute('data-test-close')){dialog.close();return;}
    try{
      if(button.hasAttribute('data-pin-open')){await open('pin');return;}
      if(button.hasAttribute('data-fixture-save')){const data=JSON.parse(dialog.querySelector('[data-fixture-data]').value);await post(`${base()}/fixtures`,{run_id:context.runId,step_id:context.stepId,name:dialog.querySelector('[data-fixture-name]').value,data});await open('step');return;}
      if(button.hasAttribute('data-test-again')){await open('step');return;}
      if(button.hasAttribute('data-author-start')){
        const values=authoredValues();
        const definition=structuredClone(context.draft);
        const payload={step_id:context.stepId,name:dialog.querySelector('[data-fixture-name]').value,definition,...values};
        button.disabled=true;
        const fixture=await post(`${base()}/fixtures/author`,payload);
        const test={fixture_id:fixture.id,scope:dialog.querySelector('[data-author-scope]').value,definition,mocks:JSON.parse(dialog.querySelector('[data-test-mocks]').value)};
        const model=dialog.querySelector('[data-test-model]').value.trim();if(model)test.model=model;
        record=await post(`${base()}/tests`,test);mode='results';render();poll(generation);return;
      }
      if(button.hasAttribute('data-test-start')){
        const payload={fixture_id:dialog.querySelector('[data-fixture-choice]').value,scope,mocks:JSON.parse(dialog.querySelector('[data-test-mocks]').value)};
        if(dialog.querySelector('[data-test-version]').value==='draft')payload.definition=structuredClone(context.draft);
        const model=dialog.querySelector('[data-test-model]').value.trim();if(model)payload.model=model;
        button.disabled=true;record=await post(`${base()}/tests`,payload);mode='results';render();poll(generation);
      }
    }catch(err){error=err.message;const status=dialog.querySelector('[role="status"]');if(status)status.textContent=error;button.disabled=false;}
  });
  return {open};
}
