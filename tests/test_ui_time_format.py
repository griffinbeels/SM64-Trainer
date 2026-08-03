"""One time notation on screen: M'SS"CC, minutes dropped under a minute.

User, 2026-08-03, pointing at the rank-standards table printing `102.20` beside
a practice log printing `1'53"16`: *"All of these should be in the format of
MM'SS"MS (like 1'21"32). This is important because that matches the format we
actually display in the practice log... If the time is less than a minute, we
omit the MM' section (e.g., 23 seconds is just 23"00)."*

Two shapes are needed because the two sources hold different units — a recorded
attempt is FRAMES, a rank standard is SECONDS with centisecond precision, and
routing the standard through the frame formatter would round a published
cutoff. So they are pinned against each other here rather than trusted to stay
alike.

`fmtIgt` itself is deliberately UNCHANGED and still prints its `0'`: it is
mirrored byte-for-byte by `core/timefmt.py::format_igt`
(tests/test_cross_language_parity.py), and that function also builds saved clip
FILENAMES (`replay/service.py`), so it is an identifier and not only a display.
The short form is a transformation OF it.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FORMAT_JS = REPO / "src" / "sm64_events" / "ui" / "format.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")


def _node(expression: str):
    script = (f"import {{ fmtIgt, fmtIgtShort, fmtSeconds }} "
              f"from {FORMAT_JS.as_uri()!r};\n"
              f"console.log(JSON.stringify({expression}));")
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


# Frame counts spanning both sides of a minute, plus the exact boundary.
FRAMES = [0, 1, 29, 30, 45, 690, 1799, 1800, 1801, 2629, 3066, 5400, 11000]


def test_a_time_under_a_minute_drops_its_empty_minutes_field():
    short = _node(f"{FRAMES}.map(fmtIgtShort)")
    full = _node(f"{FRAMES}.map(fmtIgt)")
    for frames, one, other in zip(FRAMES, short, full):
        if frames < 1800:
            assert other.startswith("0'"), (frames, other)
            assert one == other[2:], (frames, one, other)
            assert "'" not in one, (frames, one)
        else:
            assert one == other, (frames, one, other)


def test_the_long_form_is_untouched():
    """It is mirrored by core/timefmt.py::format_igt AND names saved clip
    files, so a change here is not cosmetic."""
    assert _node("[fmtIgt(690), fmtIgt(2629), fmtIgt(0)]") \
        == ["0'23\"00", "1'27\"63", "0'00\"00"]


def test_seconds_and_frames_agree_wherever_both_are_exact():
    """A rank standard in seconds and an attempt in frames must never print
    the same quantity two ways. Only frame-EXACT seconds can be compared —
    that is the whole reason the standards path does not go through frames."""
    # Multiples of 3 frames only: 3 frames is exactly 0.10 s, so the value is
    # representable in centiseconds and the two paths are comparable at all.
    # Elsewhere they legitimately differ by a hundredth -- the frame path
    # TRUNCATES (floor(frames*100/30), Usamune's own display) while the
    # seconds path rounds an already-exact figure. A rank standard is always
    # authored in centiseconds, so that case never arises for one.
    exact = [f for f in FRAMES if f % 3 == 0]
    seconds = [f / 30 for f in exact]
    from_seconds = _node(f"{seconds}.map(fmtSeconds)")
    from_frames = _node(f"{exact}.map(fmtIgtShort)")
    assert from_seconds == from_frames, list(zip(exact, from_seconds,
                                                 from_frames))


def test_the_shape_he_asked_for():
    assert _node("[fmtSeconds(23), fmtSeconds(81.32), fmtSeconds(102.20),"
                 " fmtSeconds(0), fmtSeconds(59.99), fmtSeconds(60)]") \
        == ["23\"00", "1'21\"32", "1'42\"20", "00\"00", "59\"99", "1'00\"00"]


def test_a_published_cutoff_is_never_moved_by_being_displayed():
    """Every ladder value in the bundled seed round-trips: the printed
    centiseconds are the authored ones, with no frame-quantisation drift. This
    is the failure the seconds path exists to avoid — 76.66 s is not a whole
    number of frames, and going via frames would print 76.63."""
    import json as _json

    seed = _json.loads(
        (REPO / "src" / "sm64_events" / "data" / "rank_standards.seed.json")
        .read_text(encoding="utf-8"))
    values = sorted({round(v, 2)
                     for entity in seed["entities"].values()
                     for ladder in entity.get("strategies", {}).values()
                     for v in ladder.values()})
    printed = _node(f"{values}.map(fmtSeconds)")
    for seconds, text in zip(values, printed):
        minutes, _, rest = text.rpartition("'")
        secs, _, cents = rest.partition('"')
        total = (int(minutes) * 60 if minutes else 0) + int(secs) + int(cents) / 100
        assert abs(total - seconds) < 1e-9, (seconds, text)
    assert len(values) > 1000, f"only {len(values)} cutoffs checked"
