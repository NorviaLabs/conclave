// Local development only: serve the built Worker and reload source changes.
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {watch} from 'node:fs';
import {injectReload} from './live-reload.mjs';
import {hostConfig} from './config.mjs';

if (Number(process.versions.node.split('.')[0]) < 24) {
  throw new Error('The local Studio preview requires Node.js 24 or newer.');
}
process.chdir(fileURLToPath(new URL('..', import.meta.url)));
await import('./build.mjs');
const config=hostConfig();
let {default:worker} = await import('../dist/server/index.js');
let revision=Date.now(),reload=revision,timer,rebuilding=false;
const pending=new Set();
async function rebuild(){
  if(rebuilding)return;
  rebuilding=true;
  const changes=[...pending];pending.clear();
  try{
    const next=Date.now();
    await import(`./build.mjs?dev=${next}`);
    const updated=await import(`../dist/server/index.js?dev=${next}`);
    worker=updated.default;revision=next;
    if(changes.some(path=>!path.endsWith('.css')))reload=next;
    console.log('Studio updated: '+changes.join(', '));
  }catch(error){console.error('Studio rebuild failed:',error.message);}
  finally{rebuilding=false;if(pending.size)timer=setTimeout(rebuild,100);}
}
const watchers=['web','shared','worker'].map(directory=>watch(directory,{recursive:true},(_event,filename)=>{
  pending.add(directory+'/'+String(filename||''));clearTimeout(timer);timer=setTimeout(rebuild,100);
}));
const port = config.port;
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid STUDIO_PORT');
const origin = 'http://127.0.0.1:' + port;
const server = createServer(async (incoming, outgoing) => {
  try {
    if (incoming.headers.host !== '127.0.0.1:' + port && incoming.headers.host !== 'localhost:' + port) {
      outgoing.writeHead(403);
      outgoing.end('Use the local Studio address.');
      return;
    }
    if(incoming.url==='/__studio_reload'&&incoming.method==='GET'){
      outgoing.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});
      outgoing.end(JSON.stringify({revision,reload}));return;
    }
    const chunks = [];
    let bytes = 0;
    for await (const chunk of incoming) {
      bytes += chunk.length;
      if (bytes > 200000) {
        outgoing.writeHead(413);
        outgoing.end('Request too large.');
        return;
      }
      chunks.push(chunk);
    }
    const method = incoming.method || 'GET';
    const request = new Request('http://' + incoming.headers.host + incoming.url, {
      method,
      headers:incoming.headers,
      ...(method === 'GET' || method === 'HEAD' ? {} : {body:Buffer.concat(chunks)})
    });
    const response = await worker.fetch(request, {CONCLAVE_URL:config.apiUrl,CONCLAVE_API_TOKEN:config.apiToken,CONCLAVE_TIMEOUT_MS:config.bridgeTimeout,CONCLAVE_PROPOSAL_TIMEOUT_MS:config.proposalTimeout});
    outgoing.writeHead(response.status, Object.fromEntries(response.headers));
    const body=await response.arrayBuffer();
    outgoing.end(response.headers.get('content-type')?.startsWith('text/html')?injectReload(Buffer.from(body).toString()):Buffer.from(body));
  } catch (error) {
    console.error('Local Studio request failed:', error.message);
    if (!outgoing.headersSent) outgoing.writeHead(500);
    outgoing.end('Local Studio request failed.');
  }
});
server.listen(port, '127.0.0.1', () => {
  console.log('Conclave Studio: ' + origin);
  console.log('Conclave REST API: '+config.apiUrl);
  console.log('Live reload enabled: CSS updates in place; other changes reload the page.');
});
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    clearTimeout(timer);for(const watcher of watchers)watcher.close();
    server.close(() => { process.exit(0); });
    // Browser requests can stay open while the backend shuts down. This dev
    // server owns no durable work; close those sockets instead of waiting.
    server.closeAllConnections();
  });
}
