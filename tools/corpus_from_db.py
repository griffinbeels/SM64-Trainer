"""A live segment definition -> pasteable `tools/corpus_movements.py` source.

A fix recorded live (the segment builder / "point at what you just did"
recorder, tracking/synthesize.py) lives in one machine's db until someone
copies it into the corpus by hand. This tool is that copy step: it reads
`segment_defs` rows and prints the exact `movement(...)` line a human pastes
into MOVEMENTS, inverting the eight clause helpers in corpus_vocab.py.

    uv run python tools/corpus_from_db.py 35                # by id
    uv run python tools/corpus_from_db.py --all-dirty       # every seed_dirty=1 row
    uv run python tools/corpus_from_db.py --db path\\to.db 35

Read-only (mode=ro, like tools/dedupe_journal.py) -- this tool never writes
to the db, `data/defaults.seed.json`, or `corpus_movements.py` itself. It only
ever emits TEXT for a human to paste and review; `build_defaults_seed.py
--check` remains the only guard on what actually ships.

WHAT segment_row_source DOES NOT CARRY: `guards`, `category`, `default_strat`
and `match_mode` are not part of a `movement(...)` call at all -- every entry
in corpus_movements.py::MOVEMENTS gets those four stamped UNIFORMLY by
build_defaults_seed.py::_movement_row (ROUTE_SCOPED guards, "Castle Movement",
"Standard", the match_mode column's own default), regardless of what the
tuple contains. So a live def whose guards/category/default_strat/match_mode
happen to differ from those constants is not something this tool can (or
should try to) preserve -- there is nowhere in corpus_movements.py's row shape
to put a per-row override. If a def genuinely needs one of those to differ
(a real "loose"-matched recording, say), the paste is still correct for
start/end/via and the four constant fields need setting BY HAND in the UI
after the corpus round-trips it back in -- flagged here, not solved here,
because corpus_vocab.py/build_defaults_seed.py are out of scope for this tool.

Two traps found pre-verifying the inversion (see NotExpressible call sites):
  (a) `enter_area(area)` always hardcodes `level=CASTLE`. A def whose
      `area_enter` clause names a non-castle level (courses have their own
      subareas -- CCM's slide is area 2 -- and synthesize.py's
      `_area_changed_params` will happily record one) cannot be expressed by
      `enter_area()` and is refused rather than silently mis-filed under the
      castle.
  (b) Areas print as the NAMED constants corpus_movements.py actually uses
      (LOBBY/UPSTAIRS/BASEMENT), never the raw ints -- a correct
      `enter_area(3)` round-trips but looks nothing like the file it's meant
      to be pasted into.
A third, found against the live dev db rather than the plan: a `level_exit`/
`level_enter` clause can carry `from_subarea`/`to_subarea` (segments.py's
_DEST_FLOW/_SOURCE_FLOW) once a definition has been edited past the plain
corpus shape. `exit_level()`/`enter_level()` have no parameter for either, so
a clause carrying one refuses rather than silently dropping the constraint.
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_vocab import BASEMENT, CASTLE, LOBBY, UPSTAIRS  # noqa: E402

_AREA_NAMES = {LOBBY: "LOBBY", UPSTAIRS: "UPSTAIRS", BASEMENT: "BASEMENT"}


class NotExpressible(ValueError):
    """A clause or definition shape corpus_vocab.py's helpers cannot invert.
    Carries a human-readable reason; the CLI prints it and moves on rather
    than crashing the whole batch over one row."""


def _area_name(area) -> str:
    name = _AREA_NAMES.get(area)
    if name is None:
        raise NotExpressible(
            f"area {area!r} is not one of LOBBY/UPSTAIRS/BASEMENT -- "
            "corpus_movements.py has no name for it")
    return name


def _no_subarea(clause: dict) -> None:
    """`from_subarea`/`to_subarea` (segments.py's _DEST_FLOW/_SOURCE_FLOW) are
    real, matchable constraints on a level_exit/level_enter clause that
    exit_level()/enter_level() have no parameter for -- trap (c), found
    against the live dev db (an edited MIPS Clip, a freshly-recorded Toad
    Star). Refuse rather than silently print a clause that matches MORE than
    the live one did."""
    for key in ("from_subarea", "to_subarea"):
        if clause.get(key) is not None:
            raise NotExpressible(
                f"{clause['type']} carries {key}={clause[key]!r}; "
                "corpus_vocab has no way to express a subarea constraint "
                "on this trigger")


# --- the inverse of corpus_vocab.py's eight clause helpers ------------------
# One row per TRIGGERS key a movement's start/end/waypoint clause can carry --
# the same registry shape as segments.py's _ORIGIN_PARAMS/_PRECONDITION_PARAM
# and synthesize.py's _SYNTH_PARAMS: a new trigger type is one row here, or an
# explicit "cannot express" entry. `reset_game` is deliberately ABSENT (it
# rides route(start_condition=...), not a movement clause) and is caught by
# name in clause_source with its own message, rather than falling through to
# the generic "unknown trigger type" one.

def _exit_level_src(c: dict) -> str:
    _no_subarea(c)
    args = [str(c["from"])]
    if c.get("to") is not None:
        args.append(f"to={c['to']}")
    return f"exit_level({', '.join(args)})"


def _enter_level_src(c: dict) -> str:
    _no_subarea(c)
    args = [str(c["to"])]
    if c.get("from") is not None:
        args.append(f"frm={c['from']}")
    return f"enter_level({', '.join(args)})"


def _enter_area_src(c: dict) -> str:
    if c.get("level") != CASTLE:
        raise NotExpressible(
            f"area_enter level={c.get('level')!r} is not the castle -- "
            "enter_area() always hardcodes CASTLE and cannot express this")
    args = [_area_name(c["area"])]
    if c.get("from") is not None:
        args.append(f"frm={_area_name(c['from'])}")
    return f"enter_area({', '.join(args)})"


def _grab_star_src(c: dict) -> str:
    return f"grab_star({c['course']}, {c['star']})"


def _enter_warp_src(c: dict) -> str:
    return f"enter_warp({c['level']})"


def _grab_key_src(c: dict) -> str:
    return f"grab_key({c['level']})"


def _anchor_src(c: dict) -> str:
    args = [str(c["level"])]
    if c.get("area") is not None:
        args.append(f"area={_area_name(c['area'])}")
    return f"anchor({', '.join(args)})"


def _spawn_src(c: dict) -> str:
    return f"spawn({c['level']})"


_CLAUSE_BUILDERS = {
    "level_exit": _exit_level_src,
    "level_enter": _enter_level_src,
    "area_enter": _enter_area_src,
    "star_grabbed": _grab_star_src,
    "warp_entered": _enter_warp_src,
    "key_grabbed": _grab_key_src,
    "attempt_anchor": _anchor_src,
    "spawned": _spawn_src,
}


def clause_source(clause: dict) -> str:
    """One TRIGGERS-shaped clause -> the corpus_vocab.py call that builds it,
    or NotExpressible with a reason."""
    kind = clause.get("type")
    if kind == "reset_game":
        raise NotExpressible(
            "reset_game has no clause helper -- it rides "
            "route(start_condition=...), not a segment start/end/waypoint")
    builder = _CLAUSE_BUILDERS.get(kind)
    if builder is None:
        raise NotExpressible(f"no corpus_vocab helper inverts trigger type "
                             f"{kind!r}")
    return builder(clause)


# --- the movement(...) line -------------------------------------------------

def _lit(text: str) -> str:
    """A double-quoted Python string literal matching corpus_movements.py's
    own style. json.dumps (not repr) both quotes with " and keeps a literal
    unicode arrow instead of escaping it to \\uXXXX."""
    return json.dumps(text, ensure_ascii=False)


def _slugify(name: str) -> str:
    """A best-effort seed_key for a definition that never had one -- a
    brand-new user-created segment, not yet in the corpus at all. The corpus
    always gets eyeballed before it ships, so approximate is fine; this only
    saves the human from typing one by hand."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return f"seg:{slug}"


_INDENT = " " * len("movement(")


def segment_row_source(defn: dict) -> str:
    """A live segment_defs row -> the `movement(...)` line for
    tools/corpus_movements.py's MOVEMENTS list, trailing comma included (so
    it can be pasted straight into the list, and so the round-trip test can
    `.rstrip(",")` it back into one bare call). Raises NotExpressible when the
    row's shape has no movement() equivalent."""
    starts, ends = defn["start_triggers"], defn["end_triggers"]
    if len(starts) != 1 or len(ends) != 1:
        raise NotExpressible(
            f"movement() takes exactly one start clause and one end clause; "
            f"this definition has {len(starts)} start / {len(ends)} end "
            "trigger(s) -- that's corpus_legacy.py's _seg() shape, not "
            "movement()")
    start_src = clause_source(starts[0])
    end_src = clause_source(ends[0])

    via_srcs = []
    for waypoint in defn.get("waypoints", []):
        if len(waypoint) != 1:
            raise NotExpressible(
                f"a waypoint has {len(waypoint)} clauses (an any-of); "
                "movement()'s via= only supports single-clause waypoints -- "
                "no corpus movement needs one (corpus_movements.py docstring)")
        via_srcs.append(clause_source(waypoint[0]))

    seed_key = defn.get("seed_key") or _slugify(defn["name"])
    header = f"movement({_lit(seed_key)}, {_lit(defn['name'])},"
    clauses = f"{_INDENT}{start_src}, {end_src}"
    if not via_srcs:
        return f"{header}\n{clauses}),"
    return (f"{header}\n{clauses},\n"
           f"{_INDENT}via=[{', '.join(via_srcs)}]),")


# --- CLI ---------------------------------------------------------------------

def _load_definitions(db_path: Path) -> list[dict]:
    """Every segment_defs row, read-only (mode=ro -- tools/dedupe_journal.py's
    own convention, so running this alongside a live server is always safe:
    this tool never writes anywhere)."""
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM segment_defs ORDER BY id").fetchall()
    conn.close()
    return [{"id": r["id"], "name": r["name"], "seed_key": r["seed_key"],
             "seed_dirty": r["seed_dirty"],
             "start_triggers": json.loads(r["start_triggers"]),
             "end_triggers": json.loads(r["end_triggers"]),
             "waypoints": json.loads(r["waypoints"])} for r in rows]


def main(argv) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("id", nargs="?", type=int,
                        help="segment_defs.id to print")
    parser.add_argument("--all-dirty", action="store_true",
                        help="print every row with seed_dirty=1 (a user "
                             "edit to a seeded row)")
    parser.add_argument("--db", default="data/tracker.db",
                        help="path to tracker.db (default: data/tracker.db)")
    args = parser.parse_args(argv)

    if (args.id is not None) == bool(args.all_dirty):
        parser.error("pass exactly one of: an id, or --all-dirty")

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 1

    defs = _load_definitions(db_path)
    if args.all_dirty:
        targets = [d for d in defs if d["seed_dirty"]]
        if not targets:
            print("No dirty (user-edited) definitions found.", file=sys.stderr)
            return 0
    else:
        targets = [d for d in defs if d["id"] == args.id]
        if not targets:
            print(f"ERROR: no segment_defs row with id={args.id}",
                 file=sys.stderr)
            return 1

    printed = 0
    for defn in targets:
        try:
            print(segment_row_source(defn))
        except NotExpressible as exc:
            print(f"# refused -- {defn['name']!r} (id={defn['id']}): {exc}",
                 file=sys.stderr)
        else:
            printed += 1
    print(f"\n{printed}/{len(targets)} printed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
