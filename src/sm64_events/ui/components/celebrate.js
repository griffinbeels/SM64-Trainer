// src/sm64_events/ui/components/celebrate.js — celebration PREFERENCES.
//
// This file used to also own the two hand-rolled SCOPE overlays (TierRankUp's
// fill -> flip -> hold, DivisionRankUp's top banner) — a KNOWN DEVIATION
// flagged since 2026-07-26, because they ran their own beat machine instead
// of the climb engine every other rank-up on screen uses. They are gone as of
// 2026-07-28: components/marelocelebrate.js's `MareloCelebration` replaces
// both with one overlay that flies the REAL route rank card to the centre and
// hands it to ui/rankclimb.js, the same engine the star banners run,
// amplified by one tunable (ui/marelotuning.js). See app.js for the mount and
// .claude/rules/ui-climb.md for the beat-by-beat.
//
// The two ENTITY treatments that used to live here (a glow pop for a
// division-up, a small toast for a tier-up, both on the active-target card)
// were deleted earlier, with task 0012, 2026-07-26. The user's report was
// that they "kinda just… appear", and the answer was not a better toast: the
// rank-up is performed by the rank BANNER itself climbing (ui/rankclimb.js),
// on the very bar the rank already lives on.
//
// The "Celebrations" pref lives in ui/celebrations.js and is re-exported here
// so header.js's import is unchanged. It moved because the level-up climb has
// to honour it too, and rankclimb.js importing THIS file would close a cycle
// (celebrate.js -> ranks.js -> rankclimb.js).
export { celebrationsEnabled, setCelebrationsEnabled,
         CLIMB_SKIP_STYLES, climbSkipStyle, setClimbSkipStyle } from "../celebrations.js";
