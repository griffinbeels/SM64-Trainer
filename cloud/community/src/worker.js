import {WorkerEntrypoint} from 'cloudflare:workers';
import {createService} from './service.js';
import {Store} from './store.js';
import {config} from './config.js';
import {runMaintenance} from './maintenance.js';
export {MaintenanceAlarm} from './maintenance-alarm.js';
export class MetadataCache extends WorkerEntrypoint{
  async fetch(){const rows=await new Store(this.env.DB,config(this.env)).poolCandidates(this.ctx.props.query);return Response.json(rows,{headers:{'Cache-Control':'public, max-age=30, s-maxage=30'}});}
}
export class AssetCache extends WorkerEntrypoint{
  // A range reads one part of an object that also holds a ghost's start.
  async fetch(){const {key,range}=this.ctx.props,object=await (range?this.env.GHOSTS.get(key,{range}):this.env.GHOSTS.get(key));if(!object)return new Response(null,{status:404,headers:{'Cache-Control':'no-store'}});return new Response(object.body,{headers:{'Content-Type':'application/octet-stream','Cache-Control':'public, max-age=31536000, immutable'}});}
}
const service=createService();
export default {fetch:service.fetch,async scheduled(_controller,env,ctx){ctx.waitUntil(runMaintenance(env));}};
