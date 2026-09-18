"""The one line of JSON the trainer sends the overlay child, and how it reads it.

The overlay is a process with no chrome and no console: if a line it cannot
understand ended it, the ghost would vanish mid-attempt with nothing on screen
to say why. So a bad field is DROPPED WITH A REASON and the rest of the line
still applies; only an explicit `quit` ends the child.

The trainer encodes with `encode_control_line` rather than calling json.dumps
itself, so both halves of the pipe have one spelling (domain contract 6).
"""
import json
import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit

#: The overlay loads pages the trainer itself serves. Anything else -- a
#: `javascript:` or `data:` URL above all -- is refused at the pipe, because
#: the child runs with the trainer's own privileges and shows no address bar.
ALLOWED_URL_SCHEMES = ("http", "https", "file")


@dataclass(frozen=True)
class ControlMessage:
    """One line's worth of instructions, already validated.

    Every field is None when the line did not carry it, so "set nothing" and
    "set it to false" stay different things.
    """

    url: str | None = None
    opacity: float | None = None   # already clamped to 0..1
    visible: bool | None = None
    quit: bool = False
    rejected: tuple[str, ...] = field(default=())

    @property
    def is_empty(self) -> bool:
        return (self.url is None and self.opacity is None
                and self.visible is None and not self.quit)


def alpha_byte(opacity: float) -> int:
    """0..1 -> the whole-window alpha Windows takes (0..255).

    Whole-window alpha is GPU-blended and cheap; per-pixel alpha at video
    sizes is not, which is why the overlay's opacity is one number for the
    window rather than a channel in the picture.
    """
    clamped = min(1.0, max(0.0, float(opacity)))
    return int(clamped * 255 + 0.5)


def is_allowed_url(url: object) -> bool:
    if not isinstance(url, str) or not url.strip():
        return False
    return urlsplit(url).scheme in ALLOWED_URL_SCHEMES


def _read_opacity(payload: dict, rejected: list[str]) -> float | None:
    # Presence is "is the key there", not "is the value non-null": an explicit
    # null is a value the trainer never sends, so it is a fault worth naming
    # rather than a silent no-op.
    if "opacity" not in payload:
        return None
    raw = payload["opacity"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw):
        rejected.append(f"opacity must be a number between 0 and 1, got {raw!r}")
        return None
    return min(1.0, max(0.0, float(raw)))


def _read_flag(payload: dict, name: str, rejected: list[str]) -> bool | None:
    if name not in payload:
        return None
    raw = payload[name]
    if not isinstance(raw, bool):
        rejected.append(f"{name} must be true or false, got {raw!r}")
        return None
    return raw


def _read_url(payload: dict, rejected: list[str]) -> str | None:
    if "url" not in payload:
        return None
    raw = payload["url"]
    if not is_allowed_url(raw):
        rejected.append(
            f"url must start with one of {'/'.join(ALLOWED_URL_SCHEMES)}, got {raw!r}")
        return None
    return raw


def parse_control_line(line: str) -> ControlMessage | None:
    """One line -> what to do about it. None means the line said nothing.

    Never raises: the caller is a loop that must outlive whatever arrives.
    """
    text = (line or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return ControlMessage(rejected=("line is not JSON",))
    if not isinstance(payload, dict):
        return ControlMessage(rejected=("line is not a JSON object",))

    rejected: list[str] = []
    return ControlMessage(
        url=_read_url(payload, rejected),
        opacity=_read_opacity(payload, rejected),
        visible=_read_flag(payload, "visible", rejected),
        quit=bool(_read_flag(payload, "quit", rejected)),
        rejected=tuple(rejected),
    )


def encode_control_line(**fields) -> str:
    """The trainer's half of the pipe. Fields left as None are not sent."""
    payload = {name: value for name, value in fields.items() if value is not None}
    return json.dumps(payload) + "\n"


def encode_event(event: str, **fields) -> str:
    """The child's half: one named event per line on stdout."""
    return json.dumps({"event": event, **fields}) + "\n"
