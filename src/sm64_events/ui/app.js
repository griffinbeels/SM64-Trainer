// src/sm64_events/ui/app.js — responsive application shell + mounted views
import { h, render } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { useTracker } from "./store.js";
import { Header } from "./components/header.js";
import { Practice } from "./components/practice.js";
import { Feed } from "./components/feed.js";
import { Segments } from "./components/segments.js";
import { Routes } from "./components/routes.js";
import { Run } from "./components/runview.js";
import { Compare } from "./components/compare.js";
import { Library } from "./components/library.js";
import { UpdatePopup } from "./components/update.js";
import { RecordingDot } from "./components/replay.js";
import { Icon } from "./components/icons.js";
import { RankPage } from "./components/rankpage.js";
import { RankUpCelebration } from "./components/marelocelebrate.js";

const html = htm.bind(h);

const NAV_GROUPS = [
  // "Library" (the sheet-library tab, below) goes HERE rather than into the
  // "Library" group two rows down -- that group already exists (Sessions /
  // Live feed) and putting a tab of the same name inside it renders the word
  // twice (task-3-caveats.md point 2). It is a play-time reference tool, and
  // Task 6 removes Compare from this group once the Library absorbs it -- so
  // it ends up standing exactly where Compare stood. Last item on purpose.
  ["Play", [
    ["Practice", "practice"],
    ["Run", "run"],
    ["Rank", "rank"],
    ["Compare", "compare"],
    ["Library", "library"],
  ]],
  ["Build", [
    ["Routes", "routes"],
    ["Segments", "segments"],
  ]],
  ["Library", [
    ["Sessions", "sessions"],
    ["Live feed", "feed"],
  ]],
];

// Every destination the shell has, flat. The narrow layouts DERIVE from this
// rather than restating it, and that is the whole point: MobileNav and
// MobileMore used to carry their own hand-written lists, Rank appeared in
// NAV_GROUPS and in NEITHER of them, and it was therefore unreachable at every
// width at or below 760px — confirmed by the sweep at all 13 of them (live
// report 2026-07-28, "rank is missing from the more menu at the bottom!!!!").
//
// Nothing enumerates destinations twice now: the bar declares the few that
// earn a permanent slot, and More is the COMPLEMENT — computed, so a new entry
// in NAV_GROUPS cannot fail to appear somewhere.
const NAV_ITEMS = NAV_GROUPS.flatMap(([, items]) => items);

// The bottom bar has room for three plus "More". This is a ranking of what
// gets a permanent slot, NOT a second list of what exists: anything named here
// that is not a real destination is a bug, and anything omitted still shows up
// in More automatically.
const MOBILE_BAR = ["Practice", "Run", "Compare"];

const inBar = ([name]) => MOBILE_BAR.includes(name);

function NavItem({ name, icon, tab, setTab, onSessions, compact = false }) {
  const active = tab === name;
  const choose = () => name === "Sessions" ? onSessions() : setTab(name);
  return html`<button type="button" class="nav-item ${active ? "on" : ""}"
      aria-current=${active ? "page" : null} title=${name}
      onclick=${choose}>
    <${Icon} name=${icon} size=${20} />
    <span>${name}</span>
  </button>`;
}

function ConnectionStatus({ t, mobile = false }) {
  const label = !t.connected ? "Offline"
    : t.paused ? (t.pauseReason === "afk" ? "Paused · AFK" : "Paused")
    : "Live";
  return html`<div class=${mobile ? "mobile-status" : "sidebar-status"}>
    <div class="connection-line ${t.connected && !t.paused ? "is-live" : "is-warn"}">
      <span class="status-light" aria-hidden="true"></span>
      <span class="status-main">${label}</span>
      ${!mobile && html`<span class="status-sub">${t.connected ? "Server connected" : "Reconnecting…"}</span>`}
    </div>
    <div class="recording-status"><${RecordingDot} /></div>
  </div>`;
}

