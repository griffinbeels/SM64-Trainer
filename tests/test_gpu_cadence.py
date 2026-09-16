"""Autonomous actual source + NVENC: media stalls must not consume source slots.

Unlike the small command-driven delivery fixture, this links stamp_adapter.cpp
and never substitutes its refusal frontier. Only the fixture RAM decoder and
final memory archive replace application boundaries.
"""

import ctypes
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay import gpuchannel
from sm64_events.replay.channelencoder import ChannelEncoder
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.feedmap import feed_map
from sm64_events.replay.gpuinput import CapturedOffer, ChannelSelection
from sm64_events.replay.gpumedia import GpuMedia
from sm64_events.replay.gpumediaworker import MediaWorker
from sm64_events.replay.gpuprocess.process_controller import Controller
from sm64_events.replay.gpuprocess.retirement import close_encoder
from sm64_events.replay.gpusettings import GpuSettings
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import H264Format, PacketFragmentMux
from sm64_events.replay.pixels import SampledPicture
from test_gpu_delivery import Running, eventually

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin/gfxwrap"
ENCODER = ROOT / "src/sm64_events/data/plugin/SM64GpuEncoderV1.dll"
pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("SM64_TEST_GPU_BRIDGE") != "1",
    reason="explicit Windows NVIDIA witness: set SM64_TEST_GPU_BRIDGE=1",
)


@pytest.fixture(scope="module")
def cadence_host(tmp_path_factory):
    try:
        ctypes.WinDLL("nvEncodeAPI64.dll")
    except OSError:
        pytest.skip("NVIDIA encoder driver unavailable")
    if not ENCODER.is_file():
        pytest.skip("built GPU encoder DLL unavailable")
    spec = importlib.util.spec_from_file_location("cadence_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vc = build.find_vcvars32()
    if vc is None:
        pytest.skip("x86 MSVC toolchain unavailable")

    def quiet_run(*args, **kwargs):
        kwargs.update(quiet_spawn_kwargs())
        return subprocess.run(*args, **kwargs)

    build.subprocess = NS(run=quiet_run)
    out = tmp_path_factory.mktemp("gpu-cadence")
    target = out / "cadence.exe"
    sources = [NATIVE / name for name in (
        "gpu_cadence_host.cpp", "gpu_delivery.cpp", "gpu_channel.cpp",
        "gpu_delivery_context.cpp", "gpu_bridge.cpp", "source_snapshot.cpp",
        "gl_snapshot.cpp", "gpu_selection.cpp", "renderer_boundary.cpp",
        "context_lifetime.cpp", "stamp_adapter.cpp",
    )]
    inputs = [*sources, *sorted(NATIVE.glob("*.h"))]
    def fingerprints():
        return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in inputs}
    before = fingerprints()
    flags = [flag for flag in build.COMMON_FLAGS if not flag.startswith("/std:")]
    build._cl(vc, flags + ["/std:c++17", "/EHsc", f"/I{NATIVE}",
        *map(str, sources), f"/Fe:{target}", f"/Fo{out}\\", "/link",
        "/EXPORT:SM64ReplaySourceV2", *build.LIBS, "d3d11.lib", "dxgi.lib", "bcrypt.lib"], out)
    assert fingerprints() == before, "native inputs changed while the fixture compiled"
    (out / "build_identity.json").write_text(json.dumps(dict(
        source_files=before, host_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        encoder_sha256=hashlib.sha256(ENCODER.read_bytes()).hexdigest(),
        source_checkout=str(ROOT), compiler_environment=str(vc),
    ), indent=2), encoding="utf-8")
    return target


class MemoryArchive:
    def __init__(self):
        self.blocks = []
        self.error = None

    def write(self, data):
        self.blocks.append(bytes(data))
        return len(data)

    feed = write

    def finish(self, error=None):
        self.error = error


class FixtureDecoder:
    namespace = "autonomous-native-fixture"

    def __init__(self, header):
        self.header = header

    def timestamp(self, qpc):
        return qpc / self.header.qpc_frequency

    def decode(self, offer):
        assert offer.lengths[:2] == (4, 8) and len(offer.stamp_bytes) == 12
        marker = int.from_bytes(offer.stamp_bytes[:4], "little")
        assert offer.stamp_bytes[4] == (255 - marker) & 255 and offer.stamp_bytes[8] == 0xA5
        return CapturedOffer(
            SampledPicture(offer.width, offer.height, offer.sample, 8),
            NS(frame=marker, extras=lambda: {"exact": True, "pad": [marker, 0, 1]}),
            self.timestamp(offer.boundary_qpc),
        )


