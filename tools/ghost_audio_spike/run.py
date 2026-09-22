"""Build/run a private pinned-libsm64 audio experiment; no emulator interaction."""
from __future__ import annotations

import argparse
import array
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402


def function(source: str, name: str) -> str:
    """Extract an exact upstream function, leaving its implementation unmodified."""
    start = source.index(name + "(") if name + "(" in source else source.index(name + " (")
    start = source.rfind("\n", 0, start) + 1
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def replace(path: Path, old: str, new: str) -> None:
    content = path.read_text(encoding="utf-8")
    if content.count(old) != 1:
        raise ValueError(f"Patch anchor changed: {path.name}: {old[:60]}")
    path.write_text(content.replace(old, new), encoding="utf-8", newline="\n")


def stage(archive: Path, out: Path) -> Path:
    pin = json.loads((ROOT / "renderer/model/inputs.json").read_text())["libsm64"]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != pin["sha256"]:
        raise ValueError("Archive does not match the pinned libsm64 SHA256")
    source = out / "source"
    source.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as incoming:
        for entry in incoming.infolist():
            if entry.is_dir():
                continue
            relative = Path(*Path(entry.filename).parts[1:])
            target = (source / relative).resolve()
            if not target.is_relative_to(source.resolve()):
                raise ValueError("Archive path leaves private staging")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(incoming.read(entry))
    src = source / "src"
    # Same small MSVC portability corrections already used by the pose adapter.
    replace(src / "libsm64.h", "#if defined(_WIN32)",
            "#if defined(SM64_LIB_STATIC)\n#define SM64_LIB_FN\n#elif defined(_WIN32)")
    replace(src / "decomp/include/platform_info.h",
            "#define IS_BIG_ENDIAN (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)",
            "#ifdef _WIN32\n#define IS_BIG_ENDIAN 0\n#else\n"
            "#define IS_BIG_ENDIAN (__BYTE_ORDER__ == __ORDER_BIG_ENDIAN__)\n#endif")
    for alignment in (8, 16):
        replace(src / "decomp/include/macros.h", f"#else\n#define ALIGNED{alignment}\n",
                f"#else\n#define ALIGNED{alignment} __declspec(align({alignment}))\n")
    header = src / "decomp/include/PR/libaudio.h"
    replace(header, "u8 *offset __attribute__((aligned (8)));", "__declspec(align(8)) u8 *offset;")
    replace(header, "s32 len __attribute__((aligned (8)));", "__declspec(align(8)) s32 len;")
    replace(header, "} __attribute__((aligned (16))) ALSeqFile;", "} ALSeqFile;")
    replace(header, "typedef struct\n{\n    unsigned short  revision;",
            "typedef struct __declspec(align(16))\n{\n    unsigned short  revision;")
    header = src / "decomp/tools/convTypes.h"
    replace(header, "uintptr_t offset __attribute__((aligned (8)));",
            "__declspec(align(8)) uintptr_t offset;")
    replace(header, "unsigned int len __attribute__((aligned (8)));",
            "__declspec(align(8)) unsigned int len;")
    replace(header, "struct seqFile{", "struct __declspec(align(16)) seqFile{")
    replace(header, "} __attribute__((aligned (16)));", "};")
    original = (src / "libsm64.c").read_text()
    api = ('#include "libsm64.h"\n#include "load_audio_data.h"\n'
           '#include "debug_print.h"\n#include "play_sound.h"\n'
           '#include "decomp/audio/external.h"\n'
           '#define SAMPLES_HIGH 544\n#define SAMPLES_LOW 528\n')
    for name in ("sm64_audio_init", "sm64_audio_tick", "sm64_play_sound", "sm64_set_sound_volume"):
        api += function(original, name)
    (out / "api.c").write_text(api, encoding="utf-8", newline="\n")
    # These are the only non-inline conversion utilities needed by convUtils.
    utilities = (src / "decomp/tools/utils.c").read_text()
    text = '#include <stdint.h>\n#include "decomp/tools/utils.h"\n'
    for name in ("read_s16_be", "read_f32_be"):
        text += function(utilities, name)
    (out / "conversion.c").write_text(text, encoding="utf-8", newline="\n")
    return source


def build(source: Path, out: Path, vcvars: Path | None) -> Path:
    if vcvars is None:
        spec = importlib.util.spec_from_file_location("build_plugin", ROOT / "tools/build_plugin.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        vcvars = next((path for path in module.KNOWN_VCVARS if path.is_file()), None)
    if vcvars is None:
        raise RuntimeError("x86 MSVC toolchain required")
    src = source / "src"
    paths = sorted((src / "decomp/audio").glob("*.c"))
    paths += [src / name for name in (
        "load_audio_data.c", "play_sound.c", "debug_print.c",
        "decomp/pc/mixer.c", "decomp/pc/ultra_reimplementation.c",
        "decomp/pc/alBnkfNew.c", "decomp/tools/convUtils.c",
    )]
    paths += [out / "api.c", out / "conversion.c", HERE / "probe.c"]
    objects = out / "objects"
    objects.mkdir(exist_ok=True)
    flags = ["/nologo", "/O2", "/W3", "/TC", "/std:c17", "/MT", "/Gy", "/Gw",
             "/D_CRT_SECURE_NO_WARNINGS", "/DSM64_LIB_STATIC", "/DGBI_FLOATS",
             "/DVERSION_US", "/DNO_SEGMENTED_MEMORY", f'/I"{src}"',
             f'/I"{src / "decomp/include"}"']
    lines = ["@echo off", f'call "{vcvars}" >nul', "if errorlevel 1 exit /b 1"]
    compiled = []
    for index, path in enumerate(paths):
        obj = objects / f"{index:02d}-{path.stem}.obj"
        compiled.append(obj)
        lines += [" ".join(["cl", *flags, "/c", f'"{path}"', f'/Fo"{obj}"']),
                  "if errorlevel 1 exit /b 1"]
    target = out / "audio-probe.exe"
    response = out / "link.rsp"
    response.write_text(f'/NOLOGO /MACHINE:X86 /OPT:REF /OUT:"{target}"\n'
                        + "\n".join(f'"{obj}"' for obj in compiled), encoding="utf-8")
    lines += [f'link @"{response}"', "exit /b %errorlevel%"]
    script = out / "build.bat"
    script.write_text("\r\n".join(lines), encoding="ascii")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=out,
                            capture_output=True, text=True, timeout=180,
                            stdin=subprocess.DEVNULL, **quiet_spawn_kwargs())
    (out / "compiler.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Compile failed ({result.returncode}):\n{result.stdout[-6000:]}")
    return target


