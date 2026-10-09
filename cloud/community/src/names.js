import data from "./casefold.json" with {type:"json"};
import {requireThat} from "./errors.js";
export function fold(value) { return [...value.normalize("NFKC")].map(c=>data.map[c]??c).join("").trim(); }
export function checkedName(value) {
  requireThat(typeof value==="string" && value.trim() && new TextEncoder().encode(value).length<=128 &&
    !/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value),"invalid_name","Use a name of up to128 UTF-8 bytes without hidden controls.");
  const name=value.normalize("NFKC").trim(), key=fold(name);
  requireThat(key && new TextEncoder().encode(key).length<=256 &&
    !["admin","administrator","moderator","sm64 trainer","deleted"].includes(key),"invalid_name","This name is unavailable.");
  return {name,key};
}
export function prefixRange(value) {
  requireThat(typeof value==="string" && value.length>=1 && new TextEncoder().encode(value).length<=128 &&
    !/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value),"invalid_prefix","Enter a bounded player name prefix.");
  const start=fold(value);
  requireThat(start.length>0);
  // D1 UTF-8 BINARY collation and a Unicode scalar successor give an indexed range.
  const points=[...start],last=points.pop().codePointAt(0);
  requireThat(last<0x10ffff);
  return {start,end:points.join("")+String.fromCodePoint(last===0xd7ff?0xe000:last+1)};
}
