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
# Who set `receive_beta_updates`. Every write persists the flag, so its mere
# presence never meant a choice: an install record alone writes `false`.
BETA_CHOICES = ("player", "build")


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
        if "beta_choice" in changes and changes["beta_choice"] not in BETA_CHOICES:
            raise ValueError("beta preference source must be the player or the build")
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


def adopt_channel_default(path: Path, channel: str) -> dict:
    """A beta build opts its installation into beta updates once, unless the
    player already chose either way; a production build changes nothing.

    His rule (round 50): "The beta build that I send over to my tester should
    already include this functionality (they automatically get the latest
    version between prod/beta, and if I push any fixes / improvements,
    they'll get it)", while production users "can select in the settings if
    they want to opt into beta builds". The choice persists, so a tester stays
    on betas until they untick Settings > Receive beta updates.
    """
    with _LOCK:
        doc = read_policy(path)
        if channel != "beta" or doc.get("beta_choice") in BETA_CHOICES:
            return doc
        return write_policy(path, receive_beta_updates=True, beta_choice="build")
