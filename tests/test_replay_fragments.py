"""Observable complete-byte publication and bounded failure contracts."""
import struct

import pytest

from sm64_events.replay.fragments import FragmentReader


def box(kind, payload=b"", extended=False):
    if extended:
        return struct.pack(">I4sQ", 1, kind, len(payload) + 16) + payload
    return struct.pack(">I4s", len(payload) + 8, kind) + payload


def stream():
    return [box(b"ftyp", b"isom"), box(b"moov", b"init"),
            box(b"moof", b"fragment-0"), box(b"mdat", bytes(range(256)), True),
            box(b"moof", b"fragment-1"), box(b"mdat", b"tail"), box(b"mfra", b"index")]


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 8, 9, 15, 16, 17, 67, 4096])
def test_arbitrary_chunks_preserve_every_byte_and_original_range(chunk_size):
    data = b"".join(stream())
    reader = FragmentReader()
    units = []
    for start in range(0, len(data), chunk_size):
        units.extend(reader.feed(data[start:start + chunk_size]))
    reader.finish()
    assert [unit.kind for unit in units] == ["init", "media", "media", "trailer"]
    assert b"".join(unit.data for unit in units) == data
    for unit in units:
        assert data[unit.offset:unit.offset + len(unit.data)] == unit.data


def test_publish_first_complete_media_before_tail_or_eof():
    boxes = stream()
    reader = FragmentReader()
    assert [unit.kind for unit in reader.feed(b"".join(boxes[:2]))] == ["init"]
    assert reader.feed(boxes[2] + boxes[3][:-1]) == []
    first = reader.feed(boxes[3][-1:])
    assert first[0].kind == "media"
    assert first[0].data == boxes[2] + boxes[3]
    assert [unit.kind for unit in reader.feed(b"".join(boxes[4:]))] == ["media", "trailer"]
    reader.finish()


@pytest.mark.parametrize("cut", [0, 1, 8, 23, 24, 30, 300, 327])
def test_eof_refuses_unfinished_init_or_fragment(cut):
    reader = FragmentReader()
    reader.feed(b"".join(stream())[:cut])
    with pytest.raises(ValueError, match="unfinished"):
        reader.finish()
    with pytest.raises(ValueError, match="closed"):
        reader.feed(b"")


@pytest.mark.parametrize("bad", [box(b"mdat"), struct.pack(">I4s", 0, b"ftyp"),
                                   struct.pack(">I4s", 7, b"ftyp"),
                                   struct.pack(">I4sQ", 1, b"ftyp", 15)])
def test_bad_size_and_unsupported_topology_fail_closed(bad):
    reader = FragmentReader()
    with pytest.raises(ValueError):
        reader.feed(bad)
    with pytest.raises(ValueError, match="closed"):
        reader.feed(box(b"ftyp"))


def test_declared_size_is_rejected_before_body_is_buffered():
    reader = FragmentReader(max_unit_bytes=64)
    assert reader.feed(box(b"ftyp", b"init")) == []
    with pytest.raises(ValueError, match="byte limit"):
        reader.feed(struct.pack(">I4s", 64, b"moov"))


def test_new_run_requires_new_reader():
    reader = FragmentReader()
    reader.feed(b"".join(stream()))
    with pytest.raises(ValueError, match="box order"):
        reader.feed(box(b"ftyp", b"new-run"))


def test_actual_mux_publishes_complete_media_while_encoder_run_is_open():
    """FFmpeg's actual output reaches the reader without a completed file."""
    from fractions import Fraction
    import io

    import av
    import numpy as np

    class Output(io.RawIOBase):
        def __init__(self):
            self.reader = FragmentReader()
            self.units = []
            self.data = bytearray()

        def writable(self):
            return True

        def write(self, data):
            self.data.extend(data)
            # Split muxer writes inside headers as well as sample payloads.
            for start in range(0, len(data), 37):
                self.units.extend(self.reader.feed(data[start:start + 37]))
            return len(data)

    output = Output()
    with av.open(output, "w", format="mp4", options={
        "movflags": "delay_moov+default_base_moof+frag_keyframe",
        "frag_duration": "100000", "video_track_timescale": "90000",
    }) as container:
        video = container.add_stream("libx264", rate=30)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        video.time_base = video.codec_context.time_base = Fraction(1, 90000)
        video.options = {"preset": "ultrafast", "tune": "zerolatency", "bf": "0", "g": "15"}
        for number in range(30):
            pixels = np.full((90, 160, 3), number * 8, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = number * 3000, Fraction(1, 90000)
            for packet in video.encode(frame):
                container.mux(packet)
        assert any(unit.kind == "media" for unit in output.units)
        prefix_count = len(output.units)
        for packet in video.encode():
            container.mux(packet)
    output.reader.finish()
    assert len(output.units) > prefix_count
    reconstructed = b"".join(unit.data for unit in output.units)
    assert reconstructed == bytes(output.data)
    with av.open(io.BytesIO(reconstructed)) as container:
        pictures = list(container.decode(video=0))
    assert [frame.pts for frame in pictures] == [number * 3000 for number in range(30)]
    assert all(frame.time_base == Fraction(1, 90000) for frame in pictures)
