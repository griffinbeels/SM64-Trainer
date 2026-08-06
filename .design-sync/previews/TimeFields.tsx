import * as React from "react";
import { TimeFields } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 16, justifyItems: "start" }}>{children}</div>
);

/** Three boxes rather than one string, so a mistyped digit stays local. */
export const Standalone = () => (
  <S><TimeFields seconds={26.13} onCommit={() => {}} label="Personal best" /></S>
);

/** The tighter form, for a standards table row. */
export const Compact = () => (
  <S>
    <TimeFields seconds={21.9} onCommit={() => {}} compact label="Wario I" />
    <TimeFields seconds={38.7} onCommit={() => {}} compact label="Toad III" />
  </S>
);
