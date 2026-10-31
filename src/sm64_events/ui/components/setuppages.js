import { h } from "preact";
import { useRef } from "preact/hooks";
import htm from "htm";
import { Icon } from "./icons.js";
import { RegionFlag, REGIONS } from "./regionflag.js";
import { castSrc } from "./emptystate.js";
import { SetupDisclosure, SetupSwap } from "./setupmotion.js";
import { STAGE_ORDER, stageOf } from "../setupflow.js";

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

export function Project64Help() {
  return html`<div class="setup-help-list">
    <${SetupDisclosure} label="I don't have Project64">
      <p>Install Project64 v1.6, then open it. We'll find its folder automatically.</p>
      <${GuideLink} path="getting_emu/">Get Project64 v1.6</${GuideLink}>
      <p>Use a folder you can write to, such as your Games folder.</p>
    </${SetupDisclosure}>
    <${SetupDisclosure} label="It's open, but isn't detected">
      <p>Check that you're using version 1.6. If you have several copies open, close the extras.
        Run Project64 and the trainer under the same Windows account.</p>
    </${SetupDisclosure}>
  </div>`;
}

export function ConnectPage({ setup }) {
  const emu = setup?.emu;
  const found = emu?.target?.state === "ready";
  return html`<div class="setup-connect">
    <${SetupStatus} done=${found} title=${found ? "Project64 found" : "Open Project64 v1.6"}
        detail=${found ? "Your emulator is connected. Moving on…" : "We'll find its folder so we can add Practice Replay."}/>
    ${found && html`<code class="setup-path">${emu.pj64_dir}</code>`}
    ${!found && emu?.target?.state && emu.target.state !== "missing" && html`<p class="setup-warning">${emu.target.message}</p>`}
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
    <p>Adds three files to:</p>
    <code class="setup-path">${emu.plugin_dir || `${emu.pj64_dir}\\Plugin`}</code>
    <p><code>sm64_trainer_gfx.renderer</code> is the trainer's build of LINK's GLideN64 v4.2 with the
      capture hooks; <code>sm64_trainer_gfx.dll</code> and its <code>.ini</code> are the capture layer
      Project64 loads, the only one of these Project64 lists as a graphics plugin.</p>
    <p>Turns off frame buffer emulation in <code>GLideN64.ini</code> in the same folder, and makes
      that file if it is missing. With this setting on, ghosts do not show and the game lags.
      Removing Practice Replay puts your old setting back. Nothing else is changed or tuned.</p>
    <p>Selects <code>SM64 Trainer v1.0</code> in Project64's <code>Graphics Dll</code> setting.${" "}
      ${previous
        ? html`Your current plugin, <code>${previous}</code>, stays in the folder and is selected again when you remove Practice Replay.`
        : html`No previous graphics plugin is recorded, so removing Practice Replay keeps it selected until you pick another plugin in Project64.`}</p>
    <p>You can remove Practice Replay here in Settings any time. Project64 must be closed.</p>
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

// One card per visible stage (`shown`, the modal's useShownStep): the card
// swaps only when the stage changes; the server's finer steps change its text.
export function EmuSetupPane({ setup, shown, installing, onInstall, onRemove, onResume, manual }) {
  const emu = setup.emu;
  const { step, stage, acknowledging } = shown;
  const rom = useStageRom(emu, step);
  const direction = useStageDirection(stage);
  return html`<div class="setup-install" data-stage=${stage} data-substep=${step}>
    <${SetupSwap} identity=${stage} direction=${direction} className="setup-substep-swap">
      <${EmuStepStatus} step=${step} acknowledging=${acknowledging} emu=${emu}
        installing=${installing} onInstall=${onInstall}/>
      <${RomResult} rom=${rom}/>
      ${stage === "install" && html`<${InstallDetails} emu=${emu}/>`}
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

function EmuStepStatus({step, acknowledging, emu, installing, onInstall}) {
  const [title, , acknowledgment] = STAGE_COPY[stageOf(step)];
  return html`<${SetupStatus} done=${acknowledging || step === "ready"}
      title=${acknowledging ? acknowledgment : step === "ready" ? "Setup checked" : title}
      detail=${acknowledging ? null : stageDetail(step, emu)}>
    ${step === "install" && !acknowledging && html`<div class="setup-install-action">
      <p>Adds the trainer's graphics plugin to Project64. You can remove it later in Settings.</p>
      <button class="primary-button" type="button" disabled=${installing}
          onclick=${onInstall}>${installing ? "Installing…" : "Install Practice Replay"}</button>
    </div>`}
  </${SetupStatus}>`;
}
