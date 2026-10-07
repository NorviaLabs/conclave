import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {readFile} from 'node:fs/promises';
import {injectReload,reloadClient} from './live-reload.mjs';

assert.match(injectReload('<body>Hello</body>'),/\/__studio_reload/);
assert.equal(injectReload('plain'),'plain');
let tick,current={revision:1,reload:1},reloads=0;
const link={href:'http://studio.local/studio.css'},original=link.href;
runInNewContext(reloadClient.slice('<script>'.length,-'</script>'.length),{
  setInterval:callback=>{tick=callback;},
  fetch:async()=>({ok:true,json:async()=>current}),URL,
  document:{querySelectorAll:()=>[link]},location:{reload:()=>reloads++}
});
await tick();assert.equal(reloads,0);
current={revision:2,reload:1};await tick();
assert.notEqual(link.href,original);assert.equal(reloads,0,'CSS preserves page state');
current={revision:3,reload:3};await tick();assert.equal(reloads,1);
await tick();assert.equal(reloads,1,'unchanged builds do not reload repeatedly');
const dev=await readFile(new URL('./dev.mjs',import.meta.url),'utf8');
assert.match(dev,/\['web','shared','worker'\]/);
const worker=await readFile(new URL('../worker/index.js',import.meta.url),'utf8');
assert.doesNotMatch(worker,/__studio_reload|injectReload/,'production has no reload endpoint or client');
console.log('PASS: dev-only injection, CSS refresh, page reload, and unchanged revision handling.');
