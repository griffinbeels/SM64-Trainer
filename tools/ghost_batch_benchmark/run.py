"""Build a private Win32 model/batch/presentation benchmark; never install it."""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import types

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

STOCK_SHA256 = "17ce077343c6133f8c9f2d6d6d9a4ab62c8cd2aa57c40aea1f490b4c8bb21d91"


def module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def quiet_run(*args, **kwargs):
    kwargs.update(quiet_spawn_kwargs())
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(*args, **kwargs)


def source_hashes() -> dict[str, str]:
    files = [ROOT / "tools/build_model.py", ROOT / "src/sm64_events/core/childproc.py",
             ROOT / "tests/native/ghost_compositor_host.cpp"]
    files += [ROOT / "plugin/gfxwrap" / name for name in (
        "ghost_compositor.cpp", "ghost_compositor.h", "ghost_compositor_gl.h",
        "ghost_presentation.cpp", "ghost_presentation.h")]
    files += [path for path in (ROOT / "renderer/model").iterdir() if path.is_file()]
    files += [HERE / "run.py", HERE / "probe.cpp"]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files)}


def build(out: Path, seed: Path) -> Path:
    builder = module("batch_build_model", ROOT / "tools/build_model.py")
    plugin = module("batch_build_plugin", ROOT / "tools/build_plugin.py")
    vcvars = next((path for path in plugin.KNOWN_VCVARS if path.is_file()), None)
    if vcvars is None:
        raise RuntimeError("x86 MSVC toolchain required")
    # Change only this imported recipe's subprocess binding: no source patch and
    # no global monkeypatch. Its owned compiler child gets both Windows flags.
    builder.subprocess = types.SimpleNamespace(run=quiet_run, DEVNULL=subprocess.DEVNULL,
                                              CREATE_NO_WINDOW=subprocess.CREATE_NO_WINDOW)
    library = builder.build_model(out / "model", vcvars32=vcvars, seed=seed)
    sources = [HERE / "probe.cpp", ROOT / "plugin/gfxwrap/ghost_compositor.cpp",
               ROOT / "plugin/gfxwrap/ghost_presentation.cpp"]
    flags = ["/nologo", "/O2", "/W4", "/EHsc", "/std:c++17", "/MT", "/Gy",
             "/D_CRT_SECURE_NO_WARNINGS", f'/I"{ROOT / "tests/native"}"',
             f'/I"{ROOT / "renderer/model"}"', f'/I"{ROOT / "plugin/gfxwrap"}"']
    lines = ["@echo off", f'call "{vcvars}" >nul', "if errorlevel 1 exit /b 1"]
    objects = []
    for i, path in enumerate(sources):
        obj = out / f"{i}-{path.stem}.obj"
        objects.append(obj)
        lines += [" ".join(["cl", *flags, "/c", f'"{path}"', f'/Fo"{obj}"']),
                  "if errorlevel 1 exit /b 1"]
    exe = out / "batch-probe.exe"
    response = out / "link.rsp"
    response.write_text(f'/NOLOGO /MACHINE:X86 /OPT:REF /OUT:"{exe}"\n'
                        + "\n".join(f'"{p}"' for p in [*objects, library])
                        + "\nopengl32.lib gdi32.lib user32.lib\n", encoding="utf-8")
    lines += [f'link @"{response}"', "exit /b %errorlevel%"]
    script = out / "build.bat"
    script.write_text("\r\n".join(lines), encoding="ascii")
    result = quiet_run(["cmd.exe", "/d", "/c", str(script)], cwd=out,
                       capture_output=True, text=True, timeout=120)
    (out / "compiler.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Compile failed:\n{result.stdout[-8000:]}\n{result.stderr}")
    return exe


def stats(values: list[float]) -> dict:
    ordered = sorted(values)
    return {"samples": len(values), "mean_ms": statistics.mean(values),
            "p50_ms": statistics.median(values),
            "p95_ms": ordered[math.ceil(len(values) * .95) - 1],
            "max_ms": ordered[-1], "over_33_33_ms": sum(v > 1000 / 30 for v in values)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, required=True)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=120)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--max-count", type=int, choices=[1, 4, 16, 32, 64], default=64)
    parser.add_argument("--resolutions", nargs="+", choices=["640x480", "1280x960"],
                        default=["640x480", "1280x960"])
    args = parser.parse_args()
    out = args.out.resolve()
    private = (ROOT / ".iteration/ghost-batch-benchmark").resolve()
    if not out.is_relative_to(private):
        parser.error("--out must be inside this worktree's ignored .iteration/ghost-batch-benchmark")
    if not 1 <= args.samples <= 2000 or not 0 <= args.warmup <= 100:
        parser.error("samples must be 1..2000; warmup 0..100")
    rom_hash = hashlib.sha256(args.rom.read_bytes()).hexdigest()
    if rom_hash != STOCK_SHA256:
        parser.error("this benchmark requires the exact local stock US ROM; no ROM is distributed")
    # Require all pinned inputs locally; don't let the common builder download.
    for pin in json.loads((ROOT / "renderer/model/inputs.json").read_text()).values():
        candidate = args.seed / pin["filename"]
        if not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != pin["sha256"]:
            parser.error(f"missing or mismatched local pinned seed: {candidate.name}")
    out.mkdir(parents=True, exist_ok=True)
    before = source_hashes()
    exe = build(out, args.seed.resolve())
    manifest = {"source_sha256": before, "rom_sha256": rom_hash,
                "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
                "samples": args.samples, "warmup": args.warmup, "cases": {}}
    for resolution in args.resolutions:
        width, height = resolution.split("x")
        dest = out / resolution
        dest.mkdir(exist_ok=True)
        result = quiet_run([str(exe), str(args.rom.resolve()), str(dest), width, height,
                            str(args.samples), str(args.warmup), str(args.max_count)],
                           cwd=out, capture_output=True, text=True, timeout=180)
        (dest / "process.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"Native probe failed ({resolution}, {result.returncode}): {result.stdout} {result.stderr}")
        case = {"context": json.loads(result.stdout), "timings": {}}
        with (dest / "frames.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        for key in sorted({(r["layout"], int(r["count"])) for r in rows}):
            selected = [r for r in rows if (r["layout"], int(r["count"])) == key]
            case["timings"][f"{key[0]}-{key[1]}"] = {
                field: stats([float(r[field]) for r in selected])
                for field in selected[0] if field.endswith("_ms")}
            # The compositor's driver queries per timed frame (the live path's residue).
            queries = [int(r["driver_queries"]) for r in selected]
            case["timings"][f"{key[0]}-{key[1]}"]["driver_queries"] = {"min": min(queries), "max": max(queries)}
        with (dest / "proof.csv").open(newline="") as handle:
            case["proof"] = list(csv.DictReader(handle))
        manifest["cases"][resolution] = case
        print(f"{resolution}: {len(case['proof'])} correctness cases; {len(rows)} timed frames passed", flush=True)
    if source_hashes() != before:
        raise RuntimeError("source changed during benchmark; results cannot attest current files")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(out / "manifest.json")


if __name__ == "__main__":
    main()
