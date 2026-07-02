// src/sm64_events/ui/components/goldstar.js — self-contained SVG gold star.
// A reusable visual primitive: no sprite art, no external asset, so it fits the
// packaged-exe / strict-CSP posture. Used by stagebanner.js's StarRow; the
// shaded/eyes flags keep the SM64 flourishes a one-line switch at the call site.
import { h } from "preact";
import { useRef } from "preact/hooks";
import htm from "htm";

const html = htm.bind(h);

// Five-point star, viewBox 0..100, one point up. Chunky inner radius gives the
// stubby SM64 silhouette.
const STAR_PATH =
  "M50,3 L61.8,33.8 L94.7,35.5 L69,56.2 L77.6,88 " +
  "L50,70 L22.4,88 L31,56.2 L5.3,35.5 L38.2,33.8 Z";

// SVG gradient ids are document-global, so each shaded instance needs a unique
// one. Assigned once per mount (lazy ref init) so re-renders keep a stable id.
let gradSeq = 0;

export function GoldStar({ size = 64, shaded = true, eyes = false,
                           active = false, dim = false }) {
  const idRef = useRef(null);
  if (idRef.current === null) idRef.current = `gs${++gradSeq}`;
  const gid = idRef.current;

  const fill = shaded ? `url(#${gid})` : "#ffcf45";
  const stroke = shaded ? "#a5670c" : "#c79017";
  const filter = active
    ? "drop-shadow(0 0 6px rgba(255,215,95,.85)) drop-shadow(0 0 2px rgba(255,215,95,.9))"
    : dim ? "saturate(.85)" : "none";
  const opacity = active ? 1 : dim ? 0.72 : 1;

  return html`<svg viewBox="0 0 100 100" width=${size} height=${size}
      style=${`display:block;overflow:visible;filter:${filter};opacity:${opacity}`}>
    ${shaded && html`<defs>
      <radialGradient id=${gid} cx="42%" cy="34%" r="72%">
        <stop offset="0%" stop-color="#fff6c9" />
        <stop offset="34%" stop-color="#ffe271" />
        <stop offset="72%" stop-color="#f5b722" />
        <stop offset="100%" stop-color="#d98a12" />
      </radialGradient>
    </defs>`}
    <path d=${STAR_PATH} fill=${fill} stroke=${stroke} stroke-width="3"
          stroke-linejoin="round" />
    ${shaded && html`<path d=${STAR_PATH} fill="none" stroke="#fff8d6"
          stroke-opacity=".55" stroke-width="1.2"
          transform="scale(.82) translate(11,7)" />`}
    ${eyes && html`<g fill="#241a05">
      <ellipse cx="41" cy="46" rx="3.6" ry="5.2" />
      <ellipse cx="59" cy="46" rx="3.6" ry="5.2" />
    </g>`}
  </svg>`;
}
