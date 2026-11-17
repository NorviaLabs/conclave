// D7 — Studio's source settings say that PDF and Word files are read as text,
// with page and heading markers. Offline: no browser, backend or provider.
//
//   node studio/scripts/check-documents.mjs
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const studio = await readFile(new URL('../web/studio.js', import.meta.url), 'utf8');
const help = studio.match(/File tools read documents \([^)]*\)[^<]*/)?.[0] || '';
assert.ok(help, 'the source settings explain what file tools read');
for (const words of ['.txt', '.md', '.csv', '.html', '.pdf', '.docx', 'page', 'heading'])
  assert.ok(help.includes(words), `the source settings help names ${words}: ${help}`);
console.log('PASS: the source settings say PDF and Word files are read as text, with page and heading markers.');
