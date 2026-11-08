// A ghost's start: optional opaque bytes a trainer uploads after the ghost's
// GHC1 bytes. The service stores both in one R2 object, never reads the start,
// and serves it beside the unchanged ghost. Griffin, 2026-11-08: "Yes, 5% more
// per ghost is fine, and go ahead and update.."
import test from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {createService} from '../src/service.js';
import {reconcile} from '../src/reconcile.js';
import {regrade} from '../src/regrade.js';
import {deleteIdentities} from '../src/identity-deletion.js';
import {config,LIMITS} from '../src/config.js';
import {canonical} from '../src/errors.js';
import {environment,alice,catalog,nativeClip,upload,digest} from './fixtures.js';

// The strict v1 metadata row every released trainer checks key for key.
const METADATA=['id','player_id','player_name','target','strategy','clock','game_version','frames','raw_sha256','raw_bytes',
  'encoded_sha256','encoded_bytes','codec','generation','step','provenance','structurally_valid','full_start'];

function harness(env,{cached=true,model=catalog}={}){
  const props=[],caches=new Map();
  const exports={AssetCache:({props:p})=>({async fetch(){
    props.push(p);const key=canonical(p);
    if(!caches.has(key))caches.set(key,(await env.GHOSTS.get(p.key,p.range?{range:p.range}:undefined))?.body);
    return caches.get(key)?new Response(caches.get(key)):new Response(null,{status:404});
  }})};
  const service=createService({auth:async r=>({issuer:config(env).issuer,sub:r.headers.get('authorization')==='Bearer bob'?'10000000-0000-4000-8000-000000000002':alice}),
    catalog:async()=>model,onError:e=>{throw e;}});
  return {props,async call(path,{method='GET',body,headers={},user='alice',bytes}={}){
    const request=new Request('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+user,
      ...(body?{'content-type':'application/json','idempotency-key':crypto.randomUUID()}:{}),
      ...(bytes?{'content-type':'application/octet-stream'}:{}),...headers},body:body?JSON.stringify(body):bytes});
    const response=await service.fetch(request,env,cached?{exports}:{});
    const json=response.headers.get('content-type')?.includes('json')?await response.json():null;
    return {response,json};
  }};
}

// A start the trainer would send: incompressible bytes of a measured size.
function withStart(fixture,size=26132){
  const start=randomBytes(size);
  return {...fixture,start,body:{...fixture.body,start_sha256:digest(start),start_bytes:start.length}};
}

async function publish(h,fixture,{user='alice'}={}){
  const admission=await h.call('/v1/uploads',{method:'POST',body:fixture.body,user});
  assert.equal(admission.json.disposition,'upload',JSON.stringify(admission.json));
  const bytes=fixture.start?Buffer.concat([fixture.bytes,fixture.start]):fixture.bytes;
  const result=await h.call('/v1/uploads/'+admission.json.upload_id,{method:'PUT',bytes,user});
  assert.equal(result.json?.disposition,'published',JSON.stringify(result.json));
  return result.json.recording_id;
}

const bytesOf=async response=>Buffer.from(await response.arrayBuffer());
// node:sqlite rows have a null prototype; compare their fields.
const row=async(store,...query)=>({...await store.one(...query)});
const used=store=>row(store,'SELECT bytes_used,bytes_reserved FROM storage_budget WHERE id=1');

