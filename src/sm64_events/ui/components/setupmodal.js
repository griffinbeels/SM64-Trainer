// The installation wizard: local navigation, observed prerequisites, explicit writes.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { Modal } from "./modal.js";
import { Icon } from "./icons.js";
import { EMU, N64 } from "../platform.js";
import { SetupSwap, useSetupAttention } from "./setupmotion.js";
import { ConnectPage, EmuSetupPane, N64SetupPane, CompletionPage } from "./setuppages.js";
import { SETUP_TITLES, ACKNOWLEDGE_MS, canAdvance, canReviewForward,
         pagesFor, readResume, saveResume } from "../setupflow.js";

const html = htm.bind(h);
export { EmuSetupPane, N64SetupPane };
export const SETUP_PANES = { [EMU]: EmuSetupPane, [N64]: N64SetupPane };

function initialState(initialPlatform, manual) {
  const resume = readResume();
  const platform = initialPlatform || resume?.platform || null;
  return {platform, page: initialPlatform ? (platform === N64 ? "console" : "connect") : resume?.page || "platform"};
}

function useSetupConnection(onFailure) {
  const [setup, setSetup] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [offline, setOffline] = useState(false);
  const request = useRef(0);
  const alive = useRef(true);
  const mutating = useRef(false);
  const release = () => { mutating.current = false; if (alive.current) setBusy(false); };
  const accept = (value) => { setSetup(value); setOffline(false); };
  async function refresh() {
    if (mutating.current) return;
    const number = ++request.current;
    try {
      const value = await getJSON("/api/setup");
      if (alive.current && number === request.current) accept(value);
    } catch {
      if (alive.current && number === request.current) setOffline(true);
    }
  }
  useEffect(() => {
    alive.current = true;
    let timer;
    const poll = async () => {
      await refresh();
      if (alive.current) timer = setTimeout(poll, 1000);
    };
    poll();
    return () => { alive.current = false; ++request.current; clearTimeout(timer); };
  }, []);

  async function mutate(method, url, body, reload = false) {
    if (mutating.current) return null;
    mutating.current = true; ++request.current; setBusy(true); setError(null);
    try {
      let value = await send(method, url, body);
      if (reload) value = await getJSON("/api/setup");
      if (alive.current) accept(value);
      return value;
    } catch (failure) {
      if (alive.current) {
        onFailure();
        setError(failure.message || "Couldn't complete that action. Try again.");
      }
      return null;
    } finally {
      release();
    }
  }
  return {setup, busy, error, offline, alive, refresh, mutate, setError};
}

function SetupContent({page, platform, setup, choose, finish, busy, refresh, install, remove, resume, manual}) {
  const Pane = SETUP_PANES[platform];
  return page === "platform" ? html`<div class="setup-platform-picks">
      ${[[EMU, "Emulator", "Only Project64 v1.6 supported", "compare"],
        [N64, "N64", "Requires capture card.", "more"]].map(([key, name, detail, icon]) => html`<button
          type="button" key=${key} class=${`setup-platform-choice ${platform === key ? "is-selected" : ""}`}
          onclick=${() => choose(key)}>
        <${Icon} name=${icon} size=${34}/><span class="setup-platform-name">${name}</span>
        <span class="setup-platform-detail">${detail}</span><span class="setup-choice-arrow" aria-hidden="true">
          <${Icon} name="arrowRight" size=${22}/></span>
      </button>`)}
    </div>`
    : page === "connect" ? html`<${ConnectPage} setup=${setup}/>`
    : !setup ? html`<p role="status">Connecting to the trainer…</p>`
    : page === "complete" ? html`<${CompletionPage} setup=${setup} onFinish=${finish} busy=${busy}/>`
    : Pane ? html`<${Pane} setup=${setup} refresh=${refresh} installing=${busy}
        onInstall=${install} onRemove=${remove} onResume=${resume} manual=${manual}
        onEmulator=${() => choose(EMU)} onFinish=${finish} busy=${busy}/>` : null;
}

