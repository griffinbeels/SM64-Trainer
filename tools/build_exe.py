# tools/build_exe.py
"""One-command build: `uv run python tools/build_exe.py` ->
dist/SM64Trainer/ (onedir app) + dist/SM64TrainerSetup.exe (bootstrap).

The app is ONEDIR (spec 2026-07-23-incremental-updates): the updater
replaces individual files under the install root, so the build must produce
a folder, not a fused exe. That folder holds two programs: the Trainer
(SM64Trainer.exe, `_internal/`) and the Ghost Lab's simulation program
(SM64TrainerSim.exe, `_sim/`, sim_args), which alone carries Unicorn;
bundle_policy.check_programs refuses a build that mixes them. The bootstrap is a tiny stdlib-only ONEFILE
installer, uploaded to releases under the load-bearing asset name
SM64Trainer.exe (old shipped updaters can only install that name).

Reproducibility: PYTHONHASHSEED randomizes compiled bytecode, which would
make every .pyc/PYZ hash differently per build and bloat update deltas.
main() re-execs itself with PYTHONHASHSEED=1 + SOURCE_DATE_EPOCH=<HEAD
commit time> so unchanged sources produce identical bytes across releases.

ffmpeg is bundled automatically: the one tools/build_ffmpeg.py builds from
the inputs ffmpeg/build-inputs.json pins (cached; built on first use).
--ffmpeg PATH names that same binary elsewhere; third_party_notices refuses
any other, since each release attaches the source of that build only.

After PyInstaller, tools/third_party_notices.py stages _internal/licenses/
and refuses the build when a bundled component's license is missing or
describes another version (licenses/README.md)."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
DATA_DIR = REPO / "src" / "sm64_events" / "data"
SEP = ";" if os.name == "nt" else ":"
from bundle_policy import (LAB_MODULES, SIM_CONTENTS, SIM_EXE, SIM_MODULES, check_programs,  # noqa: E402
                           data_inputs, map_inputs, ui_inputs)
from sm64_events.core.update_plan import BUNDLED_NATIVES, LAB_NATIVES  # noqa: E402
# Native/binary deps whose data files or submodules PyInstaller's auto
# analysis can miss — collect everything for each.
# proctap in particular: its backend is chosen by name at runtime and its
# WASAPI process-loopback code is a .pyd inside the package, so static
# analysis finds neither — without collecting it the shipped exe silently
# falls back to device-wide loopback (i.e. records the whole desktop).
COLLECT = ["av", "proctap", "pyaudiowpatch", "pycaw",
           "comtypes", "webview", "pystray", "numpy", "yt_dlp", "sentry_sdk"]
# The simulation program's own (sim_args): unicorn is the Ghost Lab's MIPS
# emulator, and its engine is a DLL inside the package (unicorn/lib/unicorn.dll)
# that the binding loads by path. Never collected into the Trainer.
SIM_COLLECT = ["unicorn", "numpy"]
# Left out of the simulation program by name. PyInstaller follows imports
# inside functions too, so the lab modules' lazy paths that no simulation
# process runs (audio_layer's FLAC encode -> PyAV, the recorder's capture
# -> pyaudiowpatch/proctap/pycaw/comtypes, frame_sim's scan CLI -> capstone,
# the picture tools -> PIL) would otherwise carry those libraries in.
# OpenSSL is Apache-2.0 and the program needs no network: hashlib falls back
# to CPython's built-in hash modules. tests/test_sim_boundary.py proves no
# simulation process imports any of them (sim_boundary.MODULES at run time),
# and bundle_policy.check_programs still refuses a build that holds one.
SIM_EXCLUDE = ["_ssl", "_hashlib", "ssl", "av", "pystray", "pyaudiowpatch", "proctap", "pycaw", "comtypes",
               "capstone", "PIL", "webview", "fastapi", "starlette", "uvicorn", "pydantic", "sentry_sdk",
               "yt_dlp", "psutil", "scipy", "yaml", "setuptools", "pkg_resources", "tkinter",
               # unicorn's and numpy's test and build tooling, which --collect-all would carry in
               "pytest", "_pytest", "PyInstaller", "pefile", "altgraph", "pygments", "colorama", "iniconfig",
               "pluggy", "py", "cffi", "pycparser", "win32ctypes", "packaging",
               # The Trainer's own modules a simulation process never loads (sim_boundary.MODULES is what it does):
               # the live recording's sound and the game's memory reader, reached through lazy imports only.
               "audio_capture", "closure", "live_audio", "scene_audio", "stems_live",
               "sm64_events.core.capturelayer", "sm64_events.core.capturelayer_win",
               "sm64_events.core.heap_observation", "sm64_events.core.procmem", "sm64_events.core.recorder_lock",
               "sm64_events.core.release_contract", "sm64_events.memory.pj64", "sm64_events.memory.winprocess",
               "sm64_events.replay", "sm64_events.storage"]
# Left out of the Trainer: Unicorn is the simulation program's alone. The
# Trainer's lab modules reach it only through functions its own processes
# never call (a free camera, a re-simulation or a save state runs in a
# simulation process); check_programs refuses a Trainer that holds it.
TRAINER_EXCLUDE = ["unicorn"]


def needs_reexec(environ) -> bool:
    return environ.get("PYTHONHASHSEED") != "1"


def _source_date_epoch() -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=REPO,
                             capture_output=True, text=True, check=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() or "315532800"
    except Exception:
        return "315532800"    # 1980-01-01, matches the zip timestamp floor


def app_args(ffmpeg: "str | None", *, name: str = "SM64Trainer",
             metadata_path: "Path | None" = None,
             reporting_path: "Path | None" = None,
             version_hook: "Path | None" = None,
             version_metadata: "Path | None" = None,
             distpath: "Path | None" = None, workpath: "Path | None" = None,
             specpath: "Path | None" = None) -> list[str]:
    argv = [
        str(REPO / "gui_entry.py"),
        "--name", name,
        "--windowed", "--clean", "--noconfirm",     # onedir: no --onefile
        "--paths", str(REPO / "src"),
        # The Ghost Lab's own modules (tools/scene_lab) under the bare names
        # they import each other by: bundle_policy.LAB_MODULES, read through
        # sm64_events/scenelab_code.py. They are reached by name at run time,
        # so each one is named here.
        "--paths", str(REPO / "tools" / "scene_lab"),
        *(arg for module in LAB_MODULES for arg in ("--hidden-import", module)),
        *(arg for module in TRAINER_EXCLUDE for arg in ("--exclude-module", module)),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
        "--runtime-hook", str(REPO / "tools" / "rthook_comtypes.py"),
        # The UI is READ FROM DISK at runtime (server/app.py _UI_INDEX), not
        # imported, so it must be collected preserving the package path.
        *(arg for file in ui_inputs(REPO) for arg in ("--add-data",
          f"{file}{SEP}sm64_events/ui/{file.relative_to(REPO / 'src/sm64_events/ui').parent.as_posix()}")),
        # The STROOP symbol maps (memory/behaviours.py via
        # core/paths.py::bundled_symbol_map): a JP door is named by symbol
        # only if the frozen exe carries both versions' TSVs.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'behaviours_us.tsv'}{SEP}.",
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'behaviours_jp.tsv'}{SEP}.",
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'symbols_us.tsv'}{SEP}.",
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'symbols_jp.tsv'}{SEP}.",
        # The natives (core/update_plan.py::BUNDLED_NATIVES, found through
        # core/paths.py::bundled_plugin_file): the graphics plugin the setup
        # screen installs into Project64 and the 64-bit encoder helper the
        # server spawns, under the package path so the frozen lookup mirrors
        # the source one. The retired names' placeholder text is written into
        # the built app afterwards (write_retired_natives).
        *(arg for name in BUNDLED_NATIVES
          for arg in ("--add-data",
                      f"{REPO / 'src' / 'sm64_events' / 'data' / 'plugin' / name}{SEP}sm64_events/data/plugin")),
        # The Ghost Lab's natives (core/update_plan.py::LAB_NATIVES, found
        # through core/paths.py::bundled_lab_file), built into the data
        # folder's lab/ by tools/build_natives.py. The installed app never
        # compiles one. STROOP's two symbol maps follow the package data below.
        *(arg for name in LAB_NATIVES
          for arg in ("--add-data", f"{DATA_DIR / 'lab' / name}{SEP}sm64_events/data/lab")),
        # The desktop tray + pywebview window load assets/ukiki.ico at RUNTIME
        # via _asset_path (-> sys._MEIPASS/ukiki.ico when frozen). --icon only
        # embeds it in the PE header (Explorer/taskbar); without bundling it as
        # data the frozen tray fell back to a placeholder. Land it at root.
        "--add-data", f"{REPO / 'assets' / 'ukiki.ico'}{SEP}.",
        # The rank standards seed (bundled_rank_standards() in core/paths.py)
        # is read from sys._MEIPASS when frozen; without this entry the whole
        # ranks feature has no data in the released exe.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'rank_standards.seed.json'}{SEP}.",
        # The default routes/segments seed (bundled_defaults_seed() in
        # core/paths.py) is read from sys._MEIPASS when frozen; without this
        # entry a released exe never seeds the default segments/routes
        # (reconcile_defaults safely no-ops on a missing seed).
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'defaults.seed.json'}{SEP}.",
        # Ladders derived from the Ultimate Sheet (bundled_sheet_ladders() in
        # core/paths.py). Without this entry the 75 sheet-derived strategies
        # work perfectly from source and vanish from the released exe, which is
        # a silent failure: the helper simply returns None and the store falls
        # back to vetted ladders alone.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'sheet_ladders.seed.json'}{SEP}.",
        # The library snapshot itself (bundled_sheet_library()). Same silent
        # failure as above if omitted: the Library page simply has no data.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'sheet_library.seed.json.gz'}{SEP}.",
        # The human's audit corrections to our reading of the sheet
        # (bundled_library_overrides()). Without this entry a refresh run from
        # the released exe rebuilds the library with none of them applied, and
        # the un-corrected (but newer-revisioned) copy then wins over the
        # bundled, corrected one until the next release.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'library_overrides.json'}{SEP}.",
        # Video-liveness verdicts (bundled_video_checks()). Same silent
        # failure as the snapshot if omitted: the released exe filters no dead
        # example links while dev does.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'video_checks.seed.json.gz'}{SEP}.",
        # Approved runtime data also lives under its package path. Not every
        # reader goes through core/paths.py: ranks/policy.py opens
        # ranking_policy.json relative to its own module and the pad/oracle
        # display readers use importlib.resources on sm64_events.data. Those
        # resolve to <_MEIPASS>/sm64_events/data/<name> when frozen, so the
        # root-only copies above left 1.10.0-beta.1 crashing at startup
        # (FileNotFoundError in RankingPolicy; round 38). The explicit policy
        # preserves those readers without collecting incidental local setup.
        # The plugin DLLs keep their own entries.
        *(arg for file in data_inputs(DATA_DIR)
          for arg in ("--add-data", f"{file}{SEP}sm64_events/data")),
        # STROOP's symbol maps for the Ghost Lab (core/paths.py::bundled_lab_file),
        # committed in the data folder's maps/ and pinned (bundle_policy.map_inputs).
        *(arg for file in map_inputs(DATA_DIR)
          for arg in ("--add-data", f"{file}{SEP}sm64_events/data/maps")),
    ]
    for pkg in COLLECT:
        argv += ["--collect-all", pkg]
    if ffmpeg:
        argv += ["--add-binary", f"{ffmpeg}{SEP}."]
    if metadata_path is not None:
        argv += ["--add-data", f"{metadata_path}{SEP}."]
    argv += ["--add-data", f"{reporting_path or DATA_DIR / 'error-reporting.json'}{SEP}."]
    if version_hook is not None:
        argv += ["--runtime-hook", str(version_hook)]
    if version_metadata is not None:
        argv += ["--add-data", f"{version_metadata}{SEP}."]
    for option, path in (("--distpath", distpath), ("--workpath", workpath),
                         ("--specpath", specpath)):
        if path is not None:
            argv += [option, str(path)]
    return argv


def sim_args(*, metadata_path: "Path | None" = None, version_hook: "Path | None" = None,
             version_metadata: "Path | None" = None, distpath: "Path | None" = None,
             workpath: "Path | None" = None, specpath: "Path | None" = None) -> list[str]:
    """SM64TrainerSim.exe, the app's second program (sim_entry.py): the Ghost
    Lab's children that run Unicorn, which is GPL-2.0-only and so never shares
    a process with the Trainer's PyAV/FFmpeg (GPL-3.0) or pystray (LGPL-3.0).
    Its contents folder is SIM_CONTENTS beside the Trainer's `_internal`;
    merge_sim puts both into the Trainer's folder. It carries the same build
    identity and version as the Trainer, so its paths resolve the same way."""
    argv = [
        str(REPO / "sim_entry.py"),
        "--name", SIM_EXE.removesuffix(".exe"),
        "--windowed", "--clean", "--noconfirm",
        "--contents-directory", SIM_CONTENTS,
        "--paths", str(REPO / "src"),
        "--paths", str(REPO / "tools" / "scene_lab"),
        *(arg for module in SIM_MODULES for arg in ("--hidden-import", module)),
        # OpenSSL is Apache-2.0, which cannot share a process with GPL-2.0-only
        # Unicorn; hashlib falls back to CPython's built-in hash modules and
        # the program needs no network (licenses/README.md, the simulation program).
        *(arg for module in SIM_EXCLUDE for arg in ("--exclude-module", module)),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
    ]
    for pkg in SIM_COLLECT:
        argv += ["--collect-all", pkg]
    for path in (metadata_path, version_metadata):
        if path is not None:
            argv += ["--add-data", f"{path}{SEP}."]
    if version_hook is not None:
        argv += ["--runtime-hook", str(version_hook)]
    for option, path in (("--distpath", distpath), ("--workpath", workpath), ("--specpath", specpath)):
        if path is not None:
            argv += [option, str(path)]
    return argv


def merge_sim(sim_dist: Path, app_dir: Path) -> None:
    """Put the built simulation program (its exe and SIM_CONTENTS) beside the
    Trainer's, then refuse an app whose programs hold what they must not
    (bundle_policy.check_programs)."""
    shutil.copy2(sim_dist / SIM_EXE, app_dir / SIM_EXE)
    shutil.copytree(sim_dist / SIM_CONTENTS, app_dir / SIM_CONTENTS)
    check_programs(app_dir)


def bootstrap_args(*, distpath: Path | None = None, workpath: Path | None = None,
                   specpath: Path | None = None, offer_path: Path | None = None) -> list[str]:
    argv = [
        str(REPO / "bootstrap_entry.py"),
        "--name", "SM64TrainerSetup",
        "--onefile", "--windowed", "--clean", "--noconfirm",
        "--paths", str(REPO / "src"),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
    ]
    if offer_path is not None:
        argv += ["--add-data", f"{offer_path}{SEP}."]
    for option, path in (("--distpath", distpath), ("--workpath", workpath), ("--specpath", specpath)):
        if path is not None:
            argv += [option, str(path)]
    return argv


def stage_public_offer(work: Path, version: str, config: Path | None = None) -> Path:
    """Stage only public release identity; never copy the application's config."""
    sys.path.insert(0, str(REPO / "src"))
    from sm64_events.bootstrap.public_offer import FILENAME, validate_offer
    from sm64_events.core.release_contract import version_key
    # Standalone/legacy callers can supply only --version. Candidate builds
    # additionally validate the normal application's declared channel.
    channel = "beta" if version_key(version).prerelease else "stable"
    if config is not None:
        from sm64_events.core.build_channel import read_metadata
        build = read_metadata(config)
        if build.installation != "normal":
            raise ValueError("public installer requires normal installation identity")
        channel = build.channel
    offer = validate_offer({"schema": 1, "version": version,
                            "channel": channel})
    work.mkdir(parents=True, exist_ok=True)
    path = work / FILENAME
    path.write_text(json.dumps(offer, sort_keys=True) + "\n", encoding="utf-8")
    return path


