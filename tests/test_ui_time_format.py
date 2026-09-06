"""One display notation, preserving the canonical identifier and seeded cutoffs.

fmtIgt also names saved clips and retains its zero minutes. fmtSeconds keeps
centisecond precision: routing a published 76.66-second cutoff through game
frames would move it. The assertions stay in Python; the real JS functions
run in one short-lived Node process per module, rather than eleven startups.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

REPO = Path(__file__).resolve().parents[1]
FRAMES = [0, 1, 29, 30, 45, 690, 1799, 1800, 1801, 2629, 3066, 5400, 11000]
CASES = [102.20, 81.32, 23.0, 12.93, 0.07, 60.0, 119.99]
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


@pytest.fixture(scope="module")
def samples():
    seed = json.loads((REPO / "src/sm64_events/data/rank_standards.seed.json")
                      .read_text(encoding="utf-8"))
    values = sorted({round(v, 2) for entity in seed["entities"].values()
                     for ladder in entity.get("strategies", {}).values()
                     for v in ladder.values()})
    result = subprocess.run(
        ["node", str(REPO / "tests/frontend/format-samples.mjs")],
        input=json.dumps({"frames": FRAMES, "cutoffs": values, "cases": CASES}),
        capture_output=True, encoding="utf-8", check=True, timeout=30,
        **quiet_spawn_kwargs(),
    )
    return values, json.loads(result.stdout)


def test_a_time_under_a_minute_drops_its_empty_minutes_field(samples):
    _, data = samples
    for frames, short, full in zip(FRAMES, data["short"], data["full"], strict=True):
        if frames < 1800:
            assert full.startswith("0'")
            assert short == full[2:]
            assert "'" not in short
        else:
            assert short == full


def test_the_long_form_is_untouched(samples):
    assert samples[1]["longForm"] == ['0\'23"00', '1\'27"63', '0\'00"00']


def test_seconds_and_frames_agree_wherever_both_are_exact(samples):
    data = samples[1]
    assert len(data["fromSeconds"]) == len([f for f in FRAMES if f % 3 == 0])
    assert data["fromSeconds"] == data["fromFrames"]


def test_the_shape_he_asked_for(samples):
    assert samples[1]["display"] == ['23"00', '1\'21"32', '1\'42"20',
                                     '00"00', '59"99', '1\'00"00']


def test_a_published_cutoff_is_never_moved_by_being_displayed(samples):
    values, data = samples
    assert len(values) > 1000, f"only {len(values)} cutoffs checked"
    for seconds, text in zip(values, data["printedCutoffs"], strict=True):
        minutes, _, rest = text.rpartition("'")
        secs, _, cents = rest.partition('"')
        total = (int(minutes) * 60 if minutes else 0) + int(secs) + int(cents) / 100
        assert abs(total - seconds) < 1e-9, (seconds, text)


def test_the_boxes_round_trip_every_seeded_cutoff(samples):
    values, data = samples
    for original, restored in zip(values, data["restoredCutoffs"], strict=True):
        assert abs(original - restored) < 1e-9, (original, restored)


def test_the_boxes_hold_exactly_what_the_cell_prints(samples):
    data = samples[1]
    for value, part, text in zip(CASES, data["parts"], data["printedCases"], strict=True):
        expected = (f"{part['seconds']:02d}\"{part['centis']:02d}"
                    if part["minutes"] == 0
                    else f"{part['minutes']}'{part['seconds']:02d}"
                         f"\"{part['centis']:02d}")
        assert text == expected, (value, part, text)


def test_a_blank_minutes_box_is_zero_not_an_error(samples):
    assert samples[1]["joined"] == [23, 0, 81.32]
