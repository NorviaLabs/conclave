// Offline checks for Studio's half of Google sign-in: the page reads who it is
// before it fetches anything, a host that answers 401 shows one way in and
// nothing else, and the account line appears only where a login exists.
//
// No browser and no Google: the host is faked at the fetch boundary.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const source = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const css = await readFile(new URL('../web/studio.css', import.meta.url), 'utf8');
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));

// The pieces that decide sign-in, exercised as they are written.
const code = source.slice(source.indexOf('// Who this page is, as the host reports it'), source.indexOf('async function load(){'));
assert.ok(code.length > 400, 'the sign-in code was found');
const page = {innerHTML: ''};
const context = {
  escape, console, Promise, URL, encodeURIComponent, location: {href: 'https://studio.example.com/'},
  history: {replaceState() {}}, uiIcon: name => `<svg data-icon="${name}"></svg>`,
  apiSettingsNow: () => ({base: '', token: '', credentials: 'include'}),
  apiUrl: (settings, path) => path,
  $: id => (id === 'app' ? page : {}),
  account: {principal: null, login: null}, request: async () => { throw new Error('not used'); }
};
context.window = context;
// The page's own state is reached through setters, because a top-level `const`
// stays inside the script that declared it.
runInNewContext(`${code}\nglobalThis.api = {signInMarkup,signInUrl,readAccount,accountMarkup,showSignIn,
  get account(){return account}, setAccount(value){account=value;},
  set request(fn){request=fn}};`, context);

// --- The challenge ---------------------------------------------------------
context.request = async () => { const error = new Error('Sign in to continue'); error.status = 401; error.login = '/auth/google/start'; throw error; };
await context.api.readAccount();
assert.equal(context.api.account.principal, null, 'a 401 leaves the page with no principal');
assert.equal(context.api.account.login.start, '/auth/google/start', 'and remembers where sign-in starts');
const screen = context.api.signInMarkup('');
assert.match(screen, /Sign in to Conclave/, 'the page explains what it wants');
assert.match(screen, /href="\/auth\/google\/start"[^>]*>Sign in with Google</, 'and offers one way in');
assert.match(screen, /separate step, in Connections/, 'and says this is not the Google connection');
assert.ok(!/data-action="run"|application-header/.test(screen), 'the sign-in screen shows no application chrome');
assert.match(context.api.signInMarkup('That sign-in did not complete.'), /role="alert"[^>]*>That sign-in did not complete/, 'a failed return is reported on the page');

// --- A session that lapses reads the same way as a first visit -------------
page.innerHTML = '';
context.api.showSignIn();
assert.match(page.innerHTML, /Sign in to Conclave/, 'a 401 anywhere shows the sign-in screen');
assert.match(page.innerHTML, /href="\/auth\/google\/start"/, 'with the same one way in');
context.location.href = 'https://studio.example.com/?signin=failed';
context.api.showSignIn();
assert.match(page.innerHTML, /That sign-in did not complete/, 'and the page reports a failed return');
context.location.href = 'https://studio.example.com/';

// --- The account line -----------------------------------------------------
context.api.setAccount({principal: null, login: null});
assert.equal(context.api.accountMarkup(), '', 'a local host shows no account line');
context.api.setAccount({principal: {name: 'person@example.com', tenant: 'acme', label: 'person@example.com · acme'}, login: {start: '/auth/google/start', logout: '/auth/logout'}});
const chip = context.api.accountMarkup();
assert.match(chip, /person@example\.com · acme/, 'a signed-in page names who it is');
assert.match(chip, /data-action="sign-out"/, 'and offers one way out');
assert.match(chip, /data-icon="person"/, 'with an icon rather than a bare word');

// --- What the page must do -------------------------------------------------
assert.match(source, /const who=await readAccount\(\);\n  if\(!who\.principal\)\{/, 'the page asks who it is before anything else');
assert.match(source, /error\.status=response\.status;error\.login=result\.login/, 'a challenge is carried on the error, not flattened into a message');
assert.ok(source.includes("action==='sign-out'"), 'signing out is an action of the page');
assert.ok(source.includes("await request(account.login.logout,{method:'POST',body:'{}'})"), 'it tells the host, so the cookie is cleared there');
assert.ok(source.includes("finally{location.reload();}"), 'and reloads into the sign-in screen either way');
assert.match(source, /searchParams\.get\('signin'\)==='failed'/, 'a failed return is read from the URL the host sent');
assert.match(source, /function showSignIn\(message\)\{[\s\S]{0,200}\$\('app'\)\.innerHTML=signInMarkup/, 'and the screen replaces the page rather than sitting inside it');
assert.match(source, /if\(error\.login&&response\.status===401\)showSignIn\(\)/, 'any 401 that names a sign-in puts the person back on that screen');
assert.match(source, /const who=await readAccount\(\);\n  if\(!who\.principal\)\{\n    showSignIn\(\);/, 'including the first visit');
assert.match(css, /\.sign-in\{/, 'the sign-in screen is styled');
assert.match(css, /\.account\{display:flex/, 'so is the account line');
assert.match(css, /\.rail:not\(\.open\) \.account\{flex-direction:column/, 'a collapsed rail stacks it rather than clipping it');

console.log('PASS: Studio asks who it is first, shows one way in when the host demands sign-in, and names the account it is using.');
