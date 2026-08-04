// src/sm64_events/ui/components/timefields.js — THE way a time is TYPED.
//
// Three boxes reading `{m}'{s}"{cc}`, not one box taking a decimal. User,
// 2026-08-03: "People generally communicate in terms of XX'YY"ZZ when talking
// about times, not 105 seconds. So, our data entry system should match how
// humans actually communicate their times."
//
// Three fields rather than one parsed field is the whole point: there is
// nothing to parse and nothing to get wrong. A single box would have to decide
// what `1.42` means (1.42 seconds? 1'42"?) and would be ambiguous exactly
// where the two notations overlap — which is most short times.
//
// SECONDS remain the stored unit and the value this emits, so no consumer,
// endpoint or file format changes; the split/join lives in ui/format.js beside
// the formatter that prints the same three parts, so the editor and the
// display can never disagree about where the boundaries are.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { joinTime, splitSeconds } from "../format.js";

const html = htm.bind(h);

// A blank BOX is a zero; three blank boxes are "no time at all", which is what
// clears a cutoff. Those are different answers and the caller gets to tell
// them apart — an empty ladder row must not save as 0'00"00.
function readFields(fields) {
  if (["minutes", "seconds", "centis"].every((k) => fields[k] === "")) return null;
  return joinTime(fields.minutes, fields.seconds, fields.centis);
}

export function TimeFields({ seconds, onCommit, compact = false, label = "" }) {
  const [fields, setFields] = useState(() => toFields(seconds));
  // THE latest fields, read by every handler instead of the closure's copy.
  // Preact commits after the tick, so tabbing quickly through the three boxes
  // hands the second and third handlers the state from BEFORE the first one's
  // commit — each then writes {...stale, myField} and silently reverts its
  // neighbours. Caught by driving the real editor: typing 1 / 21 / 32 saved
  // `1'09"32`, keeping the seconds the cell already had. Same trap
  // `.claude/rules/ui-core.md` names for driving two controls in one tick,
  // except here the user is the one doing it.
  const latest = useRef(fields);

  function write(next) {
    latest.current = next;
    setFields(next);
    return next;
  }

  useEffect(() => { write(toFields(seconds)); }, [seconds]);

  const box = (key, placeholder, max, width) => html`<input
      type="number" min="0" max=${max} step="1" inputmode="numeric"
      class="timefield" style=${`--tf-width:${width}`}
      placeholder=${placeholder} value=${fields[key]}
      aria-label=${`${label} ${key}`.trim()}
      onblur=${(e) => onCommit(readFields(
        write({ ...latest.current, [key]: pad(key, e.target.value) })))}
      oninput=${(e) => write({ ...latest.current, [key]: e.target.value })} />`;

  return html`<span class=${`timefields${compact ? " compact" : ""}`}>
    ${box("minutes", "0", 59, "22px")}<span class="timefield-sep">'</span>
    ${box("seconds", "00", 59, "28px")}<span class="timefield-sep">"</span>
    ${box("centis", "00", 99, "28px")}
  </span>`;
}

function toFields(seconds) {
  if (seconds == null || seconds === "") {
    return { minutes: "", seconds: "", centis: "" };
  }
  const parts = splitSeconds(seconds);
  return { minutes: parts.minutes ? String(parts.minutes) : "",
           seconds: String(parts.seconds).padStart(2, "0"),
           centis: String(parts.centis).padStart(2, "0") };
}

// Typing "5" in the centiseconds box means five HUNDREDTHS, not five tenths —
// the display is always two digits, so the entry is read the same way rather
// than guessing at intent. Seconds pad for looks; minutes never do.
function pad(key, raw) {
  const text = String(raw).trim();
  if (text === "" || key === "minutes") return text;
  return text.padStart(2, "0");
}
