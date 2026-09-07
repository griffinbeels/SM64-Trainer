import pytest

from sm64_events.inputs.overlay import (CODECS, GAME_FPS, LAYERS,
                                        concat_script, encode_argv,
                                        output_name, plan_overlay)


def runs(spec):
    """spec: list of (start, length, buttons, stick_x, stick_y[, yaw]) --
    the timeline payload's run dicts, as the export tool hands them over."""
    out = []
    for row in spec:
        start, length, buttons, stick_x, stick_y = row[:5]
        out.append({"start": start, "length": length, "buttons": buttons,
                    "stick_x": stick_x, "stick_y": stick_y,
                    "yaw": row[5] if len(row) > 5 else 0, "speed": 0.0})
    return out


def test_identical_pictures_collapse_to_one_state():
    plan = plan_overlay(runs([(0, 5, 0x8000, 40, 0), (5, 5, 0, 0, 0),
                              (10, 5, 0x8000, 40, 0)]))
    # Unknown capture is transparent; a captured neutral controller is still
    # a real picture. Repeated active states share their own image.
    assert len(plan.states) == 3
    assert plan.states[0] is None
    assert plan.per_frame[0] == plan.per_frame[10]
    assert plan.states[plan.per_frame[5]] == (0, 0, 0, 0)


def test_the_stick_layer_ignores_buttons():
    """Two frames this layer DRAWS identically are one picture, so the button
    lane's changes must not multiply the stick layer's frame count."""
    plan = plan_overlay(runs([(0, 5, 0x8000, 40, 0), (5, 5, 0x4000, 40, 0)]),
                        layer="stick")
    assert plan.per_frame[0] == plan.per_frame[5]


def test_the_buttons_layer_ignores_the_stick():
    plan = plan_overlay(runs([(0, 5, 0x8000, 40, 0), (5, 5, 0x8000, -70, 12)]),
                        layer="buttons")
    assert plan.per_frame[0] == plan.per_frame[5]


def test_the_combined_layer_keeps_both_apart():
    plan = plan_overlay(runs([(0, 5, 0x8000, 40, 0), (5, 5, 0x8000, -70, 12)]))
    assert plan.per_frame[0] != plan.per_frame[5]


def test_a_capture_HOLE_draws_nothing_rather_than_the_last_pad():
    """The blank is the honest picture of "we do not know", and in an edit it
    reads as a gap rather than a stuck hand."""
    plan = plan_overlay(runs([(0, 2, 0x8000, 40, 0), (6, 2, 0x8000, 40, 0)]))
    assert plan.states[plan.per_frame[3]] is None


def test_the_video_frame_count_is_the_game_count_times_the_hold():
    plan = plan_overlay(runs([(0, 30, 0, 0, 0)]), video_fps=60)
    assert plan.game_frames == 30
    assert plan.video_frames == 60


def test_a_frame_rate_that_does_not_divide_by_thirty_is_refused():
    """A game frame held for a fractional number of video frames is exactly
    the drift this export exists to avoid, so it is refused rather than
    rounded."""
    with pytest.raises(ValueError, match="30"):
        plan_overlay(runs([(0, 30, 0, 0, 0)]), video_fps=50)


@pytest.mark.parametrize("layer", LAYERS)
def test_every_layer_plans(layer):
    assert plan_overlay(runs([(0, 3, 0x2000, 5, 5)]), layer=layer).layer == layer


def test_an_unknown_layer_or_codec_is_refused():
    with pytest.raises(ValueError, match="layer"):
        plan_overlay(runs([(0, 1, 0, 0, 0)]), layer="nope")
    with pytest.raises(ValueError, match="codec"):
        plan_overlay(runs([(0, 1, 0, 0, 0)]), codec="dnxhr")


def test_the_concat_script_has_ONE_LINE_PER_OUTPUT_FRAME():
    """Exact by construction: the file count IS the frame count, so a
    mis-encode is a number you can check rather than a drift to hunt."""
    plan = plan_overlay(runs([(0, 4, 0x8000, 0, 0)]), video_fps=60)
    script = concat_script(plan, lambda index: f"s{index}.png")
    files = [line for line in script.splitlines() if line.startswith("file ")]
    assert len(files) == plan.video_frames + 1     # +1: the demuxer drops one


def test_each_game_frame_is_held_for_the_whole_number_of_video_frames():
    plan = plan_overlay(runs([(0, 1, 0x8000, 0, 0), (1, 1, 0, 0, 0)]),
                        video_fps=60)
    files = [line for line in
             concat_script(plan, lambda index: f"s{index}.png").splitlines()
             if line.startswith("file ")]
    assert files[0] == files[1] != files[2]


def test_at_thirty_fps_each_game_frame_is_one_video_frame():
    plan = plan_overlay(runs([(0, 3, 0x8000, 0, 0)]), video_fps=GAME_FPS)
    files = [line for line in
             concat_script(plan, lambda index: f"s{index}.png").splitlines()
             if line.startswith("file ")]
    assert len(files) == 4


def test_the_encode_asks_for_a_CONSTANT_frame_rate_and_an_alpha_codec():
    plan = plan_overlay(runs([(0, 1, 0, 0, 0)]))
    argv = encode_argv("ffmpeg", "in.txt", "out.mov", plan)
    assert "-fps_mode" in argv and argv[argv.index("-fps_mode") + 1] == "cfr"
    assert "yuva444p10le" in argv           # the alpha half of ProRes 4444


