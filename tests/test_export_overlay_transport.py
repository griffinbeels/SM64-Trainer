"""Exercise the real CLI orchestration with no HTTP, browser or encoder process."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error

import pytest
from PIL import Image

from test_inputs_overlay_clock import view


@pytest.fixture
def exporter():
    path = Path(__file__).resolve().parents[1] / "tools/export_overlay.py"
    spec = importlib.util.spec_from_file_location("overlay_export_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_uses_captured_states_even_when_the_raw_track_is_ambiguous(tmp_path, monkeypatch, exporter):
    monkeypatch.setattr(sys, "argv", ["export_overlay", "--attempt", "1", "--base", "http://unused",
                                     "--out", str(tmp_path), "--layers", "combined"])
    monkeypatch.setattr(exporter, "clip_view_from_server", lambda *a: view())

    def reject_track(*args):
        pytest.fail("mapped export must not join the input store by raw counter")

    monkeypatch.setattr(exporter, "track_from_server", reject_track)
    monkeypatch.setattr(exporter, "render_states", lambda base, plan, *a:
                        ([tmp_path / f"s{i}.png" for i in range(len(plan.states))], {}))
    encoded = []
    monkeypatch.setattr(exporter, "encode", lambda plan, *a: encoded.append(plan))
    assert exporter.main() == 0
    [plan] = encoded
    assert plan.duration_s == 2.5
    assert [plan.states[i] for i in plan.per_frame] == [
        None, (0x8000, 40, 0, 0), None, (0x4000, -40, 0, 100)]


def test_cli_refuses_a_clip_with_no_source_states_before_rendering(tmp_path, monkeypatch, exporter):
    monkeypatch.setattr(sys, "argv", ["export_overlay", "--attempt", "1", "--base", "http://unused",
                                     "--out", str(tmp_path / "output")])
    data = view()
    data.pop("picture_states")
    monkeypatch.setattr(exporter, "clip_view_from_server", lambda *a: data)
    with pytest.raises(SystemExit, match="captured state"):
        exporter.main()
    assert not (tmp_path / "output").exists()


def test_replay_service_failure_does_not_silently_export_a_plain_track(monkeypatch, exporter):
    def unavailable(*args, **kwargs):
        raise urllib.error.HTTPError("http://unused", 503, "unavailable", None, None)

    monkeypatch.setattr(exporter.urllib.request, "urlopen", unavailable)
    with pytest.raises(SystemExit, match="could not load the replay"):
        exporter.clip_view_from_server("http://unused", 1)


@pytest.mark.parametrize("detail", ["no footage in the replay buffer", "no footage overlaps the requested span"])
def test_expired_footage_still_allows_the_plain_track_export(monkeypatch, exporter, detail):
    def expired(*args, **kwargs):
        body = io.BytesIO(json.dumps({"detail": detail}).encode())
        raise urllib.error.HTTPError("http://unused", 409, "conflict", None, body)

    monkeypatch.setattr(exporter.urllib.request, "urlopen", expired)
    assert exporter.clip_view_from_server("http://unused", 1) is None


@pytest.mark.parametrize("detail", ["no readable pictures at the requested start", "span too short to extract"])
def test_extraction_errors_do_not_become_plain_track_fallback(monkeypatch, exporter, detail):
    def failed(*args, **kwargs):
        body = io.BytesIO(json.dumps({"detail": detail}).encode())
        raise urllib.error.HTTPError("http://unused", 409, "conflict", None, body)

    monkeypatch.setattr(exporter.urllib.request, "urlopen", failed)
    with pytest.raises(SystemExit, match="could not load the replay"):
        exporter.clip_view_from_server("http://unused", 1)


def test_unknown_state_has_no_ink_even_when_the_inspector_draws_scaffolding(exporter):
    image = io.BytesIO()
    Image.new("RGB", (3, 2), (70, 90, 100)).save(image, format="PNG")
    # The same opaque glyph over either background has full recovered alpha.
    known = Image.open(io.BytesIO(exporter.recover_alpha(image.getvalue(), image.getvalue())))
    missing = Image.open(io.BytesIO(exporter.recover_alpha(image.getvalue(), image.getvalue(), empty=True)))
    assert known.getchannel("A").getextrema() == (255, 255)
    assert missing.getchannel("A").getextrema() == (0, 0)


def test_explicit_plain_export_does_not_require_a_usable_replay(tmp_path, monkeypatch, exporter):
    monkeypatch.setattr(sys, "argv", ["export_overlay", "--attempt", "1", "--base", "http://unused",
        "--plain", "--out", str(tmp_path), "--layers", "combined"])

    def no_replay(*args):
        pytest.fail("plain export must not request or guess a replay association")

    monkeypatch.setattr(exporter, "clip_view_from_server", no_replay)
    monkeypatch.setattr(exporter, "track_from_server", lambda *a: {
        "runs": [{"start": 0, "length": 1, "buttons": 0x8000, "stick_x": 40, "stick_y": 0, "yaw": 0}],
        "buttons": exporter.A.BUTTON_BITS, "stick_max": exporter.A.STICK_MAX,
        "dead_zone": exporter.A.STICK_DEAD_ZONE})
    monkeypatch.setattr(exporter, "render_states", lambda base, plan, *a:
        ([tmp_path / f"s{i}.png" for i in range(len(plan.states))], {}))
    encoded = []
    monkeypatch.setattr(exporter, "encode", lambda plan, *a: encoded.append(plan))
    assert exporter.main() == 0
    assert encoded[0].frame_times is None
