"""Render the CAVEAT BADGE on both surfaces, in one image, to LOOK at.

Built as a chooser — three candidate treatments side by side, so the mark that
says "this saved time does not mean what the rank beside it implies" was picked
by looking rather than by reasoning. Griffin picked the CORNER BADGE on
2026-08-01 ("I like the idea of the corner badge") and the other two were
deleted, so this now draws the one that shipped. Adding a candidate back is
adding a renderer to `ui/components/marks.js` and a column here.

Three findings converged on the same two surfaces and are deliberately decided
in one pass, because deciding them separately is how the two surfaces diverge
again (round-4 ledger, 2026-08-01):

  * round-4 item 2  — the quick-select cell floors an unattributable PB, which
                      asserts a rank that contradicts the time beside it. The
                      practice card already refuses to (`_section_banner`'s
                      "unattributed" sentinel); the cell is one surface behind.
  * round-3 ruling 6 — a PB timed by a wall-frame delta whose closing event
                      type WOULD carry Usamune's IGT today: not comparable to
                      a fresh run of the same thing.
  * round-4 item 4  — a star timed at the GRAB rather than the x-cam: the wrong
                      quantity for a leaderboard, true of every star row
                      recorded before 2026-08-01.

The two surfaces have opposite budgets, which is the whole design problem: the
practice card can afford a sentence and the quick-select cell is an icon-only
badge with room for a single glyph. So both are drawn here, for every
treatment, at their REAL sizes.

Imports the REAL PracticeCell, the REAL PbTag and the REAL marks registry, and
wears the REAL stylesheet stolen out of ui/index.html at runtime — a sheet
drawing its own copy of any of those would be judging something the app does
not ship. Same technique as tools/hat_sheet.py, which this is modelled on.

What it deliberately does NOT do: mount the practice card's own fixed-height
chrome. `.objective-card` was welded into practice.js behind a live store
(and is gone from the real page since amendment A8, spec practice-log-entity-
cards — the mark's PB-tag home is a `.log-card` now), so only the metrics
row's own shape (the row the mark actually lands in) is reproduced here.
Once a treatment is picked, confirm it in the real card with
`uv run python tools/contact_sheet.py .log-card` — that one boots the
real app and is the only thing that can answer "does it still fit at 850px".

Run: uv run python tools/mark_sheet.py
"""
import re
import socket
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "sm64_events"
UI = SRC / "ui"
HARNESS_PATH = UI / "_mark_sheet_harness.html"
OUT_DIR = ROOT / ".iteration" / "multi-step-segments"
OUT_PNG = OUT_DIR / "caveat-marks-sheet.png"

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The pane width a full 7-cell star row gets in the real app before its cells
# start shrinking (index.html's --selector-height comment: >= ~980px). Judging
# a 16px mark at any other width judges a cell size that does not ship.
PANE_WIDTH = 980
# The pane the same row gets in an 850px window -- the supported floor
# (CLAUDE.md), where the sidebar is its 76px rail and the cells are at their
# smallest. A 16px mark that reads at 980 and not here is not a mark that ships.
NARROW_PANE = 740

