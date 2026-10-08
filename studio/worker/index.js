// Studio is an HTTP client. Conclave owns all persistence and execution.
function json(value,status=200){return new Response(JSON.stringify(value),{status,headers:{'content-type':'application/json;charset=utf-8','cache-control':'no-store'}})}
async function boundedBody(request,limit){
 const reader=request.body?.getReader();if(!reader)return new Uint8Array();
 const chunks=[];let bytes=0;
 for(;;){const {done,value}=await reader.read();if(done)break;bytes+=value.byteLength;if(limit!==null&&bytes>limit){await reader.cancel();return null;}chunks.push(value);}
 const body=new Uint8Array(bytes);let offset=0;for(const chunk of chunks){body.set(chunk,offset);offset+=chunk.length;}return body;
}
export default {async fetch(request,env){
 const url=new URL(request.url),path=url.pathname;
 const aliases={'/catalogue/defaults.json':'/api/config/defaults','/catalogue/bundled.json':'/api/config/catalogue','/downloads/application.py':'/api/sdk/application'};
 if(path.startsWith('/api/')||aliases[path]){
  if(!['GET','POST','PUT','DELETE'].includes(request.method))return json({error:'Method not allowed.'},405);
  if(request.method!=='GET'&&(request.headers.get('origin')!==url.origin||!request.headers.get('content-type')?.startsWith('application/json')))return json({error:'Use a same-origin JSON request.'},403);
  if(!env.CONCLAVE_URL)return json({error:'Configure the Conclave REST API address.'},503);
  try{
   const raw=env.CONCLAVE_MAX_REQUEST_BYTES??200000,limit=['none','unlimited'].includes(String(raw).toLowerCase())?null:Number(raw);
   if(limit!==null&&(!Number.isSafeInteger(limit)||limit<1))return json({error:'Invalid CONCLAVE_MAX_REQUEST_BYTES host policy.'},503);
   const body=request.method==='GET'?undefined:await boundedBody(request,limit);if(body===null)return json({error:`Request exceeds host limit of ${limit} bytes.`},413);
   const endpoint=new URL(aliases[path]||path,env.CONCLAVE_URL);endpoint.search=url.search;
   const headers={'accept':'application/json',...(request.method!=='GET'?{'content-type':'application/json'}:{}),...(env.CONCLAVE_API_TOKEN?{'authorization':'Bearer '+env.CONCLAVE_API_TOKEN}:{})};
   const response=await fetch(endpoint,{method:request.method,headers,...(request.method==='GET'?{}:{body}),signal:AbortSignal.timeout(path==='/api/proposals'?(env.CONCLAVE_PROPOSAL_TIMEOUT_MS||305000):(env.CONCLAVE_TIMEOUT_MS||15000))});
   if(path==='/downloads/application.py'&&response.ok){const sdk=await response.json();return new Response(sdk.source,{headers:{'content-type':'text/x-python;charset=utf-8','content-disposition':'attachment; filename="conclave_application.py"','cache-control':'no-store'}});}
   return new Response(response.body,{status:response.status,headers:{'content-type':'application/json;charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}});
  }catch{return json({error:'The Conclave REST API is unavailable. Check its server and address before retrying.'},503)}
 }
 if(!['GET','HEAD'].includes(request.method))return new Response('Method not allowed',{status:405});
 const key=path==='/'?'/index.html':path;
 const asset=ASSETS[key];if(!asset)return new Response('Not found',{status:404});
 return new Response(request.method==='HEAD'?null:asset.body,{headers:{'content-type':asset.type,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'same-origin',...(key.endsWith('.py')?{'content-disposition':'attachment; filename="conclave_application.py"'}:{})}});
}};
