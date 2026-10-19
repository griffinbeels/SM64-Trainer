import {boundedBody,canonical,hex64,integer,requireThat,ServiceError,sha256,uuid} from './errors.js';

export const PROGRESS_NOTICE='progress-1';
export const PROGRESS_LIMITS=Object.freeze({rows:4096,bytes:1048576});
const encoded=value=>new TextEncoder().encode(value).length;
const fields=(value,names)=>Boolean(value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===names.length&&names.every(name=>Object.hasOwn(value,name)));
export const boundedText=(value,limit)=>typeof value==='string'&&encoded(value)<=limit&&!/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value);
// The progress reader uses the same UTF-8/ASCII-control bounds as its saved-PB
// and portable-target contracts, independently of account display-name policy.
const progressText=(value,minimum=0)=>typeof value==='string'&&encoded(value)>=minimum&&encoded(value)<=256&&!/[\u0000-\u001f\p{Cs}]/u.test(value);
export function utc(value){
  if(typeof value!=='string'||value.length>40||Number(value.slice(0,4))===0||!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)$/.test(value))return false;
  const stamp=Date.parse(value);return Number.isFinite(stamp)&&new Date(stamp).toISOString().slice(0,10)===value.slice(0,10);
}
export async function readPlayerJSON(request,names){
  requireThat(request.headers.get('content-type')?.split(';')[0]==='application/json','content_type','Expected application/json.');
  let value;
  try{value=JSON.parse(new TextDecoder('utf-8',{fatal:true}).decode(await boundedBody(request,PROGRESS_LIMITS.bytes)));}
  catch(error){
    if(error instanceof ServiceError){if(error.code==='body_limit')requireThat(false,'snapshot_too_large','The complete snapshot exceeds the publication size limit.',413);throw error;}
    requireThat(false,'invalid_progress','Invalid progress document.');
  }
  requireThat(fields(value,names),'invalid_progress','Unexpected progress document fields.');return value;
}
export function publisher(value,{label=true}={}){
  requireThat(fields(value,label?['installation_id','profile_id','label']:['installation_id','profile_id'])&&uuid(value.installation_id)&&uuid(value.profile_id),'invalid_progress','Choose a publishing installation and profile.');
  if(label)requireThat(boundedText(value.label,128)&&value.label.trim(),'invalid_progress','Give the publishing profile a short label.');
  return value;
}
export const samePublisher=(a,b)=>Boolean(a&&b&&a.installation_id===b.installation_id&&a.profile_id===b.profile_id);
function starKey(value){
  const match=typeof value==='string'&&/^([1-9]\d?)-(\d)$/.exec(value);
  requireThat(match,'invalid_progress','Invalid portable star identity.');
  const course=Number(match[1]),star=Number(match[2]);
  // Structural identities mirror memory/addresses.py::star_count. They are
  // neither rank thresholds nor a requirement to exist in the ghost catalog.
  const count=course<=15?7:course===19?2:course<=24?1:0;
  requireThat(star<count,'invalid_progress','Unknown portable star.');return 'star:'+value;
}
function portable(value){
  const segment=value?.kind==='segment',names=segment?['kind','entity_key','name','signature']:['kind','entity_key','name'];
  requireThat(fields(value,names)&&['star','segment'].includes(value.kind),'invalid_progress','Invalid portable target fields.');
  requireThat(value.name===null||progressText(value.name),'invalid_progress','Invalid public target name.');
  if(!segment)return starKey(value.entity_key);
  requireThat(value.entity_key==='1'&&hex64(value.signature),'invalid_progress','A segment requires its portable signature and placeholder identity.');
  return 'segment:'+value.signature;
}
function recordedRegion(value){
  requireThat([null,'us','jp'].includes(value.game_version)&&[null,'us','jp'].includes(value.pb_version),'invalid_progress','Invalid recorded or effective PB region.');
  requireThat(value.game_version===null||value.pb_version===value.game_version,'invalid_progress','A recorded PB region must agree with its effective region.');
}
function row(value,index,identities){
  requireThat(fields(value,['target','strategy','clock','frames','game_version','pb_version','save_order','source_kind','source_cs','achieved_utc','saved_utc']),'invalid_progress','Invalid saved PB row.');
  const target=portable(value.target);
  requireThat(value.strategy===null||progressText(value.strategy,1),'invalid_progress','Invalid saved strategy.');
  requireThat(['igt','rta'].includes(value.clock)&&integer(value.frames,1,2592000),'invalid_progress','Invalid saved clock or frames.');
  recordedRegion(value);
  requireThat(value.save_order===index+1&&['played','manual','sheet','imported','legacy'].includes(value.source_kind),'invalid_progress','Invalid current PB order or source.');
  requireThat(value.source_cs===null||integer(value.source_cs,1,8640000),'invalid_progress','Invalid original source time.');
  requireThat([value.achieved_utc,value.saved_utc].every(date=>date===null||utc(date)),'invalid_progress','Invalid saved or achieved UTC time.');
  requireThat(value.source_kind==='played'||value.achieved_utc===null,'invalid_progress','A non-played source cannot claim an observed achievement time.');
  const identity=canonical([target,value.strategy,value.clock,value.game_version]);
  requireThat(!identities.has(identity),'invalid_progress','Current PB slots must be unique.');identities.add(identity);
}
function coverage(value,size){
  requireThat(fields(value,['total_rows','shared_rows','withheld_rows','unsupported_rows','complete']),'invalid_progress','Invalid progress coverage.');
  const counts=[value.total_rows,value.shared_rows,value.withheld_rows,value.unsupported_rows];
  requireThat(counts.every(n=>integer(n,0,2147483647))&&value.total_rows===value.shared_rows+value.withheld_rows+value.unsupported_rows&&value.shared_rows===size,'invalid_progress','Progress coverage disagrees with its rows.');
  requireThat(value.complete===(value.withheld_rows+value.unsupported_rows===0),'invalid_progress','Partial progress must be labelled as partial.');
}
export async function snapshot(input,uid){
  requireThat(input.uid===uid&&integer(input.expected_revision,0,Number.MAX_SAFE_INTEGER)&&integer(input.consent_revision,0,Number.MAX_SAFE_INTEGER)&&utc(input.captured_utc)&&hex64(input.content_hash),'invalid_progress','Invalid snapshot owner, revision or capture time.');publisher(input.publisher,{label:false});
  const value=input.content;
  requireThat(fields(value,['schema','default_rom','rows','coverage'])&&value.schema===1&&['us','jp'].includes(value.default_rom)&&Array.isArray(value.rows),'invalid_progress','Unsupported current-progress schema.');
  requireThat(value.rows.length<=PROGRESS_LIMITS.rows,'snapshot_too_large','The complete snapshot exceeds the publication row limit.',413);
  const seen=new Set();value.rows.forEach((item,index)=>row(item,index,seen));coverage(value.coverage,value.rows.length);
  const body=canonical(value),bytes=encoded(body);
  requireThat(bytes<=PROGRESS_LIMITS.bytes,'snapshot_too_large','The complete snapshot exceeds the publication size limit.',413);
  requireThat(await sha256(body)===input.content_hash,'invalid_progress','The progress content hash does not match.');
  return {body,bytes};
}
export function control(value){
  requireThat(integer(value.expected_revision,0,Number.MAX_SAFE_INTEGER)&&['enable','disable','withdraw','switch'].includes(value.action),'invalid_progress','Choose a progress publication action.');
  if(['enable','switch'].includes(value.action)){
    publisher(value.publisher);requireThat(value.notice_version===PROGRESS_NOTICE,'progress_consent_required','Accept the current progress-sharing notice.',403);
  }else{requireThat(value.notice_version===null,'invalid_progress','Disable or withdraw does not grant new consent.');if(value.publisher!==null)publisher(value.publisher);}
}
export function profile(value){
  const bio=typeof value.bio==='string'&&encoded(value.bio)<=1000&&boundedText(value.bio.replaceAll('\n',''),1000);
  requireThat(integer(value.expected_revision,0,Number.MAX_SAFE_INTEGER)&&bio&&Array.isArray(value.socials)&&value.socials.length<=5,'invalid_profile','Use a short bio and at most five social links.');
  for(const link of value.socials){
    requireThat(fields(link,['kind','url'])&&['twitch','youtube','website'].includes(link.kind)&&boundedText(link.url,1024),'invalid_profile','Invalid social link.');
    let url;try{url=new URL(link.url);}catch{requireThat(false,'invalid_profile','Use complete HTTPS social links.');}
    requireThat(url.protocol==='https:'&&!url.username&&!url.password,'invalid_profile','Use HTTPS links without credentials.');
    const host=url.hostname.toLowerCase();
    if(link.kind==='twitch')requireThat(['twitch.tv','www.twitch.tv'].includes(host),'invalid_profile','Use a Twitch profile link.');
    if(link.kind==='youtube')requireThat(['youtube.com','www.youtube.com','youtu.be'].includes(host),'invalid_profile','Use a YouTube link.');
  }
}
export function storageLimit(env){
  if(env.PROGRESS_STORAGE_BYTES===undefined)return 128*1024*1024;
  const value=Number(env.PROGRESS_STORAGE_BYTES);
  requireThat(/^\d+$/.test(env.PROGRESS_STORAGE_BYTES)&&integer(value,PROGRESS_LIMITS.bytes,1024*1024*1024),'configuration','Progress storage is unavailable.',503);return value;
}
