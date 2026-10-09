import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {integrationManagerMarkup,connectionAssignmentMarkup} from '../web/integrations-ui.js';
// A web module loads in node once its absolute browser specifiers point at the
// file they are served from (the same trick check-canvas.mjs uses).
const load = async (path, replacements = []) => {
  let source = await readFile(new URL(path, import.meta.url), 'utf8');
  for (const [specifier, target] of replacements) source = source.replaceAll(`'${specifier}'`, JSON.stringify(target));
  return 'data:text/javascript,' + encodeURIComponent(source);
};
const catalogueUrl = new URL('../shared/catalogue.js', import.meta.url).href;
const {catalogueMarkup} = await import(await load('../web/catalogue-ui.js', [['/catalogue.js', catalogueUrl]]));
const escape=v=>String(v).replaceAll('<','&lt;').replaceAll('"','&quot;');
const services=[{id:'slack',name:'Slack',base_url:'https://slack.com',auth:'bearer',operations:[{name:'send_message',description:'Send message',capability:'write',scopes:['chat:write']}]}];
const connections=[{id:'product',name:'Product workspace',integration:'slack',applications:['app'],operations:['send_message']}];
const manager=integrationManagerMarkup(services,connections,'app',escape,true);
assert.match(manager,/type="password"/);assert.match(manager,/data-connection-create="slack"/);
assert.match(manager,/data-connection-delete="product"/);assert.match(manager,/data-connection-health="product"/);
assert.match(manager,/Approval required/);assert.doesNotMatch(manager,/private-token/);
const assigned=connectionAssignmentMarkup({id:'member',tools:['connection.product.send_message']},0,services,connections,'app',escape);
assert.match(assigned,/data-connection-tool="connection.product.send_message"/);assert.match(assigned,/checked/);
const denied=connectionAssignmentMarkup({id:'member',tools:[]},0,services,connections,'other',escape);
assert.doesNotMatch(denied,/data-connection-tool/);
console.log('PASS: connection setup, secret input, health/disconnect actions, explicit authorized assignment and write approval labels.');

// The catalogue must describe this host, not the bundled wish-list.
const hostIntegrations=[
  {id:'slack',name:'Slack',category:'Communication',group:'communication',description:'Connect an account to use Slack.',available:true,status:'available',origin:'integration',
   operations:[{name:'search_messages',description:'Search messages',capability:'read'},{name:'send_message',description:'Send message',capability:'write'}]},
  {id:'s3',name:'Amazon S3',category:'Data and storage',group:'data-and-storage',description:'Connect an account to use Amazon S3.',available:false,status:'unavailable',origin:'integration',operations:[]},
];
const catalogue=catalogueMarkup('connectors',escape,'all',{integrations:hostIntegrations,connections:[],enabled:true,application:'app'});
assert.match(catalogue,/Slack/);assert.match(catalogue,/Available — not connected yet/);
assert.match(catalogue,/1 read action · 1 change action needing approval/);
assert.match(catalogue,/Not installed on this host/);assert.match(catalogue,/Needs host setup/);
assert.doesNotMatch(catalogue,/data-catalogue-connect="s3"/);
assert.match(catalogue,/data-catalogue-connect="slack"/);
assert.doesNotMatch(catalogue,/Connected for this application/);
const connected=catalogueMarkup('connectors',escape,'all',{integrations:hostIntegrations,
  connections:[{id:'product',integration:'slack',applications:['app'],operations:['send_message']}],enabled:true,application:'app'});
assert.match(connected,/Connected for this application/);
const elsewhere=catalogueMarkup('connectors',escape,'all',{integrations:hostIntegrations,
  connections:[{id:'product',integration:'slack',applications:['other']}],enabled:true,application:'app'});
assert.doesNotMatch(elsewhere,/Connected for this application/);
const instances=catalogueMarkup('connectors',escape,'all',{integrations:[
  {id:'archive',integration:'s3',name:'Archive bucket',category:'Data and storage',group:'data-and-storage',description:'Connect an account to use Archive bucket.',available:true,status:'available',origin:'integration',operations:[{name:'read_object',description:'Read object',capability:'read'}]}],
  connections:[{id:'store',integration:'s3',instance:'archive',applications:['app'],operations:['read_object']}],enabled:true,application:'app'});
assert.match(instances,/Archive bucket/);assert.match(instances,/Connected for this application/);
assert.match(instances,/data-catalogue-connect="archive"/);
const off=catalogueMarkup('connectors',escape,'all',{integrations:hostIntegrations,connections:[],enabled:false,application:'app'});
assert.match(off,/CONCLAVE_INTEGRATION_KEY/);
console.log('PASS: the catalogue lists this host\'s integrations by category with real availability, no connected claim without a connection, and host-setup guidance for the rest.');