export function SetupModal({ onClose, onComplete, initialPlatform, initialPane, manual = false }) {
  const [initial] = useState(() => initialState(initialPlatform || initialPane, manual));
  const [platform, setPlatform] = useState(initial.platform);
  const [page, setPage] = useState(initial.page);
  const [reached, setReached] = useState(() => pagesFor(initial.platform).slice(0,
    Math.max(1, pagesFor(initial.platform).indexOf(initial.page) + 1)));
  const [review, setReview] = useState(false);
  const [direction, setDirection] = useState(1);
  const attentive = useSetupAttention();
  const root = useRef(null);
  const initialized = useRef(false);
  const {setup, busy, error, offline, alive, refresh, mutate, setError} = useSetupConnection(() => {
    if (page === "complete") go("install", true, true);
  });


  useEffect(() => {
    if (!setup || initialized.current) return;
    initialized.current = true;
    if (setup.emu.consented_at || setup.emu.wrapper_present) {
      setPlatform(setup.platform);
      setPage(setup.platform === N64 ? "console" : "install");
      setReached(pagesFor(setup.platform));
      setReview(manual);
    } else if (!initial.platform && setup.onboarding?.started) {
      setPlatform(EMU); setPage("install"); setReached(["platform", "connect", "install"]);
    }
  }, [setup]);

  function go(next, backwards = false, reviewing = false) {
    setDirection(backwards ? -1 : 1);
    setReview(reviewing);
    setPage(next);
    setReached(current => current.includes(next) ? current : [...current, next]);
    setError(null);
  }
  function choose(next) {
    setPlatform(next);
    setReached(["platform", next === EMU ? "connect" : "console"]);
    go(next === EMU ? "connect" : "console");
  }

  const advance = !offline && !busy && canAdvance(page, setup);
  useEffect(() => {
    if (!advance || review || !attentive) return;
    const timer = setTimeout(() => go(page === "connect" ? "install" : "complete"), ACKNOWLEDGE_MS);
    return () => clearTimeout(timer);
  }, [page, advance, review, attentive]);

  useEffect(() => {
    if (platform) saveResume({platform, page});
    root.current?.closest(".modal-body")?.scrollTo({top: 0, behavior: "instant"});
    if (document.hasFocus()) {
      const heading = root.current?.closest(".modal")?.querySelector(".modal-heading h2");
      if (heading) { heading.tabIndex = -1; heading.focus({preventScroll: true}); }
    }
  }, [page, platform]);


  const install = () => mutate("POST", "/api/setup/capture-layer", {consent: true});
  const remove = () => mutate("DELETE", "/api/setup/capture-layer");
  const resume = () => mutate("POST", "/api/pause", {paused: false}, true);
  async function finish() {
    const value = await mutate("POST", "/api/setup/complete", {platform});
    if (!value || !alive.current) return;
    saveResume(null);
    if (onComplete) onComplete(value);
    else onClose();
  }

  const pages = pagesFor(platform), index = pages.indexOf(page);
  const back = index > 0;
  const forward = review && canReviewForward(page, {platform, pages: reached}, setup) && !offline;
  const title = setupTitle(page, setup);


  return html`<${Modal} title=${title} icon=${page === "complete" ? "check" : "practice"} size="large"
      footer=${html`<div class="setup-footer">
        <div class="setup-navigation">
          <span>${back && html`<button type="button" class="setup-arrow" aria-label="Back" disabled=${busy}
            onclick=${() => go(pages[index - 1], true, true)}><${Icon} name="arrowLeft" size=${22}/></button>`}</span>
          <div class="setup-progress" aria-label=${`Step ${index + 1} of ${pages.length}`}>
            <div class="setup-dots">${pages.map((key, at) => html`<span key=${key}
              class=${`setup-dot ${at === index ? "is-current" : at < index ? "is-done" : ""}`}
              aria-current=${at === index ? "step" : undefined}></span>`)}</div>
            <span>Step ${index + 1} of ${pages.length}</span>
          </div>
          <span>${forward && html`<button type="button" class="setup-arrow" aria-label="Forward" disabled=${busy}
            onclick=${() => go(pages[index + 1], false, true)}><${Icon} name="arrowRight" size=${22}/></button>`}
            ${!["complete", "console"].includes(page) && html`<button type="button" class="setup-not-now"
              disabled=${busy} onclick=${onClose}>Not now</button>`}</span>
        </div>
      </div>`}>
    <div class="setup-modal" ref=${root} data-page=${page} aria-busy=${busy}>
      <${SetupSwap} identity=${page} direction=${direction}><${SetupContent} page=${page} platform=${platform} setup=${setup} choose=${choose}
        finish=${finish} busy=${busy} refresh=${refresh} install=${install} remove=${remove} resume=${resume} manual=${manual}/></${SetupSwap}>
      ${offline && html`<p class="setup-error" role="alert">Connection interrupted. Retrying automatically…</p>`}
      ${error && html`<p class="setup-error" role="alert">${error}</p>`}
    </div>
  </${Modal}>`;
}

function setupTitle(page, setup) {
  return page === "complete" && setup?.emu?.verification?.limited
    ? "Setup complete" : SETUP_TITLES[page];
}
