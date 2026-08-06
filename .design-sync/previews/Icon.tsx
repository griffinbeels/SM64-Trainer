import * as React from "react";
import { Icon } from "sm64-trainer-ui";

const NAMES = ["practice", "run", "compare", "routes", "segments", "sessions", "feed",
  "settings", "more", "clock", "target", "rank", "chevron", "pause", "restart", "updates",
  "plus", "sort", "eyeOff", "menu", "close", "play", "bookmark", "edit", "trash", "save",
  "arrowUp", "arrowDown", "upload", "download", "check", "shield", "stepBack",
  "stepForward", "expand", "split", "merge"];

/** The whole set at the size a nav rail uses. */
export const TheSet = () => (
  <div style={{ background: "var(--bg)", color: "var(--text)", fontFamily: "Consolas, monospace",
                padding: 20, display: "grid", gridTemplateColumns: "repeat(8, 1fr)", gap: 16 }}>
    {NAMES.map((name) => (
      <figure key={name} style={{ margin: 0, display: "flex", flexDirection: "column",
                                  alignItems: "center", gap: 6 }}>
        <Icon name={name} size={22} />
        <figcaption style={{ fontSize: 10, opacity: 0.7 }}>{name}</figcaption>
      </figure>
    ))}
  </div>
);

/** Sized in px and inheriting colour, so an icon sits on any accent. */
export const SizesAndColour = () => (
  <div style={{ background: "var(--bg)", fontFamily: "Consolas, monospace", padding: 20,
                display: "flex", gap: 22, alignItems: "center" }}>
    {[14, 18, 24, 32].map((size) => (
      <span key={size} style={{ color: "var(--text)" }}><Icon name="target" size={size} /></span>
    ))}
    <span style={{ color: "var(--gold)" }}><Icon name="rank" size={32} /></span>
    <span style={{ color: "var(--coral)" }}><Icon name="close" size={32} /></span>
    <span style={{ color: "var(--green)" }}><Icon name="check" size={32} /></span>
  </div>
);
