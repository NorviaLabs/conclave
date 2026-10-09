// Load this bundle's API configuration, then start Studio.
//
// A deployment that serves the bundle itself, or proxies the API from the same
// origin, needs no configuration: the default is the host that served the page.
import {apiSettings,apiUrl,loadConfiguration} from '/api.js';
// A deployment may point this bundle at its own API; an unusable configuration
// falls back to the host that served the page.
window.CONCLAVE_API=apiSettings();
try{window.CONCLAVE_API=await loadConfiguration();}catch{}
// Catalog modules are loaded over REST; report a disconnected backend clearly.
try{
  await import('/studio.js');
}catch(error){
  // A module that fails to load is not the same as a backend that is down, and
  // saying so sent us looking in the wrong place once already.
  console.error('Studio failed to load:', error);
  const settings=window.CONCLAVE_API;
  // Ask the address this page actually uses, and count any answer below a server
  // error as an answer: a deployment with sign-in replies 401 to this, and a
  // proxy that cannot reach its API replies 503. Only the second is "down".
  const probe=apiUrl(settings,'/api/health');
  let reachable=false;
  try{reachable=(await fetch(probe,{cache:'no-store',credentials:settings.credentials})).status<500;}catch{}
  const detail=error instanceof Error?error.message:String(error);
  const escaped=value=>String(value).replace(/[<>&]/g,character=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[character]));
  // Two of this page's modules are the deployment's configuration, imported over
  // REST. When a module will not load, their statuses say why, so they are asked
  // rather than guessed at.
  const statuses=await Promise.all(['/api/config/defaults','/api/config/catalogue'].map(async path=>{
    try{return `${path} ${(await fetch(apiUrl(settings,path),{cache:'no-store',credentials:settings.credentials})).status}`;}
    catch{return `${path} unreachable`;}
  }));
  const imported=escaped(statuses.join(' · '));
  document.getElementById('app').innerHTML=reachable
    ? `<main class="loading"><h1>Studio failed to load</h1><p>The Conclave backend answered, so this is the page rather than the server.</p><p class="field-help">${escaped(detail)}</p><p class="field-help">Modules this page imports over REST: ${imported}</p><button class="button" id="reload-studio">Reload</button></main>`
    : `<main class="loading"><h1>Conclave is unavailable</h1><p>Nothing answered at <code>${escaped(probe)}</code>. Start your Conclave backend, then reload Studio.</p><p class="field-help">If it is already running, this page's origin may not be allowed to call it: start the backend with CONCLAVE_UI_ORIGINS=${escaped(location.origin)}. The page itself reported: ${escaped(detail)}</p><p class="field-help">Modules this page imports over REST: ${imported}</p><button class="button" id="reload-studio">Reload</button></main>`;
  document.getElementById('reload-studio').addEventListener('click',()=>location.reload());
}
