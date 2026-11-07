"""Who may update the installed capture layer by itself, and never backwards.

Project64's Plugin folder is shared by every trainer on the machine: the
installed app (frozen, port 8064) and each source checkout's server. They all
record their installs in one shared overlay (`paths.capture_layer_settings_path`),
so "the installed DLL is the one I installed" was true for all of them at
once: within two seconds of Project64 closing, the frozen app copied its OLDER
bundle over a wrapper he had just installed from a worktree (2026-09-23).

So each install is stamped with its INSTALLER (`installer_identity`: the
frozen app, or one checkout's folder) and the BUILD it put there (the
plugin's link time and embedded build id). A trainer refreshes automatically only
what an installer like it put there -- a frozen app what a frozen app
installed, a checkout only its own install -- and never replaces a DLL with
an older build. A build's embedded id is a digest of its sources and says
nothing about order; the PE header's TimeDateStamp does. A file without one (or
an unreadable one) cannot be ordered, so it is never replaced automatically.

Since 2026-11-06 builds are repeatable (tools/native_identity.py), so that
field is no longer the moment of linking: the build writes the SOURCE DATE
there, the time of the newest commit that touched the native's inputs. Two
builds of one commit carry the same value and a later native commit a larger
one. A build from before carries its real link time, which is usually LATER
than the commit it was built from, so the first source-dated build would read
as older than the one it replaces. `not_older` therefore ranks a build that
names itself in a version resource (every build since) above one that does
not. Older trainers still running compare the dates alone.

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
    """The PE/COFF header's TimeDateStamp: the source date of a build since
    2026-11-06, the moment of linking before. None for a file that is not a
    PE image or cannot be read."""
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


_VERSION_INFO = "VS_VERSION_INFO".encode("utf-16-le")
_OUR_COMPANY = "SM64 Trainer".encode("utf-16-le")


def names_itself(path: Path) -> bool:
    """The file's version resource names SM64 Trainer: a build made since
    2026-11-06. Reads only the headers and the .rsrc section (a few KB), so
    the refresh check stays cheap on the 12 MB plugin."""
    try:
        with path.open("rb") as file:
            head = file.read(4096)
            offset = struct.unpack_from("<I", head, 0x3C)[0]
            if head[:2] != b"MZ" or head[offset:offset + 4] != b"PE\0\0":
                return False
            count, optional = struct.unpack_from("<H", head, offset + 6)[0], struct.unpack_from("<H", head, offset + 20)[0]
            table = offset + 24 + optional
            for index in range(count):
                entry = table + 40 * index
                if head[entry:entry + 8].rstrip(b"\0") == b".rsrc":
                    size, raw = struct.unpack_from("<II", head, entry + 16)
                    file.seek(raw)
                    section = file.read(min(size, 1 << 20))
                    return _VERSION_INFO in section and _OUR_COMPANY in section
    except (OSError, struct.error):
        return False
    return False


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
    """The bundled DLL is at least as new as the installed one: a build that
    names itself beats one that does not, else the PE dates decide. A missing
    installed file is nothing to protect; an unorderable pair is never replaced."""
    if not installed.exists():
        return True
    ours, theirs = link_time(bundle), link_time(installed)
    if ours is None or theirs is None:
        return False
    named = names_itself(bundle)
    if named != names_itself(installed):
        return named
    return ours >= theirs


def build_stamp(plugin: Path, identity) -> dict:
    """What an install put in the Plugin folder, for the overlay."""
    try:
        build = identity(plugin)
    except OSError:
        build = None
    return {"plugin": {"linked": link_time(plugin), "build_id": build.decode() if build else None}}
