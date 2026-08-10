"""Usamune timer display format: M'SS"CC (30 fps frames -> centiseconds)."""

# SM64 game LOGIC runs at 30 fps — the unit of IGT, frame-perfect tricks,
# and "one frame" to a practicer. Distinct from the 60 fps the emulator
# PRESENTS (and replay captures): each game frame spans two encoded video
# frames. The replay frame-stepper steps in THESE frames.
GAME_FPS = 30


def format_igt(frames: int) -> str:
    """Format game frames as Usamune IGT display: M'SS"CC.

    Args:
        frames: Game frames at 30 fps.

    Returns:
        Formatted string like "1'02\"16" (1 minute 2 seconds 16 centiseconds).
    """
    mins = frames // 1800
    secs = (frames % 1800) // 30
    cents = (frames % 30) * 100 // 30
    return f"{mins}'{secs:02d}\"{cents:02d}"


def parse_igt(text: str) -> int:
    """A reading off Usamune's own screen back into game frames — the EXACT
    inverse of `format_igt`, and the canonical one (the format has one owner,
    so its inverse lives beside it rather than growing per tool).

    Exact is a property of the format rather than luck: centiseconds are
    `frames % 30 * 100 // 30`, injective over 0..29, so every displayable
    string maps back to exactly one frame and nothing is guessed. A
    centisecond the game never displays (there is no frame reading "01") is
    REFUSED rather than rounded — silently attributing a mistyped reading to
    the nearest frame is how a screen measurement scores an offset that was
    never on the screen. `tools/hunt_value.py::parse_frames` deliberately
    stays separate: it is a lenient HUMAN-INPUT parser for memory hunting
    (accepts `f123`, plain seconds, and rounds), which is a different job
    from inverting the display.

    Raises ValueError on anything that is not a displayable Usamune reading.
    """
    cleaned = text.strip().replace("”", '"').replace("’", "'")
    try:
        minutes, rest = cleaned.split("'", 1)
        seconds, cents = rest.split('"', 1)
        frames_of_second = _frames_for_cents(int(cents))
        return int(minutes) * 1800 + int(seconds) * 30 + frames_of_second
    except (ValueError, IndexError) as exc:
        raise ValueError(
            f"{text!r} is not a Usamune reading — it looks like 1'06\"83"
        ) from exc


def _frames_for_cents(cents: int) -> int:
    for frame in range(30):
        if frame * 100 // 30 == cents:
            return frame
    raise ValueError(f"{cents:02d} is not a centisecond value the game displays")
