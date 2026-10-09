// Real local workerd/D1/R2 fixture. No network providers, credentials, or remote storage.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {Miniflare,convertV4MiniflareOptions,Response as MFResponse} from 'miniflare';
import {generateKeyPair,exportJWK,SignJWT} from 'jose';
import {alice,nativeClip,upload} from './fixtures.js';
const model=JSON.parse(readFileSync(new URL('../catalog.json',import.meta.url),'utf8'));
const entry=model.entries.find(e=>e.target.kind==='star'&&e.target.entity_key==='1-0'&&e.clock==='igt'&&e.game_version==='us');
assert(entry);
const issuer='https://fixture.supabase.co/auth/v1',clientID='client-fixture';
const {publicKey,privateKey}=await generateKeyPair('ES256'),jwk={...await exportJWK(publicKey),kid:'runtime-fixture',alg:'ES256'};
const token=await new SignJWT({role:'authenticated',client_id:clientID}).setProtectedHeader({alg:'ES256',kid:jwk.kid}).setIssuer(issuer).setAudience('authenticated').setSubject(alice).setIssuedAt().setExpirationTime('5m').sign(privateKey);
const bindings={SERVICE_ENABLED:'true',SUPABASE_URL:'https://fixture.supabase.co',SUPABASE_PUBLISHABLE_KEY:'public-fixture-only',OAUTH_CLIENT_ID:clientID,NATIVE_CALLBACK:'http://127.0.0.1:8072/api/community/auth/callback',TERMS_VERSION:'test-1',PRIVACY_VERSION:'test-1',OPERATOR_NAME:'Fixture operator',OPERATOR_CONTACT:'fixture@example.invalid',POLICY_URL:'https://community.example.invalid/policies',CURSOR_SECRET:'fixture-only-not-real-01234567890123456789',CATALOG_GENERATION:model.generation};
bindings.SUPABASE_SECRET_KEY='fixture-service-only-not-real';
let jwksCalls=0;
const options=convertV4MiniflareOptions({name:'community-runtime-fixture',modules:true,scriptPath:'.wrangler/dry-run/worker.js',compatibilityDate:'2026-10-08',compatibilityFlags:['nodejs_compat'],host:'127.0.0.1',port:0,inspectorPort:0,cf:false,bindings,d1Databases:{DB:'fixture-d1'},r2Buckets:{GHOSTS:'fixture-r2'},outboundService:request=>{assert.equal(request.url,issuer+'/.well-known/jwks.json');jwksCalls++;return MFResponse.json({keys:[jwk]});}});
options.telemetry={enabled:false};
options.workers[0].config.cache={enabled:false};
options.workers[0].config.exports={default:{type:'worker',cache:{enabled:false}},MetadataCache:{type:'worker',cache:{enabled:true}},AssetCache:{type:'worker',cache:{enabled:true}}};
const mf=new Miniflare(options);
try{
  await mf.ready;
  const db=await mf.getD1Database('DB');
  for(const sql of readFileSync(new URL('../migrations/0001_community.sql',import.meta.url),'utf8').split(';').map(s=>s.trim()).filter(Boolean))await db.prepare(sql).run();
  await db.prepare('UPDATE settings SET generation=? WHERE id=1').bind(model.generation).run();
  await db.prepare("INSERT INTO accounts(uid,issuer,subject,invited,status,name,name_key,sharing,terms_version,privacy_version,created_at,updated_at) VALUES(?,?,?,1,'active','Runtime','runtime',1,'test-1','test-1',0,0)").bind(alice,issuer,alice).run();
  async function call(path,{method='GET',body,bytes}={}){
    const response=await mf.dispatchFetch('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+token,...(body?{'content-type':'application/json','idempotency-key':crypto.randomUUID()}:{}),...(bytes?{'content-type':'application/octet-stream'}:{})},body:body?JSON.stringify(body):bytes});
    const json=response.headers.get('content-type')?.includes('json')?await response.json():null;
    assert.equal(response.status,200,JSON.stringify(json));return {response,json};
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
  assert.equal(jwksCalls,1);
  console.log('Local workerd: real jose fixed JWKS, D1 migration/guards/quotas, GHC1 zlib admission, R2 multipart, private exported asset gate and withdrawal passed.');
}finally{await mf.dispose();}
