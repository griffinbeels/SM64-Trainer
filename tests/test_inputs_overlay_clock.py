"""The exported movie keeps picture occurrence, gaps, and its final hold."""
import json
from pathlib import Path
import shutil
import subprocess

from PIL import Image
import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.inputs import overlay


def view():
    return {"frame_map": [100, None, 100], "frame_times": [0.125, 0.25, 1.123456],
            "duration_s": 2.5, "picture_states": [
                {"buttons": 0x8000, "stick_x": 40, "stick_y": 0, "yaw": 0}, None,
                {"buttons": 0x4000, "stick_x": -40, "stick_y": 0, "yaw": 100}]}


def test_repeated_counter_uses_its_own_source_state_and_unknown_stays_blank():
    plan = overlay.plan_mapped_overlay(view())
    assert [plan.states[i] for i in plan.per_frame] == [
        None, (0x8000, 40, 0, 0), None, (0x4000, -40, 0, 100)]
    assert plan.frame_times == (0, 0.125, 0.25, 1.123456)
    assert plan.duration_s == 2.5
    assert plan.video_frames == 4
    script = overlay.mapped_concat_script(plan, lambda i: f"s{i}.png")
    durations = [float(line.split()[1]) for line in script.splitlines() if line.startswith("duration ")]
    assert durations == pytest.approx([0.125, 0.125, 0.873456, 1.376544])


@pytest.mark.parametrize("field,value", [("picture_states", None), ("picture_states", []),
    ("frame_times", None), ("frame_times", [0, 0, 1]), ("duration_s", 1),
    ("duration_s", float("nan")), ("frame_map", None)])
def test_mapped_export_refuses_missing_or_invalid_evidence(field, value):
    data = view()
    data[field] = value
    with pytest.raises(ValueError):
        overlay.plan_mapped_overlay(data)


def test_neutral_capture_is_distinct_from_unknown_capture():
    data = view()
    data["picture_states"][0] = dict(buttons=0, stick_x=0, stick_y=0, yaw=0)
    plan = overlay.plan_mapped_overlay(data)
    assert plan.states[plan.per_frame[0]] is None
    assert plan.states[plan.per_frame[1]] == (0, 0, 0, 0)


def test_plain_export_fps_cannot_quantize_a_mapped_picture_clock():
    assert overlay.plan_mapped_overlay(view(), video_fps=50).frame_times == (0, 0.125, 0.25, 1.123456)


@pytest.mark.parametrize("layer,expected", [("stick", (0, 40, 0, 0)),
    ("buttons", (0x8000, 0, 0, 0)), ("combined", (0x8000, 40, 0, None)), ("facing", None)])
def test_missing_mario_state_keeps_pad_capture_without_inventing_facing(layer, expected):
    data = view()
    data["picture_states"][0]["yaw"] = None
    plan = overlay.plan_mapped_overlay(data, layer=layer)
    assert plan.states[plan.per_frame[1]] == expected


@pytest.mark.parametrize("codec", ["qtrle", "prores4444"])
def test_encoded_vfr_movie_preserves_picture_times_and_final_hold(tmp_path, codec):
    # Four 16px images, no browser/emulator/recorder. Probe the actual MOV
    # packets: a correct concat string alone cannot prove final-packet duration.
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    probe = str(Path(ff).with_name("ffprobe.exe")) if ff else None
    if not ff or not Path(probe).exists():
        pytest.skip("ffmpeg/ffprobe not available")
    plan = overlay.plan_mapped_overlay(view(), codec=codec)
    for i, state in enumerate(plan.states):
        Image.new("RGBA", (16, 16), (i * 70, 0, 0, 0 if state is None else 255)).save(tmp_path / f"s{i}.png")
    script = tmp_path / "frames.ffconcat"
    script.write_text(overlay.mapped_concat_script(plan, lambda i: f"s{i}.png"))
    out = tmp_path / "out.mov"
    subprocess.run(overlay.encode_argv(str(ff), str(script), str(out), plan),
                   check=True, capture_output=True, timeout=20, **quiet_spawn_kwargs())
    result = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_packets",
        "-show_streams", "-of", "json", str(out)], check=True, capture_output=True,
        timeout=20, **quiet_spawn_kwargs())
    evidence = json.loads(result.stdout)
    packets = evidence["packets"]
    assert [float(p["pts_time"]) for p in packets] == pytest.approx(plan.frame_times, abs=0.000001)
    assert float(packets[-1]["duration_time"]) == pytest.approx(1.376544, abs=0.000001)
    assert float(evidence["streams"][0]["duration"]) == pytest.approx(2.5, abs=0.000001)
    if codec == "qtrle":
        decoded = subprocess.run([str(ff), "-v", "error", "-i", str(out),
            "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
            check=True, capture_output=True, timeout=20, **quiet_spawn_kwargs()).stdout
        stride = 16 * 16 * 4
        assert len(decoded) == stride * len(plan.per_frame)
        assert [tuple(decoded[n * stride:n * stride + 4]) for n in range(4)] == [
            (0, 0, 0, 0), (70, 0, 0, 255), (0, 0, 0, 0), (140, 0, 0, 255)]
