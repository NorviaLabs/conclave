// Load this bundle's API configuration, then start Studio.
//
// A deployment that serves the bundle itself, or proxies the API from the same
// origin, needs no configuration: the default is the host that served the page.
import {apiSettings,loadConfiguration} from '/api.js';
// A deployment may point this bundle at its own API; an unusable configuration
// falls back to the host that served the page.
window.CONCLAVE_API=apiSettings();
try{window.CONCLAVE_API=await loadConfiguration();}catch{}
// Catalog modules are loaded over REST; report a disconnected backend clearly.
try{
  await import('/studio.js');
}catch{
  document.getElementById('app').innerHTML='<main class="loading"><h1>Conclave is unavailable</h1><p>Start your configured Conclave backend, then reload Studio.</p><button class="button" id="reload-studio">Reload</button></main>';
  document.getElementById('reload-studio').addEventListener('click',()=>location.reload());
}
