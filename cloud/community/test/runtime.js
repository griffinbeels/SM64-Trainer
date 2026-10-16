// Real local workerd/D1/R2 fixture. No network providers, credentials, or remote storage.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Miniflare,convertV4MiniflareOptions,Response as MFResponse} from 'miniflare';
import {generateKeyPair,exportJWK,SignJWT} from 'jose';
import {alice,nativeClip,upload} from './fixtures.js';
import {Store} from '../src/store.js';
import {config} from '../src/config.js';
const model=JSON.parse(readFileSync(new URL('../catalog.json',import.meta.url),'utf8'));
const entry=model.entries.find(e=>e.target.kind==='star'&&e.target.entity_key==='1-0'&&e.clock==='igt'&&e.game_version==='us');
assert(entry);
const issuer='https://fixture.supabase.co/auth/v1',clientID='client-fixture';
const publicSignup=process.argv.includes('--public-beta');
const {publicKey,privateKey}=await generateKeyPair('ES256'),jwk={...await exportJWK(publicKey),kid:'runtime-fixture',alg:'ES256'};
const token=await new SignJWT({role:'authenticated',client_id:clientID}).setProtectedHeader({alg:'ES256',kid:jwk.kid}).setIssuer(issuer).setAudience('authenticated').setSubject(alice).setIssuedAt().setExpirationTime('5m').sign(privateKey);
const bindings={SERVICE_ENABLED:'true',SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public-fixture-only',OAUTH_CLIENT_ID:clientID,NATIVE_CALLBACK:'http://127.0.0.1:8072/api/community/auth/callback',TERMS_VERSION:'test-1',PRIVACY_VERSION:'test-1',OPERATOR_NAME:'Fixture operator',OPERATOR_CONTACT:'fixture@example.invalid',CURSOR_SECRET:'fixture-only-not-real-01234567890123456789',CATALOG_GENERATION:model.generation};
bindings.SUPABASE_SECRET_KEY='fixture-service-only-not-real';
bindings.PUBLIC_BETA_SIGNUP=String(publicSignup);
let jwksCalls=0;
const options=convertV4MiniflareOptions({name:'community-runtime-fixture',modules:true,scriptPath:'.wrangler/dry-run/worker.js',compatibilityDate:'2026-10-08',compatibilityFlags:['nodejs_compat'],host:'127.0.0.1',port:0,inspectorPort:0,cf:false,bindings,d1Databases:{DB:'fixture-d1'},r2Buckets:{GHOSTS:'fixture-r2'},outboundService:request=>{assert.equal(request.url,issuer+'/.well-known/jwks.json');jwksCalls++;return MFResponse.json({keys:[jwk]});}});
options.telemetry={enabled:false};
options.workers[0].config.cache={enabled:false};
options.workers[0].config.exports={default:{type:'worker',cache:{enabled:false}},MetadataCache:{type:'worker',cache:{enabled:true}},AssetCache:{type:'worker',cache:{enabled:true}}};
const mf=new Miniflare(options);
try{
  await mf.ready;
  const db=await mf.getD1Database('DB');
  for(const migration of ['0001_community.sql','0002_friend_beta.sql','0003_auth_entry_budget.sql','0004_reusable_beta_invitation.sql'])for(const sql of readFileSync(new URL('../migrations/'+migration,import.meta.url),'utf8').split(';').map(s=>s.trim()).filter(Boolean))await db.prepare(sql).run();
  await db.prepare('UPDATE settings SET generation=? WHERE id=1').bind(model.generation).run();
  await db.prepare("INSERT INTO accounts(uid,issuer,subject,invited,status,name,name_key,sharing,terms_version,privacy_version,created_at,updated_at) VALUES(?,?,?,1,'active','Runtime','runtime',1,'test-1','test-1',0,0)").bind(alice,issuer,alice).run();
  async function call(path,{method='GET',body,bytes,bearer=token,key=crypto.randomUUID(),status=200}={}){
    const response=await mf.dispatchFetch('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+bearer,...(body?{'content-type':'application/json','idempotency-key':key}:{}),...(bytes?{'content-type':'application/octet-stream'}:{})},body:body?JSON.stringify(body):bytes});
    const json=response.headers.get('content-type')?.includes('json')?await response.json():null;
    assert.equal(response.status,status,JSON.stringify(json));return {response,json};
  }
  assert.equal((await call('/v1/account')).json.uid,alice);
  const f=upload(nativeClip());f.body={...f.body,target:entry.target,strategy:entry.strategies[0].name,generation:model.generation};
  const admitted=await call('/v1/uploads',{method:'POST',body:f.body});
  const published=await call('/v1/uploads/'+admitted.json.upload_id,{method:'PUT',bytes:f.bytes});
  assert.equal(published.json.disposition,'published');
  const id=published.json.recording_id;
  assert.equal((await call('/v1/recordings/'+id)).json.player_id,alice);
  const asset=await call('/v1/recordings/'+id+'/asset');assert.deepEqual(Buffer.from(await asset.response.arrayBuffer()),f.bytes);
  const objects=await mf.getR2Bucket('GHOSTS');assert.equal((await objects.list()).objects.length,1);
  const quota=await db.prepare("SELECT ops FROM quotas WHERE owner='*'").first();assert.equal(quota.ops,6);
  await call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}});
  const denied=await mf.dispatchFetch('https://community.example.invalid/v1/recordings/'+id+'/asset',{headers:{authorization:'Bearer '+token}});assert.equal(denied.status,404);
  const fresh='30000000-0000-4000-8000-000000000001';
  await db.prepare("UPDATE quotas SET ops=ops_limit WHERE owner='*'").run();
  const store=new Store(db,config(bindings));
  await assert.rejects(store.ticket(fresh),e=>e.code==='budget_exhausted');
  assert.equal(await db.prepare('SELECT owner FROM quotas WHERE owner=?').bind(fresh).first(),null);
  const freshToken=await new SignJWT({role:'authenticated',client_id:clientID}).setProtectedHeader({alg:'ES256',kid:jwk.kid}).setIssuer(issuer).setAudience('authenticated').setSubject(fresh).setIssuedAt().setExpirationTime('5m').sign(privateKey);
  const unknown=await mf.dispatchFetch('https://community.example.invalid/v1/account',{headers:{authorization:'Bearer '+freshToken}});
  assert.equal(unknown.status,200);assert.equal((await unknown.json()).status,publicSignup?'new':'not_invited');
  const unknownRoute=await mf.dispatchFetch('https://community.example.invalid/v1/catalog',{headers:{authorization:'Bearer '+freshToken}});
  assert.equal(unknownRoute.status,403);
  assert.equal(await db.prepare('SELECT uid FROM accounts WHERE uid=?').bind(fresh).first(),null);
  assert.equal(await db.prepare('SELECT owner FROM quotas WHERE owner=?').bind(fresh).first(),null);
  if(publicSignup){
    const profile={name:'Public Player',sharing:true,terms_version:'test-1',privacy_version:'test-1',sheet_runner:null};
    const user=(path,options={})=>call(path,{bearer:freshToken,...options});
    for(let i=0;i<4;i++)assert.equal((await user('/v1/account')).json.status,'new');
    assert.equal((await user('/v1/names/check?name=Public%20Player')).json.available,true);
    for(const body of [{...profile,terms_version:'old'},{...profile,name:'admin'}])await user('/v1/account',{method:'PUT',body,status:400});
    assert.equal(await db.prepare('SELECT owner FROM quotas WHERE owner=?').bind(fresh).first(),null);
    // The already exhausted global quota must roll back the inserted account,
    // owner ticket and receipt under real D1 CHECK error formatting.
    const exhausted=await user('/v1/account',{method:'PUT',body:profile,status:429});assert.equal(exhausted.json.error.code,'budget_exhausted');
    assert.equal(await db.prepare('SELECT uid FROM accounts WHERE uid=?').bind(fresh).first(),null);
    assert.equal(await db.prepare('SELECT owner FROM quotas WHERE owner=?').bind(fresh).first(),null);
    await db.prepare('UPDATE quotas SET ops=0').run();
    await db.prepare('UPDATE beta_admission SET max_members=1').run();
    await user('/v1/account',{method:'PUT',body:profile,status:409});
    assert.equal(await db.prepare('SELECT uid FROM accounts WHERE uid=?').bind(fresh).first(),null);
    await db.prepare('UPDATE beta_admission SET max_members=2').run();
    await user('/v1/account',{method:'PUT',body:{...profile,name:'Ｒｕｎｔｉｍｅ'},status:409});
    const key=crypto.randomUUID(),signup=await user('/v1/account',{method:'PUT',body:profile,key});assert.equal(signup.json.status,'active');
    const saved=await db.prepare('SELECT * FROM accounts WHERE uid=?').bind(fresh).first();
    assert.equal((await user('/v1/account',{method:'PUT',body:profile,key})).json.uid,fresh);
    assert.deepEqual(await db.prepare('SELECT * FROM accounts WHERE uid=?').bind(fresh).first(),saved);
    assert.equal((await user('/v1/catalog')).json.generation,model.generation);
    assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM accounts').first()).n,2);
    assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM beta_invitations').first()).n,0);
    await db.prepare('UPDATE accounts SET invited=0 WHERE uid=?').bind(fresh).run();
    assert.equal((await user('/v1/account')).json.status,'not_invited');
    await user('/v1/account',{method:'PUT',body:profile,status:403});
    for(const status of ['suspended','deleted']){
      await db.prepare('UPDATE accounts SET invited=1,status=? WHERE uid=?').bind(status,fresh).run();
      assert.equal((await user('/v1/account')).json.status,status);
      await user('/v1/account',{method:'PUT',body:profile,status:403});
    }
    for(const claims of [{client_id:'unregistered'},{iss:'https://other.invalid/auth/v1'}]){
      const invalid=await new SignJWT({role:'authenticated',client_id:clientID,iss:issuer,...claims}).setProtectedHeader({alg:'ES256',kid:jwk.kid}).setAudience('authenticated').setSubject(fresh).setIssuedAt().setExpirationTime('5m').sign(privateKey);
      await call('/v1/account',{bearer:invalid,status:401});
    }
    assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM guards').first()).n,0);
    console.log('Public beta workerd: invitation-free new/profile flow, no rows from reads, policy/name/cap controls, atomic budget rollback, repeated signup, revoked/suspended/deleted refusal and native JWT issuer/client enforcement passed.');
  }
  assert.equal(jwksCalls,1);
  console.log('Local workerd: real jose fixed JWKS, D1 rollback/no retained unknown-identity rows, GHC1 zlib admission, R2 multipart, private exported asset gate and withdrawal passed.');
}finally{await mf.dispose();}