def stage_version(work: Path, version: str) -> tuple[Path, Path]:
    """Embed the candidate version without mutating tracked source or uv.lock."""
    sys.path.insert(0, str(REPO / "src"))
    from sm64_events.core.release_contract import version_key
    if version.startswith(("v", "V")):
        raise ValueError("build version must not contain a tag prefix")
    version_key(version)
    work.mkdir(parents=True, exist_ok=True)
    hook, metadata = work / "rthook_release_version.py", work / "release-version.json"
    metadata.write_text(json.dumps({"schema": 1, "version": version}, sort_keys=True) + "\n", encoding="utf-8")
    hook.write_text(
        "# Generated candidate runtime version, recorded in candidate provenance.\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "import sm64_events.core.version as _version\n"
        f"_version.__version__ = {version!r}\n"
        "if '--build-self-check' in sys.argv:\n"
        "    from sm64_events.core.build_channel import current_build\n"
        "    _build = current_build()\n"
        "    _destination = Path(sys.argv[sys.argv.index('--build-self-check') + 1])\n"
        "    _destination.write_text(json.dumps({'schema': 1, 'version': _version.__version__,\n"
        "        'channel': _build.channel, 'installation': _build.installation,\n"
        "        'port': _build.default_port, 'updates_enabled': _build.updates_enabled}, sort_keys=True), encoding='utf-8')\n"
        "    os._exit(0)\n", encoding="utf-8")
    return hook, metadata


