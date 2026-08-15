# tools/sync_version.py
"""Walk every version-sync gate and write data/version_sync/<version>.json.

WHAT TO HAVE RUNNING: Project64 1.6 with the ROM loaded and UNPAUSED (US:
Usamune v1.93u; JP: whichever build you are syncing). A live tracker server
is optional -- if one answers /health on the usual dev port or on 8066
(run-test-server.bat's port, per CLAUDE.md), verdicts also get PUT to it as
they land, so /ui/sync.html updates live; pass --no-server to skip even
trying, or --server to point it somewhere else.

WHAT YOU WILL BE ASKED TO DO: this script prints one instruction at a time --
"Open any wooden door", "Grab a star while standing on the ground" -- and
waits for Enter before it starts watching. A handful of gates are fully
automatic (ticking counters, things the game already satisfies) and print
without waiting on you at all. It prints verified / failed / skipped as it
goes, with the evidence for each.

    uv run python tools/sync_version.py --version jp
    uv run python tools/sync_version.py --version jp --only "star grab"
    uv run python tools/sync_version.py --version jp --only address.curr_level
    uv run python tools/sync_version.py --version us
        # the non-regression run: every gate should verify against TODAY'S
        # US layout, since nothing about US changed by adding this script.

WHERE THE REPORT LANDS: data/version_sync/<version>.json (data/ is
cwd-relative like every other tool here -- run this from the repo root, or
pass --report-root to point it elsewhere for a dry run). It is rewritten
after EVERY verdict, so stopping the script loses nothing already recorded,
and it is meant to be committed -- it is evidence, like
data/object_pool_probe.jsonl.

WHAT TO HAND CLAUDE WHEN YOU ARE DONE: the printed summary, or just say
"the jp sync report is ready" -- Claude reads the promotion list in it and
writes the confirmed addresses into memory/layout.py's JP row, with their
evidence (tests/test_layout_matches_report.py then keeps the shipped layout
and the report from ever drifting apart).

READ-ONLY against the emulator, always, and this script takes NEITHER the DB
instance lock NOR the recorder lock -- it is safe to run beside a live
practice session (sync/runner.py's module docstring has the full reasoning).
"""
import argparse
import sys
import time
from pathlib import Path

from sm64_events.memory.pj64 import Pj64Memory
from sm64_events.memory.version_probe import detect_version
from sm64_events.sync.runner import default_server, run, summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Walk the version-sync gate registry against a live "
                    "Project64 + ROM, writing data/version_sync/<version>.json.")
    parser.add_argument("--version", choices=("us", "jp"), default=None,
                        help="which ROM's layout to walk; default: read off "
                             "the attached ROM's own header")
    parser.add_argument("--only", default=None,
                        help="one gate id (e.g. address.global_timer) or "
                             "one feature name (e.g. 'star grab')")
    parser.add_argument("--server", default=None,
                        help="also PUT verdicts to this server "
                             "(default: auto-detect on the usual dev ports)")
    parser.add_argument("--no-server", action="store_true",
                        help="never try to reach a server")
    parser.add_argument("--timeout", type=float, default=90.0,
                        help="seconds to watch for a feature/calibration "
                             "gate's event before giving up (default: 90)")
    parser.add_argument("--report-root", type=Path, default=None,
                        help="write the report somewhere other than data/ "
                             "(mainly for a dry run)")
    return parser.parse_args(argv)


def _attach() -> Pj64Memory:
    mem = Pj64Memory()
    print("Attaching to Project64.exe ...")
    while not mem.attach():
        print("  not found (is PJ64 running with the ROM loaded?) retrying in 2s")
        time.sleep(2)
    print("Attached.")
    return mem


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    mem = _attach()
    version = args.version or detect_version(mem)
    if version is None:
        print("could not read the ROM version off the attached emulator -- "
             "pass --version explicitly", file=sys.stderr)
        return 2
    server = None if args.no_server else (args.server or default_server())
    if server:
        print(f"posting verdicts to {server} as they land")
    try:
        report = run(version, mem, only=args.only, server=server,
                    report_root=args.report_root, timeout_s=args.timeout)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print()
    print(summary(report))
    return 1 if any(verdict.status == "failed"
                    for verdict in report.verdicts.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
