// The panel where a person approves or rejects one external action.
//
// Most writes show their arguments. A write that previews itself — publishing a
// report to Notion — shows where it goes and what it will write: the
// destination, the block count, what the conversion had to change, and the
// page drawn from the very blocks that will be sent. Nothing here parses
// Markdown: the preview comes from the backend's own conversion.

const VIEWS = [['preview', 'Notion preview'], ['markdown', 'Markdown'], ['raw', 'Raw request']];
const SAFE_LINK = /^(https?:\/\/|mailto:)/i;
// Where a link really goes, as a person reads it: its text can say anything.
function linkHost(url) {
  let host = '';
  if (/^mailto:/i.test(url)) host = url.slice(7).split('?')[0].split('@').pop();
  else { try { host = new URL(url).hostname; } catch { host = ''; } }
  host = host.toLowerCase();
  return host.startsWith('www.') ? host.slice(4) : host;
}
const plural = (n, word) => `${Number(n).toLocaleString('en-US')} ${word}${n === 1 ? '' : 's'}`;

function richMarkup(runs, e) {
  const list = Array.isArray(runs) ? runs : [];
  return list.map((run, index) => {
    const marks = run?.annotations || {};
    let html = e(run?.text?.content ?? '');
    if (marks.code) html = `<code>${html}</code>`;
    if (marks.bold) html = `<strong>${html}</strong>`;
    if (marks.italic) html = `<em>${html}</em>`;
    if (marks.strikethrough) html = `<s>${html}</s>`;
    const url = run?.text?.link?.url;
    if (typeof url === 'string' && SAFE_LINK.test(url)) {
      // One link can span several runs (mixed formatting): its host shows once, after it.
      const continues = list[index + 1]?.text?.link?.url === url;
      html = `<a href="${e(url)}" target="_blank" rel="noopener noreferrer">${html}</a>${continues ? '' : `<span class="link-host" title="${e(url)}">↗ ${e(linkHost(url))}</span>`}`;
    }
    return html;
  }).join('');
}

// Notion blocks drawn as Notion shows them; consecutive list items share a list.
export function notionBlocksMarkup(blocks, e) {
  const list = Array.isArray(blocks) ? blocks : [];
  let html = '';
  for (let index = 0; index < list.length;) {
    const block = list[index], type = block?.type, body = block?.[type] || {};
    if (type === 'bulleted_list_item' || type === 'numbered_list_item') {
      const tag = type === 'bulleted_list_item' ? 'ul' : 'ol';
      let items = '';
      while (index < list.length && list[index]?.type === type) {
        const item = list[index][type] || {};
        items += `<li>${richMarkup(item.rich_text, e)}${item.children?.length ? notionBlocksMarkup(item.children, e) : ''}</li>`;
        index++;
      }
      html += `<${tag}>${items}</${tag}>`;
      continue;
    }
    if (/^heading_[123]$/.test(type)) html += `<h${type.at(-1)}>${richMarkup(body.rich_text, e)}</h${type.at(-1)}>`;
    else if (type === 'paragraph') html += `<p>${richMarkup(body.rich_text, e)}</p>`;
    else if (type === 'quote') html += `<blockquote>${richMarkup(body.rich_text, e)}</blockquote>`;
    else if (type === 'divider') html += '<hr>';
    else if (type === 'code') html += `<figure class="notion-code"><figcaption>${e(body.language || 'plain text')}</figcaption><pre>${e((body.rich_text || []).map(run => run?.text?.content ?? '').join(''))}</pre></figure>`;
    else if (type === 'to_do') html += `<div class="to-do${body.checked ? ' done' : ''}"><span class="box" aria-hidden="true">${body.checked ? '✓' : ''}</span><span>${richMarkup(body.rich_text, e)}</span></div>${body.children?.length ? `<div class="to-do-children">${notionBlocksMarkup(body.children, e)}</div>` : ''}`;
    else if (type === 'table') {
      const header = !!body.has_column_header;
      html += `<table>${(body.children || []).map((row, rowIndex) => `<tr>${(row?.table_row?.cells || []).map(cell => {
        const tag = header && rowIndex === 0 ? 'th' : 'td';
        return `<${tag}>${richMarkup(cell, e)}</${tag}>`;
      }).join('')}</tr>`).join('')}</table>`;
    }
    index++;
  }
  return html;
}

