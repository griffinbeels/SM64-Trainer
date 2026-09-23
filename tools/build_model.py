"""Build the pinned C pose model as a Win32 /MT static library; never install it."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "renderer/model"
SOURCE_FILES = (
    "src/decomp/global_state.c", "src/decomp/engine/math_util.c",
    "src/decomp/engine/graph_node.c", "src/decomp/engine/graph_node_manager.c",
    "src/decomp/engine/geo_layout.c", "src/decomp/engine/guMtxF2L.c",
    "src/decomp/game/rendering_graph_node.c", "src/decomp/game/mario_misc.c",
    "src/decomp/game/object_stuff.c", "src/decomp/game/behavior_actions.c", "src/decomp/mario/geo.inc.c",
    "src/decomp/mario/model.inc.c", "src/decomp/held/bobomb.c", "src/decomp/held/bobomb_lists.c",
    "src/gfx_adapter.c",
)
# Objects Mario can hold, built like his own model from pinned decomp sources
# (inputs.json): geo, lists and animations compile through the same libsm64
# macros; each texture array becomes the model atlas cell assets.c fills from
# the ROM. As for Mario, the lists compile apart from the decomp headers
# (libsm64's gfx_macros.h); geo and animations see them as extern Gfx.
# {prefix: (geo layout, animation table, list input, geo/animation inputs, textures)}.
HELD_MODELS = {
    "bobomb": ("black_bobomb_geo", "bobomb_seg8_anims_0802396C", "bobomb_model",
               ("bobomb_anim_walk", "bobomb_anim_held", "bobomb_anims", "bobomb_geo"),
               {"bobomb_seg8_texture_0801DA60": "SG_ATLAS_CELL_BOBOMB_LEFT",
                "bobomb_seg8_texture_0801EA60": "SG_ATLAS_CELL_BOBOMB_RIGHT",
                # The buddy's sides: its geo compiles beside the black one, never drawn.
                "bobomb_seg8_texture_0801FA60": "SG_ATLAS_CELL_BOBOMB_LEFT",
                "bobomb_seg8_texture_08020A60": "SG_ATLAS_CELL_BOBOMB_RIGHT",
                "bobomb_seg8_texture_08021A60": "SG_ATLAS_CELL_BOBOMB_EYES",
                "bobomb_seg8_texture_08022260": "SG_ATLAS_CELL_BOBOMB_BLINK"}),
}
HELD_HEADER = """#include "../include/sm64.h"
#include "../include/types.h"
#include "../include/geo_commands.h"
#include "../game/rendering_graph_node.h"
#include "../shim.h"
Gfx *geo_switch_anim_state(s32 callContext, struct GraphNode *node, void *context);
"""
# Each held graph is wrapped as libsm64 wraps Mario (mario_geo_libsm64): only a
# z-buffered master list emits display lists, and libsm64's master-list hack
# drops the first opaque entry, so an empty list takes that place.
HELD_ROOT = """const GeoLayout sg_{name}_root[] = {{
   GEO_NODE_START(),
   GEO_OPEN_NODE(),
      GEO_ZBUFFER(1),
      GEO_OPEN_NODE(),
         GEO_DISPLAY_LIST(LAYER_OPAQUE, sg_{name}_skipped),
         GEO_BRANCH(1, {geo}),
      GEO_CLOSE_NODE(),
   GEO_CLOSE_NODE(),
   GEO_END(),
}};
void *sg_{name}_geo = (void *)sg_{name}_root;
const struct Animation *const *sg_{name}_animations = {table};
"""
# Ghost sound effects: SM64's own audio engine, driven by renderer/model/audio.c.
AUDIO_FILES = (
    *(f"src/decomp/audio/{name}.c" for name in (
        "data", "effects", "external", "globals_start", "heap", "load", "load_dat", "load_sh",
        "playback", "port_eu", "port_sh", "seqplayer", "shindou_debug_prints", "synthesis",
        "synthesis_sh")),
    "src/load_audio_data.c", "src/play_sound.c",
    "src/debug_print.c", "src/decomp/pc/mixer.c", "src/decomp/pc/ultra_reimplementation.c",
    "src/decomp/pc/alBnkfNew.c", "src/decomp/tools/convUtils.c", "src/conversion.c",
)


def _module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def component_hash() -> str:
    """Includes recipe, owned sources/patches and every pinned upstream hash."""
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    for path in sorted(MODEL.iterdir()):
        if path.is_file():
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _fetch(cache: Path, spec: dict, seed: Path | None) -> Path:
    target = cache / spec["filename"]
    if not target.exists():
        candidate = seed / spec["filename"] if seed else None
        if candidate is not None and candidate.is_file():
            data = candidate.read_bytes()
        else:
            with urllib.request.urlopen(spec["url"], timeout=60) as response:
                data = response.read()
        if hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ValueError(f"Model input hash mismatch: {target.name}")
        target.write_bytes(data)
    if hashlib.sha256(target.read_bytes()).hexdigest() != spec["sha256"]:
        raise ValueError(f"Model cache hash mismatch: {target}")
    return target


def stage_model(staging: Path, cache: Path, seed: Path | None = None) -> Path:
    """Write only below caller-owned staging/cache; verify bytes before use."""
    staging, cache = staging.resolve(), cache.resolve()
    staging.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    inputs = json.loads((MODEL / "inputs.json").read_text(encoding="utf-8"))
    files = {key: _fetch(cache, value, seed) for key, value in inputs.items()}
    source = staging / "source"
    source.mkdir(exist_ok=True)
    with zipfile.ZipFile(files["libsm64"]) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            relative = Path(*Path(member.filename).parts[1:])
            target = (source / relative).resolve()
            if not target.is_relative_to(source):
                raise ValueError("Model archive path leaves staging directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    importer = _module("model_geo_importer", source / "import-mario-geo.py")
    raw_geo = files["mario_geo"].read_text(encoding="utf-8")
    lines = files["mario_model"].read_text(encoding="utf-8").splitlines()
    header, skip = importer.model_inc_h, 0
    for i, line in enumerate(lines):
        if skip:
            skip -= 1
            lines[i] = "//" + line
        elif line.startswith("ALIGNED8 static const u8 mario_"):
            skip = 2
            lines[i] = "//" + line
        elif line.startswith("const "):
            header += "\nextern " + line.replace(" = {", ";")
    destination = source / "src/decomp/mario"
    destination.mkdir(exist_ok=True)
    generated = {
        "geo.inc.c": importer.geo_inc_c_header + raw_geo + importer.geo_inc_c_footer,
        "geo.inc.h": importer.geo_inc_h,
        "model.inc.h": header,
        "model.inc.c": '#include "../../load_tex_data.h"\n#include "../../gfx_macros.h"\n' + "\n".join(lines),
    }
    for name, text in generated.items():
        (destination / name).write_text(text, encoding="utf-8", newline="\n")
    _stage_held(files, source)
    _module("model_patches", MODEL / "patches.py").patch(source)
    # convUtils needs two helpers from utils.c, whose remainder is filesystem code.
    utilities = (source / "src/decomp/tools/utils.c").read_text(encoding="utf-8")
    text = '#include <stdint.h>\n#include "decomp/tools/utils.h"\n'
    for name in ("read_s16_be", "read_f32_be"):
        text += _function(utilities, name)
    (source / "src/conversion.c").write_text(text, encoding="utf-8", newline="\n")
    return source


def _stage_held(files: dict, source: Path) -> None:
    """Two translation units per held model: its lists (texture arrays
    commented out, their names now atlas cells) and its geo and animations."""
    destination = source / "src/decomp/held"
    destination.mkdir(exist_ok=True)
    for name, (geo, table, lists, inputs, textures) in HELD_MODELS.items():
        lines, skip, externs = files[lists].read_text(encoding="utf-8").splitlines(), 0, ""
        for i, line in enumerate(lines):
            if skip:
                skip -= 1
                lines[i] = "//" + line
            elif line.startswith("ALIGNED8 static const u8 ") and "_texture_" in line:
                skip = 2
                lines[i] = "//" + line
            elif line.startswith("const "):
                externs += "extern " + line.replace(" = {", ";") + "\n"
        (destination / f"{name}_lists.c").write_text(
            '#include "../../load_tex_data.h"\n#include "../../gfx_macros.h"\n'
            + "".join(f"#define {symbol} {cell}\n" for symbol, cell in textures.items())
            + "\n".join(lines) + f"\nconst Gfx sg_{name}_skipped[] = {{ gsSPEndDisplayList() }};\n",
            encoding="utf-8", newline="\n")
        text = HELD_HEADER + externs + f"extern const Gfx sg_{name}_skipped[];\n"
        text += "".join(files[key].read_text(encoding="utf-8") + "\n" for key in inputs)
        # libsm64's shadow node draws nothing and skips its children, so a
        # held graph's shadow becomes a plain start node.
        text = re.sub(r"GEO_SHADOW\([^)]*\)", "GEO_NODE_START()", text)
        text += HELD_ROOT.format(name=name, geo=geo, table=table)
        (destination / f"{name}.c").write_text(text, encoding="utf-8", newline="\n")


def _function(source: str, name: str) -> str:
    """One exact upstream function body, unmodified."""
    start = source.rfind("\n", 0, source.index(name + "(")) + 1
    end = source.index("{", start) + 1
    depth = 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end] + "\n"


def build_model(staging: Path, cache: Path | None = None, vcvars32: Path | None = None,
                *, seed: Path | None = None, testing: bool = False) -> Path:
    """Return sm64_model.lib. Root's renderer links it and includes model.h.

    staging is exclusively caller-owned; no shared DLL or emulator is touched.
    cache contains only hash-verified downloads. testing adds fault-injection
    helpers and must not be used for the delivered renderer.
    """
    staging = Path(staging).resolve()
    cache = Path(cache).resolve() if cache else staging / "cache"
    vcvars32 = vcvars32 or _module("model_build_plugin", ROOT / "tools/build_plugin.py").find_vcvars32()
    if vcvars32 is None:
        raise RuntimeError("Model build requires the x86 MSVC toolchain")
    source = stage_model(staging, cache, seed)
    output = staging / "objects"
    output.mkdir(exist_ok=True)
    sources = [source / name for name in (*SOURCE_FILES, *AUDIO_FILES)]
    sources += [MODEL / name for name in ("model.c", "assets.c", "particles.c", "memory.c", "audio.c", "held.c")]
    flags = ["/nologo", "/O2", "/W3", "/TC", "/std:c17", "/MT", "/Gy", "/Gw",
             "/D_CRT_SECURE_NO_WARNINGS", "/DSM64_LIB_STATIC", "/DGBI_FLOATS", "/DVERSION_US", "/DNO_SEGMENTED_MEMORY"]
    if testing:
        flags.append("/DSG_MODEL_TESTING")
    lines = ["@echo off", f'call "{vcvars32}" >nul', "if errorlevel 1 exit /b 1"]
    objects = []
    for i, path in enumerate(sources):
        obj = output / f"{i:02d}-{path.stem}.obj"
        objects.append(obj)
        command = ["cl", *flags, f'/I"{source / "src/decomp/include"}"', f'/I"{source / "src"}"',
                   f'/I"{MODEL}"', "/c", f'"{path}"', f'/Fo"{obj}"']
        lines += [" ".join(command), "if errorlevel 1 exit /b 1"]
    target = staging / "sm64_model.lib"
    response = staging / "archive.rsp"
    response.write_text(f'/NOLOGO /MACHINE:X86 /OUT:"{target}"\n' + "\n".join(f'"{p}"' for p in objects), encoding="utf-8")
    lines += [f'lib @"{response}"', "exit /b %errorlevel%"]
    script = staging / "build_model.bat"
    script.write_text("\r\n".join(lines), encoding="ascii")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=staging,
                            capture_output=True, text=True, timeout=180,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdin=subprocess.DEVNULL)
    (staging / "compiler.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"Model compiler failed ({result.returncode}):\n{(result.stdout + result.stderr)[-7000:]}")
    manifest = {"component_hash": component_hash(), "architecture": "Win32", "runtime": "MT",
                "testing": testing, "library_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--seed", type=Path)
    options = parser.parse_args()
    print(build_model(options.out, options.cache, seed=options.seed))


if __name__ == "__main__":
    main()
