// Contrast regression checks for Studio's shipped light-theme color roles.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const css=await readFile(new URL('../web/studio.css',import.meta.url),'utf8');
const root=css.match(/:root\{([^}]+)\}/)[1];
const tokens=Object.fromEntries([...root.matchAll(/(--[\w-]+):(#\w{6})/g)].map(([,key,value])=>[key,value]));
function luminance(hex){
 const rgb=hex.slice(1).match(/../g).map(value=>parseInt(value,16)/255);
 return rgb.map(value=>value<=.04045?value/12.92:((value+.055)/1.055)**2.4)
  .reduce((sum,value,index)=>sum+value*[.2126,.7152,.0722][index],0);
}
function check(foreground,background,minimum=4.5){
 const values=[luminance(tokens[foreground]),luminance(tokens[background])].sort((a,b)=>a-b);
 const ratio=(values[1]+.05)/(values[0]+.05);
 assert.ok(ratio>=minimum,`${foreground} on ${background}: ${ratio.toFixed(2)}:1`);
}
for(const foreground of ['--text','--muted','--dim']){
 for(const background of ['--bg','--panel','--rail','--panel2'])check(foreground,background);
}
for(const action of ['--action','--action-hover'])check('--on-action',action);
for(const [text,surface] of [['--success','--success-surface'],['--info','--info-surface'],['--idle','--idle-surface'],['--action','--working-surface'],['--amber','--warning-surface'],['--red','--error-surface']])check(text,surface);
for(const foreground of ['--action','--action-hover']){
 for(const background of ['--bg','--panel','--rail'])check(foreground,background,3);
}
assert.match(css,/\.prose\{[^}]*color:var\(--text\)/);
assert.match(css,/\.code\{[^}]*color:var\(--text\)/);
assert.match(css,/\.text-button:hover\{color:var\(--action-hover\)/);
assert.match(css,/input::placeholder,textarea::placeholder\{color:var\(--muted\);opacity:1\}/);
console.log('PASS: Studio text, actions, semantic statuses, focus and placeholder color roles meet contrast thresholds.');
