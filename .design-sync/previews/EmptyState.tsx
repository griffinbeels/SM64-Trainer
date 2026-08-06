import * as React from "react";
import { EmptyState } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 18 }}>{children}</div>
);

/** What a panel shows before it has anything: a cast member, the gap, the fix. */
export const NothingYet = () => (
  <S>
    <EmptyState headline="No attempts on this star yet"
      hint="Grab it in game and the first one lands here." />
  </S>
);

/** The headline alone, when the next action is obvious from context. */
export const HeadlineOnly = () => (
  <S>
    <EmptyState headline="No strategies documented for this star" />
  </S>
);
