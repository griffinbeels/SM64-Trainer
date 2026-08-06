import * as React from "react";
import { Hat } from "sm64-trainer-ui";

const Surface = ({ children }: { children: React.ReactNode }) => (
  <div
    style={{
      background: "#14161a",
      color: "#d8dee9",
      fontFamily: "Consolas, monospace",
      padding: "22px 20px",
      display: "flex",
      gap: 24,
      alignItems: "flex-end",
      flexWrap: "wrap",
    }}
  >
    {children}
  </div>
);

const Cell = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <figure style={{ margin: 0, display: "flex", flexDirection: "column", alignItems: "center", gap: 9 }}>
    {children}
    <figcaption style={{ fontSize: 11, opacity: 0.72, letterSpacing: 0.3 }}>{label}</figcaption>
  </figure>
);

/** Each tier's cap treatment: glow, metal, translucent, spots, outline. */
export const Treatments = () => (
  <Surface>
    <Cell label="Mario — glow">      <Hat tier="Mario" division="I" size={52} /></Cell>
    <Cell label="Metal — metal">     <Hat tier="Grandmaster" division="III" size={52} /></Cell>
    <Cell label="Vanish — translucent"><Hat tier="Master" division="III" size={52} /></Cell>
    <Cell label="Toadsworth — spots"><Hat tier="Silver" division="III" size={52} /></Cell>
    <Cell label="Capless — outline"> <Hat tier="Iron" division="V" size={52} /></Cell>
  </Surface>
);

/** Wings fold in as the climb progresses — the static ends of that motion. */
export const Wings = () => (
  <Surface>
    {[0, 0.25, 0.5, 0.75, 1].map((foldWings) => (
      <Cell key={foldWings} label={`foldWings ${foldWings}`}>
        <Hat tier="Diamond" division="II" size={52} foldWings={foldWings} />
      </Cell>
    ))}
  </Surface>
);
