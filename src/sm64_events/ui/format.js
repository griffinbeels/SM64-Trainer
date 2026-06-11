// src/sm64_events/ui/format.js — shared display formatting.
// fmtIgt mirrors Python's core/timefmt.py format_igt (M'SS"CC at 30 fps);
// keep the two in lockstep — parseTimeInput's single-digit-centisecond rule
// depends on the always-two-digit display format.
export function fmtIgt(frames) {
  const m = Math.floor(frames / 1800), s = Math.floor((frames % 1800) / 30),
        c = Math.floor(((frames % 30) * 100) / 30);
  return `${m}'${String(s).padStart(2, "0")}"${String(c).padStart(2, "0")}`;
}
