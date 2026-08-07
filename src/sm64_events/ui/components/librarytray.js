// src/sm64_events/ui/components/librarytray.js — the Library's comparison
// tray (Task 5, spec 2026-08-07-library-page): chips docked under the
// header, and the "vibes grid" overlay that plays every chip at once.
//
// TWO TIERS, on purpose, and this file only ever builds the FIRST: this grid
// answers "which of these do I even like?" with several YouTube embeds
// playing roughly together -- it cannot frame-sync (a `start=` query param on
// an iframe is not a shared clock), so the overlay says that in plain words
// rather than pretending otherwise. "Study in Compare" is the second tier,
// frame-accurate, and belongs to Task 6 (task-5-caveats.md point 3 and the
// plan's own Task 6 section) -- `onStudy` is a prop here because the BUTTON
// is this task's to place, not because the wiring behind it is.
//
// Tray item shape (library.js's own state, grown here, not reinvented --
// task-5-caveats.md point 1): {key, runner, time_cs, video, strat, trim}.
// `key` stays `entry.video`, unchanged from Task 4: a video URL is the
// footage's own identity, so the collision the caveat warns about (two
// sibling-target examples sharing a runner+time) cannot reach it -- two
// independent recordings have two distinct URLs, and the one case that WOULD
// collide (the identical clip cited from two sibling rows) is not a
// collision to fix, it is the same clip correctly deduplicated.
import { h } from "preact";
import { useLayoutEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { fmtSeconds } from "../format.js";
import { curve } from "../disclosure.js";
import { feedTuning } from "../feedtuning.js";
import { Icon } from "./icons.js";
import { Disclose } from "./collapsible.js";
import { gridShape, youtubeEmbed, youtubeThumb } from "./librarymodel.js";

const html = htm.bind(h);

export const HONESTY_LINE =
  "Embeds play roughly together — for frame-accurate sync, use Study in Compare.";

const reducedMotion = () => typeof matchMedia === "function"
  && matchMedia("(prefers-reduced-motion: reduce)").matches;

// "" -> null (unset), else a finite number. A blank field must clear a bound,
// not silently keep whatever was there -- trayToImport reads `!= null` to
// decide whether a bound is set at all (librarymodel.js).
function parseSeconds(text) {
  if (text === "" || text == null) return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

function TrayChip({ item, editing, onToggleEdit, onTrim, onRemove }) {
  const thumb = item.video ? youtubeThumb(item.video) : null;
  const trim = item.trim || {};
  return html`<div class="library-tray-chip">
    <div class="library-tray-chip-row">
      ${thumb
        ? html`<img class="library-tray-chip-thumb" src=${thumb} alt="" loading="lazy" />`
        : html`<div class="library-tray-chip-thumb library-tray-chip-nothumb"></div>`}
      <span class="library-tray-chip-runner" title=${item.runner}>${item.runner}</span>
      <span class="library-tray-chip-time">${fmtSeconds(item.time_cs / 100)}</span>
      <button type="button" class="icon-button library-tray-chip-trim"
          aria-pressed=${editing} aria-label=${`Trim ${item.runner}'s clip`}
          title="Trim this clip" onclick=${onToggleEdit}>
        <${Icon} name="clock" size=${13} />
      </button>
      <button type="button" class="icon-button library-tray-chip-remove"
          aria-label=${`Remove ${item.runner} from the tray`}
          title="Remove from the tray" onclick=${onRemove}>
        <${Icon} name="close" size=${13} />
      </button>
    </div>
    <${Disclose} open=${editing} className="library-tray-trim-disclose">
      <div class="library-tray-trim">
        <label>Start (s)<input type="number" min="0" step="0.1"
            class="library-tray-trim-start"
            value=${trim.start_s ?? ""}
            oninput=${(inputEvent) => onTrim(item.key,
              { start_s: parseSeconds(inputEvent.target.value), end_s: trim.end_s ?? null })} /></label>
        <label>End (s)<input type="number" min="0" step="0.1"
            class="library-tray-trim-end"
            value=${trim.end_s ?? ""}
            oninput=${(inputEvent) => onTrim(item.key,
              { start_s: trim.start_s ?? null, end_s: parseSeconds(inputEvent.target.value) })} /></label>
      </div>
    <//>
  </div>`;
}

/**
 * The dock. Wrapped in its OWN `Disclose` so arriving/leaving the tray is a
 * measured-height animation like every other drop-down in this app
 * (`.claude/rules/ui-core.md`'s "a state change animates" rule, and
 * task-5-caveats.md point 5 naming this surface specifically) rather than the
 * bar popping in and shoving the page down. `items.length > 0` IS `open` --
 * library.js never mounts/unmounts this component itself, so the close
 * direction gets to play too, not just the open one.
 */
export function LibraryTray({ items, onTrim, onRemove, onPlayAll, onStudy }) {
  const [editingKey, setEditingKey] = useState(null);
  return html`<${Disclose} open=${items.length > 0} className="library-tray-disclose">
    <div class="library-tray">
      <div class="library-tray-chips">
        ${items.map((item) => html`<${TrayChip} key=${item.key} item=${item}
            editing=${editingKey === item.key}
            onToggleEdit=${() => setEditingKey((prev) => (prev === item.key ? null : item.key))}
            onTrim=${onTrim} onRemove=${() => onRemove(item.key)} />`)}
      </div>
      <div class="library-tray-actions">
        <span class="meta library-tray-count">${items.length} in the tray</span>
        <button type="button" class="primary-button library-tray-playall"
            disabled=${items.length === 0} onclick=${onPlayAll}>
          <${Icon} name="play" size=${15} /> Play all
        </button>
        <button type="button" class="library-tray-study"
            disabled=${!onStudy || items.length === 0}
            title=${onStudy ? "Frame-accurate side-by-side study" : "Lands with the Compare fold-in (next task)"}
            onclick=${() => onStudy && onStudy(items)}>
          <${Icon} name="compare" size=${15} /> Study in Compare
        </button>
      </div>
    </div>
  <//>`;
}

function GridTile({ item, restartNonce }) {
  const embed = item.video
    ? youtubeEmbed(item.video, item.trim ? item.trim.start_s : null) : null;
  const thumb = item.video ? youtubeThumb(item.video) : null;
  return html`<div class="library-grid-tile">
    <div class="library-grid-tile-media">
      ${embed
        ? html`<iframe key=${`${item.key}::${restartNonce}`} class="library-embed" src=${embed}
            title=${`${item.runner} — ${fmtSeconds(item.time_cs / 100)}`}
            allow="autoplay; encrypted-media" allowfullscreen></iframe>`
        : thumb
          ? html`<img class="library-example-thumb" src=${thumb} alt="" loading="lazy" />`
          : html`<div class="library-example-thumb library-example-placeholder">
              <${Icon} name="play" size=${20} />
            </div>`}
      ${item.video && !embed
        ? html`<a class="library-example-external" href=${item.video} target="_blank"
            rel="noopener" title="open on the runner's site">
            <${Icon} name="upload" size=${13} /></a>` : null}
    </div>
    <div class="library-grid-tile-footer">
      <span class="library-grid-tile-runner">${item.runner}</span>
      <span class="library-grid-tile-time">${fmtSeconds(item.time_cs / 100)}</span>
    </div>
  </div>`;
}

/**
 * The overlay. A plain fixed backdrop, not the shared `Modal` -- every
 * existing `Modal` call site snaps open/closed with no transition, and this
 * surface was called out on its own (task-5-caveats.md point 5) as one that
 * must not. Reuses the practice log's own tuned numbers (`feedTuning()` /
 * `curve()`) rather than inventing a duration -- "match a neighbouring
 * surface" -- so no new tunable and no inspector is owed here.
 *
 * `restartNonce` is what makes "restart all" real: an iframe's `src`
 * assigned the SAME string again is a browser no-op, so the only way to
 * force every embed back to its `start=` point is to give each `<iframe>` a
 * fresh identity, which Preact reads off `key`. Changing `trim.start_s` and
 * restarting therefore also picks up the new value -- it is not a cache of
 * whatever played at open time.
 */
export function LibraryGrid({ items, onClose }) {
  const backdropRef = useRef(null);
  const panelRef = useRef(null);
  const closingRef = useRef(false);
  const [restartNonce, setRestartNonce] = useState(0);
  const { cols } = gridShape(items.length);

  useLayoutEffect(() => {
    if (reducedMotion() || !backdropRef.current || !panelRef.current) return;
    const tuning = feedTuning();
    const easing = curve(tuning);
    backdropRef.current.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: tuning.openMs, easing, fill: "backwards" });
    panelRef.current.animate(
      [{ opacity: 0, transform: "translateY(14px) scale(.97)" },
       { opacity: 1, transform: "translateY(0) scale(1)" }],
      { duration: tuning.openMs, easing, fill: "backwards" });
  }, []);

  function requestClose() {
    if (closingRef.current) return;
    closingRef.current = true;
    if (reducedMotion() || !backdropRef.current || !panelRef.current) { onClose(); return; }
    const tuning = feedTuning();
    const easing = curve(tuning);
    const backdropRun = backdropRef.current.animate([{ opacity: 1 }, { opacity: 0 }],
      { duration: tuning.closeMs, easing, fill: "forwards" });
    panelRef.current.animate(
      [{ opacity: 1, transform: "translateY(0) scale(1)" },
       { opacity: 0, transform: "translateY(14px) scale(.97)" }],
      { duration: tuning.closeMs, easing, fill: "forwards" });
    backdropRun.onfinish = onClose;
  }

  return html`<div ref=${backdropRef} class="library-grid-backdrop" onclick=${requestClose}>
    <section ref=${panelRef} class="library-grid-panel" role="dialog" aria-modal="true"
        aria-label="Play all" onclick=${(clickEvent) => clickEvent.stopPropagation()}>
      <header class="library-grid-head">
        <h3>Play all <span class="meta">(${items.length})</span></h3>
        <div class="library-grid-head-actions">
          <button type="button" class="library-grid-restart"
              onclick=${() => setRestartNonce((prev) => prev + 1)}>
            <${Icon} name="restart" size=${15} /> Restart all
          </button>
          <button type="button" class="icon-button library-grid-close"
              aria-label="Close" title="Close" onclick=${requestClose}>
            <${Icon} name="close" size=${16} />
          </button>
        </div>
      </header>
      <p class="library-grid-honesty">${HONESTY_LINE}</p>
      <div class="library-grid ${`cols-${cols}`}">
        ${items.map((item) => html`<${GridTile} key=${item.key} item=${item}
            restartNonce=${restartNonce} />`)}
      </div>
    </section>
  </div>`;
}
