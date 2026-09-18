"""Can a save-state LOAD be detected from Usamune's own counter? Score it.

WHY THIS EXISTS. `detectors/anchors.py` fires `state_loaded` when the
EMULATOR-WIDE frame counter jumps backward -- which a full-RAM PJ64 savestate
does and a Usamune section-state load does not. On 2026-08-28 eleven mid-course
loads left that counter perfectly monotonic while only Usamune's counter went
backward, so the event missed all eleven, and the docstring recorded that as a
limit ("which load mechanism leaves the timer alone is unmeasured"). His
ruling, 2026-09-18: *"I don't think we should make this assumption... is there
a way to get it to work?"*

THE CANDIDATE RULE, stated so it can fail. Between two consecutive samples of
the same run, Usamune's counter moves by something ordinary play cannot
produce:
  * BACKWARD to a value ABOVE the near-zero band, or
  * FORWARD by more than the game frames that passed (plus read skew),
with the emulator's frame counter still running forward. Everything else is
named rather than claimed: a drop INTO the near-zero band is a reset or an
area load and this rule can never separate a load from them; a drop from the
u16 ceiling is wraparound; an unmoved counter is a pause or a menu; a hole in
the samples is refused outright, because a reset plus a climb back looks
exactly like a backward jump from either side of a gap.

WHAT IT IS SCORED AGAINST. Two planes, because the machine holds two kinds of
evidence and they answer different halves:
  * DENSE -- the picture ledger's `igt_overall`, one row per drawn picture,
    from the capture scratch (`replay/picturearchive.py`) and saved clip
    sidecars. Consecutive game frames, so the rule can be run exactly as it
    would run on the poll loop. This plane measures FALSE ALARMS.
  * SPARSE -- the journals. Events carry `igt_frames` but nothing in between,
    so a pair can only prove that the counter went backward at least once,
    never on which frame. This plane measures how many loads today's system
    records nothing for. A pair is only counted when NO event that legitimately
    restarts the counter sits between its two ends.

Read-only by construction: every database is copied out with sqlite3's backup
API before a row is read, so a live server's journal and a running recorder's
scratch are never touched.

Usage:
    uv run python tools/score_state_loads.py
    uv run python tools/score_state_loads.py --dense-only --verbose
    uv run python tools/score_state_loads.py --roots <dir> [<dir> ...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from sm64_events.detectors.anchors import (BOOT_TIMER_MAX,  # noqa: E402
                                           IGT_WRAP_CEILING, NEAR_ZERO_IGT)
from sm64_events.memory.addresses import action_label  # noqa: E402
from verify_death_clock import READ_SKEW_FRAMES  # noqa: E402

# A snapshot is a dozen separate reads, so the frame and the counter in ONE
# sample can straddle a game frame -- and a comparison has two ends, each with
# its own skew. That is the whole of the tolerance; it is not a tuning knob.
FORWARD_SLACK = 2 * READ_SKEW_FRAMES

# How far apart two samples may sit and still be scored. Beyond this a reset
# and a climb back are indistinguishable from a backward jump, so the step is
# refused instead of guessed. Five frames is a sixth of a second: the capture
# drops a picture now and then, never a sixth of a second of them.
MAX_STEP_FRAMES = 5

STEADY = "steady"
STALL = "stall"
SAMPLE_GAP = "sample_gap"
CONSOLE_RESET = "console_reset"
EMULATOR_LOAD = "emulator_load"
RESTART_TO_ZERO = "restart_to_zero"
WRAPAROUND = "wraparound"
BACKWARD = "load_backward"
FORWARD = "load_forward"

LOAD_KINDS = frozenset({BACKWARD, FORWARD, EMULATOR_LOAD})
QUIET_KINDS = frozenset({STEADY, STALL})

# Event types that legitimately restart or re-base Usamune's counter. A journal
# pair straddling one of these proves nothing, so it is not scored at all.
RESTARTING_EVENTS = frozenset({
    "practice_reset", "state_loaded", "game_reset", "level_changed",
    "area_changed", "spawned", "moment_reached", "warp_entered",
    "session_started", "emulator_connected", "attempts_pruned",
})


class Sample(NamedTuple):
    """One poll or one drawn picture: both clocks, and what Mario was doing."""
    frame: int                       # gGlobalTimer, the emulator-wide counter
    igt: int | None                  # Usamune's running leg counter, in frames
    action: int | None = None
    warp_fired: bool = False         # the game's own warp countdown reached 0


@dataclass(frozen=True)
class Verdict:
    kind: str
    frames: int
    moved: int | None
    cause: str | None = None
    blind_spot: str | None = None

    @property
    def is_load(self) -> bool:
        return self.kind in LOAD_KINDS


@dataclass
class Scored:
    counts: Counter = field(default_factory=Counter)
    findings: list = field(default_factory=list)
    steps: int = 0

    @property
    def loads(self) -> int:
        return sum(self.counts[kind] for kind in LOAD_KINDS)


def classify(prev: Sample, curr: Sample) -> Verdict:
    """One step of the candidate rule. Every outcome is named, including the
    ones that are not loads -- a scorer that reports only its hits cannot tell
    a quiet trace from a rule that swallowed the evidence."""
    frames = curr.frame - prev.frame
    if prev.igt is None or curr.igt is None:
        return Verdict(SAMPLE_GAP, frames, None, cause="no_counter_reading")
    moved = curr.igt - prev.igt
    if frames < 0:
        # The emulator's own clock rewound: a full-RAM restore, or a boot.
        if curr.frame < BOOT_TIMER_MAX:
            return Verdict(CONSOLE_RESET, frames, moved, cause="boot_range")
        return Verdict(EMULATOR_LOAD, frames, moved, cause="global_timer_rewound")
    if frames > MAX_STEP_FRAMES:
        return Verdict(SAMPLE_GAP, frames, moved, cause="samples_too_far_apart")
    if moved < 0 and curr.igt <= NEAR_ZERO_IGT:
        if prev.igt >= IGT_WRAP_CEILING:
            return Verdict(WRAPAROUND, frames, moved, cause="u16_ceiling")
        # An L-reset, an area load, an in-level teleporter and a textbox's
        # time-stop all land here and NOTHING in these two numbers separates
        # them. The warp flag only carries what the rest of the system already
        # decided; it never promotes the step to a load.
        return Verdict(RESTART_TO_ZERO, frames, moved,
                       cause="area_warp" if curr.warp_fired else "reset_or_area_load",
                       blind_spot="load_into_near_zero")
    if moved < 0:
        return Verdict(BACKWARD, frames, moved, cause="counter_rewound_mid_run")
    if moved > frames + FORWARD_SLACK:
        return Verdict(FORWARD, frames, moved, cause="counter_ran_ahead_of_the_game")
    if moved == 0:
        return Verdict(STALL, frames, moved, cause="paused_or_menu")
    return Verdict(STEADY, frames, moved)


@dataclass(frozen=True)
class Finding:
    kind: str
    at: str
    moved: int | None
    frames: int
    cause: str | None = None
    detail: str = ""


def score_trace(samples: Iterable[Sample]) -> Scored:
    """Run the rule over consecutive samples of one run."""
    scored = Scored()
    previous = None
    for sample in samples:
        if previous is not None:
            verdict = classify(previous, sample)
            scored.counts[verdict.kind] += 1
            scored.steps += 1
            if verdict.kind not in QUIET_KINDS:
                scored.findings.append(Finding(
                    verdict.kind, f"frame {previous.frame}->{sample.frame}",
                    verdict.moved, verdict.frames, verdict.cause,
                    detail=f"{action_label(previous.action or 0)} -> "
                           f"{action_label(sample.action or 0)}"))
        previous = sample
    return scored


def journal_findings(rows: Iterable[dict]) -> list[Finding]:
    """Backward (or impossible-forward) counter movement between two journal
    events with NOTHING between them that could have restarted the counter.

    Deliberately unthresholded. A tight save-state loop reloads a few frames
    before the star, so two consecutive grabs land one or two frames apart --
    a size filter chosen to make the output look clean would hide exactly the
    case the loop produces.

    A restarting event is not an END of a pair either, only a wall between
    pairs. Its own reading belongs to a different epoch of the counter -- a
    `spawned` answers 0 by design -- so pairing it with the next grab measures
    the re-base rather than any jump.
    """
    found: list[Finding] = []
    previous = None
    blocked = False
    session = None
    for row in rows:
        if row.get("session_id") != session:
            session, previous, blocked = row.get("session_id"), None, False
        if row.get("type") in RESTARTING_EVENTS:
            blocked = True
            continue
        igt, frame = row.get("igt_frames"), row.get("frame")
        if igt is None or not frame:
            continue
        if previous is not None and not blocked:
            moved = igt - previous["igt_frames"]
            frames = frame - previous["frame"]
            if frames > 0 and moved < 0:
                found.append(Finding(
                    BACKWARD, f"ids {previous['id']}->{row['id']}", moved, frames,
                    cause="counter_rewound_mid_run",
                    detail=f"{previous['type']}/{previous.get('igt_source')} -> "
                           f"{row['type']}/{row.get('igt_source')}"))
            elif frames > 0 and moved > frames + FORWARD_SLACK:
                found.append(Finding(
                    FORWARD, f"ids {previous['id']}->{row['id']}", moved, frames,
                    cause="counter_ran_ahead_of_the_game",
                    detail=f"{previous['type']} -> {row['type']}"))
        previous, blocked = row, False
    return found


# --- reading the machine's evidence, always through a copy -------------------

def read_only_copy(source: Path, into: Path) -> Path | None:
    """sqlite3's backup API into our own scratch. A live journal or a running
    recorder's identity store is never opened for writing, and never locked."""
    tag = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:10]
    destination = into / f"{source.stem}-{tag}.db"
    try:
        origin = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(destination)
        origin.backup(target)
        target.close()
        origin.close()
        return destination
    except sqlite3.Error:
        # A db left with a hot rollback journal cannot be backed up read-only.
        # Copying the bytes beside it is still read-only on the original.
        try:
            shutil.copy2(source, destination)
            journal = source.with_name(source.name + "-journal")
            if journal.exists():
                shutil.copy2(journal, destination.with_name(destination.name + "-journal"))
            return destination
        except OSError:
            return None


