import test from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {readFileSync} from 'node:fs';
import {createService} from '../src/service.js';
import {config,LIMITS} from '../src/config.js';
import {sha256} from '../src/errors.js';
import {environment,alice,catalog,upload} from './fixtures.js';

const token='R'.repeat(43),uid=index=>'40000000-0000-4000-8000-'+String(index).padStart(12,'0');
const stamp=()=>Math.floor(Date.now()/1000);
function harness(env){
  const errors=[],service=createService({auth:async request=>({issuer:config(env).issuer,sub:request.headers.get('authorization').slice(7)}),catalog:async()=>catalog,onError:error=>errors.push(error)});
  return {errors,async call(path,{owner=uid(1),method='POST',body={token}}={}){
    const response=await service.fetch(new Request('https://community.example.invalid'+path,{method,
      headers:{authorization:'Bearer '+owner,'content-type':'application/json','idempotency-key':crypto.randomUUID()},
      body:method==='GET'?undefined:JSON.stringify(body)}),env,{});
    return {status:response.status,json:await response.json()};
  },join(owner=uid(1),value=token){return this.call('/v1/account/invitation',{owner,body:{token:value}});}};
}
async function code(store,{reusable=1,expiry=0,revoked=0}={}){
  await store.sql('INSERT INTO beta_invitations(token_sha256,expires_at,revoked,reusable,created_at) VALUES(?,?,?,?,?)',await sha256(token),expiry,revoked,reusable,stamp()).run();
}
async function uncapped(store){await store.sql('UPDATE beta_admission SET uncapped=1 WHERE id=1').run();}
async function activate(store,owner){
  await store.sql("UPDATE accounts SET status='active',name=?,name_key=?,sharing=1,terms_version='test-1',privacy_version='test-1' WHERE uid=?",'Tester'+owner.slice(-3),'tester'+owner.slice(-3),owner).run();
}

