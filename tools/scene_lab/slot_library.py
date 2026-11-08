"""Drive the Slot Library from a command line (Ghost Handoff; the app's buttons do not exist yet).

    uv run python tools/scene_lab/slot_library.py [--root DIR] [--pid P] COMMAND

    capture                     keep every occupied slot of the running game
    list                        every kept state, oldest first, and the course sets
    rename ID NAME              name a kept state
    install ID --slot N [--dry-run]     write a kept state into slot N (0-8), capturing all slots first
    set-assign LEVEL [--star S] [ID ...]    a course's ordered set; no ids removes it
    set-install LEVEL [--star S] [--dry-run]    what entering that course will do
    export ID FILE.sm64state    write a share file
    import FILE.sm64state       add a share file to the library, and write nothing to the game

ID may be the start of an id. Only install and set-install write to game memory, each inside one slot's range.
Import reads the game once for its Usamune fingerprint; with no game running it accepts the builds the library
already holds.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sm64_events.core.paths import slot_library_dir
from sm64_events.slotlibrary import usamune
from sm64_events.slotlibrary.atlas import default_store
from sm64_events.slotlibrary.coursesets import CourseSets, InstallReport, install_for_course, place_states
from sm64_events.slotlibrary.sharefile import export_state, import_state
from sm64_events.slotlibrary.store import KeptState, SlotLibrary
from sm64_events.slotlibrary.usamune import Refusal, WriteFailed, routine_fingerprint
from sm64_events.slotlibrary.watcher import NEVER_SEEN, watch_step


def describe(state: KeptState) -> str:
    build = (state.build or "unknown build").upper()
    return (f"{state.id[:12]}  {build}  level {state.level} area {state.area}  mario {state.mario_object:08x}  "
            f"{state.source}  {state.first_seen_utc}  {state.name or '(no name)'}")


def print_report(report: InstallReport | None, dry_run: bool) -> int:
    if report is None:
        print("this course has no set; nothing was touched")
        return 0
    for state in report.captured:
        print(f"captured first: {describe(state)}")
    for placed in report.placements:
        action = "dry run" if dry_run else "written" if placed.written else "already there"
        verdict = "loads now" if placed.loadable_now else (
            "Usamune will refuse: " + ("Mario differs" if placed.place_matches else "another level or area"))
        print(f"slot {placed.slot}: {placed.state_id[:12]} {action}; {verdict}; pointers match {placed.pointers_match}")
        if placed.words:
            print(f"    {placed.words}")
    if report.failed:
        print(f"FAILED at slot {report.failed[0]}: {report.failed[1]}", file=sys.stderr)
        return 2
    return 0


def print_timing(started: float, attached: float, atlases) -> None:
    """Where an install's time went, on stderr: finding Project64, then the checks, translation and write, and of
    those the atlas build when this place had none yet."""
    built = "".join(f"; of that, building the atlas for level {level} area {area}: {seconds:.1f} s"
                    for (level, area), seconds in atlases.built.items())
    print(f"timing: attach {attached - started:.1f} s, place {time.perf_counter() - attached:.1f} s{built}",
          file=sys.stderr)


def capture(args, library: SlotLibrary) -> int:
    result = watch_step(usamune.attach(args.pid), library, NEVER_SEEN)
    if result.not_ready:
        raise Refusal(result.not_ready)
    for slot, state in result.captured:
        print(f"slot {slot} new: {describe(state)}")
    for slot, identity in result.known:
        print(f"slot {slot} already kept: {identity[:12]}")
    for slot, why in result.rejected + result.failed:
        print(f"slot {slot} NOT kept: {why}", file=sys.stderr)
    for slot in result.unsettled:
        print(f"slot {slot} NOT kept: it changed while being read; run capture again", file=sys.stderr)
    return 2 if result.rejected or result.failed or result.unsettled else 0


def list_all(library: SlotLibrary, sets: CourseSets) -> int:
    scan = library.scan()
    for state in scan.states:
        print(describe(state))
    for identity in scan.damaged:
        print(f"DAMAGED record: {identity}", file=sys.stderr)
    for entry in sets.all():
        print(f"set: level {entry['level']} star {entry['star']}: " + " ".join(s[:12] for s in entry["states"]))
    return 2 if scan.damaged else 0


def accepted_fingerprints(args, library: SlotLibrary) -> tuple[set[str], str | None]:
    """The running game's fingerprint and build, else every fingerprint the library holds and no build."""
    try:
        memory = usamune.attach(args.pid)
        build = usamune.check_cartridge(memory)
        return {routine_fingerprint(memory.read, build)}, build.version
    except Refusal:
        held = {state.fingerprint for state in library.scan().states}
        if not held:
            raise Refusal("start the game once: with no game and an empty library, no Usamune build is known") from None
        return held, None


def run(args, library: SlotLibrary, sets: CourseSets) -> int:
    match args.command:
        case "capture":
            return capture(args, library)
        case "list":
            return list_all(library, sets)
        case "rename":
            print(describe(library.rename(library.resolve(args.id), args.name)))
        case "install":
            atlases, started = default_store(), time.perf_counter()
            memory = usamune.attach(args.pid)
            attached = time.perf_counter()
            report = place_states(memory, library, {args.slot: library.resolve(args.id)},
                                  dry_run=args.dry_run, atlases=atlases)
            print_timing(started, attached, atlases)
            return print_report(report, args.dry_run)
        case "set-assign":
            sets.assign(library, args.level, args.star, [library.resolve(identity) for identity in args.ids])
            print(f"level {args.level} star {args.star}: {len(args.ids)} states")
        case "set-install":
            report = install_for_course(usamune.attach(args.pid), library, sets, args.level, args.star,
                                        dry_run=args.dry_run, atlases=default_store())
            return print_report(report, args.dry_run)
        case "export":
            print(export_state(library, library.resolve(args.id), args.file))
        case "import":
            state, is_new = import_state(library, args.file, *accepted_fingerprints(args, library))
            print(f"{'added' if is_new else 'already kept'}: {describe(state)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="the library's folder; default the app's user data")
    parser.add_argument("--pid", type=int)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capture")
    commands.add_parser("list")
    rename = commands.add_parser("rename")
    rename.add_argument("id")
    rename.add_argument("name")
    install = commands.add_parser("install")
    install.add_argument("id")
    install.add_argument("--slot", type=int, required=True)
    install.add_argument("--dry-run", action="store_true")
    assign = commands.add_parser("set-assign")
    assign.add_argument("level", type=int)
    assign.add_argument("--star")
    assign.add_argument("ids", nargs="*")
    install_set = commands.add_parser("set-install")
    install_set.add_argument("level", type=int)
    install_set.add_argument("--star")
    install_set.add_argument("--dry-run", action="store_true")
    export = commands.add_parser("export")
    export.add_argument("id")
    export.add_argument("file", type=Path)
    bring = commands.add_parser("import")
    bring.add_argument("file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root or slot_library_dir()
    library, sets = SlotLibrary(root), CourseSets(root / "course-sets.json")
    try:
        return run(args, library, sets)
    except (Refusal, WriteFailed) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
