"""Native ghost packets preserve cropped pixels and never queue stale frames."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs

REPO = Path(__file__).resolve().parents[1]
MODULE = (REPO / "src/sm64_events/ui/ghost/presenter.js").as_uri()


@pytest.fixture(scope="module")
def result():
    node = shutil.which("node")
    assert node, "Node is required for the ghost presenter contract"
    script = "import {cropPacket, createPresenter} from " + json.dumps(MODULE) + ";\n"
    script += r"""
const out = {};
function canvas(width = 20, height = 12, cssWidth = 10, cssHeight = 4) {
  const reads = [];
  return {width, height, reads,
    getBoundingClientRect: () => ({width: cssWidth, height: cssHeight}),
    getContext: () => ({getImageData(x, y, w, h) {
      reads.push([x, y, w, h]);
      const data = new Uint8ClampedArray(w * h * 4);
      for (let i = 0; i < data.length; i += 4) {
        data.set([11, 29, 47, (i / 4) % 256], i);
      }
      return {data};
    }}),
  };
}
const c = canvas();
const packet = cropPacket(c, {dx: 1.25, dy: 0.5, dw: 1.25, dh: 0.6}, 7, 'mario', true);
out.crop = {...packet, pixels: [...Buffer.from(packet.pixels, 'base64')]};
out.reads = c.reads;
const clipped = canvas();
out.clipped = cropPacket(clipped, {dx: -0.25, dy: 3.1, dw: 11, dh: 2}, 8, 'mario', true);
out.clipped.pixels = [...Buffer.from(out.clipped.pixels, 'base64')];
out.clippedReads = clipped.reads;

const absent = canvas();
out.hidden = [
  cropPacket(absent, null, 9, 'mario', true),
  cropPacket(absent, {dx: 1, dy: 1, dw: 1, dh: 1}, 10, 'mario', false),
  cropPacket(absent, {dx: 20, dy: 20, dw: 1, dh: 1}, 11, 'mario', true),
  cropPacket(absent, {dx: 0, dy: 0, dw: 0, dh: 1}, 12, 'mario', true),
  cropPacket(absent, {dx: NaN, dy: 0, dw: 1, dh: 1}, 13, 'mario', true),
  cropPacket(null, null, 14, 'mario', true),
  cropPacket(canvas(0, 0), {dx: 0, dy: 0, dw: 1, dh: 1}, 15, 'mario', true),
  cropPacket(canvas(10, 10, 0, 0), {dx: 0, dy: 0, dw: 1, dh: 1}, 16, 'mario', true),
];
out.absentReads = absent.reads;
out.whole = cropPacket(absent, null, 17, 'whole', true);
out.wholeHidden = cropPacket(absent, null, 18, 'whole', false);
const unreadable = canvas();
unreadable.getContext = () => ({getImageData() { throw new Error('tainted'); }});
out.unreadable = cropPacket(unreadable, {dx: 0, dy: 0, dw: 1, dh: 1}, 19, 'mario', true);

const large = canvas(6000, 5000, 6000, 5000);
out.limited = [
  cropPacket(large, {dx: 0, dy: 0, dw: 4097, dh: 1}, 20, 'mario', true),
  cropPacket(large, {dx: 0, dy: 0, dw: 1, dh: 4097}, 21, 'mario', true),
  cropPacket(large, {dx: 0, dy: 0, dw: 2001, dh: 2000}, 22, 'mario', true),
];
out.limitReads = [...large.reads];
out.smallHighDpi = cropPacket(large, {dx: 5900, dy: 4900, dw: 2, dh: 2}, 23, 'mario', true);

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
let finish, live = 0, maxLive = 0;
const sent = [];
const presenter = createPresenter(async (frame) => {
  live++;
  maxLive = Math.max(maxLive, live);
  sent.push(frame.sequence);
  await new Promise((resolve) => { finish = resolve; });
  live--;
});
presenter.submit({sequence: 1});
const draining = presenter.idle();
let idleFinished = false;
draining.then(() => { idleFinished = true; });
for (let sequence = 2; sequence <= 1000; sequence++) presenter.submit({sequence});
out.idleShared = presenter.idle() === draining;
out.beforeRelease = [...sent];
finish();
await tick();
out.afterRelease = [...sent];
out.idleDuringNewest = idleFinished;
presenter.submit(null);
finish();
await draining;
out.idleFinished = idleFinished;
out.maxLive = maxLive;
presenter.submit({sequence: 1001});
out.restarted = [...sent];
finish();
await presenter.idle();

