"""GLideN64.ini, the renderer's own settings: frame buffer emulation stays off.

The trainer's renderer is LINK's GLideN64 (`renderer/build-inputs.json`). It
reads ``GLideN64.ini`` beside itself in Project64's Plugin folder through Qt's
QSettings in IniFormat. The release zips ship no ini, and GLideN64's compiled
defaults (`Config.cpp::resetToDefaults`) turn frame buffer emulation ON. LINK's
own package ini equals those defaults except ``frameBufferEmulation\\enable=0``.
With it on, ghosts do not draw and the game lags. Griffin, 2026-10-30: "We
should just ship whatever the default settings are, and MAKE SURE THAT FRAME
BUFFER EMULATION IS DISABLED." So the trainer changes that one key and leaves
every other setting to the file, or to the compiled defaults.

How the pinned GLideN64 reads the file (GLideNUI `Settings.cpp::loadSettings`):

- A root ``version`` below 23, or none, is the old flat format. GLideN64
  loads the root keys, then rewrites them as the ``User`` profile.
- Otherwise the root ``profile`` (default ``User``) names the section it
  loads, matched exactly. A key missing there falls back to the compiled
  default. A missing section makes GLideN64 rewrite the file from its
  defaults, which turns frame buffer emulation back on. A section whose own
  ``version`` is not 26 is rewritten from the values it loaded, so the key
  survives that rewrite.
- Root keys sit before the first section header or under ``[General]``. Qt on
  Windows reads key names without regard to case, writes ``\\`` between a
  key's groups and percent-escapes other characters in names.

Edits work on raw lines. Every other byte, line ending and ``@Variant`` value
stays as it was; a configparser round trip would rewrite all of them.
`core/capturelayer.py` decides which files and when; this module only reads
and edits one file.
"""
from __future__ import annotations

from contextlib import suppress
import os
from pathlib import Path
import re
from typing import NamedTuple

RENDERER_INI = "GLideN64.ini"
#: CONFIG_VERSION_CURRENT in the pinned `Config.h`
CONFIG_VERSION = 26
#: CONFIG_WITH_PROFILES: a root version below this is the old flat format
PROFILES_SINCE = 23
DEFAULT_PROFILE = "User"
_KEY = "framebufferemulation/enable"   # how Qt names frameBufferEmulation\enable
_KEY_TEXT = b"frameBufferEmulation\\enable"
_OFF = b"0"

#: What the trainer writes where no file exists, in Qt's own layout: the
#: profile header GLideN64 expects and the one key. Every other setting falls
#: to GLideN64's compiled defaults, which are LINK's defaults.
MINIMAL = (b"[General]\r\nversion=%d\r\ntranslation=\r\nprofile=User\r\n\r\n"
           b"[User]\r\nversion=%d\r\n%s=0\r\n") % (CONFIG_VERSION, CONFIG_VERSION, _KEY_TEXT)

_ESCAPE = re.compile(r"\\|%U([0-9A-Fa-f]{4})|%([0-9A-Fa-f]{2})")
_INTEGER = re.compile(r"[+-]?[0-9]+")


class _Line(NamedTuple):
    raw: bytes            # the line with its own ending, byte for byte
    section: str | None   # unescaped section name as written; None is the root
    header: bool          # a [section] line
    key: str | None       # unescaped, lower-case key of a key=value line
    value: bytes          # the raw text after "="


def _name(raw: bytes) -> str:
    """Qt's iniUnescapedKey: "\\" separates groups, %XX and %UXXXX escape."""
    def plain(match):
        code = match.group(1) or match.group(2)
        return "/" if code is None else chr(int(code, 16))
    return _ESCAPE.sub(plain, raw.decode("latin-1"))


def _escaped(name: str) -> bytes:
    """Qt's iniEscapedKey, for a section header the trainer adds."""
    out = []
    for ch in name:
        if ch == "/":
            out.append("\\")
        elif ch.isascii() and (ch.isalnum() or ch in "_-."):
            out.append(ch)
        else:
            out.append(f"%{ord(ch):02X}" if ord(ch) <= 0xFF else f"%U{ord(ch):04X}")
    return "".join(out).encode("ascii")


def _section(raw: bytes) -> str | None:
    if raw.lower() == b"general":
        return None
    return raw[1:].decode("latin-1") if raw.lower() == b"%general" else _name(raw)


def _lines(data: bytes) -> list[_Line]:
    lines, section = [], None
    for index, raw in enumerate(data.splitlines(keepends=True)):
        text = raw.rstrip(b"\r\n")
        if index == 0 and text.startswith(b"\xef\xbb\xbf"):
            text = text[3:]
        text = text.lstrip()
        if text.startswith(b"["):
            end = text.find(b"]")
            section = _section(text[1:end if end >= 0 else None].strip())
            lines.append(_Line(raw, section, True, None, b""))
        elif b"=" in text and not text.startswith(b";"):
            key, _, value = text.partition(b"=")
            lines.append(_Line(raw, section, False, _name(key.strip()).lower(), value))
        else:
            lines.append(_Line(raw, section, False, None, b""))
    return lines


