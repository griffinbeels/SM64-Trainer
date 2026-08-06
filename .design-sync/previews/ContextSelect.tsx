import * as React from "react";
import { ContextSelect } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 14, maxWidth: 460 }}>{children}</div>
);

const COURSES: Array<[string, string]> = [
  ["bob", "Bob-omb Battlefield"], ["wf", "Whomp's Fortress"], ["jrb", "Jolly Roger Bay"],
  ["ccm", "Cool, Cool Mountain"], ["bitdw", "Bowser in the Dark World"],
];
const STRATS: Array<[string, string]> = [
  ["xcam", "Xcam"], ["standard", "Standard"], ["cannonless", "Cannonless"],
];

/** The course/star/strategy flow the Library reuses rather than reinvents. */
export const ThePickerFlow = () => (
  <S>
    <ContextSelect icon="target" label="Course" options={COURSES} value="wf" onChange={() => {}} />
    <ContextSelect icon="rank" label="Strategy" options={STRATS} value="xcam" onChange={() => {}} />
  </S>
);

/** Nothing to choose from yet — say so rather than show an empty control. */
export const NothingToChoose = () => (
  <S>
    <ContextSelect icon="segments" label="Segment" options={[]} value=""
      onChange={() => {}} empty="No segments defined for this course" />
  </S>
);