for(const cached of [true,false])
test(`a start uploads with its ghost and downloads beside the unchanged ghost bytes (${cached?'asset cache':'direct R2'})`,async()=>{
  const {env,store}=await environment(),h=harness(env,{cached});
  try{
    const fixture=withStart(upload()),id=await publish(h,fixture);
    // One object holds both, in order; its metadata names both hashes.
    assert.equal(env.GHOSTS.objects.size,1);
    const [key,object]=[...env.GHOSTS.objects][0];
    assert.deepEqual(Buffer.from(object),Buffer.concat([fixture.bytes,fixture.start]));
    assert.deepEqual(env.GHOSTS.metadata.get(key),{encoded_sha256:fixture.body.encoded_sha256,start_sha256:fixture.body.start_sha256});
    // The strict metadata row is unchanged for released trainers.
    const metadata=await h.call('/v1/recordings/'+id);
    assert.deepEqual(Object.keys(metadata.json).filter(k=>k!=='v').sort(),[...METADATA].sort());
    // The ghost's bytes, length and ETag are exactly what an older trainer expects.
    const asset=await h.call('/v1/recordings/'+id+'/asset',{user:'bob'});
    assert.equal(asset.response.status,200);
    assert.deepEqual(await bytesOf(asset.response),fixture.bytes);
    assert.equal(asset.response.headers.get('content-length'),String(fixture.bytes.length));
    assert.equal(asset.response.headers.get('etag'),'"'+fixture.body.encoded_sha256+'"');
    assert.equal(asset.response.headers.get('ghost-start-sha256'),fixture.body.start_sha256);
    assert.equal(asset.response.headers.get('ghost-start-bytes'),String(fixture.start.length));
    const start=await h.call('/v1/recordings/'+id+'/start',{user:'bob'});
    assert.equal(start.response.status,200);
    assert.deepEqual(await bytesOf(start.response),fixture.start);
    assert.equal(start.response.headers.get('content-type'),'application/octet-stream');
    assert.equal(start.response.headers.get('cache-control'),'private, no-store');
    assert.equal(start.response.headers.get('etag'),'"'+fixture.body.start_sha256+'"');
    if(cached)assert.deepEqual(h.props.map(p=>p.range),[{offset:0,length:fixture.bytes.length},{offset:fixture.bytes.length,length:fixture.start.length}]);
    // Each download spends one Class B ticket, the start's like the ghost's.
    assert.equal((await store.one("SELECT class_b FROM quotas WHERE owner='*'")).class_b,2);
  }finally{env.DB.close();}
});

test('an upload from a trainer that sends no start is unchanged',async()=>{
  // The released beta.37 trainer: no start fields, ghost bytes only.
  const {env,store}=await environment(),h=harness(env);
  try{
    const fixture=upload(),id=await publish(h,fixture);
    const [key,object]=[...env.GHOSTS.objects][0];
    assert.deepEqual(Buffer.from(object),fixture.bytes);
    assert.deepEqual(env.GHOSTS.metadata.get(key),{encoded_sha256:fixture.body.encoded_sha256});
    assert.deepEqual(await row(store,'SELECT start_sha256,start_bytes FROM assets'),{start_sha256:null,start_bytes:0});
    assert.equal((await used(store)).bytes_used,fixture.bytes.length);
    const asset=await h.call('/v1/recordings/'+id+'/asset',{user:'bob'});
    assert.deepEqual(await bytesOf(asset.response),fixture.bytes);
    assert.equal(asset.response.headers.get('ghost-start-sha256'),null);
    assert.equal(asset.response.headers.get('ghost-start-bytes'),null);
    // The asset cache key is the one this Worker always used: no range.
    assert.deepEqual(h.props,[{key,encoded_sha256:fixture.body.encoded_sha256}]);
    const start=await h.call('/v1/recordings/'+id+'/start',{user:'bob'});
    assert.equal(start.response.status,404);assert.equal(start.json.error.code,'start_missing');
    // Unknown admission fields still fail, so a typo cannot pass as a start.
    assert.equal((await h.call('/v1/uploads',{method:'POST',body:{...upload(nativeClip({marker:2})).body,start:'x'}})).response.status,400);
  }finally{env.DB.close();}
});

test('the public config advertises the start cap a trainer checks before sending one',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    const value=(await h.call('/v1/config')).json;
    assert.equal(value.limits.start_bytes,64*1024);
    assert.equal(LIMITS.start_bytes,64*1024);
  }finally{env.DB.close();}
});

