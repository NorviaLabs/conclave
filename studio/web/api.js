// Where this bundle talks to the API, and how it authenticates.
//
// The default is the host that served the page, which is what a local
// `conclave serve` and the same-origin proxy both are. A deployment that serves
// this bundle from somewhere else writes its own config.json beside the page:
//
//   {"base": "https://api.example.com", "credentials": "include"}
//
// `credentials: "include"` is what a gateway that authenticates the user with a
// session cookie needs. A token here is visible to anyone who can load the page,
// so it is for a deployment that accepts that, not for a secret.

export function apiSettings(value){
  const config=value&&typeof value==='object'?value:{};
  const base=typeof config.base==='string'?config.base.trim().replace(/\/+$/,''):'';
  if(base&&!/^https?:\/\//.test(base))throw new Error('The API base must be an http or https URL.');
  return {base,token:typeof config.token==='string'?config.token.trim():'',
          credentials:['omit','same-origin','include'].includes(config.credentials)?config.credentials:'same-origin'};
}

export async function loadConfiguration(location=globalThis.location,fetchImpl=globalThis.fetch){
  // A page served from a data: or file: URL has no server to ask.
  if(!location||!['http:','https:'].includes(new URL(location.href).protocol))return apiSettings();
  try{
    const response=await fetchImpl(new URL('/config.json',location.href).href,{cache:'no-store',credentials:'same-origin'});
    return apiSettings(response.ok?await response.json():null);
  }catch{return apiSettings();}
}

export function apiUrl(settings,path){
  return `${settings?.base||''}${path}`;
}

export function apiHeaders(settings,options={}){
  return {'content-type':'application/json',...(settings?.token?{authorization:`Bearer ${settings.token}`}:{}),...(options.headers||{})};
}
