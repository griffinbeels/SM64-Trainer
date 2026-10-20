import {requireThat} from './errors.js';
import {prefixRange} from './names.js';
import {encodeCursor,decodeUnboundedCursor} from './cursors.js';

const display=r=>r.status==='rename_required'?'Player '+r.uid.slice(0,8):r.name;
const visible="a.invited=1 AND a.status IN ('active','rename_required')";
function ownerSQL(s,a,values){
  const owners=s.visibleOwners(a);if(!owners)return '';
  values.push(...owners);return ' AND a.uid IN ('+owners.map(()=>'?').join(',')+')';
}
export async function player(s,a,uid){
  const values=[uid],owners=ownerSQL(s,a,values);
  const row=await s.one("SELECT a.uid,a.name,a.status,a.sheet_runner,a.sheet_verified,p.bio,p.socials_json,c.revision,c.visible,b.content_hash,b.captured_utc,b.synced_utc "+
    'FROM accounts a LEFT JOIN player_profiles p ON p.uid=a.uid LEFT JOIN progress_publications c ON c.uid=a.uid LEFT JOIN progress_snapshots b ON b.uid=a.uid WHERE a.uid=? AND '+visible+owners,...values);
  requireThat(row,'not_found','This player is unavailable.',404);return row;
}
function progress(row){
  const available=Boolean(row.visible&&row.content_hash);
  return {available,revision:row.revision||0,content_hash:available?row.content_hash:null,captured_utc:available?row.captured_utc:null,synced_utc:available?row.synced_utc:null};
}
export async function publicPlayer(s,a,uid){
  const r=await player(s,a,uid);
  return {v:2,uid,name:display(r),claimed_account:true,sheet_runner:r.sheet_verified?r.sheet_runner:null,sheet_verified:Boolean(r.sheet_verified),bio:r.bio||'',socials:r.socials_json?JSON.parse(r.socials_json):[],progress:progress(r)};
}
export async function publicProgress(s,a,uid){
  // Account visibility and current body are one query. No cached header can
  // authorize a body withdrawn while the caller was opening the profile.
  const values=[uid],owners=ownerSQL(s,a,values);
  const body=await s.one('SELECT b.*,p.revision FROM progress_snapshots b JOIN progress_publications p ON p.uid=b.uid JOIN accounts a ON a.uid=b.uid WHERE b.uid=? AND p.visible=1 AND '+visible+owners,...values);
  requireThat(body,'progress_unavailable','This player has no published current progress.',404);
  return {v:2,uid,revision:body.revision,content_hash:body.content_hash,captured_utc:body.captured_utc,synced_utc:body.synced_utc,content:JSON.parse(body.content_json)};
}
const searchAfter=value=>Array.isArray(value)&&value.length===2&&typeof value[0]==='string'&&value[0].length<=256&&typeof value[1]==='string'&&value[1].length===36;
const recordingAfter=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
export async function playerSearch(s,a,url,env){
  const prefix=prefixRange(url.searchParams.get('prefix')),gate=await s.one('SELECT global_epoch FROM settings WHERE id=1');
  const identity={kind:'players',viewer:a.uid,prefix,globalEpoch:gate.global_epoch,visibleOwners:s.visibleOwners(a)};
  const cursor=url.searchParams.get('cursor'),previous=cursor===null?null:await decodeUnboundedCursor(env.CURSOR_SECRET,cursor,identity,searchAfter);
  const values=[prefix.start,prefix.end];let continuation='';
  if(previous){continuation=' AND (a.name_key,a.uid)>(?,?)';values.push(...previous.after);}
  const owners=ownerSQL(s,a,values);values.push(21);
  const found=await s.rows('SELECT a.uid,a.name,a.name_key,a.status FROM accounts a INDEXED BY account_names WHERE a.name_key>=? AND a.name_key<? AND '+visible+continuation+owners+' ORDER BY a.name_key,a.uid LIMIT ?',...values);
  const rows=found.slice(0,20),complete=found.length<=20;
  const next=complete?null:await encodeCursor(env.CURSOR_SECRET,identity,[rows.at(-1).name_key,rows.at(-1).uid],(previous?.page||0)+1);
  return {v:2,players:rows.map(r=>({uid:r.uid,name:display(r),claimed_account:true})),next_cursor:next,complete};
}
function inventoryGate(s,a,uid){
  const values=[uid],permitted=ownerSQL(s,a,values);
  return s.one('SELECT a.uid,a.recording_epoch,g.generation,g.global_epoch,g.migration_generation FROM accounts a CROSS JOIN settings g WHERE a.uid=? AND g.id=1 AND '+visible+permitted,...values);
}
function currentInventory(gate,model){
  requireThat(gate,'not_found','This player is unavailable.',404);
  requireThat(gate.generation===model.generation&&!gate.migration_generation,'generation_changed','Catalog changed.',409);
}
export async function playerRecordings(s,a,uid,url,env,model){
  const gate=await inventoryGate(s,a,uid);currentInventory(gate,model);
  const identity={kind:'player-recordings',viewer:a.uid,owner:uid,ownerEpoch:gate.recording_epoch,generation:model.generation,globalEpoch:gate.global_epoch,visibleOwners:s.visibleOwners(a)};
  const cursor=url.searchParams.get('cursor'),previous=cursor===null?null:await decodeUnboundedCursor(env.CURSOR_SECRET,cursor,identity,recordingAfter);
  const values=[uid,model.generation];let continuation='';
  if(previous){continuation=' AND r.id>?';values.push(previous.after);}
  const owners=ownerSQL(s,a,values);values.push(21);
  const found=await s.rows('SELECT r.*,a.name,a.status FROM recordings r INDEXED BY recording_owner JOIN accounts a ON a.uid=r.uid JOIN assets x ON x.key=r.asset_key WHERE r.uid=? AND r.generation=? AND r.published=1 AND '+visible+" AND x.state='stored' AND EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=r.id)"+continuation+owners+' ORDER BY r.id LIMIT ?',...values);
  // Combining the initial owner/settings gates pays for this final point read
  // without increasing the query count. A commit during the page cannot turn
  // an obsolete keyset into a falsely complete inventory.
  const current=await inventoryGate(s,a,uid);currentInventory(current,model);
  requireThat(current.recording_epoch===gate.recording_epoch&&current.global_epoch===gate.global_epoch,'invalid_cursor','The player inventory changed. Reload its first page.');
  const rows=found.slice(0,20),complete=found.length<=20;
  const next=complete?null:await encodeCursor(env.CURSOR_SECRET,identity,rows.at(-1).id,(previous?.page||0)+1);
  return {v:2,uid,generation:model.generation,recordings:rows.map(r=>s.metadata(r)),next_cursor:next,complete};
}
