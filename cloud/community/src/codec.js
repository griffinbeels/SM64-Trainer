import {inflateSync} from "node:zlib";
import {createHash} from "node:crypto";
import {requireThat} from "./errors.js";
import {LIMITS} from "./config.js";
const FORMATS = {4:[652,636,5],5:[1268,1252,6],6:[1276,1260,7],7:[1284,1268,8]};
// Match ghost.portable._checked_recording_target and memory.addresses.
const COURSE_BY_LEVEL={9:1,24:2,12:3,5:4,4:5,7:6,22:7,8:8,23:9,10:10,11:11,36:12,13:13,14:14,15:15,17:16,19:17,21:18,27:19,28:20,29:21,18:22,31:23,20:24};
const check = ok => requireThat(ok,"invalid_clip","The recording fails native structural validation.",422);
const zero = (b,lo,hi) => { for(let i=lo;i<hi;i++) if(b[i]) return false; return true; };
const finite = (view, at, count, limit = 1e7) => {
  for(let i=0;i<count;i++) { const value=view.getFloat32(at+i*4,true); check(Number.isFinite(value) && Math.abs(value)<=limit); }
};
function affine(view, at) { finite(view,at,16); check([3,7,11].every(i=>view.getFloat32(at+i*4,true)===0) && view.getFloat32(at+60,true)===1); }
// Little-endian reads over one native structure (a record, or the clip header).
function reader(bytes) {
  const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength);
  return {bytes,v,u32:at=>v.getUint32(at,true),u64:at=>v.getBigUint64(at,true),
    u16:at=>v.getUint16(at,true),i16:at=>v.getInt16(at,true)};
}
// gclip_frame's size, version and configuration, then gc_sample's own size, version and epoch.
function checkStamp(r, version, header, valid) {
  const [width,sampleWidth,sampleVersion]=FORMATS[version];
  check(r.u32(0)===width && r.u32(4)===version && r.u32(8)===header.configuration && r.u32(12)<=11 &&
    r.u32(16)===sampleWidth && r.u32(20)===sampleVersion && r.u32(24)===header.epoch && !(valid & ~(version===7?1023:511)));
}
// gc_sample's counters, place and action; a zero status requires every GC_VALID_REQUIRED field.
function checkFrame(r, valid) {
  const frame={sequence:r.u64(32),dlist:r.u64(40),qpc:r.v.getBigInt64(48,true),timer:r.u32(56),igt:r.u16(62),
    level:r.i16(64),area:r.i16(66)}, status=r.u32(68), action=r.u32(72);
  check(frame.sequence>0n && frame.dlist>0n && frame.qpc>=0n && status<=13 && r.bytes[153]<24);
  check(!(valid&1) || (frame.level>=4 && frame.level<=36 && frame.area>=1 && frame.area<=8));
  check(Boolean(valid&8)===Boolean(action));
  check(status!==0 || (valid&31)===31);
  return frame;
}
// GC_VALID_POSE: a GC_ROOT_TRS root keeps its matrix zero; a scaled-modelview root needs the camera.
function checkPose(r, valid) {
  if(valid&2) {
    finite(r.v,76,6); const kind=r.u32(128); check(kind===1 || kind===2);
    if(kind===1) check(zero(r.bytes,156,220)); else { check(valid&4);affine(r.v,156); }
  } else check(zero(r.bytes,76,153) && zero(r.bytes,154,220));
}
// GC_VALID_CAMERA
function checkCamera(r, valid) {
  if(valid&4) { finite(r.v,220,6);finite(r.v,248,1);check(r.v.getFloat32(248,true)>0&&r.v.getFloat32(248,true)<180); }
  else check(zero(r.bytes,220,256));
}
// gc_clock has four u32 then six u64: raw_igt lives at 272.
function checkClock(r, valid, igt) {
  const clockValid=r.u32(260),refusal=r.u32(264),transition=r.u32(268);
  const actualRaw=r.u32(272), actualBanked=r.u64(276), actualProgress=r.u64(284), actualOrigin=r.u64(292),
    actualLeg=r.u64(300), actualElapsed=r.u64(308), actualContinuity=r.u64(316);
  check(!(clockValid&~7) && !(refusal&~1023) && transition<=6);
  check((clockValid&5)===5 && (valid&25)===25 && actualRaw===igt && actualLeg>0n && actualContinuity>0n);
  if(clockValid&2) check(!refusal && actualOrigin>0n && actualProgress===actualBanked+BigInt(actualRaw));
  else check(actualBanked===0n && actualProgress===0n);
  return {origin:actualOrigin,elapsed:actualElapsed,continuity:actualContinuity};
}
// GC_VALID_SHADOW needs a scaled-modelview pose.
function checkShadow(r, valid) {
  if(valid&32) { check(valid&2 && r.u32(128)===2 && !r.bytes[487]);affine(r.v,324); }
  else check(zero(r.bytes,324,616));
}
// GC_VALID_SOUND
function checkSound(r, valid) {
  const soundCount=r.bytes[616],soundFlags=r.bytes[617];
  if(valid&64) check(soundCount<=8 && !(soundFlags&~1) && (!(soundFlags&1)||soundCount===8) && zero(r.bytes,620+4*soundCount,652));
  else check(zero(r.bytes,616,652));
}
// GC_VALID_PARTICLES (version 5 on)
function checkParticles(r, valid) {
  const count=r.bytes[652],flags=r.bytes[653];
  if(valid&128) {
    check(count<=16 && !(flags&~1) && (!(flags&1)||count===16) && zero(r.bytes,654,656) && zero(r.bytes,656+count*36,1232));
    for(let i=0;i<count;i++) {const at=656+i*36,opacity=r.u16(at+22);check(!opacity||(opacity>=256&&opacity<=511));finite(r.v,at+24,3);}
  } else check(zero(r.bytes,652,1232));
}
// GC_VALID_HELD (version 5 on; `released` from version 6) needs a pose.
function checkHeld(r, valid, version) {
  const width=FORMATS[version][0];
  if(valid&256) {
    const behaviour=r.u32(1232),animation=r.i16(1236),frame=r.i16(1238),animState=r.u16(1242);
    check(valid&2 && (behaviour>>>16)===0x1300 && animation>=-1 && frame>=0 &&
      (animation!==-1||frame===0) && animState<=255);finite(r.v,1244,6);
    check([1256,1260,1264].every(at=>r.v.getFloat32(at,true)!==0));
    if(version>=6) {const released=r.u16(1274);check(released<=1 && (released||zero(r.bytes,1268,1274)));}
  } else check(zero(r.bytes,1232,width>=1276?1276:width));
}
// GC_VALID_PAD (version 7)
function checkPad(r, valid) {
  if(valid&512) check(r.i16(1276)>=-128 && r.i16(1276)<=127 &&
    r.i16(1278)>=-128 && r.i16(1278)<=127 && !(r.u16(1280)&~0xff3f) && !r.u16(1282));
  else check(zero(r.bytes,1276,1284));
}
function record(bytes, version, header, previous) {
  // The native layouts are rooted in plugin/gfxwrap/ghost_{clip,capture}.h.
  // Validation never upgrades or rewrites the bytes that establish their SHA.
  const r=reader(bytes), valid=r.u32(28);
  checkStamp(r,version,header,valid);
  const frame=checkFrame(r,valid);
  checkPose(r,valid);
  checkCamera(r,valid);
  check((valid&16) || zero(bytes,256,260));
  const clock=checkClock(r,valid,frame.igt);
  checkShadow(r,valid);
  checkSound(r,valid);
  if(version>=5) { checkParticles(r,valid); checkHeld(r,valid,version); }
  else check(!(valid&(128|256)));
  if(version===7) checkPad(r,valid);
  if(previous) check(frame.sequence>previous.sequence && frame.dlist>previous.dlist && frame.qpc>=previous.qpc &&
    frame.timer>=previous.timer && clock.elapsed>=previous.elapsed && clock.continuity===previous.continuity);
  return {...frame,...clock,playable:(valid&3)===3};
}
// The GHC1 envelope: the grant's size and hash, the magic, and the native sizes it declares.
function checkEnvelope(encoded, grant) {
  check(encoded.byteLength===grant.encoded_bytes && encoded.byteLength<=LIMITS.encoded_bytes && encoded.byteLength>=189);
  const encodedHash=createHash("sha256").update(encoded).digest("hex");
  check(encodedHash===grant.encoded_sha256);
  const e=reader(encoded);
  check(new TextDecoder().decode(encoded.subarray(0,4))==="GHC1");
  const rawBytes=e.u32(4),headerBytes=e.u32(8),recordBytes=e.u32(12),count=e.u32(16);
  check(rawBytes===grant.raw_bytes && rawBytes<=LIMITS.raw_bytes && headerBytes===160 && count>0 &&
    count<=100000 && rawBytes===160+count*recordBytes);
  return {e,recordBytes,count};
}
// gclip_header's magic, version and sizes agree with the envelope and a known format.
function checkHeaderLayout(hd, count, recordBytes) {
  const version=hd.u32(8),format=FORMATS[version];
  check(format && new TextDecoder().decode(hd.bytes.subarray(0,8))==="SM64GHO"+version &&
    hd.u32(12)===160 && hd.u32(16)===recordBytes && recordBytes===format[0] && hd.u32(20)===count &&
    hd.u32(24)===1 && hd.u32(28)===format[2]);
}
// gclip_header's capture identity, anchors, clock kind and flags.
function checkHeaderCapture(hd) {
  check(hd.u32(32)>0 && hd.u32(36)>0 && !zero(hd.bytes,40,56) &&
    hd.u64(56)>0n && hd.v.getBigInt64(64,true)>=0n && Number.isFinite(hd.v.getFloat64(72,true)) &&
    hd.v.getFloat64(72,true)>0 && [1,2].includes(hd.u32(112)) && !(hd.u32(124)&~1) && zero(hd.bytes,152,160));
}
// A segment uses GCLIP_SEGMENT_CLOCK; a star uses GCLIP_STAR_CLOCK (star 6 of courses 1-15
// may use either) and its course must match the header's level.
function checkTarget(grant, hd) {
  const target=grant.target||JSON.parse(grant.target_json||'null');
  check(target?.kind==='star'||target?.kind==='segment');
  if(target.kind==='segment')check(hd.u32(112)===2);
  else{const [course,star]=target.entity_key.split('-').map(Number),level=hd.v.getInt32(104,true);
    check(hd.u32(112)===1||(hd.u32(112)===2&&course>=1&&course<=15&&star===6));
    check(course===0?[6,16,26].includes(level):COURSE_BY_LEVEL[level]===course);}
}
// One block's records come deflated column by column; this restores row order.
function inflateBlock(compressed, n, recordBytes) {
  let inflated;
  try { inflated=inflateSync(compressed,{maxOutputLength:n*recordBytes,info:true}); }
  catch { check(false); }
  check(inflated.buffer.length===n*recordBytes && inflated.engine.bytesWritten===compressed.length);
  const raw=new Uint8Array(n*recordBytes);
  for(let c=0;c<recordBytes;c++) for(let r=0;r<n;r++) raw[r*recordBytes+c]=inflated.buffer[c*n+r];
  return raw;
}
// Every block in order: the records chain, the timeline stays in range, and some frame is playable.
function checkRecords(encoded, e, hd, grant, {version,header,count,recordBytes}) {
  const digest=createHash("sha256").update(hd.bytes);
  let offset=180,seen=0,first,last,playable=false,indexing=true;
  while(seen<count) {
    check(offset+8<=encoded.length);
    const n=e.u32(offset),length=e.u32(offset+4);offset+=8;
    check(n===Math.min(256,count-seen) && length>0 && offset+length<=encoded.length);
    const raw=inflateBlock(encoded.subarray(offset,offset+length),n,recordBytes);
    digest.update(raw);
    for(let r=0;r<n;r++) {
      const frame=record(raw.subarray(r*recordBytes,(r+1)*recordBytes),version,header,last);
      first ||= frame;
      const coordinate=frame.elapsed-first.elapsed+(hd.u32(112)===1?BigInt(first.igt):0n);
      check(coordinate>=0n&&coordinate<(1n<<64n));
      if(hd.u32(112)===1 && frame.origin!==first.origin) indexing=false;
      playable ||= indexing&&frame.playable;last=frame;
    }
    offset+=length;seen+=n;
  }
  check(offset===encoded.length && playable && digest.digest("hex")===grant.raw_sha256);
  return {first,last};
}
export function validateGHC1(encoded, grant) {
  const {e,recordBytes,count}=checkEnvelope(encoded,grant);
  const hd=reader(encoded.subarray(20,180)),version=hd.u32(8);
  checkHeaderLayout(hd,count,recordBytes);
  checkHeaderCapture(hd);
  const header={configuration:hd.u32(32),epoch:hd.u32(36)};
  checkTarget(grant,hd);
  const {first,last}=checkRecords(encoded,e,hd,grant,{version,header,count,recordBytes});
  check(hd.v.getInt32(104,true)===first.level && hd.v.getInt32(108,true)===first.area &&
    hd.u64(80)===first.sequence && hd.u64(88)===last.sequence && hd.u32(96)===first.timer && hd.u32(100)===last.timer &&
    hd.u32(116)===first.timer && hd.u32(120)===first.igt && hd.u64(128)===first.sequence &&
    hd.u64(136)===first.elapsed && hd.u64(144)===first.continuity);
  // There is no signed completion certificate in native bytes. These checks
  // establish a playable origin and a plausible duration, not honest gameplay.
  check(grant.frames<=last.timer-first.timer+first.igt+31);
  return {version,record_count:count,clock_kind:hd.u32(112),native_sha256:grant.raw_sha256};
}
