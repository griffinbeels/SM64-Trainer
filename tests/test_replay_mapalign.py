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


# --- one answer per PICTURE (his ruling, 2026-08-28) -------------------------

def grey_of(runs):
    """A fake decoded clip: `runs` is a list of (brightness, length)."""
    rows = []
    for value, length in runs:
        rows.extend([[value] * 40] * length)
    return np.array(rows, dtype=np.uint8)


def test_picture_runs_find_each_distinct_picture():
    grey = grey_of([(0, 2), (60, 3), (120, 1), (200, 2)])
    assert mapalign.picture_runs(grey) == [(0, 2), (2, 3), (5, 1), (6, 2)]


def test_an_empty_clip_has_no_runs():
    assert mapalign.picture_runs(np.zeros((0, 40), dtype=np.uint8)) == []


def test_every_video_frame_of_one_picture_answers_the_same():
    """His ruling: "if there's duplicated frames, input timeline should be
    identical for the sequential duplicated frames." Measured on his clip
    4374: 257 pictures carried two different timeline frames before this,
    and none after."""
    runs = [(0, 2), (2, 3), (5, 2)]
    held = mapalign.quantised([10, 10, 11, 11, 12, 13, 13], runs)
    for start, length in runs:
        window = {held[slot] for slot in range(start, start + length)}
        assert len(window) == 1, "a picture must carry exactly one answer"


def test_the_map_still_supplies_the_advance_across_a_missed_frame():
    """The runs give BOUNDARIES, never the count. Numbering pictures
    consecutively would drift by every game frame the capture missed --
    428 pictures for ~478 frames on his clip, so a counted map would end
    fifty frames adrift."""
    runs = [(0, 2), (2, 2), (4, 2)]
    #                     the capture missed 101 entirely
    held = mapalign.quantised([100, 100, 102, 102, 103, 103], runs)
    assert held == [100, 100, 102, 102, 103, 103]


def test_a_map_already_holding_per_picture_is_left_alone():
    runs = [(0, 2), (2, 2)]
    already = [7, 7, 8, 8]
    assert mapalign.quantised(already, runs) == already


def test_a_split_picture_takes_the_later_frame():
    """A run whose slots disagree evenly is a boundary the clock put half a
    frame off; the picture is the newer of the two, because the map is
    catching up to it rather than running ahead of it."""
    assert mapalign.quantised([4, 5], [(0, 2)]) == [5, 5]


def test_slots_the_map_could_not_answer_stay_unanswered():
    held = mapalign.quantised([None, None, 9, 9], [(0, 2), (2, 2)])
    assert held[:2] == [None, None] and held[2:] == [9, 9]


def test_consecutive_pictures_are_consecutive_frames():
    """The second half of his ruling, found by stepping: holding the
    boundaries alone left the map's own irregular advance untouched, so on
    his clip 4441 three different pictures all read frame 7 and the next
    jumped to 9 -- 230 pictures sharing a frame with the next, 225 skipping
    one. After this: zero shared, 1,157 of 1,163 advancing by exactly one."""
    runs = [(0, 2), (2, 2), (4, 2), (6, 2), (8, 2)]
    held = mapalign.quantised([7, 7, 7, 7, 7, 7, 9, 9, 10, 10], runs)
    seen = [held[start] for start, _length in runs]
    assert all(later > earlier for earlier, later in zip(seen, seen[1:])), (
        f"every picture must advance the frame: {seen}")


def test_a_frame_the_capture_missed_is_still_a_step_of_two():
    """Rising by MORE is allowed where the map says so -- the capture
    really does miss frames (1,230 pictures for ~1,326 game frames on his
    clip), and forcing a step of exactly one would end the clip ninety-six
    frames adrift."""
    runs = [(0, 2), (2, 2), (4, 2), (6, 2)]
    held = mapalign.quantised([10, 10, 11, 11, 20, 20, 21, 21], runs)
    seen = [held[start] for start, _length in runs]
    assert seen[-1] - seen[0] >= 9, f"the map's own advance must survive: {seen}"


def test_the_correction_moves_each_picture_as_little_as_the_rule_allows():
    """A clip that already obeys the rule is left exactly as it is."""
    runs = [(0, 2), (2, 2), (4, 2)]
    already = [5, 5, 6, 6, 7, 7]
    assert mapalign.quantised(already, runs) == already


def test_rising_is_the_nearest_non_decreasing_series():
    assert mapalign._rising([1.0, 2.0, 3.0]) == [1.0, 2.0, 3.0]
    # a dip is pooled with its neighbours into their shared mean
    assert mapalign._rising([1.0, 5.0, 3.0]) == [1.0, 4.0, 4.0]
    assert mapalign._rising([]) == []


def test_a_dip_splits_the_difference_rather_than_dragging_the_clip_up():
    """WHY the correction is a least-squares fit and not a walk that just
    bumps each picture past its predecessor. A map that dips -- 10 then 9
    then 11 -- is evidence about all three pictures, so the answer sits
    between them (9, 10, 11). Bumping alone would read the first value as
    gospel and shove everything after it upward (10, 11, 12), moving every
    later picture in the clip to satisfy one bad reading."""
    runs = [(0, 2), (2, 2), (4, 2)]
    held = mapalign.quantised([10, 10, 9, 9, 11, 11], runs)
    assert [held[start] for start, _length in runs] == [9, 10, 11]
