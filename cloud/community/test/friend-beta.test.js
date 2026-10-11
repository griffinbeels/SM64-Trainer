import test from 'node:test';
import assert from 'node:assert/strict';
import {createService} from '../src/service.js';
import {Store} from '../src/store.js';
import {config,LIMITS} from '../src/config.js';
import {MaintenanceClock,MAINTENANCE_INTERVAL_MS,runMaintenance,armMaintenance} from '../src/maintenance.js';
import {reconcile} from '../src/reconcile.js';
import {canonical,sha256} from '../src/errors.js';
import {environment,alice,bob,catalog,target,nativeClip,upload} from './fixtures.js';

const third='30000000-0000-4000-8000-000000000003',fourth='30000000-0000-4000-8000-000000000004';
const second=Math.floor(Date.now()/1000),token='A'.repeat(43);
function harness(env,model=catalog,ctx={}){
  const errors=[],cached=new Map();
  ctx.exports={MetadataCache:({props})=>({async fetch(){const key=canonical(props);if(!cached.has(key))cached.set(key,await new Store(env.DB,config(env)).poolCandidates(props.query));return Response.json(cached.get(key));}})};
  const service=createService({auth:async r=>({issuer:config(env).issuer,sub:r.headers.get('authorization').slice(7)}),catalog:async()=>model,onError:e=>errors.push(e)});
  return {errors,cached,model,async call(path,{uid=alice,method='GET',body,bytes}={}){
    const response=await service.fetch(new Request('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+uid,...(body?{'content-type':'application/json','idempotency-key':crypto.randomUUID()}:{}),...(bytes?{'content-type':'application/octet-stream'}:{})},body:body?JSON.stringify(body):bytes}),env,ctx);
    const json=response.headers.get('content-type')?.includes('json')?await response.json():null;return {response,json};
  }};
}
const pool=(overrides={})=>'/v1/pool?'+new URLSearchParams({target:JSON.stringify(target),clock:'igt',rom:'us',scope:'strategy',strategy:'Standard',min_step:'2',max_step:'2',base_step:'2',...overrides});
async function invite(store,value=token,{expiry=second+3600,revoked=0}={}){await store.sql('INSERT INTO beta_invitations(token_sha256,expires_at,revoked,created_at) VALUES(?,?,?,?)',await sha256(value),expiry,revoked,second).run();}
async function published(h,{uid=alice,marker=1,frames=100,strategy='Standard'}={}){
  const f=upload(nativeClip({marker}),frames);f.body.strategy=strategy;f.body.generation=h.model.generation;
  const grant=await h.call('/v1/uploads',{uid,method:'POST',body:f.body});assert.equal(grant.response.status,200,JSON.stringify(grant.json)+String(h.errors));
  const result=await h.call('/v1/uploads/'+grant.json.upload_id,{uid,method:'PUT',bytes:f.bytes});assert.equal(result.response.status,200,JSON.stringify(result.json)+String(h.errors));return result.json;
}

test('one-use invitations atomically bind one authenticated UID and repeat safely',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    await invite(store);const results=await Promise.all([third,fourth].map(uid=>h.call('/v1/account/invitation',{uid,method:'POST',body:{token}})));
    assert.deepEqual(results.map(r=>r.response.status).sort(),[200,403]);
    const owner=(await store.one('SELECT claimed_uid FROM beta_invitations')).claimed_uid;
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,3);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM quotas')).n,0);
    assert.equal((await h.call('/v1/account/invitation',{uid:owner,method:'POST',body:{token}})).response.status,200);
    assert.equal((await h.call('/v1/catalog',{uid:owner})).response.status,403); // Current policies/name still required.
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM guards')).n,0);assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('invalid, revoked, expired, capped and disabled invitations leave no retained unknown identity',async()=>{
  for(const mode of ['invalid','revoked','expired','capped','disabled']){
    const {env,store}=await environment(),h=harness(env);
    try{
      if(mode!=='invalid')await invite(store,token,{expiry:mode==='expired'?second-1:second+3600,revoked:Number(mode==='revoked')});
      if(mode==='capped')await store.sql('UPDATE beta_admission SET max_members=2').run();
      if(mode==='disabled')await store.sql('UPDATE settings SET enabled=0').run();
      const result=await h.call('/v1/account/invitation',{uid:third,method:'POST',body:{token}});assert.equal(result.response.status,403,mode);assert.equal(result.json.error.code,'invite_unavailable');
      assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,2);assert.equal((await store.one('SELECT COUNT(*) AS n FROM quotas')).n,0);
    }finally{env.DB.close();}
  }
});
test('invitations cannot reactivate deleted or suspended owners',async()=>{
  for(const status of ['deleted','suspended']){const {env,store}=await environment(),h=harness(env);try{
    await invite(store);await store.sql('UPDATE accounts SET status=?,invited=0 WHERE uid=?',status,alice).run();
    assert.equal((await h.call('/v1/account/invitation',{method:'POST',body:{token}})).response.status,403);
    assert.equal((await store.one('SELECT status FROM accounts WHERE uid=?',alice)).status,status);
    assert.equal((await store.one('SELECT claimed_uid FROM beta_invitations')).claimed_uid,null);
  }finally{env.DB.close();}}
});
test('operator invitations respect the same lifetime member cap',async()=>{
  const {env,store}=await environment();try{
    await store.sql('UPDATE beta_admission SET max_members=2').run();
    await assert.rejects(store.invite(third,true,'operator@example.invalid','Reviewed tester','CHANGE INVITATION',{key:crypto.randomUUID(),digest:'0'.repeat(64)}));
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,2);
  }finally{env.DB.close();}
});
test('an empty rank pool returns at most four fastest compatible eligible ghosts',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    await published(h,{uid:alice,marker:1,frames:110});await published(h,{uid:bob,marker:2,frames:100});
    for(let i=3;i<=7;i++){
      const uid='30000000-0000-4000-8000-00000000000'+i;
      await store.sql("INSERT INTO accounts(uid,issuer,subject,invited,status,name,name_key,sharing,terms_version,privacy_version,created_at,updated_at) VALUES(?,?,?,1,'active',?,?,1,'test-1','test-1',0,0)",uid,config(env).issuer,uid,'Guest'+i,'guest'+i).run();
      await published(h,{uid,marker:i,frames:100+i});
    }
    const result=await h.call(pool());assert.equal(result.response.status,200,JSON.stringify(result.json));
    assert.deepEqual(result.json.recordings.map(r=>r.frames),[100,103,104,105]);assert.equal(result.json.coverage.min_step,2);assert.equal(result.json.next_cursor,null);
    assert(result.json.recordings.every(r=>r.strategy==='Standard'&&r.game_version==='us'&&r.clock==='igt'));
    assert.equal((await store.poolCandidates({key:'star:1-0',clock:'igt',rom:'us',scope:'strategy',strategy:'Standard',generation:catalog.generation,minStep:2,maxStep:2,pageSize:256,after:[2,50,'f'.repeat(64)]})).length,0);
    await store.sql("UPDATE accounts SET invited=0 WHERE uid=?",bob).run();await store.sql('UPDATE settings SET global_epoch=global_epoch+1').run();
    assert(!((await h.call(pool())).json.recordings.some(r=>r.player_id===bob)));
    assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('restricted authors gate cached pools, search, direct metadata, bytes and pins by UID',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    const p=await published(h);env.BETA_SHARED_PUBLISHERS='[]';
    assert.equal((await h.call(pool(),{uid:bob})).json.recordings.length,0);
    assert.equal((await h.call(pool(),{uid:alice})).json.recordings.length,1);
    assert.equal(h.cached.size,2);
    for(const suffix of ['','/asset'])assert.equal((await h.call('/v1/recordings/'+p.recording_id+suffix,{uid:bob})).response.status,404);
    assert.equal((await h.call('/v1/pins',{uid:bob,method:'PUT',body:{recording_ids:[p.recording_id]}})).response.status,404);
    const search='/v1/search?'+new URLSearchParams({target:JSON.stringify(target),clock:'igt',rom:'us',scope:'strategy',strategy:'Standard',prefix:'Al'});
    assert.equal((await h.call(search,{uid:bob})).json.recordings.length,0);
    env.BETA_SHARED_PUBLISHERS=JSON.stringify([alice]);
    assert.equal((await h.call(search,{uid:bob})).json.recordings.length,1);
    assert.equal((await h.call('/v1/recordings/'+p.recording_id+'/asset',{uid:bob})).response.status,200);
    assert.equal((await h.call(pool(),{uid:bob})).json.recordings.length,1);
    env.BETA_SHARED_PUBLISHERS='["griffman1212"]';assert.equal((await h.call(pool(),{uid:bob})).response.status,503);
    assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('fastest fallback preserves strategy selection and overall can expose other strategies',async()=>{
  const {env,store}=await environment(),entries=structuredClone(catalog.entries);
  entries[0].strategies.push({name:'Alternate',cutoffs:structuredClone(entries[0].strategies[0].cutoffs)});
  const model={v:1,generation:await sha256(canonical(entries)),entries},h=harness(env,model);
  try{
    await store.sql('UPDATE settings SET generation=?',model.generation).run();
    await published(h,{frames:110});await published(h,{uid:bob,marker:2,frames:90,strategy:'Alternate'});
    assert.deepEqual((await h.call(pool())).json.recordings.map(r=>[r.strategy,r.frames]),[['Standard',110]]);
    const url=new URL(pool(),'https://community.example.invalid');url.searchParams.set('scope','overall');url.searchParams.delete('strategy');
    assert.deepEqual((await h.call(url.pathname+url.search)).json.recordings.map(r=>[r.strategy,r.frames]),[['Alternate',90],['Standard',110]]);
    assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('a stale upload catalog is an explicit 409 refresh boundary before retained admission',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    const body={...upload().body,generation:'0'.repeat(64)},r=await h.call('/v1/uploads',{method:'POST',body});
    assert.equal(r.response.status,409);assert.equal(r.json.error.code,'generation_changed');assert.equal((await store.one('SELECT COUNT(*) AS n FROM grants')).n,0);assert.equal((await store.one('SELECT bytes_reserved FROM storage_budget')).bytes_reserved,0);
  }finally{env.DB.close();}
});
test('collection has reserved capacity when user budgets are spent and admission is disabled',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    await published(h);await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}});
    await store.sql('UPDATE assets SET created_at=0').run();await store.sql('UPDATE quotas SET ops=ops_limit').run();await store.sql('UPDATE settings SET enabled=0').run();
    const before=await store.rows('SELECT owner,ops FROM quotas ORDER BY owner');
    assert.equal((await reconcile(env)).deleted,1);assert.deepEqual(await store.rows('SELECT owner,ops FROM quotas ORDER BY owner'),before);
    const reserved=await store.one('SELECT ops,class_a FROM maintenance_quotas');assert.equal(reserved.ops,1);assert.equal(reserved.class_a,1);
    assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,0);
  }finally{env.DB.close();}
});
test('one failed physical deletion is charged and does not block other due assets',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    await published(h);await published(h,{uid:bob,marker:2});
    for(const uid of [alice,bob])await h.call('/v1/account/withdraw',{uid,method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}});
    await store.sql('UPDATE assets SET created_at=0').run();const assets=await store.rows('SELECT key,encoded_bytes FROM assets ORDER BY key');
    const remove=env.GHOSTS.delete.bind(env.GHOSTS);env.GHOSTS.delete=async key=>{if(key===assets[0].key)throw Error('fixture provider failure');return remove(key);};
    const result=await reconcile(env);assert.deepEqual(result,{deleted:1,retried:1});assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,assets[0].encoded_bytes);
    assert((await store.one('SELECT retry_at FROM assets')).retry_at>second);assert.equal((await reconcile(env)).retried,0);
    await store.sql('UPDATE assets SET retry_at=0').run();env.GHOSTS.delete=remove;assert.equal((await reconcile(env)).deleted,1);
  }finally{env.DB.close();}
});
test('maintenance budget exhaustion postpones IO without refunding retained storage',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    await published(h);await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}});await store.sql('UPDATE assets SET created_at=0').run();
    await store.ticket('*',{maintenance:true});await store.sql('UPDATE maintenance_quotas SET ops=10000').run();
    assert.equal((await reconcile(env)).retried,1);assert.equal(env.GHOSTS.objects.size,1);assert((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used>0);
  }finally{env.DB.close();}
});
test('migration-blocked stored grants back off so the next bounded batch drains current work',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    await published(h);await published(h,{uid:bob,marker:2});
    await store.sql("UPDATE grants SET id='00000000-0000-4000-8000-000000000001',state='stored',generation=? WHERE uid=?",'0'.repeat(64),alice).run();
    await store.sql("UPDATE grants SET id='ffffffff-ffff-4fff-8fff-ffffffffffff',state='stored' WHERE uid=?",bob).run();
    await store.sql('UPDATE settings SET migration_generation=?',catalog.generation).run();
    assert.equal((await reconcile(env,{limit:1})).retried,1);
    assert((await store.one('SELECT retry_at FROM grants WHERE uid=?',alice)).retry_at>second);
    assert.equal((await store.one('SELECT state FROM grants WHERE uid=?',bob)).state,'stored');
    await store.sql('UPDATE settings SET migration_generation=NULL').run();
    assert.equal((await reconcile(env,{limit:1})).retried,0);
    assert.equal((await store.one('SELECT state FROM grants WHERE uid=?',bob)).state,'complete');
  }finally{env.DB.close();}
});
test('daily denial gives UTC reset Retry-After, burst denial gives a short retry',async()=>{
  const {env,store}=await environment(),h=harness(env);try{
    await store.ticket(alice);await store.sql('UPDATE quotas SET ops=ops_limit').run();const r=await h.call('/v1/catalog');
    assert.equal(r.response.status,429);assert.equal(r.json.error.code,'budget_exhausted');assert(Number(r.response.headers.get('retry-after'))>=1&&Number(r.response.headers.get('retry-after'))<=86400);
    env.BURST={limit:async()=>({success:false})};assert.equal((await h.call('/v1/catalog')).response.headers.get('retry-after'),'60');
  }finally{env.DB.close();}
});
test('maintenance stages isolate failures and durable alarms reschedule before external IO',async()=>{
  const calls=[],storage={alarmTime:null,values:new Map(),async getAlarm(){return this.alarmTime;},async setAlarm(at){this.alarmTime=at;},async put(k,v){this.values.set(k,v);},async get(k){return this.values.get(k);}};
  const run=()=>runMaintenance({}, {identity:async()=>{calls.push('identity');throw Error('secret provider text');},catalog:async()=>{calls.push('catalog');return {};},storage:async()=>{calls.push('storage');return {retried:1};}});
  const clock=new MaintenanceClock(storage,{},async()=>{assert(storage.alarmTime>Date.now());return run();});const start=Date.now();
  await clock.arm();const alarm=storage.alarmTime;assert(alarm>=start+MAINTENANCE_INTERVAL_MS);await clock.arm();assert.equal(storage.alarmTime,alarm);
  storage.alarmTime=null;await clock.alarm();assert.deepEqual(calls,['identity','catalog','storage']);
  const status=await clock.status();assert.deepEqual(status.last_result.stages,{identity:'retry',catalog:'ok',storage:'retry'});assert(status.next_alarm_ms>status.last_attempt_ms);assert(!JSON.stringify(status).includes('secret'));
  assert.doesNotThrow(()=>armMaintenance({MAINTENANCE:{getByName(){throw Error('unavailable');}}},{}));
});
test('only admitted requests can arm the fixed maintenance object',async()=>{
  const {env}=await environment();let calls=0;
  env.MAINTENANCE={getByName(name){assert.equal(name,'community-maintenance');return {async arm(){calls++;}};}};
  const h=harness(env);try{
    assert.equal((await h.call('/v1/catalog',{uid:third})).response.status,403);assert.equal(calls,0);
    assert.equal((await h.call('/v1/catalog')).response.status,200);assert.equal(calls,1);
    assert.equal((await h.call('/maintenance')).response.status,404);
  }finally{env.DB.close();}
});
