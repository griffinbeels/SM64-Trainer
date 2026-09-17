import csv, hashlib, io, json, shutil, subprocess, threading, re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import av, numpy as np, pytest
from sm64_events.replay.packetmux import EncodedPicture, PacketFragmentMux, H264Format
from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink, PICTURE_TIME_BASE
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.fragments import FragmentReader
from sm64_events.replay.media import MediaRun
from sm64_events.replay.ring import SegmentRing
from sm64_events.replay.virtualmp4 import VirtualMp4

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/data/gpu-packets"
RATE = 48000


def fixture_packets():
    rows = list(csv.DictReader((FIXTURE / "packets.csv").open()))
    data = (FIXTURE / "witness.h264").read_bytes()
    pos = 0
    result = []
    for row in rows:
        size = int(row["bytes"])
        result.append(
            EncodedPicture(
                int(row["occurrence"]),
                int(row["pts"]),
                int(row["duration"]),
                bool(int(row["idr"])),
                data[pos : pos + size],
            )
        )
        pos += size
    assert pos == len(data)
    return result


def pcm(first, count=960):
    samples = np.arange(first, first + count)
    # Distinct channels and pulse envelope give clock/phase/side independent witnesses.
    envelope = np.where(((samples // 4800) % 2) == 0, 0.65, 0.25)
    left = np.rint(
        14000 * envelope * np.sin(samples * (2 * np.pi * 440 / RATE))
    ).astype("<i2")
    right = np.rint(
        12000 * envelope * np.sin(samples * (2 * np.pi * 660 / RATE))
    ).astype("<i2")
    return np.column_stack([left, right]).tobytes()


class Output(io.RawIOBase):
    def __init__(self, directory, run):
        self.archive = FragmentArchive(
            directory / "archive",
            SegmentRing(None, 16 * 1024 * 1024, scratch_root=directory / "archive"),
            run,
        )
        self.parser = FragmentReader()
        self.units = []
        self.path = directory / "progressive.mp4"
        self.file = self.path.open("wb")
        self.bytes = 0

    def writable(self):
        return True

    def write(self, data):
        # The actual archive accepts fractured headers/sample bytes.
        for offset in range(0, len(data), 317):
            chunk = data[offset : offset + 317]
            self.archive.feed(chunk)
            for unit in self.parser.feed(chunk):
                self.file.write(unit.data)
                self.file.flush()
                self.units.append(unit)
        self.bytes += len(data)
        return len(data)

    def flush(self):
        if not self.file.closed:
            self.file.flush()

    def finish(self):
        self.parser.finish()
        self.archive.finish()
        self.file.close()


def decode_video(path):
    with av.open(str(path)) as source:
        return [
            (
                int(frame.pts * frame.time_base * 90000),
                frame.to_ndarray(format="yuv420p"),
            )
            for frame in source.decode(video=0)
        ]


def decode_audio(path):
    with av.open(str(path)) as source:
        frames = [
            (round(frame.pts * frame.time_base * RATE), frame.to_ndarray())
            for frame in source.decode(audio=0)
        ]
    for (start, array), (next_start, _) in zip(frames, frames[1:], strict=False):
        assert next_start == start + array.shape[1]
    return frames[0][0], np.concatenate([array for _, array in frames], axis=1)


def reference_audio(directory, run, end_sample):
    nut = directory / "reference-audio.nut"
    with av.open(str(nut), "w", format="nut") as mux:
        stream = mux.add_stream("pcm_s16le", rate=RATE)
        stream.layout = "stereo"
        stream.time_base = stream.codec_context.time_base = PICTURE_TIME_BASE
        owner = SimpleNamespace(
            _cfg=SimpleNamespace(audio_rate=RATE),
            _run_epoch=run.origin_ts,
            _mux_lock=threading.Lock(),
            _mux_audio=stream,
            _mux=mux,
        )
        for first in range(-2400, end_sample, 960):
            count = min(960, end_sample - first)
            FfmpegAvSink._mux_audio_chunk(
                owner,
                pcm(first, count),
                round(run.origin_ts * 1e6 + first / RATE * 1e6),
            )
    args = [
        shutil.which("ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-copyts",
        "-f",
        "nut",
        "-i",
        str(nut),
        "-map",
        "0:a:0",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        "-ar",
        "48000",
        "-af",
        "aresample=async=1:first_pts=0:min_hard_comp=0.1",
        "-avoid_negative_ts",
        "disabled",
        str(directory / "reference-audio.m4a"),
    ]
    result = subprocess.run(
        args, capture_output=True, timeout=20, **quiet_spawn_kwargs()
    )
    (directory / "reference-cli.log").write_bytes(result.stderr)
    assert result.returncode == 0, result.stderr
    return directory / "reference-audio.m4a"


@pytest.mark.parametrize("native_format", [False, True])
@pytest.mark.parametrize("mutation", [None, "tail-duration", "audio-clock"])
def test_progressive_gpu_packets_and_audio(tmp_path, mutation, native_format):
    expected_packets = fixture_packets()
    packets = expected_packets.copy()
    if mutation == "tail-duration":
        packets[-1] = replace(packets[-1], duration=1)
    audio_shift_us = 20000 if mutation == "audio-clock" else 0
    run = MediaRun("packet-prototype", 1000.0)
    out = Output(tmp_path, run)
    # Header/template originates in this already encoded GPU fixture; no video encode/decode in the mux module.
    with av.open(str(FIXTURE / "witness.h264")) as source:
        source_video = source.streams.video[0]
        timing_owner = SimpleNamespace(
            _mux=SimpleNamespace(mux=lambda packet: None),
            _mux_lock=threading.Lock(),
            _mux_stream=source_video,
            _media_run=run,
            _last_video_pts=None,
        )
        raw_ticks = [0, 3001, 3001, 179999, 180000, 720000, 720000, 720000, 720000]
        assigned = [
            FfmpegAvSink._mux_picture(timing_owner, b"\0", run.origin_ts + tick / 90000)
            for tick in raw_ticks
        ]
        assert assigned == [p.pts for p in expected_packets]
        mux = PacketFragmentMux(
            out,
            H264Format(320, 240, 30) if native_format else source_video,
            run,
            audio_rate=RATE,
            audio_bitrate=160000,
            packet_limit=8 * 1024 * 1024,
            pcm_limit=384000,
        )
        if native_format:
            assert mux.video.codec_context is None
        end_tick = expected_packets[-1].pts + expected_packets[-1].duration
        end_sample = round(end_tick / 90000 * RATE)
        events = [(p.pts / 90000, "video", p) for p in packets]
        events += [
            (first / RATE, "audio", (first, min(960, end_sample - first)))
            for first in range(-2400, end_sample, 960)
        ]
        events.sort(key=lambda row: row[0])
        split = next(i for i, event in enumerate(events) if event[0] >= 2.4)
        supply(mux, events[:split], run, audio_shift_us)
        assert mux.video_count == 5 and not mux.closed
        coverage = out.archive.coverage()
        print(
            "PREFIX", coverage, "units", len(out.units), "video_sent", mux.video_count
        )
        assert coverage and coverage[0] == 0 and coverage[1] >= 90000
        prefix = tmp_path / "prefix.mp4"
        with out.archive.selection(0, min(180000, coverage[1])) as (
            init,
            tracks,
            units,
            _,
        ):
            virtual = VirtualMp4(init, tracks, units, 0, min(180000, coverage[1]))
            prefix.write_bytes(b"".join(virtual.chunks()))
        prefix_video = decode_video(prefix)
        prefix_audio = decode_audio(prefix)
        assert (
            prefix_video
            and prefix_video[0][0] == 0
            and prefix_audio[1].shape[1] >= RATE
        )
        supply(mux, events[split:], run, audio_shift_us)
        mux.close()
        out.finish()
    verify_video(out, packets, expected_packets, mutation)
    verify_audio(tmp_path, run, end_sample, mux, out, mutation)


def supply(mux, events, run, audio_shift_us):
    for _, kind, item in events:
        if kind == "video":
            mux.write_video(item)
        else:
            first, count = item
            mux.write_pcm(
                pcm(first, count),
                round(run.origin_ts * 1e6 + first / RATE * 1e6) + audio_shift_us,
            )


def verify_video(out, packets, expected_packets, mutation):
    decoded = decode_video(out.path)
    assert [tick for tick, _ in decoded] == [p.pts for p in packets]
    with av.open(str(FIXTURE / "witness.h264")) as original:
        original_pixels = [
            frame.to_ndarray(format="yuv420p") for frame in original.decode(video=0)
        ]
    assert all(
        np.array_equal(actual, expected)
        for (_, actual), expected in zip(decoded, original_pixels, strict=True)
    )
    with av.open(str(out.path)) as media:
        output_packets = [
            {
                "pts": p.pts,
                "duration": p.duration,
                "key": p.is_keyframe,
                "tb": str(p.time_base),
            }
            for p in media.demux(video=0)
            if p.size
        ]
    expected_metadata = [
        dict(pts=p.pts, duration=p.duration, key=p.key, tb="1/90000")
        for p in expected_packets
    ]
    if mutation == "tail-duration":
        assert (
            output_packets != expected_metadata and output_packets[-1]["duration"] == 1
        )
    else:
        assert output_packets == expected_metadata
    # Independently read MP4 length-prefixed NAL bytes; every original SPS/PPS/VCL byte must survive.
    with av.open(str(out.path)) as source:
        encoded = [bytes(p) for p in source.demux(video=0) if p.size]
    for actual, original in zip(encoded, packets, strict=True):
        expected_nals = [
            nal for nal in re.split(b"\x00\x00\x00?\x01", original.data) if nal
        ]
        nals = []
        offset = 0
        while offset < len(actual):
            size = int.from_bytes(actual[offset : offset + 4], "big")
            offset += 4
            assert 0 < size <= len(actual) - offset
            nals.append(actual[offset : offset + size])
            offset += size
        assert nals == expected_nals
    actual_end = packets[-1].pts + packets[-1].duration
    assert out.archive.coverage() == (0, actual_end)
    with out.archive.selection(0, actual_end) as (_, tracks, units, _):
        index_packets = [
            (sample.pts, sample.duration, sample.key)
            for unit in units
            for sample in unit.samples
            if tracks[sample.track_id].kind == "video"
        ]
    assert index_packets == [(p.pts, p.duration, p.key) for p in packets]


def verify_audio(tmp_path, run, end_sample, mux, out, mutation):
    reference = reference_audio(tmp_path, run, end_sample)
    first, audio = decode_audio(out.path)
    other_start, other = decode_audio(reference)
    low = max(first, other_start)
    high = min(first + audio.shape[1], other_start + other.shape[1])
    delta = (
        audio[:, low - first : high - first]
        - other[:, low - other_start : high - other_start]
    )
    reference_samples = (
        np.frombuffer(pcm(0, end_sample), dtype="<i2").reshape(-1, 2).T.astype(float)
        / 32768
    )
    source_low = max(0, first)
    source_high = min(end_sample, first + audio.shape[1])
    actual = audio[:, source_low - first : source_high - first]
    target = reference_samples[:, source_low:source_high]
    correlation = [float(np.corrcoef(actual[c], target[c])[0, 1]) for c in range(2)]

    def packet_info(path):
        with av.open(str(path)) as source:
            return [
                dict(
                    pts=p.pts,
                    dts=p.dts,
                    duration=p.duration,
                    tb=str(p.time_base),
                    sha=hashlib.sha256(bytes(p)).hexdigest(),
                    side=[
                        (str(d.data_type), bytes(d).hex()) for d in p.iter_sidedata()
                    ],
                )
                for p in source.demux(audio=0)
                if p.size
            ]

    apackets, rpackets = packet_info(out.path), packet_info(reference)
    packet_clock_equal = [
        {k: v for k, v in p.items() if k != "sha"} for p in apackets
    ] == [{k: v for k, v in p.items() if k != "sha"} for p in rpackets]
    report = dict(
        mutation=mutation,
        cli_audio_identical=bool(np.all(delta == 0)),
        cli_audio_max=float(np.abs(delta).max()),
        audio_source_correlation=correlation,
        audio_packet_clock_equal=packet_clock_equal,
        audio_input_samples=mux.audio_input_samples,
        audio_first_packet=apackets[0],
        audio_last_packet=apackets[-1],
        audio_packet_payload_differences=[
            i
            for i, (a, b) in enumerate(zip(apackets, rpackets, strict=False))
            if a["sha"] != b["sha"]
        ],
    )
    (tmp_path / "report.json").write_bytes(json.dumps(report, indent=2).encode())
    if mutation == "audio-clock":
        assert not report["cli_audio_identical"] and min(correlation) < 0.995
        assert mux.audio_input_samples == end_sample + 960
    else:
        assert mux.audio_input_samples == end_sample and first == 0
        assert packet_clock_equal and high - low >= end_sample and np.all(delta == 0)
        assert min(correlation) > 0.995
