"""The engine `tools/sync_version.py` drives: walk the gate registry, write
verdicts as they land.

`run()` turns "the human did the thing" into a persisted `Verdict`: it builds
a `GateContext` bound to ONE ROM version, walks `sync.registry.ordered()`
(needs before dependants -- Kahn's algorithm, sync/gates.py), skips a gate
whose needs are not all `verified` in the report yet (recording WHY), and
prints the instruction before every non-auto gate so a human running the
script at a terminal sees exactly what to go do next. It refuses outright
when the attached ROM disagrees with the version it was told to walk --
`detect_version` is cheap and a wrong-version run would otherwise write one
ROM's addresses into the other's report -- with one carve-out: `--only
version.rom` is allowed to run anyway, because that gate's own job is to
report the disagreement, not have the runner pre-empt it.

`working_layout` is the seam between "what's shipped" and "what THIS run has
already confirmed": a report-verified address fills a `None` field so a later
gate (a feature check that needs mario_struct AND curr_level, say) can read
through a layout that is still being discovered -- but a value that already
shipped in `memory/layout.py` is NEVER overridden, because that file is the
non-regression contract (`tests/test_layout_us.py` pins every US value; a
report can only ever supplement JP, never contradict a shipped field).
`GateContext.snapshots` is the only place a gate touches this layout, so a
check never has to know whether an address came from the shipped file or
from a verdict recorded five minutes ago in the same run.

`GateContext.snapshots` is genuinely real-time: it sleeps out the remainder
of each 1/30 s tick so a live run samples RDRAM the same way the poller does
(`server/poller.py`). Offline tests build their OWN lightweight duck-typed
context whose `snapshots()` returns an already-built list with no delay --
`tests/test_sync_feature_gates.py`'s `_FakeContext` is the pattern; nothing
in this file needs to be slow to test.

READ-ONLY, AND NO LOCK. `sync_version.py` takes neither the DB instance lock
(`storage/instance_lock.py`) nor the recorder lock (`core/recorder_lock.py`):
it never writes to the emulator, the database, or the journal, so it is safe
to run beside a live practice session on purpose -- the whole point of this
tool is to gate JP work without ever displacing his recording.
"""
import dataclasses
import json
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.paths import server_port
from sm64_events.core.snapshot import GameSnapshot, SnapshotReader
from sm64_events.memory.behaviours import globals_map
from sm64_events.memory.layout import LAYOUT_ROWS, Layout, layout_for
from sm64_events.memory.version_probe import detect_version
from sm64_events.sync import registry
from sm64_events.sync.gates import Gate, Verdict, gate_id_for_field
from sm64_events.sync.report import Report, report_path

SNAPSHOT_HZ = 30.0
_RUN_TEST_SERVER_PORT = 8066   # run-test-server.bat -- CLAUDE.md, the port he
                               # is usually actually playing on


def working_layout(version: str, report: Report) -> Layout:
    """The shipped layout for `version`, with every field it left `None`
    filled in from a `verified` `address.<field>` verdict in `report`. A
    shipped value always wins -- this can only ever ADD a field, never
    contradict one, which is the same promise
    `tests/test_layout_matches_report.py` checks the other direction (report
    vs. what actually got promoted into layout.py)."""
    base = layout_for(version)
    updates = {}
    for row in LAYOUT_ROWS:
        if getattr(base, row.field) is not None:
            continue
        verdict = report.verdicts.get(gate_id_for_field(row.field))
        if (verdict is not None and verdict.status == "verified"
                and verdict.value is not None):
            updates[row.field] = verdict.value
    return dataclasses.replace(base, **updates) if updates else base


