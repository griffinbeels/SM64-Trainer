# src/sm64_events/inputs/document.py
"""The portable form of an input track: a text file a person can also WRITE.

    # sm64-inputs v1
    target:   star WF 1
    strategy: 10 coin
    version:  us
    fps:      30
    origin:   attempt 40213
    --
    0-3       -        +58,+61
    4         A        +02,+79
    5-60      A        -49,+55
    61-64     -        gap

**A template is a document, not a flag on an attempt** (his ruling,
2026-08-20). That is what lets one come from an attempt he marked, a file
another player sent him, or one he typed out by hand, with nothing downstream
caring which — and it is why hand-authoring is a format decision rather than a
sequencer to build.

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
from typing import NamedTuple

from sm64_events.core.timefmt import GAME_FPS as FPS
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.runs import capture_axis, collapse
from sm64_events.memory import addresses as A

MAGIC = "# sm64-inputs v1"
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
            return int(x_text), int(y_text)
        except ValueError:
            raise DocumentError(f"cannot read the stick value {word!r}") from None
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


def _same_row(frame: InputFrame, previous: InputFrame) -> bool:
    """Two frames that WRITE identically. Only the pad is written, so a yaw
    or speed moving under a held stick must not split a row in two."""
    return (frame.buttons == previous.buttons
            and frame.stick_x == previous.stick_x
            and frame.stick_y == previous.stick_y)


def _span_word(start: int, end: int) -> str:
    return f"{start}" if start == end else f"{start}-{end}"


def encode(frames: list[tuple[int, InputFrame]], *, target: str,
           strategy: str | None, version: str, origin: str) -> str:
    lines = [MAGIC,
             f"target:   {target}",
             f"strategy: {strategy if strategy else '-'}",
             f"version:  {version}",
             f"fps:      {FPS}",
             f"origin:   {origin}",
             "--"]
    next_expected = 0
    for run in collapse(capture_axis(frames), _same_row):
        if run.start > next_expected:                # the hole itself
            lines.append(
                f"{_span_word(next_expected, run.start - 1):<10}{'-':<9}gap")
        names = "+".join(
            name for bit, name in A.BUTTON_BITS if run.frame.buttons & bit)
        lines.append(f"{_span_word(run.start, run.end - 1):<10}"
                     f"{names or '-':<9}{_stick_word(run.frame)}")
        next_expected = run.end
    return "\n".join(lines) + "\n"


def decode(text: str) -> Document:
    lines = [line.rstrip() for line in text.splitlines()]
    if not lines or lines[0].strip() != MAGIC:
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
    for line in lines[split + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise DocumentError(f"cannot read the row {line!r}")
        span, buttons_word, stick_word = parts
        matched = _RANGE.match(span)
        if matched is None:
            raise DocumentError(f"cannot read the frame span {span!r}")
        start = int(matched.group(1))
        end = int(matched.group(2)) if matched.group(2) else start
        if stick_word == "gap":
            continue
        buttons = _parse_buttons(buttons_word)
        stick_x, stick_y = _parse_stick(stick_word)
        for number in range(start, end + 1):
            frames.append((number, InputFrame(buttons, 0, stick_x, stick_y)))
    strategy = meta.get("strategy")
    return Document(target=meta["target"],
                    strategy=None if strategy in (None, "-") else strategy,
                    version=meta["version"], fps=FPS, origin=meta["origin"],
                    frames=frames)
