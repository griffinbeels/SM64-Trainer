// One short-lived Node process for all Python formatting assertions. Import
// the real implementation; Python independently checks the returned values.
import { readFileSync } from "node:fs";
import { fmtIgt, fmtIgtShort, fmtSeconds, splitSeconds, joinTime } from "../../src/sm64_events/ui/format.js";

const { frames, cutoffs, cases } = JSON.parse(readFileSync(0, "utf8"));
const exact = frames.filter((value) => value % 3 === 0);
console.log(JSON.stringify({
  short: frames.map(fmtIgtShort),
  full: frames.map(fmtIgt),
  longForm: [690, 2629, 0].map(fmtIgt),
  fromSeconds: exact.map((value) => fmtSeconds(value / 30)),
  fromFrames: exact.map(fmtIgtShort),
  display: [23, 81.32, 102.20, 0, 59.99, 60].map(fmtSeconds),
  printedCutoffs: cutoffs.map(fmtSeconds),
  restoredCutoffs: cutoffs.map((value) => {
    const parts = splitSeconds(value);
    return joinTime(parts.minutes, parts.seconds, parts.centis);
  }),
  parts: cases.map(splitSeconds),
  printedCases: cases.map(fmtSeconds),
  joined: [joinTime("", "23", "00"), joinTime("", "", ""), joinTime("1", "21", "32")],
}));
