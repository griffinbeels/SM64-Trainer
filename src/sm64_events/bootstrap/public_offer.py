"""Public setup's release intent, embedded by the builder, never a credential.

Only the frozen bundle supplies this file. A renamed download or a sidecar in
the working directory cannot choose a channel. Old/source installers omit it.
"""
import json
from pathlib import Path
import sys

from sm64_events.core.release_contract import version_key

FILENAME = "public-installer-offer.json"
MAX_BYTES = 4096


def validate_offer(value: dict) -> dict:
    if (not isinstance(value, dict) or set(value) != {"schema", "channel", "version"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or value["channel"] not in ("stable", "beta")):
        raise ValueError("public installation offer is invalid")
    version = value["version"]
    key = version_key(version)
    if version.startswith(("v", "V")) or bool(key.prerelease) != (value["channel"] == "beta"):
        raise ValueError("public installation offer has mismatched release identity")
    return dict(value)


def read_offer(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError("public installation offer exceeds its size limit or is unsafe")
    return validate_offer(json.loads(path.read_text(encoding="utf-8")))


def bundled_offer() -> dict | None:
    root = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or root is None:
        return None
    path = Path(root) / FILENAME
    return read_offer(path) if path.exists() else None
