import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createService} from '../src/service.js';
import {config} from '../src/config.js';
import {canonical,sha256} from '../src/errors.js';
import {snapshot as validateSnapshot} from '../src/player-validation.js';
import {environment,alice,bob,catalog,digest,nativeClip,upload} from './fixtures.js';

const wire=JSON.parse(readFileSync(new URL('./player-wire-fixture.json',import.meta.url),'utf8'));
const clone=value=>structuredClone(value),controlPath='/v2/account/progress/control',snapshotPath='/v2/account/progress/snapshot',profilePath='/v2/account/player-profile';
const publicPath=uid=>'/v2/players/'+uid;
const source={installation_id:wire.enable.publisher.installation_id,profile_id:wire.enable.publisher.profile_id};
const other={installation_id:'40000000-0000-4000-8000-000000000001',profile_id:'50000000-0000-4000-8000-000000000001',label:'Other profile'};
function harness(env){
  const errors=[],service=createService({auth:async r=>({issuer:config(env).issuer,sub:r.headers.get('authorization').slice(7)}),catalog:async()=>catalog,onError:e=>errors.push(e)});
  return {errors,async call(path,{uid=alice,method='GET',body,key=crypto.randomUUID(),raw,bytes}={}){
    const response=await service.fetch(new Request('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+uid,...(body||raw?{'content-type':'application/json','idempotency-key':key}:{}),...(bytes?{'content-type':'application/octet-stream'}:{})},body:bytes??raw??(body?JSON.stringify(body):undefined)}),env,{});
    return {status:response.status,json:await response.json()};
  }};
}
const state=h=>h.call('/v2/account/progress').then(r=>r.json);
async function enable(h){const r=await h.call(controlPath,{method:'PUT',body:clone(wire.enable)});assert.equal(r.status,200,JSON.stringify(r));return r.json;}
async function bodyFor(h,content=clone(wire.snapshot.content),publisher=source){const s=await state(h);return {...clone(wire.snapshot),expected_revision:s.revision,consent_revision:s.consent_revision,publisher,content,content_hash:await sha256(canonical(content))};}
async function publish(h,content){const body=await bodyFor(h,content),r=await h.call(snapshotPath,{method:'PUT',body});assert.equal(r.status,200,JSON.stringify(r));return {body,state:r.json};}
async function control(h,action,extra={}){const s=await state(h),r=await h.call(controlPath,{method:'PUT',body:{expected_revision:s.revision,action,publisher:null,notice_version:null,...extra}});assert.equal(r.status,200,JSON.stringify(r));return r.json;}
async function seedRecording(s,index){
  const id=digest('player-inventory-'+index),target={kind:'star',entity_key:Math.floor(index/7)+1+'-'+index%7,name:'Fixture '+index},key='star:'+target.entity_key,asset=alice+'/'+id;
  await s.sql("INSERT INTO assets(key,uid,raw_sha256,encoded_sha256,encoded_bytes,state,created_at) VALUES(?,?,?,?,200,'stored',0)",asset,alice,id,id).run();
  await s.sql('INSERT INTO recordings(id,uid,asset_key,target_key,target_json,clock,rom,strategy,frames,raw_sha256,raw_bytes,encoded_sha256,encoded_bytes,generation,step,overall_step,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',id,alice,asset,key,canonical(target),'igt','us','Standard',100+index,id,130000,id,200,catalog.generation,1,1).run();
  await s.sql('INSERT INTO winners VALUES(?,?,?,?,?,?,?,?,?)',alice,key,'igt','us','Standard',catalog.generation,1,id,100+index).run();return {id,asset};
}
async function uploadGhost(h,uid,frames=90){
  const data=upload(nativeClip({count:frames})),grant=await h.call('/v1/uploads',{uid,method:'POST',body:data.body});assert.equal(grant.status,200,JSON.stringify(grant));
  const result=await h.call('/v1/uploads/'+grant.json.upload_id,{uid,method:'PUT',bytes:data.bytes});assert.equal(result.json.disposition,'published',JSON.stringify(result));return {id:result.json.recording_id,raw_sha256:data.body.raw_sha256};
}

test('v2 owner reads retain no rows, progress consent is separate and v1 account stays strict',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const before=(await h.call('/v1/account')).json;
    assert.deepEqual(await state(h),{v:2,uid:alice,revision:0,consent_revision:0,enabled:false,visible:false,publisher:null,notice_version:'progress-1',content_hash:null,captured_utc:null,synced_utc:null,limits:{rows:4096,bytes:1048576}});
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM progress_publications')).n,0);
    await enable(h);assert.deepEqual((await h.call('/v1/account')).json,before);
    const wrong=await h.call(controlPath,{method:'PUT',body:{...wire.enable,expected_revision:1,notice_version:'old'}});assert.equal(wrong.status,403);assert.equal(wrong.json.error.code,'progress_consent_required');
  }finally{env.DB.close();}
});
test('canonical fixture publishes raw/effective regions and nullable source facts without private publisher IDs',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    await enable(h);assert.equal((await h.call(snapshotPath,{method:'PUT',body:clone(wire.snapshot)})).status,200);
    const p=await h.call(publicPath(alice),{uid:bob});assert.equal(p.json.claimed_account,true);assert.equal(p.json.progress.available,true);assert.equal(p.json.sheet_runner,null);
    const r=await h.call(publicPath(alice)+'/progress',{uid:bob});assert.deepEqual(r.json.content,wire.snapshot.content);assert.equal(r.json.revision,2);
    assert(!JSON.stringify(r.json).includes(source.installation_id));assert(!JSON.stringify(r.json).includes(source.profile_id));assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('slower save, Undo, reclassification and deletion replace the whole current state',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    await enable(h);await publish(h);const slower=clone(wire.snapshot.content);slower.rows[0].frames=500;await publish(h,slower);
    assert.equal((await h.call(publicPath(alice)+'/progress')).json.content.rows[0].frames,500);
    await publish(h,wire.snapshot.content);assert.equal((await h.call(publicPath(alice)+'/progress')).json.content.rows[0].frames,397);
    slower.rows[0].strategy=null;slower.rows[0].achieved_utc=null;await publish(h,slower);assert.equal((await h.call(publicPath(alice)+'/progress')).json.content.rows[0].strategy,null);
    const empty={schema:1,default_rom:'jp',rows:[],coverage:{total_rows:0,shared_rows:0,withheld_rows:0,unsupported_rows:0,complete:true}};await publish(h,empty);assert.deepEqual((await h.call(publicPath(alice)+'/progress')).json.content,empty);
  }finally{env.DB.close();}
});
test('disable retains the prior public snapshot and rejects pending publication',async()=>{
  const {env}=await environment(),h=harness(env);
  try{await enable(h);await publish(h);const pending=await bodyFor(h),off=await control(h,'disable');assert.equal(off.enabled,false);assert.equal(off.visible,true);
    const denied=await h.call(snapshotPath,{method:'PUT',body:pending});assert.equal(denied.json.error.code,'progress_disabled');assert.equal((await h.call(publicPath(alice)+'/progress',{uid:bob})).status,200);
  }finally{env.DB.close();}
});
test('withdraw and re-enable retain the fence and cannot resurrect an old snapshot or request',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{await enable(h);await publish(h);const old=await bodyFor(h),withdrawn=await control(h,'withdraw');assert.equal(withdrawn.visible,false);assert.equal(withdrawn.content_hash,null);assert.equal((await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used,0);
    const next=await control(h,'enable',{publisher:wire.enable.publisher,notice_version:'progress-1'});assert(next.revision>withdrawn.revision);assert.equal(next.visible,false);
    assert.equal((await h.call(snapshotPath,{method:'PUT',body:old})).json.error.code,'revision_conflict');assert.equal((await h.call(publicPath(alice)+'/progress')).status,404);assert.equal((await publish(h)).state.visible,true);
  }finally{env.DB.close();}
});
test('one authoritative publisher requires a deliberate switch and old source cannot overwrite it',async()=>{
  const {env}=await environment(),h=harness(env);
  try{await enable(h);await publish(h);const old=await bodyFor(h);
    assert.equal((await h.call(controlPath,{method:'PUT',body:{...wire.enable,expected_revision:2,publisher:other}})).json.error.code,'publisher_conflict');
    const switched=await control(h,'switch',{publisher:other,notice_version:'progress-1'});assert.equal(switched.visible,false);assert.equal(switched.enabled,true);
    assert.equal((await h.call(snapshotPath,{method:'PUT',body:old})).json.error.code,'publisher_conflict');
    const body=await bodyFor(h,wire.snapshot.content,{installation_id:other.installation_id,profile_id:other.profile_id});assert.equal((await h.call(snapshotPath,{method:'PUT',body})).status,200);
  }finally{env.DB.close();}
});
test('lost ACK retries return current state and a changed payload cannot reuse a mutation key',async()=>{
  const {env}=await environment(),h=harness(env),key=crypto.randomUUID();
  try{await enable(h);const body=clone(wire.snapshot);assert.equal((await h.call(snapshotPath,{method:'PUT',body,key})).status,200);assert.equal((await state(h)).content_hash,body.content_hash);await control(h,'withdraw');
    const replay=await h.call(snapshotPath,{method:'PUT',body,key});assert.equal(replay.status,200);assert.equal(replay.json.visible,false);assert.equal(replay.json.revision,3);
    assert.equal((await h.call(snapshotPath,{method:'PUT',body:{...body,captured_utc:'2026-10-19T12:00:07Z'},key})).json.error.code,'idempotency_conflict');
  }finally{env.DB.close();}
});
test('same-key concurrency commits once and two competing snapshots cannot win one revision',async()=>{
  const {env}=await environment(),h=harness(env),key=crypto.randomUUID();
  try{await enable(h);const body=clone(wire.snapshot),same=await Promise.all([h.call(snapshotPath,{method:'PUT',body,key}),h.call(snapshotPath,{method:'PUT',body,key})]);assert(same.every(r=>r.status===200),JSON.stringify(same));assert.equal((await state(h)).revision,2);
    const one=await bodyFor(h),two=clone(one);one.content.rows[0].frames=410;two.content.rows[0].frames=420;one.content_hash=await sha256(canonical(one.content));two.content_hash=await sha256(canonical(two.content));
    const competing=await Promise.all([h.call(snapshotPath,{method:'PUT',body:one}),h.call(snapshotPath,{method:'PUT',body:two})]);assert.deepEqual(competing.map(r=>r.status).sort(),[200,409]);assert.equal((await state(h)).revision,3);
  }finally{env.DB.close();}
});
test('same content hash avoids body rewrite and does not inflate revision or capture freshness',async()=>{
  const {env}=await environment(),h=harness(env);
  try{await enable(h);const first=await publish(h),body=await bodyFor(h);body.captured_utc='2026-10-19T12:00:07Z';const result=await h.call(snapshotPath,{method:'PUT',body});assert.equal(result.status,200);assert.equal(result.json.revision,first.state.revision);assert.equal(result.json.captured_utc,first.state.captured_utc);
  }finally{env.DB.close();}
});
test('UUID links survive rename and a claimed Sheet name is hidden until separately verified',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{await store.sql('UPDATE accounts SET sheet_runner=?,sheet_verified=0 WHERE uid=?','Famous runner',alice).run();assert.equal((await h.call(publicPath(alice))).json.sheet_runner,null);
    await h.call('/v1/account/name',{method:'PUT',body:{name:'NewAlice'}});assert.equal((await h.call(publicPath(alice),{uid:bob})).json.name,'NewAlice');assert.equal((await h.call(publicPath(bob))).json.uid,bob);
    await store.sql('UPDATE accounts SET sheet_verified=1 WHERE uid=?',alice).run();assert.equal((await h.call(publicPath(alice))).json.sheet_runner,'Famous runner');
  }finally{env.DB.close();}
});
test('bio and socials use independent CAS and bounded HTTPS links without credential-bearing URLs',async()=>{
  const {env}=await environment(),h=harness(env);
  try{assert.deepEqual((await h.call(profilePath)).json,{v:2,uid:alice,revision:0,bio:'',socials:[]});assert.equal((await h.call(profilePath,{method:'PUT',body:wire.player_profile})).json.revision,1);assert.equal((await h.call(publicPath(alice),{uid:bob})).json.bio,wire.player_profile.bio);assert.equal((await h.call(profilePath,{method:'PUT',body:wire.player_profile})).status,409);
    const named=new URL('https://www.twitch.tv/example');named.username='fixture-user';
    for(const url of ['javascript:alert(1)','http://twitch.tv/example','https://attacker.invalid/twitch',named.href])assert.equal((await h.call(profilePath,{method:'PUT',body:{expected_revision:1,bio:'',socials:[{kind:'twitch',url}]}})).status,400,url);
    assert.equal((await h.call(profilePath,{method:'PUT',body:{expected_revision:1,bio:'x'.repeat(1001),socials:[]}})).status,400);assert.equal((await state(h)).revision,0);
  }finally{env.DB.close();}
});
test('moderation hides pages and fences old requests through admission restoration; lapsed owners can remove/export',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{await enable(h);await publish(h);const old=await bodyFor(h);await store.sql('UPDATE accounts SET invited=0 WHERE uid=?',alice).run();assert.equal((await h.call(publicPath(alice),{uid:bob})).status,404);assert.equal((await h.call('/v2/account/player-export')).status,200);
    await store.sql('UPDATE accounts SET invited=1 WHERE uid=?',alice).run();assert.equal((await h.call(snapshotPath,{method:'PUT',body:old})).json.error.code,'progress_disabled');await control(h,'enable',{publisher:wire.enable.publisher,notice_version:'progress-1'});await publish(h);
    await store.sql("UPDATE accounts SET status='suspended' WHERE uid=?",alice).run();assert.equal((await h.call(publicPath(alice),{uid:bob})).status,404);assert.equal((await control(h,'withdraw')).visible,false);
    await store.sql("UPDATE accounts SET status='active' WHERE uid=?",alice).run();assert.equal((await h.call(publicPath(alice)+'/progress',{uid:bob})).status,404);
  }finally{env.DB.close();}
});
test('account deletion removes current payloads, refunds D1 bytes and retains monotonic tombstones',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{await enable(h);await publish(h);await h.call(profilePath,{method:'PUT',body:wire.player_profile});const r2=(await store.one('SELECT bytes_used FROM storage_budget')).bytes_used;
    assert.equal((await h.call('/v1/account/delete',{method:'POST',body:{confirmation:'DELETE MY ACCOUNT'}})).status,200);assert.equal((await h.call(publicPath(alice),{uid:bob})).status,404);assert.equal((await store.one('SELECT COUNT(*) AS n FROM progress_snapshots')).n,0);assert.equal((await store.one('SELECT COUNT(*) AS n FROM player_profiles')).n,0);
    assert.equal((await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used,0);assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,r2);const tombstone=await state(h);assert.equal(tombstone.visible,false);assert.equal(tombstone.enabled,false);assert(tombstone.revision>2);assert.equal((await h.call('/v2/account/player-export')).json.snapshot,null);
  }finally{env.DB.close();}
});
test('ghost withdrawal remains independent from opted-in progress',async()=>{
  const {env}=await environment(),h=harness(env);
  try{await enable(h);await publish(h);assert.equal((await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}})).status,200);assert.equal((await h.call(publicPath(alice)+'/progress',{uid:bob})).status,200);assert.equal((await state(h)).enabled,true);
  }finally{env.DB.close();}
});
test('unknown identities cannot retain profile, publisher, account or quota rows through v2 routes',async()=>{
  const {env,store}=await environment(),h=harness(env),uid='60000000-0000-4000-8000-000000000001';
  try{env.PUBLIC_BETA_SIGNUP='true';for(const path of ['/v2/account/progress',profilePath,'/v2/account/player-export',publicPath(alice),'/v2/players?prefix=A'])assert.equal((await h.call(path,{uid})).status,403);
    for(const [path,body] of [[controlPath,wire.enable],[profilePath,wire.player_profile],[snapshotPath,wire.snapshot]])assert.equal((await h.call(path,{uid,method:'PUT',body})).status,403);
    assert.equal(await store.one('SELECT * FROM accounts WHERE uid=?',uid),null);assert.equal(await store.one('SELECT * FROM quotas WHERE owner=?',uid),null);assert.equal((await store.one('SELECT COUNT(*) AS n FROM progress_publications')).n,0);
  }finally{env.DB.close();}
});
test('snapshot validation rejects malformed, duplicate, over-row and over-byte bodies without partial publication',async()=>{
  const {env}=await environment(),h=harness(env);
  try{await enable(h);for(const change of [b=>b.uid=bob,b=>b.content_hash='f'.repeat(64),b=>b.content.rows[0].save_order=0,b=>b.content.rows[0].game_version='eu',b=>b.content.rows[0].source_kind='verified',b=>b.content.coverage.complete=false,b=>b.content.rows[0].achieved_utc='yesterday',b=>b.content.extra=true]){const body=clone(wire.snapshot);change(body);assert.equal((await h.call(snapshotPath,{method:'PUT',body})).status,400);}
    const duplicate=clone(wire.snapshot);duplicate.content.rows.push({...clone(duplicate.content.rows[0]),save_order:2});duplicate.content.coverage.total_rows=2;duplicate.content.coverage.shared_rows=2;assert.equal((await h.call(snapshotPath,{method:'PUT',body:duplicate})).status,400);
    const rows=clone(wire.snapshot);rows.content.rows=Array(4097).fill(wire.snapshot.content.rows[0]);await assert.rejects(validateSnapshot(rows,alice),e=>e.status===413&&e.code==='snapshot_too_large');
    const large=await h.call(snapshotPath,{method:'PUT',raw:' '.repeat(1048577)});assert.equal(large.status,413);assert.equal(large.json.error.code,'snapshot_too_large');assert.equal((await state(h)).visible,false);
  }finally{env.DB.close();}
});
test('retained D1 byte budget atomically replaces/refunds and rejects capacity without mixing R2 costs',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{await enable(h);await publish(h);const larger=clone(wire.snapshot.content);larger.rows[0].target.name='Fixture longer target';await publish(h,larger);assert.equal((await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used,new TextEncoder().encode(canonical(larger)).length);
    await control(h,'withdraw');assert.equal((await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used,0);await control(h,'enable',{publisher:wire.enable.publisher,notice_version:'progress-1'});env.PROGRESS_STORAGE_BYTES='1048576';await store.sql('UPDATE progress_storage_budget SET bytes_used=1048576').run();
    const r=await h.call(snapshotPath,{method:'PUT',body:await bodyFor(h)});assert.equal(r.status,429,JSON.stringify(r));assert.equal(r.json.error.code,'storage_limit');assert.equal((await state(h)).visible,false);assert.equal((await store.one('SELECT COUNT(*) AS n FROM guards')).n,0);assert.equal((await store.one('SELECT bytes_used FROM storage_budget')).bytes_used,0);
  }finally{env.DB.close();}
});
test('player search uses indexed prefix pages and obeys per-viewer publisher restrictions',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{for(let i=0;i<45;i++){const uid='70000000-0000-4000-8000-'+String(i).padStart(12,'0');await store.sql("INSERT INTO accounts(uid,issuer,subject,invited,status,name,name_key,created_at,updated_at) VALUES(?,?,?,1,'active',?,?,0,0)",uid,config(env).issuer,uid,'Test '+String(i).padStart(3,'0'),'test '+String(i).padStart(3,'0')).run();}
    const seen=[];let path='/v2/players?prefix=Test';for(;;){const page=await h.call(path);assert.equal(page.status,200);assert(page.json.players.length<=20);seen.push(...page.json.players.map(p=>p.uid));if(page.json.complete)break;path='/v2/players?'+new URLSearchParams({prefix:'Test',cursor:page.json.next_cursor});}assert.equal(new Set(seen).size,45);
    const cursor=(await h.call('/v2/players?prefix=Test')).json.next_cursor;assert.equal((await h.call('/v2/players?'+new URLSearchParams({prefix:'Other',cursor}))).json.error.code,'invalid_cursor');env.BETA_SHARED_PUBLISHERS='[]';assert.deepEqual((await h.call('/v2/players?prefix=Test')).json.players,[]);assert.equal((await h.call(publicPath(bob))).status,404);
  }finally{env.DB.close();}
});
test('progress disclosure is explicit and does not bump the legacy ghost policy versions',async()=>{
  const {env}=await environment();
  try{const response=await createService().fetch(new Request('https://community.example.invalid/policies'),env,{}),body=await response.text();assert(body.includes('id="progress"'));assert(body.includes('progress-1'));assert(body.includes('leaves the last published snapshot visible'));assert(body.includes('Version test-1.'));
  }finally{env.DB.close();}
});
test('per-player inventory is paged by owner index, returns unchanged metadata and gates missing/withdrawn assets',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const expected=[];for(let i=0;i<25;i++)expected.push(await seedRecording(store,i));
    const seen=[];let path=publicPath(alice)+'/recordings';
    for(;;){const r=await h.call(path,{uid:bob});assert.equal(r.status,200,JSON.stringify(r));assert(r.json.recordings.length<=20);seen.push(...r.json.recordings.map(item=>item.id));if(r.json.complete)break;path=publicPath(alice)+'/recordings?'+new URLSearchParams({cursor:r.json.next_cursor});}
    assert.equal(new Set(seen).size,25);assert.deepEqual(new Set(seen),new Set(expected.map(item=>item.id)));
    const direct=(await h.call('/v1/recordings/'+seen[0],{uid:bob})).json;delete direct.v;
    assert.deepEqual((await h.call(publicPath(alice)+'/recordings',{uid:bob})).json.recordings[0],direct);
    await store.sql("UPDATE assets SET state='deleting' WHERE key=?",expected[0].asset).run();
    const remaining=[];for(const r of (await h.call(publicPath(alice)+'/recordings',{uid:bob})).json.recordings)remaining.push(r.id);assert(!remaining.includes(expected[0].id));
    assert.equal((await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}})).status,200);
    assert.deepEqual((await h.call(publicPath(alice)+'/recordings',{uid:bob})).json.recordings,[]);
  }finally{env.DB.close();}
});
test('profile reads and search do not make per-row lookups or scan the progress payload catalog',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    await enable(h);await publish(h);const prepare=env.DB.prepare.bind(env.DB),queries=[];
    env.DB.prepare=sql=>{queries.push(sql);return prepare(sql);};
    await h.call(publicPath(alice)+'/progress',{uid:bob});assert.equal(queries.filter(q=>q.includes('FROM progress_snapshots b')).length,1);assert.equal(queries.length,6);
    queries.length=0;await h.call('/v2/players?prefix=A',{uid:bob});assert.equal(queries.length,7);assert.equal(queries.filter(q=>q.includes('INDEXED BY account_names')).length,1);assert(!queries.some(q=>q.includes('content_json')));
    queries.length=0;await h.call(publicPath(alice)+'/recordings',{uid:bob});assert.equal(queries.length,8);assert(queries.some(q=>q.includes('INDEXED BY recording_owner')));assert(!queries.some(q=>q.includes('SUM(epoch)')));
  }finally{env.DB.close();}
});
test('partial coverage and untagged/unstamped rows remain explicit and segment local identities cannot leak',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    await enable(h);const content=clone(wire.snapshot.content);Object.assign(content.rows[0],{strategy:null,pb_version:null,source_kind:'legacy',saved_utc:null,achieved_utc:null});
    content.coverage={total_rows:3,shared_rows:1,withheld_rows:1,unsupported_rows:1,complete:false};await publish(h,content);
    const result=(await h.call(publicPath(alice)+'/progress',{uid:bob})).json.content;assert.deepEqual(result,content);
    const invalid=await bodyFor(h);invalid.content.rows[0].target={kind:'segment',entity_key:'387',signature:'a'.repeat(64),name:'A definition'};invalid.content_hash=await sha256(canonical(invalid.content));assert.equal((await h.call(snapshotPath,{method:'PUT',body:invalid})).status,400);
    const exported=await h.call('/v2/account/player-export');assert.deepEqual(exported.json.snapshot.content,content);assert.equal(exported.json.publication.publisher.label,wire.enable.publisher.label);
  }finally{env.DB.close();}
});
test('profile payloads share the retained D1 budget, replace/refund atomically and leave old bio on capacity failure',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const first=await h.call(profilePath,{method:'PUT',body:wire.player_profile});assert.equal(first.status,200);
    const original=await store.one('SELECT byte_size FROM player_profiles WHERE uid=?',alice);assert(original.byte_size>0);assert.equal((await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used,original.byte_size);
    await enable(h);await publish(h);const full=(await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used;assert(full>original.byte_size);
    await control(h,'withdraw');assert.equal((await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used,original.byte_size);
    const smaller={expected_revision:1,bio:'Two lines\nOne account',socials:[]};assert.equal((await h.call(profilePath,{method:'PUT',body:smaller})).status,200);const reduced=(await store.one('SELECT bytes_used FROM progress_storage_budget')).bytes_used;assert(reduced<original.byte_size);
    env.PROGRESS_STORAGE_BYTES='1048576';await store.sql('UPDATE progress_storage_budget SET bytes_used=1048576').run();
    const refused=await h.call(profilePath,{method:'PUT',body:{expected_revision:2,bio:'x'.repeat(1000),socials:[]}});assert.equal(refused.status,429);assert.equal(refused.json.error.code,'storage_limit');assert.equal((await h.call(profilePath)).json.bio,smaller.bio);
  }finally{env.DB.close();}
});
test('actual publisher arrivals fence their own inventory but not another owner, including retraction and withdrawal',async()=>{
  const {env,store}=await environment(),h=harness(env),base=publicPath(alice)+'/recordings';
  try{
    for(let i=0;i<25;i++)await seedRecording(store,i);
    const first=await h.call(base,{uid:bob}),cursor=first.json.next_cursor;assert(cursor);
    await uploadGhost(h,bob);assert.equal((await h.call(base+'?'+new URLSearchParams({cursor}),{uid:bob})).status,200);
    const replacement=await uploadGhost(h,alice);assert.equal((await h.call(base+'?'+new URLSearchParams({cursor}),{uid:bob})).json.error.code,'invalid_cursor');
    const after=(await h.call(base,{uid:bob})).json.next_cursor;assert(after);
    assert.equal((await h.call('/v1/recordings/retract',{method:'POST',body:{raw_sha256:replacement.raw_sha256,keep_strategy:null}})).status,200);
    assert.equal((await h.call(base+'?'+new URLSearchParams({cursor:after}),{uid:bob})).json.error.code,'invalid_cursor');
    const fresh=(await h.call(base,{uid:bob})).json.next_cursor;
    assert.equal((await h.call('/v1/account/withdraw',{method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'}})).status,200);
    assert.equal((await h.call(base+'?'+new URLSearchParams({cursor:fresh}),{uid:bob})).json.error.code,'invalid_cursor');
    assert.equal((await store.one('SELECT recording_epoch FROM accounts WHERE uid=?',alice)).recording_epoch,3);
  }finally{env.DB.close();}
});
test('an inventory publication between the gate and rows cannot return a falsely complete page',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    await seedRecording(store,0);const prepare=env.DB.prepare.bind(env.DB);let fired=false;
    env.DB.prepare=sql=>{const statement=prepare(sql);if(sql.includes('INDEXED BY recording_owner')){const all=statement.all;statement.all=async()=>{const result=await all();if(!fired){fired=true;await store.sql('UPDATE accounts SET recording_epoch=recording_epoch+1 WHERE uid=?',alice).run();}return result;};}return statement;};
    const result=await h.call(publicPath(alice)+'/recordings',{uid:bob});assert.equal(result.json.error.code,'invalid_cursor');assert.equal(fired,true);assert(!Object.hasOwn(result.json,'complete'));
  }finally{env.DB.close();}
});
test('actual progress admission honors reader numeric boundaries without applying ghost rank cutoffs',async()=>{
  const body=clone(wire.snapshot),content=body.content;
  async function check(){body.content_hash=await sha256(canonical(content));return validateSnapshot(body,alice);}
  content.rows[0].frames=2592000;content.rows[0].source_cs=8640000;await check();
  for(const value of [0,-1,true,1.5,2592001,Number.MAX_SAFE_INTEGER,NaN,Infinity]){
    content.rows[0].frames=value;await assert.rejects(check(),error=>error.code==='invalid_progress',String(value));
  }
  content.rows[0].frames=1;
  for(const value of [0,-1,true,1.5,8640001,Number.MAX_SAFE_INTEGER,NaN,Infinity]){
    content.rows[0].source_cs=value;await assert.rejects(check(),error=>error.code==='invalid_progress',String(value));
  }
  content.rows[0].source_cs=null;await check();
  content.rows=[];content.coverage={total_rows:2147483647,shared_rows:0,withheld_rows:2147483647,unsupported_rows:0,complete:false};await check();
  content.coverage.total_rows=2147483648;content.coverage.withheld_rows=2147483648;await assert.rejects(check(),error=>error.code==='invalid_progress');
});
test('actual progress admission enforces portable shapes, ROM/source consistency and Python-safe dates',async()=>{
  async function check(change){const body=clone(wire.snapshot);change(body.content.rows[0]);body.content_hash=await sha256(canonical(body.content));return validateSnapshot(body,alice);}
  const valid=[r=>r.target.name=null,r=>r.target.name='é'.repeat(128),r=>r.strategy='é'.repeat(128),r=>r.strategy='\u200d',r=>r.strategy=null,r=>Object.assign(r,{game_version:'jp',pb_version:'jp'}),r=>Object.assign(r,{source_kind:'manual',achieved_utc:null}),r=>r.saved_utc='0001-01-01T00:00:00Z',r=>r.saved_utc='2026-10-19T00:00:00+00:00',r=>r.target={kind:'star',entity_key:'19-1',name:'Slide'},r=>r.target={kind:'segment',entity_key:'1',name:'Public seed',signature:'a'.repeat(64)}];
  for(const change of valid)await check(change);
  const invalid=[r=>delete r.target.name,r=>r.target.strat=null,r=>r.target.signature=null,r=>r.target.name='é'.repeat(129),r=>r.strategy='',r=>r.strategy='é'.repeat(129),r=>r.strategy='bad\nstrategy',r=>r.target.name='\ud800',r=>r.target.entity_key='01-0',r=>r.target.entity_key='0-0',r=>r.target.entity_key='25-0',r=>r.target.entity_key='16-1',r=>r.target.entity_key='19-2',r=>Object.assign(r,{game_version:'us',pb_version:'jp'}),r=>Object.assign(r,{game_version:'us',pb_version:null}),r=>r.source_kind='manual',r=>r.saved_utc='0000-01-01T00:00:00Z',r=>r.saved_utc='2026-02-30T00:00:00Z',r=>r.saved_utc='2026-10-19T24:00:00Z'];
  for(const change of invalid)await assert.rejects(check(change),error=>error.code==='invalid_progress');
});
