import test from 'node:test';
import assert from 'node:assert/strict';
import {createService} from '../src/service.js';
import {config} from '../src/config.js';
import {canonical} from '../src/errors.js';
import {summaryRows,recordingRows} from '../src/library-store.js';
import {encodeCursor} from '../src/cursors.js';
import {environment,alice,bob,catalog,target,digest,nativeClip,upload} from './fixtures.js';

function harness(env,model=catalog){
  const errors=[],service=createService({auth:async r=>({issuer:config(env).issuer,sub:r.headers.get('authorization').slice(7)}),catalog:async()=>model,onError:e=>errors.push(e)});
  return {errors,async call(path,{uid=bob,method='GET',body,raw,bytes,headers={}}={}){
    const response=await service.fetch(new Request('https://community.example.invalid'+path,{method,headers:{authorization:'Bearer '+uid,...(bytes?{'content-type':'application/octet-stream'}:body||raw?{'content-type':'application/json'}:{}),...headers},body:bytes??raw??(body?JSON.stringify(body):undefined)}),env,{});
    return {status:response.status,json:await response.json()};
  }};
}
const listing=(overrides={})=>'/v1/library/recordings?'+new URLSearchParams({target:JSON.stringify(target),clock:'igt',rom:'us',strategy:'Standard',...overrides});
const metadata=(h,ids,options={})=>h.call('/v1/recordings/metadata',{method:'POST',body:{recording_ids:ids},...options});
const key=t=>t.kind==='star'?'star:'+t.entity_key:'segment:'+t.signature;
let sequence=0;
async function seed(store,{uid=alice,frames=100,step=1,portable=target,strategy='Standard',clock='igt',rom='us',generation=catalog.generation,published=1,winner=true,state='stored'}={}){
  const id=digest('library-fixture-'+ ++sequence),asset=uid+'/'+id;
  if(!await store.one('SELECT uid FROM accounts WHERE uid=?',uid))await store.sql("INSERT INTO accounts(uid,issuer,subject,invited,status,name,name_key,sharing,terms_version,privacy_version,created_at,updated_at) VALUES(?,?,?,1,'active',?,?,1,'test-1','test-1',0,0)",uid,store.cfg.issuer,uid,'Player '+sequence,'player '+sequence).run();
  await store.sql('INSERT INTO assets(key,uid,raw_sha256,encoded_sha256,encoded_bytes,state,created_at) VALUES(?,?,?,?,?,?,0)',asset,uid,id,id,200,state).run();
  await store.sql('INSERT INTO recordings(id,uid,asset_key,target_key,target_json,clock,rom,strategy,frames,raw_sha256,raw_bytes,encoded_sha256,encoded_bytes,generation,step,overall_step,published,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)',id,uid,asset,key(portable),canonical(portable),clock,rom,strategy,frames,id,130000,id,200,generation,step,step,published).run();
  if(winner)await store.sql('INSERT INTO winners VALUES(?,?,?,?,?,?,?,?,?)',uid,key(portable),clock,rom,strategy,generation,step,id,frames).run();
  return {id,asset,uid};
}
function next(path,cursor){return path+(path.includes('?')?'&':'?')+new URLSearchParams({cursor});}