class GateContext:
    """The concrete context a gate's `check` runs against: version, a live
    (or, in a test, scripted) `mem`, the report so far, and how the human
    types answers. Matches the duck-typed shape `sync/gates.py`'s docstring
    describes, so a gate written by any track can be driven by either this
    or a lightweight test double with none of this file's machinery."""

    def __init__(self, version: str, mem, report: Report, *,
                prompt=input, say=print, timeout_s: float = 90.0):
        self.version = version
        self.mem = mem
        self.report = report
        self.timeout_s = timeout_s
        self._prompt = prompt
        self._say = say
        self.layout = working_layout(version, report)

    def candidate(self, field: str) -> int | None:
        """A verified value beats a shipped one beats the STROOP map's own
        guess -- the order an address gate's hunt walks its own candidates
        in (sync/address_gates.py)."""
        verdict = self.report.verdicts.get(gate_id_for_field(field))
        if (verdict is not None and verdict.status == "verified"
                and verdict.value is not None):
            return verdict.value
        shipped = layout_for(self.version).value(field)
        if shipped is not None:
            return shipped
        row = next((row for row in LAYOUT_ROWS if row.field == field), None)
        if row is not None and row.symbol is not None:
            return globals_map(self.version).get(row.symbol)
        return None

    def raw(self):
        return self.mem

    def snapshots(self, seconds: float) -> Iterator[GameSnapshot]:
        """~30 Hz over the layout as far as THIS run has verified it so far
        -- re-resolved on every call, so a gate run after another gate has
        just promoted a field sees it immediately. Raises `LayoutIncomplete`
        (core/snapshot.py) the moment a check asks for more than the human
        has confirmed; the runner's own needs-check (below) is the primary
        guard against that, this is the defensive second layer."""
        self.layout = working_layout(self.version, self.report)
        reader = SnapshotReader(self.mem, self.layout, self.version)
        ticks = max(1, round(seconds * SNAPSHOT_HZ))
        period = 1.0 / SNAPSHOT_HZ
        for _ in range(ticks):
            started = time.perf_counter()
            yield reader.read()
            elapsed = time.perf_counter() - started
            if elapsed < period:
                time.sleep(period - elapsed)

    def prompt(self, text: str) -> str:
        return self._prompt(text)

    def say(self, text: str) -> None:
        self._say(text)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def now(self) -> float:
        return time.perf_counter()


def _select(all_gates: list[Gate], only: str | None) -> list[Gate]:
    if only is None:
        return all_gates
    by_id = [gate for gate in all_gates if gate.id == only]
    if by_id:
        return by_id
    by_feature = [gate for gate in all_gates
                  if gate.feature.lower() == only.lower()]
    if by_feature:
        return by_feature
    raise ValueError(f"--only {only!r} matches no gate id and no feature name")


