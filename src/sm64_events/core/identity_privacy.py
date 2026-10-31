"""The last pass over everything that leaves this machine: no account or computer name.

Griffin (2026-10-30): "we should also intentionally purge any identifying file
paths (like a user's username) from the filepath / files / any debug logs /
etc, just to make sure that the user's info stays private".

The allowlists (`error_reporting_privacy`, `diagnostic_privacy`) already drop
paths and free text; this pass is the backstop that does not depend on any
one field being shaped as a path. It replaces this machine's Windows account
name, the home folder's own name and the computer name with "[user]" and
"[computer]", ignoring case, wherever they appear as text. A name shorter than
three characters would wreck ordinary words, so it is replaced only as a
whole path component (between slashes).

Every outgoing door calls it: Sentry events (`safe_event`), breadcrumbs
(`safe_breadcrumb`), Sentry Logs (`safe_log`, `safe_log_item`), and the Debug
report bytes, exported or attached (`diagnostic_retention.bounded_report`).

Limits: it knows only the names this process can read cheaply (environment,
`getpass`, the home folder, the host name). A name the user typed somewhere
else (a different account's folder, a display name, an email) is not known
here; the allowlists are what keep those out.
"""
from __future__ import annotations

import functools
import getpass
import os
from pathlib import Path, PureWindowsPath
import platform
import re

USER = "[user]"
COMPUTER = "[computer]"
MIN_TEXT = 3
#: Names Windows gives every machine; they identify nobody.
_GENERIC = frozenset({"workgroup", "nt authority", "localhost", "system", "default"})


def _safe(read):
    try:
        return read()
    except Exception:  # noqa: BLE001 - an unreadable identity is one less to look for
        return None


@functools.lru_cache(maxsize=1)
def identities() -> tuple[frozenset, frozenset]:
    """(account names, computer names) this process can read, lowercased."""
    env = os.environ
    users = {env.get("USERNAME"), env.get("USER"), env.get("LOGNAME"), _safe(getpass.getuser),
             _safe(lambda: Path.home().name)}
    for key in ("USERPROFILE", "HOME"):
        if env.get(key):
            users.add(PureWindowsPath(env[key]).name)
    computers = {env.get("COMPUTERNAME"), _safe(platform.node), env.get("USERDOMAIN")}
    def clean(names):
        return frozenset(name.strip().lower() for name in names
                         if isinstance(name, str) and name.strip() and name.strip().lower() not in _GENERIC)
    users = clean(users)
    return users, clean(computers) - users


@functools.lru_cache(maxsize=1)
def _patterns():
    users, computers = identities()
    rules = []
    for names, mark in ((users, USER), (computers, COMPUTER)):
        long = sorted((name for name in names if len(name) >= MIN_TEXT), key=len, reverse=True)
        short = sorted(name for name in names if len(name) < MIN_TEXT)
        if long:
            # Never inside a marker already written: the pass runs more than once.
            rules.append((re.compile(r"(?<!\[)(?:" + "|".join(map(re.escape, long)) + r")(?!\])", re.I), mark))
        if short:
            rules.append((re.compile(r"(?<=[\\/])(?:" + "|".join(map(re.escape, short)) + r")(?=[\\/]|$)", re.I), mark))
    return tuple(rules)


def reset() -> None:
    """Forget the names read so far (tests, or an environment that changed)."""
    identities.cache_clear()
    _patterns.cache_clear()


def purge(text: str) -> str:
    for pattern, mark in _patterns():
        text = pattern.sub(mark, text)
    return text


def contains_identity(text: str) -> bool:
    return purge(text) != text


def purge_value(value):
    """`purge` over every string in a JSON-shaped value, keys included."""
    if isinstance(value, str):
        return purge(value)
    if isinstance(value, dict):
        return {purge_value(key): purge_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [purge_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(purge_value(item) for item in value)
    return value
