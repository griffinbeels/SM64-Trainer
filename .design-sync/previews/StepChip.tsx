import * as React from "react";
import { StepChip } from "sm64-trainer-ui";

const Track = ({ children }: any) => (
  <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", gap: 10,
               flexWrap: "wrap", alignItems: "center" }}>{children}</ol>
);
const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 16 }}>{children}</div>
);

/** The three states a route card shows: walked, current, still ahead. */
export const OnACard = () => (
  <S>
    <Track>
      <StepChip label="Course entry" state="done" />
      <StepChip label="Cannon" state="done" />
      <StepChip label="Island top" state="now" />
      <StepChip label="Star grab" state="ahead" />
    </Track>
  </S>
);

/** The two the editor shows: a required stop, or one you merely passed. */
export const InTheEditor = () => (
  <S>
    <Track>
      <StepChip label="Cannon" state="required" pressed onToggle={() => {}}
        title="Required: you fired from the cannon. Click if you were only passing through." />
      <StepChip label="Bridge" state="skipped" pressed={false} onToggle={() => {}}
        title="Only passing through. Click to require: you crossed the bridge." />
    </Track>
  </S>
);
