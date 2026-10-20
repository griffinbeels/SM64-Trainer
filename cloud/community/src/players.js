import {canonical,exactQuery,json,requireThat,sha256,uuid} from './errors.js';
import {control,profile,readPlayerJSON,snapshot} from './player-validation.js';
import {progressState,readOwnProfile,saveProfile,saveSnapshot,setControl} from './progress-store.js';
import {playerRecordings,playerSearch,publicPlayer,publicProgress} from './players-store.js';

const reply=value=>json({v:2,...value});
async function mutate(c,route,body,write,read){
  const {s,a,request}=c,key=request.headers.get('idempotency-key');
  requireThat(uuid(key),'idempotency_required','Use a UUID Idempotency-Key.');
  const digest=await sha256(canonical(body)),m={key,digest};
  if(await s.receipt(a.uid,route,key,digest))return reply(await read());
  try{return reply(await write(m));}
  catch(error){
    // A concurrent same-key retry may have looked up its receipt just before
    // the original committed. Report today's state instead of reviving its ACK.
    if(error.status===409&&await s.receipt(a.uid,route,key,digest))return reply(await read());
    throw error;
  }
}
async function progressControl(c){
  const body=await readPlayerJSON(c.request,['expected_revision','action','publisher','notice_version']);control(body);
  if(['enable','switch'].includes(body.action))c.s.active(c.a);
  return mutate(c,'progress-control',body,m=>setControl(c.s,c.a,body,m),()=>progressState(c.s,c.a.uid));
}
async function progressSnapshot(c){
  c.s.active(c.a);
  const body=await readPlayerJSON(c.request,['uid','expected_revision','consent_revision','publisher','captured_utc','content_hash','content']);
  const content=await snapshot(body,c.a.uid);
  return mutate(c,'progress-snapshot',body,m=>saveSnapshot(c.s,c.a,body,content,m,c.env),()=>progressState(c.s,c.a.uid));
}
async function playerProfile(c){
  c.s.active(c.a);
  if(c.request.method==='GET')return reply(await readOwnProfile(c.s,c.a.uid));
  const body=await readPlayerJSON(c.request,['expected_revision','bio','socials']);profile(body);
  return mutate(c,'player-profile',body,m=>saveProfile(c.s,c.a,body,m,c.env),()=>readOwnProfile(c.s,c.a.uid));
}
async function playerExport(c){
  const {s,a}=c;
  const row=await s.one('SELECT content_json,content_hash,captured_utc,synced_utc FROM progress_snapshots WHERE uid=?',a.uid);
  const exported=row?{content:JSON.parse(row.content_json),content_hash:row.content_hash,captured_utc:row.captured_utc,synced_utc:row.synced_utc}:null;
  return reply({uid:a.uid,profile:await readOwnProfile(s,a.uid),publication:await progressState(s,a.uid),snapshot:exported});
}
const OWNER_ROUTES=new Map([
  ['GET /v2/account/progress',c=>progressState(c.s,c.a.uid).then(reply)],
  ['PUT /v2/account/progress/control',progressControl],
  ['PUT /v2/account/progress/snapshot',progressSnapshot],
  ['GET /v2/account/player-profile',playerProfile],['PUT /v2/account/player-profile',playerProfile],
  ['GET /v2/account/player-export',playerExport],
]);
export function lapsedPlayerAccess(request,url){
  return ['GET /v2/account/progress','PUT /v2/account/progress/control','GET /v2/account/player-export'].includes(request.method+' '+url.pathname);
}
export async function playerRoute(c,catalog){
  const {request,url,s,a}=c;
  if(!url.pathname.startsWith('/v2/'))return null;
  const own=OWNER_ROUTES.get(request.method+' '+url.pathname);
  if(own){exactQuery(url,[]);return own(c);}
  s.active(a);requireThat(request.method==='GET','method_not_allowed','Use GET.',405);
  if(url.pathname==='/v2/players'){exactQuery(url,['prefix','cursor'],['prefix']);return reply(await playerSearch(s,a,url,c.env));}
  const match=/^\/v2\/players\/([0-9a-f-]{36})(\/progress|\/recordings)?$/.exec(url.pathname);
  requireThat(match&&uuid(match[1]),'not_found','Route not found.',404);
  exactQuery(url,match[2]==='/recordings'?['cursor']:[]);
  if(match[2]==='/recordings')return reply(await playerRecordings(s,a,match[1],url,c.env,await catalog(c.env)));
  return reply(await (match[2]==='/progress'?publicProgress(s,a,match[1]):publicPlayer(s,a,match[1])));
}
