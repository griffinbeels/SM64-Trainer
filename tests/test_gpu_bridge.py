"""The shipped renderer-thread bridge, x86 renderer -> x64 encoder helper.

One GL context plays the renderer and copies each picture (snapshot pool,
then render_capture::Bridge) into the two keyed-mutex textures the consumer
created, as the helper now does. The consumer checks every pixel against the
independent fixture and, in the NVENC modes, encodes with the shipped encoder;
the packets go through the recorder's own fragment mux before any decode.
"""
import os

import pytest

import gpu_render_witness as witness

pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GPU_BRIDGE") != "1",
    reason="explicit same-adapter NVIDIA hardware witness: set SM64_TEST_GPU_BRIDGE=1")

# source_w, source_h, crop_x, crop_y, snapshot_w, snapshot_h, pictures. The
# small snapshot is odd both ways: the bridge drops native row 0 and the right
# column, and the crop offset proves the snapshot's source rectangle.
SMALL = (12, 10, 1, 2, 9, 7, 6)
LARGE = (324, 244, 1, 2, 320, 240, 6)


def test_gpu_witness_refuses_wrong_actual_vendor(tmp_path, monkeypatch):
    """The hardware guard sees the GL context, rather than inventory branding."""
    import subprocess
    from sm64_events.core.childproc import quiet_spawn_kwargs

    build, vc32 = witness.toolchain()
    producer = witness.build_producer(build, vc32, tmp_path)
    actual = subprocess.run([str(producer), "capabilities"], capture_output=True,
                            text=True, timeout=10, check=True, **quiet_spawn_kwargs())
    wrong = "amd" if "vendor=NVIDIA" in actual.stdout else "nvidia"
    monkeypatch.setenv("SM64_TEST_GPU_VENDOR", wrong)
    refused = subprocess.run([str(producer), "capabilities"], capture_output=True,
                             text=True, timeout=10, check=False, **quiet_spawn_kwargs())
    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert "renderer vendor mismatch:" in refused.stderr
    assert "renderer adapter_luid=" in actual.stdout
    assert "renderer adapter_luid=" not in refused.stdout


@pytest.mark.parametrize("mode", ["pixels", "omit-copy", "nvenc", "nvenc-av1"])
def test_gpu_bridge(tmp_path, mode):
    build, vc32 = witness.toolchain()
    nvenc = mode.startswith("nvenc")
    producer = witness.build_producer(build, vc32, tmp_path,
                                      ["RENDER_BRIDGE_OMIT_COPY"] if mode == "omit-copy" else [])
    consumer = witness.build_consumer(build, vc32, tmp_path,
                                      codec={"nvenc": "h264", "nvenc-av1": "av1"}.get(mode))
    geometry = LARGE if nvenc else SMALL
    result, producer_code, output = witness.run_pair(producer, consumer, tmp_path, geometry)
    if mode == "omit-copy":
        # The key moved and the pixels did not: the oracle must see it.
        assert result.returncode == 1 and "pixel mismatch" in result.stderr, result.stdout + result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert producer_code == 0, output
    width, height = geometry[4] & ~1, geometry[5] & ~1
    assert "producer passed: pictures=6 state_restored=yes" in output, output
    assert (f"consumer passed: bits=64 pictures=6 bytes_checked={width * height * 4 * 6} "
            f"output={width}x{height} created_textures=yes") in result.stdout
    if nvenc:
        assert_nvenc_output(tmp_path, result.stdout, "av1" if mode == "nvenc-av1" else "h264")


def video_sample_entry(data: bytes) -> bytes:
    """The four characters the video track's sample description carries."""
    from sm64_events.replay.fragmentindex import boxes, child
    for kind, moov, _, _ in boxes(data):
        if kind != b"moov":
            continue
        for trak_kind, trak, _, _ in boxes(moov):
            if trak_kind != b"trak":
                continue
            mdia = child(trak, b"mdia")
            if bytes(child(mdia, b"hdlr")[8:12]) != b"vide":
                continue
            stsd = child(child(child(mdia, b"minf"), b"stbl"), b"stsd")
            return bytes(list(boxes(stsd[8:]))[0][0])
    raise AssertionError("no video track in the muxed fragments")


