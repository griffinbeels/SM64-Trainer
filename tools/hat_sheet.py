"""Render a contact sheet of every registered rank-icon STYLE (rankicon.js::
ICON_STYLES -- hat, medal, and any future style added to that registry) and
screenshot it with headless Chrome, so a palette/geometry change in caps.js,
or a brand-new style, can be judged by eye instead of guessed at.

Renders three full 9-tier x 5-division grids (96px, 30px, 13px cap height),
every style side by side under one heading per size so a new style is judged
against the others rather than alone (task 8 brief, 2026-07-25-mario-cap-
rank-icons), plus one extra 13px strip per style on the light card the
celebration overlay uses. Imports the REAL caps.js/rankicon.js -- a sheet
that drew its own copy of the registry could not catch a palette bug (or a
missing style) in the one it's supposed to check.

The harness wears the REAL design-system stylesheet too: `.hat`'s rules live
in ONE <style> block inside ui/index.html, not a linkable .css file, so the
harness fetches that file at runtime and steals the block rather than
hand-copying it (a copy could silently drift from what the app ships) --
same technique as this repo's other verification harnesses.

Run: uv run python tools/hat_sheet.py
"""
import re
import shutil
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
HARNESS_PATH = UI / "_hat_sheet_harness.html"
OUT_DIR = ROOT / ".superpowers" / "sdd" / "2026-07-25-mario-cap-rank-icons"
OUT_PNG = OUT_DIR / "hat-contact-sheet.png"

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Cap heights the brief calls for: the full-detail size, the detail
# threshold itself (hat.js only draws patch/glyph/wings from here up), and
# the smallest size the ladder-scale strip renders at today.
SIZES = (96, 30, 13)
LIGHT_STRIP_SIZE = 13

