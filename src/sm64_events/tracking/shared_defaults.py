"""Source authoring for defaults distributed by the next app release.

Personal database edits never reach this file. A maintainer save records a full
definition, regenerates the bundled seed, then holds the source transaction open
while the caller saves its database row. Failed database saves restore the source
bytes. A base fingerprint makes later corpus edits an explicit conflict rather
than silently hiding them under an older authored definition.
"""
import copy
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
from sm64_events.tracking.defaults import resolve_steps
from sm64_events.tracking.routes import validate_route
from sm64_events.tracking.segments import validate_definition

AUTHORED_NAME = "segments.authored.json"
SEED_NAME = "defaults.seed.json"
FORMAT_VERSION = 1
_LOCKS: dict[Path, threading.Lock] = {}
_OPTIONAL_FIELDS = {"enabled": True, "waypoints": [], "guards": [],
                    "category": None, "default_strat": None,
                    "match_mode": "strict", "parents": [],
                    "clock_start": "trigger"}
_REQUIRED_FIELDS = ("name", "start_triggers", "end_triggers")


def render_json(value: dict) -> str:
    """Stable UTF-8/LF source and generated artifact representation."""
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def base_fingerprint(row: dict | None) -> str | None:
    """Fingerprint the complete base row, independent of JSON key ordering."""
    if row is None:
        return None
    encoded = json.dumps(row, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_authored(path: Path) -> dict:
    """An absent authoring layer is an empty one, for pre-feature checkouts."""
    if not path.exists():
        return {"format_version": FORMAT_VERSION, "segments": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported authored segment format_version")
    if not isinstance(value.get("segments"), list):
        raise ValueError("authored segments must be a list")
    return value


def _key(row: dict, kind: str) -> str:
    if not isinstance(row, dict):
        raise ValueError(f"{kind} row must be an object")
    key = row.get("seed_key")
    if not isinstance(key, str) or not key.strip() or key != key.strip():
        raise ValueError(f"{kind} row needs a non-empty seed_key")
    return key


def _by_key(rows: list, kind: str) -> dict:
    if not isinstance(rows, list):
        raise ValueError(f"{kind} catalogue must be a list")
    result = {}
    for row in rows:
        key = _key(row, kind)
        if key in result:
            raise ValueError(f"duplicate {kind} seed_key {key!r}")
        result[key] = row
    return result


def _portable_parents(row: dict) -> None:
    for parent in row.get("parents") or []:
        if isinstance(parent, str) and parent.startswith("segment:"):
            raise ValueError(
                "Save only for me for a segment with a segment parent, or remove "
                "that parent before saving for everyone. Segment parent IDs "
                "belong to this installation; star and castle-area parents "
                "can be shared.")


def refuse_a_course_load(seed_key: str, end: list | dict) -> None:
    """Keep painting-load delay out of a shipped travelling segment's clock."""
    from sm64_events.memory.addresses import CASTLE_REGION_LEVELS

    for clause in (end if isinstance(end, list) else [end]):
        if (clause.get("type") == "level_enter"
                and clause.get("to") not in CASTLE_REGION_LEVELS):
            raise AssertionError(
                f"{seed_key}: ending on entering level {clause['to']} "
                "measures 77 frames of painting fade as travelling. Use "
                f"enter_entrance({clause['to']}) -- the entrance touch -- "
                "rather than enter_level.")


def validate_catalogue(seed: dict) -> None:
    """Validate the final artifact, including portable route references."""
    if (not isinstance(seed, dict) or not isinstance(seed.get("seed_version"), int)
            or isinstance(seed["seed_version"], bool)):
        raise ValueError("seed needs an integer seed_version")
    segments = _by_key(seed.get("segments"), "segment")
    routes = _by_key(seed.get("routes"), "route")
    landmarks = _by_key(seed.get("landmarks", []), "landmark")
    all_keys = [*segments, *routes, *landmarks]
    if len(set(all_keys)) != len(all_keys):
        raise ValueError("seed_key collides across catalogue kinds")
    for key, row in segments.items():
        if not isinstance(row.get("name"), str) or not row["name"].strip():
            raise ValueError(f"segment {key}: name must be a non-empty string")
        for field in (*_REQUIRED_FIELDS[1:], "waypoints", "guards", "parents"):
            if not isinstance(row.get(field, []), list):
                raise ValueError(f"segment {key}: {field} must be a list")
        validate_definition(row)
        _portable_parents(row)
        _validate_sheet_binding(key, row.get("sheet_target"))
        if not isinstance(row.get("enabled", True), bool):
            raise ValueError(f"segment {key}: enabled must be a boolean")
        if row.get("category") is not None and not isinstance(row["category"], str):
            raise ValueError(f"segment {key}: category must be a string or null")
        refuse_a_course_load(key, row["end_triggers"])
    key_to_id = {key: index for index, key in enumerate(segments, start=1)}
    for key, row in routes.items():
        _validate_seed_route(key, row, key_to_id)
    for key, row in landmarks.items():
        if any(not isinstance(row.get(field), str) or not row[field].strip()
               for field in ("key", "name")):
            raise ValueError(f"landmark {key}: key and name are required")


def _validate_sheet_binding(key: str, binding) -> None:
    if binding is None:
        return
    if (not isinstance(binding, dict)
            or any(not isinstance(binding.get(field), str) or not binding[field].strip()
                   for field in ("section", "label"))):
        raise ValueError(f"segment {key}: sheet_target needs section and label strings")


def _validate_seed_route(key: str, row: dict, key_to_id: dict) -> None:
    steps = row.get("steps")
    if not isinstance(steps, list):
        raise ValueError(f"route {key}: steps must be a list")
    for step in steps:
        if not isinstance(step, dict) or not isinstance(step.get("candidates"), list):
            raise ValueError(f"route {key}: each step needs a candidates list")
        for candidate in step["candidates"]:
            if not isinstance(candidate, dict):
                raise ValueError(f"route {key}: each candidate must be an object")
            if candidate.get("type") == "segment":
                if candidate.get("seed_key") not in key_to_id:
                    raise ValueError(f"route {key}: segment candidate needs a known seed_key")
    validate_route({**row, "steps": resolve_steps(row["steps"], key_to_id)})


def assemble_catalogue(base: dict, authored: dict) -> dict:
    """Replace full base rows or append new rows; reject outdated overlays."""
    seed = copy.deepcopy(base)
    base_rows = _by_key(seed.get("segments"), "segment")
    replacements = {}
    if authored.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported authored segment format_version")
    if not isinstance(authored.get("segments"), list):
        raise ValueError("authored segments must be a list")
    for entry in authored["segments"]:
        if not isinstance(entry, dict) or "base_fingerprint" not in entry:
            raise ValueError("authored segment needs definition and base_fingerprint")
        row = entry.get("definition")
        key = _key(row, "authored segment")
        if key in replacements:
            raise ValueError(f"duplicate authored segment seed_key {key!r}")
        if entry["base_fingerprint"] != base_fingerprint(base_rows.get(key)):
            raise ValueError(
                f"Authored segment {key!r} conflicts with changed base corpus. "
                "Review the base and authored definitions and update its "
                "base_fingerprint deliberately before regenerating.")
        replacements[key] = copy.deepcopy(row)
    seed["segments"] = [replacements.pop(key, row) for key, row in base_rows.items()]
    seed["segments"].extend(replacements.values())
    validate_catalogue(seed)
    return seed


def segment_definition_fields(definition: dict) -> dict:
    """The complete editable definition, excluding installation/seed metadata."""
    if not isinstance(definition, dict):
        raise ValueError("segment definition must be an object")
    missing = [field for field in _REQUIRED_FIELDS if field not in definition]
    if missing:
        raise ValueError(f"shared save requires a full definition: missing {', '.join(missing)}")
    row = {field: definition[field] for field in _REQUIRED_FIELDS}
    row.update({field: definition.get(field, default)
                for field, default in _OPTIONAL_FIELDS.items()})
    return copy.deepcopy(row)


def _full_definition(definition: dict, key: str) -> dict:
    return {"seed_key": key, **segment_definition_fields(definition)}


def _atomic_write(path: Path, data: bytes) -> None:
    """Flush a same-directory temporary file before replacing the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _source_lock(root: Path) -> Iterator[None]:
    """Serialize readers/writers through the database callback, across processes."""
    lock = _LOCKS.setdefault(root, threading.Lock())
    with lock:
        path = root / ".tasks" / "shared-defaults.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as stream:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class SharedDefaultsStore:
    """Tracked full definitions and generated seed in one source checkout."""

    def __init__(self, source_root: Path,
                 base_builder: Callable[[], dict] | None = None):
        self.root = Path(source_root).resolve()
        data = self.root / "src" / "sm64_events" / "data"
        self.authored_path = data / AUTHORED_NAME
        self.seed_path = data / SEED_NAME
        self._base_builder = base_builder or self._build_base

    def _build_base(self) -> dict:
        # The isolated interpreter cannot reuse corpus modules imported from a
        # sibling worktree. Source checkout capability is checked by the server.
        # Shared authoring is source-only. The empty frozen flag is never
        # dispatched: refuse a frozen launch before starting any child.
        command, env = python_child("tools.build_defaults_seed", "", ("--base",))
        if env is None:
            raise RuntimeError("Shared defaults authoring requires a source checkout.")
        env["PYTHONUTF8"] = "1"
        env["PYTHONPATH"] = os.pathsep.join((str(self.root / "src"), env["PYTHONPATH"]))
        try:
            process = subprocess.run(
                command, env=env, cwd=self.root, capture_output=True, text=True,
                encoding="utf-8", timeout=30, check=True, **quiet_spawn_kwargs())
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                "Cannot build the shared defaults corpus. Run "
                "uv run python tools/build_defaults_seed.py --check in this "
                "source checkout and fix the reported problem before saving.") from exc
        return json.loads(process.stdout)

    @contextmanager
    def publish_segment(self, definition: dict,
                        seed_key: str | None = None) -> Iterator[str]:
        """Persist before yielding the key; roll back if the caller's save fails."""
        with _source_lock(self.root):
            base = self._base_builder()
            authored = read_authored(self.authored_path)
            # An existing source conflict must be reviewed, never cleared by Save.
            assemble_catalogue(base, authored)
            key = self._publish_key(definition, seed_key, base, authored)
            base_row = _by_key(base["segments"], "segment").get(key)
            # Binding annotations belong to seed authoring, not to database
            # edits. Preserve them while replacing every editable field.
            existing = next((entry["definition"] for entry in authored["segments"]
                             if entry["definition"]["seed_key"] == key), base_row or {})
            row = {**existing, **_full_definition(definition, key)}
            authored["segments"] = [entry for entry in authored["segments"]
                                     if entry["definition"]["seed_key"] != key]
            authored["segments"].append({"base_fingerprint": base_fingerprint(base_row),
                                         "definition": row})
            try:
                seed = assemble_catalogue(base, authored)
            except AssertionError as exc:
                raise ValueError(str(exc)) from exc
            with self._write_transaction(authored, seed):
                yield key

    @staticmethod
    def _publish_key(definition: dict, requested: str | None,
                     base: dict, authored: dict) -> str:
        if not isinstance(definition, dict):
            raise ValueError("segment definition must be an object")
        key = requested if requested is not None else definition.get("seed_key")
        existing = {row["seed_key"] for row in base["segments"]}
        existing.update(entry["definition"]["seed_key"] for entry in authored["segments"])
        if key is None:
            key = f"seg:authored:{uuid.uuid4().hex}"
            while key in existing:
                key = f"seg:authored:{uuid.uuid4().hex}"
        if (not isinstance(key, str) or not key.startswith("seg:")
                or len(key) <= 4 or key != key.strip()):
            raise ValueError("shared segment seed_key must start with 'seg:'")
        if requested and definition.get("seed_key") not in (None, requested):
            raise ValueError("definition seed_key differs from requested seed_key")
        return key

    @contextmanager
    def _write_transaction(self, authored: dict, seed: dict) -> Iterator[None]:
        paths = (self.authored_path, self.seed_path)
        original = {path: path.read_bytes() if path.exists() else None for path in paths}
        written = []
        try:
            for path, value in zip(paths, (authored, seed), strict=True):
                _atomic_write(path, render_json(value).encode("utf-8"))
                written.append(path)
            yield
        except BaseException:
            for path in written:
                data = original[path]
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    _atomic_write(path, data)
            raise
