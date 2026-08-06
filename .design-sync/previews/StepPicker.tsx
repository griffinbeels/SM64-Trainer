import * as React from "react";
import { StepPicker } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20 }}>{children}</div>
);

const STEPS = [
  { node: "cannon", label: "Cannon", sentence: "you fired from the cannon" },
  { node: "bridge", label: "Bridge", sentence: "you crossed the bridge" },
  { node: "island", label: "Island top", sentence: "you stood on the island top" },
];

/** Which of the places you walked through this movement actually requires. */
export const SomeRequired = () => (
  <S><StepPicker steps={STEPS} required={new Set(["cannon", "island"])} onToggle={() => {}} /></S>
);

/** Nothing required is legal, and says so: any route start to finish counts. */
export const NoneRequired = () => (
  <S><StepPicker steps={STEPS} required={new Set<string>()} onToggle={() => {}} /></S>
);
