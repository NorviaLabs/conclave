// People and roles on an application, as Studio shows and changes them.
// The mockups are docs/studio/mockups/application-roles.html.
// Offline: no browser, backend or provider.
//
//   node studio/scripts/check-application-roles.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const url = path => new URL(path, import.meta.url).href;
const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
const ui = await import(url('../web/access-ui.js'));

// --- What a role allows, in the host's own terms --------------------------------------------
assert.deepEqual(ui.ROLES, ['viewer', 'runner', 'editor', 'owner']);
for (const [role, permission, allowed] of [['viewer', 'view', true], ['viewer', 'run', false], ['runner', 'run', true], ['runner', 'edit', false],
  ['editor', 'edit', true], ['editor', 'own', false], ['owner', 'own', true], [undefined, 'view', false]])
  assert.equal(ui.can(role, permission), allowed, `${role} ${permission}`);
assert.equal(ui.can('owner', 'view', {open: true}), true);
assert.equal(ui.disabledReason('runner', 'edit'), 'Your role is runner: you can run this application, not change it.');
assert.equal(ui.disabledReason('viewer', 'run'), 'Your role is viewer: you can read its runs and results.');
assert.equal(ui.disabledReason('editor', 'own'), 'Only an owner can do this; your role is editor.');
assert.equal(ui.disabledReason('owner', 'edit'), '');

// --- Your role, beside the application's name -------------------------------------------------
assert.equal(ui.roleTagMarkup({role: 'runner', open: false}), '<span class="role-tag"><span class="sr-only">Your role: </span>Runner</span>');
assert.equal(ui.roleTagMarkup({role: 'owner', open: true}), '<span class="role-tag open"><span class="sr-only">Access: </span>Everyone can change</span>');
assert.equal(ui.roleTagMarkup({}), '', 'a host without roles says nothing');

// --- People: an open application ---------------------------------------------------------------
const open = ui.peopleDialogMarkup({open: true, you: 'owner', people: [], log: []}, {escape, name: 'Release <notes>', me: 'ana@acme.com'});
assert.match(open, /^<h2 id="people-title">People · Release &lt;notes&gt;<\/h2><div class="access-open"><strong>Everyone in this workspace can change this application\.<\/strong><span class="field-help">Limit access to choose who may view, run, edit or manage it\. You become its owner; add others after\.<\/span><div><button class="button primary" data-access-limit>Limit access<\/button><\/div><\/div>/);
assert.match(open, /<details class="role-guide"><summary>What each role may do<\/summary><dl><dt>Viewer<\/dt><dd>Open the application, its versions, runs, results and files, and compare runs\.<\/dd>/);
assert.doesNotMatch(open, /people-list|people-add/);

// --- People: who may do what ---------------------------------------------------------------------
const access = {open: false, you: 'owner', people: [
  {person: 'ana@acme.com', role: 'owner', added_by: 'ana@acme.com', added_at: '2026-10-12T09:00:00Z'},
  {person: 'raj@acme.com', role: 'editor', added_by: 'ana@acme.com', added_at: '2026-10-14T09:00:00Z'},
  {person: 'li<x>@acme.com', role: 'viewer', added_by: 'raj@acme.com', added_at: '2026-10-20T09:00:00Z'}],
  log: [{at: '2026-10-20T09:00:00Z', by: 'raj@acme.com', person: 'li<x>@acme.com', change: 'added', role: 'viewer'},
        {at: '2026-10-14T09:00:00Z', by: 'ana@acme.com', person: 'raj@acme.com', change: 'changed', role: 'editor'},
        {at: '2026-10-13T09:00:00Z', by: 'ana@acme.com', person: 'sam@acme.com', change: 'removed', role: null}]};