// Griffin, 2026-11-08: "Sure, update privacy policy". The Worker that takes
// starts serves a new privacy version; an account on the old one is held at the
// consent step until it accepts once. Trainers (beta.37 and later) rely on this.
test('a new privacy version holds an account at the consent step until it accepts once',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    env.PRIVACY_VERSION='test-2';
    const config=(await h.call('/v1/config')).json;
    assert.equal(config.privacy_version,'test-2');assert.equal(config.terms_version,'test-1');
    const before=await h.call('/v1/account');
    assert.equal(before.response.status,200);assert.equal(before.json.status,'active');assert.equal(before.json.privacy_version,'test-1');
    const fixture=withStart(upload());
    for(const [path,options] of [['/v1/uploads',{method:'POST',body:fixture.body}],['/v1/catalog',{}]]){
      const refused=await h.call(path,options);
      assert.equal(refused.response.status,403,path);assert.equal(refused.json.error.code,'consent_required');
    }
    assert.deepEqual(await used(store),{bytes_used:0,bytes_reserved:0});
    const profile={name:'Alice',sharing:true,terms_version:'test-1',privacy_version:'test-1',sheet_runner:null};
    assert.equal((await h.call('/v1/account',{method:'PUT',body:profile})).response.status,400);
    const accepted=await h.call('/v1/account',{method:'PUT',body:{...profile,privacy_version:'test-2'}});
    assert.equal(accepted.response.status,200);assert.equal(accepted.json.privacy_version,'test-2');assert.equal(accepted.json.sharing,true);
    const id=await publish(h,fixture);
    assert.deepEqual(await bytesOf((await h.call('/v1/recordings/'+id+'/start')).response),fixture.start);
    // Another player still on the old version receives neither the ghost nor its start.
    for(const part of ['/asset','/start'])
      assert.equal((await h.call('/v1/recordings/'+id+part,{user:'bob'})).json.error.code,'consent_required',part);
  }finally{env.DB.close();}
});

test('the privacy notice says what a ghost start holds, its size, who receives it and that it is deleted with its ghost',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    const page=await (await h.call('/policies')).response.text();
    assert(page.includes('<p id="ghost-start">'));
    // The paragraph sits inside the privacy section, under its version.
    assert(page.indexOf('<section id="privacy">')<page.indexOf('<p id="ghost-start">')&&page.indexOf('<p id="ghost-start">')<page.indexOf('<section id="progress">'));
    const paragraph=page.slice(page.indexOf('<p id="ghost-start">')).split('</p>')[0];
    for(const words of ["a copy of the game's memory at the moment the ghost's run begins","your save file's star flags",
      'the settings of your practice ROM','about 21 KB and never more than 64 KB',
      'The players who can download a ghost can also download its start',
      'when your account is deleted, the start is removed together with the ghost'])
      assert(paragraph.includes(words),words);
  }finally{env.DB.close();}
});

test('the cap and the hash refuse a start before any storage, and refund its reservation',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const fixture=withStart(upload());
    const admit=body=>h.call('/v1/uploads',{method:'POST',body});
    const over=await admit({...fixture.body,start_bytes:LIMITS.start_bytes+1});
    assert.equal(over.response.status,413);assert.equal(over.json.error.code,'start_limit');
    for(const body of [{...fixture.body,start_bytes:undefined},{...fixture.body,start_sha256:undefined},
      {...fixture.body,start_bytes:0},{...fixture.body,start_bytes:1.5},{...fixture.body,start_sha256:'A'.repeat(64)},
      {...fixture.body,start_sha256:null}])
      assert.equal((await admit(JSON.parse(JSON.stringify(body)))).response.status,400,JSON.stringify(body));
    assert.equal((await admit({...fixture.body,start_bytes:LIMITS.start_bytes})).json.disposition,'upload');
    await store.sql("UPDATE grants SET state='failed'").run();
    await store.sql('UPDATE accounts SET pending=0,bytes_reserved=0').run();await store.sql('UPDATE storage_budget SET bytes_reserved=0').run();
    const send=async bytes=>{
      const admission=await admit(fixture.body);assert.equal(admission.json.disposition,'upload');
      return h.call('/v1/uploads/'+admission.json.upload_id,{method:'PUT',bytes});
    };
    const tampered=Buffer.from(fixture.start);tampered[0]^=1;
    const wrong=await send(Buffer.concat([fixture.bytes,tampered]));
    assert.equal(wrong.response.status,422);assert.equal(wrong.json.error.code,'start_mismatch');
    const missing=await send(fixture.bytes);
    assert.equal(missing.response.status,400);assert.equal(missing.json.error.code,'body_length');
    assert.equal(env.GHOSTS.objects.size,0);assert.equal(env.GHOSTS.multipart.size,0);
    assert.deepEqual(await used(store),{bytes_used:0,bytes_reserved:0});
    assert.deepEqual(await row(store,'SELECT pending,bytes_reserved FROM accounts WHERE uid=?',alice),{pending:0,bytes_reserved:0});
  }finally{env.DB.close();}
});

