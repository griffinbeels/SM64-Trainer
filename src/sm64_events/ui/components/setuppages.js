import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { RegionFlag, REGIONS } from "./regionflag.js";
import { castSrc } from "./emptystate.js";
import { SetupDisclosure, SetupSwap } from "./setupmotion.js";
import { SetupCheckNotes, SetupCheckList } from "./setupcheck.js";
import { STAGE_ORDER, stageOf, gateOpen, BUILD, FIXUPS } from "../setupflow.js";

const html = htm.bind(h);
const GUIDE = "https://wermi.neocities.org/emuguide/";
// Title, detail and acknowledgment of each visible stage (setupflow.js STAGE_OF).
const STAGE_COPY = {
  close: ["Close Project64", "This lets us add Practice Replay to your emulator.", "Project64 closed"],
  install: ["Install Practice Replay", "Record your practice and replay it with your inputs, frame by frame.", "Practice Replay installed"],
  game: ["Open your game", "", "Usamune found"],
  check: ["Checking your setup", "Keep the game running while we check your replay pictures and inputs.", ""],
  unavailable: ["Practice Replay unavailable", "", ""],
  checking: ["Checking your setup", "Connecting to the trainer…", ""],
};

// The line under a stage's title follows the server's step in place.
function stageDetail(step, emu) {
  if (step === "reopen") return "Start Project64 v1.6 to load Practice Replay.";
  if (step === "fixups") return "The standard speedrun fix for Project64 1.6. We download it and put it in your Plugin folder.";
  if (step === "rom" && emu.rom?.state !== "unsupported") {
    return "In Project64, choose File → Open ROM and open your Usamune practice ROM.";
  }
  if (["emulator", "rom", "unavailable"].includes(step)) return emu.verification?.message;
  if (step === "ready") return "Everything available for your game is set up.";
  return STAGE_COPY[stageOf(step)][1];
}

function GuideLink({ path, children }) {
  return html`<a href=${GUIDE + path} target="_blank" rel="noopener noreferrer">${children} ↗</a>`;
}

export function SetupStatus({ done, title, detail, children }) {
  return html`<div class=${`setup-current ${done ? "is-done" : "is-next"}`}>
    <span class=${`setup-marker ${done ? "is-checked" : ""}`} aria-hidden="true">
      ${done ? html`<${Icon} name="check" size=${25}/>` : html`<span class="setup-wait-dot"></span>`}
    </span>
    <div class="setup-current-content"><h3 aria-live="polite">${title}</h3>${detail && html`<p>${detail}</p>`}${children}</div>
  </div>`;
}

export function RomResult({ rom }) {
  if (!rom?.region) return null;
  return html`<div class="setup-rom-result">
    <div class="setup-regions" aria-label="Detected game region">
      ${REGIONS.map(region => html`<div class=${`setup-region ${rom.region === region ? "is-detected" : ""}`} key=${region}>
        <${RegionFlag} version=${region} size=${48}/><span>${region.toUpperCase()}</span>
        ${rom.region === region && html`<span class="setup-detected-label">Detected</span>`}
      </div>`)}
    </div>
    <p class="setup-rom-name">${rom.name}</p>
    ${rom.warning && html`<p class="setup-warning" role="status">${rom.warning}</p>`}
  </div>`;
}

function checkItem(setup, id) {
  return (setup?.emu?.setup_check?.items || []).find(item => item.id === id);
}

// The download of the one build the trainer is tested with.
function DownloadBuild({ setup, onClick }) {
  const build = checkItem(setup, BUILD);
  return build?.download && html`<a class="primary-button setup-download" href=${build.download}
    target="_blank" rel="noopener noreferrer" onclick=${onClick}>Download Wermi's Build v7</a>`;
}

// The sub-steps already finished, as one-line checks. The one to do now is
// the card under them; later ones stay hidden until their turn (his rule,
// 2026-11-09: "a CHECKLIST leveraging progressive disclosure ... checkpoints
// for each sub-step", so nobody skims a page of steps and skips ahead).
export function DoneSteps({ labels }) {
  if (!labels.length) return null;
  return html`<ul class="setup-done-steps" aria-label="Finished steps" data-testid="setup-done-steps">
    ${labels.map(label => html`<li key=${label}><${Icon} name="check" size=${16}/><span>${label}</span></li>`)}
  </ul>`;
}

