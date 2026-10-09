import {Store} from './store.js';
import {config} from './config.js';
import {ServiceError} from './errors.js';
export async function reconcile(env,{limit=20}={}){
  const s=new Store(env.DB,config(env)),now=Math.floor(Date.now()/1000);
  await s.sql('DELETE FROM pins WHERE rowid IN (SELECT rowid FROM pins WHERE expires_at<=? LIMIT ?)',now,limit*5).run();
  for(const g of await s.rows("SELECT * FROM grants WHERE state='pending' AND expires_at<=? LIMIT ?",now,limit)){
    try{await s.release(g);}catch(e){if(!(e instanceof ServiceError))throw e;}
  }
  // Abort is an R2 fence: an aborted multipart cannot complete later. If it
  // completed first, strong object visibility preserves the physical charge.
  for(const g of await s.rows("SELECT * FROM grants WHERE (state='receiving' AND expires_at<=?) OR state='recovering' LIMIT ?",now,limit)){
    try{await s.tx("EXISTS(SELECT 1 FROM grants WHERE id=? AND state IN ('receiving','recovering'))",[g.id],[s.sql("UPDATE grants SET state='recovering' WHERE id=?",g.id)]);}catch(e){if(e instanceof ServiceError)continue;throw e;}
    await s.ticket(g.uid,{classA:g.multipart_id?1:0,classB:1,maintenance:true});
    if(g.multipart_id){try{await env.GHOSTS.resumeMultipartUpload(g.asset_key,g.multipart_id).abort();}catch(error){
      // A completed or already-aborted multipart no longer exists. A genuine
      // network/storage failure leaves the reservation for the next retry.
      if(!/does not exist|NoSuchUpload|not found|already completed|already aborted/i.test(String(error)))throw error;
    }}
    const object=await env.GHOSTS.head(g.asset_key);
    if(object){
      if(object.size!==g.encoded_bytes||object.customMetadata?.encoded_sha256!==g.encoded_sha256)throw Error('Stored intent integrity mismatch');
      try{await s.stored(g);}catch(e){if(e instanceof ServiceError)continue;throw e;}
      await s.publish(g);
    }else await s.release(g);
  }
  for(const g of await s.rows("SELECT * FROM grants WHERE state='stored' LIMIT ?",limit))await s.publish(g);
  const assets=await s.rows("SELECT x.* FROM assets x WHERE x.state='deleting' OR (x.created_at<? AND NOT EXISTS(SELECT 1 FROM winners w JOIN recordings r ON r.id=w.recording_id WHERE r.asset_key=x.key) AND NOT EXISTS(SELECT 1 FROM pins p JOIN recordings r ON r.id=p.recording_id WHERE r.asset_key=x.key AND p.expires_at>?)) LIMIT ?",now-900,now,limit);
  let deleted=0;
  for(const a of assets){
    try{await s.tx("EXISTS(SELECT 1 FROM assets WHERE key=?) AND NOT EXISTS(SELECT 1 FROM winners w JOIN recordings r ON r.id=w.recording_id WHERE r.asset_key=?) AND NOT EXISTS(SELECT 1 FROM pins p JOIN recordings r ON r.id=p.recording_id WHERE r.asset_key=? AND p.expires_at>?)",[a.key,a.key,a.key,now],[s.sql("UPDATE assets SET state='deleting' WHERE key=?",a.key)]);}catch(e){if(e instanceof ServiceError)continue;throw e;}
    await s.ticket(a.uid,{classA:1,maintenance:true});
    await env.GHOSTS.delete(a.key);
    await s.tx("EXISTS(SELECT 1 FROM assets WHERE key=? AND state='deleting')",[a.key],[s.sql('DELETE FROM recordings WHERE asset_key=?',a.key),s.sql('DELETE FROM assets WHERE key=?',a.key),s.sql('UPDATE accounts SET bytes_used=bytes_used-? WHERE uid=?',a.encoded_bytes,a.uid),s.sql('UPDATE storage_budget SET bytes_used=bytes_used-? WHERE id=1',a.encoded_bytes)]);deleted++;
  }
  await s.sql('DELETE FROM quotas WHERE rowid IN (SELECT rowid FROM quotas WHERE day<? LIMIT ?)',new Date(Date.now()-7*86400000).toISOString().slice(0,10),limit*5).run();
  await s.sql('DELETE FROM mutation_receipts WHERE rowid IN (SELECT rowid FROM mutation_receipts WHERE created_at<? LIMIT ?)',now-86400,limit*5).run();
  await s.sql("DELETE FROM grants WHERE id IN (SELECT id FROM grants WHERE state IN ('complete','failed') AND created_at<? LIMIT ?)",now-86400,limit).run();
  return {deleted};
}
