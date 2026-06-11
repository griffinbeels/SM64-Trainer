"""Usamune timer display format: M'SS"CC (30 fps frames -> centiseconds)."""


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