// What still differs from the tested setup on this page, how to fix each
// item, and the fix the trainer can do by itself (the PJ64 Fixups).
export function GateWarning({ page, setup, accepted, busy, onFixups, children }) {
  const open = gateOpen(page, setup, accepted);
  if (!open.length) return null;
  const emu = setup.emu;
  return html`<div class="setup-gate" role="status" data-testid="setup-gate">
    <p class="setup-warning"><${Icon} name="warning" size=${18}/>${" "}
      The trainer is only tested with Wermi's Build v7 and the PJ64 Fixups. Your Project64 is different.</p>
    ${children}
    ${page === "connect" ? html`<${SetupDisclosure} label="What we found">
        <${SetupCheckList} check=${emu.setup_check} only=${open}/></${SetupDisclosure}>`
      : html`<${SetupCheckList} check=${emu.setup_check} only=${open}/>`}
    ${open.includes(BUILD) && page !== "connect" && html`<div class="setup-install-action">
      <${DownloadBuild} setup=${setup}/></div>`}
    ${open.includes(FIXUPS) && html`<div class="setup-install-action">
      ${emu.fixups?.error && html`<p class="setup-error" role="alert">${emu.fixups.error}</p>`}
      ${emu.pj64_running && html`<p>Close Project64 so we can add the PJ64 Fixups.</p>`}
      <button class="primary-button" type="button" disabled=${busy || emu.pj64_running}
        onclick=${onFixups}>${busy ? "Installing…" : "Install the PJ64 Fixups"}</button>
    </div>`}
  </div>`;
}

export function Project64Help() {
  return html`<div class="setup-help-list">
    <${SetupDisclosure} label="I already have another Project64">
      <p>Use Wermi's Build v7 anyway. It is the standard build for SM64 speedrunning, and it is the
        only one the trainer is tested with. You can keep your old Project64 in its own folder.</p>
      <${GuideLink} path="getting_emu/">Read the emulator guide</${GuideLink}>
    </${SetupDisclosure}>
    <${SetupDisclosure} label="It's open, but isn't detected">
      <p>Check that you're using version 1.6. If you have several copies open, close the extras.
        Run Project64 and the trainer under the same Windows account.</p>
    </${SetupDisclosure}>
  </div>`;
}

// The Connect page's sub-steps: [check shown once done, title, detail].
// "open" and "swap" finish by evidence (the trainer finds the right
// Project64). Nothing can read a download or an unpack, so the user's own
// click is their checkpoint; finding the right Project64 finishes them too.
const CONNECT_STEPS = {
  download: ["Wermi's Build v7 downloaded", "Download Wermi's Build v7",
             "This is the Project64 build the trainer is tested with."],
  unpack: ["Project64 unpacked", "Unpack Project64",
           "Open the file you downloaded. Choose a folder outside Program Files, such as your Games folder."],
  open: ["Project64 found", "Open Project64",
         "Open Project64.exe in the folder you chose. The trainer finds it by itself."],
  swap: ["", "Close this Project64, then open the new one",
         "Open Project64.exe in the folder you chose. The trainer checks it again by itself."],
};
const FOUND_CHECKS = ["Project64 found", "This is Wermi's Build v7"];

function connectState(setup, accepted) {
  if (setup?.emu?.target?.state !== "ready") return "missing";
  return gateOpen("connect", setup, accepted).length ? "wrong" : "found";
}

// Which sub-step shows now, and the checks above it.
function useConnectSteps(state) {
  const [clicked, setClicked] = useState({});
  // Another build was found: getting the right one starts over.
  useEffect(() => { if (state === "wrong") setClicked({}); }, [state]);
  const order = ["download", "unpack", state === "wrong" ? "swap" : "open"];
  const current = state === "found" ? null : order.find(id => !clicked[id]) || order[2];
  const done = state === "found" ? FOUND_CHECKS
    : order.slice(0, order.indexOf(current)).map(id => CONNECT_STEPS[id][0]);
  const mark = (ids, value = true) => setClicked(now => ({...now, ...Object.fromEntries(ids.map(id => [id, value]))}));
  return {current, done, mark};
}