test('the start counts in every byte budget: reservation, daily upload, retained and global storage',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const fixture=withStart(upload()),total=fixture.bytes.length+fixture.start.length;
    const admission=await h.call('/v1/uploads',{method:'POST',body:fixture.body});
    assert.deepEqual(await used(store),{bytes_used:0,bytes_reserved:total});
    assert.equal((await store.one('SELECT bytes_reserved FROM accounts WHERE uid=?',alice)).bytes_reserved,total);
    // The start shares the ghost's multipart write, so it adds bytes, not R2 operations.
    assert.deepEqual(await row(store,"SELECT uploaded,class_a FROM quotas WHERE owner=?",alice),{uploaded:total,class_a:3});
    await h.call('/v1/uploads/'+admission.json.upload_id,{method:'PUT',bytes:Buffer.concat([fixture.bytes,fixture.start])});
    assert.deepEqual(await used(store),{bytes_used:total,bytes_reserved:0});
    assert.deepEqual(await row(store,'SELECT bytes_used,bytes_reserved FROM accounts WHERE uid=?',alice),{bytes_used:total,bytes_reserved:0});
    // A ghost that would fit alone is refused when its start would not fit.
    const next=withStart(upload(nativeClip({marker:2,count:90})));
    for(const [table,limit] of [['accounts',LIMITS.retained_bytes],['storage_budget',LIMITS.global_bytes]]){
      await store.sql('UPDATE '+table+' SET bytes_used=?'+(table==='accounts'?' WHERE uid=?':''),limit-next.bytes.length,...(table==='accounts'?[alice]:[])).run();
      const refused=await h.call('/v1/uploads',{method:'POST',body:next.body});
      assert.equal(refused.response.status,429,table);assert.equal(refused.json.error.code,'storage_limit');
      await store.sql('UPDATE '+table+' SET bytes_used=?'+(table==='accounts'?' WHERE uid=?':''),total,...(table==='accounts'?[alice]:[])).run();
    }
    assert.equal((await h.call('/v1/uploads',{method:'POST',body:next.body})).json.disposition,'upload');
  }finally{env.DB.close();}
});

test('a replayed admission keeps its start, and a changed start under the same key conflicts',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    const fixture=withStart(upload()),key=crypto.randomUUID(),headers={'idempotency-key':key};
    const first=await h.call('/v1/uploads',{method:'POST',body:fixture.body,headers});
    const replay=await h.call('/v1/uploads',{method:'POST',body:fixture.body,headers});
    assert.equal(replay.json.upload_id,first.json.upload_id);
    const {start_sha256:_sha,start_bytes,...startless}=fixture.body;
    assert.equal((await h.call('/v1/uploads',{method:'POST',body:startless,headers})).response.status,409);
    assert.equal((await h.call('/v1/uploads',{method:'POST',body:{...fixture.body,start_bytes:start_bytes-1},headers})).response.status,409);
  }finally{env.DB.close();}
});

