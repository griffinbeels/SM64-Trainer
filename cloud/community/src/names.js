import data from "./casefold.json" with {type:"json"};
import {requireThat} from "./errors.js";
export function fold(value) { return [...value.normalize("NFKC")].map(c=>data.map[c]??c).join("").trim(); }
export function checkedName(value) {
  requireThat(typeof value==="string" && value.trim() &&
    !/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}]/u.test(value),"invalid_name","Use a bounded name without hidden controls.");
  requireThat(new TextEncoder().encode(value).length<=128,"invalid_name","Name input is too long. Use at most 20 normalized characters and 63 UTF-8 bytes.");
  const name=value.normalize("NFKC").trim(), key=fold(name);
  requireThat([...name].length<=20,"invalid_name","Use a name of at most 20 characters after normalization.");
  requireThat(new TextEncoder().encode(name).length<=63,"invalid_name","Use a name of at most 63 UTF-8 bytes after normalization.");
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
