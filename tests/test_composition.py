# tests/test_composition.py
"""Composition-root contracts the runtime depends on (see projection.py
docstring: level_changed must precede anchors; anchors precede grabs)."""
from pathlib import Path

import sm64_events


def test_detector_order_is_load_bearing():
    src = (Path(sm64_events.__file__).parent / "main.py").read_text(encoding="utf-8")
    order = ["GameResetDetector", "LevelChangeDetector", "AnchorDetector",
             "DeathDetector", "StarGrabDetector"]
    # Use rindex so import-line occurrences (alphabetical) are skipped in
    # favour of the last occurrence, which is inside the detectors = [...] list.
    positions = [src.rindex(name) for name in order]
    assert positions == sorted(positions)


def test_build_wires_replay_endpoints(monkeypatch, tmp_path):
    # Stub instance lock so build() doesn't acquire a real file lock.
    monkeypatch.setattr(
        "sm64_events.storage.instance_lock.acquire_instance_lock",
        lambda path: object())
    # Stub pick_video_codec so build() skips the ~100 ms NVENC probe.
    monkeypatch.setattr(
        "sm64_events.replay.encoder.pick_video_codec",
        lambda: "libx264")
    # Patch Database at the name main.py imported it under so the type
    # annotation in service.py (which already imported the real class) is
    # unaffected.  Return a sentinel object; TrackerService accepts db=None
    # too, but a truthy object exercises the normal path.
    import importlib
    import sm64_events.main as main_mod
    importlib.reload(main_mod)
    monkeypatch.setattr(main_mod, "Database", lambda path: object())
    app = main_mod.build()
    paths = {r.path for r in app.routes}
    assert "/api/replay/status" in paths
    assert "/api/replay/clips/{name}" in paths
