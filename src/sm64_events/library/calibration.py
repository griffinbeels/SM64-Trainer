"""Prepare the local grading population using the same placement as imports.

Source row identity and timed stretches stay in Library; statistical fitting
and the resulting curve do not depend on a local database or route preset.

`SavedCalibration` keeps the last validated preparation on disk, so a restart
whose inputs are identical reuses it instead of refitting every Overall curve
(~1.8 s of every launch, measured 2026-09-25).
"""
from collections import defaultdict
from dataclasses import dataclass
from functools import cache
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import pickle
import sys
import tempfile

from sm64_events.library.adoptions import _rows, library_ladders
from sm64_events.library.audit import target_key
from sm64_events.library.ladder_estimates import estimate_times
from sm64_events.library.ladders import fit_payload, row_times
from sm64_events.library.placements import row_identity, scoring_identity, scoring_rows
from sm64_events.ranks.calibration import Calibration, build_calibration, fingerprint

_log = logging.getLogger("sm64.library")


def prepare(payload, assignments, definitions, standards, policy):
    """Fit both regions, keyed locally but configured by stable star/seed identity.

    `payload` must be detached: `LibraryStore` hands every preparation a
    payload of its own, which may be refitted here and becomes the
    calibration's payload. Everything else read from the arguments is keyed by
    `Adoptions._calibration_inputs`, which skips a preparation it has already
    made; a new read from `standards` belongs in its `calibration_inputs`."""
    from sm64_events.ranks.overall import fit_overall
    grouped = defaultdict(list)
    identities = {f"segment:{definition['id']}": definition.get("seed_key")
                  for definition in definitions}
    grading_rows = scoring_rows(payload, assignments, definitions)

    def strategy_identity(target, item, kind):
        kind = {"approaches": "approach", "subsections": "subsection"}.get(kind, kind)
        placed = row_identity(target, item, kind, assignments)
        entity, strategy = placed or ("", item.get("matched_strategy") or item.get("name"))
        stable = entity if entity.startswith("star:") else identities.get(entity)
        return stable or target_key(target), strategy

    # Loaded snapshots are already fitted. Refit when a scoped policy changes
    # the effective strategy settings, including a newly resolved local seed.
    for target, item, _key, kind in _rows(payload):
        stable, strategy = strategy_identity(target, item, kind)
        version = item.get("ladder_version") or "us"
        previous = item.get("ladder_policy_revision") or policy.effective_revision(
            "", version=version, layer="strategy")
        expected = policy.effective_revision(stable, version, strategy, "strategy")
        previous_jp = item.get("ladder_jp_policy_revision") or policy.effective_revision(
            "", version="jp", layer="strategy")
        jp_changed = bool(item.get("ladder_jp")) and previous_jp != policy.effective_revision(
            stable, "jp", strategy, "strategy")
        if expected != previous or jp_changed:
            fit_payload(payload, policy=policy, identity_of=strategy_identity)
            break
    populations = [(target, kind, item, *row_times(item))
                   for target, item, _key, kind in _rows(payload)]
    for target, item, key, kind in _rows(payload):
        identity = scoring_identity(target, item, kind, grading_rows, standards.clock_for)
        if identity is None:
            placed = row_identity(target, item, kind, grading_rows)
            if placed is not None:
                grouped.setdefault(placed[0], [])
            continue
        entity, strategy = identity
        stable = (entity if entity.startswith("star:") else identities.get(entity))
        stable = stable or (target_key(target) if kind == "approach" else key)
        identities[entity] = stable
        row = {**item, "row_id": key, "target_id": stable, "strategy": strategy}
        if not item.get("entries"):
            times, version, provenance = estimate_times(target, kind, item, populations)
            row.update(estimate_times_cs=times, estimate_provenance=provenance,
                       estimate_version=version)
        grouped[entity].append(row)
    overall = {}
    for entity, rows in grouped.items():
        versions = {}
        for version in ("us", "jp"):
            curve = fit_overall(rows, policy=policy, target_id=identities.get(entity) or entity, version=version)
            if curve is not None:
                versions[version] = curve
        # Empty is an explicit no-compatible-evidence result, not permission
        # to resurrect excluded observations through a strategy fallback.
        overall[entity] = versions
    layers = standards.sheet_layers(library_ladders(payload, assignments))
    return build_calibration(payload, assignments, layers, overall,
                             {key: value for key, value in identities.items() if value},
                             policy.revision, scoring_rows=grading_rows, owned_payload=True)


@dataclass(frozen=True)
class Preparation:
    """Everything `prepare` receives besides the payload, gathered first.

    Gathering provisions practice entries in the database, so it runs where
    the caller owns that state; `run` is the pure fit and may run on a worker
    thread. `key` names every input here (`Adoptions._calibration_inputs`);
    `generation` is the gatherer's count of input changes when it was made."""
    key: str
    assignments: dict
    definitions: list
    standards: object
    policy: object
    generation: int = 0

    def run(self, payload):
        return prepare(payload, self.assignments, self.definitions, self.standards, self.policy)


