"""One owner decides which ffmpeg.exe runs, and a source run uses the pinned build.

Lead decision 2026-11-08: Griffin's test server runs from source, where the
app used to fall back to the ffmpeg on PATH (gyan.dev's build on his
machine), so his live testing never ran the binary releases ship. Now
core/paths.py `ffmpeg_program()` answers: the bundled ffmpeg.exe (frozen:
beside the exe; from a checkout: the pinned build tools/build_ffmpeg.py
places in src/sm64_events/data/ffmpeg/), else PATH.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_ffmpeg  # noqa: E402
from sm64_events.core import paths  # noqa: E402
from source_scan import strip_comments  # noqa: E402


def _on_path(monkeypatch, found: str | None):
    monkeypatch.setattr(paths.shutil, "which", lambda name: found if name == "ffmpeg" else None)


def test_a_checkout_runs_the_pinned_build_before_the_one_on_path(tmp_path, monkeypatch):
    pinned = tmp_path / "data" / "ffmpeg" / "ffmpeg.exe"
    monkeypatch.setattr(paths, "SOURCE_FFMPEG", pinned)
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    _on_path(monkeypatch, r"D:\ffmpeg\bin\ffmpeg.exe")
    assert paths.ffmpeg_program() == r"D:\ffmpeg\bin\ffmpeg.exe"     # not placed: PATH
    pinned.parent.mkdir(parents=True)
    pinned.write_bytes(b"pinned build")
    assert paths.bundled_ffmpeg() == str(pinned)
    assert paths.ffmpeg_program() == str(pinned)                     # placed: it wins
    _on_path(monkeypatch, None)
    assert paths.ffmpeg_program() == str(pinned)


def test_a_frozen_app_runs_the_ffmpeg_beside_it_and_never_a_checkouts(tmp_path, monkeypatch):
    pinned = tmp_path / "checkout" / "ffmpeg.exe"
    pinned.parent.mkdir()
    pinned.write_bytes(b"a checkout's copy")
    monkeypatch.setattr(paths, "SOURCE_FFMPEG", pinned)
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "internal"), raising=False)
    _on_path(monkeypatch, r"D:\ffmpeg\bin\ffmpeg.exe")
    assert paths.bundled_ffmpeg() is None                            # nothing bundled
    assert paths.ffmpeg_program() == r"D:\ffmpeg\bin\ffmpeg.exe"
    (tmp_path / "internal").mkdir()
    (tmp_path / "internal" / "ffmpeg.exe").write_bytes(b"bundled")
    assert paths.ffmpeg_program() == str(tmp_path / "internal" / "ffmpeg.exe")


def test_the_build_tool_places_the_file_the_app_looks_for():
    assert build_ffmpeg.SOURCE_COPY.resolve() == paths.SOURCE_FFMPEG.resolve()


def test_placing_copies_the_cached_pin_and_never_keeps_another_build(tmp_path, monkeypatch):
    data = {"ffmpeg.exe": b"the pinned ffmpeg.exe", "ffprobe.exe": b"the pinned ffprobe.exe"}
    spec = json.loads(json.dumps(build_ffmpeg.inputs()))
    spec["expected_sha256"] = hashlib.sha256(data["ffmpeg.exe"]).hexdigest()
    spec["test_programs_sha256"] = {"ffprobe.exe": hashlib.sha256(data["ffprobe.exe"]).hexdigest()}
    monkeypatch.setattr(build_ffmpeg, "inputs", lambda: spec)
    target = tmp_path / "checkout" / "ffmpeg.exe"
    assert build_ffmpeg.place(tmp_path / "cache", target) == "not built"
    cache = tmp_path / "cache" / "out" / build_ffmpeg.build_id()
    cache.mkdir(parents=True)
    for name, content in data.items():
        (cache / name).write_bytes(content)
    assert build_ffmpeg.place(tmp_path / "cache", target) == "placed"
    assert {name: (target.parent / name).read_bytes() for name in data} == data   # ffprobe beside ffmpeg
    assert build_ffmpeg.place(tmp_path / "cache", target) == "fresh"
    (cache / "ffmpeg.exe").unlink()
    target.write_bytes(b"an older pinned build")
    assert build_ffmpeg.place(tmp_path / "cache", target) == "not built"
    assert not target.exists(), "a stale copy must fall back to PATH, not keep running"


def test_no_other_module_decides_which_ffmpeg_runs():
    """Five call sites each assembled `bundled_ffmpeg() or shutil.which("ffmpeg")`;
    one owner now answers, so a source run cannot use the pin in one place and
    PATH in another."""
    offenders = []
    for path in (ROOT / "src" / "sm64_events").rglob("*.py"):
        if path.name == "paths.py" and path.parent.name == "core":
            continue
        code = strip_comments(path.read_text(encoding="utf-8"))
        if re.search(r"""which\(\s*["']ffmpeg["']""", code) or re.search(r"\bbundled_ffmpeg\(\)\s+or\b", code):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"{offenders} pick an ffmpeg themselves; call core.paths.ffmpeg_program()"