const date = iso => iso.slice(0, 10);
const people = ui.peopleDialogMarkup(access, {escape, name: 'Release notes', me: 'ana@acme.com', date, message: 'raj@acme.com is now an editor.'});
assert.match(people, /<p class="field-help">3 people\. Only they can see this application and its runs\.<\/p><ul class="people-list" aria-label="People with access">/);
assert.match(people, /<li><span class="person">ana@acme\.com<small id="access-note-0">You · the only owner, so you stay an owner<\/small><\/span><select data-access-role="ana@acme\.com" aria-label="Role of ana@acme\.com" aria-describedby="access-note-0"><option value="owner" selected>Owner<\/option><option value="editor" disabled>Editor<\/option><option value="runner" disabled>Runner<\/option><option value="viewer" disabled>Viewer<\/option><\/select><button class="text-button" data-access-remove="ana@acme\.com" aria-label="Remove ana@acme\.com" aria-describedby="access-note-0" disabled>Remove<\/button><\/li>/);
assert.match(people, /<li><span class="person">raj@acme\.com<small id="access-note-1">Added by ana@acme\.com · 2026-10-14<\/small><\/span><select data-access-role="raj@acme\.com" aria-label="Role of raj@acme\.com" aria-describedby="access-note-1"><option value="owner" >Owner<\/option><option value="editor" selected>Editor<\/option>/);
assert.match(people, /li&lt;x&gt;@acme\.com/);
assert.doesNotMatch(people, /li<x>/);
assert.match(people, /<p class="field-help" role="status">raj@acme\.com is now an editor\.<\/p>/);
assert.match(people, /<div class="people-add"><div class="field"><label for="access-person">Add a person<\/label><input id="access-person" data-access-person placeholder="name@example\.com" autocomplete="off" aria-describedby="access-person-help"><p class="field-help" id="access-person-help">The name they sign in with, usually their email\.<\/p><\/div><div class="field"><label for="access-new-role">Role<\/label><select id="access-new-role" data-access-new-role><option value="viewer" >Viewer<\/option><option value="runner" selected>Runner<\/option><option value="editor" >Editor<\/option><option value="owner" >Owner<\/option><\/select><\/div><button class="button primary" data-access-add>Add<\/button><\/div>/);
assert.match(people, /<details class="role-guide"><summary>Recent changes<\/summary><ul class="access-log"><li>2026-10-20 · raj@acme\.com added li&lt;x&gt;@acme\.com as viewer<\/li><li>2026-10-14 · ana@acme\.com made raj@acme\.com an editor<\/li><li>2026-10-13 · ana@acme\.com removed sam@acme\.com<\/li><\/ul><\/details>/);
const twoOwners = ui.peopleDialogMarkup({...access, people: [access.people[0], {...access.people[1], role: 'owner'}]}, {escape, name: 'x', me: 'ana@acme.com', date});
assert.match(twoOwners, /<small id="access-note-0">You · added 2026-10-12<\/small>/, 'with another owner, you may step down');
assert.match(twoOwners, /data-access-remove="ana@acme\.com" aria-label="Remove ana@acme\.com" aria-describedby="access-note-0" >Remove/);

assert.match(ui.peopleDialogMarkup(access, {escape, name: 'x', me: 'Raj@ACME.com', date}), /raj@acme\.com<small id="access-note-1">You · added 2026-10-14<\/small>/, 'you, whatever the capitals');
// Not an owner: the list, read only, and a way to leave.
const editor = ui.peopleDialogMarkup({...access, you: 'editor'}, {escape, name: 'x', me: 'raj@acme.com', date});
assert.doesNotMatch(editor, /data-access-role|people-add|data-access-remove="ana/);
assert.match(editor, /<li><span class="person">ana@acme\.com<small id="access-note-0">Added by ana@acme\.com · 2026-10-12<\/small><\/span><span class="role-name">Owner<\/span><span><\/span><\/li>/);
assert.match(editor, /<button class="text-button" data-access-remove="raj@acme\.com" aria-label="Remove raj@acme\.com" aria-describedby="access-note-1" >Leave<\/button>/);
assert.match(editor, /<p class="field-help">Only an owner can add people or change roles; your role is editor\.<\/p>/);
// Removing yourself asks first.
assert.match(ui.peopleDialogMarkup(access, {escape, name: 'Release notes', me: 'raj@acme.com', date, confirmLeave: true}),
  /<section class="panel" aria-labelledby="access-leave-title"><h3 id="access-leave-title">Remove yourself\?<\/h3><p>You will no longer see Release notes or its runs\. Someone with owner access can add you back\.<\/p><div class="actions"><button class="button primary" data-access-leave-confirm>Remove me<\/button><button class="button" data-access-leave-cancel>Keep my access<\/button><\/div><\/section>/);
assert.match(ui.peopleDialogMarkup(access, {escape, name: 'x', me: 'ana@acme.com', date, error: 'ana@acme.com is this application’s only owner. Make someone else an owner first.'}),
  /<p class="validation" role="alert">ana@acme\.com is this application’s only owner\. Make someone else an owner first\.<\/p>/);

// --- Studio wires it in ----------------------------------------------------------------------------
const [studio, html, css] = await Promise.all(['../web/studio.js', '../web/index.html', '../web/studio.css'].map(path => readFile(new URL(path, import.meta.url), 'utf8')));
assert.match(studio, /import \{[^}]*peopleDialogMarkup[^}]*\} from '\/access-ui\.js';/);
assert.match(studio, /\/api\/applications\/\$\{encodeURIComponent\(currentId\)\}\/access/);
assert.match(studio, /data-action="open-people"/);
assert.match(studio, /action==='open-people'/);
assert.match(studio, /roleTagMarkup\(/);
assert.match(studio, /disabledReason\(/);
assert.match(html, /<dialog id="people-dialog" aria-labelledby="people-title">/);
for (const rule of ['.role-tag', '.access-open', '.people-list', '.people-add', '.role-guide', '.access-log', '.why-disabled'])
  assert.ok(css.includes(rule + '{') || css.includes(rule + ' ') || css.includes(rule + ','), `studio.css has ${rule}`);
console.log('application role checks passed');
