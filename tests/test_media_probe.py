"""The in-process media reads answer what ffprobe answered, without ffprobe.

`replay/mediaprobe.py` replaced every ffprobe spawn because the packaged app
ships no ffprobe. Each case below is a real clip. The PyAV half pins what the
callers rely on and needs no ffprobe; the parity half compares every reading
with ffprobe's own output where one is installed. No expected number is
written here: ffmpeg makes the clips and ffprobe states their facts.
"""
import json
import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import av
import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.replay import mediaprobe

FIXTURES = Path(__file__).parent / "fixtures"
PICTURES = ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=1"]
TONE = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1"]
# Drops picture 0 too, so the first picture starts after zero.
VFR = ["-vf", "select='mod(n,7)'", "-fps_mode", "passthrough"]
# name -> (ffmpeg arguments, or a source and its damage; the input format)
CASES = {
    "h264.mp4": ([*PICTURES, *VFR, "-c:v", "libx264", "-bf", "0",
                  "-video_track_timescale", "90000"], None),
    "h264_bframes.mp4": ([*PICTURES, *VFR, "-c:v", "libx264", "-bf", "2",
                          "-video_track_timescale", "90000"], None),
    "av1.mp4": ([*PICTURES, *VFR, "-c:v", "libsvtav1", "-video_track_timescale", "90000"], None),
    "with_audio.mp4": ([*PICTURES, *TONE, *VFR, "-c:v", "libx264", "-bf", "0", "-c:a", "aac",
                        "-video_track_timescale", "90000", "-movflags", "+faststart"], None),
    "segment.ts": (["-f", "lavfi", "-i", "testsrc=size=320x240:rate=60:duration=2", *TONE,
                    "-c:v", "libx264", "-preset", "ultrafast", "-bf", "0", "-c:a", "aac",
                    "-f", "mpegts"], "mpegts"),
    "tiny_held.ts": (FIXTURES / "replay_tiny_held.ts", "mpegts"),
    # The extractor's native cut: keyframe pre-roll hidden by a 90 kHz edit list.
    "preroll.mp4": ((["-copyts", "-f", "mpegts", "-i", "{segment.ts}", "-ss", "1.4", "-t", "0.5",
                      "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "copy",
                      "-avoid_negative_ts", "disabled", "-movie_timescale", "90000",
                      "-output_ts_offset", "-0.25", "-use_editlist", "1",
                      "-movflags", "+faststart"]), None),
    "truncated.mp4": (("with_audio.mp4", "truncate"), None),
    "damaged.mp4": (("with_audio.mp4", "flip"), None),
}
NATIVE = ("h264.mp4", "av1.mp4", "with_audio.mp4", "segment.ts", "tiny_held.ts")


def _damaged(source: Path, target: Path, how: str) -> None:
    data = bytearray(source.read_bytes())
    if how == "truncate":
        data = data[: len(data) * 7 // 10]     # faststart: the index survives, samples do not
    else:
        with av.open(str(source)) as container:
            spans = [(p.pos, p.size) for p in container.demux(video=0) if p.size]
        at, size = spans[len(spans) // 2]      # one picture's payload, mid-clip
        for offset in range(at + 8, at + size, 3):
            data[offset] ^= 0xFF
    target.write_bytes(bytes(data))


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")   # without one, the stored fixture still runs
    folder = tmp_path_factory.mktemp("mediaprobe")
    made = {}
    for name, (recipe, input_format) in CASES.items():
        path = folder / name
        if isinstance(recipe, Path):
            path = recipe
        elif isinstance(recipe, tuple):
            if recipe[0] not in made:
                continue
            _damaged(made[recipe[0]][0], path, recipe[1])
        else:
            sources = [a[1:-1] for a in recipe if a.startswith("{")]
            if ff is None or not all(source in made for source in sources):
                continue
            args = [str(made[a[1:-1]][0]) if a.startswith("{") else a for a in recipe]
            built = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args, str(path)],
                                   capture_output=True, timeout=60, **quiet_spawn_kwargs())
            if built.returncode:
                continue                       # an encoder this ffmpeg lacks (libsvtav1)
        made[name] = (path, input_format)
    return made


def _case(clips, name):
    if name not in clips:
        pytest.skip(f"{name}: this ffmpeg cannot make it")
    return clips[name]


# -- the PyAV half: always runs ----------------------------------------------

@pytest.mark.parametrize("name", NATIVE)
def test_native_packets_carry_the_decoded_picture_times(clips, name):
    path, input_format = _case(clips, name)
    decoded = mediaprobe.frame_times_of(path, input_format=input_format)
    packets = mediaprobe.native_packet_index(path, input_format=input_format)
    assert decoded and packets
    assert [round(p.pts / 90000, 6) for p in packets] == decoded
    assert mediaprobe.frame_times_of(path, input_format=input_format, native_packets=True) == decoded