def test_quicktime_animation_is_offered_and_is_also_alpha():
    plan = plan_overlay(runs([(0, 1, 0, 0, 0)]), codec="qtrle")
    assert "argb" in encode_argv("ffmpeg", "in.txt", "out.mov", plan)


def test_no_offered_codec_is_one_that_cannot_carry_alpha():
    """DNxHR SQ was the original ask and is 4:2:2 with no alpha channel, in
    any wrapper. Nothing here may quietly become that."""
    for args in CODECS.values():
        joined = " ".join(args)
        assert "dnx" not in joined
        assert "yuva" in joined or "argb" in joined or "rgba" in joined


def test_the_file_name_says_which_layer_it_is():
    plan = plan_overlay(runs([(0, 1, 0, 0, 0)]), layer="stick")
    assert output_name("run", plan) == "run.inputs-stick.mov"


def test_the_encode_demands_an_EXACT_frame_count():
    """The concat demuxer's per-entry durations cannot express 1/60 exactly,
    and a real export came out 3 frames long (6,129 for 6,126) -- small enough
    to read as rounding, large enough to slide an overlay off its footage by
    the end of a long clip. Asking for the number leaves no room for it."""
    plan = plan_overlay(runs([(0, 300, 0x8000, 0, 0)]), video_fps=60)
    argv = encode_argv("ffmpeg", "in.txt", "out.mov", plan)
    assert argv[argv.index("-frames:v") + 1] == str(plan.video_frames) == "600"


# --- round 32: Mario's facing is its own layer ------------------------------

def test_the_facing_layer_ignores_the_pad_entirely():
    plan = plan_overlay(runs([(0, 5, 0x8000, 40, 0, 1000),
                              (5, 5, 0x4000, -70, 12, 1000)]), layer="facing")
    assert plan.per_frame[0] == plan.per_frame[5]


def test_the_facing_layer_separates_two_different_bearings():
    plan = plan_overlay(runs([(0, 5, 0, 0, 0, 1000),
                              (5, 5, 0, 0, 0, 40000)]), layer="facing")
    assert plan.per_frame[0] != plan.per_frame[5]


def test_the_other_layers_ignore_the_facing():
    """Mario's yaw changes on nearly every moving frame, so folding it into
    the pad layers would multiply their distinct pictures by the length of the
    run -- a few dozen screenshots becoming a few thousand."""
    for layer in ("stick", "buttons"):
        plan = plan_overlay(runs([(0, 5, 0x8000, 40, 0, 1000),
                                  (5, 5, 0x8000, 40, 0, 40000)]), layer=layer)
        assert plan.per_frame[0] == plan.per_frame[5], layer


def test_a_run_with_no_facing_captured_still_plans():
    """A v1 chunk predates Mario's capture; 0 is what it honestly says."""
    plan = plan_overlay(runs([(0, 3, 0x8000, 0, 0)]), layer="facing")
    assert plan.states[plan.per_frame[0]] == (0, 0, 0, 0)

# --- the mapped script (round 32 item 17) -----------------------------------

def test_the_mapped_script_draws_what_each_video_frame_actually_shows():
    """A duplicate holds the same pad for two slots; a skipped game frame's
    pad never draws; a slot before the map's coverage or outside the track
    draws the blank. Every source picture retains its own interval."""
    from sm64_events.inputs.overlay import mapped_concat_script, plan_mapped_overlay
    runs = [{"start": 0, "length": 2, "buttons": 0x8000, "stick_x": 10,
             "stick_y": 0, "yaw": 0},
            {"start": 2, "length": 2, "buttons": 0x4000, "stick_x": 20,
             "stick_y": 0, "yaw": 0}]
    # raw 90 = axis 0. The map: a lead-in slot (null), frame 90 twice (the
    # duplicate), 92 (91 skipped), then past the track (94).
    frame_map = [None, 90, 90, 92, 94]
    plan = plan_mapped_overlay({"frame_map": frame_map,
        "frame_times": [n / 60 for n in range(5)], "duration_s": 5 / 60,
        "picture_states": [None, runs[0], runs[0], runs[1], None]})
    script = mapped_concat_script(plan, lambda index: f"s{index}.png")
    files = [line for line in script.splitlines() if line.startswith("file")]
    blank, first, second = "file 's0.png'", "file 's1.png'", "file 's2.png'"
    assert files == [blank, first, first, second, blank]
    #                 ^lead  ^90   ^dup   ^92     ^unknown


def test_the_mapped_encode_asks_for_the_maps_own_frame_count():
    from sm64_events.inputs.overlay import encode_argv, plan_overlay
    plan = plan_overlay([{"start": 0, "length": 2, "buttons": 0,
                          "stick_x": 0, "stick_y": 0, "yaw": 0}],
                        video_fps=60)
    argv = encode_argv("ffmpeg", "s", "o", plan, frames=123)
    assert argv[argv.index("-frames:v") + 1] == "123"
    plain = encode_argv("ffmpeg", "s", "o", plan)
    assert plain[plain.index("-frames:v") + 1] == str(plan.video_frames)
