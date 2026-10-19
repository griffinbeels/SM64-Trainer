import {canonical,requireThat,ServiceError} from './errors.js';
import {PROGRESS_LIMITS,PROGRESS_NOTICE,samePublisher,storageLimit} from './player-validation.js';

const stamp=()=>new Date().toISOString();
export async function progressState(s,uid){
  const r=await s.one('SELECT p.*,b.content_hash,b.captured_utc,b.synced_utc FROM progress_publications p LEFT JOIN progress_snapshots b ON b.uid=p.uid WHERE p.uid=?',uid)||{};
  return {v:2,uid,revision:r.revision||0,consent_revision:r.consent_revision||0,enabled:Boolean(r.enabled),visible:Boolean(r.visible&&r.content_hash),publisher:r.publisher_json?JSON.parse(r.publisher_json):null,notice_version:PROGRESS_NOTICE,content_hash:r.content_hash||null,captured_utc:r.captured_utc||null,synced_utc:r.synced_utc||null,limits:PROGRESS_LIMITS};
}
export function removeSnapshotSQL(s,uid,{profile=false}={}){
  const profileBytes=profile?'-COALESCE((SELECT byte_size FROM player_profiles WHERE uid=?),0)':'';
  return [s.sql('UPDATE progress_storage_budget SET bytes_used=bytes_used-COALESCE((SELECT byte_size FROM progress_snapshots WHERE uid=?),0)'+profileBytes+' WHERE id=1',uid,...(profile?[uid]:[])),
    s.sql('DELETE FROM progress_snapshots WHERE uid=?',uid),...(profile?[s.sql('DELETE FROM player_profiles WHERE uid=?',uid),s.sql('UPDATE progress_publications SET publisher_json=NULL,notice_version=NULL WHERE uid=?',uid)]:[])];
}
function ownerPredicate(){return "EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1 AND status='active' AND terms_version=? AND privacy_version=?)";}
export async function setControl(s,a,input,m){
  const old=await progressState(s,a.uid),enable=['enable','switch'].includes(input.action);
  requireThat(input.expected_revision===old.revision,'revision_conflict','Progress sharing changed. Refresh before trying again.',409);
  if(input.action==='enable')requireThat(!old.publisher||samePublisher(old.publisher,input.publisher),'publisher_conflict','Another installation or profile publishes this account. Review and switch deliberately.',409);
  const publisher=enable?input.publisher:old.publisher,erase=['withdraw','switch'].includes(input.action),at=stamp();
  const active=enable?' AND '+ownerPredicate():" AND EXISTS(SELECT 1 FROM accounts WHERE uid=? AND status!='deleted')";
  const args=enable?[a.uid,s.cfg.terms,s.cfg.privacy]:[a.uid];
  await s.tx('COALESCE((SELECT revision FROM progress_publications WHERE uid=?),0)=?'+active,[a.uid,input.expected_revision,...args],[
    s.sql('INSERT INTO progress_publications(uid,revision,consent_revision,enabled,visible,publisher_json,notice_version,changed_utc) VALUES(?,1,1,?,0,?,?,?) ON CONFLICT(uid) DO UPDATE SET revision=revision+1,consent_revision=consent_revision+1,enabled=excluded.enabled,visible=CASE WHEN ?=1 THEN 0 ELSE visible END,publisher_json=excluded.publisher_json,notice_version=excluded.notice_version,changed_utc=excluded.changed_utc',a.uid,Number(enable),publisher?canonical(publisher):null,enable?PROGRESS_NOTICE:null,at,Number(erase)),
    ...(erase?removeSnapshotSQL(s,a.uid):[]),s.receiptSQL(a.uid,'progress-control',m.key,m.digest,{acknowledged:true})],
  'revision_conflict','Progress sharing changed. Refresh before trying again.',409);
  return progressState(s,a.uid);
}
export async function saveSnapshot(s,a,input,content,m,env){
  const state=await progressState(s,a.uid);
  requireThat(state.enabled,'progress_disabled','Enable current-progress sharing before publishing.',409);
  requireThat(samePublisher(state.publisher,input.publisher),'publisher_conflict','A different installation or profile is selected.',409);
  requireThat(state.revision===input.expected_revision&&state.consent_revision===input.consent_revision,'revision_conflict','Progress sharing changed. Reconcile before publishing.',409);
  const budget=storageLimit(env),at=stamp(),publisherJSON=canonical(state.publisher);
  const predicate=ownerPredicate()+" AND EXISTS(SELECT 1 FROM progress_publications WHERE uid=? AND revision=? AND consent_revision=? AND enabled=1 AND publisher_json=? AND notice_version=?)";
  const values=[a.uid,s.cfg.terms,s.cfg.privacy,a.uid,input.expected_revision,input.consent_revision,publisherJSON,PROGRESS_NOTICE];
  // The byte reservation, replacement and revision receipt commit together.
  // A failed budget/CAS rolls back both the new body and any refund.
  const identical=state.visible&&state.content_hash===input.content_hash;
  const updates=identical?[]:[
    s.sql('UPDATE progress_storage_budget SET bytes_used=bytes_used-COALESCE((SELECT byte_size FROM progress_snapshots WHERE uid=?),0)+? WHERE id=1',a.uid,content.bytes),
    s.sql('INSERT INTO progress_snapshots(uid,content_json,content_hash,byte_size,captured_utc,synced_utc) VALUES(?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET content_json=excluded.content_json,content_hash=excluded.content_hash,byte_size=excluded.byte_size,captured_utc=excluded.captured_utc,synced_utc=excluded.synced_utc',a.uid,content.body,input.content_hash,content.bytes,input.captured_utc,at),
    s.sql('UPDATE progress_publications SET revision=revision+1,visible=1,changed_utc=? WHERE uid=?',at,a.uid)];
  try{
    await s.tx(predicate+' AND (SELECT bytes_used FROM progress_storage_budget WHERE id=1)-COALESCE((SELECT byte_size FROM progress_snapshots WHERE uid=?),0)+?<=?',
      [...values,a.uid,content.bytes,budget],[...updates,s.receiptSQL(a.uid,'progress-snapshot',m.key,m.digest,{acknowledged:true})],
      'revision_conflict','Progress changed. Refresh before trying again.',409);
  }catch(error){
    if(error instanceof ServiceError&&error.code==='revision_conflict'){
      const current=await progressState(s,a.uid);
      if(current.revision===input.expected_revision){
        const usage=await s.one('SELECT bytes_used-COALESCE((SELECT byte_size FROM progress_snapshots WHERE uid=?),0)+? AS next FROM progress_storage_budget WHERE id=1',a.uid,content.bytes);
        requireThat(usage.next<=budget,'storage_limit','Current-progress storage is full. Your prior publication is unchanged.',429);
      }
    }
    throw error;
  }
  return progressState(s,a.uid);
}
export async function readOwnProfile(s,uid){
  const row=await s.one('SELECT revision,bio,socials_json FROM player_profiles WHERE uid=?',uid);
  return {v:2,uid,revision:row?.revision||0,bio:row?.bio||'',socials:row?JSON.parse(row.socials_json):[]};
}
export async function saveProfile(s,a,input,m,env){
  const bytes=new TextEncoder().encode(canonical({bio:input.bio,socials:input.socials})).length,budget=storageLimit(env);
  try{
    await s.tx(ownerPredicate()+' AND COALESCE((SELECT revision FROM player_profiles WHERE uid=?),0)=? AND (SELECT bytes_used FROM progress_storage_budget WHERE id=1)-COALESCE((SELECT byte_size FROM player_profiles WHERE uid=?),0)+?<=?',
      [a.uid,s.cfg.terms,s.cfg.privacy,a.uid,input.expected_revision,a.uid,bytes,budget],[
        s.sql('UPDATE progress_storage_budget SET bytes_used=bytes_used-COALESCE((SELECT byte_size FROM player_profiles WHERE uid=?),0)+? WHERE id=1',a.uid,bytes),
        s.sql('INSERT INTO player_profiles(uid,revision,bio,socials_json,updated_utc,byte_size) VALUES(?,1,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET revision=revision+1,bio=excluded.bio,socials_json=excluded.socials_json,updated_utc=excluded.updated_utc,byte_size=excluded.byte_size',a.uid,input.bio,canonical(input.socials),stamp(),bytes),
        s.receiptSQL(a.uid,'player-profile',m.key,m.digest,{acknowledged:true})],
      'revision_conflict','Your player profile changed. Refresh before saving.',409);
  }catch(error){
    if(error instanceof ServiceError&&error.code==='revision_conflict'&&(await readOwnProfile(s,a.uid)).revision===input.expected_revision){
      const usage=await s.one('SELECT bytes_used-COALESCE((SELECT byte_size FROM player_profiles WHERE uid=?),0)+? AS next FROM progress_storage_budget WHERE id=1',a.uid,bytes);
      requireThat(usage.next<=budget,'storage_limit','Player profile storage is full. Your prior profile is unchanged.',429);
    }
    throw error;
  }
  return readOwnProfile(s,a.uid);
}
