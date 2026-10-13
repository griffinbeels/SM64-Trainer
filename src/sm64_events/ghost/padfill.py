"""The attempt's own input track, written into a playback copy of its native clip.

His ask (task 0148): "we leverage the input timeline data to be able to FRAME
PERFECTLY display the ghost's inputs above the ghost as they're being input."
Exact pad capture (`Frame.pad`) began 2026-10-02 16:09Z, so every older clip,
his PBs included, showed INPUTS UNKNOWN. The trainer also polled each attempt's
controller (inputs/track.py), filed under the game frame number the clip's
records carry (gGlobalTimer).

CALIBRATED 2026-10-02 on his live session: 45,659 archived records with a
captured pad, joined to the input store by global_timer within the same few
seconds. The polled pad at frame t equalled the captured pad on every one,
including all 405 frames where the pad changed; t-1 and t+1 disagreed on
0.9%. Mario's action agreed on 51,608 of 51,608 at t.

MARIO'S ACTION IS THE WITNESS that a track and a clip number the same frames,
since a clip without pads has nothing else to compare. Over his 167 stored
clips that day it missed at most 2 frames at t (a poll that read the action a
moment before Mario's update; 7 clips, all GCLIP4/6) and at least 34 at t-1 or
t+1 wherever it missed at t. So the join must beat both neighbouring offsets
by ALIGNMENT_MARGIN, not match on every frame: a strict rule refused six of
his correctly aligned PBs over one stale read each.

The stored clip is never changed: it stays captured evidence, and this copy is
what the renderer plays and a collection exports. A captured pad is never
replaced. A record is filled only from the track frame with its own
global_timer, and only when that timer occurs once in the clip. The whole fill
is refused when the track is not one ascending counter stretch, when a
captured pad disagrees with the track at its frame, or when Mario's action
does not prove the alignment (a clip where he never changes action proves
nothing). A frame the track does not hold stays unknown, never neutral.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
from typing import NamedTuple

from sm64_events.ghost.clip import RECORD_BYTES, SAMPLE_VERSION, VERSION, Clip, encode_clip, inputs_complete, read_clip
from sm64_events.ghost.native_layout import PAD_AT, RECORD_PREFIX_BYTES, VALID_ACTION, VALID_PAD
from sm64_events.inputs.frame import valid_raw_stick
from sm64_events.memory.addresses import BUTTON_VALID_MASK

log = logging.getLogger("sm64.ghost")

# gc_sample opens with bytes, version and epoch; valid_fields follows them.
VALID_FIELDS_AT = RECORD_PREFIX_BYTES + 12
_PAD = struct.Struct("<hhHH")
# Mario's action must disagree with the track at t-1 and at t+1 at least this
# many times as often as at t (his clips: 1-2 misses at t, 34+ beside it).
ALIGNMENT_MARGIN = 4


class PadFillRefused(ValueError):
    """The track cannot be proven to hold this clip's frames."""


class PadFill(NamedTuple):
    data: bytes
    filled: int    # records whose pad came from the track
    captured: int  # records whose pad the capture recorded


def fill_pads(clip: Clip, track, producer) -> PadFill | None:
    """The clip with the track's pad on each record that has none.

    `track` is `[(frame, InputFrame)]` over the clip's frames; `producer` the
    manifest's source. None when no record can be filled.
    """
    polled = {}
    previous = None
    for number, frame in track:
        if previous is not None and number <= previous:
            raise PadFillRefused("the input track spans more than one frame counter stretch")
        polled[number] = frame
        previous = number
    occurrences = Counter(record.global_timer for record in clip.frames)
    records, filled = [], 0
    for record in clip.frames:
        sample = polled.get(record.global_timer)
        raw = record.raw
        if sample is not None:
            pad = (sample.stick_x, sample.stick_y, sample.buttons)
            if record.pad is not None and record.pad != pad:
                raise PadFillRefused(f"captured pad disagrees with the input track at frame {record.global_timer}")
            if (record.pad is None and occurrences[record.global_timer] == 1
                    and valid_raw_stick(sample.stick_x, sample.stick_y) and not sample.buttons & ~BUTTON_VALID_MASK):
                raw = bytearray(raw)
                struct.pack_into("<I", raw, VALID_FIELDS_AT, record.valid_fields | VALID_PAD)
                _PAD.pack_into(raw, PAD_AT, *pad, 0)
                raw = bytes(raw)
                filled += 1
        records.append(raw)
    if not filled:
        return None
    early, exact, late = (_action_misses(clip, polled, shift) for shift in (-1, 0, 1))
    if not ALIGNMENT_MARGIN * exact < min(early, late):
        raise PadFillRefused(f"Mario's action does not line the input track up with this clip "
                             f"(misses {early} a frame early, {exact} exact, {late} a frame late)")
    data = encode_clip(records, producer, clock_kind=clip.clock_kind, clip_id=clip.clip_id,
                       dance_tail=clip.dance_tail)
    return PadFill(data, filled, sum(record.pad is not None for record in clip.frames))


def _action_misses(clip: Clip, polled: dict, shift: int) -> int:
    """Records whose action differs from the track's action `shift` frames on."""
    misses = 0
    for record in clip.frames:
        sample = polled.get(record.global_timer + shift)
        if sample is not None and sample.action and record.valid_fields & VALID_ACTION:
            misses += sample.action != record.live_action
    return misses


def with_track_pads(found, attempt, track_of, directory: Path):
    """The (path, manifest) the renderer plays for a validated native clip.

    `found` is `ReplayService.find_native`'s answer; `track_of(attempt, span)`
    the attempt's input track over the raw game frames `span` covers. Returns
    `found` itself when every pad was captured, nothing can be filled, or the
    join is refused. The copy keeps the clip's identity and gets its own hash.
    """
    path, manifest = found
    # The manifest states whether every record carries a pad: his rule makes
    # that the one fact every playback door asks (ghost/clip.py::INCOMPLETE_INPUTS).
    unknown = (path, {**manifest, "inputs_complete": False})
    try:
        clip = read_clip(path, manifest["sha256"])
        if inputs_complete(clip):
            return path, {**manifest, "inputs_complete": True}
        span = (clip.frames[0].global_timer, clip.frames[-1].global_timer)
        fill = fill_pads(clip, track_of(attempt, span), SimpleNamespace(**manifest["source"]))
    except (OSError, ValueError, LookupError, TypeError) as error:  # PadFillRefused included
        log.info("Ghost inputs for attempt %s stay unknown: %s", attempt.id, error)
        return unknown
    if fill is None:
        return unknown
    destination = Path(directory) / f"attempt_{int(attempt.id)}.ghost"
    digest = hashlib.sha256(fill.data).hexdigest()
    played = {**manifest, "file": destination.name, "sha256": digest, "bytes": len(fill.data),
              "version": VERSION, "record_bytes": RECORD_BYTES, "sample_version": SAMPLE_VERSION,
              "pad_records": fill.captured + fill.filled, "track_pad_records": fill.filled,
              "source_sha256": manifest["sha256"],
              "inputs_complete": fill.captured + fill.filled == len(clip.frames)}
    try:
        _replace(destination, fill.data)
        _replace(destination.with_suffix(".json"),
                 json.dumps(played, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    except OSError as error:
        log.info("Ghost inputs for attempt %s stay unknown: %s", attempt.id, error)
        return unknown
    return destination, played


def _replace(path: Path, data: bytes) -> None:
    """Atomically, and not at all when the bytes are already there: an
    unchanged file keeps the fingerprint the ghost picker caches on."""
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
