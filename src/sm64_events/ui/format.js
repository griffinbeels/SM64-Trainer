// src/sm64_events/ui/format.js — shared display formatting.
// fmtIgt is the JS half of Python's core/timefmt.py::format_igt (M'SS"CC at
// 30 fps). They are pinned to each other by tests/test_cross_language_parity.py
// — not by this comment, which is all that held them until 2026-07-28. The
// drift they are one edit away from: JS `/` is float division where Python `//`
// is not, and RANKS ARE GRADED ON DISPLAYED CENTISECONDS
// (ranks/classify.py::display_cs), so a formatting drift is a grading drift.
// parseTimeInput's single-digit-centisecond rule also depends on the
// always-two-digit display format.
export function fmtIgt(frames) {
  const m = Math.floor(frames / 1800), s = Math.floor((frames % 1800) / 30),
        c = Math.floor(((frames % 30) * 100) / 30);
  return `${m}'${String(s).padStart(2, "0")}"${String(c).padStart(2, "0")}`;
}

// A time under a minute drops its empty minutes field: 23 seconds reads
// `23"00`, not `0'23"00` (user, 2026-08-03). ONE rule, expressed as a
// transformation OF fmtIgt rather than as a second formatter, so the two can
// never disagree about the seconds and centiseconds — which is the half that
// grading depends on (ranks/classify.py::display_cs).
function dropEmptyMinutes(text) {
  return text.startsWith("0'") ? text.slice(2) : text;
}

export function fmtIgtShort(frames) {
  return dropEmptyMinutes(fmtIgt(frames));
}

// A rank standard is stored in SECONDS with centisecond precision, not in
// frames, so it cannot route through fmtIgt without a rounding trip that
// would move a cutoff. It formats from centiseconds directly and wears the
// same shape, and tests/test_ui_time_format.py pins the two against each
// other on every frame-exact value so the shapes cannot drift apart.
export function fmtSeconds(seconds) {
  const cs = Math.round(seconds * 100);
  const m = Math.floor(cs / 6000), s = Math.floor((cs % 6000) / 100),
        c = cs % 100;
  return dropEmptyMinutes(
    `${m}'${String(s).padStart(2, "0")}"${String(c).padStart(2, "0")}`);
}
