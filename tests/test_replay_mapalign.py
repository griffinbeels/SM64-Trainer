"""Aligning a clip's frame map to the game's own display.

The worlds here are synthetic but the physics is his: a video slot shows
some game frame, Usamune draws that frame's stick into the picture, and the
lit-pixel count follows the glyphs it drew. A map that names the wrong
frame predicts the wrong glyphs, and the measurement has to find that --
and, just as importantly, REFUSE when the pixels cannot answer, because a
confident wrong shift is worse than the constants it replaces.
"""
import numpy as np

from sm64_events.replay import mapalign

# Per-glyph ink, roughly as the HUD font really behaves: an 8 is heavier
# than a 1, a letter heavier than a digit. Any weights work -- the fit
# learns them -- but varied ones make the world honest.
INK_WEIGHTS = {"0": 52, "1": 24, "2": 46, "3": 47, "4": 44, "5": 48,
               "6": 50, "7": 38, "8": 56, "9": 51,
               "U": 40, "D": 40, "L": 34, "R": 44}
FIRST_FRAME = 1000


def stick_at(frame: int) -> tuple[int, int]:
    """A stick that moves every frame, the way his does."""
    vertical = 84 - (frame * 7) % 90
    horizontal = ((frame * 13) % 150) - 75
    return horizontal, vertical


def true_frame_at(slot: int) -> int:
    """30 game frames a second over 60 video slots a second."""
    return FIRST_FRAME + slot // 2


def ink_from(frames, jitter=0.0, seed=7):
    rng = np.random.default_rng(seed)
    values = []
    for frame in frames:
        row = mapalign.glyph_row(*stick_at(frame))
        weights = np.array([INK_WEIGHTS[glyph] for glyph in mapalign.GLYPHS])
        values.append(float(row @ weights) + rng.normal(0, jitter))
    return np.array(values)


def world(slots=900, map_ahead_by=0):
    """(ink measured from the screen, the rows the MAP claims per slot)."""
    ink = ink_from([true_frame_at(slot) for slot in range(slots)], jitter=3.0)
    rows = [mapalign.glyph_row(*stick_at(true_frame_at(slot + map_ahead_by)))
            for slot in range(slots)]
    return ink, rows


def test_it_finds_a_map_that_runs_ahead_of_the_footage():
    ink, rows = world(map_ahead_by=3)
    found = mapalign.measure_offset(ink, rows)
    assert found is not None
    assert found.offset == -3, "a map three slots ahead must measure -3"
    assert found.paired > mapalign.MIN_PAIRED_SLOTS


def test_it_finds_a_map_that_lags_the_footage():
    ink, rows = world(map_ahead_by=-2)
    found = mapalign.measure_offset(ink, rows)
    assert found is not None and found.offset == 2


def test_a_map_already_on_the_footage_measures_zero():
    ink, rows = world(map_ahead_by=0)
    found = mapalign.measure_offset(ink, rows)
    assert found is not None and found.offset == 0


def test_shifting_by_the_measured_offset_lands_the_map_on_zero():
    """The property the whole fix rests on, and the one his clip proved
    (attempt 3739: -3 slots, then 0 after the shift)."""
    ink, rows = world(map_ahead_by=4)
    first = mapalign.measure_offset(ink, rows)
    assert first.offset == -4
    moved = [rows[slot + first.offset] if 0 <= slot + first.offset < len(rows)
             else None for slot in range(len(rows))]
    again = mapalign.measure_offset(ink, moved)
    assert again is not None and again.offset == 0


def test_an_unlit_region_earns_no_verdict():
    """Usamune's display off: no digits, no ink, nothing to align to. The
    map must stand as built rather than move by a fitted nothing."""
    _ink, rows = world()
    assert mapalign.measure_offset(np.zeros(len(rows)), rows) is None


def test_pure_noise_earns_no_verdict():
    _ink, rows = world()
    noise = np.random.default_rng(3).normal(500, 120, len(rows))
    assert mapalign.measure_offset(noise, rows) is None


def test_too_few_paired_slots_earns_no_verdict():
    """A track that covers only a sliver of the clip cannot carry the
    question -- the fit would be reading a handful of frames."""
    ink, rows = world(slots=900, map_ahead_by=3)
    sparse = [row if slot < mapalign.MIN_PAIRED_SLOTS // 2 else None
              for slot, row in enumerate(rows)]
    assert mapalign.measure_offset(ink, sparse) is None


def test_shifted_moves_the_map_and_refuses_to_invent_the_ends():
    frame_map = [10, 11, 12, 13, 14]
    assert mapalign.shifted(frame_map, 0) == frame_map
    assert mapalign.shifted(frame_map, -2) == [None, None, 10, 11, 12]
    assert mapalign.shifted(frame_map, 2) == [12, 13, 14, None, None]


def test_rows_follow_the_map_and_hole_where_it_says_nothing():
    pads = {500: (0, 84), 501: (70, 71)}
    rows = mapalign.rows_for_map([500, None, 501, 999], pads.get)
    assert rows[1] is None and rows[3] is None
    assert np.array_equal(rows[0], mapalign.glyph_row(0, 84))
    assert np.array_equal(rows[2], mapalign.glyph_row(70, 71))


def test_the_glyphs_are_what_usamune_draws():
    # "U84" over "R70" is six glyphs; "U84" over a bare "0" is four.
    assert mapalign.glyph_row(70, 84).sum() == 6
    assert mapalign.glyph_row(0, 84).sum() == 4
    assert mapalign.glyph_row(0, 0).sum() == 2      # "0" over "0"
    left = mapalign.glyph_row(-5, -84)
    assert left[mapalign.GLYPHS.index("L")] == 1
    assert left[mapalign.GLYPHS.index("D")] == 1


def test_ink_counts_only_saturated_fire():
    """The mask must ignore what the game draws BEHIND the digits -- a
    brown brick and a blue water tile, both sampled from his clips."""
    picture = np.zeros((1, 3, 1, 3), dtype=np.int16)
    picture[0, 0, 0] = (255, 140, 20)      # a lit digit
    picture[0, 1, 0] = (121, 84, 42)       # brick
    picture[0, 2, 0] = (88, 119, 189)      # water
    assert mapalign.ink_per_slot(picture)[0] == 1


def test_the_region_scales_with_the_capture():
    assert mapalign.region_for_width(1600) == mapalign.DIGIT_REGION_AT_1600
    half = mapalign.region_for_width(800)
    assert half == tuple(round(v / 2) for v in mapalign.DIGIT_REGION_AT_1600)