function Sidebar({ t, tab, setTab, openSettings }) {
  return html`<aside class="app-sidebar" aria-label="Primary navigation">
    <div class="app-brand">
      <img src="/ui/assets/sm64_tracker.png" alt="" />
      <span>SM64 Trainer</span>
    </div>
    <div class="sidebar-nav">
      ${NAV_GROUPS.map(([label, items]) => html`<div class="nav-group">
        <div class="nav-group-label">${label}</div>
        ${items.map(([name, icon]) => html`<${NavItem}
          name=${name} icon=${icon} tab=${tab} setTab=${setTab}
          onSessions=${openSettings} />`)}
      </div>`)}
    </div>
    <div class="sidebar-footer">
      <button type="button" class="nav-item settings-link" onclick=${openSettings}>
        <${Icon} name="settings" size=${21} /><span>Settings</span>
      </button>
      <${ConnectionStatus} t=${t} />
    </div>
  </aside>`;
}

function MobileTop({ t, openSettings }) {
  return html`<header class="mobile-appbar">
    <div class="mobile-brand">
      <img src="/ui/assets/sm64_tracker.png" alt="" />
      <span>SM64 Trainer</span>
    </div>
    <${ConnectionStatus} t=${t} mobile=${true} />
    <button type="button" class="icon-button" aria-label="Open settings"
        onclick=${openSettings}><${Icon} name="settings" size=${20} /></button>
  </header>`;
}

function MobileNav({ tab, setTab, openMore }) {
  const items = NAV_ITEMS.filter(inBar);
  return html`<nav class="mobile-nav" aria-label="Primary navigation">
    ${items.map(([name, icon]) => html`<${NavItem}
      name=${name} icon=${icon} tab=${tab} setTab=${setTab}
      onSessions=${() => {}} compact=${true} />`)}
    <button type="button" class="nav-item" onclick=${openMore}>
      <${Icon} name="more" size=${20} /><span>More</span>
    </button>
  </nav>`;
}

function MobileMore({ open, close, tab, setTab, openSettings }) {
  if (!open) return null;
  const pick = (name) => { setTab(name); close(); };
  return html`<div class="mobile-more-backdrop" onclick=${close}>
    <section class="mobile-more-sheet" role="dialog" aria-modal="true"
        aria-label="More navigation"
        onclick=${(e) => e.stopPropagation()}>
      <div class="sheet-head">
        <div><b>More</b><span>Build, review, and configure</span></div>
        <button type="button" class="icon-button" aria-label="Close menu" onclick=${close}>
          <${Icon} name="close" /></button>
      </div>
      <div class="mobile-more-grid">
        ${/* The complement of the bottom bar, computed — never a second list.
             Sessions opens the settings drawer rather than a tab, the same
             special case the sidebar makes. */""}
        ${NAV_ITEMS.filter((item) => !inBar(item)).map(([name, icon]) => html`
          <button type="button" class=${tab === name ? "on" : ""}
              onclick=${() => (name === "Sessions"
                ? (close(), openSettings()) : pick(name))}>
            <${Icon} name=${icon} size=${21} /><span>${name}</span>
          </button>`)}
        <button type="button" onclick=${() => { close(); openSettings(); }}>
          <${Icon} name="settings" size=${21} /><span>Settings</span>
        </button>
      </div>
    </section>
  </div>`;
}

