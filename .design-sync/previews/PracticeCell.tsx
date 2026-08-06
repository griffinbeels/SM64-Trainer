import * as React from "react";
import { DS_ASSET, PracticeCell } from "sm64-trainer-ui";

// Callers supply the icon, which is why 1.9 MB of star art never has to ship
// in the bundle. A generic star stands in here.
const STAR = DS_ASSET("/ui/assets/star_1.png");

const Grid = ({ children }: any) => (
  <div style={{ background: "var(--bg)", fontFamily: "Consolas, monospace", padding: 20,
                display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start" }}>{children}</div>
);

/** The selector's unit: several cells, one of them the current target. */
export const TheSelector = () => (
  <Grid>
    <PracticeCell active iconSrc={STAR} name="Chip Off Whomp's Block" sub={`0'21"90`}
      strat="Xcam" rank={{ rank: "Gold", division: "IV" }} hasStandards onPick={() => {}} />
    <PracticeCell active={false} iconSrc={STAR} name="To the Top of the Fortress" sub={`0'26"13`}
      strat="Standard" rank={{ rank: "Platinum", division: "II" }} hasStandards dimIdle onPick={() => {}} />
    <PracticeCell active={false} iconSrc={STAR} name="Shoot into the Wild Blue" sub="no time yet"
      strat="Cannonless" rank={null} hasStandards dimIdle onPick={() => {}} />
    <PracticeCell active={false} iconSrc={STAR} name="Red Coins on the Floating Isle" sub="no standards"
      strat={null} rank={null} dimIdle onPick={() => {}} />
  </Grid>
);

/** A result with a caveat carries its mark on the cell, not in a footnote. */
export const WithCaveat = () => (
  <Grid>
    <PracticeCell active iconSrc={STAR} name="Blast Away the Wall" sub={`0'14"53`}
      strat="Xcam" rank={{ rank: "Diamond", division: "I" }} hasStandards
      caveat="reset" onPick={() => {}} onEdit={() => {}} />
  </Grid>
);

/** The corner-badge form the picker grid uses instead of an in-flow rank row. */
export const RankAsBadge = () => (
  <Grid>
    <PracticeCell active={false} iconSrc={STAR} name="Big Bob-omb on the Summit" sub={`0'38"70`}
      strat="Standard" rank={{ rank: "Silver", division: "III" }} hasStandards
      rankBadge onPick={() => {}} />
    <PracticeCell active={false} iconSrc={STAR} name="Footrace with Koopa the Quick" sub="no time yet"
      strat="Standard" rank={null} hasStandards rankBadge onPick={() => {}} />
  </Grid>
);
