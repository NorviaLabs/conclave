// Local development only. Execute the built Worker with a small SQLite-backed
// implementation of the D1 methods used by this application.
import {createServer} from 'node:http';
import {mkdir, readdir, readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import {fileURLToPath} from 'node:url';
import {startRuntime} from './runtime.mjs';

if (Number(process.versions.node.split('.')[0]) < 24) {
  throw new Error('The local Studio preview requires Node.js 24 or newer.');
}
process.chdir(fileURLToPath(new URL('..', import.meta.url)));
await import('./build.mjs');
await mkdir('.studio-local', {recursive:true});
const sqlite = new DatabaseSync('.studio-local/studio.sqlite');
sqlite.exec('PRAGMA journal_mode = WAL');
sqlite.exec('CREATE TABLE IF NOT EXISTS studio_local_migrations (name TEXT PRIMARY KEY)');
for (const name of (await readdir('drizzle')).filter(name => name.endsWith('.sql')).sort()) {
  if (sqlite.prepare('SELECT name FROM studio_local_migrations WHERE name = ?').get(name)) continue;
  sqlite.exec('BEGIN');
  try {
    sqlite.exec(await readFile('drizzle/' + name, 'utf8'));
    sqlite.prepare('INSERT INTO studio_local_migrations (name) VALUES (?)').run(name);
    sqlite.exec('COMMIT');
  } catch (error) {
    sqlite.exec('ROLLBACK');
    throw error;
  }
}

function prepare(sql, values = []) {
  return {
    bind(...parameters) { return prepare(sql, parameters); },
    all() { return Promise.resolve({results:sqlite.prepare(sql).all(...values)}); },
    run() {
      const result = sqlite.prepare(sql).run(...values);
      return Promise.resolve({meta:{changes:Number(result.changes)}});
    }
  };
}
const database = {
  prepare,
  async batch(statements) {
    // Current Worker batches contain writes. Keep the transaction synchronous
    // so another HTTP request cannot interleave with the batch.
    sqlite.exec('BEGIN');
    try {
      const results = statements.map(statement => statement.run());
      sqlite.exec('COMMIT');
      return Promise.all(results);
    } catch (error) {
      sqlite.exec('ROLLBACK');
      throw error;
    }
  }
};
const {default:worker} = await import('../dist/server/index.js');
const port = Number(process.env.STUDIO_PORT || 8787);
if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Invalid STUDIO_PORT');
const origin = 'http://127.0.0.1:' + port;
const runtime=process.env.STUDIO_PROVIDER?startRuntime():null;
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
    const response = await worker.fetch(request, {DB:database,STUDIO_LOCAL:true,...(runtime?{RUNS:(command,value)=>runtime.call(command,value)}:{})});
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
  console.log('Local data: studio/.studio-local/studio.sqlite');
  console.log(runtime?'Python run service enabled. AI and source access use host configuration.':'Sample preview. Set STUDIO_PROVIDER to enable the local Python run service.');
});
for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {runtime?.close();server.close(() => { sqlite.close(); process.exit(0); });});
}
