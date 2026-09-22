"""The library a running app reads, and the rule for which copy wins.

Two copies exist: the snapshot bundled with the release, and whatever the user
last refreshed into their data directory. THE NEWER SHEET REVISION WINS, in
both directions — that is the whole point, and it is answerable only because a
snapshot carries the Ultimate Sheet's OWN newest Log-tab timestamp rather than
the moment we happened to fetch it.

Both directions matter, and the second is the one a naive rule gets wrong:

  * refresh on Tuesday, update the app on Wednesday to a release built Monday
    → the refreshed copy is newer and must survive the update;
  * refresh in January, update in March → the bundled copy is newer and must
    replace it, rather than a stale local file shadowing the release forever.

`schema_version` is a separate question and is checked first: a copy written by
an older build of THIS code is discarded whatever its sheet revision says,
because its shape is not what the readers expect."""
import gzip
import json
import logging
import os
import shutil
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.library.build import SCHEMA_VERSION
from sm64_events.ranks.calibration import Calibration, CalibrationRegistry, fingerprint, observation_fingerprint

_log = logging.getLogger("sm64.library")


def read_snapshot(path) -> dict | None:
    """A snapshot from disk, gzipped or not, or None when unusable."""
    if not path:
        return None
    path = Path(path)
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                data = json.load(handle)
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, EOFError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("targets"), list):
        return None
    return data


