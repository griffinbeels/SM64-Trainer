import * as React from "react";
import { CellRow, DS_ASSET, PracticeCell } from "sm64-trainer-ui";

const STAR = DS_ASSET("/ui/assets/star_2.png");

/** A row of cells that cross-fades when its contents change identity, rather
 *  than cutting between two correct states. */
export const ARowOfCells = () => (
  <div style={{ background: "var(--bg)", fontFamily: "Consolas, monospace", padding: 20 }}>
    <CellRow class="cell-row">
      <PracticeCell active iconSrc={STAR} name="Chip Off Whomp's Block" sub={`0'21"90`}
        strat="Xcam" rank={{ rank: "Gold", division: "IV" }} hasStandards onPick={() => {}} />
      <PracticeCell active={false} iconSrc={STAR} name="Shoot into the Wild Blue" sub={`0'19"07`}
        strat="Cannonless" rank={{ rank: "Diamond", division: "III" }} hasStandards dimIdle onPick={() => {}} />
      <PracticeCell active={false} iconSrc={STAR} name="Fall onto the Caged Island" sub="no time yet"
        strat="Standard" rank={null} hasStandards dimIdle onPick={() => {}} />
    </CellRow>
  </div>
);
