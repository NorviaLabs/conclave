// People and roles on an application: who may view, run, edit or own it.
// The mockups are docs/studio/mockups/application-roles.html. The host decides
// and enforces every role; this module only draws what it says. Loads on its own.
export const ROLES=['viewer','runner','editor','owner'];
const NEEDS={view:'viewer',run:'runner',edit:'editor',own:'owner'};
const LABEL={viewer:'Viewer',runner:'Runner',editor:'Editor',owner:'Owner'};
const GUIDE=[['Viewer','Open the application, its versions, runs, results and files, and compare runs.'],
  ['Runner','Everything a viewer may, and start, continue, pause and stop runs, approve actions and answer reviews.'],
  ['Editor','Everything a runner may, and change and save the plan, restore versions, test steps, and set its schedule and notifications.'],
  ['Owner','Everything, and choose who has access, turn events on and delete the application.']];
const article=role=>role==='editor'||role==='owner'?'an':'a';
// Whether a role allows something: the same ranking the host uses.
export function can(role,permission,{open=false}={}){
  if(open)return true;
  return ROLES.indexOf(role)>=0&&ROLES.indexOf(role)>=ROLES.indexOf(NEEDS[permission]);
}
// Why a control is not offered to this role, in words; empty when it is.
export function disabledReason(role,permission){
  if(!role||can(role,permission))return '';
  if(permission==='edit')return `Your role is ${role}: you can ${role==='runner'?'run this application':'read its runs and results'}, not change it.`;
  if(permission==='run')return `Your role is ${role}: you can read its runs and results.`;
  return `Only an owner can do this; your role is ${role}.`;
}
// Beside the application's name.
export function roleTagMarkup(application){
  if(application?.open)return '<span class="role-tag open"><span class="sr-only">Access: </span>Everyone can change</span>';
  if(!LABEL[application?.role])return '';
  return `<span class="role-tag"><span class="sr-only">Your role: </span>${LABEL[application.role]}</span>`;
}
const guideMarkup=()=>`<details class="role-guide"><summary>What each role may do</summary><dl>${GUIDE.map(([role,text])=>`<dt>${role}</dt><dd>${text}</dd>`).join('')}</dl></details>`;
function logLine(item,e,date){
  const when=date(item.at);
  if(item.change==='limited')return `${e(when)} · ${e(item.by)} limited access`;
  if(item.change==='removed')return `${e(when)} · ${e(item.by)} removed ${e(item.person)}`;
  if(item.change==='changed')return `${e(when)} · ${e(item.by)} made ${e(item.person)} ${article(item.role)} ${e(item.role)}`;
  return `${e(when)} · ${e(item.by)} added ${e(item.person)} as ${e(item.role)}`;
}
const defaultDate=iso=>{const value=iso?new Date(iso):null;return value&&!Number.isNaN(value.getTime())?value.toLocaleDateString([], {day:'numeric',month:'short',year:'numeric'}):'';};
// The People dialog. `me` is who is looking; `message` what just changed;
// `error` what the host refused; `confirmLeave` asks before someone removes themselves.
export function peopleDialogMarkup(access,{escape:e,name,me,date=defaultDate,message='',error='',confirmLeave=false}){
  const title=`<h2 id="people-title">People · ${e(name)}</h2>`;
  const alert=error?`<p class="validation" role="alert">${e(error)}</p>`:'';
  if(access.open)return `${title}<div class="access-open"><strong>Everyone in this workspace can change this application.</strong><span class="field-help">Limit access to choose who may view, run, edit or manage it. You become its owner; add others after.</span><div><button class="button primary" data-access-limit>Limit access</button></div></div>${alert}${guideMarkup()}`;
  if(confirmLeave)return `${title}<section class="panel" aria-labelledby="access-leave-title"><h3 id="access-leave-title">Remove yourself?</h3><p>You will no longer see ${e(name)} or its runs. Someone with owner access can add you back.</p><div class="actions"><button class="button primary" data-access-leave-confirm>Remove me</button><button class="button" data-access-leave-cancel>Keep my access</button></div></section>`;
  const owner=access.you==='owner',owners=access.people.filter(item=>item.role==='owner');
  const rows=access.people.map((item,index)=>{
    // One person, whatever the capitals of their sign-in name (as the host matches them).
    const self=String(item.person).toLowerCase()===String(me||'').toLowerCase(),onlyOwner=item.role==='owner'&&owners.length===1,note=`access-note-${index}`;
    const said=self&&onlyOwner&&owner?'You · the only owner, so you stay an owner':self?`You · added ${date(item.added_at)}`:`Added by ${item.added_by} · ${date(item.added_at)}`;
    const person=`<span class="person">${e(item.person)}<small id="${note}">${e(said)}</small></span>`;
    if(!owner){
      const leave=self&&!onlyOwner?`<button class="text-button" data-access-remove="${e(item.person)}" aria-label="Remove ${e(item.person)}" aria-describedby="${note}" >Leave</button>`:'<span></span>';
      return `<li>${person}<span class="role-name">${LABEL[item.role]||e(item.role)}</span>${leave}</li>`;
    }
    const locked=onlyOwner;
    const select=`<select data-access-role="${e(item.person)}" aria-label="Role of ${e(item.person)}" aria-describedby="${note}">${['owner','editor','runner','viewer'].map(role=>`<option value="${role}" ${role===item.role?'selected':locked?'disabled':''}>${LABEL[role]}</option>`).join('')}</select>`;
    return `<li>${person}${select}<button class="text-button" data-access-remove="${e(item.person)}" aria-label="Remove ${e(item.person)}" aria-describedby="${note}" ${locked?'disabled':''}>Remove</button></li>`;
  }).join('');
  const count=access.people.length;
  const add=owner?`<div class="people-add"><div class="field"><label for="access-person">Add a person</label><input id="access-person" data-access-person placeholder="name@example.com" autocomplete="off" aria-describedby="access-person-help"><p class="field-help" id="access-person-help">The name they sign in with, usually their email.</p></div><div class="field"><label for="access-new-role">Role</label><select id="access-new-role" data-access-new-role>${ROLES.map(role=>`<option value="${role}" ${role==='runner'?'selected':''}>${LABEL[role]}</option>`).join('')}</select></div><button class="button primary" data-access-add>Add</button></div>`
    :`<p class="field-help">Only an owner can add people or change roles; your role is ${e(access.you)}.</p>`;
  const log=access.log?.length?`<details class="role-guide"><summary>Recent changes</summary><ul class="access-log">${access.log.map(item=>`<li>${logLine(item,e,date)}</li>`).join('')}</ul></details>`:'';
  return `${title}<p class="field-help">${count} ${count===1?'person':'people'}. Only they can see this application and its runs.</p><ul class="people-list" aria-label="People with access">${rows}</ul>${message?`<p class="field-help" role="status">${e(message)}</p>`:''}${alert}${add}${guideMarkup()}${log}`;
}