def stage_reporting(work: Path) -> Path:
    """Embed the public destination, with an explicit environment override."""
    from sm64_events.core.reporting_setup import CONFIG_FILE, read_config_dsn, validate_dsn
    override = os.environ.get("SM64_SENTRY_DSN")
    dsn = read_config_dsn(DATA_DIR / CONFIG_FILE) if override is None else validate_dsn(override)
    work.mkdir(parents=True, exist_ok=True)
    output = work / CONFIG_FILE
    output.write_text(json.dumps({"schema": 1, "dsn": dsn}) + "\n", encoding="utf-8")
    return output


def ready_natives() -> None:
    """The natives are build outputs, not tracked files: package only ones
    built from THESE sources (tools/build_natives.py), or raise. Fresh ones
    were not scanned by this run, and a Defender verdict can arrive long
    after a build (2026-11-06), so packaging scans them all again."""
    sys.path.insert(0, str(REPO / "tools"))
    import build_natives
    import defender_scan
    build_natives.ensure(strict=True, lab=True)
    defender_scan.require_clean(build_natives.bundle_files(lab=True), strict=True)


PLUGIN_FOLDER = Path("_internal") / "sm64_events" / "data" / "plugin"
LAB_FOLDER = Path("_internal") / "sm64_events" / "data" / "lab"


