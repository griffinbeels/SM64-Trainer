# tests/test_composition.py
"""Composition-root contracts the runtime depends on (see projection.py
docstring: level_changed must precede anchors; anchors precede grabs)."""
from pathlib import Path

import sm64_events


def test_detector_order_is_load_bearing():
    src = (Path(sm64_events.__file__).parent / "main.py").read_text(encoding="utf-8")
    order = ["GameResetDetector", "LevelChangeDetector", "AnchorDetector",
             "DeathDetector", "StarGrabDetector"]
    # Use rindex so import-line occurrences (alphabetical) are skipped in
    # favour of the last occurrence, which is inside the detectors = [...] list.
    positions = [src.rindex(name) for name in order]
    assert positions == sorted(positions)
