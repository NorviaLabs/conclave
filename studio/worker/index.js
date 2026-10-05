import {validateDefinition, validateMember, templates} from '../shared/definition.js';
function json(value,status=200){return new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json;charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}})}
function db(env){if(!env.DB)throw new Error('Saved applications are unavailable.');return env.DB}
async function seed(database){await database.batch(templates.map(t=>database.prepare('INSERT OR IGNORE INTO applications (id, definition, revision, updated_at) VALUES (?, ?, 1, ?)').bind(t.id,JSON.stringify(t.definition),'2026-10-04T00:00:00.000Z')))}
async function body(request){const reader=request.body?.getReader();if(!reader)throw new Error('A JSON body is required.');const chunks=[];let bytes=0;for(;;){const{value,done}=await reader.read();if(done)break;bytes+=value.byteLength;if(bytes>200000){await reader.cancel();throw new Error('Application definition is too large.')}chunks.push(value)}const buffer=new Uint8Array(bytes);let offset=0;for(const c of chunks){buffer.set(c,offset);offset+=c.length}return JSON.parse(new TextDecoder().decode(buffer))}
export default {async fetch(request,env){const url=new URL(request.url),path=url.pathname;try{
 if(path.startsWith('/api/')){
  if(!['GET','POST','PUT'].includes(request.method))return json({error:'Method not allowed.'},405);
  if(request.method!=='GET'&&(request.headers.get('origin')!==url.origin||!request.headers.get('content-type')?.startsWith('application/json')))return json({error:'Use a same-origin JSON request.'},403);
  const database=db(env);
  if(path==='/api/runtime'&&request.method==='GET'){if(!env.RUNS)return json({local:!!env.STUDIO_LOCAL,available:false,reason:'Live runs need a configured local Python host.',tools:[]});const result=await env.RUNS('runtime',{});return json(result.result,result.status)}
  if(path==='/api/runs'){
   if(!env.RUNS)return json({error:'Live runs need a configured local Python host.'},503);
   if(request.method==='GET'){const result=await env.RUNS('list',{application_id:url.searchParams.get('application_id')});return json(result.result,result.status)}
   if(request.method==='POST'){
    let value;try{value=await body(request);if(Object.keys(value).sort().join(',')!=='application_id,inputs,revision,task,version'||value.version!==1||typeof value.application_id!=='string'||!Number.isInteger(value.revision)||value.revision<1)throw new Error('Choose a saved application version and provide request values.')}catch(error){return json({error:error.message},400)}
    const rows=await database.prepare('SELECT id, definition, revision FROM applications WHERE id = ?').bind(value.application_id).all(),saved=rows.results[0];
    if(!saved)return json({error:'Saved application not found.'},404);
    if(saved.revision!==value.revision)return json({error:'This application changed. Reload its saved version before starting.'},409);
    const result=await env.RUNS('start',{...value,definition:JSON.parse(saved.definition)});return json(result.result,result.status);
   }
  }
  const runMatch=path.match(/^\/api\/runs\/([a-f0-9]{32})(?:\/(resume|cancel|reviews))?$/);
  if(runMatch&&env.RUNS){const id=runMatch[1],action=runMatch[2];
   if(!action&&request.method==='GET'){const result=await env.RUNS('get',{run_id:id});return json(result.result,result.status)}
   if(action&&request.method==='POST'){let value;try{value=await body(request);if(action==='reviews'?Object.keys(value).sort().join(',')!=='output,review_id':Object.keys(value).length)throw new Error('Use the supported request fields.')}catch(error){return json({error:error.message},400)}const result=await env.RUNS(action==='reviews'?'review':action,{...value,run_id:id});return json(result.result,result.status)}
  }
  if(path==='/api/members'&&request.method==='GET'){const rows=await database.prepare('SELECT id, member, updated_at FROM member_templates ORDER BY updated_at DESC, id LIMIT 200').all();return json({members:rows.results.map(row=>({id:row.id,member:JSON.parse(row.member),updated_at:row.updated_at}))})}
  if(path==='/api/members'&&request.method==='POST'){let member;try{member=validateMember((await body(request)).member)}catch(error){return json({error:error.message},400)}const id=crypto.randomUUID(),updated_at=new Date().toISOString();await database.prepare('INSERT INTO member_templates (id, member, updated_at) VALUES (?, ?, ?)').bind(id,JSON.stringify(member),updated_at).run();return json({id,member,updated_at},201)}
  if(path==='/api/applications'&&request.method==='GET'){await seed(database);const rows=await database.prepare('SELECT id, definition, revision, updated_at FROM applications ORDER BY updated_at DESC, id').all();return json({applications:rows.results.map(r=>({id:r.id,definition:JSON.parse(r.definition),revision:r.revision,updated_at:r.updated_at}))})}
  if(path==='/api/applications'&&request.method==='POST'){let value;try{value=await body(request);validateDefinition(value.definition)}catch(error){return json({error:error.message},400)}const id=crypto.randomUUID(),updated_at=new Date().toISOString();await database.prepare('INSERT INTO applications (id, definition, revision, updated_at) VALUES (?, ?, 1, ?)').bind(id,JSON.stringify(value.definition),updated_at).run();return json({id,revision:1,updated_at,definition:value.definition},201)}
  const match=path.match(/^\/api\/applications\/([a-zA-Z0-9-]+)$/);
  if(match&&request.method==='PUT'){let value;try{value=await body(request);validateDefinition(value.definition);if(!Number.isInteger(value.revision)||value.revision<1)throw new Error('A saved revision is required.')}catch(error){return json({error:error.message},400)}const updated_at=new Date().toISOString();const result=await database.prepare('UPDATE applications SET definition = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?').bind(JSON.stringify(value.definition),updated_at,match[1],value.revision).run();if(!result.meta.changes)return json({error:'This application changed in another tab. Your draft is preserved; reload after exporting it.'},409);return json({id:match[1],revision:value.revision+1,updated_at,definition:value.definition})}
  return json({error:'Endpoint not found.'},404);
 }
 if(!['GET','HEAD'].includes(request.method))return new Response('Method not allowed',{status:405});
 const key=path==='/'?'/index.html':path==='/investigation'||path==='/investigation/'?'/investigation/index.html':path;
 const asset=ASSETS[key];if(!asset)return new Response('Not found',{status:404});
 return new Response(request.method==='HEAD'?null:asset.body,{headers:{'content-type':asset.type,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'same-origin',...(key.endsWith('.py')?{'content-disposition':'attachment; filename="conclave_application.py"'}:{})}});
 }catch(error){console.error('Application studio request failed:',error.message);return json({error:'Saved applications are unavailable. Your edits remain in this tab. Try again.'},503)}}};