function App() {
  const t = useTracker();
  // There is deliberately NO app-wide background tint here. One existed for
  // part of 2026-07-28 (ui/ranktint.js, worn at rest off the store's tier)
  // and was deleted the same day: "It should NOT be tinted by default. It
  // should only tint during the animation" (user). The celebration's own
  // backdrop is the only thing that tints the page, and it already runs the
  // whole sequence -- default -> the rank you're on -> each tier you climb
  // through -> back to default.
  const [tab, setTabState] = useState("Practice");
  const [compareIntent, setCompareIntent] = useState(null);
  // Same intent-plus-tab shape as openCompare below, deliberately: a
  // second mechanism for "go to that tab and open that thing" is how the
  // two drift. Noticing a wrong STEP happens while playing, and until
  // this existed the only way into the definition was the Segments tab
  // plus a hunt through the library.
  const [segmentIntent, setSegmentIntent] = useState(null);
  // Same shape again, for the Library tab (library.js consumes it). Task 6
  // finishes the fold-in that points existing openCompare call sites here
  // instead -- this task only adds the mechanism, byte-for-byte how the two
  // above already work.
  const [libraryIntent, setLibraryIntent] = useState(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (event) => event.key === "Escape" && setMoreOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);
  const setTab = (name) => { setTabState(name); setMoreOpen(false); };
  const openCompare = (intent) => { setCompareIntent(intent); setTab("Compare"); };
  const openSegment = (id) => { setSegmentIntent(id); setTab("Segments"); };
  const openLibrary = (intent) => { setLibraryIntent(intent); setTab("Library"); };

  return html`<div class="app-shell">
    <${Sidebar} t=${t} tab=${tab} setTab=${setTab}
      openSettings=${() => setSettingsOpen(true)} />
    <${MobileTop} t=${t} openSettings=${() => setSettingsOpen(true)} />
    <main class="app-main">
      <${Header} t=${t} settingsOpen=${settingsOpen}
        closeSettings=${() => setSettingsOpen(false)} setTab=${setTab} />
      <div class="workspace ${tab === "Practice" ? "practice-workspace" : ""}">
        ${/* Compare stays mounted across tab switches so loaded media and sync
             survive leaving and returning. */""}
        <div class="view-pane" style=${tab === "Compare" ? "" : "display:none"}>
          <${Compare} t=${t} intent=${compareIntent}
            clearIntent=${() => setCompareIntent(null)} active=${tab === "Compare"} />
        </div>
        ${/* Library stays mounted for the same reason Compare does: its own
             auto-open-once (library.js) needs to remember it already ran, and
             an intent arriving from elsewhere in the app must be able to
             reach an ALREADY-open tab, not just a freshly mounted one. */""}
        <div class="view-pane" style=${tab === "Library" ? "" : "display:none"}>
          <${Library} t=${t} intent=${libraryIntent}
            clearIntent=${() => setLibraryIntent(null)} active=${tab === "Library"} />
        </div>
        ${tab === "Practice" ? html`<div class="view-pane"><${Practice} t=${t}
            openCompare=${openCompare} openSegment=${openSegment} /></div>`
          : tab === "Segments" ? html`<div class="view-pane"><${Segments} t=${t}
              intent=${segmentIntent}
              clearIntent=${() => setSegmentIntent(null)} /></div>`
          : tab === "Routes" ? html`<div class="view-pane"><${Routes} t=${t} /></div>`
          : tab === "Run" ? html`<div class="view-pane"><${Run} t=${t} /></div>`
          : tab === "Rank" ? html`<div class="view-pane"><${RankPage} t=${t} /></div>`
          : tab === "Live feed" ? html`<div class="view-pane"><${Feed} t=${t} /></div>`
          : null}
      </div>
    </main>
    ${t.notice && html`<div class="app-notice" role="status">${t.notice}</div>`}
    <${MobileNav} tab=${tab} setTab=${setTab}
      openMore=${() => setMoreOpen(true)} />
    <${MobileMore} open=${moreOpen} close=${() => setMoreOpen(false)}
      tab=${tab} setTab=${setTab} openSettings=${() => setSettingsOpen(true)} />
    <${UpdatePopup} t=${t} />
    ${/* Mounted at root, not inside the Rank tab: a rank-up earned while on
         the Practice page must still celebrate, and rule 10 (browser<->GUI
         parity) means the desktop window and the browser tab agree on this
         without desktop/ adding a second copy. */""}
    <${RankUpCelebration} celebration=${t.marelo && t.marelo.celebration}
      scopeId=${t.marelo && t.marelo.scope_id} marelo=${t.marelo}
      routes=${t.routes} activeRouteId=${t.activeRouteId}
      onDone=${t.clearMareloCelebration} />
  </div>`;
}

render(html`<${App} />`, document.getElementById("app"));