def write_retired_natives(app_dir: Path) -> None:
    """Write the placeholder text (core/update_plan.py RETIRED_NATIVES) into
    the BUILT app, never the source bundle: older apps refuse an update
    without a file at each of these names. Plain text, not a native: the
    Defender gate and the beta self-check leave it out."""
    from sm64_events.core.update_plan import RETIRED_NATIVE_TEXT, RETIRED_NATIVES
    for name in RETIRED_NATIVES:
        (app_dir / PLUGIN_FOLDER / name).write_bytes(RETIRED_NATIVE_TEXT)


def our_files(app_dir: Path, exe_name: str) -> list[Path]:
    """The files of a built app that this repository compiled: the app's own
    exe, the natives (core/update_plan.py BUNDLED_NATIVES), the Ghost Lab's
    natives (LAB_NATIVES) and the simulation program's exe, which every
    normal build has (check_programs refuses one without it)."""
    sim = [app_dir / SIM_EXE] if (app_dir / SIM_EXE).is_file() else []
    return [app_dir / f"{exe_name}.exe", *(app_dir / PLUGIN_FOLDER / name for name in BUNDLED_NATIVES),
            *(app_dir / LAB_FOLDER / name for name in LAB_NATIVES), *sim]


def scan_built(app_dir: Path, exe_name: str) -> None:
    """Refuse the build if Microsoft Defender flags one of our own files in the
    built app or cannot scan it (tools/defender_scan.py). Runs before the zip
    and manifest are made, so they carry only scanned bytes. The files ship
    unsigned: Griffin decided against code signing (2026-11-06)."""
    import defender_scan
    defender_scan.require_clean(our_files(app_dir, exe_name), strict=True)


