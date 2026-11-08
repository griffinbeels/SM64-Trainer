"""The bundled ffmpeg.exe is one pinned build whose every input a release attaches.

tools/build_ffmpeg.py builds FFmpeg 8.1.1 from the sources PyAV's own FFmpeg
comes from, with a portable MSYS2 toolchain pinned package by package
(ffmpeg/README.md). The build itself takes minutes and an MSYS2 download, so
these tests check the recipe; the binary is checked where it exists.
Lead decision 2026-11-08: our own trimmed, static, GPL v3 build, no nonfree.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_ffmpeg  # noqa: E402

SPEC = build_ffmpeg.inputs()


def test_every_input_is_pinned_by_url_and_sha256():
    msys2 = SPEC["msys2"]
    pinned = [msys2["base"], *msys2["packages"], *SPEC["sources"].values()]
    for item in pinned:
        assert item["url"].startswith("https://"), item
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), item
    # Exact package files from the MSYS2 repository, never a name pacman resolves.
    for item in msys2["packages"]:
        assert re.fullmatch(r"https://repo\.msys2\.org/(mingw/ucrt64/mingw-w64-ucrt-x86_64-|msys/x86_64/)"
                            r"[^/]+-\d[^/]*\.pkg\.tar\.zst", item["url"]), item
    assert re.fullmatch(r"[0-9a-f]{64}", SPEC["expected_sha256"])
    assert set(build_ffmpeg.pins(SPEC)) == set(build_ffmpeg.programs(SPEC)), "every program built is pinned"
    assert all(re.fullmatch(r"[0-9a-f]{64}", sha) for sha in build_ffmpeg.pins(SPEC).values())


def test_the_configuration_is_gpl_v3_without_nonfree_and_names_no_local_path():
    args = build_ffmpeg.configure_args(SPEC)
    assert "--enable-gpl" in args and "--enable-version3" in args
    assert not any("nonfree" in arg for arg in args)
    assert "--disable-everything" in args and "--disable-autodetect" in args
    assert not any(re.search(r"(?i)[a-z]:[/\\]|/c/users|appdata", arg) for arg in args), args
    # Static, with the external libraries the brief names and no others.
    external = {arg for arg in args if re.fullmatch(r"--enable-(lib\w+|zlib|schannel)", arg)}
    assert external == {"--enable-zlib", "--enable-libx264", "--enable-libdav1d", "--enable-libvpl",
                        "--enable-schannel"}
    assert "--enable-static" in args and "--disable-shared" in args


def test_test_only_components_are_kept_apart_from_what_the_app_runs():
    app, tests = SPEC["components"], SPEC["test_components"]
    for kind, names in tests.items():
        assert not set(names) & set(app.get(kind, [])), kind
    merged = build_ffmpeg.components(SPEC)
    assert set(merged["indev"]) == {"lavfi"} and "tee" in merged["muxer"]
    assert set(app["encoder"]) <= set(merged["encoder"])


def test_the_listing_parser_reads_flagged_comma_and_bare_rows():
    listing = (" D   mov,mp4,m4a,3gp,3g2,mj2 QuickTime / MOV\n D   mpeg            MPEG-PS\n"
               " V....D libx264              libx264 H.264\n ... aresample         A->A       Resample\n"
               "Bitstream filters:\naac_adtstoasc\nnull\n\nInput:\n  file\n  https\n"
               " D. = Demuxing supported\n ..d = Is a device\n")
    names = build_ffmpeg.listed_names(listing)
    assert {"mov", "mp4", "mpeg", "libx264", "aresample", "aac_adtstoasc", "null", "file", "https"} <= names
    assert "=" not in names and "Input" not in names


def test_the_notice_describes_this_exact_recipe():
    """licenses/ffmpeg/BUILD-README.txt is generated (--notices): a changed
    input, flag or pin without a refreshed notice turns this red."""
    tracked = (ROOT / "licenses" / "ffmpeg" / "BUILD-README.txt").read_bytes().decode("utf-8")
    assert tracked == build_ffmpeg.notices_text(), "run uv run python tools/build_ffmpeg.py --notices"
    assert f"Version: {build_ffmpeg.version_name(SPEC)}" in tracked
    for folder, _what, _license in build_ffmpeg.NOTICE_FOLDERS:
        texts = [p for p in (ROOT / "licenses" / "ffmpeg" / folder).iterdir() if p.is_file()]
        assert texts, f"licenses/ffmpeg/{folder} holds no license text"


def test_the_cache_key_follows_the_recipe_not_the_tools_own_text(monkeypatch):
    before = build_ffmpeg.build_id()
    changed = json.loads(json.dumps(SPEC))
    changed["configure"].append("--enable-small")
    monkeypatch.setattr(build_ffmpeg, "inputs", lambda: changed)
    assert build_ffmpeg.build_id() != before
    pinned_only = json.loads(json.dumps(SPEC))
    pinned_only["expected_sha256"] = "0" * 64
    monkeypatch.setattr(build_ffmpeg, "inputs", lambda: pinned_only)
    assert build_ffmpeg.build_id() == before


def test_a_build_that_is_not_the_pin_is_refused(tmp_path, monkeypatch):
    out = tmp_path / "out" / build_ffmpeg.build_id()
    out.mkdir(parents=True)
    for name in build_ffmpeg.programs(SPEC):
        (out / name).write_bytes(b"another build")
    with pytest.raises(RuntimeError, match="not the pinned build"):
        build_ffmpeg.ensure(tmp_path, build_missing=False)
    (out / "ffprobe.exe").unlink()
    with pytest.raises(RuntimeError, match="not built yet"):
        build_ffmpeg.ensure(tmp_path, build_missing=False)


def test_ffprobe_is_built_for_the_tests_and_never_shipped():
    assert build_ffmpeg.programs(SPEC) == ["ffmpeg.exe", "ffprobe.exe"]
    assert "--enable-ffprobe" in build_ffmpeg.configure_args(SPEC)
    exe = (ROOT / "tools" / "build_exe.py").read_text(encoding="utf-8")
    assert "ffprobe" not in exe, "the app bundles ffmpeg.exe alone"


def test_the_built_binary_lists_every_pinned_component():
    out = build_ffmpeg.default_root() / "out" / build_ffmpeg.build_id()
    if not (out / "ffmpeg.exe").is_file():
        pytest.skip(f"ffmpeg.exe is not built in this machine's cache ({out}): tools/build_ffmpeg.py")
    for name, sha in build_ffmpeg.pins(SPEC).items():
        assert build_ffmpeg._sha256(out / name) == sha, name
    assert build_ffmpeg.verify(out / "ffmpeg.exe") == []
