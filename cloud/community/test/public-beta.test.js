import test from 'node:test';
import assert from 'node:assert/strict';
import {createService} from '../src/service.js';
import {config,LIMITS} from '../src/config.js';
import {Store} from '../src/store.js';
import {environment,alice,bob,catalog,target,upload} from './fixtures.js';

const fresh='30000000-0000-4000-8000-000000000001';
const another='30000000-0000-4000-8000-000000000002';
const profile={name:'New Player',sharing:true,terms_version:'test-1',privacy_version:'test-1',sheet_runner:null};
function harness(env){
  const errors=[],service=createService({auth:async r=>({issuer:config(env).issuer,sub:r.headers.get('authorization').slice(7)}),catalog:async()=>catalog,onError:e=>errors.push(e)});
  return {errors,async call(path='/v1/account',{uid=fresh,method='GET',body,bytes,key=crypto.randomUUID()}={}){
    const response=await service.fetch(new Request('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+uid,...(body?{'content-type':'application/json','idempotency-key':key}:{}),...(bytes?{'content-type':'application/octet-stream'}:{})},body:body?JSON.stringify(body):bytes}),env,{});
    return {status:response.status,json:await response.json()};
  }};
}
async function fixture(){const result=await environment();result.env.PUBLIC_BETA_SIGNUP='true';return {...result,h:harness(result.env)};}
async function counts(store){return {accounts:(await store.one('SELECT COUNT(*) AS n FROM accounts')).n,quotas:(await store.one('SELECT COUNT(*) AS n FROM quotas')).n,receipts:(await store.one('SELECT COUNT(*) AS n FROM mutation_receipts')).n};}

test('public eligibility reads use the old new-profile wire shape and retain no identity or quota',async()=>{
  const {env,store,h}=await fixture();try{
    let armed=0;env.MAINTENANCE={getByName(){return {async arm(){armed++;}};}};
    for(let i=0;i<8;i++){
      const result=await h.call();assert.equal(result.status,200);
      assert.deepEqual(result.json,{v:1,uid:fresh,name:null,status:'new',sharing:false,terms_version:null,privacy_version:null,sheet_runner:null,sheet_verified:false});
      assert.equal((await h.call('/v1/names/check?name=New%20Player')).json.available,true);
    }
    for(const path of ['/v1/catalog','/v1/account/export','/v1/unsupported'])assert.equal((await h.call(path)).status,403);
    for(const [path,body] of [['/v1/account/name',{name:'New Player'}],['/v1/account/sharing',{enabled:true}],['/v1/account/delete',{confirmation:'DELETE MY ACCOUNT'}]])
      assert.equal((await h.call(path,{method:path.endsWith('delete')?'POST':'PUT',body})).status,403);
    assert.equal((await h.call('/v1/account/invitation',{method:'POST',body:{token:'A'.repeat(43)}})).status,403);
    assert.deepEqual(await counts(store),{accounts:2,quotas:0,receipts:0});
    assert.equal(armed,0);
    assert((await store.one('SELECT ops FROM auth_entry_budget')).ops>0);assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});

test('invitation-free profile completion, retries and concurrent first submissions preserve one account',async()=>{
  const {env,store,h}=await fixture();try{
    const key=crypto.randomUUID(),first=await h.call('/v1/account',{method:'PUT',body:profile,key});
    assert.equal(first.status,200,JSON.stringify(first.json));assert.equal(first.json.status,'active');
    const before=await store.one('SELECT * FROM accounts WHERE uid=?',fresh),epoch=await store.one('SELECT global_epoch FROM settings');
    assert.equal(before.invited,1);assert.equal(before.sharing,1);assert.equal(before.issuer,config(env).issuer);
    assert.deepEqual((await h.call('/v1/account',{method:'PUT',body:profile,key})).json,first.json);
    assert.deepEqual(await store.one('SELECT * FROM accounts WHERE uid=?',fresh),before);
    assert.deepEqual(await store.one('SELECT global_epoch FROM settings'),epoch);
    assert.equal((await h.call('/v1/account',{method:'PUT',body:{...profile,name:'Changed'},key})).json.error.code,'idempotency_conflict');
    assert.equal((await h.call('/v1/catalog')).status,200);
    const results=await Promise.all(Array.from({length:8},()=>h.call('/v1/account',{uid:another,method:'PUT',body:{...profile,name:'Concurrent'}})));
    assert(results.every(r=>r.status===200),JSON.stringify(results));
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,4);
    assert.equal((await store.one('SELECT ops FROM quotas WHERE owner=?',another)).ops,8);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM beta_invitations')).n,0);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM guards')).n,0);assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});