test('additive flags preserve legacy invitation rows, caps and boolean validation',()=>{
  const db=new DatabaseSync(':memory:');
  try{
    for(const migration of ['0001_community.sql','0002_friend_beta.sql','0003_auth_entry_budget.sql'])db.exec(readFileSync(new URL('../migrations/'+migration,import.meta.url),'utf8'));
    db.exec("INSERT INTO beta_invitations(token_sha256,expires_at,created_at) VALUES('"+'0'.repeat(64)+"',12345,6789)");
    const invitation={...db.prepare('SELECT * FROM beta_invitations').get()},admission={...db.prepare('SELECT * FROM beta_admission').get()};
    db.exec(readFileSync(new URL('../migrations/0004_reusable_beta_invitation.sql',import.meta.url),'utf8'));
    assert.deepEqual({...db.prepare('SELECT * FROM beta_invitations').get()},{...invitation,reusable:0});
    assert.deepEqual({...db.prepare('SELECT * FROM beta_admission').get()},{...admission,uncapped:0});
    assert.throws(()=>db.exec('UPDATE beta_invitations SET reusable=2'),/CHECK constraint/);
    assert.throws(()=>db.exec('UPDATE beta_admission SET uncapped=2'),/CHECK constraint/);
  }finally{db.close();}
});
test('one reusable code admits more than 64 distinct authenticated players without consumption',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    await code(store);await uncapped(store);
    const results=await Promise.all(Array.from({length:75},(_,index)=>h.join(uid(index+1))));
    assert(results.every(result=>result.status===200&&result.json.status==='new'));
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,77);
    assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,75);
    assert.deepEqual({...await store.one('SELECT claimed_uid,claimed_at FROM beta_invitations')},{claimed_uid:null,claimed_at:null});
    assert.equal((await store.one('SELECT ops FROM auth_entry_budget')).ops,75);
    await store.invite(uid(100),true,'operator@example.invalid','Another beta tester','CHANGE INVITATION',{key:crypto.randomUUID(),digest:'0'.repeat(64)});
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,78);
    assert.equal((await store.one('SELECT max_members FROM beta_admission')).max_members,8);
    assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('concurrent same-UID joins and retries preserve account/profile and increment the epoch once',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    await code(store);await uncapped(store);
    assert((await Promise.all(Array.from({length:8},()=>h.join()))).every(result=>result.status===200));
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,3);
    assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,1);
    await activate(store,uid(1));
    const before=await store.one('SELECT * FROM accounts WHERE uid=?',uid(1));
    const results=await Promise.all(Array.from({length:20},()=>h.join()));
    assert(results.every(result=>result.status===200&&result.json.name===before.name&&result.json.sharing));
    assert.deepEqual(await store.one('SELECT * FROM accounts WHERE uid=?',uid(1)),before);
    assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,1);
    assert.equal((await store.one('SELECT ops FROM quotas WHERE owner=?',uid(1))).ops,20);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM guards')).n,0);
    assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('reusable codes retain atomic capped mode and single-use codes retain one UID in uncapped mode',async()=>{
  for(const reusable of [0,1]){
    const {env,store}=await environment(),h=harness(env);
    try{
      await code(store,{reusable,expiry:reusable?0:stamp()+3600});
      if(reusable)await store.sql('UPDATE beta_admission SET max_members=3').run();else await uncapped(store);
      const results=await Promise.all([uid(1),uid(2),uid(3)].map(owner=>h.join(owner)));
      assert.deepEqual(results.map(result=>result.status).sort(),[200,403,403]);
      assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,3);
      const owner=[uid(1),uid(2),uid(3)][results.findIndex(result=>result.status===200)];
      assert.equal((await h.join(owner)).status,200);
      assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,1);
      assert.equal((await store.one('SELECT claimed_uid FROM beta_invitations')).claimed_uid,reusable?null:owner);
      assert.deepEqual(h.errors,[]);
    }finally{env.DB.close();}
  }
});
test('shared invitations reject invalid, revoked, expired, legacy-zero-expiry and disabled admission',async()=>{
  for(const mode of ['invalid','revoked','expired','legacy-zero','disabled']){
    const {env,store}=await environment(),h=harness(env);
    try{
      await uncapped(store);
      if(mode!=='invalid')await code(store,{reusable:mode==='legacy-zero'?0:1,expiry:mode==='expired'?stamp()-1:0,revoked:Number(mode==='revoked')});
      if(mode==='disabled')await store.sql('UPDATE settings SET enabled=0').run();
      const result=await h.join();assert.equal(result.status,403,mode);assert.equal(result.json.error.code,'invite_unavailable');
      assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,2);
      assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,0);
      assert.deepEqual(h.errors,[]);
    }finally{env.DB.close();}
  }
});
test('finite reusable expiry admits several players until the boundary and then rejects new or existing UID',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    await code(store,{expiry:stamp()+3600});await uncapped(store);
    assert.equal((await h.join(uid(1))).status,200);assert.equal((await h.join(uid(2))).status,200);
    await store.sql('UPDATE beta_invitations SET expires_at=?',stamp()).run();
    for(const owner of [uid(1),uid(3)])assert.equal((await h.join(owner)).status,403);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,4);
    assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,2);
    assert.deepEqual({...await store.one('SELECT claimed_uid,claimed_at FROM beta_invitations')},{claimed_uid:null,claimed_at:null});
  }finally{env.DB.close();}
});
test('a shared code cannot restore revoked, suspended or deleted accounts or cross issuers',async()=>{
  for(const mode of ['revoked','suspended','deleted','issuer']){
    const {env,store}=await environment(),h=harness(env);
    try{
      await code(store);await uncapped(store);
      if(mode==='revoked')await store.sql('UPDATE accounts SET invited=0 WHERE uid=?',alice).run();
      else if(mode==='issuer')await store.sql('UPDATE accounts SET issuer=? WHERE uid=?','https://other.example.invalid/auth/v1',alice).run();
      else await store.sql('UPDATE accounts SET status=? WHERE uid=?',mode,alice).run();
      const before=await store.one('SELECT * FROM accounts WHERE uid=?',alice);
      assert.equal((await h.join(alice)).status,403,mode);
      assert.deepEqual(await store.one('SELECT * FROM accounts WHERE uid=?',alice),before);
      assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,0);
      await assert.rejects(store.redeem({issuer:'https://other.example.invalid/auth/v1',sub:uid(1)},token),error=>error.code==='invalid_issuer');
    }finally{env.DB.close();}
  }
});
test('revocation after capability lookup still blocks the transactional join',async()=>{
  const {env,store}=await environment();
  try{
    await code(store);await uncapped(store);
    const transaction=store.tx.bind(store);store.tx=async(...args)=>{
      await store.sql('UPDATE beta_invitations SET revoked=1').run();return transaction(...args);
    };
    await assert.rejects(store.redeem({issuer:config(env).issuer,sub:uid(1)},token),error=>error.code==='invite_unavailable');
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,2);
    assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,0);
  }finally{env.DB.close();}
});
test('uncapped membership does not bypass entry, owner/global requests or retained storage limits',async()=>{
  for(const mode of ['entry','owner','global','storage']){
    const {env,store}=await environment(),h=harness(env),day=new Date().toISOString().slice(0,10);
    try{
      await code(store);await uncapped(store);
      if(mode==='entry'){
        await store.sql('UPDATE auth_entry_budget SET day=?,ops=?',day,LIMITS.global_ops_day).run();
        const denied=await h.join();assert.equal(denied.status,429);assert.equal(denied.json.error.code,'budget_exhausted');
        assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,2);
      }else{
        assert.equal((await h.join()).status,200);await activate(store,uid(1));
        await store.ticket(uid(1));
        if(mode==='owner'||mode==='global'){
          await store.sql('UPDATE quotas SET ops=ops_limit WHERE owner=?',mode==='owner'?uid(1):'*').run();
          const denied=await h.join();assert.equal(denied.status,429);assert.equal(denied.json.error.code,'budget_exhausted');
        }else{
          await store.sql('UPDATE accounts SET bytes_used=? WHERE uid=?',LIMITS.retained_bytes,uid(1)).run();
          await store.sql('UPDATE storage_budget SET bytes_used=?',LIMITS.retained_bytes).run();
          const denied=await h.call('/v1/uploads',{body:upload().body});assert.equal(denied.status,429);assert.equal(denied.json.error.code,'storage_limit');
          assert.equal((await store.one('SELECT COUNT(*) AS n FROM grants')).n,0);
          assert.equal((await store.one('SELECT bytes_reserved FROM storage_budget')).bytes_reserved,0);
          assert.equal(env.GHOSTS.objects.size,0);
        }
      }
      assert.equal((await store.one('SELECT global_epoch FROM settings')).global_epoch,mode==='entry'?0:1);
      assert.deepEqual(h.errors,[]);
    }finally{env.DB.close();}
  }
});