let rejectFirst;
const recovery = [];
const failing = createPresenter((frame) => {
  recovery.push(frame.sequence);
  if (frame.sequence === 1) return new Promise((resolve, reject) => { rejectFirst = reject; });
  if (frame.sequence === 4) throw new Error('synchronous failure');
  return Promise.resolve();
});
failing.submit({sequence: 1});
failing.submit({sequence: 2});
failing.submit({sequence: 3});
rejectFirst(new Error('bridge failure'));
await failing.idle();
failing.submit({sequence: 4});
await failing.idle();
failing.submit({sequence: 5});
await failing.idle();
out.recovery = recovery;
console.log(JSON.stringify(out));
"""
    done = subprocess.run(
        [node, "--input-type=module", "-"], input=script, capture_output=True,
        text=True, encoding="utf-8", timeout=30, **quiet_spawn_kwargs(),
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_fractional_css_box_reads_only_the_corresponding_backing_pixels(result):
    packet = result["crop"]
    assert result["reads"] == [[2, 1, 3, 3]]
    assert {key: packet[key] for key in ("x", "y", "width", "height")} == {
        "x": 2, "y": 1, "width": 3, "height": 3,
    }
    assert (packet["viewport_width"], packet["viewport_height"]) == (20, 12)
    assert (packet["sequence"], packet["mode"], packet["visible"]) == (7, "mario", True)
    assert packet["pixels"] == [value for alpha in range(9) for value in (11, 29, 47, alpha)]


def test_crop_clips_both_edges_without_shifting_or_rescaling_pixels(result):
    assert result["clippedReads"] == [[0, 9, 20, 3]]
    packet = result["clipped"]
    assert (packet["x"], packet["y"], packet["width"], packet["height"]) == (0, 9, 20, 3)
    assert len(packet["pixels"]) == 20 * 3 * 4


def test_missing_unpresentable_and_unreadable_frames_clear_previous_pixels(result):
    for packet in [*result["hidden"], result["unreadable"]]:
        assert packet["visible"] is False
        assert packet["pixels"] is None
        assert (packet["x"], packet["y"], packet["width"], packet["height"]) == (0, 0, 0, 0)
    assert result["absentReads"] == []


def test_whole_mode_transports_visibility_without_reading_canvas_pixels(result):
    assert result["whole"]["visible"] is True
    assert result["whole"]["pixels"] is None
    assert result["wholeHidden"]["visible"] is False


def test_crop_limits_prevent_large_allocations_but_allow_large_viewports(result):
    assert all(packet["visible"] is False for packet in result["limited"])
    assert result["limitReads"] == []
    assert result["smallHighDpi"]["visible"] is True
    assert result["smallHighDpi"]["viewport_width"] == 6000


def test_slow_bridge_keeps_one_in_flight_and_only_the_newest_pending_packet(result):
    assert result["beforeRelease"] == [1]
    assert result["afterRelease"] == [1, 1000]
    assert result["maxLive"] == 1
    assert result["idleShared"] is True
    assert result["idleDuringNewest"] is False
    assert result["idleFinished"] is True
    assert result["restarted"] == [1, 1000, 1001]


def test_rejected_and_throwing_sends_do_not_wedge_the_bridge(result):
    assert result["recovery"] == [1, 3, 4, 5]
