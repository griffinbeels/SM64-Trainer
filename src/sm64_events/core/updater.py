# src/sm64_events/core/updater.py
"""Self-update for the frozen exe: check the GitHub 'latest' release, download
the new exe, verify its SHA-256, and swap it in over the running process using
the Windows rename-a-running-exe trick (the OS forbids DELETING a running exe
but ALLOWS renaming one). The restart rides core/relaunch.spawn_replacement.

Pure helpers (parse_version … exe_dir_writable) take an injected HTTP opener and
operate on explicit paths so tests never touch the network or a real exe. The
stateful UpdateService orchestrates them, caches the check, tracks download
progress, and persists the 'skipped' version. Everything is guarded on
is_frozen(): from source it is inert (update_available is always False) so a dev
tree is never swapped."""
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sm64_events.core.paths import is_frozen, update_state_path

log = logging.getLogger("sm64.updater")

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
EXE_NAME = "sm64_tracker.exe"
_UA = "sm64_tracker-updater"
_CHECK_TTL_S = 3600.0


def parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). A non-numeric piece stops the parse, so
    '1.2.3-beta' compares as (1, 2, 3)."""
    out: list[int] = []
    for part in tag.lstrip("vV").split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        out.append(int(num))
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)