function destinationMarkup(item, preview, e) {
  const destination = preview.destination || {}, create = preview.operation === 'create_page';
  const named = destination.name ? e(destination.name) : 'A page not named in this connection';
  const marker = destination.allowed === true ? ' <span class="allowed-page">✓ allowed page</span>'
    : destination.allowed === false ? ' <span class="not-allowed-page">not an allowed page</span>' : '';
  return `<dl class="publish-destination"><dt>Connection</dt><dd><code>${e(item.tool)}</code></dd>`
    + `<dt>${create ? 'New page under' : 'Appends to'}</dt><dd>${named}${marker}<br><code>${e(destination.page_id)}</code></dd>`
    + `${create && preview.title ? `<dt>Title</dt><dd><strong>${e(preview.title)}</strong></dd>` : ''}</dl>`;
}

function decisionMarkup(item, e, approveLabel = 'Approve this action') {
  return `<button class="button primary" data-tool-approval="${e(item.id)}" data-approved="true">${approveLabel}</button>`
    + `<button class="button remove" data-tool-approval="${e(item.id)}" data-approved="false">Reject</button>`;
}

// The content of one approval's preview, in the chosen view, from its detail
// (fetched once). Kept per detail and view: polling never rebuilds it.
const contents = new WeakMap();
export function approvalPreviewContent(item, detail, view, e) {
  if (!detail?.preview) return '<p class="notion-loading">Loading the preview…</p>';
  const views = contents.get(detail) || new Map();
  contents.set(detail, views);
  if (!views.has(view)) {
    const preview = detail.preview, create = preview.operation === 'create_page';
    views.set(view, view === 'markdown' ? `<pre class="code">${e(detail.arguments?.markdown ?? '')}</pre>`
      : view === 'raw' ? `<pre class="code">${e(JSON.stringify(detail.arguments, null, 2))}</pre>`
      : `${create && preview.title ? `<div class="notion-title">${e(preview.title)}</div>` : '<p class="notion-existing">Appended below the page’s existing content ↓</p>'}${notionBlocksMarkup(preview.blocks, e)}`);
  }
  return views.get(view);
}

// Fill each preview placeholder once: a render leaves a placeholder whose key is
// unchanged alone, so a filled preview is neither re-parsed nor re-diffed.
export function fillApprovalPreviews(root, items, {escape: e, details = {}, views = {}} = {}) {
  for (const element of root.querySelectorAll('.notion-preview[data-render-key]')) {
    const key = element.dataset.renderKey, id = key.slice(0, key.lastIndexOf(':')), view = key.slice(key.lastIndexOf(':') + 1);
    const item = (items || []).find(candidate => String(candidate.id) === id);
    const state = `${key}:${details[id]?.preview ? 'ready' : 'loading'}`;
    if (!item || element.dataset.filled === state) continue;
    element.innerHTML = approvalPreviewContent(item, details[id], view, e);
    element.dataset.filled = state;
  }
}

function notionArticle(item, e, view) {
  const preview = item.preview, create = preview.operation === 'create_page';
  const requests = Number(preview.requests) || 0;
  const notes = Array.isArray(preview.notes) ? preview.notes : [];
  const shown = VIEWS.some(([key]) => key === view) ? view : 'preview';
  const body = `<div class="notion-preview" data-render-key="${e(item.id)}:${shown}"></div>`;
  return `<article data-approval-preview="notion_publish"><strong>${e(item.agent)} · ${create ? 'Create report page' : 'Append to page'} in Notion</strong>`
    + destinationMarkup(item, preview, e)
    + `<p class="publish-facts"><strong>${plural(Number(preview.total_blocks) || 0, 'Notion block')}</strong>${requests > 1 ? ` · ${plural(requests, 'request')} to Notion` : ''}</p>`
    + (notes.length ? `<ul class="publish-notes">${notes.map(note => `<li>${e(note)}</li>`).join('')}</ul>` : '')
    + `<div class="approval-views" role="group" aria-label="Show the content as">${VIEWS.map(([key, label]) => `<button type="button" data-approval-view="${key}" data-approval-id="${e(item.id)}" aria-pressed="${shown === key}">${label}</button>`).join('')}</div>`
    + body
    + `<p class="publish-hint">Nothing is written to Notion until you approve.</p>`
    + decisionMarkup(item, e, 'Approve and publish') + '</article>';
}

export function toolApprovalMarkup(items, {escape: e, views = {}} = {}) {
  if (!Array.isArray(items) || !items.length) return '';
  const articles = items.map(item => item?.preview?.kind === 'notion_publish'
    ? notionArticle(item, e, views[item.id])
    : `<article><strong>${e(item.agent)} · ${e(item.tool)}</strong><pre class="code">${e(JSON.stringify(item.arguments, null, 2))}</pre>${decisionMarkup(item, e)}</article>`);
  return `<section class="tool-approval-panel" role="region" aria-label="External action approval"><h3>Review external action</h3><p>Approval applies to this call only. Check the destination and content before proceeding.</p>${articles.join('')}</section>`;
}