def assert_nvenc_output(tmp_path, output, codec):
    """The native encoder's OWN packets, through the archive that stores them.

    The bytes are never decoded on the way in: they are sliced back out of the
    witness stream by the packet sizes the encoder reported, muxed by
    `PacketFragmentMux` exactly as the recorder would, and only then decoded.
    That is what makes this a witness for AV1 as well as H.264 -- the question
    is not whether NVENC can encode, it is whether the fragment archive can
    carry what NVENC emitted with no decoder in the recording path."""
    assert "drained submitted=6 completed=6 eos=yes" in output
    import av
    import csv
    import io
    import numpy as np
    from sm64_events.replay.media import MediaRun
    from sm64_events.replay.packetmux import (EncodedPicture, NativeFormat,
                                              PacketFragmentMux)
    with (tmp_path / "packets.csv").open() as source:
        packets = list(csv.DictReader(source))
    assert [int(row["pts"]) for row in packets] == [0, 3001, 3002, 90000, 91000, 180000]
    assert [int(row["duration"]) for row in packets] == [3001, 1, 86998, 1000, 89000, 9000]
    assert [int(row["idr"]) for row in packets] == [1, 0, 0, 1, 0, 1]

    stream = (tmp_path / f"witness.{codec}").read_bytes()
    assert sum(int(row["bytes"]) for row in packets) == len(stream)
    output_mp4 = io.BytesIO()
    mux = PacketFragmentMux(output_mp4, NativeFormat(codec, 320, 240, 30),
                            MediaRun(f"gpu-bridge-{codec}", 1000.0),
                            audio_rate=48000, audio_bitrate=160000,
                            packet_limit=1 << 20, pcm_limit=384000)
    at = 0
    try:
        for row in packets:
            size = int(row["bytes"])
            mux.write_video(EncodedPicture(
                int(row["occurrence"]), int(row["pts"]), int(row["duration"]),
                int(row["idr"]) == 1, stream[at:at + size]))
            at += size
        mux.close()
    finally:
        if not mux.closed:
            mux.abort()
    (tmp_path / f"witness-{codec}.mp4").write_bytes(output_mp4.getvalue())

    # The sample entry, not the decoder's name: libav answers `libdav1d` for an
    # AV1 stream, which says which decoder it chose and nothing about what the
    # archive wrote. `avc1`/`av01` is what `fragmentindex.tracks_of` admits and
    # what a browser dispatches on.
    assert video_sample_entry(output_mp4.getvalue()) == {
        "h264": b"avc1", "av1": b"av01"}[codec]

    with av.open(io.BytesIO(output_mp4.getvalue())) as video:
        video_stream = video.streams.video[0]
        ticks = []
        frames = []
        for frame in video.decode(video_stream):
            ticks.append(int(frame.pts * frame.time_base * 90000))
            frames.append(frame.to_ndarray(format="rgb24"))
    assert ticks == [0, 3001, 3002, 90000, 91000, 180000]
    assert len(frames) == 6
    ys, xs = np.indices((240, 320)); xs = xs + 1; ys = 240 - 1 - ys + 2
    references = []
    for occurrence in range(1, 7):
        references.append(np.stack([(occurrence*29+xs*11+ys*3+17)&255,
            (occurrence*13+xs*7+ys*23+41)&255,
            (occurrence*19+xs*31+ys*5+71)&255], axis=-1).astype(float))
    distances = [[float(np.abs(frame.astype(float) - ref).mean()) for ref in references] for frame in frames]
    assert [int(np.argmin(row)) for row in distances] == [0, 1, 2, 3, 4, 5], distances
    (tmp_path / "decoded-marker-distances.txt").write_text(str(distances), encoding="utf-8")
