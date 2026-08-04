// src/sm64_events/ui/nextstep.js — the "time to go" wording, pulled out of
// ranks.js so it is import-free and node can drive it directly
// (tests/test_ui_nextstep.py) the same way climbplan.js/climbcurve.js/
// caps.js already are for the climb itself.
//
// Covers three of RankBanner's four `nextStepMode` values -- "always" and
// "compact" (spec practice-log-entity-cards, round 3: Griffin, "I think we
// tighten up the wording, and no need to tell them what they're ranking up
// to... What we DO need is an indicator of the timesave needed") plus the
// shared "no next step" sentinel both fall back to. "classic" (today's
// wording everywhere but the practice log: "→ Waluigi 3 · 0.22s to rank up")
// stays inline in ranks.js's own `nextStepWords`, unchanged from before this
// landed, because it renders a bold destination-rank `<b>` tag and needs the
// real `html` (htm-bound) helper this module deliberately has no access to.
// "hidden"/"hover" never call this at all -- ranks.js resolves those to
// "nothing inline" and a separate popup respectively, above this function.
export function tightenedNextStepText(mode, nextLabel, gap) {
  // There IS a next step, we are just not naming it -- "top rank" is the
  // one sentinel every mode shares with "classic" for genuinely being at
  // the ceiling.
  if (!nextLabel) return "top rank";
  // Mid-climb the exact time delta is withheld until it settles (the
  // server's `next_gap_cs` only describes the FINAL rank) -- printing
  // nothing here is deliberate, not a missing case: the line is faded to
  // zero for this whole stretch regardless (`.claude/rules/ui-climb.md`'s
  // "may only change while invisible" rule), so there is nothing for a
  // reader to see change either way.
  if (gap == null) return "";
  return mode === "compact" ? `${gap}s` : `${gap}s to go`;
}
