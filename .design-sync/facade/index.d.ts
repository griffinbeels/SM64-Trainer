import type * as React from "react";

/**
 * A rank tier, hardest first. The ladder order IS this union's order and it
 * mirrors the server's own rank list. Each tier draws as a Mario-cast cap:
 * Mario, Metal, Vanish, Luigi, Wario, Waluigi, Toadsworth, Toad, Capless.
 */
export type RankTier =
  | "Mario" | "Grandmaster" | "Master" | "Diamond" | "Platinum"
  | "Gold" | "Silver" | "Bronze" | "Iron";

/** Division within a tier, bottom of the tier first. */
export type RankDivision = "V" | "IV" | "III" | "II" | "I";

/**
 * Draws a rank as an icon. This is the dispatcher every surface calls: it
 * reads the user's chosen icon STYLE (caps or medals) and renders that
 * style, so no call site ever learns which style is active.
 */
export interface RankIconProps {
  /** Which rank to draw. An unknown tier draws the neutral fallback cap. */
  tier: RankTier;
  /** Division numeral, or null for a tier shown without one. */
  division?: RankDivision | null;
  /** Rendered height in px; width follows the cap's own aspect ratio. */
  size?: number;
  /** Tooltip override. Defaults to the cap's name plus its division digit. */
  title?: string | null;
  /** Animate the cap's brim flap. */
  flap?: boolean;
  /** 0..1 — folds the winged tiers' wings in. */
  foldWings?: number;
}
export declare const RankIcon: React.FC<RankIconProps>;

/** The cap style itself. Prefer RankIcon unless you must pin the style. */
export interface HatProps extends RankIconProps {
  /** 0..1 — grows the wings out, for the rank-up climb. */
  growWings?: number;
  growProgress?: number;
  foldProgress?: number | null;
  flapPhase?: number | null;
  roll?: number | null;
  squashX?: number | null;
  squashY?: number | null;
  shake?: number | null;
  sparkle?: number | null;
}
export declare const Hat: React.FC<HatProps>;

/** The medal style. Prefer RankIcon unless you must pin the style. */
export interface MedalProps {
  tier: RankTier;
  division?: RankDivision | null;
  size?: number;
  title?: string | null;
}
export declare const Medal: React.FC<MedalProps>;

export declare const ICON_STYLES: Record<string, { label: string; render: React.FC<any> }>;
export declare const DEFAULT_ICON_STYLE: string;
export declare function getRankIconStyle(): string;
export declare function setRankIconStyle(style: string): void;
