import * as React from "react";
import { Medal } from "sm64-trainer-ui";

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

const LADDER = [
  "Mario", "Grandmaster", "Master", "Diamond", "Platinum",
  "Gold", "Silver", "Bronze", "Iron",
] as const;

/** The alternate icon style: the same ladder drawn as medals instead of caps. */
export const TheLadder = () => (
  <Surface>
    {LADDER.map((tier) => (
      <Cell key={tier} label={tier}>
        <Medal tier={tier} division="III" size={46} />
      </Cell>
    ))}
  </Surface>
);

/** The division digit is what a medal carries instead of a cap's wings. */
export const Divisions = () => (
  <Surface>
    {(["V", "IV", "III", "II", "I"] as const).map((division) => (
      <Cell key={division} label={`Waluigi ${division}`}>
        <Medal tier="Gold" division={division} size={46} />
      </Cell>
    ))}
  </Surface>
);
