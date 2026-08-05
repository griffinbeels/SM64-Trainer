"""Audit the sheet library by eye, and correct it.

Phase 1 classified 702 rows and 252 targets from somebody else's spreadsheet.
Every verdict is a guess, and a wrong one is INVISIBLE downstream -- a
subsection promoted to an approach is just a fast time attached to the wrong
thing, and the fitted ladders in phase 2 would inherit it without complaint.
This serves the whole classification as one page: every target, its verdict,
its rows with the ratio that decided each one, and the flags on anything
sitting near a boundary. Change what is wrong, press save, and
`tools/scrape_sheet.py` honours it on the next rebuild.

Binds an OS-chosen free port, so it can never collide with the server the
human is playing on. Serves only on localhost and dies with Ctrl+C.

Run: `uv run python tools/audit_library.py`
     `uv run python tools/audit_library.py --no-open`   (do not open a browser)
"""
import argparse
import gzip
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.library.audit import (audit_view, load_overrides,       # noqa: E402
                                       save_overrides)

SNAPSHOT = ROOT / "src" / "sm64_events" / "data" / "sheet_library.seed.json.gz"
DEFAULTS = ROOT / "src" / "sm64_events" / "data" / "defaults.seed.json"
OVERRIDES = ROOT / "src" / "sm64_events" / "data" / "library_overrides.json"
PAGE = Path(__file__).resolve().parent / "audit_library.html"


def read_snapshot() -> dict:
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def segment_names() -> dict:
    """{segment id: name} from the bundled defaults, so the entity picker can
    offer a real segment rather than a bare id."""
    try:
        seed = json.loads(DEFAULTS.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    out = {}
    for index, segment in enumerate(seed.get("segments", []), start=1):
        out[segment.get("id") or index] = segment.get("name") or f"Segment {index}"
    return out


def build_page() -> bytes:
    view = audit_view(read_snapshot(), load_overrides(OVERRIDES), segment_names())
    html = PAGE.read_text(encoding="utf-8")
    return html.replace("__PAYLOAD__",
                        json.dumps(view, ensure_ascii=False)).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):        # the console is the human's, not ours
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self._send(404, b"not found", "text/plain")
            return
        self._send(200, build_page(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/save":
            self._send(404, b"not found", "text/plain")
            return
        try:
            raw = self.rfile.read(int(self.headers.get("content-length", 0)))
            overrides = json.loads(raw.decode("utf-8"))
            save_overrides(OVERRIDES, overrides)
        except (ValueError, OSError) as err:
            self._send(400, str(err).encode("utf-8"), "text/plain; charset=utf-8")
            return
        saved = load_overrides(OVERRIDES)
        counts = {"targets": len(saved["targets"]), "rows": len(saved["rows"])}
        print(f"saved {counts['targets']} target and {counts['rows']} row "
              f"corrections -> {OVERRIDES.relative_to(ROOT)}")
        self._send(200, json.dumps(counts).encode("utf-8"), "application/json")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-open", action="store_true",
                        help="do not open a browser window")
    parser.add_argument("--port", type=int, default=0,
                        help="bind a specific port (default: any free one)")
    args = parser.parse_args()

    if not SNAPSHOT.exists():
        raise SystemExit(f"no snapshot at {SNAPSHOT} -- run tools/scrape_sheet.py")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    view = audit_view(read_snapshot(), load_overrides(OVERRIDES), segment_names())
    flagged = sum(1 for t in view["targets"] if t["flags"])
    print(f"{len(view['targets'])} targets, {flagged} carrying a flag worth a look")
    print(f"audit page: {url}   (Ctrl+C to stop)")
    if not args.no_open:
        # A browser window is the point of the command he just typed, so this
        # focus change is his gesture rather than ours.
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