def trace_samples(db: Path) -> list[Sample]:
    """The picture ledger as a sample stream: one row per drawn picture."""
    connection = sqlite3.connect(db)
    try:
        rows = connection.execute("select ts, data from pictures order by ts").fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    by_frame: dict[int, Sample] = {}
    for _ts, raw in rows:
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        frame = row.get("frame")
        if frame is None:
            continue
        mario = row.get("mario") or []
        by_frame.setdefault(frame, Sample(frame, row.get("igt_overall"),
                                          mario[0] if mario else None))
    return [by_frame[frame] for frame in sorted(by_frame)]


def clip_samples(sidecar: Path) -> list[Sample]:
    """A saved clip's own sidecar carries the same rows as `picture_ledger`."""
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    by_frame: dict[int, Sample] = {}
    for row in meta.get("picture_ledger") or []:
        frame = row.get("frame")
        if frame is None:
            continue
        mario = row.get("mario") or []
        by_frame.setdefault(frame, Sample(frame, row.get("igt_overall"),
                                          mario[0] if mario else None))
    return [by_frame[frame] for frame in sorted(by_frame)]


def journal_rows(db: Path) -> list[dict]:
    connection = sqlite3.connect(db)
    try:
        raw = connection.execute(
            "select id, session_id, type, frame, wall_time_utc, payload "
            "from events order by id").fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    rows = []
    for (event_id, session_id, kind, frame, wall, payload) in raw:
        try:
            body = json.loads(payload) if payload else {}
        except (json.JSONDecodeError, TypeError):
            body = {}
        rows.append({"id": event_id, "session_id": session_id, "type": kind,
                     "frame": frame, "wall_time_utc": wall,
                     "igt_frames": body.get("igt_frames"),
                     "igt_source": body.get("igt_source")})
    return rows


