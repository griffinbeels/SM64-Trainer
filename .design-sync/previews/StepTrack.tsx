import * as React from "react";
import { StepTrack } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 16 }}>{children}</div>
);

const DETAIL = {
  steps: ["Course entry", "Cannon", "Island top", "Star grab"],
  progress: 2,
  total: 3,
  waiting_for: "Island top",
};

/** Where a run has got to, and what it is waiting for. */
export const MidRun = () => <S><StepTrack detail={DETAIL} /></S>;

/** The same track as a door into the step editor. */
export const Editable = () => <S><StepTrack detail={DETAIL} onEdit={() => {}} /></S>;
