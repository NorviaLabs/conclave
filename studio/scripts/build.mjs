import {readFile,writeFile,mkdir,readdir,rm,cp} from 'node:fs/promises';
const types={'.html':'text/html;charset=utf-8','.js':'text/javascript;charset=utf-8','.css':'text/css;charset=utf-8','.py':'text/x-python;charset=utf-8','.json':'application/json;charset=utf-8','.woff2':'font/woff2','.png':'image/png','.webp':'image/webp','.jpg':'image/jpeg','.avif':'image/avif','.svg':'image/svg+xml'};
const binary=new Set(['.woff2','.png','.webp','.jpg','.avif']);
const assets={};
async function gather(path,route=''){for(const entry of await readdir(path,{withFileTypes:true})){if(entry.isDirectory())await gather(`${path}/${entry.name}`,`${route}/${entry.name}`);else{const ext=entry.name.substring(entry.name.lastIndexOf('.'));assets[`${route}/${entry.name}`]=binary.has(ext)?{body:(await readFile(`${path}/${entry.name}`)).toString('base64'),type:types[ext],binary:true}:{body:await readFile(`${path}/${entry.name}`,'utf8'),type:types[ext]||'text/plain;charset=utf-8'}}}}
await gather('web');
function browserImports(source){return source.replace("'../../conclave/application/data/defaults.json'","'/api/config/defaults'").replace("'../../conclave/application/data/catalogue.json'","'/api/config/catalogue'")}
assets['/definition.js']={body:browserImports(await readFile('shared/definition.js','utf8')),type:types['.js']};
assets['/catalogue.js']={body:browserImports(await readFile('shared/catalogue.js','utf8')),type:types['.js']};
const worker=await readFile('worker/index.js','utf8');
await rm('dist',{recursive:true,force:true});await mkdir('dist/server',{recursive:true});await mkdir('dist/.openai',{recursive:true});
await writeFile('dist/server/index.js',`const ASSETS=${JSON.stringify(assets)};\n${worker}`);
await cp('.openai/hosting.json','dist/.openai/hosting.json');console.log('Built Studio assets and REST proxy.');