def normalize(path: Path) -> bytes:
    rom = path.read_bytes()
    if len(rom) < 0x800000 or len(rom) % 4:
        raise ValueError("ROM must be at least 8 MiB and word aligned")
    if rom[:4] == bytes.fromhex("37804012"):
        rom = b"".join(rom[i:i + 2][::-1] for i in range(0, len(rom), 2))
    elif rom[:4] == bytes.fromhex("40123780"):
        rom = b"".join(rom[i:i + 4][::-1] for i in range(0, len(rom), 4))
    if rom[:4] != bytes.fromhex("80371240") or rom[0x3e] != ord("E"):
        raise ValueError("Probe supports big-endian-normalized US ROMs only")
    return rom


def run(exe: Path, rom: Path, out: Path, label: str) -> dict:
    normalized = normalize(rom)
    temporary = out / f"{label}.private.z64"
    temporary.write_bytes(normalized)
    record = {"sha256": hashlib.sha256(normalized).hexdigest(), "bytes": len(normalized),
              "audio_region_sha256": hashlib.sha256(normalized[0x57B720:0x7CC6C1]).hexdigest()}
    try:
        for mode in ("silence", "cues", "half", "mute"):
            pcm = out / f"{label}-{mode}.pcm"
            result = subprocess.run([str(exe), str(temporary), str(pcm), mode], cwd=out,
                                    capture_output=True, text=True, timeout=30,
                                    stdin=subprocess.DEVNULL, **quiet_spawn_kwargs())
            (out / f"{label}-{mode}.stdout").write_text(result.stdout, encoding="utf-8")
            (out / f"{label}-{mode}.stderr").write_text(result.stderr, encoding="utf-8")
            if result.returncode:
                raise RuntimeError(f"{label}/{mode} failed ({result.returncode}): {result.stderr}")
            data = json.loads(result.stdout)
            raw = pcm.read_bytes()
            if len(raw) != data["frames"] * 4 or data["frames"] != sum(t["frames"] for t in data["ticks"]):
                raise AssertionError("Two-block stereo byte/frame count mismatch")
            values = array.array("h", raw)
            if sys.byteorder != "little":
                values.byteswap()
            durations = sorted(t["ms"] for t in data["ticks"])
            record[mode] = {"init_ms": data["init_ms"], "frames": data["frames"],
                            "seconds": data["frames"] / 32000, "pcm_bytes": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "nonzero_values": sum(value != 0 for value in values),
                            "peak": max(abs(value) for value in values),
                            "rms": math.sqrt(statistics.mean(value * value for value in values)),
                            "tick_ms": {"mean": statistics.mean(durations),
                                        "p95": durations[int(len(durations) * .95)],
                                        "max": max(durations)}}
            if mode == "cues":
                cue_windows = {}
                for name, start, end in (("jump", 16000, 48000), ("landing", 48000, 80000),
                                         ("triple-jump", 80000, 112000), ("metal-landing", 112000, 192000)):
                    section = values[2 * start:2 * end]
                    cue_windows[name] = sum(value != 0 for value in section)
                if not all(cue_windows.values()):
                    raise AssertionError("An explicit cue produced no nonzero samples")
                record[mode]["nonzero_values_by_cue_window"] = cue_windows
            with wave.open(str(out / f"{label}-{mode}.wav"), "wb") as audio:
                audio.setparams((2, 2, 32000, 0, "NONE", "not compressed"))
                audio.writeframes(raw)
        if record["silence"]["nonzero_values"] or not record["cues"]["nonzero_values"]:
            raise AssertionError("Silent control or explicit-cue audio witness failed")
        # Pinned synthesis leaves +/-1 LSB rounding residue at native volume 0.
        # Preserve that measurement; an eventual output mixer must implement mute.
        if record["mute"]["peak"] > 1 or not 0 < record["half"]["rms"] < record["cues"]["rms"]:
            raise AssertionError("Native mute/half-volume witness failed")
    finally:
        temporary.unlink(missing_ok=True)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--vcvars", type=Path)
    parser.add_argument("--rom", action="append", default=[], metavar="LABEL=PATH")
    options = parser.parse_args()
    out = options.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = stage(options.archive, out)
    exe = build(source, out, options.vcvars)
    manifest = {"libsm64": json.loads((ROOT / "renderer/model/inputs.json").read_text())["libsm64"],
                "platform": platform.platform(), "processor": platform.processor(),
                "build": "MSVC x86 /O2 /MT; isolated executable; no audio device",
                "executable_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
                "owned_source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                         for path in HERE.iterdir() if path.suffix in (".c", ".py")}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    results = {}
    for argument in options.rom:
        label, path = argument.split("=", 1)
        if not label.isascii() or not label.replace("-", "").isalnum():
            raise ValueError("ROM label must contain only ASCII letters/digits/hyphens")
        results[label] = run(exe, Path(path), out, label)
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
