// Development-only pipe to the Python runtime. No provider credentials or
// execution settings are accepted from browser requests.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

export function startRuntime(){
  const child=spawn('uv',['run','python','-m','studio.runtime.host'],{
    cwd:fileURLToPath(new URL('../..',import.meta.url)),
    stdio:['pipe','pipe','ignore'],env:process.env
  });
  const pending=new Map();let serial=0,closed=false;
  const unavailable=()=>{
    closed=true;
    for(const {resolve,timer} of pending.values()){clearTimeout(timer);resolve({status:503,result:{error:'The local Python runtime is unavailable.'}});}
    pending.clear();
  };
  child.on('error',unavailable);child.on('exit',unavailable);
  child.stdin.on('error',unavailable);
  createInterface({input:child.stdout}).on('line',line=>{
    let value;try{value=JSON.parse(line);}catch{return;}
    const waiter=pending.get(value.id);if(!waiter)return;
    pending.delete(value.id);clearTimeout(waiter.timer);waiter.resolve(value);
  });
  return {
    async call(command,value={}){
      if(closed)return {status:503,result:{error:'The local Python runtime is unavailable.'}};
      const id=++serial;
      return await new Promise(resolve=>{
        const timer=setTimeout(()=>{pending.delete(id);resolve({status:503,result:{error:'The local runtime did not respond. Check saved requests before retrying.'}});},15000);
        pending.set(id,{resolve,timer});
        child.stdin.write(JSON.stringify({id,command,value})+'\n');
      });
    },
    close(){child.stdin.end();}
  };
}
