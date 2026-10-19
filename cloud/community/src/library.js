import {exactQuery,readJSON,requireThat,hex64,json} from './errors.js';
import {lookup,targetKey} from './catalog.js';
import {encodeCursor,decodeLibraryCursor} from './cursors.js';
import {summaryRows,recordingRows,libraryGate} from './library-store.js';

const PAGE_ROWS=20;
export async function renewMetadata({request,url,s,a}){
  exactQuery(url,[]);
  const body=await readJSON(request,['recording_ids']),ids=body.recording_ids;
  requireThat(Array.isArray(ids)&&ids.length<=4&&ids.every(hex64)&&new Set(ids).size===ids.length);
  const found=await s.recordings(a,ids),byId=new Map(found.map(r=>[r.id,r]));
  return json({recordings:ids.filter(id=>byId.has(id)).map(id=>s.metadata(byId.get(id))),unavailable:ids.filter(id=>!byId.has(id))});
}
async function pageIdentity({s,a,model},kind,filter={}){
  const gate=await libraryGate(s,filter.key);
  requireThat(gate&&gate.generation===model.generation&&!gate.migration_generation,'generation_changed','Catalog changed.',409);
  return {kind,viewer:a.uid,generation:model.generation,globalEpoch:gate.global_epoch,publicationEpoch:gate.publication_epoch,visibleOwners:s.visibleOwners(a),...filter};
}
async function page(c,kind,filter,fetchRows,convert,sortKey){
  const identity=await pageIdentity(c,kind,filter),cursor=c.url.searchParams.get('cursor');
  const previous=cursor===null?null:await decodeLibraryCursor(c.env.CURSOR_SECRET,cursor,identity);
  const found=await fetchRows(c.s,{...identity,...filter,after:previous?.after,pageSize:PAGE_ROWS});
  const rows=found.slice(0,PAGE_ROWS),complete=found.length<=PAGE_ROWS;
  const next=complete?null:await encodeCursor(c.env.CURSOR_SECRET,identity,sortKey(rows.at(-1)),(previous?.page||0)+1);
  return json({generation:c.model.generation,[kind==='library-summary'?'entries':'recordings']:rows.map(convert),next_cursor:next,complete});
}
export async function librarySummary(c){
  const {url,model}=c;
  exactQuery(url,['target','rom','cursor']);
  const filter={};
  if(url.searchParams.has('rom')){filter.rom=url.searchParams.get('rom');requireThat(['us','jp'].includes(filter.rom));}
  if(url.searchParams.has('target')){
    let target;try{target=JSON.parse(url.searchParams.get('target'));}catch{requireThat(false,'invalid_target','Invalid portable target.');}
    filter.key=targetKey(target);
    requireThat(model.entries.some(e=>targetKey(e.target)===filter.key&&(!filter.rom||e.game_version===filter.rom)),'unsupported_target','Unsupported sharing identity.',422);
  }
  return page(c,'library-summary',filter,summaryRows,r=>({target:JSON.parse(r.target_json),clock:r.clock,game_version:r.rom,strategy:r.strategy,recordings:r.recordings,players:r.players,best_frames:r.best_frames}),r=>[r.target_key,r.clock,r.rom,r.strategy]);
}
export async function libraryRecordings(c){
  const {url,model,s}=c;
  exactQuery(url,['target','clock','rom','strategy','cursor'],['target','clock','rom','strategy']);
  let target;try{target=JSON.parse(url.searchParams.get('target'));}catch{requireThat(false,'invalid_target','Invalid portable target.');}
  const clock=url.searchParams.get('clock'),rom=url.searchParams.get('rom'),strategy=url.searchParams.get('strategy'),{key}=lookup(model,target,clock,rom,strategy);
  return page(c,'library-recordings',{key,clock,rom,strategy},recordingRows,r=>s.metadata(r),r=>[r.frames,r.id]);
}