def pinned_ffmpeg(*, build_missing: bool = True) -> Path:
    """The ffmpeg.exe every app bundles: tools/build_ffmpeg.py's build of the
    pinned inputs, built hidden at below-normal priority when not cached."""
    import build_ffmpeg
    return build_ffmpeg.ensure(build_missing=build_missing)


def ffmpeg_to_bundle(explicit: str | None) -> str | None:
    """--ffmpeg, else the pinned build; or None after printing why there is none."""
    try:
        ffmpeg = explicit or str(pinned_ffmpeg())
    except (RuntimeError, ValueError, OSError) as failure:
        print(f"Build refused: ffmpeg.exe is required for a complete application: {failure}", file=sys.stderr)
        return None
    if not Path(ffmpeg).exists():
        print(f"ffmpeg not found: {ffmpeg}", file=sys.stderr)
        return None
    return ffmpeg


def stage_notices(distpath: Path | None, ffmpeg: Path) -> Path | None:
    """Stage `_internal/licenses/` and the retired names' placeholder
    (write_retired_natives) into the app PyInstaller just built and return
    its folder, or print why the build is refused and return None."""
    import third_party_notices
    # PyInstaller's default distpath is ./dist under the working directory.
    app_dir = (distpath or Path.cwd() / "dist") / "SM64Trainer"
    # One licenses folder for the install: the Trainer's packages and the
    # simulation program's (Unicorn's tracked texts among them, since its
    # wheel installs none).
    names = third_party_notices.bundled_import_names(app_dir)
    if (app_dir / SIM_EXE).is_file():
        names |= third_party_notices.bundled_import_names(app_dir, SIM_EXE.removesuffix(".exe"), SIM_CONTENTS)
    try:
        third_party_notices.stage(app_dir / "_internal", names, ffmpeg)
    except RuntimeError as failure:
        print(f"Build refused: {failure}", file=sys.stderr)
        return None
    write_retired_natives(app_dir)
    return app_dir


