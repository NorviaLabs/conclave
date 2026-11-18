// Each source a record cites, marked with what the host found when it checked
// it against what the record's step read: found, not found, or not checkable.
// Records from runs before checks existed carry none and say so.
const MARKS={
  found:'<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M4.6 8.3l2.2 2.2 4.6-4.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  not_found:'<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
  not_checkable:'<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.6 2.2"/></svg>',
};
const WORDS={found:'found',not_found:'not found',not_checkable:'not checkable'};
export const CHECK_TEXT={
  found:'Found: the step read or fetched this',
  not_found:'Not found: the step did not read or fetch this',
  not_checkable:'Not checkable: this names nothing Conclave can look for, or the step read it in a way that cannot be checked',
};
// The checks, when they line up with the sources one for one; otherwise none.
export function sourceChecks(record){
  const refs=record?.source_refs||[],checks=record?.source_checks;
  return Array.isArray(checks)&&checks.length===refs.length&&checks.every(check=>check in WORDS)?checks:null;
}
export function sourceChipsMarkup(record,{escape:e}){
  const refs=record?.source_refs||[],checks=sourceChecks(record);
  if(refs.length>9){const found=checks?checks.filter(check=>check==='found').length:null;
    return `<span class="source-chip" title="${e(found===null?`${refs.length} sources`:`${found} of ${refs.length} sources found`)}">${refs.length}</span>`;}
  return refs.map((ref,index)=>{const check=checks?.[index];
    return check?`<span class="source-chip" data-check="${check}" title="${e(`${CHECK_TEXT[check].split(':')[0]}: ${ref}`)}">${MARKS[check]}${index+1}<span class="sr-only"> ${WORDS[check]}</span></span>`
      :`<span class="source-chip" title="${e(ref)}">${index+1}</span>`;}).join('');
}
// "1 of 3 found", or the plain count for a record nothing checked.
export function sourceSummaryText(record){
  const refs=record?.source_refs||[],checks=sourceChecks(record);
  if(!refs.length)return '';
  return checks?`${checks.filter(check=>check==='found').length} of ${refs.length} found`:`${refs.length} ${refs.length===1?'source':'sources'}`;
}
// The record detail: each source with its mark and the words for it. `link`
// draws the source itself (a record button, a safe link, or text).
export function sourceListMarkup(record,{escape:e,link,stepName=''}){
  const refs=record?.source_refs||[],checks=sourceChecks(record);
  if(!refs.length)return '<span class="detail-label">Supporting sources</span><p>No supporting source attached.</p>';
  if(!checks)return `<span class="detail-label">Supporting sources · not checked in this run</span><ul class="source-list">${refs.map(ref=>`<li>${link(ref)}</li>`).join('')}</ul>`;
  return `<span class="detail-label">Supporting sources · checked against what ${e(stepName||'its step')} read</span><ul class="source-list checked">${refs.map((ref,index)=>{const check=checks[index];
    return `<li><span class="check-mark check-${check}">${MARKS[check]}</span>${link(ref)}<span class="check-word check-${check}">${e(CHECK_TEXT[check])}</span></li>`;}).join('')}</ul>`;
}
