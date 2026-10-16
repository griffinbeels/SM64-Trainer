import {ServiceError,requireThat,fail,sha256,canonical} from "./errors.js";
import {LIMITS} from "./config.js";
import {checkedName} from "./names.js";
const sec=()=>Math.floor(Date.now()/1000);
// Identical bytes can legitimately have different strategy/timing metadata.
// Deduplication and publication must agree on the full immutable identity.
const recordingID=r=>sha256(canonical([r.uid,r.target_key,r.clock,r.rom,r.strategy,r.frames,r.raw_sha256]));
export class Store {
  constructor(db,cfg) {this.db=db;this.cfg=cfg;}
  sql(sql,...values) {return this.db.prepare(sql).bind(...values);}
  async one(sql,...values) {return this.sql(sql,...values).first();}
  async rows(sql,...values) {return (await this.sql(sql,...values).all()).results;}
  async tx(predicate,values,statements,code="conflict",message="The request changed; try again.",status=409) {
    const id=crypto.randomUUID();
    try {
      return await this.db.batch([
        this.sql("INSERT INTO guards(id,allowed) SELECT ?, CASE WHEN ("+predicate+") THEN 1 ELSE 0 END",id,...values),
        ...statements,this.sql("DELETE FROM guards WHERE id=?",id)]);
    } catch(error) {
      if(/CHECK constraint failed:.*(?:ops_limit|upload_limit|class_a|class_b)/.test(String(error)))
        fail(429,"budget_exhausted","The community daily budget is exhausted.");
      if(/CHECK constraint failed|UNIQUE constraint failed|FOREIGN KEY constraint failed/.test(String(error))) fail(status,code,message);
      throw error;
    }
  }
  async account(identity) {
    requireThat(identity.issuer===this.cfg.issuer,"invalid_issuer","This identity provider is not configured.",401);
    const uid=identity.sub;
    // Public beta eligibility is transient until deliberate profile submission.
    // Look up the UID across issuers so an existing denied owner never rejoins.
    const stored=await this.one("SELECT * FROM accounts WHERE uid=?",uid);
    requireThat(!stored||stored.issuer===identity.issuer,"account_unavailable","This account is unavailable.",403);
    return stored||
      {uid,issuer:identity.issuer,subject:uid,ephemeral:true,invited:Number(this.cfg.publicBetaSignup===true),status:"new",name:null,
        sharing:0,terms_version:null,privacy_version:null,sheet_runner:null,sheet_verified:0};
  }
  publicAccount(a) {
    return {uid:a.uid,name:a.name,status:(!a.invited&&!["deleted","suspended"].includes(a.status)?"not_invited":a.status),sharing:Boolean(a.sharing),
      terms_version:a.terms_version,privacy_version:a.privacy_version,sheet_runner:a.sheet_runner,sheet_verified:Boolean(a.sheet_verified)};
  }
  active(a,profile=false) {
    if(a?.status==="deleted") fail(403,"deleted","This account has been deleted.");
    if(a?.status==="suspended") fail(403,"suspended","This account is suspended.");
    if(!a?.invited) fail(403,"not_invited","This account is not invited to the private beta.");
    // An ephemeral public-beta identity can only prepare its initial profile.
    requireThat(a.status==="active" || (profile&&["new","rename_required"].includes(a.status)),
      a.status==="rename_required"?"rename_required":"account_unavailable","Your account is not admitted for this action.",403);
    if(!profile) requireThat(a.terms_version===this.cfg.terms && a.privacy_version===this.cfg.privacy,
      "consent_required","Accept the current policies.",403);
  }
  // Only authenticated UID ownership or an immutable operator publisher list
  // grants visibility. Renaming an account can never imitate an approved author.
  visibleOwners(a) {return this.cfg.sharedPublishers===null?null:[...new Set([a.uid,...this.cfg.sharedPublishers])].sort();}
  async redeem(identity,token) {
    requireThat(typeof token==='string'&&/^[A-Za-z0-9_-]{43}$/.test(token),'invite_unavailable','This invitation is unavailable.',403);
    const digest=await sha256(token),uid=identity.sub,now=sec();
    const prior=await this.account(identity);
    requireThat(!['deleted','suspended'].includes(prior.status),'account_unavailable','This account cannot use an invitation.',403);
    requireThat(prior.ephemeral||prior.invited,'invite_unavailable','This invitation is unavailable.',403);
    const invitation=await this.one('SELECT * FROM beta_invitations WHERE token_sha256=?',digest);
    requireThat(invitation&&!invitation.revoked&&(invitation.expires_at>now||invitation.reusable===1&&invitation.expires_at===0)&&
      (invitation.reusable===1||!invitation.claimed_uid||invitation.claimed_uid===uid),'invite_unavailable','This invitation is unavailable.',403);
    await this.tx("(SELECT enabled FROM settings WHERE id=1)=1 AND EXISTS(SELECT 1 FROM beta_invitations WHERE token_sha256=? AND revoked=0 AND "+
      "(expires_at>? OR (reusable=1 AND expires_at=0)) AND (reusable=1 OR claimed_uid IS NULL OR claimed_uid=?)) AND "+
      "NOT EXISTS(SELECT 1 FROM accounts WHERE uid=? AND (issuer!=? OR invited=0 OR status IN ('deleted','suspended'))) AND "+
      "((SELECT uncapped FROM beta_admission WHERE id=1)=1 OR EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1) OR "+
      "(SELECT COUNT(*) FROM accounts)<(SELECT max_members FROM beta_admission WHERE id=1))",[digest,now,uid,uid,this.cfg.issuer,uid],[
      // The epoch predicate and insert share one serialized transaction. Two
      // first joins for the same UID increment once, without resetting a profile.
      this.sql('UPDATE settings SET global_epoch=global_epoch+1 WHERE id=1 AND NOT EXISTS(SELECT 1 FROM accounts WHERE uid=?)',uid),
      this.sql("INSERT INTO accounts(uid,issuer,subject,invited,created_at,updated_at) VALUES(?,?,?,1,?,?) ON CONFLICT(uid) DO NOTHING",uid,this.cfg.issuer,uid,now,now),
      this.sql('UPDATE beta_invitations SET claimed_uid=?,claimed_at=? WHERE token_sha256=? AND reusable=0 AND claimed_uid IS NULL',uid,now,digest)],
      'invite_unavailable','This invitation is unavailable.',403);
    return this.publicAccount(await this.account(identity));
  }
  async authEntryTicket() {
    const day=new Date().toISOString().slice(0,10);
    try {
      // Count before reading an account. Unknown callers cannot create quota
      // dimensions, and changing UID or route cannot bypass this fixed row.
      const result=await this.sql('UPDATE auth_entry_budget SET day=MAX(day,?),ops=CASE WHEN day<? THEN 1 ELSE ops+1 END WHERE id=1',day,day).run();
      requireThat(result.meta?.changes===1,'configuration','Account entry is unavailable.',503);
    } catch(error) {
      if(/CHECK constraint failed/.test(String(error)))fail(429,'budget_exhausted','The community daily budget is exhausted.');
      throw error;
    }
  }
  async ticket(uid,{uploadBytes=0,classA=0,classB=0,maintenance=false}={}) {
    const day=new Date().toISOString().slice(0,10);
    if(maintenance){
      // Collection must retain its own bounded capacity after normal admission
      // is disabled or a player deliberately spends their entire daily allowance.
      requireThat(uploadBytes===0&&Number.isSafeInteger(classA)&&classA>=0&&Number.isSafeInteger(classB)&&classB>=0);
      try{await this.db.batch([
        this.sql('INSERT INTO maintenance_quotas(day) VALUES(?) ON CONFLICT DO NOTHING',day),
        this.sql('UPDATE maintenance_quotas SET ops=ops+1,class_a=class_a+?,class_b=class_b+? WHERE day=?',classA,classB,day)]);
      }catch(error){if(/CHECK constraint failed/.test(String(error)))fail(429,'maintenance_budget_exhausted','Maintenance will retry later.');throw error;}
      return;
    }
    // Every admitted call consumes an exact account and global ticket, even cache hits.
    // Asset downloads conservatively consume a Class B ticket even on cache hits.
    const statements=this.ticketSQL(uid,{uploadBytes,classA,classB,maintenance});
    try {
      // D1 batch rolls back both counters AND newly inserted quota rows when
      // either CHECK fails. Denied new identities cannot grow quota history.
      await this.db.batch(statements);
    } catch(error) {
      if(/CHECK constraint failed/.test(String(error))) fail(429,"budget_exhausted","The community daily budget is exhausted.");
      throw error;
    }
  }
  // Initial public signup includes these tickets in the account transaction.
  ticketSQL(uid,{uploadBytes=0,classA=0,classB=0,maintenance=false}={}) {
    const day=new Date().toISOString().slice(0,10);
    return [
      this.sql("INSERT INTO quotas(owner,day,ops_limit,upload_limit) VALUES(?,?,?,?) ON CONFLICT DO NOTHING",uid,day,LIMITS.account_ops_day,LIMITS.upload_bytes_day),
      this.sql("INSERT INTO quotas(owner,day,ops_limit,upload_limit) VALUES('*',?,?,?) ON CONFLICT DO NOTHING",day,LIMITS.global_ops_day,LIMITS.global_upload_bytes_day),
      this.sql("UPDATE quotas SET ops=CASE WHEN (SELECT enabled FROM settings WHERE id=1)=1 OR ?=1 THEN ops+1 ELSE ops_limit+1 END,"+
        "uploaded=uploaded+?,class_a=class_a+?,class_b=class_b+? WHERE (owner=? OR owner='*') AND day=?",
        Number(maintenance),uploadBytes,classA,classB,uid,day)];
  }
  async receipt(uid,route,key,digest) {
    const row=await this.one("SELECT * FROM mutation_receipts WHERE uid=? AND route=? AND request_key=?",uid,route,key);
    if(row) {requireThat(row.digest===digest,"idempotency_conflict","This request key was already used for different data.",409);return JSON.parse(row.response_json);}
    return null;
  }
  receiptSQL(uid,route,key,digest,response) {
    return this.sql("INSERT INTO mutation_receipts VALUES(?,?,?,?,?,?)",uid,route,key,digest,JSON.stringify(response),sec());
  }
  async profile(a,input,key,digest) {
    this.active(a,true); const named=checkedName(input.name);
    requireThat(typeof input.sharing==="boolean" && input.terms_version===this.cfg.terms && input.privacy_version===this.cfg.privacy &&
      (input.sheet_runner===null || (typeof input.sheet_runner==="string" && input.sheet_runner.length<=128)),
      "invalid_profile","Accept current policies and provide a valid name.");
    const response={...this.publicAccount({...a,...input,name:named.name,status:"active",sheet_verified:input.sheet_runner===a.sheet_runner?a.sheet_verified:0})};
    const joining=a.ephemeral&&this.cfg.publicBetaSignup;
    const existing="EXISTS(SELECT 1 FROM accounts WHERE uid=? AND issuer=? AND invited=1 AND status IN ('new','active','rename_required'))";
    const predicate=joining?"(SELECT enabled FROM settings WHERE id=1)=1 AND ("+existing+" OR (NOT EXISTS(SELECT 1 FROM accounts WHERE uid=?) AND "+
      "((SELECT uncapped FROM beta_admission WHERE id=1)=1 OR (SELECT COUNT(*) FROM accounts)<(SELECT max_members FROM beta_admission WHERE id=1))))":existing;
    const values=joining?[a.uid,this.cfg.issuer,a.uid]:[a.uid,this.cfg.issuer];
    await this.tx(predicate,values,[
      ...(joining?[
        this.sql("INSERT INTO accounts(uid,issuer,subject,invited,created_at,updated_at) VALUES(?,?,?,1,?,?) ON CONFLICT(uid) DO NOTHING",a.uid,this.cfg.issuer,a.uid,sec(),sec()),
        ...this.ticketSQL(a.uid)]:[]),
      this.sql("UPDATE accounts SET name=?,name_key=?,status='active',sharing=?,terms_version=?,privacy_version=?,sheet_verified=CASE WHEN sheet_runner IS ? THEN sheet_verified ELSE 0 END,sheet_runner=?,updated_at=? WHERE uid=?",
        named.name,named.key,Number(input.sharing),input.terms_version,input.privacy_version,input.sheet_runner,input.sheet_runner,sec(),a.uid),
      this.sql("UPDATE settings SET global_epoch=global_epoch+1 WHERE id=1"),
      this.receiptSQL(a.uid,"profile",key,digest,response)],"name_unavailable",joining?"This name is already held or beta admission is unavailable.":"This name is already held or the account changed.");
    return response;
  }
  async rename(a,input,key,digest) {
    this.active(a,true);const named=checkedName(input.name);
    const response={...this.publicAccount({...a,name:named.name,status:"active"})};
    await this.tx("EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1 AND status IN ('active','rename_required'))",[a.uid],[
      this.sql("UPDATE accounts SET name=?,name_key=?,status='active',updated_at=? WHERE uid=?",named.name,named.key,sec(),a.uid),
      this.sql("UPDATE settings SET global_epoch=global_epoch+1 WHERE id=1"),this.receiptSQL(a.uid,"name",key,digest,response)],
      "name_unavailable","This name is already held or the account changed.");
    return response;
  }
  async admit(a,input,catalog,key,digest,grading) {
    this.active(a);requireThat(a.sharing,"sharing_disabled","Enable sharing before uploading.",403);
    // A run past the ghost ceiling never becomes a ghost. The answer is the
    // quiet refusal every deployed trainer already accepts; no budget is spent.
    if(grading.aboveCeiling) return {disposition:"not_faster"};
    const old=await this.one("SELECT * FROM grants WHERE uid=? AND request_key=?",a.uid,key);
    if(old) {requireThat(old.request_digest===digest,"idempotency_conflict","Upload key already binds different data.",409);return this.grantResponse(old);}
    const recordingId=await recordingID({...input,uid:a.uid,target_key:grading.key});
    const already=await this.one("SELECT r.id FROM recordings r JOIN assets x ON x.key=r.asset_key WHERE r.id=? AND r.generation=? AND r.published=1 AND x.state='stored' AND EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=r.id)",recordingId,catalog.generation);
    if(already) return {disposition:"already_present",recording_id:already.id};
    const winner=await this.one("SELECT frames FROM winners WHERE uid=? AND target_key=? AND clock=? AND rom=? AND strategy=? AND generation=? AND step=?",
      a.uid,grading.key,input.clock,input.rom,input.strategy,catalog.generation,grading.step);
    if(winner && winner.frames<=input.frames) return {disposition:"not_faster"};
    // Admission consumption never refunds daily bytes or operations.
    await this.ticket(a.uid,{uploadBytes:input.encoded_bytes,classA:3});
    const id=crypto.randomUUID(),expires=sec()+LIMITS.grant_seconds,assetKey=a.uid+"/"+input.raw_sha256+"/"+id;
    await this.tx(
      "EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1 AND status='active' AND sharing=1 AND terms_version=? AND privacy_version=? AND pending<? AND bytes_used+bytes_reserved+?<=?) AND "+
      "(SELECT bytes_used+bytes_reserved FROM storage_budget WHERE id=1)+?<=? AND "+
      "(SELECT generation FROM settings WHERE id=1)=? AND (SELECT migration_generation FROM settings WHERE id=1) IS NULL",
      [a.uid,this.cfg.terms,this.cfg.privacy,LIMITS.pending_uploads,input.encoded_bytes,LIMITS.retained_bytes,
       input.encoded_bytes,LIMITS.global_bytes,catalog.generation],[
      this.sql("UPDATE accounts SET bytes_reserved=bytes_reserved+?,pending=pending+1 WHERE uid=?",input.encoded_bytes,a.uid),
      this.sql("UPDATE storage_budget SET bytes_reserved=bytes_reserved+? WHERE id=1",input.encoded_bytes),
      this.sql("INSERT INTO grants(id,uid,request_key,request_digest,target_key,target_json,clock,rom,strategy,frames,raw_sha256,raw_bytes,encoded_sha256,encoded_bytes,generation,step,overall_step,state,expires_at,asset_key,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?,?)",
        id,a.uid,key,digest,grading.key,canonical(grading.entry.target),input.clock,input.rom,input.strategy,input.frames,
        input.raw_sha256,input.raw_bytes,input.encoded_sha256,input.encoded_bytes,catalog.generation,grading.step,grading.overallStep,expires,assetKey,sec())],
      "storage_limit","Upload reservation unavailable.",429);
    return {disposition:"upload",upload_id:id,expires_at:expires};
  }
  grantResponse(g) {
    if(g.state==="complete") return {disposition:g.disposition,recording_id:g.recording_id};
    requireThat(g.state==="pending" && g.expires_at>sec(),"upload_expired","Upload grant is no longer available.",409);
    return {disposition:"upload",upload_id:g.id,expires_at:g.expires_at};
  }
  async beginUpload(a,id) {
    const g=await this.one("SELECT * FROM grants WHERE id=? AND uid=?",id,a.uid);
    requireThat(g,"not_found","Upload not found.",404);
    if(g.state==="complete"||g.state==="stored") return g;
    await this.tx("EXISTS(SELECT 1 FROM grants WHERE id=? AND uid=? AND state='pending' AND expires_at>?) AND "+
      "EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1 AND status='active' AND sharing=1 AND terms_version=? AND privacy_version=?) AND "+
      "(SELECT generation FROM settings WHERE id=1)=? AND (SELECT migration_generation FROM settings WHERE id=1) IS NULL",
      [id,a.uid,sec(),a.uid,this.cfg.terms,this.cfg.privacy,g.generation],
      [this.sql("UPDATE grants SET state='receiving' WHERE id=?",id)],"upload_unavailable","Upload grant changed or was already consumed.");
    return {...g,state:"receiving"};
  }
  async release(g) {
    // Only unstored reservations refund. The SQL predicate makes repeated cleanup harmless.
    await this.tx("EXISTS(SELECT 1 FROM grants WHERE id=? AND state IN ('pending','receiving','recovering'))",[g.id],[
      this.sql("UPDATE accounts SET bytes_reserved=bytes_reserved-?,pending=pending-1 WHERE uid=?",g.encoded_bytes,g.uid),
      this.sql("UPDATE storage_budget SET bytes_reserved=bytes_reserved-? WHERE id=1",g.encoded_bytes),
      this.sql("UPDATE grants SET state='failed' WHERE id=?",g.id)]);
  }
  async stored(g) {
    await this.tx("EXISTS(SELECT 1 FROM grants WHERE id=? AND state IN ('receiving','recovering'))",[g.id],[
      this.sql("INSERT INTO assets(key,uid,raw_sha256,encoded_sha256,encoded_bytes,state,created_at) VALUES(?,?,?,?,?,'stored',?)",
        g.asset_key,g.uid,g.raw_sha256,g.encoded_sha256,g.encoded_bytes,sec()),
      this.sql("UPDATE accounts SET bytes_reserved=bytes_reserved-?,bytes_used=bytes_used+?,pending=pending-1 WHERE uid=?",g.encoded_bytes,g.encoded_bytes,g.uid),
      this.sql("UPDATE storage_budget SET bytes_reserved=bytes_reserved-?,bytes_used=bytes_used+? WHERE id=1",g.encoded_bytes,g.encoded_bytes),
      this.sql("UPDATE grants SET state='stored' WHERE id=?",g.id)]);
  }
  async publish(g) {
    requireThat(!(await this.one('SELECT migration_generation FROM settings WHERE id=1')).migration_generation,'generation_changed','A catalog migration is in progress.',409);
    const id=await recordingID(g);
    const predicate="EXISTS(SELECT 1 FROM grants WHERE id=? AND state='stored') AND "+
      "EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1 AND status='active' AND sharing=1 AND terms_version=? AND privacy_version=?) AND "+
      "(SELECT generation FROM settings WHERE id=1)=? AND (SELECT migration_generation FROM settings WHERE id=1) IS NULL AND NOT EXISTS(SELECT 1 FROM winners WHERE uid=? AND target_key=? AND clock=? AND rom=? AND strategy=? AND generation=? AND step=? AND frames<=?)";
    try {await this.tx(predicate,
      [g.id,g.uid,this.cfg.terms,this.cfg.privacy,g.generation,g.uid,g.target_key,g.clock,g.rom,g.strategy,g.generation,g.step,g.frames],[
      // Withdrawal can leave the immutable recording row while R2 collection
      // is pending. Re-sharing relinks only that withdrawn identity to newly
      // validated bytes; its retired asset remains charged until collection.
      this.sql("INSERT INTO recordings(id,uid,asset_key,target_key,target_json,clock,rom,strategy,frames,raw_sha256,raw_bytes,encoded_sha256,encoded_bytes,generation,step,overall_step,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET asset_key=excluded.asset_key,encoded_sha256=excluded.encoded_sha256,encoded_bytes=excluded.encoded_bytes,generation=excluded.generation,step=excluded.step,overall_step=excluded.overall_step,published=1 WHERE recordings.published=0",
        id,g.uid,g.asset_key,g.target_key,g.target_json,g.clock,g.rom,g.strategy,g.frames,g.raw_sha256,g.raw_bytes,g.encoded_sha256,g.encoded_bytes,g.generation,g.step,g.overall_step,sec()),
      this.sql("INSERT INTO winners VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(uid,target_key,clock,rom,strategy,generation,step) DO UPDATE SET recording_id=excluded.recording_id,frames=excluded.frames WHERE excluded.frames<winners.frames",
        g.uid,g.target_key,g.clock,g.rom,g.strategy,g.generation,g.step,id,g.frames),
      this.sql("UPDATE grants SET state='complete',recording_id=?,disposition='published' WHERE id=?",id,g.id),
      this.sql("INSERT INTO scope_epochs(scope,epoch) VALUES(?,1) ON CONFLICT(scope) DO UPDATE SET epoch=epoch+1",g.target_key)]);
      return {disposition:"published",recording_id:id};
    } catch(error) {
      if(!(error instanceof ServiceError)||error.code!=="conflict") throw error;
      requireThat(!(await this.one('SELECT migration_generation FROM settings WHERE id=1')).migration_generation,'generation_changed','A catalog migration is in progress.',409);
      // Keep physical losing bytes charged until reconciler proves R2 deletion.
      await this.sql("UPDATE grants SET state='complete',disposition='not_faster' WHERE id=? AND state='stored'",g.id).run();
      return {disposition:"not_faster"};
    }
  }
  async gates(a,scope) {
    this.active(a);
    const value=await this.one("SELECT s.generation,s.global_epoch,COALESCE(e.epoch,0) AS scope_epoch FROM settings s LEFT JOIN scope_epochs e ON e.scope=? WHERE s.id=1",scope);
    requireThat(value,"service_disabled","Service is unavailable.",503);return value;
  }
  metadata(r) {
    return {id:r.id,player_id:r.uid,player_name:r.status==="rename_required"?"Player "+r.uid.slice(0,8):r.name,
      target:JSON.parse(r.target_json),strategy:r.strategy,clock:r.clock,game_version:r.rom,frames:r.frames,
      raw_sha256:r.raw_sha256,raw_bytes:r.raw_bytes,encoded_sha256:r.encoded_sha256,encoded_bytes:r.encoded_bytes,
      codec:"ghc1",generation:r.generation,step:r.response_step??r.step,provenance:"community-unverified",structurally_valid:true,full_start:true};
  }
  async recording(a,id) {
    this.active(a);
    const owners=this.visibleOwners(a),ownerSQL=owners===null?'':' AND r.uid IN ('+owners.map(()=>'?').join(',')+')';
    const r=await this.one("SELECT r.*,a.name,a.status,x.state AS asset_state FROM recordings r JOIN accounts a ON a.uid=r.uid JOIN assets x ON x.key=r.asset_key "+
      "WHERE r.id=? AND r.published=1 AND a.invited=1 AND a.status IN ('active','rename_required') AND x.state='stored' AND "+
      "(EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=r.id) OR EXISTS(SELECT 1 FROM pins p WHERE p.recording_id=r.id AND p.uid=? AND p.expires_at>?))"+ownerSQL,id,a.uid,sec(),...(owners||[]));
    requireThat(r,"not_found","Recording unavailable.",404);return r;
  }
  async candidates(query) {
    const column=query.scope==="strategy"?"r.step":"r.overall_step";
    const strategySQL=query.scope==="strategy"?" AND r.strategy=?":"";
    const cursorSQL=query.after?" AND ("+column+">? OR ("+column+"=? AND (r.frames>? OR (r.frames=? AND r.id>?))))":"";
    const values=[query.key,query.clock,query.rom,query.generation,query.minStep,query.maxStep];
    if(query.scope==="strategy") values.push(query.strategy);
    if(query.prefix) values.push(query.prefix.start,query.prefix.end);
    if(query.after) values.push(query.after[0],query.after[0],query.after[1],query.after[1],query.after[2]);
    if(query.visibleOwners)values.push(...query.visibleOwners);
    values.push(query.pageSize+1);
    return this.rows("SELECT r.*,a.name,a.status,"+column+" AS response_step FROM recordings r JOIN accounts a ON a.uid=r.uid "+
      "WHERE r.target_key=? AND r.clock=? AND r.rom=? AND r.generation=? AND "+column+" BETWEEN ? AND ?"+
      strategySQL+(query.prefix?" AND a.name_key>=? AND a.name_key<?":"")+cursorSQL+
      " AND r.published=1 AND a.invited=1 AND a.status IN ('active','rename_required') AND EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=r.id)"+
      (query.visibleOwners?' AND r.uid IN ('+query.visibleOwners.map(()=>'?').join(',')+')':'')+
      " ORDER BY "+column+",r.frames,r.id LIMIT ?",...values);
  }
  async poolCandidates(query) {
    const rows=await this.candidates(query);
    if(rows.length||query.after)return rows;
    const column=query.scope==='strategy'?'r.step':'r.overall_step',strategySQL=query.scope==='strategy'?' AND r.strategy=?':'',
      values=[query.key,query.clock,query.rom,query.generation];
    if(query.scope==='strategy')values.push(query.strategy);
    if(query.visibleOwners)values.push(...query.visibleOwners);
    values.push(4);
    return this.rows("SELECT r.*,a.name,a.status,"+column+" AS response_step FROM recordings r JOIN accounts a ON a.uid=r.uid "+
      "WHERE r.target_key=? AND r.clock=? AND r.rom=? AND r.generation=?"+strategySQL+
      " AND r.published=1 AND a.invited=1 AND a.status IN ('active','rename_required') AND EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=r.id)"+
      (query.visibleOwners?' AND r.uid IN ('+query.visibleOwners.map(()=>'?').join(',')+')':'')+
      ' ORDER BY r.frames,r.id LIMIT ?',...values);
  }
  async searchCandidates(query) {
    // Bound name-index exploration independently of returned rows. A prefix
    // with no matching ghosts cannot scan every player/recording in one call.
    const after=query.after,values=[query.prefix.start,query.prefix.end];
    let suffix="";
    if(after){suffix=" AND (name_key>? OR (name_key=? AND uid>=?))";values.push(after[0],after[0],after[1]);}
    if(query.visibleOwners){suffix+=' AND uid IN ('+query.visibleOwners.map(()=>'?').join(',')+')';values.push(...query.visibleOwners);}
    const names=await this.rows("SELECT name_key,uid FROM accounts INDEXED BY account_names WHERE name_key>=? AND name_key<? AND invited=1 AND status IN ('active','rename_required')"+suffix+" ORDER BY name_key,uid LIMIT 65",...values);
    const scanned=names.slice(0,64),column=query.scope==="strategy"?"r.step":"r.overall_step";
    if(!scanned.length)return {rows:[],more:false,after:null};
    const args=[...scanned.map(a=>a.uid),query.key,query.clock,query.rom,query.generation,query.minStep,query.maxStep];
    const strategySQL=query.scope==="strategy"?" AND r.strategy=?":"";if(query.scope==="strategy")args.push(query.strategy);
    const cursorSQL=after?" AND (a.name_key>? OR (a.name_key=? AND (a.uid>? OR (a.uid=? AND r.id>?))))":"";
    if(after)args.push(after[0],after[0],after[1],after[1],after[2]);args.push(query.pageSize+1);
    let rows=await this.rows("SELECT r.*,a.name,a.name_key,a.status,"+column+" AS response_step FROM recordings r INDEXED BY recording_search JOIN accounts a ON a.uid=r.uid WHERE r.uid IN ("+scanned.map(()=>"?").join(",")+") AND r.target_key=? AND r.clock=? AND r.rom=? AND r.generation=? AND "+column+" BETWEEN ? AND ?"+strategySQL+cursorSQL+" AND r.published=1 AND a.invited=1 AND a.status IN ('active','rename_required') AND EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=r.id) ORDER BY a.name_key,a.uid,r.id LIMIT ?",...args);
    const extra=rows.length>query.pageSize;rows=rows.slice(0,query.pageSize);
    const last=extra?rows.at(-1):scanned.at(-1);
    return {rows,more:extra||names.length>64,after:[last.name_key,last.uid,extra?last.id:"f".repeat(64)]};
  }
  async pins(a,ids,key,digest) {
    this.active(a);for(const id of ids) await this.recording(a,id);
    const now=sec(),expires=now+LIMITS.pin_seconds;
    const response={recording_ids:ids,expires_at:expires};
    const predicates=ids.map(()=>"(SELECT COUNT(*) FROM recordings r JOIN assets x ON x.key=r.asset_key JOIN accounts a ON a.uid=r.uid WHERE r.id=? AND r.published=1 AND x.state='stored' AND a.invited=1 AND a.status IN ('active','rename_required'))=1");
    await this.tx("EXISTS(SELECT 1 FROM accounts WHERE uid=? AND status='active' AND invited=1)"+(predicates.length?" AND "+predicates.join(" AND "):""),
      [a.uid,...ids],[this.sql("DELETE FROM pins WHERE uid=?",a.uid),
      ...ids.map(id=>this.sql("INSERT INTO pins VALUES(?,?,?)",a.uid,id,expires)),this.receiptSQL(a.uid,"pins",key,digest,response)]);
    return response;
  }
  async withdraw(a,key,digest,deleting=false) {
    requireThat(a.status!=="deleted","account_deleted","Account already deleted.",403);
    const response={withdrawn:true,...(deleting?{status:"deleted",identity_deletion:"pending"}:{})};
    await this.tx("EXISTS(SELECT 1 FROM accounts WHERE uid=? AND status!='deleted')",[a.uid],[
      this.sql("INSERT INTO scope_epochs(scope,epoch) SELECT DISTINCT target_key,1 FROM recordings WHERE uid=? ON CONFLICT(scope) DO UPDATE SET epoch=epoch+1",a.uid),
      this.sql("UPDATE recordings SET published=0 WHERE uid=?",a.uid),
      this.sql("DELETE FROM winners WHERE uid=?",a.uid),
      this.sql("DELETE FROM pins WHERE recording_id IN (SELECT id FROM recordings WHERE uid=?) OR uid=?",a.uid,a.uid),
      this.sql("UPDATE accounts SET sharing=0,updated_at=?"+(deleting?",status='deleted',name=NULL,name_key=NULL,sheet_runner=NULL,sheet_verified=0":"")+" WHERE uid=?",sec(),a.uid),
      this.sql("UPDATE settings SET global_epoch=global_epoch+1 WHERE id=1"),
      ...(deleting?[this.sql("INSERT INTO deletion_outbox(uid,created_at) VALUES(?,?) ON CONFLICT DO NOTHING",a.uid,sec())]:[]),
      this.receiptSQL(a.uid,deleting?"delete":"withdraw",key,digest,response)]);
    return response;
  }
  async retract(a,rawSHA,keep,key,digest) {
    requireThat(a.status!=="deleted","account_deleted","Account already deleted.",403);
    // One selector for every statement: this owner's identities of these exact
    // bytes under another strategy. `strategy IS NOT NULL` selects them all.
    const own="uid=? AND raw_sha256=? AND strategy IS NOT ?",args=[a.uid,rawSHA,keep];
    const live=own+" AND (published=1 OR EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=recordings.id))";
    await this.tx("EXISTS(SELECT 1 FROM accounts WHERE uid=? AND status!='deleted')",[a.uid],[
      // The receipt counts live identities before the statements below retire them.
      this.sql("INSERT INTO mutation_receipts VALUES(?,?,?,?,json_object('retracted',(SELECT COUNT(*) FROM recordings WHERE "+live+")),?)",a.uid,"retract",key,digest,...args,sec()),
      this.sql("INSERT INTO scope_epochs(scope,epoch) SELECT DISTINCT target_key,1 FROM recordings WHERE "+live+" ON CONFLICT(scope) DO UPDATE SET epoch=epoch+1",...args),
      this.sql("DELETE FROM pins WHERE recording_id IN (SELECT id FROM recordings WHERE "+own+")",...args),
      this.sql("DELETE FROM winners WHERE recording_id IN (SELECT id FROM recordings WHERE "+own+")",...args),
      this.sql("UPDATE recordings SET published=0 WHERE "+own+" AND published=1",...args),
      // Unfinished grants for those identities can no longer publish later:
      // pending ones expire into the reconciler's refund, and stored bytes end
      // as a lost race whose asset the reconciler collects.
      this.sql("UPDATE grants SET expires_at=MIN(expires_at,?) WHERE "+own+" AND state='pending'",sec(),...args),
      this.sql("UPDATE grants SET state='complete',disposition='not_faster' WHERE "+own+" AND state='stored'",...args)]);
    // Physical bytes stay charged until the reconciler proves R2 deletion.
    return this.receipt(a.uid,"retract",key,digest);
  }
  async invite(subject,invited,actor,reason,confirmation,receipt) {
    requireThat(confirmation==="CHANGE INVITATION","confirmation_required","Confirm invitation change.");
    const uid=subject.toLowerCase(),now=sec();
    await this.tx("(SELECT uncapped FROM beta_admission WHERE id=1)=1 OR EXISTS(SELECT 1 FROM accounts WHERE uid=?) OR "+
      "(SELECT COUNT(*) FROM accounts)<(SELECT max_members FROM beta_admission WHERE id=1)",[uid],[
      this.sql("INSERT INTO accounts(uid,issuer,subject,invited,created_at,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET invited=excluded.invited,updated_at=excluded.updated_at",
        uid,this.cfg.issuer,subject.toLowerCase(),Number(invited),now,now),
      this.sql("UPDATE settings SET global_epoch=global_epoch+1 WHERE id=1"),
      this.sql("INSERT INTO admin_audit VALUES(?,?,?,?,?,?,?,?)",crypto.randomUUID(),actor,"invitation",uid,reason,"{}",JSON.stringify({invited}),now),
      this.receiptSQL(actor,"invite",receipt.key,receipt.digest,{uid,invited})]);
    return {uid,invited};
  }
  async reclaim(body,actor,receipt) {
    const named=checkedName(body.name);const before=await this.one("SELECT * FROM accounts WHERE name_key=?",named.key);
    requireThat(body.confirmation==="RECLAIM "+named.name,"confirmation_required","Confirm this exact public name.");
    requireThat(before?.uid===body.expected_owner,"owner_changed","The name owner has changed.",409);
    const now=sec();
    await this.tx("EXISTS(SELECT 1 FROM accounts WHERE uid=? AND name_key=?) AND EXISTS(SELECT 1 FROM accounts WHERE uid=? AND invited=1 AND status IN ('new','active','rename_required')) AND ?!=?",
      [body.expected_owner,named.key,body.destination_uid,body.expected_owner,body.destination_uid],[
      this.sql("UPDATE accounts SET name=NULL,name_key=NULL,status=CASE WHEN status IN ('suspended','deleted') THEN status ELSE 'rename_required' END,sheet_runner=NULL,sheet_verified=0,updated_at=? WHERE uid=?",now,body.expected_owner),
      this.sql("UPDATE accounts SET name=?,name_key=?,status=CASE WHEN terms_version IS NULL THEN 'new' ELSE 'active' END,sheet_runner=?,sheet_verified=?,updated_at=? WHERE uid=?",
        named.name,named.key,body.transfer_sheet?before.sheet_runner:null,body.transfer_sheet?before.sheet_verified:0,now,body.destination_uid),
      this.sql("UPDATE settings SET global_epoch=global_epoch+1 WHERE id=1"),
      this.sql("INSERT INTO admin_audit VALUES(?,?,?,?,?,?,?,?)",crypto.randomUUID(),actor,"reclaim",body.expected_owner,body.reason,
        JSON.stringify({owner:before.uid,name:before.name,sheet_runner:before.sheet_runner}),JSON.stringify({owner:body.destination_uid,name:named.name,transfer_sheet:body.transfer_sheet}),now),
      this.receiptSQL(actor,"reclaim",receipt.key,receipt.digest,{name:named.name,owner:body.destination_uid,displaced_uid:body.expected_owner})]);
    return {name:named.name,owner:body.destination_uid,displaced_uid:body.expected_owner};
  }
}