def write_snapshot(path, payload: dict) -> None:
    """Replace one complete gzip atomically; invalid data leaves old bytes intact."""
    path = Path(path)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    compressed = gzip.compress(encoded, compresslevel=9, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            out.write(compressed)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _usable(snapshot) -> bool:
    return bool(snapshot) and snapshot.get("schema_version") == SCHEMA_VERSION


def _validated_revision(payload):
    """A refresh needs the supported shape and a comparable Sheet timestamp."""
    if (not isinstance(payload, dict) or not _usable(payload)
            or not isinstance(payload.get("targets"), list)):
        raise ValueError("library refresh requires the current schema and a targets list")
    revision = payload.get("sheet_revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("library refresh requires a Sheet revision timestamp")
    try:
        parsed = datetime.fromisoformat(revision)
    except ValueError as exc:
        raise ValueError("library refresh requires a valid Sheet revision timestamp") from exc
    # Workbook Log timestamps have no offset; use a common comparison basis.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def build_and_stamp(data: bytes, overrides: dict | None = None, step=None) -> dict:
    """Raw workbook bytes -> a library payload with ladders FITTED and the
    vetted `matched_strategy` pairing stamped on -- the steps every reader of
    raw sheet bytes needs together: a payload with no stamp cannot show "=
    your …" on the Library tab, and cannot resolve
    `library/export_column.py::column_lines`'s matched-strategy path either.

    Fit BEFORE stamp: fitting also computes the stable identity signature
    (`strategy_signature.matching_profile`) used by `adopt.match_vetted`. Until
    round 33 (2026-09-05) this stamped first and `refresh()` fitted after,
    so every live refresh lost the vetted pairing -- measured on the live
    workbook: 270 approaches unmatched and none carrying a vetted name,
    against the bundled snapshot's Time Stop / Open / Log WK -- which is how
    an import came to name strategies "Left side TJ" and "Singlestar strat"
    beside the vetted "Leftside" and "SS". The fit is percentile arithmetic
    over at most a few hundred numbers per row and costs milliseconds.

    `step(fraction, sentence)` narrates the two boundaries when a job is
    watching (`refresh` adds the download before them)."""
    from datetime import datetime, timezone
    from sm64_events.core.paths import bundled_rank_standards
    from sm64_events.library import ladders as ladder_fit
    from sm64_events.library.adopt import stamp_matches
    from sm64_events.library.build import build

    fetched_at = (datetime.now(timezone.utc).replace(microsecond=0)
                  .isoformat().replace("+00:00", "Z"))
    if step:
        step(0.45, "Building the library from the sheet's rows…")
    payload = build(data, fetched_at=fetched_at, overrides=overrides)
    if step:
        step(0.7, "Fitting the rank ladders…")
    ladder_fit.fit_payload(payload)
    seed_path = bundled_rank_standards()
    if seed_path:
        seed = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        stamp_matches(payload,
                      {ek: {s: l for s, l in e.get("strategies", {}).items() if l}
                       for ek, e in seed["entities"].items()})
    return payload


def newer(first, second):
    """Whichever snapshot carries the later SHEET revision, ignoring anything
    written by an older schema. Ties go to the first argument."""
    candidates = [s for s in (first, second) if _usable(s)]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.get("sheet_revision") or "")


class LibraryStore:
    """Read side of the library, plus the refresh that replaces it."""

    def __init__(self, path=None, bundled_path=None):
        self.path = Path(path) if path else None
        self.bundled_path = Path(bundled_path) if bundled_path else None
        # The store OWNS `path`: `refresh`/`absorb` rewrite it whole. So it may
        # never BE the bundled snapshot, which is the read-only fallback every
        # fresh install starts from. Measured 2026-09-05: a harness handed the
        # bundled seed as the store's own path, one import rewrote it without
        # its vetted `matched_strategy` stamps, and fourteen unrelated tests
        # went red in the NEXT full run -- nothing failed at the time, and a
        # tracked file sat quietly modified in the worktree. Hand it a copy.
        if (self.path is not None and self.bundled_path is not None
                and self.path.resolve() == self.bundled_path.resolve()):
            raise ValueError(
                f"the library store would overwrite its own bundled snapshot "
                f"({self.path}); pass a copy as `path`, or omit `path` to read "
                f"the bundled one without ever writing it")
        self._payload = None
        self._source = None   # "local" | "bundled" | None (nothing loaded)
        self.calibrations = CalibrationRegistry()
        self.prepare_calibration = None  # (detached payload) -> complete Calibration

    # ---- load ----
    def load(self) -> None:
        with self.calibrations.update_lock:
            bundled = read_snapshot(self.bundled_path)
            local = read_snapshot(self.path)
            selected = newer(local, bundled)
            if selected is None:
                _log.info("no usable sheet library at %s or %s", self.path, self.bundled_path)
                return
            from sm64_events.library.ladders import LADDER_MODEL_VERSION, fit_payload
            from sm64_events.ranks.policy import RankingPolicy

            # Just parsed from disk and held by nothing else, so the store owns
            # it outright: no copy is needed before it is refitted or prepared.
            payload = selected
            selected_date = _validated_revision(payload)
            current = self._current_payload()
            if current is not None and selected_date < _validated_revision(current):
                _log.info("keeping the active library instead of an older disk snapshot")
                return
            model = payload.get("ladder_model") or {}
            if (model.get("version") != LADDER_MODEL_VERSION
                    or model.get("policy_revision") != RankingPolicy().revision):
                # Refit selected observations offline without rewriting either
                # snapshot. A bound callback resolves effective local policy.
                fit_payload(payload)
            candidate = self._prepare(payload)
            if candidate is not None:
                self.calibrations.publish(candidate)
                payload = candidate.payload
            self._payload = payload
            self._source = "local" if selected is local else "bundled"
            if local is not None and selected is not local:
                _log.info("bundled sheet library is newer (%s) than the local copy "
                          "(%s); using the bundled one", bundled.get("sheet_revision"),
                          local.get("sheet_revision"))

    @property
    def payload(self) -> dict:
        calibration = self.calibrations.read
        if calibration is not None:
            return calibration.payload
        held_empty = self.calibrations.is_pinned and self.calibrations.active is not None
        return (None if held_empty else self._payload) or {"schema_version": SCHEMA_VERSION,
                                 "sheet_revision": None, "targets": [],
                                 "runners": [], "ladder_model": {}}

    @property
    def revision(self) -> str | None:
        return self.payload.get("sheet_revision")

    def status(self) -> dict:
        calibration = self.calibrations.read
        payload = calibration.payload if calibration is not None else self.payload
        return {"sheet_revision": payload.get("sheet_revision"),
                "calibration_revision": calibration.revision if calibration is not None else None,
                "data_revision": (calibration.data_revision if calibration is not None else
                                  observation_fingerprint(payload) if self._payload is not None else None),
                "fetched_at": payload.get("fetched_at"),
                "targets": len(payload["targets"]),
                "runners": len(payload.get("runners") or []),
                "ladder_model": payload.get("ladder_model") or {},
                "source": self._source}

    # ---- reads ----
    def index(self) -> dict:
        """Groups, and the targets inside each — enough to draw a browser
        without shipping 44,000 entries to do it."""
        groups = {}
        for position, target in enumerate(self.payload["targets"]):
            # The stored "group" must be the SAME value used as the dict key --
            # a falsy target["group"] keyed by target["section"] but stamped
            # with the raw (falsy) value would hand the UI a group it can
            # never reopen, since falsy is its own "nothing is open" sentinel.
            key = target["group"] or target["section"]
            group = groups.setdefault(key, {"group": key, "targets": []})
            group["targets"].append({
                "index": position,
                "section": target["section"], "label": target["label"],
                "entity_key": target["entity_key"],
                "miss_reason": target["miss_reason"],
                "approaches": len(target["approaches"]),
                "subsections": len(target["subsections"]),
                # The names of every way of doing this target, so the Library's
                # search box can match on them without a lookup per keystroke.
                # Griffin's call (round 12): a result row is always a TARGET,
                # matched on its own label AND on its approaches' names, so
                # typing "LBLJ" finds the target that documents it. The whole
                # snapshot's 631 names are 15.9 KB of text against an index
                # already carrying 252 rows -- cheap enough that a second
                # endpoint would cost more than it saved, and this way the
                # match runs on data the page already holds.
                "approach_names": [way["name"] for way
                                   in target["approaches"] + target["subsections"]
                                   if way.get("name")],
                "entries": sum(len(item["entries"]) for item
                               in target["approaches"] + target["subsections"])})
        return {"sheet_revision": self.revision, "groups": list(groups.values()),
                "runners": self._runner_roster()}

    def _runner_roster(self) -> dict:
        """`{runner: [target position, ...]}` — who has a time on what.

        A ROSTER rather than a `runner_names` list per target, and the shape
        was picked by measuring rather than by symmetry with `approach_names`
        beside it. A runner appears on 124 targets on average and one appears
        on 324, so the per-target shape repeats the same 448 names until it
        weighs 340 KB against a 70 KB index; deduped this way it is 139 KB,
        and the search reads it the same number of times either way.

        Positions, not labels: they are the ids `groups[].targets[].index`
        already carries, so the client joins without a second name→target
        table of its own.
        """
        roster: dict[str, list[int]] = {}
        for position, target in enumerate(self.payload["targets"]):
            seen = set()
            for way in target["approaches"] + target["subsections"]:
                for entry in way["entries"]:
                    name = entry.get("runner")
                    if name and name not in seen:
                        seen.add(name)
                        roster.setdefault(name, []).append(position)
        return roster

    def target(self, index: int) -> dict | None:
        targets = self.payload["targets"]
        return targets[index] if 0 <= index < len(targets) else None

    def for_entity(self, entity_key: str) -> list:
        """Every target mapped to one entity — what the book mark on the
        objective card jumps to. Several is normal: each `+ 100c` row is the
        same 100-coin star run a different way."""
        return [{"index": position, **target}
                for position, target in enumerate(self.payload["targets"])
                if target["entity_key"] == entity_key]

    def runner(self, name: str) -> dict:
        """One runner's whole sheet, newest question first: what have they run,
        how fast, and where is the video."""
        rows = []
        for position, target in enumerate(self.payload["targets"]):
            for kind in ("approaches", "subsections"):
                for item in target[kind]:
                    for entry in item["entries"]:
                        if entry["runner"] != name:
                            continue
                        rows.append({
                            "target_index": position, "target": target["label"],
                            "section": target["section"],
                            "entity_key": target["entity_key"],
                            "approach": item["name"], "kind": kind[:-1],
                            "time_cs": entry["time_cs"],
                            "video": entry["video"],
                            "ladder": item.get("ladder")})
        return {"runner": name, "entries": rows}

    def runners(self) -> list:
        return list(self.payload.get("runners") or [])

    # ---- refresh ----
    def refresh(self, fetch_fn, overrides=None, step=None) -> dict:
        """Fetch the live sheet and keep newer dates or corrected same-date data.

        A refresh that lands on an older revision than what we already have is
        not an error and is not applied: the sheet is the authority on its own
        age, and re-fetching an unchanged sheet should not churn the file.

        `step(fraction, sentence)` is optional and is called BETWEEN the three
        pieces of real work -- the ~5.6 MB download, the build over its rows,
        the ladder fit -- never inside them (round 29: the sheet import
        narrates itself the way the column export does, and these are the only
        boundaries a refresh genuinely has)."""
        if step:
            step(0.05, "Downloading the current sheet…")
        data = fetch_fn()
        if _usable(self._payload):
            from sm64_events.library.workbook import log_revision
            skipped = self.skip_revision(log_revision(data))
            if skipped is not None:
                return skipped
        return self.absorb(build_and_stamp(data, overrides, step=step))

    def skip_revision(self, revision: str | None) -> dict | None:
        """Cheap pre-build check for the startup worker (library/background.py):
        an OLDER Sheet is never built. A same-date Sheet still builds, because
        `absorb` detects same-date corrections by content; `absorb` rechecks
        the date under the update lock after any concurrent apply."""
        current = self._current_payload()
        if current is None or not _usable(current):
            return None
        probe = {"schema_version": SCHEMA_VERSION, "sheet_revision": revision, "targets": []}
        try:
            older = _validated_revision(probe) < _validated_revision(current)
        except ValueError:
            return None  # the full path reports an unusable revision
        if not older:
            return None
        return self._update_result(False, current, {"sheet_revision": revision},
                                   "the live sheet is older than what we have")

    def absorb(self, fresh: dict, *, prepared_snapshot=None) -> dict:
        """Prepare, save, then publish a complete calibration under one lock.

        The Sheet timestamp protects against rollback; observation content
        detects same-date corrections. A pinned request never supplies the
        update baseline and keeps reading its old complete generation.

        `prepared_snapshot` is the startup worker's compressed file of exactly
        `fresh`. It replaces re-serialising only when no calibration changed
        the payload (calibration refits ladders, so its bytes would be stale).
        """
        with self.calibrations.update_lock:
            payload = deepcopy(fresh)
            incoming_date = _validated_revision(payload)
            current = self._current_payload()
            if current is not None and incoming_date < _validated_revision(current):
                return self._update_result(False, current, payload, "the live sheet is older than what we have")
            candidate = self._prepare(payload)
            if candidate is not None:
                payload = candidate.payload
            if self._unchanged(payload, candidate):
                return self._update_result(False, current, payload, "observations and calibration are unchanged")
            self._activate(payload, candidate,
                           prepared_snapshot if candidate is None else None)
            return self._update_result(True, payload, payload)

    def recalibrate(self) -> dict:
        """Apply policy/assignment changes to the current unpinned observations."""
        with self.calibrations.update_lock:
            current = self._current_payload()
            if current is None or self.prepare_calibration is None:
                return self._update_result(False, current, current, "no calibration preparation is available")
            candidate = self._prepare(deepcopy(current))
            if self._unchanged(candidate.payload, candidate):
                return self._update_result(False, current, current, "observations and calibration are unchanged")
            self._activate(candidate.payload, candidate)
            return self._update_result(True, candidate.payload, candidate.payload)

    def _current_payload(self):
        active = self.calibrations.active
        return active.payload if active is not None else self._payload

    def _prepare(self, payload):
        # Validate all JSON numbers before invoking a callback with side effects.
        fingerprint(payload)
        if self.prepare_calibration is None:
            if self.calibrations.active is not None:
                raise ValueError("an active calibration requires its preparation callback")
            return None
        candidate = self.prepare_calibration(payload)
        if not isinstance(candidate, Calibration):
            raise TypeError("prepare_calibration must return a complete Calibration")
        _validated_revision(candidate.payload)
        if candidate.data_revision != observation_fingerprint(candidate.payload):
            raise ValueError("prepared calibration does not match its observations")
        return candidate

    def _unchanged(self, payload, candidate):
        active = self.calibrations.active
        if candidate is not None and active is not None:
            return candidate.revision == active.revision
        current = self._current_payload()
        return (candidate is None and current is not None
                and observation_fingerprint(payload) == observation_fingerprint(current))

    def _activate(self, payload, candidate, prepared_snapshot=None):
        if self.path:
            if prepared_snapshot is None:
                write_snapshot(self.path, payload)
            else:
                _install_prepared(self.path, prepared_snapshot)
        if candidate is not None:
            self.calibrations.publish(candidate)
        self._payload = payload
        self._source = "local" if self.path else None

    def _update_result(self, applied, payload, fresh, reason=None):
        active = self.calibrations.active
        result = {"applied": applied, "sheet_revision": (payload or {}).get("sheet_revision"),
                  "fetched_revision": (fresh or {}).get("sheet_revision"),
                  "calibration_revision": active.revision if active is not None else None,
                  "data_revision": (active.data_revision if active is not None else
                                    observation_fingerprint(payload) if payload is not None else None)}
        if reason:
            result["reason"] = reason
        elif payload is not None:
            result["targets"] = len(payload["targets"])
        return result


def _install_prepared(path: Path, prepared: Path) -> None:
    """Install the worker's already-compressed snapshot without rebuilding it.

    The caller has decoded/validated that same file. Stage beside the owned
    destination so failure preserves the previous snapshot, even across disks.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(fd)
    pending = Path(name)
    try:
        shutil.copyfile(prepared, pending)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
