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
function record(bytes, version, header, previous) {
  // The native layouts are rooted in plugin/gfxwrap/ghost_{clip,capture}.h.
  // Validation never upgrades or rewrites the bytes that establish their SHA.
  const v=new DataView(bytes.buffer,bytes.byteOffset,bytes.byteLength), u=at=>v.getUint32(at,true),
    q=at=>v.getBigUint64(at,true), h=at=>v.getUint16(at,true);
  const [width,sampleWidth,sampleVersion]=FORMATS[version], valid=u(28);
  check(u(0)===width && u(4)===version && u(8)===header.configuration && u(12)<=11 &&
    u(16)===sampleWidth && u(20)===sampleVersion && u(24)===header.epoch && !(valid & ~(version===7?1023:511)));
  const sequence=q(32),dlist=q(40),qpc=v.getBigInt64(48,true),timer=u(56),igt=h(62),status=u(68),action=u(72),
    level=v.getInt16(64,true),area=v.getInt16(66,true);
  check(sequence>0n && dlist>0n && qpc>=0n && status<=13 && bytes[153]<24);
  check(!(valid&1) || (level>=4 && level<=36 && area>=1 && area<=8));
  check(Boolean(valid&8)===Boolean(action));
  check(status!==0 || (valid&31)===31);
  if(valid&2) {
    finite(v,76,6); const kind=u(128); check(kind===1 || kind===2);
    if(kind===1) check(zero(bytes,156,220)); else { check(valid&4);affine(v,156); }
  } else check(zero(bytes,76,153) && zero(bytes,154,220));
  if(valid&4) { finite(v,220,6);finite(v,248,1);check(v.getFloat32(248,true)>0&&v.getFloat32(248,true)<180); }
  else check(zero(bytes,220,256));
  check((valid&16) || zero(bytes,256,260));
  const clockValid=u(260),refusal=u(264),transition=u(268);
  // gc_clock has four u32 then six u64: raw_igt lives at 272.
  const actualRaw=u(272), actualBanked=q(276), actualProgress=q(284), actualOrigin=q(292),
    actualLeg=q(300), actualElapsed=q(308), actualContinuity=q(316);
  check(!(clockValid&~7) && !(refusal&~1023) && transition<=6);
  check((clockValid&5)===5 && (valid&25)===25 && actualRaw===igt && actualLeg>0n && actualContinuity>0n);
  if(clockValid&2) check(!refusal && actualOrigin>0n && actualProgress===actualBanked+BigInt(actualRaw));
  else check(actualBanked===0n && actualProgress===0n);
  if(valid&32) { check(valid&2 && u(128)===2 && !bytes[487]);affine(v,324); }
  else check(zero(bytes,324,616));
  const soundCount=bytes[616],soundFlags=bytes[617];
  if(valid&64) check(soundCount<=8 && !(soundFlags&~1) && (!(soundFlags&1)||soundCount===8) && zero(bytes,620+4*soundCount,652));
  else check(zero(bytes,616,652));
  if(version>=5) {
    const count=bytes[652],flags=bytes[653];
    if(valid&128) {
      check(count<=16 && !(flags&~1) && (!(flags&1)||count===16) && zero(bytes,654,656) && zero(bytes,656+count*36,1232));
      for(let i=0;i<count;i++) {const at=656+i*36,opacity=h(at+22);check(!opacity||(opacity>=256&&opacity<=511));finite(v,at+24,3);}
    } else check(zero(bytes,652,1232));
    if(valid&256) {
      const behaviour=u(1232),animation=v.getInt16(1236,true),frame=v.getInt16(1238,true),animState=h(1242);
      check(valid&2 && (behaviour>>>16)===0x1300 && animation>=-1 && frame>=0 &&
        (animation!==-1||frame===0) && animState<=255);finite(v,1244,6);
      check([1256,1260,1264].every(at=>v.getFloat32(at,true)!==0));
      if(version>=6) {const released=h(1274);check(released<=1 && (released||zero(bytes,1268,1274)));}
    } else check(zero(bytes,1232,width>=1276?1276:width));
  } else check(!(valid&(128|256)));
  if(version===7) {
    if(valid&512) check(v.getInt16(1276,true)>=-128 && v.getInt16(1276,true)<=127 &&
      v.getInt16(1278,true)>=-128 && v.getInt16(1278,true)<=127 && !(h(1280)&~0xff3f) && !h(1282));
    else check(zero(bytes,1276,1284));
  }
  if(previous) check(sequence>previous.sequence && dlist>previous.dlist && qpc>=previous.qpc &&
    timer>=previous.timer && actualElapsed>=previous.elapsed && actualContinuity===previous.continuity);
  return {sequence,dlist,qpc,timer,igt,level,area,origin:actualOrigin,elapsed:actualElapsed,continuity:actualContinuity,playable:(valid&3)===3};
}
export function validateGHC1(encoded, grant) {
  check(encoded.byteLength===grant.encoded_bytes && encoded.byteLength<=LIMITS.encoded_bytes && encoded.byteLength>=189);
  const encodedHash=createHash("sha256").update(encoded).digest("hex");
  check(encodedHash===grant.encoded_sha256);
  const v=new DataView(encoded.buffer,encoded.byteOffset,encoded.byteLength), u=at=>v.getUint32(at,true);
  check(new TextDecoder().decode(encoded.subarray(0,4))==="GHC1");
  const rawBytes=u(4),headerBytes=u(8),recordBytes=u(12),count=u(16);
  check(rawBytes===grant.raw_bytes && rawBytes<=LIMITS.raw_bytes && headerBytes===160 && count>0 &&
    count<=100000 && rawBytes===160+count*recordBytes);
  const bytes=encoded.subarray(20,180),h=new DataView(bytes.buffer,bytes.byteOffset,160),
    hu=at=>h.getUint32(at,true),hq=at=>h.getBigUint64(at,true),version=hu(8),format=FORMATS[version];
  check(format && new TextDecoder().decode(bytes.subarray(0,8))==="SM64GHO"+version &&
    hu(12)===160 && hu(16)===recordBytes && recordBytes===format[0] && hu(20)===count &&
    hu(24)===1 && hu(28)===format[2] && hu(32)>0 && hu(36)>0 && !zero(bytes,40,56) &&
    hq(56)>0n && h.getBigInt64(64,true)>=0n && Number.isFinite(h.getFloat64(72,true)) &&
    h.getFloat64(72,true)>0 && [1,2].includes(hu(112)) && !(hu(124)&~1) && zero(bytes,152,160));
  const header={configuration:hu(32),epoch:hu(36)};
  const target=grant.target||JSON.parse(grant.target_json||'null');
  check(target?.kind==='star'||target?.kind==='segment');
  if(target.kind==='segment')check(hu(112)===2);
  else{const [course,star]=target.entity_key.split('-').map(Number),level=h.getInt32(104,true);
    check(hu(112)===1||(hu(112)===2&&course>=1&&course<=15&&star===6));
    check(course===0?[6,16,26].includes(level):COURSE_BY_LEVEL[level]===course);}
  const digest=createHash("sha256").update(bytes);
  let offset=180,seen=0,first,last,playable=false,indexing=true;
  while(seen<count) {
    check(offset+8<=encoded.length);
    const n=u(offset),length=u(offset+4);offset+=8;
    check(n===Math.min(256,count-seen) && length>0 && offset+length<=encoded.length);
    let inflated;
    try { inflated=inflateSync(encoded.subarray(offset,offset+length),{maxOutputLength:n*recordBytes,info:true}); }
    catch { check(false); }
    check(inflated.buffer.length===n*recordBytes && inflated.engine.bytesWritten===length);
    const raw=new Uint8Array(n*recordBytes);
    for(let c=0;c<recordBytes;c++) for(let r=0;r<n;r++) raw[r*recordBytes+c]=inflated.buffer[c*n+r];
    digest.update(raw);
    for(let r=0;r<n;r++) {
      const frame=record(raw.subarray(r*recordBytes,(r+1)*recordBytes),version,header,last);
      first ||= frame;
      const coordinate=frame.elapsed-first.elapsed+(hu(112)===1?BigInt(first.igt):0n);
      check(coordinate>=0n&&coordinate<(1n<<64n));
      if(hu(112)===1 && frame.origin!==first.origin) indexing=false;
      playable ||= indexing&&frame.playable;last=frame;
    }
    offset+=length;seen+=n;
  }
  check(offset===encoded.length && playable && digest.digest("hex")===grant.raw_sha256);
  check(h.getInt32(104,true)===first.level && h.getInt32(108,true)===first.area &&
    hq(80)===first.sequence && hq(88)===last.sequence && hu(96)===first.timer && hu(100)===last.timer &&
    hu(116)===first.timer && hu(120)===first.igt && hq(128)===first.sequence &&
    hq(136)===first.elapsed && hq(144)===first.continuity);
  // There is no signed completion certificate in native bytes. These checks
  // establish a playable origin and a plausible duration, not honest gameplay.
  check(grant.frames<=last.timer-first.timer+first.igt+31);
  return {version,record_count:count,clock_kind:hu(112),native_sha256:grant.raw_sha256};
}
