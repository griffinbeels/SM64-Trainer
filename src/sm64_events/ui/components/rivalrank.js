import {h} from "preact";
import {useEffect, useRef, useState} from "preact/hooks";
import htm from "htm";
import {RankIcon, getRankIconStyle} from "./rankicon.js";
import {rankAt, capName, divisionDigit} from "./caps.js";
import {Icon} from "./icons.js";
import {useRouteSwap} from "../routeswap.js";
import {mareloTuning} from "../marelotuning.js";
import {prefersReducedMotion} from "../useTween.js";

const html = htm.bind(h);

function RankPicture({display,motion}) {
  return display?.tier ? html`<${RankIcon} tier=${display.tier} division=${display.division} size=${48} ...${motion} />` : "–";
}

// One source transition at a time. Fast presses update the draft immediately
// and coalesce the next visual destination, preserving the squash/pop pivot.
function MovingRank({step, mode}) {
  const [shown, setShown] = useState({step, revision:0});
  const [reduced,setReduced] = useState(prefersReducedMotion);
  useEffect(()=>{
    const media=matchMedia("(prefers-reduced-motion: reduce)");
    const update=()=>setReduced(media.matches);
    media.addEventListener("change",update);
    return ()=>media.removeEventListener("change",update);
  },[]);
  const flight = useRef({busy:false, seen:false, wanted:step, mode});
  const current = shown.step == null ? {tier:null,division:null,fill:0,label:""} : {...rankAt(shown.step), step:shown.step, fill:0, label:String(shown.step)};
  const swap = useRouteSwap(shown.revision, current, mareloTuning(), {suppress:reduced});
  useEffect(() => {
    const state = flight.current;
    state.wanted = step;
    if (state.mode !== mode || step == null || shown.step == null || reduced) {
      state.mode = mode; state.busy = false; state.seen = false;
      if (shown.step !== step) setShown({step,revision:shown.revision});
    } else if (!state.busy && shown.step !== step) {
      state.busy = true; state.seen = false;
      setShown({step,revision:shown.revision+1});
    }
  }, [step, mode, shown.step, reduced]);
  useEffect(() => {
    const state = flight.current;
    if (swap) state.seen = true;
    else if (state.busy && state.seen) {
      state.busy = false; state.seen = false;
      if (state.wanted !== shown.step) {
        state.busy = true;
        setShown({step:state.wanted,revision:shown.revision+1});
      }
    }
  }, [swap, shown.revision]);
  const display = swap ? (swap.crossed ? swap.to : swap.from) : current;
  const motion = swap?.icon || {};
  const medalStyle = getRankIconStyle() === "medal" && swap
    ? {scale:`${motion.squashX ?? 1} ${motion.squashY ?? 1}`} : {};
  return html`<span class="rank-icon-slot rival-rank" style=${{...medalStyle,"--icon-size":"48px"}}
      data-progress=${swap?.progress ?? 1} data-displayed-step=${display?.step ?? ""}>
    <${RankPicture} display=${display} motion=${motion} />
  </span>`;
}

export function RivalRankStepper({name, step, offset, mode, canDecrease, canIncrease, change}) {
  const rank = step == null ? null : rankAt(step);
  const rankName = rank ? `${capName(rank.tier)} ${divisionDigit(rank.division)}` : "No personal best";
  return html`<div class="rival-rank-field">
    <span>${name}</span>
    <div class="rival-rank-stepper" role="group" aria-label=${name}>
      <button type="button" class="icon-button" aria-label=${`Decrease ${name.toLowerCase()}`}
        disabled=${!canDecrease} onclick=${()=>change(-1)}><${Icon} name="minus" /></button>
      <span title=${rankName} aria-label=${rankName}><${MovingRank} step=${step} mode=${mode} /></span>
      <button type="button" class="icon-button" aria-label=${`Increase ${name.toLowerCase()}`}
        disabled=${!canIncrease} onclick=${()=>change(1)}><${Icon} name="plus" /></button>
    </div>
    ${offset != null && html`<small>+${offset}</small>`}
  </div>`;
}
