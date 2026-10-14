"""Build an offline website review with inert downloads and local Pages headers."""
from __future__ import annotations

import argparse
from functools import partial
import fnmatch
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import build_website as builder

DISABLED_URL = "#preview-download-disabled"
SAMPLE_TIME = "2026-10-13T12:00:00Z"


def sample_release(policy: dict, state: str) -> dict:
    """Display sample metadata without constructing a publishable release feed."""
    release = {"status": state, "release_url": f"https://github.com/{policy['repository']}/releases",
               "installer_url": DISABLED_URL}
    if state == "ready":
        release.update(version=policy["minimum_version"], tag="v" + policy["minimum_version"],
                       installer_size=12_000_000, installer_uploaded_at=SAMPLE_TIME,
                       published_at=SAMPLE_TIME, assets={}, checksums={})
    return release


def build_preview(source: Path, output: Path, state: str = "ready") -> dict:
    """Reuse production rendering, then mark the review and disable downloads."""
    if state not in ("ready", "unavailable"):
        raise builder.BuildError("Preview state must be ready or unavailable.")
    policy = builder.load_policy(source / "release-policy.json")
    metadata = builder.write_site(source, output, policy, sample_release(policy, state))
    page = (output / "index.html").read_bytes().decode("utf-8")
    hook = f'href="{DISABLED_URL}"'
    if page.count(hook) != 3 or page.count("<body>") != 1:
        raise builder.BuildError("Preview download controls or body are missing.")
    page = page.replace(hook, hook + ' aria-disabled="true" tabindex="-1"')
    notice = ('<div class="preview-note" id="preview-download-disabled" role="note">'
              f'Local preview &middot; {state} state &middot; sample release data '
              '&middot; downloads disabled</div>')
    page = page.replace("<body>", "<body>\n  " + notice, 1)
    (output / "index.html").write_bytes(page.encode("utf-8"))
    metadata["status"] = "preview"
    metadata["preview"] = {"rendered_state": state, "downloads_disabled": True}
    (output / "release.json").write_bytes(
        (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return metadata


def header_rules(directory: Path) -> list[tuple[str, dict]]:
    """Read the static path patterns used by the checked-in Pages header file."""
    rules = []
    current = None
    for line in (directory / "_headers").read_bytes().decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            if not line.startswith("/"):
                raise builder.BuildError("Preview headers need static path patterns.")
            current = {}
            rules.append((line.strip(), current))
        else:
            if current is None or ":" not in line:
                raise builder.BuildError("Preview headers contain an invalid field.")
            name, value = line.strip().split(":", 1)
            current[name.casefold()] = (name, value.strip())
    return rules


class PreviewHandler(SimpleHTTPRequestHandler):
    """Serve only the generated directory with its configured response headers."""

    def __init__(self, *args, rules: list, **kwargs):
        self.rules = rules
        super().__init__(*args, **kwargs)

    def end_headers(self):
        path = unquote(urlsplit(self.path).path)
        values = {}
        for pattern, headers in self.rules:
            if fnmatch.fnmatchcase(path, pattern):
                values.update(headers)
        for name, value in values.values():
            self.send_header(name, value)
        super().end_headers()

    def log_message(self, format, *args):
        pass


def make_server(directory: Path, port: int = 8787) -> ThreadingHTTPServer:
    """Return an owned loopback server; callers close it after their review."""
    handler = partial(PreviewHandler, directory=str(directory.resolve()),
                      rules=header_rules(directory))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=builder.ROOT / "website")
    parser.add_argument("--state", choices=("ready", "unavailable"), default="ready")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    try:
        build_preview(args.source, args.output, args.state)
        print(json.dumps({"status": "preview", "state": args.state, "output": str(args.output)}),
              flush=True)
        if args.serve:
            with make_server(args.output, args.port) as server:
                print(json.dumps({"preview_url": f"http://127.0.0.1:{server.server_port}"}),
                      flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        return 0
    except (builder.BuildError, OSError) as error:
        message = str(error) if isinstance(error, builder.BuildError) else "Preview IO failed."
        print(json.dumps({"status": "invalid", "message": message}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
