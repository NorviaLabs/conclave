// Local development only. Execute the built Worker with a small SQLite-backed
// implementation of the D1 methods used by this application.
import {createServer} from 'node:http';
import {fileURLToPath} from 'node:url';
import {hostConfig} from './config.mjs';

if (Number(process.versions.node.split('.')[0]) < 24) {
  throw new Error('The local Studio preview requires Node.js 24 or newer.');
}
process.chdir(fileURLToPath(new URL('..', import.meta.url)));
await import('./build.mjs');
const config=hostConfig();
const {default:worker} = await import('../dist/server/index.js');
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
    outgoing.end(Buffer.from(await response.arrayBuffer()));
  } catch (error) {
    console.error('Local Studio request failed:', error.message);
    if (!outgoing.headersSent) outgoing.writeHead(500);
    outgoing.end('Local Studio request failed.');
  }
});
server.listen(port, '127.0.0.1', () => {
  console.log('Conclave Studio: ' + origin);
  console.log('Conclave REST API: '+config.apiUrl);
});
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {server.close(() => { process.exit(0); });});
}
