// Catalog modules are loaded over REST; report a disconnected backend clearly.
try{
  await import('/studio.js');
}catch{
  document.getElementById('app').innerHTML='<main class="loading"><h1>Conclave is unavailable</h1><p>Start your configured Conclave backend, then reload Studio.</p><button class="button" id="reload-studio">Reload</button></main>';
  document.getElementById('reload-studio').addEventListener('click',()=>location.reload());
}
