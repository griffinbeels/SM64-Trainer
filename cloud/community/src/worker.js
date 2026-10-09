import {WorkerEntrypoint} from 'cloudflare:workers';
import {createService} from './service.js';
import {Store} from './store.js';
import {config} from './config.js';
import {reconcile} from './reconcile.js';
import {regrade} from './regrade.js';
import {deleteIdentities} from './identity-deletion.js';
export class MetadataCache extends WorkerEntrypoint{
  async fetch(){const rows=await new Store(this.env.DB,config(this.env)).candidates(this.ctx.props.query);return Response.json(rows,{headers:{'Cache-Control':'public, max-age=30, s-maxage=30'}});}
}
export class AssetCache extends WorkerEntrypoint{
  async fetch(){const object=await this.env.GHOSTS.get(this.ctx.props.key);if(!object)return new Response(null,{status:404,headers:{'Cache-Control':'no-store'}});return new Response(object.body,{headers:{'Content-Type':'application/octet-stream','Cache-Control':'public, max-age=31536000, immutable'}});}
}
const service=createService();
export default {fetch:service.fetch,async scheduled(_controller,env,ctx){ctx.waitUntil((async()=>{await deleteIdentities(env);await regrade(env);await reconcile(env);})());}};
