import * as React from "react";
import { Progress } from "sm64-trainer-ui";

// One segment per session; a break between segments is what makes "I got
// faster within a sitting" separable from "I got faster over weeks".
const seconds = (value: number) => Math.round(value * 30);
const PROG = {
  sessions: [
    {
      id: 1,
      points: [
        { attempt_id: 11, igt_frames: seconds(29.4), rta_frames: seconds(31.0), is_pb_igt: false },
        { attempt_id: 12, igt_frames: seconds(28.1), rta_frames: seconds(29.6), is_pb_igt: false },
        { attempt_id: 13, igt_frames: seconds(27.3), rta_frames: seconds(28.8), is_pb_igt: true },
        { attempt_id: 14, igt_frames: seconds(27.9), rta_frames: seconds(29.2), is_pb_igt: false },
      ],
    },
    {
      id: 2,
      points: [
        { attempt_id: 21, igt_frames: seconds(27.6), rta_frames: seconds(29.0), is_pb_igt: false },
        { attempt_id: 22, igt_frames: seconds(26.4), rta_frames: seconds(27.9), is_pb_igt: true },
        { attempt_id: 23, igt_frames: seconds(26.13), rta_frames: seconds(27.6), is_pb_igt: true },
      ],
    },
  ],
};

const S = ({ children }: any) => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20 }}>{children}</div>
);

/** Two sittings on one star, on the in-game clock. */
export const TwoSessionsOnIGT = () => (
  <S><Progress prog={PROG} clock="igt" onPick={() => {}} /></S>
);

/** The same attempts measured on real time instead. */
export const TheSameOnRTA = () => (
  <S><Progress prog={PROG} clock="rta" onPick={() => {}} /></S>
);