function ConnectStepActions({ step, setup, mark }) {
  if (step === "download") return html`<div class="setup-step-actions">
    <${DownloadBuild} setup=${setup} onClick=${() => mark(["download"])}/>
    <button type="button" class="setup-step-link" onclick=${() => mark(["download"])}>I downloaded it</button>
    <button type="button" class="setup-step-link" onclick=${() => mark(["download", "unpack"])}>I already have this build</button>
  </div>`;
  if (step === "unpack") return html`<div class="setup-step-actions">
    <button type="button" class="primary-button" onclick=${() => mark(["unpack"])}>I unpacked it</button>
    <button type="button" class="setup-step-link" onclick=${() => mark(["download"], false)}>Go back</button>
  </div>`;
  return html`<div class="setup-step-actions">
    <button type="button" class="setup-step-link" onclick=${() => mark(["unpack"], false)}>Go back</button>
  </div>`;
}

export function ConnectPage({ setup, accepted = [] }) {
  const target = setup?.emu?.target || {};
  const state = connectState(setup, accepted);
  const { current, done, mark } = useConnectSteps(state);
  const [, title, detail] = current ? CONNECT_STEPS[current] : ["", "Project64 found", "Your emulator is connected. Moving on…"];
  const card = html`<${DoneSteps} labels=${done}/>
    <${SetupStatus} done=${!current} title=${title} detail=${detail}>
      ${current && html`<${ConnectStepActions} step=${current} setup=${setup} mark=${mark}/>`}
    </${SetupStatus}>`;
  return html`<div class="setup-connect" data-connect-step=${current || "found"}>
    ${state === "missing" && target.state && target.state !== "missing"
      && html`<p class="setup-warning">${target.message}</p>`}
    ${state === "wrong" ? html`<${GateWarning} page="connect" setup=${setup} accepted=${accepted}>${card}</${GateWarning}>` : card}
    ${state !== "missing" && html`<code class="setup-path">${setup.emu.pj64_dir}</code>`}
    <${Project64Help}/>
  </div>`;
}

function RomHelp() {
  return html`<div class="setup-help-list">
    <${SetupDisclosure} label="I don't have Usamune">
      <p>Usamune is the practice version of Super Mario 64 that the trainer reads.
        Its patcher turns your own SM64 ROM into a practice ROM.</p>
      <p>You'll need to supply your own Super Mario 64 ROM. We don't provide game ROM downloads.</p>
      <${GuideLink} path="getting_game/">Game and Usamune setup guide</${GuideLink}>
    </${SetupDisclosure}>
    <${SetupDisclosure} label="The game or controller isn't working">
      <p>For US tracking, use Usamune v1.93u with 8 MB memory. The guide explains the game settings and controller plugins.</p>
      <${GuideLink} path="basic_config/">Game configuration</${GuideLink}>
      <${GuideLink} path="plugin_setup/">Controller and plugin help</${GuideLink}>
    </${SetupDisclosure}>
  </div>`;
}

function InstallDetails({ emu }) {
  const previous = emu.previous_graphics_dll;
  return html`<${SetupDisclosure} label="What does this install change?">
    <p>Adds one file, <code>sm64_trainer_gfx.dll</code>, to this folder:</p>
    <code class="setup-path">${emu.plugin_dir || `${emu.pj64_dir}\\Plugin`}</code>
    <p>This file is a graphics plugin. It is the trainer's copy of LINK's GLideN64 v4.2, and it
      also records the game for replays. If an older version of the trainer left its own files
      in this folder, they are removed.</p>
    <p>Turns off frame buffer emulation in <code>GLideN64.ini</code> in the same folder, and makes
      that file if it is missing. With this setting on, ghosts do not show and the game lags.
      Removing Practice Replay puts your old setting back. Nothing else is changed or tuned.</p>
    <p>Selects <code>SM64 Trainer v1.0</code> in Project64's <code>Graphics Dll</code> setting.${" "}
      ${previous
        ? html`Your current plugin, <code>${previous}</code>, stays in the folder and is selected again when you remove Practice Replay.`
        : html`No previous graphics plugin is recorded, so removing Practice Replay keeps it selected until you pick another plugin in Project64.`}</p>
    <p>Adds the PJ64 Fixups 2.0, the standard speedrun fix for Project64 1.6. We download
      <code>RSP.dll</code> from its author's GitHub page and put it in the same folder. Your old
      file is kept beside it as <code>RSP.before-fixups</code>.</p>
    <p>You can remove Practice Replay here in Settings any time. Project64 must be closed.
      The PJ64 Fixups stay.</p>
  </${SetupDisclosure}>`;
}

