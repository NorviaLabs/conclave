// Files a run stored from its steps' outputs, as Studio shows them. The mockups
// are docs/studio/mockups/output-files.html. The host makes every file and
// checks it before it is sent; this module only draws what the run record says.
const DOWNLOAD_ICON='<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M12 4v11m0 0l-4-4m4 4l4-4M5 19h14"></path></svg>';
const WARN_ICON='<svg aria-hidden="true" viewBox="0 0 16 16"><path d="M8 1.5l7 12.5H1z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M8 6v4M8 12v.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
// The same rules as conclave/application/output_files.py.
const DEFAULT_FORMAT={document:'markdown',dataset:'csv',artifact:'json'};
const ALLOWED={document:['markdown'],dataset:['csv','json'],artifact:['json']};
const EXTENSION={markdown:'.md',csv:'.csv',json:'.json'};
const LABEL={markdown:'Markdown',csv:'CSV',json:'JSON'};
const ICON={markdown:'MD',csv:'CSV',json:'JSON'};
const MADE_FROM={markdown:'the recorded text',csv:'the recorded rows',json:'the recorded entries'};
const plural=(count,word)=>`${count} ${word}${count===1?'':'s'}`;
export function fileSizeText(bytes){
  const size=Math.max(0,Number(bytes)||0);
  if(size<1024)return `${Math.round(size)} B`;
  if(size<1024*1024)return `${(size/1024).toFixed(1)} KB`;
  return `${(size/1024/1024).toFixed(1)} MB`;
}
export function fileFormatFor(collection){
  const kind=collection?.kind;
  if(!DEFAULT_FORMAT[kind]||collection.file===false)return null;
  return ALLOWED[kind].includes(collection.file?.format)?collection.file.format:DEFAULT_FORMAT[kind];
}
const slug=text=>String(text||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,60).replace(/-+$/,'');
export function fileNameFor(collection){
  const stem=slug(collection?.label)||slug(collection?.key).slice(0,60)||'output';
  return stem+EXTENSION[fileFormatFor(collection)||'json'];
}
// The run a file's checked link belongs to, read from the link itself.
const runOf=file=>{const match=/^\/api\/runs\/([^/]+)\/files\//.exec(String(file?.link||''));try{return match?decodeURIComponent(match[1]):'';}catch{return '';}};
const downloadAttributes=(file,e)=>`data-file-download="${e(file.id)}" data-file-run="${e(runOf(file))}"`;
function problemMarkup(){
  return `<div class="file-problem" role="status">${WARN_ICON}<span><strong>This file is missing or was changed</strong> since the run stored it, so it is not offered for download. Run the step again to make a new one.</span></div>`;
}
// One stored file: its name, what it is, the first lines and its download.
export function fileCardMarkup(file,{escape:e,label='',earlier=[],busy=false,error=false}){
  const meta=[label,LABEL[file.format]||file.format,fileSizeText(file.size),file.round>1?`round ${file.round}`:''].filter(Boolean).join(' · ');
  const button=file.available?`<button class="button primary" ${downloadAttributes(file,e)} aria-label="Download ${e(file.name)}" ${busy||error?'disabled':''}>${DOWNLOAD_ICON}<span>${busy?'Downloading…':'Download'}</span></button>`:'<span></span>';
  const shown=String(file.preview||'').split('\n').length;
  const body=!file.available?problemMarkup():`<pre class="file-preview" tabindex="0" aria-label="First lines of ${e(file.name)}">${e(file.preview)}</pre>${file.lines>shown?`<span class="file-preview-note">First ${shown} of ${file.lines} lines. Download the file to read all of it.</span>`:''}`;
  const refused=error?'<p class="validation" role="alert">The download was refused: this file is missing or was changed since the run stored it.</p>':'';
  const versions=earlier.length?`<details class="file-versions"><summary>Earlier versions · ${earlier.length}</summary><ul>${earlier.map(item=>`<li><span>${e(item.name)} · round ${e(item.round)} · ${fileSizeText(item.size)}</span>${item.available?`<button class="text-button" ${downloadAttributes(item,e)} aria-label="Download ${e(item.name)}, round ${e(item.round)}">Download round ${e(item.round)}</button>`:'<span class="field-help">missing or changed</span>'}</li>`).join('')}</ul></details>`:'';
  return `<article class="file-card" aria-labelledby="file-${e(file.id)}"><div class="file-head"><span class="file-icon" aria-hidden="true">${ICON[file.format]||'FILE'}</span><span><span class="file-name" id="file-${e(file.id)}">${e(file.name)}</span><span class="file-meta">${e(meta)}</span></span>${button}</div>${body}${refused}${versions}</article>`;
}
// An output that has a file, in a run from before files were stored.
export function noFileMarkup(label,{escape:e}){
  const id=`file-none-${slug(label)||'output'}`;
  return `<article class="file-card" aria-labelledby="${e(id)}"><div class="file-head"><span class="file-icon" aria-hidden="true">—</span><span><span class="file-name" id="${e(id)}">${e(label)}</span><span class="file-meta">No stored file for this run: it ran before results were stored as files.</span></span><span></span></div></article>`;
}
// Each step's latest file, and the rounds before it.
export function filesByStage(files){
  const grouped={};
  for(const file of files||[]){const group=grouped[file.stage]??={latest:null,earlier:[]};if(!group.latest||file.round>=group.latest.round){if(group.latest)group.earlier.push(group.latest);group.latest=file;}else group.earlier.push(file);}
  for(const group of Object.values(grouped))group.earlier.sort((a,b)=>a.round-b.round);
  return grouped;
}
// The selected step's own file, in its details.
export function stepFileMarkup(files,stepId,{escape:e}){
  const file=filesByStage(files)[stepId]?.latest;
  if(!file)return '';
  const button=file.available?`<button class="button" ${downloadAttributes(file,e)} aria-label="Download ${e(file.name)}" title="Download ${e(file.name)}" >${DOWNLOAD_ICON}</button>`:'<span></span>';
  return `<h4>File</h4><article class="file-card" aria-labelledby="file-${e(file.id)}"><div class="file-head"><span class="file-icon" aria-hidden="true">${ICON[file.format]||'FILE'}</span><span><span class="file-name" id="file-${e(file.id)}">${e(file.name)}</span><span class="file-meta">${e([LABEL[file.format]||file.format,fileSizeText(file.size)].join(' · '))}</span></span>${button}</div>${file.available?'':problemMarkup()}</article>`;
}
// Set up › Results: which file an output becomes.
export function storedFileFieldMarkup(collection,index,{escape:e}){
  const allowed=ALLOWED[collection?.kind];
  if(!allowed)return '';
  const chosen=fileFormatFor(collection)||'none',id=`output-file-${index}`;
  const options=[...allowed.map(format=>[format,`${LABEL[format]} (${EXTENSION[format]})`]),['none','No file']];
  const help=chosen==='none'?'This result is not stored as a file.'
    :`Each run stores this result as <strong>${e(fileNameFor(collection))}</strong>, made by Conclave from ${MADE_FROM[chosen]}. Agents never choose the file or its address.`;
  return `<div class="field"><label for="${id}">Stored file</label><select id="${id}" data-file-format="${e(index)}" aria-describedby="${id}-help">${options.map(([value,text])=>`<option value="${value}" ${value===chosen?'selected':''}>${text}</option>`).join('')}</select><p class="field-help" id="${id}-help">${help}</p></div>`;
}
// The `file` setting a choice makes: none for the usual file, false for no file.
export function fileSetting(collection,choice){
  if(choice==='none')return false;
  return choice===DEFAULT_FORMAT[collection?.kind]?undefined:{format:choice};
}
// Testing a step stores nothing; it says what a run would store.
export function wouldStoreMarkup(collection,{escape:e}){
  if(!fileFormatFor(collection))return '';
  return `<div class="file-would">${DOWNLOAD_ICON}<span>A run would store this result as <strong>${e(fileNameFor(collection))}</strong>. Tests do not store files.</span></div>`;
}