HARNESS_HTML = """<!doctype html>
<meta charset="utf-8">
<title>caveat mark sheet</title>
<script type="importmap">
{"imports": {
  "preact": "/ui/vendor/preact.module.js",
  "preact/hooks": "/ui/vendor/hooks.module.js",
  "htm": "/ui/vendor/htm.module.js"
}}
</script>
<style>
  body { background: #0b1422; color: #d8dee9; margin: 0; padding: 18px 20px 28px;
         font: 12px Consolas, monospace; }
  h1 { font-size: 14px; color: #eef2f6; margin: 0 0 4px; }
  h1 + p { color: #8fa0b5; margin: 0 0 18px; max-width: 900px; line-height: 1.5; }
  h2 { font-size: 13px; color: #f0bd32; margin: 22px 0 2px; }
  h2 + p { color: #8fa0b5; margin: 0 0 8px; max-width: 900px; line-height: 1.5; }
  .legend { color: #66778d; margin: 0 0 10px; }
  .pane { width: __PANE__px; }
  .pane-narrow { width: __NARROW__px; }
  .surface-label { color: #66778d; text-transform: uppercase; letter-spacing: .05em;
                   font-size: 10px; margin: 10px 0 2px; }
  .cardrow { display: flex; flex-direction: column; gap: 6px; width: __PANE__px; }
  .metrics-demo { display: flex; align-items: center; gap: 14px;
                  border: 1px solid #1d3147; border-radius: 10px;
                  background: #0c1928; padding: 10px 14px; }
  .metrics-demo .why { color: #66778d; margin-left: auto; }
</style>
<script type="module">
  import { h, render } from "preact";
  import htm from "htm";
  import { PracticeCell } from "/ui/components/practicecell.js";
  import { PbTag } from "/ui/components/attemptlog.js";
  import { CAVEATS } from "/ui/components/marks.js";

  const html = htm.bind(h);

  // Steal the REAL design-system block: it is one <style> inside index.html,
  // not a linkable .css file, so a copy here could drift from what ships.
  const indexHtml = await fetch("/ui/index.html").then((r) => r.text());
  const styleMatch = indexHtml.match(/<style>([\\s\\S]*?)<\\/style>/);
  if (!styleMatch) throw new Error("index.html has no <style> block to steal");
  const styleTag = document.createElement("style");
  styleTag.textContent = styleMatch[1];
  document.head.appendChild(styleTag);

  const ART = (slot) => `/ui/assets/star_${slot}.png`;
  const RANKED = { rank: "gold", division: "III" };

  // Severity order, worst first -- the order tracking/caveats.py resolves in.
  // Restated here rather than imported because the SERVER owns it (one badge
  // draws one thing, so the server picks); this sheet only needs a stable
  // left-to-right layout, and pinning it to a JS export nothing else reads
  // would invent a second authority.
  const ORDER = ["grab_timed", "old_clock", "unattributed"];

  // Seven cells, the row the app actually draws. Cell 1 is the ACTIVE target
  // and carries no caveat (the control -- a mark is only legible against the
  // ordinary cell beside it); cells 2-4 carry the three caveats in severity
  // order; cells 5-7 are ordinary graded/ungraded cells so the mark is judged
  // in a real row rather than alone.
  function starRow(paneClass) {
    const cells = [
      { name: "Chip Off Whomp's Block", sub: "Standard", rank: RANKED, active: true, caveat: null, slot: 1 },
      { name: "To the Top of the Fortress", sub: "Standard", rank: null, caveat: ORDER[0], slot: 2 },
      { name: "Shoot into the Wild Blue", sub: "Cannonless", rank: RANKED, caveat: ORDER[1], slot: 3 },
      { name: "Red Coins on the Floating Isle", sub: "Standard", rank: null, caveat: ORDER[2], slot: 4 },
      { name: "Fall onto the Caged Island", sub: "Standard", rank: RANKED, caveat: null, slot: 5 },
      { name: "Blast Away the Wall", sub: "Standard", rank: null, caveat: null, slot: 6 },
      { name: "100 Coins", sub: "Standard", rank: null, caveat: null, slot: 6 },
    ];
    return html`<div class="practice-page ${paneClass}"><div class="starrow">
      ${cells.map((cell) => html`<${PracticeCell}
          active=${!!cell.active} iconSrc=${ART(cell.slot)} fallbackSlot=${cell.slot}
          rank=${cell.rank} hasStandards=${true} name=${cell.name}
          sub=${html`<span class="strat">${cell.sub}</span>`}
          dimIdle=${true} caveat=${cell.caveat} />`)}
    </div></div>`;
  }

  // The practice card's own metrics row -- the PB tag is where a mark with
  // room for WORDS lands. One row per caveat, plus the uncaveated control.
  function cardRows() {
    const rows = [null, ...ORDER];
    return html`<div class="cardrow">
      ${rows.map((key) => html`<div class="metrics-demo">
        <div class="objective-live-state"><span>Ready</span></div>
        <${PbTag} pb=${{ display: "0'26\\"30", caveat: key }} mode="pb"
            rows=${[]} pick=${null} t=${{}} />
        <span class="why">${key ? CAVEATS[key].sentence : "(no caveat — the control)"}</span>
      </div>`)}
    </div>`;
  }

  const root = document.body;
  const title = document.createElement("h1");
  title.textContent = "The caveat badge — three findings, two surfaces";
  root.appendChild(title);
  const blurb = document.createElement("p");
  blurb.textContent =
    "Three separate findings, one badge, two surfaces with opposite budgets. "
    + "The quick-select row is at its real pane widths, so the badges are the "
    + "size they ship at; the card row is the practice card's metrics line, "
    + "where the badge can also afford the word (dropped below a 793px pane -- "
    + "see the narrow row, and index.html's own band).";
  root.appendChild(blurb);
  const legend = document.createElement("p");
  legend.className = "legend";
  legend.textContent = ORDER
    .map((key) => `${CAVEATS[key].glyph}  ${CAVEATS[key].short}`).join("      ");
  root.appendChild(legend);

  const heading = document.createElement("h2");
  heading.textContent = "Corner badge   (the shipped treatment)";
  root.appendChild(heading);
  const why = document.createElement("p");
  why.textContent =
    "Your rank and a caveat about the time behind it are two different facts, "
    + "so they are drawn in two places and neither hides the other. Cell 4 "
    + "(Red Coins) is unattributed and therefore draws NO floor -- that "
    + "suppression is the caveat's own property, not the badge's, so cell 3 "
    + "keeps its real medal beside its old-clock mark.";
  root.appendChild(why);

  for (const [paneClass, caption] of [["pane", "quick-select cells — 980px pane"],
                                      ["pane-narrow", "quick-select cells — 740px pane (an 850px window, the supported floor)"]]) {
    const cellLabel = document.createElement("div");
    cellLabel.className = "surface-label";
    cellLabel.textContent = caption;
    root.appendChild(cellLabel);
    const cellHost = document.createElement("div");
    root.appendChild(cellHost);
    render(starRow(paneClass), cellHost);
  }

  const cardLabel = document.createElement("div");
  cardLabel.className = "surface-label";
  cardLabel.textContent = "practice card — metrics row (wide; the word drops below a 793px pane)";
  root.appendChild(cardLabel);
  const cardHost = document.createElement("div");
  root.appendChild(cardHost);
  render(cardRows(), cardHost);

  document.title = `${document.documentElement.scrollWidth}x${document.documentElement.scrollHeight}`;
</script>
"""