def _text(value: bytes) -> str:
    """A plain string value: spaces trimmed, a quoted run unquoted, ";" ends it."""
    value = value.strip()
    if value.startswith(b'"'):
        end = value.find(b'"', 1)
        return value[1:end if end > 0 else None].decode("latin-1")
    return value.split(b";", 1)[0].strip().decode("latin-1")


def _integer(text: str) -> int | None:
    return int(text) if _INTEGER.fullmatch(text) else None


def _active(lines: list[_Line]) -> str | None:
    """The section GLideN64 loads, as written; None is the root (flat format)."""
    root = {line.key: line.value for line in lines if line.section is None and line.key}
    if (_integer(_text(root.get("version", b""))) or 0) < PROFILES_SINCE:
        return None
    return _text(root["profile"]) if "profile" in root else DEFAULT_PROFILE


def _effective(lines: list[_Line]) -> bytes | None:
    """The raw value GLideN64 loads for the key, or None when it uses its default."""
    active = _active(lines)
    values = [line.value for line in lines if line.section == active and line.key == _KEY]
    return values[-1] if values else None


def frame_buffer_emulation_off(data: bytes | None) -> bool:
    """GLideN64 would load frame buffer emulation as off from this file.
    No file, no key or a missing profile section all mean the default: on."""
    if data is None:
        return False
    value = _effective(_lines(data))
    return value is not None and _integer(_text(value)) == 0


def previous_value(data: bytes | None) -> str | None:
    """The value text the file holds now, for undo; None when it has none."""
    value = None if data is None else _effective(_lines(data))
    return None if value is None else _text(value)


def _line_ending(data: bytes) -> bytes:
    match = re.search(rb"\r\n|\r|\n", data)
    return match.group(0) if match else b"\r\n"


def _with_value(data: bytes, value: bytes | None) -> bytes:
    """Set the key wherever GLideN64 reads it, or add it once to the section
    it loads (adding that section when missing); None removes the key."""
    lines = _lines(data)
    active = _active(lines)
    raws = [line.raw for line in lines]
    hits = [i for i, line in enumerate(lines) if line.section == active and line.key == _KEY]
    for i in reversed(hits):
        body = raws[i].rstrip(b"\r\n")
        if value is None:
            del raws[i]
        else:
            raws[i] = body[:body.index(b"=") + 1] + value + raws[i][len(body):]
    if hits or value is None:
        return b"".join(raws)
    eol = _line_ending(data)
    added = [_KEY_TEXT + b"=" + value + eol]
    own = [i for i, line in enumerate(lines) if line.section == active and (line.header or line.key)]
    if own:
        at = own[-1]
    else:
        if raws and not raws[-1].endswith((b"\r", b"\n")):
            raws[-1] += eol
        if raws and raws[-1].strip():
            raws.append(eol)
        header = b"General" if active is None else _escaped(active)
        version = [] if active is None else [b"version=%d" % CONFIG_VERSION + eol]
        raws.append(b"[" + header + b"]" + eol)
        raws.extend(version)
        at = len(raws) - 1
    if not raws[at].endswith((b"\r", b"\n")):
        raws[at] += eol
    raws[at + 1:at + 1] = added
    return b"".join(raws)


def disable(data: bytes | None) -> bytes:
    """This file with frame buffer emulation off, every other byte as it was;
    `data` itself when it already is. No file, or an empty one, is `MINIMAL`."""
    if data is None or not data.strip():
        return MINIMAL
    if frame_buffer_emulation_off(data):
        return data
    return _with_value(data, _OFF)


def read(path: Path) -> bytes | None:
    """The file's bytes, or None when there is no file. Other failures raise:
    an unreadable file must never be mistaken for a missing one."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def write_atomic(path: Path, data: bytes) -> None:
    """Replace the file in one step, so a reader never sees half of it."""
    scratch = path.with_name(path.name + ".sm64-trainer.tmp")
    try:
        with scratch.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(scratch, path)
    except BaseException:
        with suppress(OSError):
            scratch.unlink(missing_ok=True)
        raise


def _verified(path: Path, data: bytes) -> None:
    if read(path) != data:
        raise OSError(f"{path.name} verification failed")


def turn_off(path: Path) -> bool:
    """Write frame buffer emulation off into this file; True when it changed."""
    before = read(path)
    after = disable(before)
    if after == before:
        return False
    write_atomic(path, after)
    _verified(path, after)
    return True


def turn_off_in_place(path: Path, before: bytes | None) -> bool:
    """The same edit for a file the caller holds open against replacement (the
    UAC helper's pin), so it cannot be swapped by a rename: written into the
    file itself. `before` is what the file held, or None when it was missing."""
    after = disable(before)
    if after == before:
        return False
    path.write_bytes(after)
    _verified(path, after)
    return True


def restore(path: Path, record) -> bool:
    """Undo `turn_off` from its record, `{created, previous}`; True when the
    file changed. A file the trainer created and nothing changed since goes
    away. A value someone turned back on since is their choice and stays."""
    if not isinstance(record, dict) or not isinstance(record.get("previous"), str | None):
        return False
    data = read(path)
    if data is None or not frame_buffer_emulation_off(data):
        return False
    if record.get("created") is True and data == MINIMAL:
        path.unlink()
        return True
    previous = record["previous"]
    after = _with_value(data, None if previous is None else previous.encode("latin-1", "replace"))
    write_atomic(path, after)
    _verified(path, after)
    return True
