// A ghost's start is checked before it is stored (Griffin, 2026-11-09). The
// service reads only the header the trainer writes (the trainer's
// src/sm64_events/ghost/start_frame.py, format versions 2 and 3); it never
// unpacks the start's game state. Anything that is not such a header is
// refused: the wrong magic or version, a header of the wrong size or fields,
// regions other than the trainer's spans for that game version, any main-pool
// changes, a game-data difference outside the boot spans, or a start named
// for another ghost. The spans are start-spans.json, which the trainer's
// tests keep equal to its own (tests/test_ghost_start_frame.py).
import SPANS from "./start-spans.json" with {type:"json"};
import {requireThat,hex64,integer} from "./errors.js";

const MAGIC="SM64STRT",HEAD=16;
const COMMON=["version","clip_sha256","game_version","rom_sha256","usamune_fingerprint","global_timer",
  "area_update_counter","level","area","act","regions","pool","made_utc"];
const FIELDS={2:COMMON,3:[...COMMON,"boot"]};
const NUMBERS=["global_timer","area_update_counter","level","area","act"];
const BOOT_FIELDS=["base_sha256","begin","runs"];

const exactly=(value,keys)=>value&&typeof value==="object"&&!Array.isArray(value)&&
  Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));

const ok=(test,message)=>requireThat(test,"start_invalid",message,422);
const FIELDS_WRONG="The ghost start's header has the wrong fields.";
const OUTSIDE="The ghost start carries game memory outside the parts a start may hold.";

// The version and the header JSON, every length bounded before it is read.
function header(bytes){
  ok(bytes.length>HEAD&&new TextDecoder().decode(bytes.subarray(0,8))===MAGIC,"The ghost start is not a ghost start.");
  const view=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  const version=view.getUint32(8,true),length=view.getUint32(12,true);
  ok(SPANS._versions.includes(version),"The ghost start is a format version this service does not take.");
  ok(length>0&&length<=SPANS._max_meta_bytes&&HEAD+length<bytes.length,"The ghost start's header is the wrong size.");
  let meta=null;
  try{meta=JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(bytes.subarray(HEAD,HEAD+length)));}catch{/* refused below */}
  ok(exactly(meta,FIELDS[version])&&meta.version===version,FIELDS_WRONG);
  return {version,meta};
}

// Version 3: the regions sent as their change from the ROM's boot image, only over the boot spans.
function checkBoot(boot,spans){
  ok(Array.isArray(boot),FIELDS_WRONG);
  const seen=new Set();
  for(const entry of boot){
    ok(exactly(entry,BOOT_FIELDS)&&hex64(entry.base_sha256)&&integer(entry.runs,0,SPANS._max_boot_runs),FIELDS_WRONG);
    ok(spans.boot.includes(entry.begin)&&!seen.has(entry.begin),OUTSIDE);
    seen.add(entry.begin);
  }
}

export function checkStart(bytes,{raw_sha256}){
  const {version,meta}=header(bytes);
  const spans=typeof meta.game_version==="string"&&!meta.game_version.startsWith("_")&&Object.hasOwn(SPANS,meta.game_version)?SPANS[meta.game_version]:null;
  ok(spans,"The ghost start names a game version this service does not take.");
  ok(hex64(meta.clip_sha256)&&hex64(meta.rom_sha256)&&hex64(meta.usamune_fingerprint),FIELDS_WRONG);
  ok(meta.clip_sha256===raw_sha256,"The ghost start belongs to another ghost.");
  ok(NUMBERS.every(k=>integer(meta[k],0,0xffffffff))&&typeof meta.made_utc==="string"&&meta.made_utc.length<=64,FIELDS_WRONG);
  ok(Array.isArray(meta.regions)&&meta.regions.length===spans.regions.length&&
    meta.regions.every((r,i)=>Array.isArray(r)&&r.length===2&&r[0]===spans.regions[i][0]&&r[1]===spans.regions[i][1]),OUTSIDE);
  ok(meta.pool===null,"The ghost start carries main-pool changes, which a shared start never holds.");
  if(version===3)checkBoot(meta.boot,spans);
}
