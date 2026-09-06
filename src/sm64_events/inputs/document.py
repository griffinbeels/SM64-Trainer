# src/sm64_events/inputs/document.py
"""The portable form of an input track: a text file a person can also WRITE.

    # sm64-inputs v2
    target:   star WF 1
    strategy: 10 coin
    version:  us
    fps:      30
    origin:   attempt 40213
    --
    0-3       -        +58,+61   walking        -8192   12.5
    4         A        +02,+79   jump           -8192   12.5
    5-60      A        -49,+55   dive           -8000   31.25
    61-64     -        gap

**A template is a document, not a flag on an attempt** (his ruling,
2026-08-20). That is what lets one come from an attempt he marked, a file
another player sent him, or one he typed out by hand, with nothing downstream
caring which — and it is why hand-authoring is a format decision rather than a
sequencer to build.

**It carries Mario, not only the pad** (his ruling, 2026-08-22: *"someone
records a PERFECT INPUT EXAMPLE, with every piece of information about Mario
that we need, as well as their controller data… I need to be able to compare
my gameplay against the exact example, including all mario data"*). Each row
is the pad, then what Mario was doing while it was held: his action as its
decomp word (or a hex id this project has no word for), his yaw in the game's
own units, his speed. A row may stop after the pad -- that is what a v1
document and a hand-authored row both look like -- and then Mario reads as
"not captured", which is the honest value for a body nobody recorded.

Frames are numbered from the start of the TRACK, always from zero, so two
documents lie on one axis without either knowing when it was played.

A captured track writes exact raw stick values. An AUTHORED one may write a
named direction and a magnitude band instead — `UR/full` — because nobody can
meaningfully hand-author `+58,+61`. Both spellings parse to the same thing.

**Import REFUSES rather than guesses.** A document from another frame rate is
a load error naming the reason, not a silent rescale: rescaling would move
every input, which is the one thing this whole feature exists to get right.
"""
import math
import re
import struct
from typing import NamedTuple

from sm64_events.core.timefmt import GAME_FPS as FPS
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.runs import capture_axis, collapse, same_state
from sm64_events.memory import addresses as A

FORMAT = 2
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_FRAMES = 30 * 60 * FPS
MAGIC = f"# sm64-inputs v{FORMAT}"
_MAGICS = {"# sm64-inputs v1": 1, MAGIC: 2}
_FLOAT32 = struct.Struct("<f")
_BY_NAME = {name: bit for bit, name in A.BUTTON_BITS}
_OCTANTS = {"R": 0, "UR": 45, "U": 90, "UL": 135,
            "L": 180, "DL": 225, "D": 270, "DR": 315}
_BANDS = {"light": 20, "half": 40, "full": A.STICK_MAX}
_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")
_REQUIRED = ("target", "version", "fps", "origin")


class DocumentError(ValueError):
    """A document that cannot be loaded, with the reason in the message."""


class Document(NamedTuple):
    target: str
    strategy: str | None
    version: str
    fps: int
    origin: str
    frames: list[tuple[int, InputFrame]]
    author: str | None = None
    frame_count: int = 0
    name: str | None = None


def _stick_word(frame: InputFrame) -> str:
    """`neutral` only for a stick at EXACTLY centre, never for one merely
    inside the dead zone — that would round a real reading away, and the
    document has to survive a round trip byte for byte."""
    if frame.stick_x == 0 and frame.stick_y == 0:
        return "neutral"
    return f"{frame.stick_x:+d},{frame.stick_y:+d}"


def _parse_stick(word: str) -> tuple[int, int]:
    if word == "neutral":
        return 0, 0
    if "," in word:
        x_text, y_text = word.split(",", 1)
        try:
            x, y = int(x_text), int(y_text)
        except ValueError:
            raise DocumentError(f"cannot read the stick value {word!r}") from None
        if not (-128 <= x <= 127 and -128 <= y <= 127):
            raise DocumentError(f"stick value {word!r} is outside the game's s8")
        return x, y
    if "/" not in word:
        raise DocumentError(f"cannot read the stick value {word!r}")
    octant, band = word.split("/", 1)
    if octant not in _OCTANTS or band not in _BANDS:
        raise DocumentError(f"cannot read the stick value {word!r}")
    radians = math.radians(_OCTANTS[octant])
    magnitude = _BANDS[band]
    return (round(math.cos(radians) * magnitude),
            round(math.sin(radians) * magnitude))


def _parse_buttons(word: str) -> int:
    if word == "-":
        return 0
    mask = 0
    for name in word.split("+"):
        if name not in _BY_NAME:
            raise DocumentError(f"unknown button {name!r}")
        mask |= _BY_NAME[name]
    return mask


def _span_word(start: int, end: int) -> str:
    return f"{start}" if start == end else f"{start}-{end}"


def _speed_word(speed: float) -> str:
    """The SHORTEST decimal that reads back as the same float32.

    A captured speed is a float32 out of RAM; Python's repr of it is exact but
    reads as `28.12345695495605`, which nobody can check by eye. Six-ish
    digits almost always round-trip and are tried first; the loop stops at
    the first that does, so the file is exact AND legible.
    """
    for digits in range(1, 10):
        word = f"{speed:.{digits}g}"
        if _FLOAT32.unpack(_FLOAT32.pack(float(word)))[0] == speed:
            return word
    return repr(speed)


