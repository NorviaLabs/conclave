// Studio connects to an independently managed Conclave REST service.
export function hostConfig(env=process.env){
  function integer(name,fallback,max=Number.MAX_SAFE_INTEGER){const value=Number(env[name]??fallback);if(!Number.isSafeInteger(value)||value<1||value>max)throw new Error('Invalid '+name);return value;}
  const apiUrl=env.CONCLAVE_API_URL||'http://127.0.0.1:8080';
  const endpoint=new URL(apiUrl);if(!['http:','https:'].includes(endpoint.protocol)||endpoint.username||endpoint.password||endpoint.pathname!=='/'||endpoint.search||endpoint.hash)throw new Error('Invalid CONCLAVE_API_URL');
  return {apiUrl,apiToken:env.CONCLAVE_API_TOKEN,port:integer('STUDIO_PORT',8787,65535),bridgeTimeout:integer('CONCLAVE_API_TIMEOUT_MS',15000),proposalTimeout:integer('CONCLAVE_PROPOSAL_TIMEOUT_MS',305000)};
}
