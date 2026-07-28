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
