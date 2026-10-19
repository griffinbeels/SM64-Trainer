// Library discovery is winner-only. Pins retain a replaced rival for its owner,
// but must not inflate discovery counts or expose that owner's private choice.
const published="r.published=1 AND a.invited=1 AND a.status IN ('active','rename_required') AND x.state='stored' AND EXISTS(SELECT 1 FROM winners w WHERE w.recording_id=r.id)";
function owners(query,values){
  if(!query.visibleOwners)return '';
  values.push(...query.visibleOwners);
  return ' AND r.uid IN ('+query.visibleOwners.map(()=>'?').join(',')+')';
}
export function summaryRows(s,query){
  const values=[query.generation];
  let filter='';
  if(query.key){filter+=' AND r.target_key=?';values.push(query.key);}
  if(query.rom){filter+=' AND r.rom=?';values.push(query.rom);}
  const continuation=query.after?' AND (r.target_key,r.clock,r.rom,r.strategy)>(?,?,?,?)':'';
  if(query.after)values.push(...query.after);
  const visible=owners(query,values);values.push(query.pageSize+1);
  // One ordered, indexed GROUP BY, not one pool request per catalog target.
  // LIMIT bounds identity groups; exact counts still read the eligible records
  // in those groups, whose size is bounded by the service's storage admission.
  return s.rows("SELECT r.target_key,r.clock,r.rom,r.strategy,MIN(r.target_json) AS target_json,COUNT(*) AS recordings,COUNT(DISTINCT r.uid) AS players,MIN(r.frames) AS best_frames "+
    "FROM recordings r INDEXED BY recording_library JOIN accounts a ON a.uid=r.uid JOIN assets x ON x.key=r.asset_key WHERE r.generation=?"+filter+continuation+' AND '+published+visible+
    ' GROUP BY r.target_key,r.clock,r.rom,r.strategy ORDER BY r.target_key,r.clock,r.rom,r.strategy LIMIT ?',...values);
}
export function recordingRows(s,query){
  const values=[query.key,query.clock,query.rom,query.generation,query.strategy];
  const continuation=query.after?' AND (r.frames,r.id)>(?,?)':'';
  if(query.after)values.push(...query.after);
  const visible=owners(query,values);values.push(query.pageSize+1);
  return s.rows("SELECT r.*,a.name,a.status FROM recordings r INDEXED BY fastest_strategy JOIN accounts a ON a.uid=r.uid JOIN assets x ON x.key=r.asset_key "+
    'WHERE r.target_key=? AND r.clock=? AND r.rom=? AND r.generation=? AND r.strategy=?'+continuation+' AND '+published+visible+
    ' ORDER BY r.frames,r.id LIMIT ?',...values);
}
export function libraryGate(s,key){
  // Publications/retractions increment scope epochs; moderation/name/account
  // changes increment global_epoch. Bind both without adding writes to every
  // upload solely for a rarely opened Library page. Scope epochs are retained.
  // A target-filtered page must survive publications on unrelated stars.
  // Global discovery covers every scope, so its continuation fences the sum.
  const epoch=key?'COALESCE((SELECT epoch FROM scope_epochs WHERE scope=?),0)':'(SELECT COALESCE(SUM(epoch),0) FROM scope_epochs)';
  return s.one('SELECT generation,global_epoch,migration_generation,'+epoch+' AS publication_epoch FROM settings WHERE id=1',...(key?[key]:[]));
}
