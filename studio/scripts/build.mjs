import {readFile,writeFile,mkdir,readdir,rm,cp} from 'node:fs/promises';
const types={'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8','.css':'text/css;charset=utf-8','.py':'text/x-python;charset=utf-8','.json':'application/json;charset=utf-8'};
const assets={};
async function gather(path,route=''){for(const entry of await readdir(path,{withFileTypes:true})){if(entry.isDirectory())await gather(`${path}/${entry.name}`,`${route}/${entry.name}`);else{const ext=entry.name.substring(entry.name.lastIndexOf('.'));assets[`${route}/${entry.name}`]={body:await readFile(`${path}/${entry.name}`,'utf8'),type:types[ext]||'text/plain;charset=utf-8'}}}}
await gather('web');assets['/definition.js']={body:await readFile('shared/definition.js','utf8'),type:types['.js']};assets['/downloads/application.py']={body:await readFile('runtime/application.py','utf8'),type:types['.py']};
const catalogue=(await readFile('shared/catalogue.js','utf8')).replace(/^export /gm,'');
assets['/catalogue.js']={body:await readFile('shared/catalogue.js','utf8'),type:types['.js']};
const model=(await readFile('shared/definition.js','utf8')).replace(/^import .*;\n/gm,'').replace(/^export /gm,'');const worker=(await readFile('worker/index.js','utf8')).replace(/^import .*;\n/,'');
await rm('dist',{recursive:true,force:true});await mkdir('dist/server',{recursive:true});await mkdir('dist/.openai',{recursive:true});await writeFile('dist/server/index.js',`const ASSETS=${JSON.stringify(assets)};\n${catalogue}\n${model}\n${worker}`);await cp('.openai/hosting.json','dist/.openai/hosting.json');await cp('drizzle','dist/.openai/drizzle',{recursive:true});console.log('Built application studio Worker, assets and schema migrations.');