test('batch metadata consumes one ticket and one publication query, preserves the strict row schema',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const rows=[];for(let i=0;i<4;i++)rows.push(await seed(store,{step:i,frames:100+i}));
    const queries=[],prepare=env.DB.prepare.bind(env.DB);env.DB.prepare=sql=>{queries.push(sql);return prepare(sql);};
    const result=await metadata(h,rows.map(r=>r.id));assert.equal(result.status,200,JSON.stringify(result));
    assert.equal(queries.length,6,'one entry update, account lookup, three ticket statements and one publication read');
    assert.equal(result.json.recordings.length,4);assert.deepEqual(result.json.unavailable,[]);
    assert.equal((await store.one('SELECT ops FROM quotas WHERE owner=?',bob)).ops,1);
    assert.equal(queries.filter(q=>q.includes('FROM recordings r')).length,1);
    const single=await h.call('/v1/recordings/'+rows[0].id);
    const {v:_v,...legacy}=single.json;assert.deepEqual(result.json.recordings[0],legacy);
    assert.deepEqual(result.json.recordings.map(r=>r.id),rows.map(r=>r.id));assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('missing asset metadata is unavailable even when its winner row survives a damaged fixture',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const r=await seed(store);
    // Normal FK-protected maintenance cannot create this state. Discovery must
    // still fail closed if imported/corrupted metadata lacks its asset row.
    env.DB.sqlite.exec('PRAGMA foreign_keys=OFF');
    await store.sql('DELETE FROM assets WHERE key=?',r.asset).run();
    assert.deepEqual((await metadata(h,[r.id])).json.unavailable,[r.id]);
    assert.deepEqual((await h.call('/v1/library')).json.entries,[]);
    assert.deepEqual((await h.call(listing())).json.recordings,[]);
    assert.equal((await h.call('/v1/recordings/'+r.id)).status,404);
  }finally{env.DB.close();}
});
test('batch input is strictly bounded, read-only and needs no mutation key',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    assert.deepEqual((await metadata(h,[])).json,{v:1,recordings:[],unavailable:[]});
    const id=(await seed(store)).id;
    for(const ids of [[id,id],Array(5).fill(id),['BAD'],null,'not-an-array'])assert.equal((await metadata(h,ids)).status,400);
    assert.equal((await h.call('/v1/recordings/metadata',{method:'POST',body:{recording_ids:[],extra:1}})).status,400);
    assert.equal((await h.call('/v1/recordings/metadata?extra=1',{method:'POST',body:{recording_ids:[]}})).status,400);
    assert.equal((await h.call('/v1/recordings/metadata',{method:'POST',raw:' '.repeat(16385)})).status,413);
    assert.equal((await store.one('SELECT COUNT(*) AS n FROM mutation_receipts')).n,0);
  }finally{env.DB.close();}
});
test('batched renewal preserves owner pin visibility without putting replaced winners in discovery',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const old=await seed(store,{winner:false}),current=await seed(store,{frames:90});
    await store.sql('INSERT INTO pins VALUES(?,?,?)',bob,old.id,Math.floor(Date.now()/1000)+60).run();
    assert.deepEqual((await metadata(h,[old.id,current.id])).json.unavailable,[]);
    assert.deepEqual((await metadata(h,[old.id],{uid:alice})).json.unavailable,[old.id]);
    assert.deepEqual((await h.call(listing())).json.recordings.map(r=>r.id),[current.id]);
    assert.equal((await h.call('/v1/library')).json.entries[0].recordings,1);
    await store.sql('UPDATE pins SET expires_at=0').run();
    assert.deepEqual((await metadata(h,[old.id])).json.unavailable,[old.id]);
  }finally{env.DB.close();}
});
test('current publication, publisher status and stored assets gate every new route',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const a=await seed(store),missing='f'.repeat(64);
    for(const change of ["UPDATE accounts SET invited=0 WHERE uid=?","UPDATE accounts SET status='suspended' WHERE uid=?","UPDATE recordings SET published=0 WHERE uid=?","UPDATE assets SET state='deleting' WHERE uid=?"]){
      await store.sql(change,alice).run();
      assert.deepEqual((await metadata(h,[a.id,missing])).json,{v:1,recordings:[],unavailable:[a.id,missing]});
      assert.deepEqual((await h.call('/v1/library')).json.entries,[]);
      assert.deepEqual((await h.call(listing())).json.recordings,[]);
      await store.sql("UPDATE accounts SET invited=1,status='active' WHERE uid=?",alice).run();
      await store.sql('UPDATE recordings SET published=1 WHERE uid=?',alice).run();
      await store.sql("UPDATE assets SET state='stored' WHERE uid=?",alice).run();
    }
    // Sharing disabled stops new contributions. Explicit withdrawal hides old ones.
    await store.sql('UPDATE accounts SET sharing=0 WHERE uid=?',alice).run();
    assert.equal((await h.call('/v1/library')).json.entries.length,1);
    assert.equal((await h.call('/v1/account/withdraw',{uid:alice,method:'POST',body:{confirmation:'WITHDRAW MY GHOSTS'},headers:{'idempotency-key':crypto.randomUUID()}})).status,200);
    assert.deepEqual((await metadata(h,[a.id])).json.unavailable,[a.id]);
    assert.deepEqual((await h.call('/v1/library')).json.entries,[]);
  }finally{env.DB.close();}
});
test('friend publisher allowlist applies to summary, records and metadata; the viewer always sees their own',async()=>{
  const {env,store}=await environment();
  try{
    const a=await seed(store),b=await seed(store,{uid:bob});env.BETA_SHARED_PUBLISHERS='[]';let h=harness(env);
    assert.deepEqual((await metadata(h,[a.id,b.id])).json.unavailable,[a.id]);
    assert.deepEqual((await h.call(listing())).json.recordings.map(r=>r.id),[b.id]);
    assert.equal((await h.call('/v1/library')).json.entries[0].players,1);
    env.BETA_SHARED_PUBLISHERS=JSON.stringify([alice]);h=harness(env);
    assert.equal((await h.call('/v1/library')).json.entries[0].players,2);
    assert.equal((await metadata(h,[a.id,b.id])).json.recordings.length,2);
  }finally{env.DB.close();}
});
test('summary counts records independently from players and excludes stale generations',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    await seed(store,{step:1,frames:100});await seed(store,{step:2,frames:50});await seed(store,{uid:bob,frames:110});
    await seed(store,{generation:'a'.repeat(64),frames:1});
    assert.deepEqual((await h.call('/v1/library')).json,{v:1,generation:catalog.generation,entries:[{target,clock:'igt',game_version:'us',strategy:'Standard',recordings:3,players:2,best_frames:50}],next_cursor:null,complete:true});
    assert.deepEqual((await h.call(listing())).json.recordings.map(r=>r.frames),[50,100,110]);
  }finally{env.DB.close();}
});
test('summary target and ROM filters apply before keyset pagination and bind the cursor',async()=>{
  const {env,store}=await environment(),other={kind:'star',entity_key:'2-0',name:'Other star'},model=structuredClone(catalog);
  model.entries.push({...structuredClone(model.entries[0]),target:other},{...structuredClone(model.entries[0]),game_version:'jp'});
  const h=harness(env,model),path='/v1/library?'+new URLSearchParams({target:JSON.stringify(target),rom:'us'});
  try{
    // More than a page of preceding identities must not hide the requested one.
    for(let i=0;i<25;i++)await seed(store,{portable:other,strategy:'Method '+i});
    await seed(store);await seed(store,{rom:'jp'});
    assert.deepEqual((await h.call(path)).json.entries.map(e=>e.target.entity_key),[target.entity_key]);
    assert.equal((await h.call(path)).json.entries[0].game_version,'us');
    const jp=await h.call('/v1/library?rom=jp');assert.equal(jp.json.entries.length,1);assert.equal(jp.json.entries[0].game_version,'jp');
    const cursor=(await h.call('/v1/library')).json.next_cursor;assert(cursor);
    assert.equal((await h.call(next(path,cursor))).json.error.code,'invalid_cursor');
  }finally{env.DB.close();}
});
test('recording keysets traverse more than four pages with ties, bounded20 rows and no repeats',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const expected=[];for(let i=0;i<105;i++)expected.push(await seed(store,{uid:'40000000-0000-4000-8000-'+String(i).padStart(12,'0'),frames:100+Math.floor(i/4)}));
    const seen=[],sizes=[];let path=listing();
    for(;;){const r=await h.call(path);assert.equal(r.status,200,JSON.stringify(r));sizes.push(r.json.recordings.length);seen.push(...r.json.recordings.map(r=>r.id));assert.equal(r.json.complete,r.json.next_cursor===null);if(r.json.complete)break;path=next(listing(),r.json.next_cursor);}
    assert.deepEqual(sizes,[20,20,20,20,20,5]);assert.equal(new Set(seen).size,105);assert.deepEqual(new Set(seen),new Set(expected.map(r=>r.id)));
  }finally{env.DB.close();}
});
test('summary keysets traverse every identity without per-target queries or a hidden depth cap',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    for(let i=0;i<105;i++)await seed(store,{portable:{kind:'star',entity_key:Math.floor(i/7)+'-'+i%7,name:'Fixture '+i}});
    const queries=[],prepare=env.DB.prepare.bind(env.DB);env.DB.prepare=sql=>{queries.push(sql);return prepare(sql);};
    const seen=[];let path='/v1/library',pages=0;
    for(;;){const r=await h.call(path);assert.equal(r.status,200,JSON.stringify(r));assert(r.json.entries.length<=20);seen.push(...r.json.entries.map(e=>key(e.target)));pages++;if(r.json.complete)break;path=next('/v1/library',r.json.next_cursor);}
    assert.equal(pages,6);assert.equal(new Set(seen).size,105);
    assert.equal(queries.filter(q=>q.includes('FROM recordings r')).length,6);
    assert(queries.filter(q=>q.includes('FROM recordings r')).every(q=>q.includes('GROUP BY')&&q.includes('INDEXED BY recording_library')));
  }finally{env.DB.close();}
});
test('library cursors reject changed filters, caller, publication, visibility, generation and migration',async()=>{
  const {env,store}=await environment(),model=structuredClone(catalog);model.entries[0].strategies.push({name:'Other',cutoffs:model.entries[0].strategies[0].cutoffs});
  const h=harness(env,model);
  try{
    for(let i=0;i<21;i++)await seed(store,{uid:'50000000-0000-4000-8000-'+String(i).padStart(12,'0')});
    const cursor=(await h.call(listing())).json.next_cursor;assert(cursor);
    assert.equal((await h.call(next(listing({strategy:'Other'}),cursor))).json.error.code,'invalid_cursor');
    assert.equal((await h.call(next(listing(),cursor),{uid:alice})).json.error.code,'invalid_cursor');
    assert.equal((await h.call(next('/v1/library',cursor))).json.error.code,'invalid_cursor');
    await store.sql('INSERT INTO scope_epochs VALUES(?,1)',key(target)).run();
    assert.equal((await h.call(next(listing(),cursor))).json.error.code,'invalid_cursor');
    const fresh=(await h.call(listing())).json.next_cursor;
    await store.sql('UPDATE settings SET global_epoch=global_epoch+1').run();
    assert.equal((await h.call(next(listing(),fresh))).json.error.code,'invalid_cursor');
    await store.sql('UPDATE settings SET migration_generation=?',catalog.generation).run();
    for(const path of ['/v1/library',listing()])assert.equal((await h.call(path)).json.error.code,'generation_changed');
    await store.sql('UPDATE settings SET migration_generation=NULL,generation=?','a'.repeat(64)).run();
    assert.equal((await h.call(listing())).json.error.code,'generation_changed');
  }finally{env.DB.close();}
});
test('target-scoped continuations survive unrelated uploads but reject publications in their own scope',async()=>{
  const {env,store}=await environment(),model=structuredClone(catalog),other={kind:'star',entity_key:'1-1',name:'Other star'};
  model.entries.push({...structuredClone(model.entries[0]),target:other});
  for(let i=0;i<21;i++)model.entries[0].strategies.push({name:'Method '+i,cutoffs:model.entries[0].strategies[0].cutoffs});
  const h=harness(env,model),summary='/v1/library?'+new URLSearchParams({target:JSON.stringify(target)});
  async function publish(portable,marker){
    const fixture=upload(nativeClip({marker}));
    const admitted=await h.call('/v1/uploads',{uid:alice,method:'POST',body:{...fixture.body,target:portable},headers:{'idempotency-key':crypto.randomUUID()}});
    assert.equal(admitted.status,200,JSON.stringify(admitted));assert.equal(admitted.json.disposition,'upload');
    const result=await h.call('/v1/uploads/'+admitted.json.upload_id,{uid:alice,method:'PUT',bytes:fixture.bytes});
    assert.equal(result.status,200,JSON.stringify(result));assert.equal(result.json.disposition,'published');
  }
  try{
    for(let i=0;i<21;i++){
      await seed(store,{uid:'70000000-0000-4000-8000-'+String(i).padStart(12,'0')});
      await seed(store,{strategy:'Method '+i});
    }
    const cursor=(await h.call(listing())).json.next_cursor,summaryCursor=(await h.call(summary)).json.next_cursor,
      globalCursor=(await h.call('/v1/library')).json.next_cursor;
    assert(cursor&&summaryCursor&&globalCursor);
    await publish(other,5);
    assert.equal((await store.one('SELECT epoch FROM scope_epochs WHERE scope=?',key(other))).epoch,1);
    assert.equal((await h.call(next(listing(),cursor))).status,200);
    assert.equal((await h.call(next(summary,summaryCursor))).status,200);
    assert.equal((await h.call(next('/v1/library',globalCursor))).json.error.code,'invalid_cursor');
    await publish(target,6);
    assert.equal((await store.one('SELECT epoch FROM scope_epochs WHERE scope=?',key(target))).epoch,1);
    assert.equal((await h.call(next(listing(),cursor))).json.error.code,'invalid_cursor');
    assert.equal((await h.call(next(summary,summaryCursor))).json.error.code,'invalid_cursor');
    assert.deepEqual(h.errors,[]);
  }finally{env.DB.close();}
});
test('library query and cursor schemas reject arbitrary selectors, malformed, expired and unsigned continuations',async()=>{
  const {env}=await environment(),h=harness(env);
  try{
    for(const path of ['/v1/library?owner=anything','/v1/library?rom=eu','/v1/library?target=null','/v1/library?cursor=x&cursor=y',listing({scope:'overall'}),listing({rom:'eu'}),listing({target:'null'}),listing({target:'broken'}),'/v1/library/recordings']){const result=await h.call(path);assert([400,422].includes(result.status),path);}
    for(const cursor of ['','x','x.y','a'.repeat(2049)])assert.equal((await h.call(next('/v1/library',cursor))).json.error.code,'invalid_cursor');
    // A correctly signed foreign-purpose cursor still cannot become a Library page.
    const foreign=await encodeCursor(env.CURSOR_SECRET,{key:'wrong'},[1,100,'a'.repeat(64)],1);
    assert.equal((await h.call(next('/v1/library',foreign))).json.error.code,'invalid_cursor');
  }finally{env.DB.close();}
});
test('signed Library cursors expire after five minutes and reject signature tampering',async(t)=>{
  const {env,store}=await environment(),h=harness(env);
  const original=Date.now;
  try{
    for(let i=0;i<21;i++)await seed(store,{uid:'60000000-0000-4000-8000-'+String(i).padStart(12,'0')});
    const cursor=(await h.call(listing())).json.next_cursor;assert(cursor);
    const [body,signature]=cursor.split('.');
    const corrupted=body+'.'+(signature[0]==='a'?'b':'a')+signature.slice(1);
    assert.equal((await h.call(next(listing(),corrupted))).json.error.code,'invalid_cursor');
    t.mock.method(Date,'now',()=>original()+301000);
    assert.equal((await h.call(next(listing(),cursor))).json.error.code,'invalid_cursor');
  }finally{env.DB.close();}
});
test('query plans seek existing/new indexes and do not sort all recording rows before paging',async()=>{
  const {env,store}=await environment();
  try{
    await seed(store);const plans=[],rows=store.rows.bind(store);
    store.rows=async(sql,...values)=>{plans.push(await rows('EXPLAIN QUERY PLAN '+sql,...values));return rows(sql,...values);};
    await summaryRows(store,{generation:catalog.generation,pageSize:20,after:['star:0-0','igt','us','Standard']});
    await recordingRows(store,{key:key(target),clock:'igt',rom:'us',generation:catalog.generation,strategy:'Standard',pageSize:20,after:[100,'0'.repeat(64)]});
    assert(plans[0].some(r=>r.detail.includes('recording_library')));assert(plans[1].some(r=>r.detail.includes('fastest_strategy')));
    assert(plans.every(plan=>!plan.some(r=>r.detail.includes('TEMP B-TREE FOR ORDER BY'))),JSON.stringify(plans));
  }finally{env.DB.close();}
});
test('unexpected database failures fail the whole renewal instead of extending partial leases',async()=>{
  const {env,store}=await environment(),h=harness(env);
  try{
    const r=await seed(store),prepare=env.DB.prepare.bind(env.DB);
    env.DB.prepare=sql=>{if(sql.includes('FROM recordings r'))throw Error('Fixture database unavailable');return prepare(sql);};
    const result=await metadata(h,[r.id]);assert.equal(result.status,503);assert.equal(result.json.error.code,'service_error');assert(!Object.hasOwn(result.json,'unavailable'));assert.equal(h.errors.length,1);
  }finally{env.DB.close();}
});
