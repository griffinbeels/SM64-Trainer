import * as React from "react";
import { StandardsPanel } from "sm64-trainer-ui";

// The panel fetches its ladder when it opens, so a static card can only show
// the collapsed header honestly. That header is what a page actually leads
// with anyway: the rank, the PB, and the affordance to see the whole ladder.
const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 14 }}>{children}</div>
);

/** Collapsed, carrying the rank and PB it summarises. */
export const CollapsedHeader = () => (
  <S>
    <StandardsPanel
      entity="wf:chip"
      activeStrat="Xcam"
      strategies={["Xcam", "Standard", "Cannonless"]}
      sectionRank={{ rank: "Gold", division: "IV" }}
      sectionPb={{ igt_frames: 657 }}
    />
  </S>
);
