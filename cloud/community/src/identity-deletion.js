import {createClient} from '@supabase/supabase-js';
import {Store} from './store.js';
import {config} from './config.js';
// No SDK error text, URLs, keys or tokens enter logs or persisted diagnostics.
// A lease avoids ordinary overlapping cron retries; deleteUser remains idempotent
// after a crash between managed Auth deletion and recording the local receipt.
export async function deleteIdentities(env,{limit=20,deleteIdentity}={}){
  const s=new Store(env.DB,config(env)),now=Math.floor(Date.now()/1000);
  if(!deleteIdentity){
    if(!config(env).identityAdminConfigured)return {complete:0,configuration_missing:true};
    const client=createClient(env.SUPABASE_URL,env.SUPABASE_SECRET_KEY,{auth:{persistSession:false,autoRefreshToken:false,detectSessionInUrl:false},global:{fetch:(url,options)=>fetch(url,{...options,signal:AbortSignal.timeout(10000)})}});
    deleteIdentity=uid=>client.auth.admin.deleteUser(uid);
  }
  let complete=0;
  for(const row of await s.rows("SELECT d.* FROM deletion_outbox d JOIN accounts a ON a.uid=d.uid WHERE d.state IN ('pending','retrying') AND d.next_at<=? AND a.status='deleted' ORDER BY d.next_at,d.uid LIMIT ?",now,limit)){
    const result=await s.sql("UPDATE deletion_outbox SET state='retrying',attempts=attempts+1,next_at=? WHERE uid=? AND attempts=? AND next_at<=? AND state IN ('pending','retrying')",now+300,row.uid,row.attempts,now).run();
    if(!result.meta?.changes)continue;
    let error;try{({error}=await deleteIdentity(row.uid));}catch{error={status:503};}
    if(!error||error.code==='user_not_found'){await s.sql("UPDATE deletion_outbox SET state='complete',last_error=NULL,completed_at=? WHERE uid=?",now,row.uid).run();complete++;continue;}
    const attempts=row.attempts+1,configuration=[401,403].includes(error.status),terminal=attempts>=8;
    await s.sql('UPDATE deletion_outbox SET state=?,next_at=?,last_error=? WHERE uid=?',terminal?'operator_pending':'retrying',now+(configuration?3600:Math.min(86400,60*2**attempts)),configuration?'identity_admin_configuration':'identity_delete_retry',row.uid).run();
  }
  return {complete};
}