HARNESS_HTML = """<!doctype html>
<meta charset="utf-8">
<title>rank icon contact sheet</title>
<script type="importmap">
{"imports": {
  "preact": "/ui/vendor/preact.module.js",
  "preact/hooks": "/ui/vendor/hooks.module.js",
  "htm": "/ui/vendor/htm.module.js"
}}
</script>
<style>
  body { background: #0d1220; color: #d8dee9; margin: 0; padding: 16px;
         font: 12px Consolas, monospace; }
  h2 { font-size: 12px; color: #9fb3c8; margin: 20px 0 8px; font-weight: 600; }
  h3 { font-size: 11px; color: #6f95c2; margin: 0 0 6px; font-weight: 600;
       text-transform: uppercase; letter-spacing: .04em; }
  table { border-collapse: collapse; }
  td, th { padding: 4px 10px; text-align: center; font-weight: 400; color: #7e8796; }
  th.tier { text-align: right; color: #d8dee9; white-space: nowrap; }
  th.div { color: #5f6b7a; }
  .size-section { display: flex; gap: 28px; align-items: flex-start; flex-wrap: wrap; }
  .style-block { flex: 0 0 auto; }
  .strip { display: flex; gap: 24px; align-items: flex-start; }
  .light { background: #e8edf4; padding: 10px; display: inline-flex; gap: 12px; align-items: center; }
</style>
<script type="module">
  import { h, render } from "preact";
  import { ICON_STYLES } from "/ui/components/rankicon.js";
  import { CAP, RANK_NAMES } from "/ui/components/caps.js";

  // Wear the REAL stylesheet: .hat's rules are one <style> block in
  // index.html, not a linkable .css file, so fetch the real page and steal
  // it rather than hand-copying (a copy could silently drift from what the
  // app actually ships). Medal draws with inline styles only, so it needs
  // nothing from this block -- it's fetched for Hat's sake, but any future
  // style that DOES lean on the design system gets it for free too.
  const indexHtml = await fetch("/ui/index.html").then((r) => r.text());
  const styleMatch = indexHtml.match(/<style>([\\s\\S]*?)<\\/style>/);
  if (!styleMatch) throw new Error("index.html has no <style> block to steal");
  const styleTag = document.createElement("style");
  styleTag.textContent = styleMatch[1];
  document.head.appendChild(styleTag);

  // Roman numerals, V (bottom of a tier, no wings) to I (top, four wings) --
  // the same order caps.js's wingTiers/divisionDigit read.
  const DIVISIONS = ["V", "IV", "III", "II", "I"];

  // `renderIcon` is a STYLE's own render function (ICON_STYLES[key].render),
  // never RankIcon itself -- the sheet must show every style at once, not
  // whichever one the settings toggle currently has active.
  function grid(renderIcon, capHeight) {
    const table = document.createElement("table");
    const head = table.insertRow();
    head.appendChild(document.createElement("th"));
    for (const numeral of DIVISIONS) {
      const cell = document.createElement("th");
      cell.className = "div";
      cell.textContent = numeral;
      head.appendChild(cell);
    }
    for (const tier of RANK_NAMES) {
      const row = table.insertRow();
      const label = document.createElement("th");
      label.className = "tier";
      label.textContent = `${CAP[tier].name} (${tier})`;
      row.appendChild(label);
      for (const numeral of DIVISIONS) {
        const holder = document.createElement("td");
        render(h(renderIcon, { tier, division: numeral, size: capHeight }), holder);
        row.appendChild(holder);
      }
    }
    return table;
  }

  function styleBlock(styleEntry, capHeight) {
    const block = document.createElement("div");
    block.className = "style-block";
    const label = document.createElement("h3");
    label.textContent = styleEntry.label;
    block.appendChild(label);
    block.appendChild(grid(styleEntry.render, capHeight));
    return block;
  }

  function heading(text) {
    const el = document.createElement("h2");
    el.textContent = text;
    document.body.appendChild(el);
  }

  for (const size of __SIZES__) {
    heading(`${size}px cap height -- nine tiers x five divisions, every style side by side`);
    const section = document.createElement("div");
    section.className = "size-section";
    for (const styleEntry of Object.values(ICON_STYLES)) {
      section.appendChild(styleBlock(styleEntry, size));
    }
    document.body.appendChild(section);
  }

  heading("__LIGHT_STRIP_SIZE__px again, on the light card the celebration overlay uses");
  const stripRow = document.createElement("div");
  stripRow.className = "strip";
  for (const styleEntry of Object.values(ICON_STYLES)) {
    const light = document.createElement("div");
    light.className = "light";
    for (const tier of RANK_NAMES) {
      const holder = document.createElement("span");
      holder.title = tier;
      render(h(styleEntry.render, { tier, division: "V", size: __LIGHT_STRIP_SIZE__, title: tier }), holder);
      light.appendChild(holder);
    }
    stripRow.appendChild(light);
  }
  document.body.appendChild(stripRow);

  // Read back by the measurement pass -- title is trivially recoverable
  // from --dump-dom without waiting on any image decode, since every icon's
  // box size comes from its own inline width/height, not from the
  // (still-loading) background art.
  document.title = `${document.documentElement.scrollWidth}x${document.documentElement.scrollHeight}`;
</script>
"""


def require_chrome() -> str:
    if not Path(CHROME).exists():
        raise SystemExit(f"Chrome not found at {CHROME}")
    return CHROME


def free_port() -> int:
    """Bind to an ephemeral port and hand it back before the caller uses it,
    same trick the fixture-server verification pattern uses elsewhere."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def run_chrome(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(
        [require_chrome(), "--headless=new", "--force-device-scale-factor=1",
         "--hide-scrollbars", "--virtual-time-budget=3000", *args],
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
    harness = (HARNESS_HTML
               .replace("__SIZES__", "[" + ",".join(str(size) for size in SIZES) + "]")
               .replace("__LIGHT_STRIP_SIZE__", str(LIGHT_STRIP_SIZE)))
    HARNESS_PATH.write_text(harness, encoding="utf-8", newline="")

    port = free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(SRC))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{port}/ui/_hat_sheet_harness.html"

    try:
        # Pass 1: measure. A small window is enough -- nothing here lays
        # out relative to viewport width, so this only needs to avoid
        # accidental wrapping of the light strip's nine icons.
        dump = run_chrome("--dump-dom", "--window-size=1600,900", url)
        match = re.search(r"<title>(\d+)x(\d+)</title>", dump)
        if not match:
            raise SystemExit("measurement pass found no <title>WxH</title> -- "
                              "the harness script did not finish (see stderr above)")
        width, height = (int(value) for value in match.groups())
        print(f"measured content: {width}x{height}")

        # Pass 2: screenshot at the real size, so the sheet holds every
        # section with no wasted navy padding and nothing clipped.
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
