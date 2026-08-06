import * as React from "react";
import { RankIcon } from "sm64-trainer-ui";

// Every surface in the app draws ranks on the observatory's dark field, and
// several tiers are deliberately pale (Toad, Toadsworth) — on white they read
// as blank. The cards carry that field with them.
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

// Hardest first — the ladder order is the rank list's own key order.
const LADDER = [
  "Mario", "Grandmaster", "Master", "Diamond", "Platinum",
  "Gold", "Silver", "Bronze", "Iron",
] as const;

/** The whole ladder at a glance — one cap per tier, the primary variant axis. */
export const TheLadder = () => (
  <Surface>
    {LADDER.map((tier) => (
      <Cell key={tier} label={tier}>
        <RankIcon tier={tier} division="III" size={46} />
      </Cell>
    ))}
  </Surface>
);

/** Divisions inside one tier, bottom of the tier first. */
export const Divisions = () => (
  <Surface>
    {(["V", "IV", "III", "II", "I"] as const).map((division) => (
      <Cell key={division} label={`Wario ${division}`}>
        <RankIcon tier="Platinum" division={division} size={46} />
      </Cell>
    ))}
  </Surface>
);

/** The sizes real surfaces ask for: a table cell, a chip, a card, a banner. */
export const Sizes = () => (
  <Surface>
    {[14, 18, 28, 46, 64].map((size) => (
      <Cell key={size} label={`${size}px`}>
        <RankIcon tier="Diamond" division="II" size={size} />
      </Cell>
    ))}
  </Surface>
);

/** The bottom of the ladder — what an unranked-but-rankable entity shows. */
export const FloorOfTheLadder = () => (
  <Surface>
    <Cell label="Capless 5 — has standards, no time yet">
      <RankIcon tier="Iron" division="V" size={46} />
    </Cell>
    <Cell label="Toad 1 — first tier most runners clear">
      <RankIcon tier="Bronze" division="I" size={46} />
    </Cell>
  </Surface>
);
