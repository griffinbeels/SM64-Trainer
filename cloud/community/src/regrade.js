import {Store} from './store.js';
import {config} from './config.js';
import {catalogFor,lookup,grade} from './catalog.js';
import {ServiceError,requireThat} from './errors.js';
export async function regrade(env,{model,limit=20}={}){
  model ||= await catalogFor(env);const s=new Store(env.DB,config(env));let state=await s.one('SELECT * FROM settings WHERE id=1');
  if(state.generation===model.generation&&!state.migration_generation)return {complete:true,generation:model.generation};
  if(!state.migration_generation){
    await s.tx('(SELECT migration_generation FROM settings WHERE id=1) IS NULL',[],[s.sql("UPDATE settings SET migration_generation=?,migration_cursor='' WHERE id=1",model.generation)]);state=await s.one('SELECT * FROM settings WHERE id=1');}
  requireThat(state.migration_generation===model.generation,'migration_busy','Finish the admitted catalog migration first.',409);
  const rows=await s.rows('SELECT r.* FROM recordings r JOIN assets x ON x.key=r.asset_key WHERE r.id>? AND r.published=1 AND x.state=\'stored\' ORDER BY r.id LIMIT ?',state.migration_cursor,limit);
  for(const r of rows){
    let supported;try{supported=lookup(model,JSON.parse(r.target_json),r.clock,r.rom,r.strategy);}catch(e){if(!(e instanceof ServiceError))throw e;}
    const statements=[];
    if(supported){const step=grade(supported.strat.cutoffs,r.frames),overall=grade(supported.entry.overall_cutoffs,r.frames);
      statements.push(s.sql('UPDATE recordings SET generation=?,step=?,overall_step=? WHERE id=?',model.generation,step,overall,r.id),s.sql('INSERT INTO winners VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(uid,target_key,clock,rom,strategy,generation,step) DO UPDATE SET recording_id=excluded.recording_id,frames=excluded.frames WHERE excluded.frames<winners.frames OR (excluded.frames=winners.frames AND excluded.recording_id<winners.recording_id)',r.uid,r.target_key,r.clock,r.rom,r.strategy,model.generation,step,r.id,r.frames));
    }else statements.push(s.sql('UPDATE recordings SET published=0 WHERE id=?',r.id),s.sql('DELETE FROM winners WHERE recording_id=?',r.id));
    statements.push(s.sql('UPDATE settings SET migration_cursor=? WHERE id=1',r.id));
    try{await s.tx('(SELECT migration_generation FROM settings WHERE id=1)=? AND (SELECT migration_cursor FROM settings WHERE id=1)<? AND EXISTS(SELECT 1 FROM recordings r JOIN assets x ON x.key=r.asset_key WHERE r.id=? AND r.published=1 AND x.state=\'stored\')',[model.generation,r.id,r.id],statements);}
    catch(e){if(!(e instanceof ServiceError))throw e;await s.tx('(SELECT migration_generation FROM settings WHERE id=1)=? AND (SELECT migration_cursor FROM settings WHERE id=1)<?',[model.generation,r.id],[s.sql('UPDATE settings SET migration_cursor=? WHERE id=1',r.id)]);}
  }
  if(rows.length===limit)return {complete:false,generation:model.generation};
  await s.tx('(SELECT migration_generation FROM settings WHERE id=1)=?',[model.generation],[s.sql('DELETE FROM winners WHERE generation!=?',model.generation),s.sql("UPDATE settings SET generation=?,migration_generation=NULL,migration_cursor='',global_epoch=global_epoch+1 WHERE id=1",model.generation)]);
  return {complete:true,generation:model.generation};
}
