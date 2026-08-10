"""Usamune timer display format: M'SS"CC (30 fps frames -> centiseconds)."""

# SM64 game LOGIC runs at 30 fps — the unit of IGT, frame-perfect tricks,
# and "one frame" to a practicer. Distinct from the 60 fps the emulator
# PRESENTS (and replay captures): each game frame spans two encoded video
# frames. The replay frame-stepper steps in THESE frames.
GAME_FPS = 30


def frame_at_or_after(centiseconds: int) -> int:
    """The first game frame whose display reaches `centiseconds`.

    Everything else here is a round trip through this: the timer is a frame
    counter and centiseconds are only how it prints, so the frame is the real
    unit and the honest place to do arithmetic."""
    if centiseconds <= 0:
        return 0
    whole, cents = divmod(int(centiseconds), 100)
    return whole * GAME_FPS + -(-cents * GAME_FPS // 100)


def cs_of_frame(frames: int) -> int:
    """The centiseconds a frame count displays as — `format_igt`'s own rule,
    without the punctuation."""
    frames = max(0, int(frames))
    return (frames // GAME_FPS) * 100 + (frames % GAME_FPS) * 100 // GAME_FPS


def attainable_cs(centiseconds: int) -> int:
    """The smallest displayable time at or after `centiseconds`.

    Only 30 of every 100 centisecond values can ever appear on the timer,
    because the display is a frame count converted by the rule in
    `format_igt`: 0, 3, 6, 10, 13, 16, 20, 23, 26, 30 … Ask for 15.01 and
    nobody can ever hit it; the honest answer is 15.03.

    Rounds UP, never down. A cutoff is a threshold you must beat, so rounding
    down would silently make a rank harder than the number it was derived
    from, and rounding up biases every derived ladder gentle.

    Measured 2026-08-05: 100% of the community's vetted Daily Star cutoffs
    land on this set (2,508 of 2,509 -- the exception is WF's OG Master at
    8.85, which is a typo upstream), and 97.1% of 44,701 hand-typed sheet
    entries do. The trainer's own derived ladders did not, which is what this
    exists to fix."""
    return cs_of_frame(frame_at_or_after(centiseconds))


def next_attainable_cs(centiseconds: int) -> int:
    """The smallest displayable time strictly after `centiseconds`.

    What a ladder needs to separate two tiers: `attainable_cs(x + 1)` returns
    `x` again whenever x is already displayable."""
    return cs_of_frame(frame_at_or_after(int(centiseconds) + 1))


def prev_attainable_cs(centiseconds: int) -> int:
    """The largest displayable time strictly before `centiseconds`.

    What a ladder needs to land a cutoff just short of an observed cluster:
    "everyone at 13.00 and faster earns this, nobody at 13.03 does"."""
    return cs_of_frame(max(0, frame_at_or_after(centiseconds) - 1))


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
