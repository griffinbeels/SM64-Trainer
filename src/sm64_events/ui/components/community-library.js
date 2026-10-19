import { h } from "preact";
import { useEffect, useLayoutEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { fmtIgtShort } from "../format.js";
import { readCommunityLibrary, useUiLog } from "../uilog.js";
import { Icon } from "./icons.js";
import { Modal } from "./modal.js";
import { RegionFlag } from "./regionflag.js";

const html = htm.bind(h);
const emptyPage = () => ({ rows: [], cursor: null, loading: false, error: null,
  available: null, reason: null, restart: false });
const count = (value, noun) => `${value} ${noun}${value === 1 ? "" : "s"}`;
const targetName = row => row.target?.name || "Shared practice target";
const methodLabel = row => `${row.strategy} · ${row.game_version.toUpperCase()} · ${row.clock.toUpperCase()}`;
const groupKey = row => JSON.stringify([row.target, row.clock, row.game_version, row.strategy]);
// Saved seats belong to the target/scope, across ROM browsing filters.
const pinKey = row => JSON.stringify([row.local_target, "strategy"]);

function pageURL(kind, row, cursor, entity, rom) {
  const params = new URLSearchParams();
  if (kind === "summary") {
    if (entity) params.set("entity", entity);
    if (rom) params.set("rom", rom);
  } else {
    params.set("target", JSON.stringify(row.target));
    params.set("clock", row.clock);
    params.set("rom", row.game_version);
    params.set("strategy", row.strategy);
  }
  if (cursor) params.set("cursor", cursor);
  const base = kind === "summary" ? "/api/library/community" : "/api/library/community/recordings";
  return `${base}?${params}`;
}

function Method({ row }) {
  return html`<span class="community-library-method">${row.strategy}${" · "}<${RegionFlag} version=${row.game_version} size=${18} />${" · "}${row.clock.toUpperCase()}</span>`;
}

function CommunityHeader({ selected, entity, onBrowseAll, busy, back }) {
  return html`<header class="community-library-header">
    <div><h3>${selected ? targetName(selected) : "Community ghosts"}</h3>
      <p class="meta">${selected ? html`<${Method} row=${selected} />` : "Find a recording to race and save it to that strategy’s Lineup."}</p></div>
    ${selected ? html`<button type="button" class="quiet-button" disabled=${busy != null} onclick=${back}><${Icon} name="arrowLeft" /> Back to ghost targets</button>`
      : entity && onBrowseAll ? html`<button type="button" class="quiet-button" onclick=${onBrowseAll}>Browse all targets</button>` : null}
  </header>`;
}

function PageFeedback({ page, noun, retry }) {
  return html`${page.loading ? html`<p class="community-library-status meta" role="status">Loading ${noun}…</p>` : null}
    ${page.error ? html`<div class="community-library-feedback">
      <p class="error" role="alert">${page.error}</p>
      <button type="button" disabled=${page.loading} onclick=${retry}>${page.restart ? "Reload results" : "Retry"}</button>
    </div>` : null}
    ${page.available === false && !page.error ? html`<div class="community-library-feedback">
      <p class="meta">${page.reason || "Community ghosts are unavailable right now."}</p>
      <button type="button" disabled=${page.loading} onclick=${retry}>Retry</button>
    </div>` : null}
    ${page.available && !page.rows.length && !page.loading && !page.error
      ? html`<p class="community-library-empty meta">No shared ${noun} match these filters yet.</p>` : null}`;
}

export function SeatPicker({ choice, busy, error, onChoose, onClose }) {
  const row = choice.row;
  return html`<${Modal} title="Choose a rival to replace" icon="ghost"
      description=${`${targetName(row)} · ${methodLabel(row)}`}
      onClose=${busy ? undefined : onClose}
      footer=${html`<button type="button" disabled=${busy} onclick=${onClose}>Cancel</button>`}>
    <div class="community-library-seats">
      <p class="meta">Your visible seats are saved rivals. Choose which one ${row.player_name} replaces.</p>
      ${(choice.seats || []).map(seat => html`<button key=${seat.slot} type="button"
          class="community-library-seat" disabled=${busy || !seat.visible}
          onclick=${() => onChoose(seat.slot)}>
        <span class="community-library-seat-title">Seat ${seat.slot + 1}${" · "}${seat.player_name || (seat.id ? "Saved rival unavailable" : "Empty")}</span>
        <span class="community-library-seat-detail meta">${seat.frames != null ? `${fmtIgtShort(seat.frames)} · ` : ""}${seat.pinned ? "Saved rival" : "Automatic rival"}${!seat.visible ? " · Hidden seat" : ""}</span>
        ${seat.reason_text ? html`<span class="meta">${seat.reason_text}</span>` : null}
        ${!seat.visible ? html`<span class="meta">Increase the rivals shown in Practice to use this seat.</span>` : null}
      </button>`)}
      ${error ? html`<p class="error" role="alert">${error}</p>` : null}
    </div>
  </${Modal}>`;
}

function useSavedRivals(onChanged, alive) {
  const [knownPins, setKnownPins] = useState({});
  const [busy, setBusy] = useState(null);
  const [choice, setChoice] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [message, setMessage] = useState(null);
  const writing = useRef(false);
  function clear() { setChoice(null); setActionError(null); setMessage(null); }
  function settled() { writing.current = false; if (alive.current) setBusy(null); }
  async function change(row, action, slot = null) {
    if (!alive.current || writing.current) return;
    writing.current = true; setBusy(row.id); setActionError(null); setMessage(null);
    try {
      const data = await send("POST", "/api/ghost/rivals/saved", {
        target: row.local_target, action, id: row.id, version: row.game_version,
        scope: "strategy", ...(slot == null ? {} : { slot }),
      });
      if (!alive.current) return;
      if (data.state === "needs_slot") {
        setChoice({ row, seats: data.seats });
      } else if (data.state === "added" || data.state === "removed") {
        setKnownPins(previous => ({ ...previous, [pinKey(row)]: Object.values(data.pins || {}) }));
        setChoice(null);
        setMessage(`${row.player_name} ${data.state === "added" ? "saved to" : "removed from"} ${targetName(row)} · ${methodLabel(row)} Lineup.`);
        onChanged?.(data);
      } else {
        throw new Error("The saved rival could not be confirmed. Refresh this list before trying again.");
      }
    } catch (error) {
      if (alive.current) setActionError(error.message || "This rival could not be saved. Try again.");
    } finally {
      settled();
    }
  }
  return { knownPins, busy, choice, actionError, message, clear, change };
}

function CommunityLibraryView({ entity, rom, onBrowseAll, onChanged, onOpenPlayer }) {
  const [summary, setSummary] = useState(() => ({ ...emptyPage(), loading: true }));
  const [selected, setSelected] = useState(null);
  const [recordings, setRecordings] = useState(emptyPage);
  const alive = useRef(true);
  const root = useRef(null);
  const requests = useRef({ summary: null, recordings: null });
  const actions = useSavedRivals(onChanged, alive);
  const { knownPins, busy, choice, actionError, message, change } = actions;
  useUiLog(root, [readCommunityLibrary]);

  // The active child is keyed by target and region. Hide/unmount aborts every
  // read before a late result can paint in a different Library context.
  useLayoutEffect(() => () => {
    alive.current = false;
    Object.values(requests.current).forEach(request => request?.abort());
  }, []);

  async function readPage(kind, row = null, cursor = null) {
    if (!alive.current) return;
    const setter = kind === "summary" ? setSummary : setRecordings;
    requests.current[kind]?.abort();
    const request = new AbortController();
    requests.current[kind] = request;
    const current = () => alive.current && requests.current[kind] === request;
    setter(previous => ({ ...(cursor ? previous : emptyPage()), loading: true }));
    try {
      const data = await getJSON(pageURL(kind, row, cursor, entity, rom), { signal: request.signal });
      if (!current()) return;
      const rows = data[kind === "summary" ? "entries" : "recordings"];
      if (!Array.isArray(rows)) throw new Error("These community recordings could not be read. Try again.");
      setter(previous => ({ rows: cursor ? [...previous.rows, ...rows] : rows,
        cursor: data.next_cursor || null, loading: false, error: null,
        available: data.available !== false, reason: data.reason, restart: false }));
    } catch (error) {
      if (!current() || error.name === "AbortError") return;
      setter(previous => ({ ...previous, loading: false,
        error: error.message || "Community ghosts could not be loaded.", restart: error.status === 409 }));
    }
  }

  useEffect(() => { readPage("summary"); }, []);

  function open(row) {
    setSelected(row); actions.clear();
    readPage("recordings", row);
  }
  function back() {
    requests.current.recordings?.abort(); requests.current.recordings = null;
    setSelected(null); actions.clear();
  }

  const page = selected ? recordings : summary;
  const retry = () => readPage(selected ? "recordings" : "summary", selected,
    page.restart ? null : page.cursor);
  return html`<section ref=${root} class="community-library" data-testid="community-library"
      data-view=${selected ? "recordings" : "targets"} data-entity=${entity || ""} data-rom=${rom || "both"} aria-busy=${page.loading}>
    <${CommunityHeader} selected=${selected} entity=${entity} onBrowseAll=${onBrowseAll} busy=${busy} back=${back} />
    <${PageFeedback} page=${page} noun=${selected ? "recordings" : "ghost targets"} retry=${retry} />
    ${selected ? html`<div class="community-library-recordings">
      ${recordings.rows.map(row => {
        const pins = knownPins[pinKey(row)];
        const added = pins ? pins.includes(row.id) : Number.isInteger(row.saved_slot) && row.saved_slot >= 0;
        const disabled = busy != null || (!added && (!row.can_add || !row.local_target));
        return html`<article key=${row.id} class="community-library-recording" data-recording-id=${row.id}>
          <div class="community-library-player">${onOpenPlayer
            ? html`<button type="button" class="textlink" onclick=${() => onOpenPlayer(row.player_id)}><strong>${row.player_name}</strong></button>`
            : html`<strong>${row.player_name}</strong>`}
            <span class="community-library-provenance meta">Community recording</span></div>
          <div class="community-library-performance"><strong class="community-library-time">${fmtIgtShort(row.frames)}</strong>
            <span class="meta"><${Method} row=${row} /></span></div>
          <div class="community-library-actions"><button type="button" class=${added ? "quiet-button" : "primary primary-button"}
              disabled=${disabled} onclick=${() => change(row, added ? "remove" : "add")}>
            <${Icon} name=${added ? "minus" : "ghost"} />${busy === row.id ? "Saving…" : added ? "Remove" : "Add rival"}
          </button>
          ${added ? html`<span class="community-library-saved meta">Saved for this strategy</span>` : null}</div>
          ${!added && !row.can_add ? html`<p class="community-library-unavailable meta">${row.unavailable_reason || "This recording cannot be added to a local Lineup."}</p>` : null}
        </article>`;
      })}
    </div>` : html`<div class="community-library-groups">
      ${summary.rows.map(row => html`<button key=${groupKey(row)} type="button" class="community-library-group" onclick=${() => open(row)}>
        <span class="community-library-target">${targetName(row)}</span>
        <${Method} row=${row} />
        <span class="community-library-count meta">${count(row.recordings, "recording")} · ${count(row.players, "player")}</span>
        <span class="community-library-best">Best ${fmtIgtShort(row.best_frames)}</span>
        ${!row.can_add && row.unavailable_reason ? html`<span class="community-library-unavailable meta">${row.unavailable_reason}</span>` : null}
      </button>`)}
    </div>`}
    ${page.cursor && !page.error ? html`<button type="button" class="community-library-more" disabled=${page.loading || busy != null}
      onclick=${() => readPage(selected ? "recordings" : "summary", selected, page.cursor)}>Load more</button>` : null}
    ${message ? html`<p class="community-library-success" role="status">${message}</p>` : null}
    ${actionError && !choice ? html`<p class="community-library-action-error error" role="alert">${actionError}</p>` : null}
    ${choice ? html`<${SeatPicker} choice=${choice} busy=${busy != null} error=${actionError}
      onClose=${actions.clear} onChoose=${slot => change(choice.row, "add", slot)} />` : null}
  </section>`;
}

export function CommunityLibrary({ active, entity = null, versions, onBrowseAll, onChanged, onOpenPlayer }) {
  if (!active) return null;
  const regions = [...new Set((Array.isArray(versions) ? versions : ["us"]).filter(value => value === "us" || value === "jp"))];
  const rom = regions.length === 1 ? regions[0] : null;
  return html`<${CommunityLibraryView} key=${JSON.stringify([entity, rom])}
    entity=${entity} rom=${rom} onBrowseAll=${onBrowseAll} onChanged=${onChanged} onOpenPlayer=${onOpenPlayer} />`;
}
