// Offline checks for the hosted UI: where it talks to, how it authenticates,
// whether it is told about a run or asks, and who may name a source.
// No browser, no server, no provider calls.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {apiSettings,apiUrl,apiHeaders,loadConfiguration} from '../web/api.js';

// The default is the host that served the page, with same-origin credentials.
const defaults=apiSettings();
assert.deepEqual(defaults,{base:'',token:'',credentials:'same-origin'});
assert.equal(apiUrl(defaults,'/api/runtime'),'/api/runtime');
assert.deepEqual(apiHeaders(defaults),{'content-type':'application/json'});

// A deployment may point the bundle at its own API, and say how it authenticates.
const hosted=apiSettings({base:'https://api.example.com/',token:' page-token ',credentials:'include'});
assert.equal(hosted.base,'https://api.example.com');
assert.equal(apiUrl(hosted,'/api/runtime'),'https://api.example.com/api/runtime');
assert.equal(hosted.credentials,'include');
assert.equal(apiHeaders(hosted).authorization,'Bearer page-token');
assert.equal(apiHeaders(hosted,{headers:{accept:'text/event-stream'}}).accept,'text/event-stream');
assert.deepEqual(apiSettings({credentials:'whenever'}).credentials,'same-origin');
assert.throws(()=>apiSettings({base:'file:///tmp/api'}),/http or https/);

// A configuration the deployment has not written is not an error: the page's own
// host answers, which is what a local host and the same-origin proxy both are.
const page={href:'http://127.0.0.1:8787/'};
assert.deepEqual(await loadConfiguration(page,async()=>new Response('',{status:404})),defaults);
assert.deepEqual(await loadConfiguration(page,async()=>{throw new Error('offline')}),defaults);
assert.deepEqual(await loadConfiguration(page,async()=>new Response(JSON.stringify({base:'https://api.example.com'}),{status:200})),
  {base:'https://api.example.com',token:'',credentials:'same-origin'});
assert.deepEqual(await loadConfiguration({href:'data:text/html,x'},async()=>{throw new Error('must not be asked')}),defaults);

// Being told about a run is a host capability, not a client preference.
const live=(await readFile(new URL('../web/live.js',import.meta.url),'utf8'))
  .replace("'/definition.js'",JSON.stringify(new URL('../shared/definition.js',import.meta.url).href))
  .replace("'/canvas-ui.js'",JSON.stringify(new URL('../web/canvas-ui.js',import.meta.url).href));
const {liveFollowMode,liveStreamPath,liveRunMarkup}=await import('data:text/javascript,'+encodeURIComponent(live));
assert.equal(liveFollowMode({features:{events:true}}),'push');
assert.equal(liveFollowMode({features:{events:false}}),'poll');
assert.equal(liveFollowMode({features:{}}),'poll');
assert.equal(liveFollowMode({}),'poll');
assert.equal(liveFollowMode(undefined),'poll');
assert.equal(liveStreamPath('run id/1'),'/api/runs/run%20id%2F1/stream');

// Hosted copy: the tenant is named, the host is not called "this computer", and
// a tenant is not told to add a folder it cannot add.
const {emptyDefinition,initialState} = await import(new URL('../shared/definition.js',import.meta.url).href);
const d=emptyDefinition();
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const markup=runtime=>liveRunMarkup(d,null,{escape,requestForm:'',outputs:'',human:'',progress:'',history:[],runtime,busy:false,error:''});
const hostedMarkup=markup({local:false,profile:'hosted',tenant:'acme',available:true,provider:'opencode',tools:[]});
assert.match(hostedMarkup,/HOSTED RUN · acme/);
assert.ok(!/this computer/.test(hostedMarkup));
const localMarkup=markup({local:true,profile:'local',available:true,provider:'codex',provider_source:'detected',tools:[]});
assert.match(localMarkup,/LOCAL RUN/);
assert.match(localMarkup,/this computer/);
assert.ok(!localMarkup.includes('· local'));
// A host that reports no profile is local, which is the server's default.
assert.match(markup({available:true,provider:'codex',tools:[]}),/LOCAL RUN/);

console.log('PASS: the hosted UI takes its API base and credentials from configuration, follows a run by push or by asking as the host allows, and keeps host copy and source settings where they belong.');