def test_reordered_media_keeps_decoder_semantics(clips):
    path, _ = _case(clips, "h264_bframes.mp4")
    assert mediaprobe.probe(path).video.has_b_frames
    assert mediaprobe.native_packet_index(path) is None
    assert mediaprobe.frame_times_of(path, native_packets=True) == mediaprobe.frame_times_of(path)


def test_the_native_cut_hides_only_its_negative_preroll(clips):
    path, _ = _case(clips, "preroll.mp4")
    assert mediaprobe.native_packet_index(path) is None, "pre-roll refuses unless allowed"
    packets = mediaprobe.native_packet_index(path, allow_preroll=True)
    hidden = [p for p in packets if p.discard]
    assert hidden and all(p.pts < 0 for p in hidden) and packets[0].keyframe
    assert mediaprobe.frame_times_of(path, native_packets=True, native_preroll=True) == (
        mediaprobe.frame_times_of(path))


def test_audio_presence_and_the_first_picture(clips):
    silent, _ = _case(clips, "h264.mp4")
    voiced, _ = _case(clips, "with_audio.mp4")
    assert mediaprobe.probe(silent).has_audio is False
    assert mediaprobe.probe(voiced).has_audio is True
    for path in (silent, voiced):
        start = mediaprobe.video_start_of(path)
        assert start > 0, "the fixture drops picture 0, so the video starts after zero"
        assert start == mediaprobe.frame_times_of(path)[0]


def test_a_truncated_clip_refuses_the_packet_shortcut_and_decodes_what_remains(clips):
    path, _ = _case(clips, "truncated.mp4")
    whole, _ = _case(clips, "with_audio.mp4")
    assert mediaprobe.native_packet_index(path) is None
    remaining = mediaprobe.frame_times_of(path)
    assert remaining and len(remaining) < len(mediaprobe.frame_times_of(whole))


def test_a_damaged_picture_is_skipped_by_the_decoded_clock(clips):
    path, _ = _case(clips, "damaged.mp4")
    packets = mediaprobe.native_packet_index(path)
    decoded = mediaprobe.frame_times_of(path)
    # The documented narrowing (mediaprobe's docstring): payload damage keeps
    # its packet times, which are the file's own; the decoder drops pictures.
    assert packets and decoded and len(decoded) < len(packets)


def test_an_unreadable_file_reads_as_none(tmp_path):
    missing = tmp_path / "missing.mp4"
    assert mediaprobe.probe(missing) is None
    assert mediaprobe.frame_times_of(missing) is None
    assert mediaprobe.native_packet_index(missing) is None
    assert mediaprobe.video_start_of(missing) == 0.0


# -- no ffprobe anywhere --------------------------------------------------------