def require_chrome() -> str:
    if not Path(CHROME).exists():
        raise SystemExit(f"Chrome not found at {CHROME}")
    return CHROME


def free_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def run_chrome(*args: str, timeout: int = 40) -> str:
    result = subprocess.run(
        [require_chrome(), "--headless=new", "--force-device-scale-factor=1",
         "--hide-scrollbars", "--virtual-time-budget=4000", *args],
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise SystemExit(f"chrome exited {result.returncode}\nstderr:\n{result.stderr}")
    return result.stdout


def assert_port_closed(port: int):
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1)
    try:
        probe.connect(("127.0.0.1", port))
    except (ConnectionRefusedError, OSError):
        print(f"port {port} confirmed free")
        return
    finally:
        probe.close()
    raise SystemExit(f"port {port} is still accepting connections after shutdown")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HARNESS_PATH.write_text(HARNESS_HTML.replace("__PANE__", str(PANE_WIDTH))
                                        .replace("__NARROW__", str(NARROW_PANE)),
                            encoding="utf-8", newline="")

    port = free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(SRC))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{port}/ui/_mark_sheet_harness.html"

    try:
        dump = run_chrome("--dump-dom", "--window-size=1400,1000", url)
        match = re.search(r"<title>(\d+)x(\d+)</title>", dump)
        if not match:
            raise SystemExit(
                "measurement pass found no <title>WxH</title> — the harness "
                "script did not finish. Re-run with --dump-dom by hand and read "
                "the console; a caveat treatment that throws looks exactly like "
                "a treatment that draws nothing.")
        width, height = (int(value) for value in match.groups())
        print(f"measured content: {width}x{height}")

        run_chrome(f"--screenshot={OUT_PNG}", f"--window-size={width},{height}", url)
        if not OUT_PNG.exists():
            raise SystemExit(f"chrome reported success but {OUT_PNG} was not written")
        print(f"wrote {OUT_PNG} ({OUT_PNG.stat().st_size} bytes)")
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        HARNESS_PATH.unlink(missing_ok=True)

    assert_port_closed(port)


if __name__ == "__main__":
    sys.exit(main())
