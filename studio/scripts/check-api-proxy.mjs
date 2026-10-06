import assert from 'node:assert/strict';
import worker from '../worker/index.js';
const env={CONCLAVE_URL:'http://127.0.0.1:8080',CONCLAVE_API_TOKEN:'host-secret'};
const origin='http://127.0.0.1:8787';
const request=(path,method='GET',body,headers={})=>new Request(origin+path,{method,headers:{origin,'content-type':'application/json',...headers},...(body===undefined?{}:{body})});
const realFetch=globalThis.fetch;let forwarded;
try{
 globalThis.fetch=async (url,options)=>{forwarded={url:String(url),options};return new Response(JSON.stringify({status:'ok'}),{status:201});};
 let result=await worker.fetch(request('/api/runs?application_id=custom'),env);
 assert.equal(result.status,201);assert.equal(forwarded.url,'http://127.0.0.1:8080/api/runs?application_id=custom');
 assert.equal(forwarded.options.headers.authorization,'Bearer host-secret');assert.ok(!forwarded.options.headers.origin);
 assert.ok(!(await result.text()).includes('host-secret'));
 result=await worker.fetch(request('/api/applications','POST','{}'),env);assert.equal(result.status,201);
 assert.equal(new TextDecoder().decode(forwarded.options.body),'{}');
 assert.equal((await worker.fetch(request('/api/applications','POST','{}',{origin:'https://untrusted.example'}),env)).status,403);
 assert.equal((await worker.fetch(request('/api/applications','POST','x'.repeat(200001)),env)).status,413);
 assert.equal((await worker.fetch(request('/api/runtime'),{})).status,503);
 assert.equal((await worker.fetch(request('/api/runtime','PATCH','{}'),env)).status,405);
 globalThis.fetch=async()=>new Response(JSON.stringify({filename:'conclave_application.py',source:'from conclave.application import run_application\n'}));
 result=await worker.fetch(request('/downloads/application.py'),env);assert.match(result.headers.get('content-disposition'),/conclave_application.py/);assert.match(await result.text(),/from conclave.application/);
 globalThis.fetch=async()=>{throw new Error('secret backend failure');};
 result=await worker.fetch(request('/api/runtime'),env);assert.equal(result.status,503);assert.ok(!(await result.text()).includes('secret'));
}finally{globalThis.fetch=realFetch;}
console.log('PASS: Studio REST proxy paths, methods, origin gate, request bound, host-only bearer token and redacted connection failures.');