def decoded_output(data, run, ledger, *, frames=60, audio_samples=96000):
    """Read actual pixels/PTS/audio independently of source and encoder claims."""
    import av

    ticks = []
    with av.open(io.BytesIO(data), format="mp4") as container:
        for index, picture in enumerate(container.decode(video=0)):
            marker = 30 + index
            expected = np.rint(np.array([marker % 251, (marker * 7 + marker // 251 * 31) % 251,
                                        marker * 17 % 251]) / 250 * 255)
            actual = picture.to_ndarray(format="rgb24")[120, 160].astype(float)
            assert np.max(np.abs(actual - expected)) <= 8, (marker, actual, expected)
            ticks.append(round(picture.pts * picture.time_base * 90000))
    assert len(ticks) == frames
    rows, feeds = ledger.rows_between(0, 1e10), ledger.feeds_between(0, 1e10)
    mapped_frames, repeats, mapping = feed_map(ticks, run.id, rows, feeds, lambda row: row["frame"])
    assert mapped_frames == list(range(30, 30 + len(ticks))) and not any(repeats), mapping
    assert [row["pad"][0] for row in rows] == list(range(30, 30 + len(ticks)))
    with av.open(io.BytesIO(data), format="mp4") as container:
        audio = list(container.decode(audio=0))
    signal = np.concatenate([frame.to_ndarray() for frame in audio], axis=1)
    assert audio_samples <= signal.shape[1] <= audio_samples + 2048
    magnitudes = np.abs(np.fft.rfft(signal[0]))
    peak_hz = np.fft.rfftfreq(signal.shape[1], 1 / 48000)[magnitudes.argmax()]
    assert abs(peak_hz - 440) < 2
    audio_ticks = [(frame.pts, str(frame.time_base), frame.samples) for frame in audio]
    return signal, audio_ticks, dict(source_pts_map=mapping, decoded_ticks=ticks,
                                   decoded_audio_samples=signal.shape[1])


class Pipeline:
    """One test-owned source, helper, media owner and bounded recording."""

    def __init__(self, host, output, *, isolated, delay_s, dimensions=(320, 240),
                 frames=60, vi_per_picture=1, presentation=False):
        self.label = f"{'worker' if isolated else 'synchronous'}-{delay_s:.3f}"
        self.evidence = dict(variant=self.label, source_frames=60, cadence_ms=33,
                            delay_s=delay_s, terminal=None, observed_delays=[])
        self.output, self.isolated, self.delay_s = output, isolated, delay_s
        self.dimensions, self.frames, self.vi_per_picture = dimensions, frames, vi_per_picture
        self.stream_seconds = 2 if vi_per_picture == 1 else frames / 30
        self.audio_samples = round(self.stream_seconds * 48000)
        self.native = Running(host)
        assert self.native.command(f"fixture {dimensions[0]} {dimensions[1]} {int(presentation)}") == "fixture configured"
        self.evidence.update(source_frames=frames, dimensions=dimensions, vi_per_picture=vi_per_picture,
                             cadence_ms=33 if vi_per_picture==1 else 1000/(30*vi_per_picture),
                             source_thread="dedicated synchronous command worker", presentation=presentation)
        self.client = self.controller = self.mux = self.media = self.sink = None
        self.archive, self.ledger, self.settings = MemoryArchive(), PictureLedger(), GpuSettings()
        self.options = dict(audio_rate=48000, audio_bitrate=160000,
                            packet_limit=1 << 20, pcm_limit=1 << 20)
        self.cursor = 0

    def open(self):
        self.client = self.native.start(1, encoder_ready=False)
        h = self.client.header
        self.controller = Controller(executable=Path(sys.executable),
            helper=ROOT / "src/sm64_events/replay/gpuprocess/helper_bootstrap.py",
            limits=self.settings.helper())
        self.command = dict(op="Open", dll_path=str(ENCODER),
            adapter_luid=[h.luid_high, h.luid_low], names=list(self.client.texture_names),
            options=self.settings.encoder(h, ReplayConfig(), nominal_rate=30))
        self.request_id = self.controller.enqueue(self.command)
        self.reply = eventually(self.controller.take_result, timeout=5)
        assert self.reply.metadata["result"] == 0 and not self.reply.metadata["error"], self.reply
        self.evidence["native_identity"] = self.reply.metadata.get("identity")
        if self.isolated:
            self.sink = MediaWorker(H264Format(*self.dimensions, 30), self.ledger, lambda run: self.archive,
                **self.options, max_bytes=4 << 20, max_blocks=256, max_age=2)
            eventually(self.sink_ready, timeout=5)

    def sink_ready(self):
        self.sink.check()
        return self.sink.ready

    def start(self):
        self.client.encoder_ready()
        assert self.native.command("active") == "active"
        command = (f"stream {self.frames} 33" if self.vi_per_picture == 1 else
                   f"stream_vi {self.frames * self.vi_per_picture} {30 * self.vi_per_picture} {self.vi_per_picture}")
        assert self.native.command(command) == "stream started"
        self.started = time.monotonic()
        initial = eventually(self.client.offers)
        decoder = FixtureDecoder(self.client.header)
        self.run = MediaRun(self.label, decoder.timestamp(initial[0].boundary_qpc))
        media_ledger, media_options = self.ledger, {}
        if self.sink is None:
            self.mux = PacketFragmentMux(self.archive, H264Format(*self.dimensions, 30), self.run, **self.options)
        else:
            self.sink.bind(self.run)
            self.mux = self.sink
            media_ledger = self.ledger.selection_only(self.sink.add_row)
            media_options["publish_picture"] = self.sink.publish_picture
        self.media = GpuMedia(self.run, media_ledger, self.mux, source_namespace=decoder.namespace,
            encoder_duration=3000, packet_limit=1 << 20, pending_count=8,
            pending_bytes=4 << 20, max_age=2, pcm_bytes=1 << 20, pcm_blocks=256,
            lead_ticks=9000, **media_options)
        self.adapter = ChannelEncoder(ChannelSelection(self.client, decoder, self.media), self.controller,
                                      max_pending=8, max_age=2, defer_disposal=True)
        self.adapter.attach_open(self.command, self.request_id, self.reply,
                                 initial_offers=initial, now=time.monotonic())

    def audio_until(self, now):
        audio_end = min(self.audio_samples, int((now - self.started) * 48000))
        while self.cursor + 960 <= audio_end:
            phase = np.arange(self.cursor, self.cursor + 960) / 48000
            tone = np.rint(np.sin(phase * 2 * np.pi * 440) * 10000).astype(np.int16)
            self.media.audio(np.column_stack((tone, tone)).tobytes(),
                round((self.run.origin_ts + self.cursor / 48000) * 1e6), now=now)
            self.cursor += 960

    def pump(self):
        while time.monotonic() - self.started < self.stream_seconds + .25:
            now = time.monotonic()
            try:
                self.audio_until(now)
                self.adapter.pump(now=now)
                self.media.drain()
                if self.sink is not None:
                    self.sink.check()
            except gpuchannel.ChannelStopped as exc:
                self.evidence["terminal"] = str(exc)
                break
            time.sleep(.002)

    def result(self):
        evidence = self.evidence
        # A native terminal can end the consumer before this autonomous stream
        # finishes. Retain the source's final counters even after an early fault.
        evidence["stream"] = self.native.lines.get(timeout=self.stream_seconds + 5)
        assert evidence["stream"] is not None, evidence
        evidence["native_status"] = self.native.command("status")
        fields = evidence["stream"].split()
        assert fields[:3] == ["stream", "done", str(self.frames * self.vi_per_picture)] and fields[4] == "0", evidence
        if self.delay_s and not self.isolated:
            assert int(fields[3]) > 0, "blocking control did not reach actual source capacity"
            assert "10022" in evidence["native_status"] and evidence["terminal"], evidence
            return None
        assert fields[3:5] == ["0", "0"] and evidence["terminal"] is None, evidence
        assert self.media.seal(self.adapter.frontier) and self.media.finish(audio_drained=True)
        if self.sink is not None:
            self.sink.finish(timeout=3)
            evidence["sink"] = self.sink.status()
        assert self.media.delivered == self.mux.video_count == self.frames and self.cursor == self.audio_samples
        data = b"".join(self.archive.blocks)
        (self.output / f"{self.label}.mp4").write_bytes(data)
        signal, audio_ticks, decoded = decoded_output(data, self.run, self.ledger,
                                                      frames=self.frames, audio_samples=self.audio_samples)
        evidence.update(decoded)
        return signal, audio_ticks

    def close_helper(self):
        if self.controller is None:
            return
        self.evidence["encoder_close"] = close_encoder(self.controller, timeout=3, poll_s=.002)
        self.controller.stop("autonomous fixture complete")
        deadline = time.monotonic() + 3
        while not self.controller.status()["done"] and time.monotonic() < deadline:
            self.controller.take_result()
            time.sleep(.005)
        self.evidence["helper"] = self.controller.status()

    def close(self):
        if self.native.process.poll() is None:
            self.native.command("stop")
        self.close_helper()
        if self.media is not None and not self.media.closed:
            self.media.abort("autonomous fixture complete")
        elif self.media is None and self.mux is not None and not self.mux.closed:
            self.mux.abort()
        if self.sink is not None:
            self.sink.finish(timeout=3)
        if self.client is not None:
            self.client.close()
        try:
            assert self.native.command("settled") == "settled"
            assert self.native.command("exit") == "exit"
            assert self.native.process.wait(timeout=3) == 0
        finally:
            if self.native.process.poll() is None:
                self.native.process.kill()
            self.native.process.wait(timeout=3)
            self.evidence["stderr"] = self.native.process.stderr.read()
            (self.output / f"{self.label}.json").write_text(json.dumps(self.evidence, indent=2, default=str), encoding="utf-8")
            self.native.close()
        assert self.evidence.get("encoder_close") is True
        helper = self.evidence["helper"]
        assert helper["done"] and helper["disposal"]["empty"] and helper["disposal"]["handle_closed"]


def run_case(host, output, monkeypatch, *, isolated, delay_s):
    pipeline = Pipeline(host, output, isolated=isolated, delay_s=delay_s)
    original_write = PacketFragmentMux.write_video

    def delayed_write(mux, picture):
        if delay_s and not pipeline.evidence["observed_delays"]:
            before = time.monotonic()
            time.sleep(delay_s)  # Controlled media work; the native source keeps advancing.
            pipeline.evidence["observed_delays"].append(time.monotonic() - before)
        return original_write(mux, picture)

    monkeypatch.setattr(PacketFragmentMux, "write_video", delayed_write)
    try:
        pipeline.open()
        pipeline.start()
        pipeline.pump()
        return pipeline.result()
    finally:
        pipeline.close()


def test_autonomous_source_survives_media_stall_without_losing_identity_or_audio(
    cadence_host, tmp_path, monkeypatch,
):
    with monkeypatch.context() as patch:
        baseline = run_case(cadence_host, tmp_path, patch, isolated=False, delay_s=0)
    with monkeypatch.context() as patch:
        assert run_case(cadence_host, tmp_path, patch, isolated=False, delay_s=.350) is None
    with monkeypatch.context() as patch:
        candidate = run_case(cadence_host, tmp_path, patch, isolated=True, delay_s=.350)
    assert np.array_equal(candidate[0], baseline[0])
    assert candidate[1] == baseline[1], "audio PTS/sample intervals changed through the worker"


@pytest.mark.parametrize("dimensions,presentation", [((320, 240), False), ((1600, 1200), True)])
def test_ten_second_60vi_30picture_source_cadence(cadence_host, tmp_path, dimensions, presentation):
    """Real cadence/size and renderer thread; synthetic drawing, not full LINK gameplay."""
    pipeline = Pipeline(cadence_host, tmp_path, isolated=True, delay_s=0,
                        dimensions=dimensions, frames=330, vi_per_picture=2, presentation=presentation)
    try:
        pipeline.open()
        pipeline.start()
        pipeline.pump()
        pipeline.result()
    finally:
        pipeline.close()
