"""Render scene-lab frame inputs through a REAL GLideN64 offline (E0/E1 oracle).

    uv run python tools/scene_lab/replay.py render --out <dir>
        [--pj64-plugin <dir>] [--fresh] [--warmup <frame_dir>] <frame_dir>...
    uv run python tools/scene_lab/replay.py compare <a> <b> [--diff <png>]
    uv run python tools/scene_lab/replay.py poison <frame_dir> <out_dir> [--closure <json>] [--pattern 0xA5]

render builds replay_host.c (x86, cached by source hash) and renders the
frames through the Trainer's renderer: its graphics plugin, loaded where the
Trainer keeps it (the bundle, core/paths.py's bundled_plugin_dll(); never a
copy) and run as the renderer alone (SM64RendererOnlyV1, renderer_load.h), in
one renderer instance in the order given (--fresh: one per frame). The renderer reads its
settings from the renderer settings folder (settings_folder(): Project64's
GLideN64.ini and GLideN64.custom.ini, texture paths redirected inside it, so
Project64's Plugin folder is only read).
An instance's first list renders unlike Project64's steady state, so compare
like positions or pass --warmup (replay_host.c explains the carried state).
Each <index>.raw keeps ReadScreen's bytes; <index>.png is the viewable copy.
compare reports pixel-exact differences of two .raw files or two render
folders. poison writes a frame whose RDRAM keeps only a closure's ranges
(widened to whole words) and fills every other byte with --pattern: the
closure proof's input. Frame inputs are private RAM images: keep every
output in a scratch folder, never in the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core import paths  # noqa: E402
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
sys.path.insert(0, str(ROOT / "tools/scene_lab"))
from gpu_gate import require_gpu  # noqa: E402

SOURCES = (ROOT / "tools/scene_lab/replay_host.c", ROOT / "tools/scene_lab/renderer_load.h",
           ROOT / "plugin/gfxwrap/zilmar.h")
CONFIG_FILES = ("GLideN64.ini", "GLideN64.custom.ini")
SETTINGS_FOLDER_NAME = "renderer-settings"   # under the lab root: %LOCALAPPDATA%\SM64Trainer\scene-lab
_TEXTURE_PATH = re.compile(r"^(textureFilter\\tx(?:Path|CachePath|DumpPath))=.*$", re.MULTILINE)
_WINDOWED = re.compile(r"^(video\\windowed(?:Width|Height))=\d+$", re.MULTILINE)
_SHADER_STORE = re.compile(r"^(generalEmulation\\enableShadersStorage)=\d+$", re.MULTILINE)
_PROFILE = re.compile(r"^profile=(.+)$", re.MULTILINE)


def build() -> Path:
    spec = importlib.util.spec_from_file_location("scene_lab_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    digest = hashlib.sha256(b"".join(path.read_bytes() for path in SOURCES)).hexdigest()[:16]
    work = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SM64Trainer/build-cache/scene-lab" / digest
    target = work / "replay_host.exe"
    if target.is_file():
        return target
    work.mkdir(parents=True, exist_ok=True)
    vcvars = compiler.find_vcvars32()
    if vcvars is None:
        raise RuntimeError("x86 MSVC toolchain is required")
    compiler._cl(vcvars, [*compiler.COMMON_FLAGS, f"/I{ROOT / 'plugin/gfxwrap'}", str(SOURCES[0]),
                          f"/Fe:{target}", f"/Fo{work}\\", "/link", "user32.lib", "kernel32.lib"], work)
    return target


def default_lab_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SM64Trainer" / "scene-lab"


def settings_folder(pj64_plugin: Path, resolution: tuple[int, int] | None = None,
                    lab_root: Path | None = None) -> Path:
    """THE RENDERER SETTINGS FOLDER: where a lab host's renderer reads its ini (and would keep its
    log), one stable folder per window size under <lab root>/renderer-settings ("project64" keeps
    Project64's own size). It holds Project64's GLideN64.ini and GLideN64.custom.ini with the
    texture paths redirected inside it and, with `resolution`, the windowed size replaced in both
    (GLideN64.custom.ini sets it per ROM). The shader store is off in both: in a folder every start
    shares, GLideN64 would load a store another host was still writing, or one a killed host
    left torn, and its loader does not check the programs it reads back (a private folder per
    start never had a store to load, so the pictures are the ones drawn before, 2026-11-06).
    Rewritten only where Project64's files changed; Project64's Plugin folder is only read."""
    name = "project64" if resolution is None else f"{resolution[0]}x{resolution[1]}"
    folder = ((lab_root or default_lab_root()) / SETTINGS_FOLDER_NAME / name).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    for config in CONFIG_FILES:
        text = (pj64_plugin / config).read_text(encoding="utf-8")
        if config == "GLideN64.ini":
            text = _TEXTURE_PATH.sub(lambda match: f"{match[1]}={(folder / 'textures').as_posix()}", text)
            if not _SHADER_STORE.search(text):          # absent means on: name it in the profile GLideN64 reads
                profile = _PROFILE.search(text)
                header = f"[{profile[1].strip() if profile else 'User'}]\n"
                text = text.replace(header, header + "generalEmulation\\enableShadersStorage=0\n", 1)
        text = _SHADER_STORE.sub(lambda match: f"{match[1]}=0", text)
        if resolution:
            text = _WINDOWED.sub(lambda match: f"{match[1]}={resolution[match[1].endswith('Height')]}", text)
        target = folder / config
        if not (target.is_file() and target.read_text(encoding="utf-8", errors="replace") == text):
            target.write_text(text, encoding="utf-8")
    return folder


def trainer_renderer() -> Path:
    """The Trainer's renderer where the Trainer keeps it: inside its graphics plugin, in the
    bundle (core/paths.py). The hosts load it in place, never copy it, and run it as the renderer
    alone (renderer_load.h); nothing else is drawn with (LINK's own renderer cannot take a
    settings folder, so it would read and write Project64's Plugin folder)."""
    path = paths.bundled_plugin_dll()
    if path is None:
        raise SystemExit(f"renderer not found: {paths.WRAPPER_DLL_NAME} is not in this build's bundle "
                         "(uv run python tools/build_natives.py)")
    return path.resolve()


def load_picture(raw: Path) -> tuple[np.ndarray, dict]:
    """ReadScreen's BGR bottom-up rows as an RGB top-down array."""
    meta = json.loads(raw.with_suffix(".json").read_text(encoding="utf-8"))
    pixels = np.frombuffer(raw.read_bytes(), np.uint8)
    if meta["format"] != "bgr24-bottom-up" or pixels.size != meta["width"] * meta["height"] * 3:
        raise SystemExit(f"{raw}: unexpected format or size")
    return pixels.reshape(meta["height"], meta["width"], 3)[::-1, :, ::-1], meta


def render(args) -> int:
    require_gpu()
    pj64_plugin = Path(args.pj64_plugin or os.environ.get("SM64_PJ64_PLUGIN", ""))
    if not (pj64_plugin / CONFIG_FILES[0]).is_file():
        raise SystemExit("--pj64-plugin (or SM64_PJ64_PLUGIN) must name Project64's Plugin folder")
    renderer = trainer_renderer()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for stale in [*out.glob("*.raw"), *out.glob("[0-9]*.json"), *out.glob("[0-9]*.png")]:
        stale.unlink()
    settings = settings_folder(pj64_plugin)
    listing = out / "host-frames.txt"
    listing.write_text("".join(f"{frame.resolve()}\n" for frame in args.frames), encoding="utf-8")
    command = [str(build()), "--renderer", str(renderer), "--settings", str(settings),
               "--out", str(out), *(["--fresh"] if args.fresh else []),
               *(["--warmup", str(args.warmup.resolve())] if args.warmup else []), "--list", str(listing)]
    result = subprocess.run(command, capture_output=True, text=True, check=False,
                            timeout=120 + 30 * len(args.frames), **quiet_spawn_kwargs())
    if result.returncode != 0:
        print(result.stdout, end="")
        raise SystemExit(f"replay_host failed ({result.returncode}): {result.stderr.strip()}")
    for raw in sorted(out.glob("*.raw")):
        picture, meta = load_picture(raw)
        Image.fromarray(picture).save(raw.with_suffix(".png"))
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()[:16]
        print(f"{raw.stem} instance {meta['instance']} {meta['width']}x{meta['height']} "
              f"sha256 {digest} {Path(meta['frame']).name} [{meta['renderer']}]")
    return 0


def compare_pictures(a: Path, b: Path, diff_png: Path | None = None) -> dict:
    first, _ = load_picture(a)
    second, _ = load_picture(b)
    if first.shape != second.shape:
        return {"a": str(a), "b": str(b), "identical": False,
                "size_mismatch": [list(first.shape[:2]), list(second.shape[:2])]}
    changed = np.any(first != second, axis=2)
    report = {"a": str(a), "b": str(b), "identical": not changed.any(),
              "differing_pixels": int(changed.sum()), "total_pixels": int(changed.size)}
    if changed.any():
        rows, columns = np.nonzero(changed)
        report["bbox_xyxy"] = [int(columns.min()), int(rows.min()), int(columns.max()) + 1, int(rows.max()) + 1]
        report["max_channel_delta"] = int(np.abs(first.astype(np.int16) - second).max())
        if diff_png:
            Image.fromarray(np.where(changed[..., None], [255, 0, 255], first // 3).astype(np.uint8)).save(diff_png)
    return report


def compare(args) -> int:
    if args.a.is_dir():
        pairs = [(raw, args.b / raw.name) for raw in sorted(args.a.glob("*.raw"))]
        missing = [str(b) for _, b in pairs if not b.is_file()]
        if not pairs or missing:
            raise SystemExit(f"nothing to compare or missing in b: {missing}")
    else:
        pairs = [(args.a, args.b)]
    reports = [compare_pictures(a, b, args.diff if len(pairs) == 1 else None) for a, b in pairs]
    for report in reports:
        print(json.dumps(report))
    return 0 if all(report["identical"] for report in reports) else 1


def poison(args) -> int:
    closure_path = args.closure or args.frame / "closure.json"
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    if closure.get("schema") != "scene-lab-closure/1":
        raise SystemExit(f"{closure_path}: not scene-lab-closure/1")
    rdram = np.frombuffer((args.frame / "rdram.bin").read_bytes(), np.uint8)
    # Whole words: storage is word-swapped (physical byte p sits at p ^ 3), so
    # a word-aligned physical range is the same range of storage bytes.
    keep = np.zeros(rdram.size, bool)
    for entry in closure["ranges"]:
        keep[int(entry["begin"]) & ~3:min((int(entry["end"]) + 3) & ~3, rdram.size)] = True
    kept = int(keep.sum())
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "rdram.bin").write_bytes(np.where(keep, rdram, np.uint8(args.pattern)).tobytes())
    for name in ("dmem.bin", "frame.json"):
        shutil.copyfile(args.frame / name, args.out / name)
    print(f"kept {kept} of {rdram.size} RDRAM bytes from {len(closure['ranges'])} ranges; "
          f"pattern 0x{args.pattern:02X} -> {args.out}")
    return 0


def main() -> int:
    # @file reads one argument per line: a long session's frame list overflows
    # the Windows command line.
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], fromfile_prefix_chars="@")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("render")
    run.add_argument("frames", type=Path, nargs="+")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--pj64-plugin", help="Project64's Plugin folder (read only); default SM64_PJ64_PLUGIN")
    run.add_argument("--fresh", action="store_true", help="restart the renderer between frames")
    run.add_argument("--warmup", type=Path, help="render this frame first in every instance, unsaved")
    run.set_defaults(handler=render)
    diff = commands.add_parser("compare")
    diff.add_argument("a", type=Path)
    diff.add_argument("b", type=Path)
    diff.add_argument("--diff", type=Path, help="write a difference picture (single pair only)")
    diff.set_defaults(handler=compare)
    spoil = commands.add_parser("poison")
    spoil.add_argument("frame", type=Path)
    spoil.add_argument("out", type=Path)
    spoil.add_argument("--closure", type=Path, help="default <frame>/closure.json")
    spoil.add_argument("--pattern", type=lambda value: int(value, 0) & 0xFF, default=0xA5)
    spoil.set_defaults(handler=poison)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
