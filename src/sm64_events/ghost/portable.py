"""One bounded .sm64ghosts ZIP: metadata and original native clip bytes.

No absolute paths, executable assets, ROM or replay video travel in the file.
Validation happens in staging before the collection index becomes visible.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
import shutil
import zipfile

from sm64_events.ghost.clip import HEADER_BYTES, MAX_RECORDS, RECORD_BYTES, blink_frames, read_clip
from sm64_events.ghost.native_layout import SEGMENT_CLOCK, STAR_CLOCK
from sm64_events.ghost.sharing_targets import canonical, checked_target
from sm64_events.memory.addresses import CASTLE_LEVELS, course_for_level

FORMAT, VERSION = "sm64-ghost-collection", 1
MAX_ENTRIES = 4096
MAX_PACKAGE_BYTES = 2 << 30
MAX_UNPACKED_BYTES = 4 << 30
MAX_MANIFEST_BYTES = 8 << 20
MAX_CLIP_BYTES = HEADER_BYTES + MAX_RECORDS * RECORD_BYTES
IDENTITY_FIELDS = ("sha256", "author", "target", "frames", "clock", "recorded_utc", "practice", "outcome")


def checked_hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("This collection has an invalid ghost identity.")
    return value


def checked_author(value):
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError("Enter the name to credit on your exported ghosts (up to 128 UTF-8 bytes).")
    return value.strip()


def identity(row):
    return hashlib.sha256(canonical({key: row[key] for key in IDENTITY_FIELDS})).hexdigest()


def checked_entry(row):
    if not isinstance(row, dict) or set(row) != {*IDENTITY_FIELDS, "id", "file"}:
        raise ValueError("This collection has invalid ghost metadata.")
    checked_hash(row["id"]); checked_hash(row["sha256"])
    if checked_author(row["author"]) != row["author"]:
        raise ValueError("This collection has an invalid creator name.")
    checked_target(row["target"])
    if (type(row["frames"]) is not int or not 0 <= row["frames"] <= 30 * 60 * 60 * 24
            or row["clock"] not in ("igt", "rta") or type(row["practice"]) is not bool
            or row["outcome"] not in ("success", "reset", "hard_reset", "abandoned", "death")
            or not isinstance(row["recorded_utc"], str) or len(row["recorded_utc"]) > 64):
        raise ValueError("This collection has invalid timing or recording metadata.")
    if row["file"] != f"clips/{row['sha256']}.ghost" or row["id"] != identity(row):
        raise ValueError("This collection's metadata identity does not match.")
    try:
        instant = datetime.fromisoformat(row["recorded_utc"].replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError
    except ValueError as error:
        raise ValueError("This collection has an invalid recording date.") from error
    return dict(row)


def make_entry(path, *, author, target, frames, clock, recorded_utc, practice, outcome):
    with Path(path).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    row = dict(sha256=digest, author=checked_author(author), target=checked_target(target), frames=frames,
               clock=clock, recorded_utc=recorded_utc, practice=practice, outcome=outcome)
    return checked_entry({**row, "id": identity(row), "file": f"clips/{digest}.ghost"})


def write_package(destination, entries, step=lambda *_: None):
    """entries are (checked metadata, trusted local path), never remote paths."""
    if not 1 <= len(entries) <= MAX_ENTRIES:
        raise ValueError(f"Choose between 1 and {MAX_ENTRIES} ghosts to export.")
    metadata = canonical({"format": FORMAT, "version": VERSION,
                          "ghosts": [checked_entry(row) for row, _ in entries]})
    assets = {}
    for row, path in entries:
        assets.setdefault(row["file"], path)
    total = len(metadata) + sum(path.stat().st_size for path in assets.values())
    if len(metadata) > MAX_MANIFEST_BYTES:
        raise ValueError("This collection has too much metadata; export it in smaller groups.")
    if total > MAX_UNPACKED_BYTES:
        raise ValueError("This collection is too large; export it in smaller groups.")
    written = set()
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        bundle.writestr("collection.json", metadata)
        for index, (row, path) in enumerate(entries):
            if row["file"] not in written:
                bundle.write(path, row["file"])
                written.add(row["file"])
            step((index + 1) / len(entries), f"Packed {index + 1} of {len(entries)} ghosts.")
    if Path(destination).stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("This collection is too large; export it in smaller groups.")


def _checked_recording_target(target, clock_kind, level):
    if target["kind"] == "segment":
        clocks = (SEGMENT_CLOCK,)
    else:
        course, star = map(int, target["entity_key"].split("-"))
        # The 100-coin exit engine reattributes its attempt to the star, but
        # preserves the segment's exact elapsed clock through the exit grab.
        clocks = ((STAR_CLOCK, SEGMENT_CLOCK) if 1 <= course <= 15 and star == 6 else
                  (STAR_CLOCK,))
    if clock_kind not in clocks:
        raise ValueError("This ghost's recorded clock belongs to a different target kind.")
    if target["kind"] == "star":
        same_scene = (level in CASTLE_LEVELS if course == 0 else course_for_level(level) == course)
        if not same_scene:
            raise ValueError("This ghost's recorded course belongs to a different star target.")


def validate_package(path, staging, step=lambda *_: None):
    if Path(path).stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("This ghost collection exceeds the file size limit.")
    try:
        with zipfile.ZipFile(path) as bundle:
            files = bundle.infolist()
            names = [item.filename for item in files]
            if len(names) != len(set(names)) or len(files) > MAX_ENTRIES + 1:
                raise ValueError("This collection has duplicate files or too many ghosts.")
            meta = bundle.getinfo("collection.json")
            if meta.file_size > MAX_MANIFEST_BYTES or sum(item.file_size for item in files) > MAX_UNPACKED_BYTES:
                raise ValueError("This ghost collection expands beyond the size limit.")
            document = json.loads(bundle.read(meta))
            if (not isinstance(document, dict) or set(document) != {"format", "version", "ghosts"}
                    or document["format"] != FORMAT or document["version"] != VERSION
                    or not isinstance(document["ghosts"], list) or not 1 <= len(document["ghosts"]) <= MAX_ENTRIES):
                raise ValueError("Choose an SM64 ghost collection (.sm64ghosts).")
            rows = [checked_entry(row) for row in document["ghosts"]]
            if len({row["id"] for row in rows}) != len(rows):
                raise ValueError("This collection repeats a ghost.")
            if set(names) != {"collection.json", *(row["file"] for row in rows)}:
                raise ValueError("This collection contains unexpected files.")
            verified = {}
            for index, row in enumerate(rows):
                digest = row["sha256"]
                if digest not in verified:
                    info = bundle.getinfo(row["file"])
                    if not HEADER_BYTES <= info.file_size <= MAX_CLIP_BYTES or info.flag_bits & 1:
                        raise ValueError("This collection has an invalid or encrypted recording.")
                    destination = Path(staging) / f"{digest}.ghost"
                    with bundle.open(info) as source, destination.open("wb") as output:
                        shutil.copyfileobj(source, output, 1 << 20)
                    clip = read_clip(destination, digest)
                    verified[digest] = (clip.clip_id, clip.clock_kind, blink_frames(clip),
                                        sum(frame.pad is not None for frame in clip.frames), clip.level)
                _clip_id, clock_kind, _blinks, _pad_records, level = verified[digest]
                # The raw clip owns its clock and initial scene; metadata cannot relabel them.
                _checked_recording_target(row["target"], clock_kind, level)
                step((index + 1) / len(rows), f"Checked {index + 1} of {len(rows)} ghosts.")
            return rows, verified
    except (zipfile.BadZipFile, KeyError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("This file is not a readable SM64 ghost collection.") from error
