"""Update preference and forward installation floor, outside the app tree.

The bootstrap imports this stdlib-only module while the application is broken.
Keep unrelated state (including skipped versions) when updating one field.
"""
import json
import os
from pathlib import Path
import threading

from sm64_events.core.release_contract import version_key

_LOCK = threading.RLock()


def read_policy(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return {"receive_beta_updates": False}
    except (OSError, ValueError):
        return {"receive_beta_updates": False}
    doc["receive_beta_updates"] = doc.get("receive_beta_updates") is True
    floor = doc.get("installed_version")
    try:
        version_key(floor)
    except ValueError:
        doc.pop("installed_version", None)
    return doc


def installed_floor(path: Path, current: str) -> str:
    current_key = version_key(current)
    floor = read_policy(path).get("installed_version")
    return floor if floor and version_key(floor) > current_key else current


def write_policy(path: Path, **changes) -> dict:
    """Merge atomically; an installation record can only move forward."""
    with _LOCK:
        doc = read_policy(path)
        if "receive_beta_updates" in changes and type(changes["receive_beta_updates"]) is not bool:
            raise ValueError("beta preference must be a boolean")
        if "installed_version" in changes:
            proposed = changes["installed_version"]
            version_key(proposed)
            old = doc.get("installed_version")
            if old and version_key(old) > version_key(proposed):
                changes.pop("installed_version")
        doc.update(changes)
        doc["schema"] = 1
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(doc, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        return doc


def record_installed(path: Path, version: str) -> dict:
    return write_policy(path, installed_version=version)
