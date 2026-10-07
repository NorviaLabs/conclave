// Exercise the dev server's actual signal handlers without sockets or a backend.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';

const source=await readFile(new URL('./dev.mjs',import.meta.url),'utf8');
const handlers=new Map();
let closed,callback;
const calls=[];
runInNewContext(source.slice(source.lastIndexOf("for (const signal of ['SIGINT', 'SIGTERM'])")),{
  timer:null,clearTimeout:()=>{},watchers:[{close:()=>{}}],
  process:{on:(signal,handler)=>handlers.set(signal,handler),exit:code=>calls.push(['exit',code])},
  server:{
    close:done=>{closed=true;callback=done;calls.push('close');},
    closeAllConnections:()=>{assert(closed,'stop accepting requests before closing active sockets');calls.push('connections');callback();}
  }
});
for(const signal of ['SIGINT','SIGTERM']){
  calls.length=0;closed=false;
  handlers.get(signal)();
  assert.deepEqual(calls,['close','connections',['exit',0]],signal+' closes active requests without waiting');
}
console.log('PASS: Studio dev shutdown stops listening and closes active connections on SIGINT and SIGTERM.');
