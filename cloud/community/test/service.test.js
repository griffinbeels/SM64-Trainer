import test from 'node:test';
import assert from 'node:assert/strict';
import {createService} from '../src/service.js';
import {validateGHC1} from '../src/codec.js';
import {checkedName,prefixRange} from '../src/names.js';
import {nativeAuthorize} from '../src/portal-contract.js';
import {encodeCursor,decodeCursor} from '../src/cursors.js';
import {grade} from '../src/catalog.js';
import {reconcile} from '../src/reconcile.js';
import {regrade} from '../src/regrade.js';
import {deleteIdentities} from '../src/identity-deletion.js';
import {deflateSync} from 'node:zlib';
import {config,LIMITS} from '../src/config.js';
import {canonical} from '../src/errors.js';
import {environment,alice,bob,catalog,target,nativeClip,ghc1,upload,digest} from './fixtures.js';
function harness(env){const errors=[],service=createService({auth:async r=>({issuer:config(env).issuer,sub:r.headers.get('authorization')==='Bearer bob'?bob:alice}),adminAuth:async r=>{if(r.headers.get('cf-access-jwt-assertion')!=='test-access')throw Error('No access');return 'admin@example.invalid';},catalog:async()=>catalog,onError:e=>errors.push(e)});const caches=new Map(),ctx={exports:{MetadataCache:({props})=>({async fetch(){const key=canonical(props);if(!caches.has(key)){const {store}=await import('../src/store.js').then(({Store})=>({store:new Store(env.DB,config(env))}));caches.set(key,await store.candidates(props.query));}return Response.json(caches.get(key));}}),AssetCache:({props})=>({async fetch(){const key=canonical(props);if(!caches.has(key))caches.set(key,(await env.GHOSTS.get(props.key))?.body);return new Response(caches.get(key));}})}};
  return {errors,caches,async call(path,{method='GET',body,headers={},user='alice',bytes}={}){const r=new Request('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+user,...(body?{'content-type':'application/json','idempotency-key':crypto.randomUUID()}:{}),...(bytes?{'content-type':'application/octet-stream'}:{}),...headers},body:body?JSON.stringify(body):bytes});const response=await service.fetch(r,env,ctx);const json=response.headers.get('content-type')?.includes('json')?await response.json():null;return {response,json};}};}
async function published(h,opts={}){const fixture=upload(nativeClip({marker:opts.marker||1}),opts.frames||100),admission=await h.call('/v1/uploads',{method:'POST',body:fixture.body,user:opts.user||'alice'});assert.equal(admission.response.status,200,JSON.stringify(admission.json));const result=await h.call('/v1/uploads/'+admission.json.upload_id,{method:'PUT',bytes:fixture.bytes,user:opts.user||'alice'});assert.equal(result.response.status,200,JSON.stringify(result.json));return {...fixture,...result.json};}
const pool='/v1/pool?'+new URLSearchParams({target:JSON.stringify(target),clock:'igt',rom:'us',scope:'strategy',strategy:'Standard',min_step:'0',max_step:'2',base_step:'1'});
test('unchanged native versions, bounded blocks and hashes',()=>{for(const version of [4,5,6,7]){const raw=nativeClip({version}),f=upload(raw);assert.equal(validateGHC1(f.bytes,f.body).version,version);}});
test('reject truncated/trailing streams, bomb and block count before publication',()=>{const f=upload();for(const altered of [f.bytes.subarray(0,-1),Buffer.concat([f.bytes,Buffer.from([0])]),Buffer.from(f.bytes)]){if(altered.length===f.bytes.length)altered.writeUInt32LE(255,180);const grant={...f.body,encoded_bytes:altered.length,encoded_sha256:digest(altered)};assert.throws(()=>validateGHC1(altered,grant));}const declared=Buffer.from(f.bytes);declared.writeUInt32LE(1,20);assert.throws(()=>validateGHC1(declared,{...f.body,encoded_sha256:digest(declared)}));});
test('native NaN, invalid pad, refusal residue and header mismatch fail',()=>{for(const mutate of [r=>r.writeFloatLE(NaN,160+76),r=>{r.writeUInt32LE(31|512,160+28);r.writeInt16LE(128,160+1276);},r=>r.writeUInt32LE(1,160+324),r=>r.writeBigUInt64LE(99n,80)]){const raw=nativeClip();mutate(raw);const f=upload(raw);assert.throws(()=>validateGHC1(f.bytes,f.body));}});
test('full casefold preserves Japanese and detects NFKC collisions',()=>{assert.equal(checkedName(' Straße ').key,checkedName('STRASSE').key);assert.equal(checkedName('ガミル').name,'ガミル');assert.equal(checkedName('Ａlice').key,'alice');for(const n of ['admin','a\u202eb','a\0b'])assert.throws(()=>checkedName(n));const p=prefixRange('STRASS');assert.equal(p.start,'strass');});
test('inclusive tied integer cutoffs choose highest qualifying skill',()=>{assert.equal(grade([{step:0,frames:100},{step:1,frames:100},{step:2,frames:50}],100),1);});
test('PKCE portal rejects arbitrary redirect, project, token or duplicate params',()=>{const cfg={supabaseURL:'https://fixture.supabase.co',clientID:'client-fixture',callback:'http://127.0.0.1:8072/api/community/auth/callback'},u=new URL(cfg.supabaseURL+'/auth/v1/oauth/authorize');u.search=new URLSearchParams({client_id:cfg.clientID,redirect_uri:cfg.callback,response_type:'code',code_challenge_method:'S256',code_challenge:'a'.repeat(43),state:'b'.repeat(32),scope:'openid email profile'});assert.equal(nativeAuthorize(u.href,cfg),u.href);for(const [k,v]of [['redirect_uri','https://evil.invalid'],['access_token','bad'],['client_id','wrong']]){const bad=new URL(u);bad.searchParams.set(k,v);assert.throws(()=>nativeAuthorize(bad.href,cfg));}const bad=new URL(u);bad.searchParams.append('state','x');assert.throws(()=>nativeAuthorize(bad.href,cfg));});
test('cursors bind exact canonical query and epochs',async()=>{const secret='fixture-secret-12345678901234567890',identity={generation:'x',epoch:1},c=await encodeCursor(secret,identity,[1,50,'a'.repeat(64)],1);assert.equal((await decodeCursor(secret,c,identity)).page,1);await assert.rejects(decodeCursor(secret,c,{...identity,epoch:2}));const [body,sig]=c.split('.');await assert.rejects(decodeCursor(secret,body+'.'+(sig[0]==='a'?'b':'a')+sig.slice(1),identity));const alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_',noncanonical=alphabet[alphabet.indexOf(sig.at(-1))|1];await assert.rejects(decodeCursor(secret,c.slice(0,-1)+noncanonical,identity));});
test('fastest winner races are conditional and stable ties do not replace',async()=>{const {env,store,accounts}=await environment(),h=harness(env);try{const slow=upload(nativeClip({marker:1}),120),fast=upload(nativeClip({marker:2}),100);const grants=await Promise.all([h.call('/v1/uploads',{method:'POST',body:slow.body}),h.call('/v1/uploads',{method:'POST',body:fast.body})]);const results=await Promise.all(grants.map((g,i)=>h.call('/v1/uploads/'+g.json.upload_id,{method:'PUT',bytes:[slow,fast][i].bytes})));assert(results.every(r=>r.response.status===200),String(h.errors));const rows=await store.rows('SELECT * FROM winners WHERE uid=?',accounts[0].uid);assert.equal(rows.length,1);assert.equal(rows[0].frames,100);const tie=await h.call('/v1/uploads',{method:'POST',body:upload(nativeClip({marker:3}),100).body});assert.equal(tie.json.disposition,'not_faster');assert.equal((await store.one('SELECT SUM(encoded_bytes) AS size FROM assets')).size,(await store.one('SELECT bytes_used FROM storage_budget')).bytes_used);}finally{env.DB.close();}});
test('upload replay is idempotent and account ownership cannot be substituted',async()=>{const {env}=await environment(),h=harness(env);try{const f=upload(),key=crypto.randomUUID(),a=await h.call('/v1/uploads',{method:'POST',body:f.body,headers:{'idempotency-key':key}}),replay=await h.call('/v1/uploads',{method:'POST',body:f.body,headers:{'idempotency-key':key}});assert.equal(a.json.upload_id,replay.json.upload_id);assert.equal((await h.call('/v1/uploads',{method:'POST',body:{...f.body,frames:101},headers:{'idempotency-key':key}})).response.status,409);assert.equal((await h.call('/v1/uploads/'+a.json.upload_id,{method:'PUT',bytes:f.bytes,user:'bob'})).response.status,404);const completed=await h.call('/v1/uploads/'+a.json.upload_id,{method:'PUT',bytes:f.bytes});assert.equal((await h.call('/v1/uploads/'+a.json.upload_id,{method:'PUT',bytes:f.bytes})).json.recording_id,completed.json.recording_id);}finally{env.DB.close();}});
test('exact global/account tickets and byte reservations roll back together',async()=>{const {env,store,accounts}=await environment();try{await store.ticket(accounts[0].uid);env.DB.sqlite.prepare('UPDATE quotas SET ops=? WHERE owner=?').run(LIMITS.account_ops_day,accounts[0].uid);const before=await store.one("SELECT ops FROM quotas WHERE owner='*'");await assert.rejects(store.ticket(accounts[0].uid),e=>e.code==='budget_exhausted');assert.equal((await store.one("SELECT ops FROM quotas WHERE owner='*'")).ops,before.ops);assert.equal((await store.one('SELECT COUNT(*) AS n FROM guards')).n,0);env.DB.sqlite.prepare('UPDATE accounts SET bytes_used=? WHERE uid=?').run(LIMITS.retained_bytes,accounts[1].uid);const h=harness(env),r=await h.call('/v1/uploads',{method:'POST',body:upload().body,user:'bob'});assert.equal(r.response.status,429);assert.equal((await store.one('SELECT bytes_reserved FROM storage_budget')).bytes_reserved,0);assert.equal((await store.one('SELECT pending FROM accounts WHERE uid=?',accounts[1].uid)).pending,0);}finally{env.DB.close();}});
test('withdrawal and publisher revocation gate warmed metadata/assets',async()=>{const {env,store,accounts}=await environment(),h=harness(env);try{const p=await published(h);assert.equal((await h.call(pool,{user:'bob'})).json.recordings.length,1);assert.equal((await h.call('/v1/recordings/'+p.recording_id+'/asset',{user:'bob'})).response.status,200);assert(h.caches.size>=2);const w=await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}});assert.equal(w.response.status,200,JSON.stringify(w.json)+String(h.errors));assert.equal((await h.call(pool,{user:'bob'})).json.recordings.length,0);assert.equal((await h.call('/v1/recordings/'+p.recording_id+'/asset',{user:'bob'})).response.status,404);assert((await store.one('SELECT global_epoch FROM settings')).global_epoch>0);}finally{env.DB.close();}});
test('name reclaim atomically preserves original ghost owner and invalidates pools',async()=>{const {env,store,accounts}=await environment(),h=harness(env);try{const p=await published(h);await h.call(pool,{user:'bob'});const body={name:'Alice',expected_owner:accounts[0].uid,destination_uid:accounts[1].uid,transfer_sheet:false,reason:'Manual evidence checked',confirmation:{phrase:'RECLAIM Alice',issued_at:Math.floor(Date.now()/1000)}};const headers={'cf-access-jwt-assertion':'test-access','idempotency-key':crypto.randomUUID()};const r=await h.call('/v1/admin/reclaim',{method:'POST',body,headers});assert.equal(r.response.status,200,JSON.stringify(r.json)+String(h.errors));assert.equal((await store.one('SELECT uid FROM recordings WHERE id=?',p.recording_id)).uid,accounts[0].uid);assert.equal((await store.one('SELECT status FROM accounts WHERE uid=?',accounts[0].uid)).status,'rename_required');const row=(await h.call(pool,{user:'bob'})).json.recordings[0];assert(row.player_name.startsWith('Player '));assert.equal(row.player_id,accounts[0].uid);assert.equal((await h.call('/v1/admin/reclaim',{method:'POST',body,headers})).response.status,200);assert.equal((await store.one('SELECT COUNT(*) AS n FROM admin_audit')).n,1);}finally{env.DB.close();}});
test('pins protect old winners until expiry; failed physical delete stays charged',async()=>{const {env,store,accounts}=await environment(),h=harness(env);try{const old=await published(h);assert.equal((await h.call('/v1/pins',{method:'PUT',body:{recording_ids:[old.recording_id]},user:'bob'})).response.status,200);await published(h,{marker:2,frames:90});env.DB.sqlite.prepare('UPDATE assets SET created_at=0').run();assert.equal((await reconcile(env)).deleted,0);env.DB.sqlite.prepare('UPDATE pins SET expires_at=0').run();env.GHOSTS.failDelete=true;const bytes=(await store.one('SELECT bytes_used FROM storage_budget')).bytes_used;await assert.rejects(reconcile(env));assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,bytes);env.GHOSTS.failDelete=false;assert.equal((await reconcile(env)).deleted,1);assert.equal((await store.one('SELECT COUNT(*) AS n FROM winners')).n,1);assert.equal((await h.call('/v1/recordings/'+old.recording_id,{user:'bob'})).response.status,404);}finally{env.DB.close();}});
test('stale catalog grant refuses publication and malformed body refunds only reservation',async()=>{const {env,store,accounts}=await environment(),h=harness(env);try{const f=upload(),admission=await h.call('/v1/uploads',{method:'POST',body:f.body});const malformed=Buffer.from(f.bytes);malformed[0]=0;const r=await h.call('/v1/uploads/'+admission.json.upload_id,{method:'PUT',bytes:malformed});assert.equal(r.response.status,422);assert.equal((await store.one('SELECT bytes_reserved FROM storage_budget')).bytes_reserved,0);assert((await store.one('SELECT uploaded FROM quotas WHERE owner=?',accounts[0].uid)).uploaded>0);const next=await h.call('/v1/uploads',{method:'POST',body:f.body});env.DB.sqlite.prepare("UPDATE settings SET generation='changed'").run();assert.equal((await h.call('/v1/uploads/'+next.json.upload_id,{method:'PUT',bytes:f.bytes})).response.status,409);assert.equal(env.GHOSTS.objects.size,0);}finally{env.DB.close();}});
test('query schemas and operator configuration fail closed',async()=>{const {env}=await environment(),h=harness(env);try{assert.equal((await h.call(pool+'&owner=evil')).response.status,400);assert.equal((await h.call('/v1/account',{headers:{origin:'https://evil.invalid'}})).response.status,403);env.OPERATOR_CONTACT='';assert.equal((await h.call('/v1/config')).response.status,503);}finally{env.DB.close();}});
test('actual inflated output bomb is rejected at a declared block bound',()=>{const f=upload(),zip=deflateSync(Buffer.alloc(1024*1024)),block=Buffer.alloc(8);block.writeUInt32LE(256);block.writeUInt32LE(zip.length,4);const bytes=Buffer.concat([f.bytes.subarray(0,180),block,zip]);assert.throws(()=>validateGHC1(bytes,{...f.body,encoded_bytes:bytes.length,encoded_sha256:digest(bytes)}),e=>e.code==='invalid_clip');});
test('native clock and course binding match portable eligibility',()=>{for(const mutation of [r=>r.writeUInt32LE(2,112),r=>{r.writeInt32LE(24,104);for(let at=160;at<r.length;at+=1284)r.writeInt16LE(24,at+64);}]){const raw=nativeClip();mutation(raw);const f=upload(raw);assert.throws(()=>validateGHC1(f.bytes,f.body));}const p=prefixRange('\ud7ff');assert.equal(p.end,'\ue000');});
test('UUID wire ownership and fresh account state survive mutation replay',async()=>{const {env,accounts}=await environment(),h=harness(env);try{assert.equal((await h.call('/v1/account')).json.uid,alice);const p=await published(h);assert.equal((await h.call('/v1/recordings/'+p.recording_id)).json.player_id,alice);const key=crypto.randomUUID(),options={method:'PUT',body:{name:'First'},headers:{'idempotency-key':key}};assert.equal((await h.call('/v1/account/name',options)).json.name,'First');await h.call('/v1/account/name',{method:'PUT',body:{name:'Later'}});assert.equal((await h.call('/v1/account/name',options)).json.name,'Later');env.DB.sqlite.prepare("UPDATE accounts SET status='suspended' WHERE uid=?").run(accounts[0].uid);assert.equal((await h.call(pool)).json.error.code,'suspended');}finally{env.DB.close();}});
test('new winner invalidates an already cached empty pool',async()=>{const {env,store}=await environment(),h=harness(env);try{assert.equal((await h.call(pool,{user:'bob'})).json.recordings.length,0);await published(h);assert.equal((await h.call(pool,{user:'bob'})).json.recordings.length,1);assert.equal((await store.one('SELECT epoch FROM scope_epochs')).epoch,1);}finally{env.DB.close();}});
test('expired multipart receiving reservations abort before refund or recover completed bytes',async()=>{for(const complete of [false,true]){const {env,store,accounts}=await environment(),h=harness(env);try{const f=upload(),admission=await h.call('/v1/uploads',{method:'POST',body:f.body}),g=await store.beginUpload(accounts[0],admission.json.upload_id),m=await env.GHOSTS.createMultipartUpload(g.asset_key,{customMetadata:{encoded_sha256:g.encoded_sha256}});await store.sql('UPDATE grants SET multipart_id=?,expires_at=0 WHERE id=?',m.uploadId,g.id).run();const part=await m.uploadPart(1,f.bytes);if(complete)await m.complete([part]);await reconcile(env);assert.equal((await store.one('SELECT bytes_reserved FROM storage_budget')).bytes_reserved,0);assert.equal((await store.one('SELECT pending FROM accounts WHERE uid=?',alice)).pending,0);if(complete){assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,f.bytes.length);assert.equal((await store.one('SELECT COUNT(*) AS n FROM winners')).n,1);}else{assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,0);await assert.rejects(m.complete([part]),/NoSuchUpload/);assert.equal(env.GHOSTS.objects.size,0);}}finally{env.DB.close();}}});
test('bounded catalog migration regrades retained bytes and retires former generation winners',async()=>{const {env,store}=await environment(),h=harness(env);try{await published(h);const entries=structuredClone(catalog.entries);entries[0].strategies[0].cutoffs[2].frames=110;entries[0].overall_cutoffs[2].frames=110;const model={v:1,generation:digest(canonical(entries)),entries};assert.equal((await regrade(env,{model,limit:1})).complete,false);assert.equal((await store.one('SELECT generation FROM settings')).generation,catalog.generation);assert.equal((await regrade(env,{model,limit:1})).complete,true);const rows=await store.rows('SELECT generation,step FROM winners');assert.deepEqual(rows.map(r=>[r.generation,r.step]),[[model.generation,2]]);assert.equal((await store.one('SELECT generation FROM settings')).generation,model.generation);assert.equal((await regrade(env,{model,limit:1})).complete,true);}finally{env.DB.close();}});
test('name search caps empty prefix scans and honestly returns incomplete pagination',async()=>{const {env,store}=await environment(),h=harness(env);try{for(let i=0;i<70;i++){const uid='20000000-0000-4000-8000-'+String(i).padStart(12,'0');await store.sql("INSERT INTO accounts(uid,issuer,subject,invited,status,name,name_key,created_at,updated_at) VALUES(?,?,?,1,'active',?,?,0,0)",uid,config(env).issuer,uid,'Prefix'+i,'prefix'+String(i).padStart(3,'0')).run();}const path='/v1/search?'+new URLSearchParams({target:JSON.stringify(target),clock:'igt',rom:'us',scope:'strategy',strategy:'Standard',prefix:'Prefix'}),first=await h.call(path);assert.equal(first.response.status,200,JSON.stringify(first.json)+String(h.errors));assert.equal(first.json.recordings.length,0);assert.equal(first.json.coverage.complete,false);assert(first.json.next_cursor);const last=await h.call(path+'&cursor='+encodeURIComponent(first.json.next_cursor));assert.equal(last.json.coverage.complete,true);assert.equal(last.json.next_cursor,null);}finally{env.DB.close();}});
test('Auth identity deletion is tombstoned first and SDK retries are bounded and sanitized',async()=>{const {env,store}=await environment(),h=harness(env);try{const p=await published(h),r=await h.call('/v1/account/delete',{method:'POST',body:{confirmation:'DELETE MY ACCOUNT'}});assert.equal(r.json.identity_deletion,'pending');assert.equal((await h.call('/v1/recordings/'+p.recording_id+'/asset',{user:'bob'})).response.status,404);let calls=0;const failing=async uid=>{assert.equal(uid,alice);calls++;return {error:{status:503,message:'private upstream details'}};};await deleteIdentities(env,{deleteIdentity:failing});assert.equal(calls,1);await deleteIdentities(env,{deleteIdentity:failing});assert.equal(calls,1);assert.equal((await store.one('SELECT last_error FROM deletion_outbox')).last_error,'identity_delete_retry');for(let i=1;i<8;i++){await store.sql('UPDATE deletion_outbox SET next_at=0').run();await deleteIdentities(env,{deleteIdentity:failing});}assert.equal((await store.one('SELECT state FROM deletion_outbox')).state,'operator_pending');await deleteIdentities(env,{deleteIdentity:failing});assert.equal(calls,8);await store.sql("UPDATE deletion_outbox SET state='retrying',next_at=0,attempts=0").run();assert.equal((await deleteIdentities(env,{deleteIdentity:async()=>({error:{status:400,code:'user_not_found'}})})).complete,1);assert.equal((await store.one('SELECT state FROM deletion_outbox')).state,'complete');const configResponse=await h.call('/v1/config');assert(!JSON.stringify(configResponse.json).includes(env.SUPABASE_SECRET_KEY));env.SUPABASE_SECRET_KEY='';assert.equal((await h.call('/v1/config')).response.status,503);}finally{env.DB.close();}});
test('maintained Supabase admin SDK calls only the fixed tombstoned Auth UUID',async()=>{const {env,store}=await environment(),h=harness(env),original=globalThis.fetch;try{await h.call('/v1/account/delete',{method:'POST',body:{confirmation:'DELETE MY ACCOUNT'}});let calls=0;globalThis.fetch=async(url,options)=>{assert.equal(String(url),env.SUPABASE_URL+'/auth/v1/admin/users/'+alice);assert.equal(options.method,'DELETE');assert.equal(new Headers(options.headers).get('apikey'),env.SUPABASE_SECRET_KEY);assert(options.signal);calls++;return Response.json({id:alice,aud:'authenticated',role:'authenticated'});};assert.equal((await deleteIdentities(env)).complete,1);assert.equal(calls,1);assert.equal((await store.one('SELECT state FROM deletion_outbox')).state,'complete');}finally{globalThis.fetch=original;env.DB.close();}});
test('reclaim preserves suspension and consent renewal preserves unchanged sheet verification',async()=>{const {env,store}=await environment(),h=harness(env);try{await store.sql("UPDATE accounts SET sheet_runner='claimed-runner',sheet_verified=1 WHERE uid=?",alice).run();const profile={name:'Alice',sharing:true,terms_version:'test-1',privacy_version:'test-1',sheet_runner:'claimed-runner'};assert.equal((await h.call('/v1/account',{method:'PUT',body:profile})).json.sheet_verified,true);assert.equal((await h.call('/v1/account',{method:'PUT',body:{...profile,sheet_runner:'different-runner'}})).json.sheet_verified,false);await store.sql("UPDATE accounts SET status='suspended' WHERE uid=?",alice).run();const body={name:'Alice',expected_owner:alice,destination_uid:bob,transfer_sheet:false,reason:'Reviewed claim evidence',confirmation:{phrase:'RECLAIM Alice',issued_at:Math.floor(Date.now()/1000)}};assert.equal((await h.call('/v1/admin/reclaim',{method:'POST',body,headers:{'cf-access-jwt-assertion':'test-access'}})).response.status,200);assert.equal((await store.one('SELECT status FROM accounts WHERE uid=?',alice)).status,'suspended');assert.equal((await h.call('/v1/account/name',{method:'PUT',body:{name:'Restored'}})).json.error.code,'suspended');}finally{env.DB.close();}});
test('catalog migration fences admission and every old-grant publication',async()=>{const {env,store,accounts}=await environment(),h=harness(env);try{await published(h);const fixture=upload(nativeClip({marker:9}),90),admitted=await h.call('/v1/uploads',{method:'POST',body:fixture.body});const g=await store.beginUpload(accounts[0],admitted.json.upload_id);await store.stored(g);const entries=structuredClone(catalog.entries);entries[0].overall_cutoffs[2].frames=110;const model={v:1,generation:digest(canonical(entries)),entries};assert.equal((await regrade(env,{model,limit:1})).complete,false);await assert.rejects(store.publish(g),e=>e.code==='generation_changed');assert.equal((await store.one('SELECT state FROM grants WHERE id=?',g.id)).state,'stored');assert.equal((await h.call('/v1/uploads',{method:'POST',body:upload(nativeClip({marker:8}),80).body})).response.status,429);assert.equal((await regrade(env,{model,limit:1})).complete,true);assert.equal((await store.publish(g)).disposition,'not_faster');assert.equal((await store.one('SELECT COUNT(*) AS n FROM winners WHERE generation!=?',model.generation)).n,0);}finally{env.DB.close();}});
test('same-origin policies are reachable, escaped and fail closed on missing operator',async()=>{const {env}=await environment();try{env.OPERATOR_NAME='<script>bad</script>';env.OPERATOR_CONTACT='"<&contact';const service=createService(),response=await service.fetch(new Request('https://community.example.invalid/policies'),env,{}),html=await response.text();assert.equal(response.status,200);assert(html.includes('&lt;script&gt;bad&lt;/script&gt;'));assert(html.includes('&quot;&lt;&amp;contact'));assert(!html.includes('<script>'));assert(html.includes('id="terms"')&&html.includes('id="privacy"')&&html.includes('QPC'));assert(html.includes('signup discloses automatic sharing')&&html.includes('switch sharing off in Settings')&&html.includes('choices stay unchanged when renewing consent')&&html.includes('Policy acknowledgement is required separately'));assert(!html.includes('switch sharing off there'));env.OPERATOR_CONTACT='';assert.equal((await service.fetch(new Request('https://community.example.invalid/policies'),env,{})).status,503);}finally{env.DB.close();}});
test('indexed expired receipt pruning is bounded above the daily creation rate',async()=>{const {env,store}=await environment();try{for(let i=0;i<401;i++)await store.sql('INSERT INTO mutation_receipts VALUES(?,?,?,?,?,?)',alice,'fixture',String(i),'digest','{}',0).run();await reconcile(env);assert.equal((await store.one('SELECT COUNT(*) AS n FROM mutation_receipts')).n,1);const plan=await store.rows('EXPLAIN QUERY PLAN SELECT rowid FROM mutation_receipts WHERE created_at<? LIMIT 400',1);assert(plan.some(p=>p.detail.includes('receipts_expiry')));}finally{env.DB.close();}});
test('exhausted ticket rolls back a fresh owner row as well as both counters',async()=>{
  const {env,store}=await environment(),fresh='30000000-0000-4000-8000-000000000001';
  try{
    await store.ticket(alice);
    await store.sql("UPDATE quotas SET ops=ops_limit WHERE owner='*'").run();
    await assert.rejects(store.ticket(fresh),e=>e.code==='budget_exhausted');
    assert.equal(await store.one('SELECT * FROM quotas WHERE owner=?',fresh),null);
    assert.equal((await store.one("SELECT ops FROM quotas WHERE owner='*'")).ops,LIMITS.global_ops_day);
    assert.equal((await store.one('SELECT ops FROM quotas WHERE owner=?',alice)).ops,1);
  }finally{env.DB.close();}
});
test('unknown Auth UUID status and denied routes persist no account or quota rows',async()=>{
  const {env,store}=await environment(),fresh='30000000-0000-4000-8000-000000000002';
  const service=createService({auth:async()=>({issuer:config(env).issuer,sub:fresh}),catalog:async()=>catalog});
  try{
    await store.ticket(alice);
    for(const exhausted of [false,true]){
      await store.sql("UPDATE quotas SET ops=? WHERE owner='*'",exhausted?LIMITS.global_ops_day:1).run();
      const profile=await service.fetch(new Request('https://community.example.invalid/v1/account'),env,{});
      assert.equal(profile.status,200);
      assert.deepEqual(await profile.json(),{v:1,uid:fresh,name:null,status:'not_invited',sharing:false,terms_version:null,privacy_version:null,sheet_runner:null,sheet_verified:false});
      for(const [path,method]of [['/v1/catalog','GET'],['/v1/account/export','GET'],['/v1/account','PUT'],['/v1/account/delete','POST']]){
        const response=await service.fetch(new Request('https://community.example.invalid'+path,{method}),env,{});
        assert.equal(response.status,403);assert.equal((await response.json()).error.code,'not_invited');
      }
      assert.equal(await store.one('SELECT * FROM accounts WHERE uid=?',fresh),null);
      assert.equal(await store.one('SELECT * FROM quotas WHERE owner=?',fresh),null);
      assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,2);
      assert.equal((await store.one('SELECT COUNT(*) AS n FROM quotas')).n,2);
      assert.equal((await store.one("SELECT ops FROM quotas WHERE owner='*'")).ops,exhausted?LIMITS.global_ops_day:1);
    }
    const h=harness(env),body={subject:fresh,invited:true,reason:'Fixture reviewed invitation',confirmation:{phrase:'CHANGE INVITATION',issued_at:Math.floor(Date.now()/1000)}};
    await store.sql("UPDATE quotas SET ops=1 WHERE owner='*'").run();
    assert.equal((await h.call('/v1/admin/invites',{method:'POST',body,headers:{'cf-access-jwt-assertion':'test-access'}})).response.status,200);
    assert.equal((await store.one('SELECT invited FROM accounts WHERE uid=?',fresh)).invited,1);
    const invited=await service.fetch(new Request('https://community.example.invalid/v1/account'),env,{});
    assert.equal((await invited.json()).status,'new');
  }finally{env.DB.close();}
});
test('persisted suspended owners retain status, export and removal after invitation revocation',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const recording=await published(h);
    await store.sql("UPDATE accounts SET invited=0,status='suspended' WHERE uid=?",alice).run();
    const before=(await store.one("SELECT ops FROM quotas WHERE owner='*'")).ops;
    assert.equal((await h.call('/v1/account')).json.status,'suspended');
    assert.equal((await store.one("SELECT ops FROM quotas WHERE owner='*'")).ops,before);
    assert.equal((await h.call('/v1/account/name',{method:'PUT',body:{name:'Bypass'}})).json.error.code,'suspended');
    assert.equal((await h.call('/v1/account/export')).json.recordings[0].id,recording.recording_id);
    assert.equal((await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}})).response.status,200);
    assert.equal((await h.call('/v1/account/delete',{method:'POST',body:{confirmation:'DELETE MY ACCOUNT'}})).response.status,200);
    assert.equal((await h.call('/v1/account')).json.status,'deleted');
    assert.equal((await store.one('SELECT state FROM deletion_outbox WHERE uid=?',alice)).state,'pending');
  }finally{env.DB.close();}
});
test('indexed quota pruning drains 400 expired rows per tick and preserves the current day',async()=>{
  const {env,store}=await environment();
  try{
    await store.ticket(alice);
    for(let i=0;i<401;i++)await store.sql('INSERT INTO quotas(owner,day,ops_limit,upload_limit) VALUES(?,?,?,?)','expired-'+i,'2000-01-01',LIMITS.account_ops_day,LIMITS.upload_bytes_day).run();
    await reconcile(env);
    assert.equal((await store.one("SELECT COUNT(*) AS n FROM quotas WHERE day='2000-01-01'")).n,1);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM quotas WHERE day=?',new Date().toISOString().slice(0,10))).n,2);
    const plan=await store.rows('EXPLAIN QUERY PLAN SELECT rowid FROM quotas WHERE day<? LIMIT 400','2001-01-01');
    assert(plan.some(p=>p.detail.includes('quotas_expiry')));
    assert(400*24*4>LIMITS.global_ops_day+1);
    await reconcile(env);
    assert.equal((await store.one("SELECT COUNT(*) AS n FROM quotas WHERE day='2000-01-01'")).n,0);
  }finally{env.DB.close();}
});
test('expired pin backlog defers physical collection until every FK reference drains',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const old=await published(h);
    await published(h,{marker:2,frames:90});
    const asset=(await store.one('SELECT asset_key FROM recordings WHERE id=?',old.recording_id)).asset_key;
    for(let i=0;i<101;i++){
      const uid='40000000-0000-4000-8000-'+String(i).padStart(12,'0');
      await store.sql('INSERT INTO accounts(uid,issuer,subject,created_at,updated_at) VALUES(?,?,?,0,0)',uid,config(env).issuer,uid).run();
      await store.sql('INSERT INTO pins VALUES(?,?,0)',uid,old.recording_id).run();
    }
    await store.sql('UPDATE assets SET created_at=0').run();
    await store.sql('INSERT INTO mutation_receipts VALUES(?,?,?,?,?,0)',alice,'expired','fixture','digest','{}').run();
    const charged=(await store.one('SELECT bytes_used FROM storage_budget')).bytes_used;
    assert.equal((await reconcile(env)).deleted,0);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM pins')).n,1);
    assert.equal((await store.one("SELECT COUNT(*) AS n FROM mutation_receipts WHERE route='expired'")).n,0);
    assert.equal((await store.one('SELECT state FROM assets WHERE key=?',asset)).state,'stored');
    assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,charged);
    assert(env.GHOSTS.objects.has(asset));
    const plan=await store.rows('EXPLAIN QUERY PLAN SELECT 1 FROM pins p JOIN recordings r ON r.id=p.recording_id WHERE r.asset_key=?',asset);
    assert(plan.some(p=>p.detail.includes('pins_recording')));
    assert(plan.some(p=>p.detail.includes('recording_asset')));
    assert.equal((await reconcile(env)).deleted,1);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM pins')).n,0);
    assert.equal(await store.one('SELECT id FROM recordings WHERE id=?',old.recording_id),null);
    assert(!env.GHOSTS.objects.has(asset));
    assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,charged-old.body.encoded_bytes);
  }finally{env.DB.close();}
});
test('claimed names count normalized Unicode code points and enforce the 63-byte wire bound',()=>{
  assert.equal(checkedName('a'.repeat(20)).name,'a'.repeat(20));
  assert.equal(checkedName('Ａ'.repeat(20)).name,'A'.repeat(20));
  assert.equal(checkedName('ガ'.repeat(20)).name,'ガ'.repeat(20));
  assert.equal(checkedName('😀'.repeat(15)).name,'😀'.repeat(15));
  assert.equal(checkedName('😀'.repeat(15)+'界').name,'😀'.repeat(15)+'界');
  assert.equal(checkedName('e\u0301'.repeat(20)).name,'é'.repeat(20));
  assert.equal(checkedName(' '.repeat(3)+'界'.repeat(20)+' ').name,'界'.repeat(20));
  assert.throws(()=>checkedName('a'.repeat(21)),e=>e.code==='invalid_name'&&e.message.includes('20 characters'));
  assert.throws(()=>checkedName('ﬃ'.repeat(7)),e=>e.code==='invalid_name'&&e.message.includes('20 characters'));
  assert.throws(()=>checkedName('😀'.repeat(16)),e=>e.code==='invalid_name'&&e.message.includes('63 UTF-8 bytes'));
  assert.equal(checkedName('Straße').key,checkedName('STRASSE').key);
  assert.equal(prefixRange('界'.repeat(30)).start,'界'.repeat(30));
});
test('name API reports each claim limit clearly and leaves the current account unchanged',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    for(const [name,limit]of [['x'.repeat(21),'20 characters'],['😀'.repeat(16),'63 UTF-8 bytes']]){
      const result=await h.call('/v1/account/name',{method:'PUT',body:{name}});
      assert.equal(result.response.status,400);assert.equal(result.json.error.code,'invalid_name');
      assert(result.json.error.message.includes(limit));
      assert.equal((await store.one('SELECT name FROM accounts WHERE uid=?',alice)).name,'Alice');
    }
    const result=await h.call('/v1/account/name',{method:'PUT',body:{name:'界'.repeat(20)}});
    assert.equal(result.response.status,200);assert.equal(result.json.name,'界'.repeat(20));
  }finally{env.DB.close();}
});
test('hosted provider start accepts Twitch only with the registered native request',async()=>{
  const {env}=await environment();
  try{
    env.ASSETS={fetch:async()=>new Response('<main>Provider login</main>')};
    const native=new URL(env.SUPABASE_URL+'/auth/v1/oauth/authorize');
    native.search=new URLSearchParams({client_id:env.OAUTH_CLIENT_ID,redirect_uri:env.NATIVE_CALLBACK,
      response_type:'code',code_challenge_method:'S256',code_challenge:'a'.repeat(43),state:'b'.repeat(32),scope:'openid email profile'});
    const start=(provider,value=native.href)=>createService().fetch(new Request('https://community.example.invalid/auth/start?'+
      new URLSearchParams({provider,native_authorize:value})),env,{});
    for(const provider of ['discord','google','twitch','email'])assert.equal((await start(provider)).status,200);
    assert.equal((await start('unregistered')).status,400);
    native.searchParams.set('redirect_uri','https://attacker.example/callback');
    assert.equal((await start('twitch',native.href)).status,400);
  }finally{env.DB.close();}
});
