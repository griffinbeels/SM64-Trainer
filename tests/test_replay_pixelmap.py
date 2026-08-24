"""Map v3's pure pieces: value strings, weighted templates, the banded
fit, and the refusal. The vision half (regions, masks) is judged on real
clips by tools/probe_pixel_read.py -- no assertion here can stand in for
looking at footage, and the module is NOT wired into extraction until
that probe's training accuracy meets its 99% gate (round 32 item 26).
"""
import numpy as np

from sm64_events.replay.pixelmap import (SnapResult, _learn_templates,
                                         _read, _value_string, build_map)


def test_value_strings_print_like_usamune():
    assert _value_string(84, "UD") == "U84"
    assert _value_string(-23, "UD") == "D23"
    assert _value_string(70, "RL") == "R70"
    assert _value_string(-1, "RL") == "L1"
    assert _value_string(0, "UD") == "0"


def test_templates_vote_only_where_exemplars_agree():
    rng = np.random.default_rng(7)
    glyph = rng.random(384) > 0.5
    noise_cells = rng.random(384) > 0.9         # background: never agrees
    exemplars = []
    for _ in range(6):
        sample = glyph.copy()
        flip = noise_cells & (rng.random(384) > 0.5)
        sample ^= flip
        exemplars.append(sample)
    templates = _learn_templates(exemplars, ["U84"] * 6)
    assert "U84" in templates
    bits, weight = templates["U84"]
    assert not (weight & noise_cells).any() or \
        int((weight & noise_cells).sum()) < int(noise_cells.sum()) / 2
    # a clean glyph reads back; unrelated ink does not
    assert _read([glyph], templates) == ["U84"]
    assert _read([~glyph], templates) == [None]


def test_the_fit_corrects_a_shifted_stretch_and_keeps_the_rest():
    # Track: pads by raw frame; the display read says the value arrives one
    # slot later than the prior claims for frames 104-105.
    pads = {raw: (0, 0, raw - 100) for raw in range(100, 110)}
    prior = [100, 100, 101, 101, 102, 102, 103, 103, 104, 104, 105, 105]
    field_y = [_value_string(pads[raw][2], "UD") for raw in
               [100, 100, 101, 101, 102, 102, 103, 103, 103, 104, 104, 105]]
    field_x = [None] * len(prior)
    buttons = [None] * len(prior)
    sorted_raws = sorted(pads)

    def raws_near(raw):
        return [r for r in sorted_raws if raw - 2 <= r <= raw + 2] or [raw]

    got = build_map(prior, pads.get, raws_near, field_y, field_x, buttons)
    assert isinstance(got, SnapResult)
    refined_values = [pads[raw][2] for raw in got.frame_map]
    read_values = [int(v.replace("U", "").replace("D", "-")) if v != "0"
                   else 0 for v in field_y]
    assert refined_values == read_values
    assert got.moved > 0


def test_unread_slots_keep_the_prior():
    pads = {raw: (0, 0, raw - 100) for raw in range(100, 106)}
    prior = [100, 101, 102, 103, 104, 105]
    nothing = [None] * 6

    def raws_near(raw):
        return [raw]

    got = build_map(prior, pads.get, raws_near,
                    ["U0" if False else _value_string(pads[p][2], "UD")
                     for p in prior],
                    nothing, nothing)
    assert got is not None and got.frame_map == prior and got.moved == 0


def test_too_few_reads_refuse():
    prior = [100 + n for n in range(20)]
    nothing = [None] * 20
    got = build_map(prior, lambda raw: (0, 0, 0), lambda raw: [raw],
                    nothing, nothing, nothing)
    assert got is None
