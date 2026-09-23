"""Who may update the installed capture layer by itself, and never backwards.

Project64's Plugin folder is shared by every trainer on the machine: the
installed app (frozen, port 8064) and each source checkout's server. They all
record their installs in one shared overlay (`paths.capture_layer_settings_path`),
so "the installed DLL is the one I installed" was true for all of them at
once: within two seconds of Project64 closing, the frozen app copied its OLDER
bundle over a wrapper he had just installed from a worktree (2026-09-23).

So each install is stamped with its INSTALLER (`installer_identity`: the
frozen app, or one checkout's folder) and the BUILD it put there (each DLL's
link time and embedded build id). A trainer refreshes automatically only
what an installer like it put there -- a frozen app what a frozen app
installed, a checkout only its own install -- and never replaces a DLL with
an older build. A build's embedded id is a digest of its sources and says
nothing about order; the PE header's link time does. A file without one (or
an unreadable one) cannot be ordered, so it is never replaced automatically.

The legacy `installed_sha256` field is what OLDER trainers' refresh trusts;
it is written only for a frozen installer, so an older installed app still
running today leaves a checkout's install alone as well.
"""
from __future__ import annotations

import struct
from pathlib import Path

FROZEN = "frozen"
CHECKOUT = "checkout"


def link_time(path: Path) -> int | None:
    """The PE/COFF header's TimeDateStamp: when the linker wrote this DLL.
    None for a file that is not a PE image or cannot be read."""
    try:
        with path.open("rb") as file:
            head = file.read(4096)
    except OSError:
        return None
    if len(head) < 0x40 or head[:2] != b"MZ":
        return None
    offset = struct.unpack_from("<I", head, 0x3C)[0]
    if offset + 12 > len(head) or head[offset:offset + 4] != b"PE\0\0":
        return None
    return struct.unpack_from("<I", head, offset + 8)[0]


def owns(installed_by: dict | None, installer: dict | None) -> bool:
    """May `installer` refresh what `installed_by` installed? A frozen app
    refreshes any frozen app's install (its own releases); a checkout only
    its own. An unknown installer on either side owns nothing."""
    if not isinstance(installed_by, dict) or not isinstance(installer, dict):
        return False
    kind = installer.get("kind")
    if kind == FROZEN:
        return installed_by.get("kind") == FROZEN
    if kind == CHECKOUT:
        return installed_by.get("kind") == CHECKOUT and bool(installer.get("path")) \
            and installed_by.get("path") == installer.get("path")
    return False


def not_older(bundle: Path, installed: Path) -> bool:
    """The bundled DLL is at least as new as the installed one (link times).
    A missing installed file is nothing to protect; an unorderable pair is
    never replaced."""
    if not installed.exists():
        return True
    ours, theirs = link_time(bundle), link_time(installed)
    return ours is not None and theirs is not None and ours >= theirs


def build_stamp(wrapper: Path, renderer: Path, identity) -> dict:
    """What an install put in the Plugin folder, for the overlay."""
    def one(path: Path) -> dict:
        try:
            build = identity(path)
        except OSError:
            build = None
        return {"linked": link_time(path), "build_id": build.decode() if build else None}
    return {"wrapper": one(wrapper), "renderer": one(renderer)}
