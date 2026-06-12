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