def build_app(pyi, args) -> int:
    """`--mode app`: the Trainer and the simulation program, merged into one
    folder, checked, with notices staged and Defender's verdict; 0 when built."""
    if (ffmpeg := ffmpeg_to_bundle(args.ffmpeg)) is None:
        return 2
    print(f"bundling ffmpeg: {ffmpeg}")
    try:
        ready_natives()
    except RuntimeError as failure:
        print(f"cannot package the natives: {failure}", file=sys.stderr)
        return 2
    from sm64_events.core.build_channel import BuildChannel, CONFIG_FILENAME, read_metadata
    work = args.workpath or REPO / "build" / "normal-release"
    work.mkdir(parents=True, exist_ok=True)
    build = read_metadata(args.config) if args.config else BuildChannel(installation="normal")
    if build.installation != "normal":
        raise ValueError("normal application builder requires schema2 installation normal")
    metadata = work / CONFIG_FILENAME
    metadata.write_text(json.dumps(build.metadata(), sort_keys=True) + "\n", encoding="utf-8")
    from sm64_events.core.version import __version__
    hook, version_metadata = stage_version(work, args.version or __version__)
    pyi.run(app_args(ffmpeg, metadata_path=metadata, reporting_path=stage_reporting(work), version_hook=hook,
                     version_metadata=version_metadata, distpath=args.distpath,
                     workpath=work / "app", specpath=work / "spec"))
    pyi.run(sim_args(metadata_path=metadata, version_hook=hook, version_metadata=version_metadata,
                     distpath=work / "sim-dist", workpath=work / "sim", specpath=work / "spec"))
    try:
        merge_sim(work / "sim-dist" / SIM_EXE.removesuffix(".exe"),
                  (args.distpath or Path.cwd() / "dist") / "SM64Trainer")
    except ValueError as failure:
        print(f"Build refused: {failure}", file=sys.stderr)
        return 2
    if (app_dir := stage_notices(args.distpath, Path(ffmpeg))) is None:
        return 2
    try:
        scan_built(app_dir, "SM64Trainer")
    except RuntimeError as failure:
        print(f"Build refused: {failure}", file=sys.stderr)
        return 2
    print("\nBuilt:", app_dir)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["app", "bootstrap", "all"],
                    default="all")
    ap.add_argument("--ffmpeg",
                    help="ffmpeg.exe to bundle (default: tools/build_ffmpeg.py's build)")
    ap.add_argument("--version", help="exact embedded runtime version; source remains unchanged")
    ap.add_argument("--config", type=Path, help="validated schema2 normal build configuration")
    ap.add_argument("--distpath", type=Path)
    ap.add_argument("--workpath", type=Path)
    args = ap.parse_args()

    if args.mode in ("app", "all"):
        from build_defaults_seed import main as check_defaults
        if check_defaults(["--check"]):
            print("Build refused: regenerate the defaults seed before building.",
                  file=sys.stderr)
            return 1

    if needs_reexec(os.environ):
        env = {**os.environ, "PYTHONHASHSEED": "1",
               "SOURCE_DATE_EPOCH": _source_date_epoch()}
        return subprocess.run([sys.executable, __file__, *sys.argv[1:]], env=env,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode

    import PyInstaller.__main__ as pyi

    if args.mode in ("app", "all") and build_app(pyi, args) != 0:
        return 2
    if args.mode in ("bootstrap", "all"):
        work = args.workpath or REPO / "build" / "normal-release"
        sys.path.insert(0, str(REPO / "src"))
        from sm64_events.core.version import __version__
        offer = stage_public_offer(work, args.version or __version__, args.config)
        pyi.run(bootstrap_args(distpath=args.distpath, workpath=work / "installer",
                               specpath=work / "spec", offer_path=offer))
        print("\nBuilt:", REPO / "dist" / "SM64TrainerSetup.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
