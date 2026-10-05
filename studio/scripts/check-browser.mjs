// Optional real-browser check; Playwright is installed outside the project.
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {featuredApplications} from '../shared/catalogue.js';

assert(process.env.STUDIO_PLAYWRIGHT_MODULE,'Set STUDIO_PLAYWRIGHT_MODULE to an external Playwright index.mjs path.');
const {chromium}=await import(pathToFileURL(process.env.STUDIO_PLAYWRIGHT_MODULE).href);
const [base,applicationId]=process.argv.slice(2);
const artifacts=process.env.STUDIO_BROWSER_ARTIFACTS||'/tmp/conclave-studio-browser';
await mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
const page=await context.newPage(),errors=[];
page.on('pageerror',error=>errors.push(error.message));
page.setDefaultTimeout(15000);
const open=async id=>{
  await page.goto(`${base}/?application=${encodeURIComponent(id)}`);
  await page.locator('[data-tab="use"][role="tab"]').click();
};
const fillRequest=async task=>{
  await page.locator('#live-request').fill(task);
  const context=page.locator('[data-disclosure="live-context"]');
  if(await context.getAttribute('open')===null)await context.locator('summary').click();
  for(const field of await page.locator('[data-request-field][data-input-mode="live"]').all()){
    const type=await field.getAttribute('type');
    await field.fill(type==='number'?'3':type==='date'?'2026-10-05':'Read source.md; supplied observations only. <img id="injected" src=x>');
  }
};
const status=page.locator('.run-overview');
try{
  await open(applicationId);
  assert.equal(await page.locator('#live-request').inputValue(),'');
  for(const field of await page.locator('[data-request-field][data-input-mode="live"]').all())assert.equal(await field.inputValue(),'');
  await page.locator('[data-action="start-live"]').click();
  await page.getByRole('alert').filter({hasText:'Describe what you need'}).waitFor();
  await fillRequest('Browser check: assess source.md and preserve uncertainty.');
  await page.locator('[data-action="start-live"]').click();
  await status.filter({hasText:'Your review is needed'}).waitFor();
  assert((await page.locator('#live-history option:checked').innerText()).includes('waiting for human'));
  assert(await page.locator('.sample-banner .demo-pill').isVisible());
  const runUrl=page.url();
  await page.screenshot({path:`${artifacts}/live-review-desktop.png`,fullPage:true});
  await page.reload();
  await status.filter({hasText:'Your review is needed'}).waitFor();
  await page.locator('[data-action="submit-live-review"]').click();
  await page.getByRole('alert').filter({hasText:'Record your reason'}).waitFor();
  await page.locator('#human-decision').selectOption('approved');
  const body=page.locator('#human-response');
  await body.fill('Checked the supplied source and limitations.');
  await body.focus();
  // Polling must preserve an unfinished review and its keyboard focus.
  await page.waitForTimeout(1600);
  assert.equal(await body.inputValue(),'Checked the supplied source and limitations.');
  assert.equal(await body.evaluate(el=>el===document.activeElement),true);
  await page.locator('[data-action="submit-live-review"]').click();
  await status.filter({hasText:'Results are ready'}).waitFor();
  await page.getByText('Original request and context',{exact:true}).click();
  assert.equal(await page.locator('#injected').count(),0);
  assert((await page.locator('[data-disclosure="live-request-details"]').innerText()).includes('<img id="injected" src=x>'));
  const downloadButton=page.locator('[data-download-output]').first();
  if(!(await downloadButton.isVisible()))await downloadButton.locator('xpath=ancestor::details[1]/summary').click();
  const [download]=await Promise.all([page.waitForEvent('download'),downloadButton.click()]);
  assert((await readFile(await download.path(),'utf8')).includes('do not establish causality'));
  await page.screenshot({path:`${artifacts}/live-completed-desktop.png`,fullPage:true});
  await page.reload();
  await status.filter({hasText:'Results are ready'}).waitFor();
  await page.locator('#live-history').selectOption(new URL(runUrl).searchParams.get('run'));
  await status.filter({hasText:'Results are ready'}).waitFor();
  await page.getByText('Start another request',{exact:true}).click();
  await fillRequest('SLOW_FIXTURE');
  await page.locator('[data-action="start-live"]').click();
  await page.locator('[data-action="cancel-live"]').waitFor();
  await page.locator('[data-action="cancel-live"]').click();
  await status.filter({hasText:/Work interrupted|Request cancelled/}).waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:`${artifacts}/live-mobile.png`,fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Mobile layout overflows');
  await page.setViewportSize({width:1440,height:1000});
  // Exercise all six featured workflows through their explicitly fictional runner.
  for(const application of featuredApplications){
    await open(application.id);
    await page.locator('[data-action="use-samples"]').click();
    assert((await page.locator('.sample-banner').innerText()).includes('SAMPLE MODE'));
    assert(await page.locator('.sample-banner .demo-pill').isVisible());
    await page.locator('[data-action="start-preview"]').click();
    await page.locator('[data-action="pause-preview"]').click();
    for(let i=0;i<60;i++){
      if(await page.locator('.run-status.complete').count())break;
      if(await page.locator('[data-action="complete-human"]').count()){
        if(await page.locator('#human-decision').count()){
          const options=await page.locator('#human-decision option').evaluateAll(items=>items.map(item=>item.value).filter(Boolean));
          await page.locator('#human-decision').selectOption(options.includes('approved')?'approved':options[0]);
        }
        await page.locator('#human-response').fill('Browser sample review only.');
        await page.locator('[data-action="complete-human"]').click();
        if(await page.locator('[data-action="pause-preview"]').count())await page.locator('[data-action="pause-preview"]').click();
      }else if(await page.locator('[data-action="step-preview"]').count()){
        await page.locator('[data-action="step-preview"]').focus();
        await page.keyboard.press('Enter');
      }
      await page.waitForTimeout(50);
    }
    await page.locator('.run-status.complete').waitFor();
    assert((await page.locator('.sample-banner').innerText()).includes('Sample results'));
    await page.screenshot({path:`${artifacts}/sample-${application.id}.png`,fullPage:true});
  }
  // Keyboard alternatives and dependency editing remain usable on the real canvas.
  await open(applicationId);
  await page.locator('[data-tab="build"][role="tab"]').click();
  await page.locator('[data-step="4"]').click();
  await page.locator('[data-canvas-mode="list"]').click();
  assert.equal(await page.locator('[data-canvas-mode="list"]').getAttribute('aria-pressed'),'true');
  const exportPlan=async()=>{
    const details=page.locator('.advanced-file-actions');
    if(await details.getAttribute('open')===null)await details.locator('summary').click();
    const [file]=await Promise.all([page.waitForEvent('download'),page.locator('[data-action="export"]').click()]);
    return JSON.parse(await readFile(await file.path(),'utf8'));
  };
  const before=await exportPlan(),first=before.workflow[0].id,second=before.workflow[1].id;
  await page.locator(`[data-plan-move="${first}"][data-direction="later"]`).focus();
  await page.keyboard.press('Enter');
  const reordered=await exportPlan();
  assert.notDeepEqual(reordered.workflow.map(step=>step.id),before.workflow.map(step=>step.id));
  assert.deepEqual(reordered.connections,before.connections,'List order changed workflow dependencies');
  await page.locator('[data-canvas-mode="canvas"]').click();
  await page.locator(`[data-plan-select="${second}"]`).click();
  await page.locator(`[data-disclosure="step-parents-${second}"] summary`).click();
  await page.locator(`[data-step-parent="${first}"][data-step-child="${second}"]`).uncheck();
  const branched=await exportPlan();
  assert(!branched.connections.some(edge=>edge.to===second),'Removing a prerequisite did not create another root');
  // Restore the edge through the keyboard-accessible checkbox, then save.
  await page.locator(`[data-step-parent="${first}"][data-step-child="${second}"]`).check();
  const edgeSet=edges=>edges.map(edge=>`${edge.from}->${edge.to}`).sort();
  assert.deepEqual(edgeSet((await exportPlan()).connections),edgeSet(before.connections));
  await page.locator('[data-action="save"]').click();
  await page.locator('#save-state').filter({hasText:'Saved · version 2'}).waitFor();
  await page.screenshot({path:`${artifacts}/plan-desktop.png`,fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: Chromium live start/validation, polling focus, reload/history, review/resume, text download, cancellation, mobile layout, six fictional workflows, keyboard step controls, independent list order, dependency editing, export and save.');
  console.log(`Screenshots: ${artifacts}`);
}catch(error){
  await page.screenshot({path:`${artifacts}/failure.png`,fullPage:true});
  console.error('Page status:',await page.locator('.run-overview').innerText().catch(()=>''));
  throw error;
}finally{
  await browser.close();
}