test('public signup requires current policies, explicit sharing choice and a valid unique normalized name',async()=>{
  const {env,store,h}=await fixture();try{
    for(const body of [{...profile,terms_version:'old'},{...profile,privacy_version:null},{...profile,sharing:'yes'},
      {...profile,sheet_runner:{}},{...profile,name:'admin'},{...profile,name:'Hidden\u200b'},
      {...profile,name:'x'.repeat(21)},{...profile,name:'😀'.repeat(16)},{...profile,name:'ＡＬＩＣＥ'}]){
      const denied=await h.call('/v1/account',{method:'PUT',body});assert([400,409].includes(denied.status),JSON.stringify(denied));
      assert.deepEqual(await counts(store),{accounts:2,quotas:0,receipts:0});
    }
    const result=await h.call('/v1/account',{method:'PUT',body:{...profile,name:'  日本のプレイヤー  ',sharing:false}});
    assert.equal(result.status,200);assert.equal(result.json.name,'日本のプレイヤー');assert.equal(result.json.sharing,false);
    env.TERMS_VERSION='test-2';assert.equal((await h.call('/v1/catalog')).json.error.code,'consent_required');
    assert.equal((await h.call('/v1/account',{method:'PUT',body:{...profile,name:'日本のプレイヤー',terms_version:'test-2'}})).status,200);
    assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});

test('capped public signup is atomic, counts tombstones and uncapped changes only headcount',async()=>{
  const {env,store,h}=await fixture();try{
    await store.sql('UPDATE beta_admission SET max_members=3').run();
    const results=await Promise.all([fresh,another].map((uid,i)=>h.call('/v1/account',{uid,method:'PUT',body:{...profile,name:'Player'+i}})));
    assert.deepEqual(results.map(r=>r.status).sort(),[200,409]);
    const winner=results[0].status===200?fresh:another,loser=winner===fresh?another:fresh;
    assert.equal(await store.one('SELECT owner FROM quotas WHERE owner=?',loser),null);
    await store.sql("UPDATE accounts SET status='deleted',invited=0 WHERE uid=?",winner).run();
    assert.equal((await h.call('/v1/account',{uid:loser,method:'PUT',body:profile})).status,409);
    await store.sql('UPDATE beta_admission SET uncapped=1').run();
    assert.equal((await h.call('/v1/account',{uid:loser,method:'PUT',body:profile})).status,200);
    assert.equal((await store.one('SELECT max_members FROM beta_admission')).max_members,3);assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});

test('public mode cannot restore revoked, suspended, deleted or foreign-issuer owners',async()=>{
  for(const mode of ['revoked','suspended','deleted','issuer']){
    const {env,store,h}=await fixture();try{
      if(mode==='revoked')await store.sql('UPDATE accounts SET invited=0 WHERE uid=?',alice).run();
      else if(mode==='issuer')await store.sql('UPDATE accounts SET issuer=? WHERE uid=?','https://other.invalid/auth/v1',alice).run();
      else await store.sql('UPDATE accounts SET status=? WHERE uid=?',mode,alice).run();
      const before=await store.one('SELECT * FROM accounts WHERE uid=?',alice);
      if(mode!=='issuer')assert.equal((await h.call('/v1/account',{uid:alice})).json.status,mode==='revoked'?'not_invited':mode);
      assert.equal((await h.call('/v1/account',{uid:alice,method:'PUT',body:profile})).status,403);
      assert.deepEqual(await store.one('SELECT * FROM accounts WHERE uid=?',alice),before);
      assert.equal((await store.one('SELECT COUNT(*) AS n FROM quotas')).n,0);assert.deepEqual(h.errors,[]);
    }finally{env.DB.close();}
  }
});

test('uncapped public mode admits more than 64 players without any invitation capability',async()=>{
  const {env,store,h}=await fixture();try{
    await store.sql('UPDATE beta_admission SET uncapped=1').run();
    const results=await Promise.all(Array.from({length:75},(_,i)=>h.call('/v1/account',{uid:'40000000-0000-4000-8000-'+String(i).padStart(12,'0'),method:'PUT',body:{...profile,name:'Public'+i}})));
    assert(results.every(r=>r.status===200),JSON.stringify(results));
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM accounts')).n,77);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM quotas')).n,76);
    assert.equal((await store.one("SELECT ops FROM quotas WHERE owner='*'")).ops,75);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM beta_invitations')).n,0);assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});

test('public eligibility cannot override a refusal inserted after profile lookup',async()=>{
  for(const mode of ['revoked','suspended','deleted','issuer']){
    const {env,store}=await fixture();try{
      const publicStore=new Store(env.DB,config(env)),a=await publicStore.account({issuer:config(env).issuer,sub:fresh});
      await store.sql('INSERT INTO accounts(uid,issuer,subject,invited,status,created_at,updated_at) VALUES(?,?,?,?,?,0,0)',fresh,mode==='issuer'?'https://other.invalid/auth/v1':config(env).issuer,fresh,Number(mode!=='revoked'),['suspended','deleted'].includes(mode)?mode:'new').run();
      await assert.rejects(publicStore.profile(a,profile,crypto.randomUUID(),'0'.repeat(64)),e=>e.status===409);
      assert.equal((await store.one('SELECT COUNT(*) AS n FROM quotas')).n,0);
    }finally{env.DB.close();}
  }
});

test('public reads and first signup preserve fixed entry, global and owner budgets and disabled admission',async()=>{
  for(const mode of ['entry','global','owner','disabled']){
    const {env,store,h}=await fixture();try{
      await store.sql('UPDATE beta_admission SET uncapped=1').run();
      if(mode==='entry')await store.sql('UPDATE auth_entry_budget SET day=?,ops=?',new Date().toISOString().slice(0,10),LIMITS.global_ops_day).run();
      if(mode==='global'){await store.ticket(alice);await store.sql("UPDATE quotas SET ops=ops_limit WHERE owner='*'").run();}
      if(mode==='disabled')await store.sql('UPDATE settings SET enabled=0').run();
      if(mode==='owner'){
        assert.equal((await h.call('/v1/account',{method:'PUT',body:profile})).status,200);
        await store.sql('UPDATE quotas SET ops=ops_limit WHERE owner=?',fresh).run();
      }
      const before=await counts(store),denied=await h.call('/v1/account',{method:'PUT',body:profile});
      assert.equal(denied.status,mode==='disabled'?409:429,JSON.stringify(denied));
      if(mode!=='disabled')assert.equal(denied.json.error.code,'budget_exhausted');
      assert.deepEqual(await counts(store),before);
      if(mode==='global')assert.equal(await store.one('SELECT owner FROM quotas WHERE owner=?',fresh),null);
      assert.deepEqual(h.errors,[]);
    }finally{env.DB.close();}
  }
});

test('public accounts keep sharing, storage and authenticated owner predicates',async()=>{
  const {env,store,h}=await fixture();try{
    assert.equal((await h.call('/v1/account',{method:'PUT',body:{...profile,sharing:false}})).status,200);
    assert.equal((await h.call('/v1/uploads',{method:'POST',body:upload().body})).json.error.code,'sharing_disabled');
    assert.equal((await h.call('/v1/account/sharing',{method:'PUT',body:{enabled:true}})).status,200);
    await store.sql('UPDATE accounts SET bytes_used=? WHERE uid=?',LIMITS.retained_bytes,fresh).run();
    assert.equal((await h.call('/v1/uploads',{method:'POST',body:upload().body})).json.error.code,'storage_limit');
    assert.equal((await h.call('/v1/account/sharing',{uid:bob,method:'PUT',body:{enabled:false}})).status,200);
    assert.equal((await store.one('SELECT sharing FROM accounts WHERE uid=?',fresh)).sharing,1);assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});

test('missing, false or malformed public flag preserves private invitation admission',async()=>{
  for(const flag of [undefined,'false','TRUE',true,'1']){
    const {env,store}=await environment(),h=harness(env);try{
      if(flag!==undefined)env.PUBLIC_BETA_SIGNUP=flag;
      assert.equal(config(env).publicBetaSignup,false);
      assert.equal((await h.call()).json.status,'not_invited');
      assert.equal((await h.call('/v1/account',{method:'PUT',body:profile})).status,403);
      assert.deepEqual(await counts(store),{accounts:2,quotas:0,receipts:0});assert.deepEqual(h.errors,[]);
    }finally{env.DB.close();}
  }
});

test('ordinary public members share the normal pool unless the operator restricts publishers',async()=>{
  const {env,h}=await fixture();try{
    for(const [uid,name] of [[fresh,'Publisher'],[another,'Viewer']])assert.equal((await h.call('/v1/account',{uid,method:'PUT',body:{...profile,name}})).status,200);
    const clip=upload(),grant=await h.call('/v1/uploads',{method:'POST',body:clip.body});assert.equal(grant.status,200);
    const publication=await h.call('/v1/uploads/'+grant.json.upload_id,{method:'PUT',bytes:clip.bytes});assert.equal(publication.json.disposition,'published');
    const pool='/v1/pool?'+new URLSearchParams({target:JSON.stringify(target),clock:'igt',rom:'us',scope:'strategy',strategy:'Standard',min_step:'2',max_step:'2',base_step:'2'});
    const shared=await h.call(pool,{uid:another});assert.equal(shared.status,200);assert.deepEqual(shared.json.recordings.map(r=>r.player_id),[fresh]);
    env.BETA_SHARED_PUBLISHERS='[]';assert.equal((await h.call(pool,{uid:another})).json.recordings.length,0);
    delete env.BETA_SHARED_PUBLISHERS;assert.equal((await h.call(pool,{uid:another})).json.recordings.length,1);assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});

test('policies describe the selected public or private beta audience truthfully',async()=>{
  const {env}=await fixture();try{
    for(const flag of ['true','false']){
      env.PUBLIC_BETA_SIGNUP=flag;
      const response=await createService().fetch(new Request('https://community.example.invalid/policies'),env,{}),html=await response.text();
      assert.equal(response.status,200);assert(html.includes('Community ghosts '+(flag==='true'?'open':'private')+' beta'));
      assert.equal(html.includes('Anyone can join'),flag==='true');assert(html.includes('Admitted signed-in players can receive'));
    }
  }finally{env.DB.close();}
});
