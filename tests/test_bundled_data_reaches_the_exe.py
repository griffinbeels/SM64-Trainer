"""Every seed the app reads when frozen must be in the PyInstaller spec.

A `bundled_*()` helper returns None when its file is absent from `_MEIPASS`,
and every caller treats that as "no data" rather than as an error -- so a seed
missing from the build works perfectly from source and silently disappears in
the released exe. That is the whole failure mode: nothing goes red, the feature
just has nothing to show.

Caught for real on 2026-08-05, when sheet_ladders.seed.json was added and the
spec still listed only two seeds."""
import re
from pathlib import Path

from sm64_events.core import paths

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / "build_exe.py"


def _bundled_helpers():
    """Every core/paths.py helper that resolves a file out of _MEIPASS."""
    source = Path(paths.__file__).read_text(encoding="utf-8")
    return sorted(set(re.findall(
        r'_MEIPASS[^)]*\)\s*\)\s*/\s*"([^"]+)"', source)))


def test_every_frozen_seed_is_named_in_the_build_spec():
    spec = BUILD.read_text(encoding="utf-8")
    missing = [name for name in _bundled_helpers() if name not in spec]
    assert missing == [], (
        f"these are read from _MEIPASS when frozen but never added to the "
        f"exe, so they vanish silently in a release: {missing}")


def test_the_scan_actually_finds_the_seeds():
    """Without this the test above passes for the wrong reason the moment the
    regex stops matching."""
    found = _bundled_helpers()
    assert "rank_standards.seed.json" in found
    assert "defaults.seed.json" in found
    assert "sheet_ladders.seed.json" in found


def test_each_named_seed_exists_in_the_repo():
    # Only the JSON seeds live under src/sm64_events/data; the same _MEIPASS
    # idiom also resolves a bundled ffmpeg.exe, which comes from elsewhere.
    for name in _bundled_helpers():
        if not name.endswith(".seed.json"):
            continue
        assert (REPO / "src" / "sm64_events" / "data" / name).exists(), name
