import * as React from "react";
import { InlineState } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 12 }}>{children}</div>
);

/** One line, for a state that does not deserve a whole card. */
export const LoadingAndError = () => (
  <S>
    <InlineState kind="loading">Loading this runner's times</InlineState>
    <InlineState kind="error">That video link is no longer reachable</InlineState>
  </S>
);
