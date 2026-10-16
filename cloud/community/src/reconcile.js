import {Store} from './store.js';
import {config} from './config.js';
import {ServiceError} from './errors.js';

export async function reconcile(env,{limit=20}={}) {
  const s=new Store(env.DB,config(env)),now=Math.floor(Date.now()/1000);
  let deleted=0,retried=0;
  const publish=g=>s.publish(g);
  // Each intent has an independent retry time. One faulty object/provider
  // cannot hold the first batch forever and starve later legitimate cleanup.
  const intent=async(table,key,run)=>{
    try{await run();}
    catch{
      retried++;
      await s.sql('UPDATE '+table+' SET retry_at=? WHERE '+(table==='assets'?'key':'id')+'=?',now+900,key).run();
    }
  };
  await s.sql('DELETE FROM pins WHERE rowid IN (SELECT rowid FROM pins WHERE expires_at<=? LIMIT ?)',now,limit*5).run();
  for(const g of await s.rows("SELECT * FROM grants WHERE state='pending' AND expires_at<=? AND retry_at<=? ORDER BY retry_at,id LIMIT ?",now,now,limit)) {
    await intent('grants',g.id,async()=>{
      try{await s.release(g);}catch(e){if(!(e instanceof ServiceError)||e.code!=='conflict')throw e;}
    });
  }
  // Abort fences expired multipart work before refund. A completed object is
  // still strongly visible and remains physically charged before publication.
  for(const g of await s.rows("SELECT * FROM grants WHERE ((state='receiving' AND expires_at<=?) OR state='recovering') AND retry_at<=? ORDER BY retry_at,id LIMIT ?",now,now,limit)) {
    await intent('grants',g.id,async()=>{
      try{await s.tx("EXISTS(SELECT 1 FROM grants WHERE id=? AND state IN ('receiving','recovering'))",[g.id],[s.sql("UPDATE grants SET state='recovering' WHERE id=?",g.id)]);}
      catch(e){if(e instanceof ServiceError&&e.code==='conflict')return;throw e;}
      await s.ticket(g.uid,{classA:g.multipart_id?1:0,classB:1,maintenance:true});
      if(g.multipart_id) {
        try{await env.GHOSTS.resumeMultipartUpload(g.asset_key,g.multipart_id).abort();}
        catch(error){if(!/does not exist|NoSuchUpload|not found|already completed|already aborted/i.test(String(error)))throw error;}
      }
      const object=await env.GHOSTS.head(g.asset_key);
      if(object) {
        if(object.size!==g.encoded_bytes||object.customMetadata?.encoded_sha256!==g.encoded_sha256)throw Error('Stored intent integrity mismatch');
        try{await s.stored(g);}catch(e){if(e instanceof ServiceError&&e.code==='conflict')return;throw e;}
        await publish(g);
      }else await s.release(g);
    });
  }
  for(const g of await s.rows("SELECT * FROM grants WHERE state='stored' AND retry_at<=? ORDER BY retry_at,id LIMIT ?",now,limit)) {
    await intent('grants',g.id,()=>publish(g));
  }
  // Pins may outlive one bounded prune. Keep all FK references until drained;
  // never delete R2 first and then discover a surviving pin blocks D1 removal.
  const assets=await s.rows("SELECT x.* FROM assets x WHERE x.retry_at<=? AND (x.state='deleting' OR (x.created_at<? AND NOT EXISTS(SELECT 1 FROM winners w JOIN recordings r ON r.id=w.recording_id WHERE r.asset_key=x.key) AND NOT EXISTS(SELECT 1 FROM pins p JOIN recordings r ON r.id=p.recording_id WHERE r.asset_key=x.key))) ORDER BY x.retry_at,x.key LIMIT ?",now,now-900,limit);
  for(const a of assets) {
    await intent('assets',a.key,async()=>{
      try{await s.tx("EXISTS(SELECT 1 FROM assets WHERE key=?) AND NOT EXISTS(SELECT 1 FROM winners w JOIN recordings r ON r.id=w.recording_id WHERE r.asset_key=?) AND NOT EXISTS(SELECT 1 FROM pins p JOIN recordings r ON r.id=p.recording_id WHERE r.asset_key=?)",[a.key,a.key,a.key],[s.sql("UPDATE assets SET state='deleting' WHERE key=?",a.key)]);}
      catch(e){if(e instanceof ServiceError&&e.code==='conflict')return;throw e;}
      await s.ticket(a.uid,{classA:1,maintenance:true});
      await env.GHOSTS.delete(a.key);
      await s.tx("EXISTS(SELECT 1 FROM assets WHERE key=? AND state='deleting')",[a.key],[s.sql('DELETE FROM recordings WHERE asset_key=?',a.key),s.sql('DELETE FROM assets WHERE key=?',a.key),s.sql('UPDATE accounts SET bytes_used=bytes_used-? WHERE uid=?',a.encoded_bytes,a.uid),s.sql('UPDATE storage_budget SET bytes_used=bytes_used-? WHERE id=1',a.encoded_bytes)]);
      deleted++;
    });
  }
  // These indexed bounds drain above the normal admission rate. Maintenance
  // accounting is separate and keeps only its own last seven UTC daily rows.
  const oldDay=new Date(Date.now()-7*86400000).toISOString().slice(0,10);
  await s.sql('DELETE FROM quotas WHERE rowid IN (SELECT rowid FROM quotas WHERE day<? LIMIT 400)',oldDay).run();
  await s.sql('DELETE FROM maintenance_quotas WHERE day<?',oldDay).run();
  await s.sql('DELETE FROM mutation_receipts WHERE rowid IN (SELECT rowid FROM mutation_receipts WHERE created_at<? LIMIT 400)',now-86400).run();
  await s.sql("DELETE FROM grants WHERE id IN (SELECT id FROM grants WHERE state IN ('complete','failed') AND created_at<? LIMIT 200)",now-86400).run();
  return {deleted,retried};
}