// The game a stage shows: the check stage keeps the one it detected, so its
// flag appears once and stays; the game stage names only a rejected game.
function useStageRom(emu, step) {
  const kept = useRef(null);
  const current = emu.rom?.region && emu.pj64_running ? emu.rom : null;
  if (stageOf(step) !== "check") kept.current = null;
  else if (current) kept.current = current;
  if (step === "rom") return current?.state === "unsupported" ? current : null;
  return kept.current;
}

function useStageDirection(stage) {
  const last = useRef({stage, direction: 1});
  if (last.current.stage !== stage) {
    const back = STAGE_ORDER.indexOf(stage) < STAGE_ORDER.indexOf(last.current.stage);
    last.current = {stage, direction: back ? -1 : 1};
  }
  return last.current.direction;
}

// The install page's finished stages as checks: each stage's own
// acknowledgment, plus the setup check itself once only the gate is left.
function stageChecks(stage, step, gated) {
  const at = STAGE_ORDER.indexOf(stage);
  const labels = at > 0 ? STAGE_ORDER.slice(0, at).map(name => STAGE_COPY[name][2]) : [];
  if (step === "fixups") return [...labels, STAGE_COPY.install[2]];
  return step === "ready" && gated ? [...labels, "Game, replay pictures and inputs checked"] : labels;
}

function FixupsDetails({ setup }) {
  return html`<${SetupDisclosure} label="What we found">
    <${SetupCheckList} check=${setup.emu.setup_check} only=${[FIXUPS]}/>
    <p>We download <code>RSP.dll</code> from its author's GitHub page. Your old file is kept beside it
      as <code>RSP.before-fixups</code>.</p>
  </${SetupDisclosure}>`;
}

// One card per visible stage (`shown`, the modal's useShownStep): the card
// swaps only when the stage changes; the server's finer steps change its text.
export function EmuSetupPane({ setup, shown, installing, onInstall, onRemove, onResume, manual,
                               accepted = [], onFixups }) {
  const emu = setup.emu;
  const { step, stage, acknowledging } = shown;
  const rom = useStageRom(emu, step);
  const direction = useStageDirection(stage);
  const gated = gateOpen("install", setup, accepted).length > 0;
  return html`<div class="setup-install" data-stage=${stage} data-substep=${step}>
    <${DoneSteps} labels=${stageChecks(stage, step, gated)}/>
    <${SetupSwap} identity=${stage} direction=${direction} className="setup-substep-swap">
      <${EmuStepStatus} step=${step} acknowledging=${acknowledging} emu=${emu}
        gated=${gated}
        installing=${installing} onInstall=${onInstall} onFixups=${onFixups}/>
      <${RomResult} rom=${rom}/>
      ${step === "ready" && html`<${GateWarning} page="install" setup=${setup}
        accepted=${accepted} busy=${installing} onFixups=${onFixups}/>`}
      ${step === "fixups" && html`<${FixupsDetails} setup=${setup}/>`}
      ${step === "install" && html`<${InstallDetails} emu=${emu}/>`}
      ${step === "verify" && html`<${VerificationHelp} emu=${emu} installing=${installing} onResume=${onResume}/>`}
      ${["reopen", "emulator"].includes(step) && html`<${Project64Help}/>`}
      ${["rom", "verify", "ready"].includes(step) && html`<${RomHelp}/>`}
      ${manual && emu.wrapper_present && html`<${ManageReplay} emu=${emu} installing=${installing} onRemove=${onRemove}/>`}
    </${SetupSwap}>
  </div>`;
}

export function N64SetupPane({ onEmulator, onFinish, busy }) {
  return html`<div class="setup-console">
    <${SetupStatus} title="Console support is in development"
        detail="We're collecting gameplay data and training the detection models. N64 tracking isn't available yet."/>
    <div class="setup-branch-actions"><button type="button" class="setup-green" onclick=${onEmulator}>Set up Emulator</button>
      <button type="button" disabled=${busy} onclick=${onFinish}>${busy ? "Finishing…" : "Explore the app"}</button></div>
  </div>`;
}

