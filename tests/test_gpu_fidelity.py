"""The shipped bridge's exact pixels at recording sizes, against an independent
bottom-up reference, while the renderer's own state is hostile.

The producer's one GL context copies a 640x480 or 641x481 picture through the
snapshot pool and render_capture::Bridge into the helper's texture with the
scissor test, sRGB writes, blending, depth/stencil/cull and a closed colour
mask left on (the host checks they come back). The consumer dumps the texture;
the oracle below recomputes the picture from the fixture formula alone.
Alpha passes through the bridge untouched; the encoder does not read it.
"""
import os

import numpy as np
import pytest

import gpu_render_witness as witness

pytestmark = pytest.mark.skipif(os.environ.get("SM64_TEST_GPU_BRIDGE") != "1",
    reason="explicit same-adapter NVIDIA hardware witness: set SM64_TEST_GPU_BRIDGE=1")


def fixture_rgb(width, height, occurrence=1):
    """gpu_render_wire.h's witness_channel, bottom-up rows (GL row 0 first)."""
    y, x = np.indices((height, width), dtype=np.int64)
    return np.stack([(occurrence * 29 + x * 11 + y * 3 + 17) & 255,
                     (occurrence * 13 + x * 7 + y * 23 + 41) & 255,
                     (occurrence * 19 + x * 31 + y * 5 + 71) & 255], axis=-1).astype(np.uint8)


@pytest.mark.parametrize("width,height,mutation", [
    (640, 480, None), (641, 481, None),
    (641, 481, "RENDER_FIDELITY_OMIT_FLIP"), (641, 481, "RENDER_FIDELITY_WRONG_ODD_ROW"),
])
def test_gpu_top_down_exact_pixels(tmp_path, width, height, mutation):
    build, vc32 = witness.toolchain()
    producer = witness.build_producer(build, vc32, tmp_path, [mutation] if mutation else [])
    consumer = witness.build_consumer(build, vc32, tmp_path)
    result, producer_code, output = witness.run_pair(producer, consumer, tmp_path,
                                                     (width, height, 0, 0, width, height, 1), mode="dump")
    assert result.returncode == 0, result.stdout + result.stderr
    assert producer_code == 0, output
    assert "producer passed: pictures=1 state_restored=yes" in output, output
    even_w, even_h = width & ~1, height & ~1
    rgba = np.fromfile(tmp_path / "delivered.rgba", dtype=np.uint8).reshape(even_h, even_w, 4)
    actual = rgba[:, :, :3]
    bottom_up = fixture_rgb(width, height)
    # Top row first; an odd height drops native row 0, an odd width the right column.
    expected = bottom_up[::-1][:even_h, :even_w]
    if mutation is None:
        assert np.array_equal(actual, expected), np.argwhere(actual != expected)[:8]
    else:
        assert not np.array_equal(actual, expected), "the fidelity oracle missed the injected violation"
        if mutation == "RENDER_FIDELITY_OMIT_FLIP":
            first = height & 1
            broken = bottom_up[first:first + even_h, :even_w]
        else:
            broken = bottom_up[:even_h][::-1][:, :even_w]
        assert np.array_equal(actual, broken), "negative control did not reach its intended failure"
    (tmp_path / "oracle.txt").write_text(
        f"source={width}x{height} output={even_w}x{even_h} mutation={mutation} "
        f"mismatched_bytes={np.count_nonzero(actual != expected)}\n", encoding="utf-8")
