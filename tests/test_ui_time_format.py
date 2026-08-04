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
                         capture_output=True, text=True, check=True,
                         # subprocess decodes with the Windows ANSI codepage
                         # unless told otherwise, which mojibakes the middle
                         # dot in every variant-qualified strategy name.
                         encoding="utf-8")
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


# ---- the three-box editor's arithmetic ----

def _node_fields(expression: str):
    script = (f"import {{ splitSeconds, joinTime, fmtSeconds }} "
              f"from {FORMAT_JS.as_uri()!r};\n"
              f"console.log(JSON.stringify({expression}));")
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True,
                         # subprocess decodes with the Windows ANSI codepage
                         # unless told otherwise, which mojibakes the middle
                         # dot in every variant-qualified strategy name.
                         encoding="utf-8")
    return json.loads(out.stdout)


def test_the_boxes_round_trip_every_seeded_cutoff():
    """What is split into three boxes and joined back must be the same time.
    Run over the whole bundled seed rather than samples: a cutoff that changes
    by a hundredth because it was EDITED and re-saved is a community standard
    silently rewritten, and nothing downstream would flag it."""
    import json as _json

    seed = _json.loads(
        (REPO / "src" / "sm64_events" / "data" / "rank_standards.seed.json")
        .read_text(encoding="utf-8"))
    values = sorted({round(v, 2)
                     for entity in seed["entities"].values()
                     for ladder in entity.get("strategies", {}).values()
                     for v in ladder.values()})
    back = _node_fields(
        f"{values}.map((v) => {{ const p = splitSeconds(v);"
        " return joinTime(p.minutes, p.seconds, p.centis); })")
    for original, restored in zip(values, back):
        assert abs(original - restored) < 1e-9, (original, restored)


def test_the_boxes_hold_exactly_what_the_cell_prints():
    """The editor and the display split a time at the same boundaries, because
    the formatter is written in terms of the same split — so what you see in
    the cell is what you find in the boxes."""
    cases = [102.20, 81.32, 23.0, 12.93, 0.07, 60.0, 119.99]
    parts = _node_fields(f"{cases}.map(splitSeconds)")
    printed = _node_fields(f"{cases}.map(fmtSeconds)")
    for value, part, text in zip(cases, parts, printed):
        expected = (f"{part['seconds']:02d}\"{part['centis']:02d}"
                    if part["minutes"] == 0
                    else f"{part['minutes']}'{part['seconds']:02d}"
                         f"\"{part['centis']:02d}")
        assert text == expected, (value, part, text)


def test_a_blank_minutes_box_is_zero_not_an_error():
    """Under a minute the field is left empty, which must read as 0 — the
    common case is typing only seconds and centiseconds."""
    assert _node_fields('[joinTime("", "23", "00"), joinTime("", "", ""),'
                        ' joinTime("1", "21", "32")]') == [23, 0, 81.32]
