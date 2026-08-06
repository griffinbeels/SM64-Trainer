import * as React from "react";
import { CardSelect } from "sm64-trainer-ui";

const Header = ({ children }: any) => (
  <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 10,
                padding: "10px 14px", display: "flex", alignItems: "center", gap: 8,
                color: "var(--text)" }}>{children}</div>
);

/** The bare select for a card header that already carries its own label. */
export const InsideACardHeader = () => (
  <div style={{ background: "var(--bg)", fontFamily: "Consolas, monospace", padding: 20,
                display: "grid", gap: 12, maxWidth: 460 }}>
    <Header>
      <span style={{ flex: 1 }}>Sort documented times by</span>
      <CardSelect id="sort" name="sort" label="Sort by" value="time" onChange={() => {}}
        options={[["time", "Time"], ["player", "Player"], ["date", "Date added"]]} />
    </Header>
    <Header>
      <span style={{ flex: 1 }}>Region</span>
      <CardSelect id="region" name="region" label="Region" value="jp" onChange={() => {}}
        options={[["jp", "JP"], ["us", "US"], ["both", "Both"]]} />
    </Header>
  </div>
);

/** The current value stays listed even when a filter would drop it — a value
 *  missing from the options renders BLANK and reads as unset. */
export const ValueOutsideTheFilter = () => (
  <div style={{ background: "var(--bg)", fontFamily: "Consolas, monospace", padding: 20, maxWidth: 460 }}>
    <Header>
      <span style={{ flex: 1 }}>Strategy</span>
      <CardSelect id="strat" name="strat" label="Strategy" value="retired-strat" onChange={() => {}}
        options={[["retired-strat", "Old Xcam (retired)"], ["xcam", "Xcam"], ["standard", "Standard"]]} />
    </Header>
  </div>
);