# Every source a preparation's result depends on: the fit, placement,
# population and curve code, and the helpers they import from elsewhere.
_CODE_SOURCES = ("library", "ranks", "core/timefmt.py", "memory/addresses.py",
                 "tracking/activestrat.py")
SAVED_FORMAT = 1
_MAGIC = b"sm64-rank-calibration\n"
_MAX_SAVED_BYTES = 64 * 1024 * 1024


@cache
def code_revision() -> str:
    """Identity of the code that prepares a calibration, fixed per process.

    From source it hashes the preparation's sources, so any edit invalidates
    a saved calibration. A frozen build has no sources: its version and the
    executable that embeds the code stand in, and every build replaces that
    executable."""
    if getattr(sys, "frozen", False):
        from sm64_events.core.version import __version__
        stat = Path(sys.executable).stat()
        return fingerprint(["frozen", __version__, stat.st_size, stat.st_mtime_ns])
    return source_revision(Path(__file__).resolve().parents[1])


def source_revision(root: Path) -> str:
    """Hash of every `_CODE_SOURCES` file under the package root `root`."""
    digest = hashlib.sha256()
    for name in _CODE_SOURCES:
        base = root / name
        for path in sorted(base.rglob("*.py")) if base.is_dir() else [base]:
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def payload_digest(payload: dict) -> str:
    """The payload's identity for a preparation: all of it but the fetch
    time, which no preparation reads and every refresh restamps. Like
    `fingerprint`, it refuses non-finite numbers."""
    return fingerprint({**payload, "fetched_at": None})


def saved_key(payload_digest: str, inputs_key: str) -> str:
    """Names one preparation: the payload by content, every other input, the
    Overall model, and the code and interpreter that fit it."""
    from sm64_events.ranks.overall import OVERALL_MODEL_VERSION
    return fingerprint({"format": SAVED_FORMAT, "payload": payload_digest,
                        "inputs": inputs_key, "overall_model": OVERALL_MODEL_VERSION,
                        "code": code_revision(), "python": list(sys.version_info[:2])})


class _CalibrationUnpickler(pickle.Unpickler):
    """A saved calibration is plain data inside one `Calibration`; refuse any
    other class rather than build whatever a damaged file names."""

    def find_class(self, module, name):
        if (module, name) == (Calibration.__module__, Calibration.__qualname__):
            return Calibration
        raise pickle.UnpicklingError(f"a saved calibration cannot hold {module}.{name}")


class SavedCalibration:
    """The last validated calibration, on disk beside the library it came from.

    One file, replaced whole: a header naming its key and body checksum, then
    the pickled `Calibration`. Anything unexpected -- missing, another key or
    format, a torn or altered body -- reads as a miss, and the caller prepares
    exactly as it would have without the file."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self, key: str) -> Calibration | None:
        try:
            with self.path.open("rb") as handle:
                data = handle.read(_MAX_SAVED_BYTES + 1)
        except OSError:
            return None
        try:
            if len(data) > _MAX_SAVED_BYTES or not data.startswith(_MAGIC):
                raise ValueError("not a saved calibration")
            end = data.index(b"\n", len(_MAGIC))
            header = json.loads(data[len(_MAGIC):end])
            if header.get("format") != SAVED_FORMAT or header.get("key") != key:
                return None
            body = memoryview(data)[end + 1:]
            if (len(body) != header.get("size")
                    or hashlib.sha256(body).hexdigest() != header.get("sha256")):
                raise ValueError("saved calibration body does not match its header")
            value = _CalibrationUnpickler(io.BytesIO(body)).load()
            if not isinstance(value, Calibration):
                raise ValueError("saved calibration is not a Calibration")
        except Exception as error:  # noqa: BLE001 -- any damage is a miss, never a failure
            _log.info("ignoring saved rank calibration %s: %r", self.path, error)
            return None
        return value

    def save(self, key: str, calibration: Calibration) -> None:
        """Replace the file atomically; a failed write leaves a miss, not an error."""
        body = pickle.dumps(calibration, protocol=pickle.HIGHEST_PROTOCOL)
        header = json.dumps({"format": SAVED_FORMAT, "key": key, "size": len(body),
                             "sha256": hashlib.sha256(body).hexdigest()}).encode("utf-8")
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix=f".{self.path.name}.",
                                             suffix=".tmp", delete=False) as out:
                temporary = Path(out.name)
                out.write(_MAGIC + header + b"\n")
                out.write(body)
            os.replace(temporary, self.path)
        except OSError as error:
            _log.info("could not save the rank calibration to %s: %r", self.path, error)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
