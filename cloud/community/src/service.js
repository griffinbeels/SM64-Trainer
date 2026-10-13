import {authenticate,authenticateAdmin} from './auth.js';
import {config,ready,LIMITS} from './config.js';
import {ServiceError,requireThat,json,readJSON,exactQuery,boundedBody,hex64,uuid,integer,canonical,sha256,fail} from './errors.js';
import {catalogFor,lookup,grade,targetKey,aboveCeiling} from './catalog.js';
import {Store} from './store.js';
import {validateGHC1} from './codec.js';
import {checkedName,prefixRange} from './names.js';
import {encodeCursor,decodeCursor} from './cursors.js';
import {nativeAuthorize} from './portal-contract.js';
import {policies} from './policies.js';
import {armMaintenance} from './maintenance.js';
const now=()=>Math.floor(Date.now()/1000);
async function mutation(r,s,uid,route,body){const key=r.headers.get('idempotency-key');requireThat(uuid(key),'idempotency_required','Use a UUID Idempotency-Key.');const digest=await sha256(canonical(body));return {key,digest,previous:await s.receipt(uid,route,key,digest)};}
function queryTarget(t){try{return JSON.parse(t);}catch{fail(400,'invalid_target','Invalid portable target.');}}
function intQuery(u,k){const t=u.searchParams.get(k);requireThat(/^(0|[1-9]\d{0,2})$/.test(t||''));return Number(t);}
function freshConfirmation(v,phrase){requireThat(v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===2&&v.phrase===phrase&&integer(v.issued_at,now()-300,now()+30),'confirmation_required','Use a fresh confirmation for this exact action.');}
async function portalHTML(env,r){const u=new URL(r.url);u.pathname='/index.html';u.search='';const response=await env.ASSETS.fetch(new Request(u));return new Response(response.body,{status:response.status,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'private, no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self' "+new URL(env.SUPABASE_URL).origin+"; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}});}
export function createService({auth=authenticate,adminAuth=authenticateAdmin,catalog=catalogFor,onError=()=>{}}={}){
 return {async fetch(request,env,ctx){try{
  const url=new URL(request.url);
  if(url.hostname.startsWith('www.')){return Response.redirect('https://'+url.hostname.slice(4)+url.pathname+url.search,301);}
  const cfg=config(env),origin=request.headers.get('origin');
  requireThat(!origin||origin===url.origin,'origin_denied','Origin is not allowed.',403);
  requireThat(!url.username&&!url.password);
  if(env.BURST){const result=await env.BURST.limit({key:request.headers.get('cf-connecting-ip')||'unknown'});requireThat(result.success,'burst_limit','Slow down and retry.',429);}
  ready(cfg);
  cfg.policyURL=url.origin+'/policies';  // The policies page is this Worker's own route, on whichever host served the request.
  if(url.pathname==='/policies'&&request.method==='GET'){exactQuery(url,[]);return policies(cfg);}
  if(url.pathname==='/v1/config'&&request.method==='GET'){
    exactQuery(url,[]);const model=await catalog(env);
    return json({protocol:1,limits:LIMITS,terms_version:cfg.terms,privacy_version:cfg.privacy,terms_url:cfg.policyURL+'#terms',privacy_url:cfg.policyURL+'#privacy',operator_name:cfg.operator,operator_contact:cfg.contact,generation:model.generation,auth:{supabase_url:cfg.supabaseURL,publishable_key:cfg.publishableKey,client_id:cfg.clientID,native_callback:cfg.callback,native_clients:cfg.nativeClients,authorization_ui_url:url.origin+'/auth/authorize',authorization_start_url:url.origin+'/auth/start'}});
  }
  if(url.pathname==='/auth/start'&&request.method==='GET'){
    exactQuery(url,['provider','native_authorize'],['provider','native_authorize']);requireThat(['discord','google','twitch','email'].includes(url.searchParams.get('provider')));nativeAuthorize(url.searchParams.get('native_authorize'),cfg);return portalHTML(env,request);
  }
  if(['/auth/authorize','/auth/callback','/auth/recovery'].includes(url.pathname)&&request.method==='GET'){
    exactQuery(url,url.pathname==='/auth/authorize'?['authorization_id']:['code','error','error_code','error_description']);return portalHTML(env,request);
  }
  const s=new Store(env.DB,cfg);
  if(url.pathname.startsWith('/v1/admin/')){
    const actor=await adminAuth(request,env);await s.ticket(actor);requireThat(request.method==='POST','method_not_allowed','Use POST.',405);exactQuery(url,[]);
    if(url.pathname==='/v1/admin/invites'){
      const b=await readJSON(request,['subject','invited','reason','confirmation']);requireThat(uuid(b.subject)&&typeof b.invited==='boolean'&&typeof b.reason==='string'&&b.reason.length>=5&&b.reason.length<=512);freshConfirmation(b.confirmation,'CHANGE INVITATION');
      const m=await mutation(request,s,actor,'invite',b);if(m.previous)return json(m.previous);
      return json(await s.invite(b.subject,b.invited,actor,b.reason,b.confirmation.phrase,m));
    }
    if(url.pathname==='/v1/admin/reclaim'){
      const b=await readJSON(request,['name','expected_owner','destination_uid','transfer_sheet','reason','confirmation']);requireThat(typeof b.name==='string'&&uuid(b.expected_owner)&&uuid(b.destination_uid)&&typeof b.transfer_sheet==='boolean'&&typeof b.reason==='string'&&b.reason.length>=5&&b.reason.length<=512);freshConfirmation(b.confirmation,'RECLAIM '+b.name.normalize('NFKC').trim());
      const m=await mutation(request,s,actor,'reclaim',b);if(m.previous)return json(m.previous);return json(await s.reclaim({...b,confirmation:b.confirmation.phrase},actor,m));
    }
    fail(404,'not_found','Route not found.');
  }
  const identity=await auth(request,cfg);
  await s.authEntryTicket();
  const a=await s.account(identity);
  if(url.pathname==='/v1/account/invitation'&&request.method==='POST'){
    exactQuery(url,[]);const body=await readJSON(request,['token']);
    if(a.invited)await s.ticket(a.uid);
    const account=await s.redeem(identity,body.token);armMaintenance(env,ctx);return json(account);
  }
  if(!a.invited||['deleted','suspended'].includes(a.status)){
    if(url.pathname==='/v1/account'&&request.method==='GET'){exactQuery(url,[]);return json(s.publicAccount(a));}
    // Persisted owners retain export/removal after invitation revocation or
    // suspension. Unknown identities have no owned data and must add no rows.
    const ownData=(url.pathname==='/v1/account/export'&&request.method==='GET')||
      (['/v1/account/withdraw','/v1/account/delete','/v1/recordings/retract'].includes(url.pathname)&&request.method==='POST');
    if(a.ephemeral||!ownData)s.active(a,true);
  }
  await s.ticket(a.uid,{classB:url.pathname.endsWith('/asset')?1:0});
  armMaintenance(env,ctx);
  if(url.pathname==='/v1/account'&&request.method==='GET'){exactQuery(url,[]);return json(s.publicAccount(a));}
  if(url.pathname==='/v1/account'&&request.method==='PUT'){
    exactQuery(url,[]);const b=await readJSON(request,['name','sharing','terms_version','privacy_version','sheet_runner']),m=await mutation(request,s,a.uid,'profile',b);if(m.previous)return json(s.publicAccount(await s.one('SELECT * FROM accounts WHERE uid=?',a.uid)));return json(await s.profile(a,b,m.key,m.digest));
  }
  if(url.pathname==='/v1/account/name'&&request.method==='PUT'){
    exactQuery(url,[]);const b=await readJSON(request,['name']),m=await mutation(request,s,a.uid,'name',b);if(m.previous)return json(s.publicAccount(await s.one('SELECT * FROM accounts WHERE uid=?',a.uid)));return json(await s.rename(a,b,m.key,m.digest));
  }
  if(url.pathname==='/v1/names/check'&&request.method==='GET'){
    // The live name check while typing (round 41). The claim at PUT /v1/account
    // stays the authority; this only answers "held by someone else right now?".
    exactQuery(url,['name'],['name']);const named=checkedName(url.searchParams.get('name'));
    const holder=await s.one('SELECT uid FROM accounts WHERE name_key=?',named.key);
    return json({v:1,name:named.name,available:!holder||holder.uid===a.uid});
  }
  if(url.pathname==='/v1/account/sharing'&&request.method==='PUT'){
    exactQuery(url,[]);s.active(a);const b=await readJSON(request,['enabled']);requireThat(typeof b.enabled==='boolean');const m=await mutation(request,s,a.uid,'sharing',b);if(m.previous)return json(s.publicAccount(await s.one('SELECT * FROM accounts WHERE uid=?',a.uid)));const response=s.publicAccount({...a,sharing:Number(b.enabled)});
    await s.tx("EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1 AND status='active')",[a.uid],[s.sql('UPDATE accounts SET sharing=?,updated_at=? WHERE uid=?',Number(b.enabled),now(),a.uid),s.receiptSQL(a.uid,'sharing',m.key,m.digest,response)]);return json(response);
  }
  if(['/v1/account/withdraw','/v1/account/delete'].includes(url.pathname)&&request.method==='POST'){
    exactQuery(url,[]);const deleting=url.pathname.endsWith('delete'),b=await readJSON(request,['confirmation']);requireThat(b.confirmation===(deleting?'DELETE MY ACCOUNT':'WITHDRAW MY GHOSTS'),'confirmation_required','Explicit confirmation required.');const m=await mutation(request,s,a.uid,deleting?'delete':'withdraw',b);if(m.previous)return json(m.previous);return json(await s.withdraw(a,m.key,m.digest,deleting));
  }
  if(url.pathname==='/v1/recordings/retract'&&request.method==='POST'){
    // A reclassified run unpublishes its copies under every other strategy
    // (all of them when keep_strategy is null). Owner-only, like withdrawal.
    exactQuery(url,[]);const b=await readJSON(request,['raw_sha256','keep_strategy']);requireThat(hex64(b.raw_sha256)&&(b.keep_strategy===null||typeof b.keep_strategy==='string'&&b.keep_strategy.length>=1&&b.keep_strategy.length<=256));
    const m=await mutation(request,s,a.uid,'retract',b);if(m.previous)return json(m.previous);return json(await s.retract(a,b.raw_sha256,b.keep_strategy,m.key,m.digest));
  }
  if(url.pathname==='/v1/account/export'&&request.method==='GET'){
    exactQuery(url,[]);const rows=await s.rows('SELECT id,target_json,clock,rom,strategy,frames,raw_sha256,raw_bytes,published,created_at FROM recordings WHERE uid=? ORDER BY id LIMIT 1001',a.uid);return json({account:s.publicAccount(a),recordings:rows.slice(0,1000).map(r=>({...r,target:JSON.parse(r.target_json),target_json:undefined})),complete:rows.length<=1000});
  }
  s.active(a);const model=await catalog(env);
  if(url.pathname==='/v1/catalog'&&request.method==='GET'){exactQuery(url,[]);return json(model);}
  if(['/v1/pool','/v1/search'].includes(url.pathname)&&request.method==='GET'){
    const search=url.pathname.endsWith('search');exactQuery(url,['target','clock','rom','scope','strategy','min_step','max_step','base_step','cursor',...(search?['prefix']:[])],['target','clock','rom','scope',...(search?['prefix']:['min_step','max_step','base_step'])]);
    const scope=url.searchParams.get('scope');requireThat(['strategy','overall'].includes(scope));requireThat(scope==='strategy'?Boolean(url.searchParams.get('strategy')):!url.searchParams.has('strategy'));
    const target=queryTarget(url.searchParams.get('target')),clock=url.searchParams.get('clock'),rom=url.searchParams.get('rom'),key=targetKey(target),entry=model.entries.find(e=>targetKey(e.target)===key&&e.clock===clock&&e.game_version===rom);
    requireThat(entry,'unsupported_target','Unsupported sharing identity.',422);const strategy=url.searchParams.get('strategy'),strat=entry.strategies.find(s=>s.name===strategy);requireThat(scope==='overall'||strat,'unsupported_strategy','Unsupported strategy.',422);
    const cutoffs=scope==='overall'?entry.overall_cutoffs:strat.cutoffs,minStep=search&&!url.searchParams.has('min_step')?cutoffs[0].step:intQuery(url,'min_step'),maxStep=search&&!url.searchParams.has('max_step')?cutoffs.at(-1).step:intQuery(url,'max_step'),baseStep=search&&!url.searchParams.has('base_step')?minStep:intQuery(url,'base_step');
    requireThat(minStep<=baseStep&&baseStep<=maxStep&&minStep>=cutoffs[0].step&&maxStep<=cutoffs.at(-1).step&&(search||maxStep-minStep+1<=LIMITS.max_band),'invalid_band','Choose a bounded canonical band.');
    const gate=await s.gates(a,key);requireThat(gate.generation===model.generation,'generation_changed','Catalog changed.',409);const prefix=search?prefixRange(url.searchParams.get('prefix')):null;
    const visibleOwners=s.visibleOwners(a),identity={key,clock,rom,scope,strategy:strategy||null,minStep,maxStep,baseStep,generation:model.generation,globalEpoch:gate.global_epoch,scopeEpoch:gate.scope_epoch,...(prefix?{prefix}:{}),...(visibleOwners?{visibleOwners}:{})};let page=0,after=null;
    if(url.searchParams.has('cursor')){const c=await decodeCursor(env.CURSOR_SECRET,url.searchParams.get('cursor'),identity);page=c.page;after=c.after;}
    const query={...identity,prefix,after,pageSize:search?LIMITS.search_rows:LIMITS.page_rows};let rows,searchPage;
    if(search){searchPage=await s.searchCandidates(query);rows=searchPage.rows;}
    else if(ctx?.exports?.MetadataCache){const response=await ctx.exports.MetadataCache({props:{query}}).fetch(new Request('https://internal.community/pool'));requireThat(response.ok,'cache_unavailable','Candidate cache unavailable.',503);rows=await response.json();}else rows=await s.poolCandidates(query);
    const more=search?searchPage.more:rows.length>query.pageSize;rows=rows.slice(0,query.pageSize);const next=more&&page<3?await encodeCursor(env.CURSOR_SECRET,identity,search?searchPage.after:[rows.at(-1).response_step,rows.at(-1).frames,rows.at(-1).id],page+1):null;
    return json({recordings:rows.map(r=>s.metadata(r)),coverage:{complete:!more,min_step:minStep,max_step:maxStep,base_step:baseStep,generation:model.generation},next_cursor:next});
  }
  const rm=/^\/v1\/recordings\/([a-f0-9]{64})(\/asset)?$/.exec(url.pathname);
  if(rm&&request.method==='GET'){
    exactQuery(url,[]);const r=await s.recording(a,rm[1]);if(!rm[2])return json(s.metadata(r));let response;
    if(ctx?.exports?.AssetCache)response=await ctx.exports.AssetCache({props:{key:r.asset_key,encoded_sha256:r.encoded_sha256}}).fetch(new Request('https://internal.community/asset'));
    else{const object=await env.GHOSTS.get(r.asset_key);requireThat(object,'asset_missing','Recording unavailable.',404);response=new Response(object.body);}
    requireThat(response.ok,'asset_missing','Recording unavailable.',404);return new Response(response.body,{headers:{'Content-Type':'application/octet-stream','Content-Length':String(r.encoded_bytes),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','ETag':'"'+r.encoded_sha256+'"'}});
  }
  if(url.pathname==='/v1/uploads'&&request.method==='POST'){
    exactQuery(url,[]);const b=await readJSON(request,['target','clock','rom','strategy','frames','raw_sha256','raw_bytes','encoded_sha256','encoded_bytes','codec','generation']);requireThat(integer(b.frames,1,2592000)&&hex64(b.raw_sha256)&&hex64(b.encoded_sha256)&&integer(b.raw_bytes,812,LIMITS.raw_bytes)&&integer(b.encoded_bytes,189,LIMITS.encoded_bytes)&&b.codec==='ghc1'&&hex64(b.generation));requireThat(b.generation===model.generation,'generation_changed','Catalog changed.',409);
    const found=lookup(model,b.target,b.clock,b.rom,b.strategy),key=request.headers.get('idempotency-key');requireThat(uuid(key),'idempotency_required','Use a UUID Idempotency-Key.');return json(await s.admit(a,b,model,key,await sha256(canonical(b)),{...found,step:grade(found.strat.cutoffs,b.frames),overallStep:grade(found.entry.overall_cutoffs,b.frames),aboveCeiling:aboveCeiling(found.strat.cutoffs,b.frames)}));
  }
  const um=/^\/v1\/uploads\/([0-9a-f-]{36})$/.exec(url.pathname);
  if(um&&request.method==='PUT'){
    exactQuery(url,[]);requireThat(request.headers.get('content-type')==='application/octet-stream','content_type','Expected binary GHC1.');const g=await s.beginUpload(a,um[1]);if(g.state==='complete')return json(s.grantResponse(g));if(g.state==='stored')return json(await s.publish(g));let bytes;
    try{bytes=await boundedBody(request,LIMITS.encoded_bytes,g.encoded_bytes);validateGHC1(bytes,g);}catch(error){await s.release(g);throw error;}
    const multipart=await env.GHOSTS.createMultipartUpload(g.asset_key,{httpMetadata:{contentType:'application/octet-stream'},customMetadata:{encoded_sha256:g.encoded_sha256}});
    try{
      await s.tx("EXISTS(SELECT 1 FROM grants WHERE id=? AND state='receiving' AND expires_at>?)",[g.id,now()],[s.sql('UPDATE grants SET multipart_id=? WHERE id=?',multipart.uploadId,g.id)]);
    }catch(error){await multipart.abort();throw error;}
    const part=await multipart.uploadPart(1,bytes);
    await multipart.complete([part]);
    await s.stored(g);return json(await s.publish(g));
  }
  if(url.pathname==='/v1/pins'&&request.method==='PUT'){
    exactQuery(url,[]);const b=await readJSON(request,['recording_ids']);requireThat(Array.isArray(b.recording_ids)&&b.recording_ids.length<=4&&b.recording_ids.every(hex64)&&new Set(b.recording_ids).size===b.recording_ids.length);const m=await mutation(request,s,a.uid,'pins',b);if(m.previous)return json(m.previous);return json(await s.pins(a,b.recording_ids,m.key,m.digest));
  }
  fail(404,'not_found','Route not found.');
 }catch(error){if(error instanceof ServiceError){const retry=error.status===429?(['budget_exhausted','maintenance_budget_exhausted'].includes(error.code)?86400-now()%86400:error.code==='burst_limit'?60:LIMITS.grant_seconds):null;return json({error:{code:error.code,message:error.message}},error.status,retry?{'Retry-After':String(retry)}:{});}onError(error);return json({error:{code:'service_error',message:'The community service is temporarily unavailable.'}},503);}}};
}