def _parse_mario(words: list[str], line: str) -> tuple[int, int, float]:
    action = A.action_from_word(words[0])
    if action is None:
        raise DocumentError(f"unknown action {words[0]!r} in the row {line!r}")
    try:
        yaw = int(words[1])
        # Snapped through float32 on the way in: a speed IS a float32 out of
        # RAM, so a decoded document compares equal to the capture it came
        # from rather than differing in digits no frame ever held.
        speed = _FLOAT32.unpack(_FLOAT32.pack(float(words[2])))[0]
    except (ValueError, OverflowError, struct.error):
        raise DocumentError(f"cannot read Mario's yaw or speed in the row "
                            f"{line!r}") from None
    if not math.isfinite(speed):
        raise DocumentError(f"Mario's speed must be finite in the row {line!r}")
    if not -0x8000 <= yaw <= 0x7FFF:
        raise DocumentError(f"yaw {yaw} is outside the game's s16 in the row "
                            f"{line!r}")
    return action, yaw, speed


def valid_name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 200 or len(name.splitlines()) != 1:
        raise DocumentError("template name must be one line of 1 to 200 characters")
    return name


def with_name(text: str, name: str) -> str:
    """Add the shared name while preserving unknown headers and exact body bytes."""
    name = valid_name(name)
    lines = text.splitlines(keepends=True)
    split = next((i for i, line in enumerate(lines) if line.rstrip() == "--"), None)
    if split is None:
        raise DocumentError("missing the '--' header separator")
    header = [line for line in lines[:split] if line.split(":", 1)[0].strip() != "name"]
    return "".join([*header, f"name: {name}\n", *lines[split:]])


def encode(frames: list[tuple[int, InputFrame]], *, target: str,
           strategy: str | None, version: str, origin: str,
           author: str | None = None, name: str | None = None,
           first_frame: int | None = None, frame_count: int | None = None) -> str:
    lines = [MAGIC,
             f"target:   {target}",
             f"strategy: {strategy if strategy else '-'}",
             f"version:  {version}",
             f"fps:      {FPS}",
             f"origin:   {origin}"]
    if author:
        lines.append(f"author:   {author}")
    if name is not None:
        lines.append(f"name: {valid_name(name)}")
    lines.append("--")
    next_expected = 0
    for run in collapse(capture_axis(frames, first_frame), same_state):
        if run.start > next_expected:                # the hole itself
            lines.append(
                f"{_span_word(next_expected, run.start - 1)} - gap")
        frame = run.frame
        names = "+".join(
            name for bit, name in A.BUTTON_BITS if frame.buttons & bit)
        # Width padding is not a delimiter: Cdown+Cleft overflowed its column
        # and swallowed the stick. One space is unambiguous for every mask.
        lines.append(" ".join((_span_word(run.start, run.end - 1), names or "-",
                               _stick_word(frame), A.action_word(frame.action),
                               str(frame.yaw), _speed_word(frame.speed))))
        next_expected = run.end
    if frame_count is not None and frame_count > next_expected:
        lines.append(f"{_span_word(next_expected, frame_count - 1)} - gap")
    return "\n".join(lines) + "\n"


def decode(text: str) -> Document:
    if len(text.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise DocumentError("document too large (maximum 4 MiB)")
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines or lines[0].strip() not in _MAGICS:
        raise DocumentError(f"missing the {MAGIC!r} header line")
    if "--" not in lines:
        raise DocumentError("missing the '--' header separator")
    split = lines.index("--")
    meta: dict[str, str] = {}
    for line in lines[1:split]:
        if not line.strip():
            continue
        if ":" not in line:
            raise DocumentError(f"cannot read the header line {line!r}")
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    for required in _REQUIRED:
        if required not in meta:
            raise DocumentError(f"the header has no {required!r}")
    if meta["fps"] != str(FPS):
        raise DocumentError(
            f"fps is {meta['fps']}, and a frame in this trainer is one of "
            f"{FPS} per second — rescaling would move every input, so this "
            f"document is refused rather than reinterpreted")
    frames: list[tuple[int, InputFrame]] = []
    next_frame = 0
    for line in lines[split + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) not in (3, 6):
            raise DocumentError(f"cannot read the row {line!r}")
        span, buttons_word, stick_word = parts[:3]
        matched = _RANGE.match(span)
        if matched is None:
            raise DocumentError(f"cannot read the frame span {span!r}")
        start = int(matched.group(1))
        end = int(matched.group(2)) if matched.group(2) else start
        if end < start or start < next_frame:
            raise DocumentError(f"frame spans must be ordered and non-overlapping: {span!r}")
        if end >= MAX_DOCUMENT_FRAMES:
            raise DocumentError("document exceeds the 30 minute frame limit")
        next_frame = end + 1
        if stick_word == "gap":
            if buttons_word != "-" or len(parts) != 3:
                raise DocumentError(f"a gap cannot contain input in the row {line!r}")
            continue
        buttons = _parse_buttons(buttons_word)
        stick_x, stick_y = _parse_stick(stick_word)
        action, yaw, speed = (_parse_mario(parts[3:], line)
                              if len(parts) == 6 else (0, 0, 0.0))
        for number in range(start, end + 1):
            frames.append((number, InputFrame(buttons, 0, stick_x, stick_y,
                                              action, yaw, speed)))
    strategy = meta.get("strategy")
    return Document(target=meta["target"],
                    strategy=None if strategy in (None, "-") else strategy,
                    version=meta["version"], fps=FPS, origin=meta["origin"],
                    frames=frames, author=meta.get("author") or None,
                    frame_count=next_frame,
                    name=valid_name(meta["name"]) if meta.get("name") else None)