def default_roots() -> list[Path]:
    roots = [REPO_ROOT]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "SM64Trainer")
    return [root for root in roots if root.exists()]


def find_traces(roots: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        found.extend(sorted(root.rglob("picture-identity-*.sqlite3")))
        found.extend(sorted(root.rglob("clip_attempt_*.json")))
    return found


def find_journals(roots: Iterable[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        found.extend(sorted(root.rglob("tracker.db")))
        found.extend(sorted(root.rglob("tracker-snapshot.db")))
    return found


# --- the report --------------------------------------------------------------

def run_dense(paths, scratch, minimum_rows, verbose):
    """Score every capture, largest first.

    Two kinds of duplicate would otherwise inflate every number here: an
    evidence archive is a byte copy of some worktree's buffer, and a SAVED
    clip's sidecar repeats a slice of the scratch it was cut from. Both are
    caught by frame identity -- a source whose frames are already covered is
    skipped rather than counted a second time.
    """
    totals, all_findings, scored_sources = Counter(), [], 0
    covered: set[tuple[int, int]] = set()
    loaded = []
    for path in paths:
        if path.suffix == ".json":
            samples = clip_samples(path)
        else:
            copy = read_only_copy(path, scratch)
            samples = trace_samples(copy) if copy else []
        if len(samples) >= minimum_rows:
            loaded.append((path, samples))
    loaded.sort(key=lambda pair: -len(pair[1]))
    print("DENSE plane -- one sample per drawn picture, the rule as it would run live")
    for path, samples in loaded:
        keys = {(sample.frame, sample.igt or -1) for sample in samples}
        if keys <= covered:
            continue
        covered |= keys
        scored = score_trace(samples)
        scored_sources += 1
        totals.update(scored.counts)
        all_findings.extend(scored.findings)
        print(f"  {len(samples):6d} frames  {scored.loads:2d} loads  "
              f"{len(scored.findings):3d} other findings   "
              f"{path.parent.parent.name}/{path.name}")
        if verbose:
            for finding in scored.findings:
                print(f"      {finding.kind:<16} {finding.at:<26} "
                      f"igt {finding.moved:+}  {finding.detail}")
    return totals, all_findings, scored_sources


def run_sparse(paths, scratch, minimum_rows, verbose):
    """Score every journal LINEAGE once, at its fullest snapshot.

    An `.iteration` evidence folder holds the same journal as it stood at two
    different rounds; scoring both counts the shared history twice. A lineage
    is identified by the wall time of its first event, and only the largest
    snapshot of each is scored.
    """
    totals, all_findings, scored_sources = Counter(), [], 0
    print("\nSPARSE plane -- journal events; a pair proves a backward move, not its frame")
    loaded = []
    for path in paths:
        copy = read_only_copy(path, scratch)
        rows = journal_rows(copy) if copy else []
        if len(rows) >= minimum_rows:
            loaded.append((path, rows))
    loaded.sort(key=lambda pair: -len(pair[1]))
    seen: set[str] = set()
    for path, rows in loaded:
        lineage = rows[0]["wall_time_utc"]
        if lineage in seen:
            continue
        seen.add(lineage)
        found = journal_findings(rows)
        scored_sources += 1
        totals.update(Counter(f.kind for f in found))
        all_findings.extend(found)
        already = sum(1 for row in rows if row["type"] == "state_loaded")
        print(f"  {len(rows):6d} events  {len(found):3d} unexplained moves  "
              f"{already:3d} state_loaded already journalled   {path}")
        if verbose:
            for finding in found:
                print(f"      {finding.kind:<16} {finding.at:<22} "
                      f"igt {finding.moved:+6}  over {finding.frames:6d} frames  "
                      f"{finding.detail}")
    return totals, all_findings, scored_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--roots", nargs="*", type=Path, default=None,
                        help="where to look (default: this checkout and the installed app)")
    parser.add_argument("--dense-only", action="store_true")
    parser.add_argument("--sparse-only", action="store_true")
    parser.add_argument("--min-rows", type=int, default=200,
                        help="ignore a source smaller than this (test scratch)")
    parser.add_argument("--verbose", action="store_true",
                        help="list every finding, not just the counts")
    args = parser.parse_args()

    roots = args.roots or default_roots()
    scratch = Path(tempfile.mkdtemp(prefix="score-state-loads-"))
    dense_totals = sparse_totals = Counter()
    dense_findings: list[Finding] = []
    sparse_findings: list[Finding] = []
    try:
        if not args.sparse_only:
            dense_totals, dense_findings, _ = run_dense(
                find_traces(roots), scratch, args.min_rows, args.verbose)
        if not args.dense_only:
            sparse_totals, sparse_findings, _ = run_sparse(
                find_journals(roots), scratch, args.min_rows, args.verbose)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    print("\nDENSE totals, by verdict:")
    for kind, count in sorted(dense_totals.items(), key=lambda item: -item[1]):
        print(f"  {kind:<18} {count}")
    scored_steps = sum(dense_totals[kind] for kind in dense_totals
                       if kind != SAMPLE_GAP)
    print(f"  scored steps       {scored_steps}"
          f"  ({scored_steps / 30 / 60:.1f} minutes of game time)")
    print(f"  FALSE ALARMS of the rule on real play: "
          f"{dense_totals[BACKWARD] + dense_totals[FORWARD]}")
    if dense_findings:
        causes = Counter(f.cause for f in dense_findings)
        print("  what the discontinuities actually were:", dict(causes))
        zeroes = Counter(f.detail for f in dense_findings if f.kind == RESTART_TO_ZERO)
        print("  the near-zero bucket -- the one a load could hide in:")
        for detail, count in zeroes.most_common(8):
            print(f"    {count:4d}  {detail}")

    print("\nSPARSE totals:")
    print(f"  backward, nothing between: {sparse_totals[BACKWARD]}")
    print(f"  forward,  nothing between: {sparse_totals[FORWARD]}")
    pairs = Counter(f.detail for f in sparse_findings)
    for detail, count in pairs.most_common(6):
        print(f"    {count:4d}  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