export function CompletionPage({ setup, onFinish, busy }) {
  const limited = setup.emu.verification?.limited;
  return html`<div class="setup-completion">
    <div class="setup-celebration">
    <div class="setup-confetti" aria-hidden="true">${Array.from({length: 24}, (_, i) => html`<i key=${i}
      style=${`--x:${(i * 47 % 101) - 50}%;--rise:${45 + i * 19 % 70}px;--drift:${(i % 2 ? 1 : -1) * (30 + i * 13 % 100)}px;--delay:${i % 6 * 55}ms;--turn:${i % 2 ? 320 : -280}deg;background:${["#f5cf68", "#82e9a7", "#8fc8ff", "#f497bc"][i % 4]}`}></i>`)}</div>
    <div class="setup-cast" aria-label="Boo, Toad and Ukiki">
      ${[["boo_normal", "Boo"], ["toad", "Toad"], ["ukiki_1", "Ukiki"]].map(([stem, name]) =>
        html`<img key=${stem} src=${castSrc(stem)} alt=${name} draggable="false"/>`)}
    </div>
    </div>
    <p>${limited ? "Practice Replay is installed. Your setup is saved when you finish." : "Your game, replays and inputs are connected."}</p>
    ${limited && html`<p class="setup-warning">${setup.emu.rom.warning}</p>`}
    <button class="setup-green setup-finish" type="button" disabled=${busy} onclick=${onFinish}>
      ${busy ? "Finishing…" : "Ready to practice!"}
    </button>
    <${SetupCheckNotes} check=${setup.emu.setup_check}/>
  </div>`;
}

function VerificationHelp({emu, installing, onResume}) {
  return html`<div class="setup-verification-help">
    ${emu.capture_note && html`<${SetupDisclosure} label="Why is this taking a while?">
      <p>${emu.capture_note}</p><p>Resume Usamune and move Mario. If the trainer is paused, resume recording too.</p>
    </${SetupDisclosure}>`}
    ${emu.paused && html`<button type="button" disabled=${installing}
      onclick=${onResume}>Resume trainer to check setup</button>`}
    ${emu.tracking_note && !emu.verification?.limited
      && html`<p class="setup-warning">${emu.tracking_note}</p>`}
  </div>`;
}

function ManageReplay({emu, installing, onRemove}) {
  return html`<${SetupDisclosure} label="Manage Practice Replay">
      <p>Remove the replay component and restore your previous graphics plugin.</p>
      ${emu.pj64_running && html`<p>Close Project64 before removing it.</p>`}
      <button type="button" disabled=${installing || emu.pj64_running} onclick=${onRemove}>Remove Practice Replay</button>
    </${SetupDisclosure}>`;
}

// With the gate open, a working game is not yet "everything is set up".
const GATED_READY = ["One thing left", "Your game works. Your Project64 is still different from the tested setup."];

function FixupsAction({ emu, installing, onFixups }) {
  return html`<div class="setup-install-action">
    ${emu.fixups?.error && html`<p class="setup-error" role="alert">${emu.fixups.error}</p>`}
    <button class="primary-button" type="button" disabled=${installing}
        onclick=${onFixups}>${installing ? "Installing…" : "Install the PJ64 Fixups"}</button>
  </div>`;
}

function EmuStepStatus({step, acknowledging, emu, installing, onInstall, onFixups, gated}) {
  const [stageTitle, , acknowledgment] = STAGE_COPY[stageOf(step)];
  const title = step === "fixups" ? "Install the PJ64 Fixups" : stageTitle;
  const ready = step === "ready" && !acknowledging;
  const [readyTitle, readyDetail] = gated ? GATED_READY : ["Setup checked", stageDetail(step, emu)];
  return html`<${SetupStatus} done=${acknowledging || (step === "ready" && !gated)}
      title=${acknowledging ? acknowledgment : ready ? readyTitle : title}
      detail=${acknowledging ? null : ready ? readyDetail : stageDetail(step, emu)}>
    ${step === "install" && !acknowledging && html`<div class="setup-install-action">
      <p>Adds the trainer's graphics plugin and the PJ64 Fixups to Project64. You can remove the plugin later in Settings.</p>
      <button class="primary-button" type="button" disabled=${installing}
          onclick=${onInstall}>${installing ? "Installing…" : "Install Practice Replay"}</button>
    </div>`}
    ${step === "fixups" && !acknowledging && html`<${FixupsAction} emu=${emu} installing=${installing} onFixups=${onFixups}/>`}
  </${SetupStatus}>`;
}
