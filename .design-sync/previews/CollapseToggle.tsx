import * as React from "react";
import { CollapseToggle } from "sm64-trainer-ui";

const Row = ({ children }: any) => (
  <div style={{ background: "var(--surface)", border: "1px solid var(--border)",
                borderRadius: 10, padding: "10px 14px", display: "flex",
                alignItems: "center", gap: 10, color: "var(--text)" }}>{children}</div>
);

/** Both states, because a disclosure is only legible as a pair. */
export const BothStates = () => (
  <div style={{ background: "var(--bg)", fontFamily: "Consolas, monospace", padding: 20,
                display: "grid", gap: 12 }}>
    <Row><span style={{ flex: 1 }}>Rank standards</span>
      <CollapseToggle collapsed={false} toggle={() => {}} label="rank standards" /></Row>
    <Row><span style={{ flex: 1 }}>Documented strategies</span>
      <CollapseToggle collapsed toggle={() => {}} label="documented strategies" /></Row>
  </div>
);