@pytest.fixture
def no_ffprobe(monkeypatch):
    """The installed app's world: ffmpeg at a known path and no ffprobe on
    PATH or to `shutil.which`. Any attempt to start one is recorded in
    `attempts` and refused, wherever it was found (a sibling of ffmpeg, too)."""
    world = SimpleNamespace(ffmpeg=bundled_ffmpeg() or shutil.which("ffmpeg"), attempts=[])
    keep = [d for d in os.environ.get("PATH", "").split(os.pathsep)
            if not any((Path(d) / n).exists() for n in ("ffprobe", "ffprobe.exe"))]
    monkeypatch.setenv("PATH", os.pathsep.join(keep))
    which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k:
                        None if Path(str(name)).name.lower().startswith("ffprobe")
                        else which(name, *a, **k))

    class Guarded(subprocess.Popen):
        def __init__(self, args, *rest, **kwargs):
            program = os.fsdecode(args if isinstance(args, (str, bytes, os.PathLike)) else args[0])
            if Path(program).name.lower().startswith("ffprobe"):
                world.attempts.append(args)
                raise FileNotFoundError(2, "ffprobe is not shipped", program)
            super().__init__(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", Guarded)
    return world


def test_the_shipped_reads_need_no_ffprobe(clips, no_ffprobe):
    path, _ = _case(clips, "with_audio.mp4")
    start = mediaprobe.video_start_of(path)
    times = mediaprobe.frame_times_of(path)
    assert start > 0 and times and times[0] == start
    assert mediaprobe.frame_times_of(path, native_packets=True) == times
    assert mediaprobe.probe(path).has_audio
    assert no_ffprobe.attempts == []


def test_the_guard_refuses_ffprobe_however_it_is_named(no_ffprobe, tmp_path):
    with pytest.raises(FileNotFoundError):
        subprocess.run([str(tmp_path / "bin" / "ffprobe.EXE"), "-version"])
    assert shutil.which("ffprobe") is None and len(no_ffprobe.attempts) == 1


# -- the parity half: what ffprobe itself reports ----------------------------------

def _ffprobe():
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        pytest.skip("no ffprobe to compare against")
    return ffprobe


def _ffprobe_json(path, input_format, *entries):
    out = subprocess.run([_ffprobe(), "-v", "error", *(["-f", input_format] if input_format else []),
                          *entries, "-of", "json", str(path)],
                         capture_output=True, text=True, timeout=120, check=False,
                         **quiet_spawn_kwargs())
    return json.loads(out.stdout), out.stderr.strip()


def _flags(packet):
    return (("K" if packet.keyframe else "_") + ("D" if packet.discard else "_")
            + ("C" if packet.corrupt else "_"))


@pytest.mark.parametrize("name", list(CASES))
def test_stream_facts_match_ffprobe(clips, name):
    path, input_format = _case(clips, name)
    found, _ = _ffprobe_json(path, input_format, "-select_streams", "v:0", "-show_entries",
                             "stream=codec_name,has_b_frames,time_base,start_time,duration,"
                             "nb_frames,width,height,r_frame_rate")
    audio, _ = _ffprobe_json(path, input_format, "-select_streams", "a:0",
                             "-show_entries", "stream=index")
    media = mediaprobe.probe(path, input_format=input_format)
    stream, video = found["streams"][0], media.video
    seconds = lambda key: float(stream[key]) if key in stream else None
    assert (video.codec_name, video.has_b_frames, video.time_base) == (
        stream["codec_name"], bool(stream["has_b_frames"]), Fraction(stream["time_base"]))
    assert (video.start_s, video.duration_s) == (seconds("start_time"), seconds("duration"))
    assert (video.frames, video.width, video.height, video.rate) == (
        int(stream.get("nb_frames", 0)), stream["width"], stream["height"],
        Fraction(stream["r_frame_rate"]))
    assert media.has_audio is bool(audio["streams"])


@pytest.mark.parametrize("name", list(CASES))
def test_every_packet_matches_ffprobe(clips, name):
    path, input_format = _case(clips, name)
    found, _ = _ffprobe_json(path, input_format, "-select_streams", "v:0",
                             "-show_entries", "packet=pts,dts,flags")
    _video, packets, _indexed = mediaprobe._read_packets(path, input_format)
    assert [(p.pts, p.dts, _flags(p)) for p in packets] == [
        (p.get("pts"), p.get("dts"), p["flags"]) for p in found["packets"]]


@pytest.mark.parametrize("name", list(CASES))
def test_decoded_picture_times_match_ffprobe(clips, name):
    path, input_format = _case(clips, name)
    out = subprocess.run([_ffprobe(), "-v", "error", "-select_streams", "v:0",
                          *(["-f", input_format] if input_format else []),
                          "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, timeout=120, check=False,
                         **quiet_spawn_kwargs())
    assert out.returncode == 0
    assert mediaprobe.frame_times_of(path, input_format=input_format) == [
        float(line.split(",")[0]) for line in out.stdout.split() if line.strip()]


def test_a_damaged_native_picture_keeps_its_slot_at_ffprobes_packet_time(clips):
    """The narrowing in mediaprobe's docstring, pinned to what happens: ffprobe
    logged this clip's payload damage, so its packet probe refused the clip.
    The packet index now accepts it, at exactly ffprobe's packet times, so
    the damaged picture keeps its slot; only the decoded clock drops it."""
    path, _ = _case(clips, "damaged.mp4")
    found, stderr = _ffprobe_json(path, None, "-select_streams", "v:0",
                                  "-show_entries", "packet=pts")
    assert stderr, "the damage must be one ffprobe logged, or this pins nothing"
    packets = mediaprobe.native_packet_index(path)
    assert packets and [p.pts for p in packets] == [p["pts"] for p in found["packets"]]
    slots = mediaprobe.frame_times_of(path, native_packets=True)
    assert len(slots) == len(packets) > len(mediaprobe.frame_times_of(path))


@pytest.mark.parametrize("name,ffprobe_complained", [
    ("truncated.mp4", True),    # "partial file": both refuse the packet shortcut
    ("damaged.mp4", True),      # a decoder error ffprobe logged; the narrowing
    *((name, False) for name in NATIVE),
])
def test_where_ffprobe_logged_an_error(clips, name, ffprobe_complained):
    """ffprobe's packet probe refused whenever it printed an error. This pins
    which clips that was, beside what the structural refusal decides."""
    path, input_format = _case(clips, name)
    _, stderr = _ffprobe_json(path, input_format, "-select_streams", "v:0",
                              "-show_entries", "stream=codec_name:packet=pts")
    assert bool(stderr) is ffprobe_complained
    refused = mediaprobe.native_packet_index(path, input_format=input_format) is None
    assert refused is (name == "truncated.mp4")