def _post(server: str | None, version: str, gate_id: str,
         verdict: Verdict) -> None:
    """Best-effort PUT to the live dashboard -- the report on disk is the
    source of truth; this is just so /ui/sync.html moves while he watches.
    `persist: false` tells the server to BROADCAST only: the runner already
    wrote the file, and a server in another checkout (8066 is whatever
    run-test-server.bat launched) must not write a second copy of the report
    beside its own data dir."""
    if not server:
        return
    body = json.dumps({
        "version": version, "gate_id": gate_id, "verdict": verdict.as_json(),
        "at": datetime.now(timezone.utc).isoformat(), "persist": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{server}/api/sync/verdict", data=body, method="PUT",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=1.0)
    except (urllib.error.URLError, OSError, ValueError):
        pass


def run(version: str, mem, *, only: str | None = None,
       server: str | None = None, prompt=input, say=print,
       report_root: Path | None = None, timeout_s: float = 90.0) -> Report:
    """Walk `sync.registry.ordered()` (or the one gate/feature `only` names)
    against `mem`, recording every verdict into
    `data/version_sync/<version>.json` (or `report_root`, for a test) as it
    goes -- `Report.record` rewrites the file atomically after EVERY verdict,
    so killing the process mid-walk loses nothing already decided."""
    report = Report(report_path(version, report_root)).load()
    detected = detect_version(mem)
    if detected is not None and detected != version and only != "version.rom":
        raise ValueError(
            f"attached ROM reads as {detected!r}, not {version!r} -- run "
            "with --only version.rom to see that gate's own verdict, or "
            "attach the ROM you meant to walk")
    if detected is None:
        # Not a disagreement: the header probe found nothing (which byte
        # order PJ64 stores the ROM in is itself a live-gate item), so the
        # walk proceeds on the version he named and version.rom records why.
        say(f"warning: could not read the ROM header; trusting --version {version}")
    report.walked_this_run = []
    all_gates = registry.ordered()
    selected = _select(all_gates, only)
    say(f"walking {len(selected)}/{len(all_gates)} gates for {version}")
    for index, gate in enumerate(selected, start=1):
        unmet = [need for need in gate.needs
                if report.status(need) != "verified"]
        if unmet:
            verdict = Verdict("skipped", evidence=(
                "needs not yet verified: " + ", ".join(unmet)))
        else:
            say(f"[{index}/{len(selected)}] {gate.id} -- {gate.instruction}")
            if not gate.auto:
                prompt("press Enter when ready... ")
            started = time.perf_counter()
            try:
                ctx = GateContext(version, mem, report, prompt=prompt,
                                  say=say, timeout_s=timeout_s)
                verdict = gate.check(ctx)
            except Exception:   # the check's own bug -- reported, not fatal
                last_line = traceback.format_exc().strip().splitlines()[-1]
                verdict = Verdict("failed", evidence=last_line)
            say(f"  {verdict.status} ({time.perf_counter() - started:.1f}s): "
               f"{verdict.evidence}")
        report.record(gate.id, verdict, at=datetime.now(timezone.utc).isoformat())
        report.walked_this_run.append(gate.id)
        _post(server, version, gate.id, verdict)
    return report


def failed_this_run(report: Report) -> list[str]:
    """Gate ids that came back `failed` in THIS walk -- the exit code's
    input. A stale failure loaded from last week's report must not fail
    today's `--only address.curr_level`."""
    walked = getattr(report, "walked_this_run", None)
    ids = walked if walked is not None else list(report.verdicts)
    return [gate_id for gate_id in ids if report.status(gate_id) == "failed"]


def summary(report: Report) -> str:
    """Per-feature verified/failed/skipped counts, then the two lists a
    human (or Claude) acts on directly: the PROMOTION LIST (every address
    this run verified that `memory/layout.py` has not shipped yet, with its
    value) and every gate that came back `failed`, with its evidence."""
    from collections import Counter

    version = report.path.stem
    lines = [f"=== sync summary: {version} ==="]
    optional_open: list[str] = []
    for feature, gates in registry.by_feature().items():
        if not gates:
            continue
        required = [gate for gate in gates if not gate.optional]
        counts = Counter(report.status(gate.id) for gate in required)
        line = f"{feature}: verified {counts['verified']}/{len(required)}"
        if counts["failed"]:
            line += f", failed {counts['failed']}"
        if counts["skipped"]:
            line += f", skipped {counts['skipped']}"
        if counts["missing"]:
            line += f", not yet run {counts['missing']}"
        lines.append(line)
        optional_open += [f"{gate.id} ({report.status(gate.id)})"
                          for gate in gates
                          if gate.optional and report.status(gate.id) != "verified"]
    if optional_open:
        lines.append("optional gates not verified (fine on a healthy run): "
                     + ", ".join(optional_open))

    lines.append("")
    lines.append("promotion list -- verified this run, not yet in "
                 "memory/layout.py:")
    shipped = layout_for(version)
    promoted = [
        (row.field, report.verdicts[gate_id_for_field(row.field)].value)
        for row in LAYOUT_ROWS
        if shipped.value(row.field) is None
        and report.status(gate_id_for_field(row.field)) == "verified"
        and report.verdicts[gate_id_for_field(row.field)].value is not None
    ]
    if promoted:
        lines.extend(f"  {field} = {value:#010x}" for field, value in promoted)
    else:
        lines.append("  (none)")

    failed = [gate for gate in registry.GATES
             if report.status(gate.id) == "failed"]
    lines.append("")
    lines.append("failed gates:" if failed else "failed gates: (none)")
    lines.extend(f"  {gate.id}: {report.verdicts[gate.id].evidence}"
                for gate in failed)
    return "\n".join(lines)


def default_server() -> str | None:
    """The first of our own two conventional dev ports (`core/paths.py`'s
    `server_port()`, then `run-test-server.bat`'s 8066) that answers
    `/health` within half a second, else None -- so pointing the runner at
    whatever he is actually playing on costs him nothing extra."""
    for port in (server_port(), _RUN_TEST_SERVER_PORT):
        url = f"http://127.0.0.1:{port}"
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=0.5) as response:
                if response.status == 200:
                    return url
        except (urllib.error.URLError, OSError, TimeoutError):
            continue
    return None
