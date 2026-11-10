"""Opt-in actual ROM mesh proof through compact live packets; no GPU or emulator."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.clip import decode_clip
from sm64_events.ghost.native_protocol import Producer
from sm64_events.multiplayer.source_handoff import SourceHandoff

ROOT = Path(__file__).resolve().parents[1]


def tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compact_source(path):
    """Actual handoff over historical frames; synthetic local producer identities."""
    handoff = SourceHandoff(bytes(range(16)), stale_after_ns=250_000_000)
    owner = handoff.begin_capture()
    packets = []
    for generation, name in enumerate(("attempt_2549_whomps-fortress_shoot-into-the-wild-blue_0m10s66.ghost",
                 "attempt_4015_bob-omb-battlefield_behind-chain-chomps-gate_0m13s63.ghost"), start=1):
        clip = decode_clip((ROOT / "tests/fixtures" / name).read_bytes())
        origin = clip.frames[0].qpc
        for frame in clip.frames:
            # The real captured bytes and frequency are retained. These local
            # lifecycle/receipt values do not pretend to be live capture.
            producer = Producer(123, 456, generation, generation, frame.configuration,
                frame.epoch, clip.qpc_frequency, origin, 0.0, clip.rom_layout)
            now = len(packets) + 1
            assert handoff.observe(owner, producer, (frame.raw,), received_ns=now) == 1
            value = handoff.take_latest(now_ns=now)
            assert value is not None and handoff.current(value, now_ns=now)
            packets.append(value.packet)
    assert handoff.end_capture(owner)
    path.write_bytes(b"".join(packets))
    return path


def test_one_and_three_compact_live_streams_evaluate_actual_rom_model(tmp_path):
    if sys.platform != "win32" or not os.environ.get("SM64_MODEL_ROM"):
        pytest.skip("explicit local ROM and Win32 model witness")
    rom = Path(os.environ["SM64_MODEL_ROM"])
    seed = Path(os.environ["SM64_MODEL_SEED_DIR"])
    assert hashlib.sha1(rom.read_bytes()).hexdigest() == "9bef1128717f958171a4afac3ed78ee2bb4e86ce"
    inputs = json.loads((ROOT / "renderer/model/inputs.json").read_text())
    assert all((seed / item["filename"]).is_file() and hashlib.sha256(
        (seed / item["filename"]).read_bytes()).hexdigest() == item["sha256"] for item in inputs.values())
    source = compact_source(tmp_path / "compact-live-packets.bin")
    compiler, builder = tool("build_plugin"), tool("build_model")
    vcvars = compiler.find_vcvars32()
    assert vcvars
    library = builder.build_model(tmp_path / "model", seed=seed, vcvars32=vcvars)
    target = tmp_path / "live_model_host.exe"
    native = ROOT / "plugin/gfxwrap"
    gpu = os.environ.get("SM64_TEST_LIVE_MODEL_GPU") == "1"
    source_inputs = [native / "ghost_live_render.cpp", native / "ghost_live_render.h",
        native / "ghost_live_input.cpp", native / "ghost_live_input.h",
        native / "ghost_live_codec.cpp", native / "ghost_live_codec.h",
        native / "ghost_live_protocol.h", native / "ghost_pose.cpp", native / "ghost_pose.h",
        ROOT / "tests/native/ghost_live_model_host.cpp", Path(__file__)]
    if gpu:
        source_inputs += [native / "ghost_compositor.cpp", native / "ghost_compositor.h",
            native / "ghost_annotation_assets.cpp", ROOT / "tests/native/ghost_compositor_host.cpp"]
    source_receipt = {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in source_inputs}
    extra_sources = [str(native / "ghost_compositor.cpp"), str(native / "ghost_annotation_assets.cpp")] if gpu else []
    compiler._cl(vcvars, ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", "/Gy",
        *(["/DSM64_LIVE_MODEL_GPU"] if gpu else []),
        f"/I{native}", f"/I{ROOT / 'renderer/model'}", f"/I{ROOT / 'tests/native'}",
        str(native / "ghost_live_render.cpp"), str(native / "ghost_live_input.cpp"), str(native / "ghost_live_codec.cpp"), str(native / "ghost_pose.cpp"),
        *extra_sources, str(ROOT / "tests/native/ghost_live_model_host.cpp"), str(library),
        f"/Fe:{target}", f"/Fo{tmp_path}\\", "/link", "/OPT:REF", *compiler.LIBS], tmp_path)
    result = subprocess.run([str(target), str(rom), str(source), *([str(tmp_path / "live")] if gpu else [])], cwd=tmp_path,
        capture_output=True, text=True, timeout=60, check=False, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["real_model"] is True and report["gpu_pixels"] is gpu
    assert report["native_mailbox"] is True and report["native_renderer"] is True and report["input_accepted"] == 3320
    report["source_handoff"] = True
    assert report["records"] == 830 and report["evaluations"] == 3320 and report["held_evaluations"] == 340
    assert report["minimum_mesh"] > 800 and report["minimum_visible"] > 0
    report["executable_sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    report["library_sha256"] = hashlib.sha256(library.read_bytes()).hexdigest()
    assert source_receipt == {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in source_inputs}
    report["source_sha256"] = source_receipt
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2) + "\n",encoding="utf-8")
    evidence = os.environ.get("SM64_LIVE_MODEL_EVIDENCE")
    if evidence:
        Path(evidence).write_text(json.dumps(report, indent=2) + "\n",encoding="utf-8")
        if gpu:
            import shutil
            from PIL import Image
            for image in tmp_path.glob("live-*.ppm"):
                destination = Path(evidence).parent / image.with_suffix(".png").name
                with Image.open(image) as pixels:
                    pixels.save(destination)
            shutil.copyfile(tmp_path / "report.json", Path(evidence).with_name("gpu-report.json"))
    if gpu:
        assert report["one_peer_pixels"] > 100 and report["three_peer_pixels"] > report["one_peer_pixels"]
        assert report["clean_unchanged"] is True
        assert report["one_peer_pixels"] < report["partial_peer_pixels"] < report["three_peer_pixels"]
        assert report["withdrawal_checks"] == 3
        assert report["gpu_renderer"]
