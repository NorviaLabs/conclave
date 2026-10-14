// What the page is told about the API, and why it differs by deployment.
//
// The Studio dev server serves the page and proxies `/api/*` to the API. That
// proxy answers with its own headers, so a `Set-Cookie` from a login would never
// reach the browser and the session could not be kept. When the host
// authenticates browsers with a cookie, the page must therefore call the API
// itself — which is what `config.json` says. A token deployment keeps the proxy,
// because a page cannot present a bearer token at all.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {pageConfig, hostConfig} from './config.mjs';

const apiUrl = 'http://127.0.0.1:8080';

// A login deployment: the page goes straight to the API, credentials included.
assert.deepEqual(pageConfig({apiUrl, apiToken: undefined}), {base: apiUrl, credentials: 'include'});
assert.deepEqual(pageConfig({apiUrl: 'https://api.example.com', apiToken: ''}), {base: 'https://api.example.com', credentials: 'include'});

// A token deployment: unchanged, the page stays on its own origin so the proxy
// can add the token.
assert.deepEqual(pageConfig({apiUrl, apiToken: 'a-token'}), {base: '', credentials: 'same-origin'});

// The page's own default is already the same-origin proxy, so a config that says
// so changes nothing.
const settings = await import('../web/api.js');
assert.deepEqual(settings.apiSettings({}), {base: '', token: '', credentials: 'same-origin'});
assert.deepEqual(settings.apiSettings(pageConfig({apiUrl, apiToken: ''})),
                 {base: apiUrl, token: '', credentials: 'include'});
assert.equal(settings.apiUrl(settings.apiSettings(pageConfig({apiUrl, apiToken: ''})), '/auth/google/start'),
             apiUrl + '/auth/google/start');

// The dev server serves it, and the API address is validated once.
const dev = await readFile(new URL('./dev.mjs', import.meta.url), 'utf8');
assert.match(dev, /pathname==='\/config\.json'[\s\S]{0,220}JSON\.stringify\(page\)/, 'the dev server answers /config.json');
assert.match(dev, /const page = pageConfig\(config\)/, 'from the same config the proxy uses');

const env = {...process.env, CONCLAVE_API_URL: 'http://127.0.0.1:9001'};
assert.equal(hostConfig(env).apiUrl, 'http://127.0.0.1:9001');
assert.equal(pageConfig(hostConfig(env)).base, 'http://127.0.0.1:9001');
assert.throws(() => hostConfig({...process.env, CONCLAVE_API_URL: 'ftp://127.0.0.1:8080'}), /Invalid CONCLAVE_API_URL/);
assert.throws(() => hostConfig({...process.env, CONCLAVE_API_URL: 'http://user:pw@127.0.0.1:8080'}), /Invalid CONCLAVE_API_URL/);

console.log('PASS: a login deployment points the page at the API with credentials; a token deployment keeps the proxy.');

// A limit a person did not choose stops real work part way and reads as a
// finished run. A new application starts with no limit on time or on how much
// work it does; a person who wants one sets it.
{
  const defaults = JSON.parse(await readFile(new URL('../../conclave/application/data/defaults.json', import.meta.url), 'utf8'));
  assert.equal(defaults.run_budget.wall_clock_seconds, null, 'a new application is not put on a clock');
  assert.equal(defaults.run_config.max_tool_steps, null, 'nor is a stage capped at a number of actions');
  assert.ok(Object.values(defaults.run_budget).every(value => value === null), 'nor is the run');
}