// Every way a ghost leaves the service ends at reconciliation, which deletes its
// one object. The start shares that key, so it goes with the ghost every time.
const deletions={
  withdrawal:async h=>assert.equal((await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}})).response.status,200),
  'account and identity deletion':async(h,env)=>{
    assert.equal((await h.call('/v1/account/delete',{method:'POST',body:{confirmation:'DELETE MY ACCOUNT'}})).json.identity_deletion,'pending');
    assert.equal((await deleteIdentities(env,{deleteIdentity:async()=>({error:null})})).complete,1);
  },
  retraction:async(h,_env,fixture)=>assert.equal((await h.call('/v1/recordings/retract',{method:'POST',body:{raw_sha256:fixture.body.raw_sha256,keep_strategy:null}})).json.retracted,1),
  'a faster ghost replacing it':async h=>{await publish(h,withStart(upload(nativeClip({marker:2,count:90}))));},
  'a catalog change retiring its target':async(_h,env)=>{
    const entries=[{...structuredClone(catalog.entries[0]),target:{kind:'star',entity_key:'2-0',name:'Other star'}}];
    assert.equal((await regrade(env,{model:{v:1,generation:digest(canonical(entries)),entries}})).complete,true);
  },
};
for(const [path,remove] of Object.entries(deletions))
test('the start is deleted with its ghost after '+path,async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const fixture=withStart(upload(nativeClip({count:100}))),id=await publish(h,fixture);
    const {asset_key:key}=await store.one('SELECT asset_key FROM recordings WHERE id=?',id);
    await remove(h,env,fixture);
    await store.sql('UPDATE assets SET created_at=0').run();
    await reconcile(env);
    assert.equal(env.GHOSTS.objects.has(key),false);
    assert.equal(await store.one('SELECT key FROM assets WHERE key=?',key),null);
    // What stays charged is exactly what stays stored.
    const left=await store.one('SELECT COALESCE(SUM(encoded_bytes+start_bytes),0) AS n FROM assets');
    const stored=[...env.GHOSTS.objects.values()].reduce((n,b)=>n+b.length,0);
    assert.equal(left.n,stored);
    assert.equal((await used(store)).bytes_used,stored);
    assert.equal((await store.one('SELECT bytes_used FROM accounts WHERE uid=?',alice)).bytes_used,stored);
  }finally{env.DB.close();}
});

test('an interrupted upload recovers its stored start or refunds it',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    // The request died after R2 completed the object but before D1 recorded it.
    const interrupted=async fixture=>{
      const admission=await h.call('/v1/uploads',{method:'POST',body:fixture.body});
      await store.sql("UPDATE grants SET state='receiving',expires_at=0 WHERE id=?",admission.json.upload_id).run();
      return store.one('SELECT * FROM grants WHERE id=?',admission.json.upload_id);
    };
    const whole=withStart(upload()),g=await interrupted(whole);
    env.GHOSTS.objects.set(g.asset_key,Buffer.concat([whole.bytes,whole.start]));
    env.GHOSTS.metadata.set(g.asset_key,{encoded_sha256:g.encoded_sha256,start_sha256:g.start_sha256});
    await reconcile(env);
    const published=await store.one("SELECT recording_id,disposition FROM grants WHERE id=?",g.id);
    assert.equal(published.disposition,'published');
    assert.deepEqual(await bytesOf((await h.call('/v1/recordings/'+published.recording_id+'/start',{user:'bob'})).response),whole.start);
    // An object whose start disagrees with its grant stays charged and retries.
    const odd=withStart(upload(nativeClip({marker:2,count:90}))),bad=await interrupted(odd);
    env.GHOSTS.objects.set(bad.asset_key,Buffer.concat([odd.bytes,odd.start]));
    env.GHOSTS.metadata.set(bad.asset_key,{encoded_sha256:bad.encoded_sha256,start_sha256:'0'.repeat(64)});
    assert.equal((await reconcile(env)).retried,1);
    assert.equal((await store.one('SELECT state FROM grants WHERE id=?',bad.id)).state,'recovering');
    // Nothing reached R2: the whole reservation, start included, comes back.
    env.GHOSTS.objects.delete(bad.asset_key);
    await store.sql('UPDATE grants SET retry_at=0').run();
    const before=(await used(store)).bytes_reserved;
    await reconcile(env);
    assert.equal((await store.one('SELECT state FROM grants WHERE id=?',bad.id)).state,'failed');
    assert.equal(before-(await used(store)).bytes_reserved,odd.bytes.length+odd.start.length);
    assert.equal((await used(store)).bytes_reserved,0);
  }finally{env.DB.close();}
});
