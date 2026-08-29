"""Aligning a clip's frame map to the game's own display.

The worlds here are synthetic but the physics is his: a video slot shows
some game frame, Usamune draws that frame's stick into the picture, and the
lit-pixel count follows the glyphs it drew. A map that names the wrong
frame predicts the wrong glyphs, and the measurement has to find that --
and, just as importantly, REFUSE when the pixels cannot answer, because a
confident wrong shift is worse than the constants it replaces.
"""
import itertools

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
    assert all(later > earlier for earlier, later in itertools.pairwise(seen)), (
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


# --- the anchor: frame-domain correction + the learned store -----------------

def test_frame_corrected_moves_values_and_never_splits_a_picture():
    """A measured slot offset lands as ONE constant on every frame value:
    round(offset / 2). Shifting slots by an odd amount instead would split
    pictures the quantiser just unified."""
    held = [10, 10, 11, 11, None, 12]
    assert mapalign.frame_corrected(held, -2) == [9, 9, 10, 10, None, 11]
    assert mapalign.frame_corrected(held, -3) == [8, 8, 9, 9, None, 10]
    assert mapalign.frame_corrected(held, 1) == held      # half rounds to 0
    assert mapalign.frame_corrected(held, 0) == held


def test_the_margin_gate_ignores_the_neighbour_it_cannot_beat():
    """Pinned against the REAL curve from his clip 4441 (2026-08-29): peak
    at -2 with 0.0066 over its neighbour -1 and 0.0168 over the best
    non-neighbour. On a quantised map the neighbour is near-equivalent BY
    CONSTRUCTION, so it is the instrument's resolution, not a rival; the
    neighbour-inclusive gate refused this exact curve and the clip ran a
    frame ahead."""
    curve_4441 = {-8: 0.1957, -7: 0.2319, -6: 0.2713, -5: 0.3154,
                  -4: 0.3673, -3: 0.4183, -2: 0.4688, -1: 0.4622,
                  0: 0.4520, 1: 0.3712, 2: 0.2981, 3: 0.2433, 4: 0.1936,
                  5: 0.1488, 6: 0.1033, 7: 0.0665, 8: 0.0359}
    verdict = mapalign.gate(curve_4441)
    assert verdict is not None, "the real 4441 curve must pass"
    best, margin = verdict
    assert best == -2 and margin > mapalign.MIN_MARGIN


def test_the_gate_still_refuses_a_flat_curve():
    flat = {offset: 0.40 + 0.001 * (offset == -2) for offset in range(-8, 9)}
    assert mapalign.gate(flat) is None


def test_the_gate_still_refuses_a_weak_fit():
    weak = {offset: 0.10 + 0.05 * (offset == -2) for offset in range(-8, 9)}
    assert mapalign.gate(weak) is None


def test_anchor_stats_learn_and_answer_the_median(tmp_path):
    stats = mapalign.AnchorStats(tmp_path / "anchor.json")
    assert stats.fallback_offset() is None, "no evidence, no answer"
    stats.record(1, -2, 0.5)
    stats.record(2, -2, 0.6)
    assert stats.fallback_offset() is None, "two clips are not a calibration"
    stats.record(3, -4, 0.4)
    assert stats.fallback_offset() == -2
    stats.record(3, -2, 0.7)          # re-measuring a clip replaces its row
    assert stats.fallback_offset() == -2


def test_anchor_stats_survive_a_corrupt_file(tmp_path):
    path = tmp_path / "anchor.json"
    path.write_text("{not json")
    stats = mapalign.AnchorStats(path)
    assert stats.fallback_offset() is None
    stats.record(1, -2, 0.5)          # and recording over it heals it
    assert (tmp_path / "anchor.json").exists()


def test_anchor_stats_stay_bounded(tmp_path):
    stats = mapalign.AnchorStats(tmp_path / "anchor.json")
    for attempt in range(200):
        stats.record(attempt, -2, 0.5)
    import json as _json
    rows = _json.loads((tmp_path / "anchor.json").read_text())
    assert len(rows) == mapalign.AnchorStats.KEEP


# -- the ledger map: capture's own per-picture record becomes the map -------

FPS = 60.0
LEDGER_T0 = 1000.0


def _ledger_world(count=10, skip_rows=(), frame_of=None, shift=0.0):
    """`count` pictures, two slots each; one ledger row per picture composed
    at its first slot's wall (+`shift`), frame 100+k unless `frame_of`."""
    runs = [(2 * k, 2) for k in range(count)]
    rows = [{"ts": LEDGER_T0 + (2 * k + 0.5) / FPS + shift,
             "frame": (frame_of or (lambda i: 100 + i))(k)}
            for k in range(count) if k not in skip_rows]
    return runs, rows


def test_ledger_map_gives_each_picture_its_own_recorded_frame():
    runs, rows = _ledger_world()
    out = mapalign.ledger_map(20, runs, rows, LEDGER_T0, FPS)
    assert out == [100 + k for k in range(10) for _ in (0, 1)]


def test_a_clock_bias_plus_jitter_self_calibrates_to_exact():
    """The composition clock and the slot wall disagree by a systematic
    amount nobody should tune (four hand-set constants each got it wrong):
    the median of the nearest-row deltas measures it per clip, so the
    tolerance budget is spent on jitter alone. Here bias 13 ms + jitter
    6 ms pushes half the raw deltas past the tolerance; without the
    median, the +2 step at picture 6 would be flattened to +1 by the
    consecutive default."""
    frames = lambda k: (100 + k) if k < 6 else (101 + k)
    runs, rows = _ledger_world(frame_of=frames, shift=0.013)
    for index, row in enumerate(rows):
        row["ts"] += 0.006 if index % 2 == 0 else -0.006
    out = mapalign.ledger_map(20, runs, rows, LEDGER_T0, FPS)
    assert out == [frames(k) for k in range(10) for _ in (0, 1)]


def test_a_bias_of_a_whole_picture_aliases_to_a_frame_shift():
    """Beyond half a picture period the times alone cannot say which
    picture a row belongs to -- a whole-period bias matches every run to
    its neighbour, one frame low, with the consecutive structure intact.
    That residue is exactly what the digit anchor measures and closes."""
    runs, rows = _ledger_world(shift=1 / 30)
    out = mapalign.ledger_map(20, runs, rows, LEDGER_T0, FPS)
    assert out == [99 + k for k in range(10) for _ in (0, 1)]


def test_a_picture_the_dedup_missed_takes_the_consecutive_default():
    runs, rows = _ledger_world(skip_rows={5})
    out = mapalign.ledger_map(20, runs, rows, LEDGER_T0, FPS)
    assert out[10] == out[11] == 105
    assert out == [100 + k for k in range(10) for _ in (0, 1)]


def test_a_frame_the_capture_never_grabbed_keeps_the_advance():
    """Rows carry the RAM frame, so a game frame no grab caught shows as a
    +2 step between neighbouring pictures -- preserved, exactly like
    quantised's rising-by-more rule."""
    frames = {0: 100, 1: 101, 2: 103, 3: 104, 4: 105}
    runs, rows = _ledger_world(count=5, frame_of=frames.get)
    out = mapalign.ledger_map(10, runs, rows, LEDGER_T0, FPS)
    assert out == [100, 100, 101, 101, 103, 103, 104, 104, 105, 105]


def test_lag_frames_lands_ledger_maps_in_the_series_domain():
    runs, rows = _ledger_world(count=5)
    out = mapalign.ledger_map(10, runs, rows, LEDGER_T0, FPS, lag_frames=1)
    assert out[0] == 99 and out[-1] == 103


def test_too_few_rows_is_a_refusal_not_a_guess():
    runs, rows = _ledger_world(count=10)
    assert mapalign.ledger_map(20, runs, rows[:2], LEDGER_T0, FPS) is None
    assert mapalign.ledger_map(20, runs, [], LEDGER_T0, FPS) is None
    assert mapalign.ledger_map(20, [], rows, LEDGER_T0, FPS) is None


def test_rows_without_a_frame_do_not_count_as_coverage():
    runs, rows = _ledger_world(count=10)
    for row in rows[4:]:
        row["frame"] = None
    assert mapalign.ledger_map(20, runs, rows, LEDGER_T0, FPS) is None


# -- the windowed anchor: a lag that STEPS mid-clip (items 41-43) -----------

def shelf_world(slots=1200, seam=400, early_ahead=2, late_ahead=0):
    """The map runs `early_ahead` slots ahead of the footage before `seam`
    and `late_ahead` after it -- attempt 4518's shape, where the pipeline
    lag stepped by whole frames inside one clip."""
    ink = ink_from([true_frame_at(slot) for slot in range(slots)], jitter=3.0)
    frame_map = [true_frame_at(slot + (early_ahead if slot < seam
                                       else late_ahead))
                 for slot in range(slots)]
    rows = [mapalign.glyph_row(*stick_at(value)) for value in frame_map]
    return ink, rows, frame_map


def test_each_shelf_measures_its_own_offset_and_the_seam_is_refined():
    ink, rows, _map = shelf_world()
    anchor = mapalign.measure_offset(ink, rows)
    assert anchor is not None
    shelves = mapalign.measure_windows(ink, rows, anchor)
    assert [shelf[2] for shelf in shelves] == [-2, 0]
    seam = shelves[0][1]
    # Without the change-point refinement the seam can only land on a
    # window boundary (multiples of 240); the truth is at 400.
    assert abs(seam - 400) <= 8
    assert shelves[0][0] == 0 and shelves[-1][1] == len(ink)


def test_window_corrected_lands_every_shelf_on_the_footage():
    ink, rows, frame_map = shelf_world()
    anchor = mapalign.measure_offset(ink, rows)
    shelves = mapalign.measure_windows(ink, rows, anchor)
    corrected = mapalign.window_corrected(frame_map, shelves)
    truth = [true_frame_at(slot) for slot in range(len(frame_map))]
    wrong = [slot for slot, (now, want) in enumerate(zip(corrected, truth, strict=True))
             if now != want]
    # Exact everywhere except a picture or two around the seam.
    assert all(abs(slot - 400) <= 12 for slot in wrong), wrong[:10]
    ordered = [value for value in corrected if value is not None]
    assert all(b >= a for a, b in itertools.pairwise(ordered))


def test_an_unreadable_stretch_is_absorbed_by_its_neighbours():
    ink, rows, _map = shelf_world(early_ahead=0, late_ahead=0)
    # One window of the display obscured: its ink explains nothing, its
    # gate refuses, and the shelves on either side own its slots. The
    # noise sits at the ink's own scale so the GLOBAL anchor survives --
    # louder noise drowns the whole clip's fit, which is the global
    # refusal other tests already pin.
    ink[480:720] = np.random.default_rng(3).normal(
        ink.mean(), ink.std(), 240)
    anchor = mapalign.measure_offset(ink, rows)
    shelves = mapalign.measure_windows(ink, rows, anchor)
    assert [shelf[2] for shelf in shelves] == [0]
    assert shelves[0][0] == 0 and shelves[0][1] == len(ink)


def test_no_gated_window_means_no_windows_not_a_guess():
    ink, rows, _map = shelf_world(early_ahead=0, late_ahead=0)
    anchor = mapalign.measure_offset(ink, rows)
    flat = np.zeros_like(ink)
    assert mapalign.measure_windows(flat, rows, anchor) == []


def test_window_corrected_without_windows_is_identity():
    frame_map = [10, 10, 11, 11, None, 12]
    assert mapalign.window_corrected(frame_map, []) == frame_map


def test_a_downward_seam_waits_for_a_skip_in_the_map():
    """A shelf stepping DOWN cannot land where the map advances by one per
    picture -- the corrected series would regress, and a previous+1 clamp
    would cascade (+1 on every later picture until a skip). The old
    shelf's delta holds until the first boundary the map itself can
    absorb."""
    frame_map = [100, 100, 101, 101, 102, 102, 103, 103,
                 104, 104, 105, 105, 107, 107, 108, 108]
    windows = [(0, 8, 2, 0.9, 0.5), (8, 16, 0, 0.9, 0.5)]
    out = mapalign.window_corrected(frame_map, windows)
    assert out[:8] == [101, 101, 102, 102, 103, 103, 104, 104]
    assert out[8:12] == [105, 105, 106, 106]      # held on the old shelf
    assert out[12:] == [107, 107, 108, 108]       # the skip absorbs the step


PICTURE_RUNS_2SLOT = [(slot, 2) for slot in range(0, 1200, 2)]


def physical_blip_world(slots=1200, blip_lo=600, blip_hi=608):
    """The map one frame high inside [blip_lo, blip_hi): a +2 skip at
    entry and a dup at exit -- the structure a smoothed map really carries
    around a lag step, and the ONLY places such a step is expressible."""
    truth = [true_frame_at(slot) for slot in range(slots)]
    ink = ink_from(truth, jitter=3.0)
    frame_map = [value + (1 if blip_lo <= slot < blip_hi else 0)
                 for slot, value in enumerate(truth)]
    rows = [mapalign.glyph_row(*stick_at(value)) for value in frame_map]
    return ink, rows, frame_map, truth


def test_a_short_blip_at_physical_seams_is_corrected():
    """Items 41-43: a few pictures wrong by one, entered and left at the
    map's own irregular boundaries. The path flips them; the honest floor
    is the single dup picture at the exit seam."""
    for blip_hi, worst in ((608, 2), (604, 2)):       # 4- and 2-picture blips
        ink, rows, frame_map, truth = physical_blip_world(blip_hi=blip_hi)
        anchor = mapalign.measure_offset(ink, rows)
        stretches = mapalign.measure_windows(
            ink, rows, anchor, frame_map=frame_map, runs=PICTURE_RUNS_2SLOT)
        corrected = mapalign.window_corrected(frame_map, stretches)
        wrong = sum(1 for now, want in zip(corrected, truth, strict=True)
                    if now != want)
        assert wrong <= worst, (blip_hi, wrong)


def test_the_cheap_seams_are_what_make_the_blip_flippable():
    """Mutation guard in test form: with irregular boundaries priced like
    regular ones, the same blip stays -- the physicality of the seams is
    load-bearing, not decorative."""
    ink, rows, frame_map, truth = physical_blip_world()
    anchor = mapalign.measure_offset(ink, rows)
    original = mapalign.SWITCH_AT_IRREGULAR
    try:
        mapalign.SWITCH_AT_IRREGULAR = mapalign.SWITCH_AT_REGULAR
        stretches = mapalign.measure_windows(
            ink, rows, anchor, frame_map=frame_map, runs=PICTURE_RUNS_2SLOT)
        corrected = mapalign.window_corrected(frame_map, stretches)
        wrong = sum(1 for now, want in zip(corrected, truth, strict=True)
                    if now != want)
        assert wrong >= 8, "pricing seams flat should leave the blip"
    finally:
        mapalign.SWITCH_AT_IRREGULAR = original


# -- phase unwrapping: the per-row closed form (item 50) --------------------

def test_unwrap_resolves_edge_flicker_per_row():
    """The pyramid's failure: the present sits ON the frame edge, so the
    stamp flickers +-1 with jitter. The phase says which side each row
    landed on, and the unwrap resolves every one exactly."""
    period = 1 / 30
    rows = []
    for order in range(40):
        crossed = order % 3 == 0                # jitter across the edge
        rows.append({"ts": order * period,
                     "frame": 100 + order + (1 if crossed else 0),
                     "phase": 0.002 if crossed else 0.031})
    display = mapalign.unwrapped_display(rows)
    # One consistent staircase: the flicker resolves to a SINGLE constant
    # (whose absolute value is the global anchor's job, not the unwrap's).
    assert len({display[order] - (100 + order)
                for order in range(40)}) == 1


def test_unwrap_follows_slow_drift_across_a_wrap():
    """The present drifts slowly; when it crosses an edge the stamp gains
    a frame and the phase wraps -- the unwrap keeps the display
    continuous."""
    period = 1 / 30
    rows = []
    for order in range(60):
        drift = 0.030 + order * 0.0002          # ~0.2 ms per picture
        crossed = drift >= period
        rows.append({"ts": order * period,
                     "frame": 100 + order + (1 if crossed else 0),
                     "phase": drift - (period if crossed else 0.0)})
    display = mapalign.unwrapped_display(rows)
    # One consistent constant across the wrap (its value is canonical to
    # the implied delay, not to this test's arithmetic).
    assert len({display[order] - (100 + order) for order in range(60)}) == 1


def test_unwrap_skips_a_stall_burst_instead_of_guessing():
    period = 1 / 30
    rows = [{"ts": order * period, "frame": 100 + order, "phase": 0.020}
            for order in range(10)]
    rows.append({"ts": 11 * period, "frame": 117, "phase": 0.276})
    display = mapalign.unwrapped_display(rows)
    assert 10 not in display                    # the stall row: no estimate
    assert display[9] == 108                    # neighbours untouched


def test_rows_without_phase_are_absent_not_wrong():
    display = mapalign.unwrapped_display(
        [{"ts": 0.0, "frame": 100}, {"ts": 0.03, "frame": 101,
                                     "phase": 0.02}])
    assert 0 not in display and display[1] == 100


def test_ledger_map_prefers_the_unwrap_over_the_constant():
    """A flickering stamp with phases lands EXACT through ledger_map; the
    same rows without phases keep the constant rule (and its error)."""
    runs = [(2 * order, 2) for order in range(10)]
    rows = []
    for order in range(10):
        crossed = order % 2 == 0
        rows.append({"ts": LEDGER_T0 + (2 * order + 0.5) / FPS,
                     "frame": 101 + order + (1 if crossed else 0),
                     "phase": 0.002 if crossed else 0.031})
    out = mapalign.ledger_map(20, runs, rows, LEDGER_T0, FPS, lag_frames=1)
    base = out[0]
    assert out == [base + order for order in range(10) for _ in (0, 1)]
    bare = [{key: row[key] for key in ("ts", "frame")} for row in rows]
    flickery = mapalign.ledger_map(20, runs, bare, LEDGER_T0, FPS,
                                   lag_frames=1)
    flat = {flickery[slot] - slot // 2 for slot in range(0, 20, 2)}
    assert len(flat) > 1                        # the constant cannot fix it


def test_unwrap_is_canonical_across_baselines():
    """A delay past one whole period folds into the display estimate, so
    the same physical pipeline yields the same numbers whatever the first
    row's branch -- the constant means ONE thing on every clip."""
    period = 1 / 30
    short = [{"ts": order * period, "frame": 100 + order, "phase": 0.020}
             for order in range(20)]
    long_lag = [{"ts": order * period, "frame": 100 + order, "phase": 0.040}
                for order in range(20)]
    near = mapalign.unwrapped_display(short)
    far = mapalign.unwrapped_display(long_lag)
    assert near[5] - near[0] == far[5] - far[0] == 5
    assert far[0] == near[0] - 1          # one extra whole frame of delay


def test_an_orphaned_row_carves_its_pictures_out_of_a_merged_run():
    """Item 50's headline: the encoded-side boundary detector can merge
    near-identical pictures into one run (his pyramid's dark corridor:
    three pictures, one 4-slot run, frames 276-277 gone from the map).
    The ledger saw every one of them, so the rows no run matched split
    the merged run at their own composition times -- the pixels may
    refine boundaries, never delete a picture."""
    runs = [(0, 2), (2, 6), (8, 2)]              # pictures 1-3 merged
    rows = [{"ts": LEDGER_T0 + (2 * order + 0.5) / FPS, "frame": 101 + order}
            for order in range(5)]
    out = mapalign.ledger_map(10, runs, rows, LEDGER_T0, FPS, lag_frames=1)
    assert out == [100, 100, 101, 101, 102, 102, 103, 103, 104, 104]
