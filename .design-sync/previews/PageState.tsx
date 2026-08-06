import * as React from "react";
import { PageState } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gap: 18 }}>{children}</div>
);

/** A whole card standing in for content that has not arrived. */
export const LoadingAndOffline = () => (
  <S>
    <PageState kind="loading" title="Reading the sheet"
      message="Pulling every documented time for this star." />
    <PageState kind="offline" title="No connection to the tracker"
      message="The emulator is not being read right now." />
  </S>
);
