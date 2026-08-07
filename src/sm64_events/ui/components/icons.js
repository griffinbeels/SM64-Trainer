import { h } from "preact";
import htm from "htm";

const html = htm.bind(h);

// Small, dependency-free outline icon set for the zero-build UI.  Icons are
// decorative companions to visible labels; controls provide their own names.
const PATHS = {
  practice: "M12 2l1.7 4.9L19 8.6l-4.2 3.1 1.6 5.1-4.4-3-4.4 3 1.6-5.1L5 8.6l5.3-1.7L12 2zM19 2v4M17 4h4",
  run: "M8 5v14l11-7L8 5z",
  compare: "M3 5h7v14H3V5zm11 0h7v14h-7V5zM10 9h4m-4 6h4",
  routes: "M5 6a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm14 16a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM5 6v3c0 2 2 3 4 3h6c2 0 4 1 4 3v3M17 3h4v4",
  segments: "M8 4H4v16h4M16 4h4v16h-4M11 12h2",
  sessions: "M5 5h14M5 12h14M5 19h14M3 5h.01M3 12h.01M3 19h.01",
  feed: "M3 12h4l2-7 4 14 2-7h6",
  settings: "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7zm0-6 1.1 2.2 2.4.6 2.1-1.2 2.3 2.3-1.2 2.1.6 2.4L21.5 12l-2.2 1.1-.6 2.4 1.2 2.1-2.3 2.3-2.1-1.2-2.4.6L12 21.5l-1.1-2.2-2.4-.6-2.1 1.2-2.3-2.3 1.2-2.1-.6-2.4L2.5 12l2.2-1.1.6-2.4-1.2-2.1 2.3-2.3 2.1 1.2 2.4-.6L12 2.5z",
  more: "M4 4h6v6H4V4zm10 0h6v6h-6V4zM4 14h6v6H4v-6zm10 0h6v6h-6v-6z",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 4v5l3 2",
  target: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 4a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0-6v4m0 14v4M1 12h4m14 0h4",
  rank: "M8 3h8v8l-4 3-4-3V3zm1 10v8l3-2 3 2v-8",
  chevron: "M8 10l4 4 4-4",
  pause: "M8 5v14M16 5v14",
  restart: "M20 7v5h-5M4 17v-5h5M6.1 8a7 7 0 0 1 11.3-2.2L20 9M4 15l2.6 3.2A7 7 0 0 0 17.9 16",
  updates: "M12 3v12m0 0-4-4m4 4 4-4M5 19h14",
  plus: "M12 5v14M5 12h14",
  sort: "M8 6h11M8 12h8M8 18h5M3 4v16m0 0-2-2m2 2 2-2",
  eyeOff: "M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 4.2A11.7 11.7 0 0 1 12 4c5 0 8.5 4 9.5 6a12 12 0 0 1-2.4 3.4M6.2 6.2A13 13 0 0 0 2.5 12c1 2 4.5 6 9.5 6a10 10 0 0 0 3-.5",
  menu: "M4 6h16M4 12h16M4 18h16",
  close: "M5 5l14 14M19 5L5 19",
  play: "M8 5v14l11-7L8 5z",
  bookmark: "M6 3h12v18l-6-4-6 4V3z",
  edit: "M4 20h4l11-11-4-4L4 16v4zm9-13 4 4M4 20h16",
  trash: "M4 7h16M9 7V4h6v3m3 0-1 14H7L6 7m4 4v6m4-6v6",
  save: "M5 3h12l2 2v16H5V3zm3 0v6h8V3M8 21v-7h8v7",
  arrowUp: "M12 19V5m-6 6 6-6 6 6",
  arrowDown: "M12 5v14m-6-6 6 6 6-6",
  upload: "M12 16V4m-5 5 5-5 5 5M5 20h14",
  download: "M12 4v12m-5-5 5 5 5-5M5 20h14",
  check: "M5 12l4 4L19 6",
  shield: "M12 3l7 3v5c0 4.6-2.8 8.1-7 10-4.2-1.9-7-5.4-7-10V6l7-3z",
  stepBack: "M7 5v14M18 6l-8 6 8 6V6z",
  stepForward: "M17 5v14M6 6l8 6-8 6V6z",
  expand: "M8 3H3v5M16 3h5v5M8 21H3v-5m13 5h5v-5",
  split: "M12 3v6m0 0-5 5v7m5-12 5 5v7",
  merge: "M7 3v7l5 5v6m5-18v7l-5 5",
  library: "M12 6c-1.6-1.1-4-1.6-6-1.1v13c2-.5 4.4 0 6 1.1 1.6-1.1 4-1.6 6-1.1v-13c-2-.5-4.4 0-6 1.1zM12 6v13",
};

export function Icon({ name, size = 18, className = "" }) {
  return html`<svg class=${`ui-icon ${className}`} width=${size} height=${size}
      viewBox="0 0 24 24" fill="none" stroke="currentColor"
      stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
      aria-hidden="true" focusable="false">
    <path d=${PATHS[name] || PATHS.more}></path>
  </svg>`;
}
