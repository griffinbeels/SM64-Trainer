import source from "../catalog.json" with {type:"json"};
import {canonical, requireThat, integer, hex64, sha256} from "./errors.js";
export function targetKey(target) {
  requireThat(target && typeof target === "object" && !Array.isArray(target) &&
    Object.keys(target).every(k=>["kind","entity_key","strat","name","signature"].includes(k)));
  if (target.kind === "star") {
    requireThat(typeof target.entity_key === "string" && /^\d{1,2}-\d$/.test(target.entity_key) && !target.signature);
    return "star:" + target.entity_key;
  }
  requireThat(target.kind === "segment" && hex64(target.signature));
  return "segment:" + target.signature;
}
export function validateCatalog(value) {
  requireThat(value?.v === 1 && hex64(value.generation) && Array.isArray(value.entries) &&
    value.entries.length > 0 && value.entries.length <= 4096, "catalog_unavailable","Canonical catalog is unavailable.",503);
  const seen = new Set();
  const cutoffs = rows => {
    requireThat(Array.isArray(rows) && rows.length > 0 && rows.length <= 256);
    let lastStep=-1,lastFrames=Infinity;
    for (const row of rows) {
      requireThat(Object.keys(row).length === 2 && integer(row.step,0,255) && row.step > lastStep &&
        integer(row.frames,1,2592000) && row.frames <= lastFrames);
      lastStep=row.step;lastFrames=row.frames;
    }
  };
  for (const entry of value.entries) {
    const key=targetKey(entry.target)+":"+entry.clock+":"+entry.game_version;
    requireThat(!seen.has(key) && ["igt","rta"].includes(entry.clock) && ["us","jp"].includes(entry.game_version));seen.add(key);
    requireThat(Array.isArray(entry.strategies) && entry.strategies.length > 0 && entry.strategies.length <= 64);
    const names=new Set(); for (const strategy of entry.strategies) {
      requireThat(typeof strategy.name==="string" && strategy.name.length<=128 && !names.has(strategy.name));names.add(strategy.name);
      cutoffs(strategy.cutoffs);
    }
    cutoffs(entry.overall_cutoffs);
  }
  return value;
}
export function grade(cutoffs, frames) {
  let step = cutoffs[0].step;
  for (const cutoff of cutoffs) if (frames <= cutoff.frames) step = cutoff.step;
  return step;
}
export function lookup(catalog, target, clock, rom, strategy) {
  const key=targetKey(target);
  const entry=catalog.entries.find(e=>targetKey(e.target)===key && e.clock===clock && e.game_version===rom);
  const strat=entry?.strategies.find(s=>s.name===strategy);
  requireThat(entry && strat,"unsupported_target","This target, clock, ROM or strategy cannot be shared.",422);
  return {entry,strat,key};
}
let verifiedCatalog;
export async function catalogFor(env) {
  const value=source;
  requireThat(env.CATALOG_GENERATION===value.generation,"catalog_unavailable","Catalog generation does not match configuration.",503);
  verifiedCatalog ||= (async()=>{validateCatalog(value);const digest=await sha256(canonical(value.entries));
    requireThat(digest===value.generation,"catalog_unavailable","Catalog digest does not match its source.",503);return value;})();
  await verifiedCatalog;
  return value;
}
