// Studio connects to an independently managed Conclave REST service.
//
// A page cannot present the proxy's bearer token, and the proxy answers with its
// own headers, so a Set-Cookie from the API would never reach the browser. When
// the host authenticates browsers with a session cookie instead, the page talks
// to the API directly and sends credentials; `config.json` beside the page is
// what tells it where. A token deployment keeps the same-origin proxy, because
// only the proxy can present the token.
export function pageConfig({apiUrl,apiToken}){
  return apiToken?{base:'',credentials:'same-origin'}:{base:apiUrl,credentials:'include'};
}

export function hostConfig(env=process.env){
  function integer(name,fallback,max=Number.MAX_SAFE_INTEGER){const value=Number(env[name]??fallback);if(!Number.isSafeInteger(value)||value<1||value>max)throw new Error('Invalid '+name);return value;}
  const apiUrl=env.CONCLAVE_API_URL||'http://127.0.0.1:8080';
  const endpoint=new URL(apiUrl);if(!['http:','https:'].includes(endpoint.protocol)||endpoint.username||endpoint.password||endpoint.pathname!=='/'||endpoint.search||endpoint.hash)throw new Error('Invalid CONCLAVE_API_URL');
  const raw=env.CONCLAVE_MAX_REQUEST_BYTES??env.STUDIO_MAX_REQUEST_BYTES??200000;
  const maxRequestBytes=['none','unlimited'].includes(String(raw).toLowerCase())?null:Number(raw);
  if(maxRequestBytes!==null&&(!Number.isSafeInteger(maxRequestBytes)||maxRequestBytes<1))throw new Error('Invalid CONCLAVE_MAX_REQUEST_BYTES');
  return {apiUrl,apiToken:env.CONCLAVE_API_TOKEN,port:integer('STUDIO_PORT',8787,65535),bridgeTimeout:integer('CONCLAVE_API_TIMEOUT_MS',15000),proposalTimeout:integer('CONCLAVE_PROPOSAL_TIMEOUT_MS',305000),maxRequestBytes};
}
