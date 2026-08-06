import * as React from "react";
import { RankBanner } from "sm64-trainer-ui";

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>{children}</div>
);

/** The band a page leads with, once a time exists. */
export const Ranked = () => (
  <S>
    <RankBanner label="Bowser in the Dark World" banner={{ rank: "Platinum", division: "II" }}
      hint="1.4s faster puts you in Wario I" identity="bitdw:xcam" />
    <RankBanner label="Whomp's Fortress — Chip Off Whomp's Block" banner={{ rank: "Gold", division: "IV" }}
      hint={`Waluigi III at 0'21"90`} identity="wf:chip" />
  </S>
);

/** A ladder exists but you have no time on it yet — the floor, never a blank. */
export const AtFloorAndUnranked = () => (
  <S>
    <RankBanner label="Tall, Tall Mountain — Scale the Mountain"
      banner={{ rank: null, reason: "unranked" }} atFloor
      hint="Standards exist for this star — your first time lands you somewhere on the ladder" />
    <RankBanner label="Rainbow Ride — Cruiser Crossing the Rainbow" banner={null}
      hint="No standards for this strategy yet" />
  </S>
);
