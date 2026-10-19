# tools/build_exe.py
"""One-command build: `uv run python tools/build_exe.py` ->
dist/SM64Trainer/ (onedir app) + dist/SM64TrainerSetup.exe (bootstrap).

The app is ONEDIR (spec 2026-07-23-incremental-updates): the updater
replaces individual files under the install root, so the build must produce
a folder, not a fused exe. The bootstrap is a tiny stdlib-only ONEFILE
installer, uploaded to releases under the load-bearing asset name
SM64Trainer.exe (old shipped updaters can only install that name).

Reproducibility: PYTHONHASHSEED randomizes compiled bytecode, which would
make every .pyc/PYZ hash differently per build and bloat update deltas.
main() re-execs itself with PYTHONHASHSEED=1 + SOURCE_DATE_EPOCH=<HEAD
commit time> so unchanged sources produce identical bytes across releases.

ffmpeg is bundled automatically: --ffmpeg PATH wins, else the ffmpeg on
PATH. Releases MUST bundle ffmpeg."""
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
# Native/binary deps whose data files or submodules PyInstaller's auto
# analysis can miss — collect everything for each.
# proctap in particular: its backend is chosen by name at runtime and its
# WASAPI process-loopback code is a .pyd inside the package, so static
# analysis finds neither — without collecting it the shipped exe silently
# falls back to device-wide loopback (i.e. records the whole desktop).
COLLECT = ["av", "proctap", "pyaudiowpatch", "pycaw",
           "comtypes", "pymem", "webview", "pystray", "numpy", "yt_dlp", "sentry_sdk"]


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
        "--icon", str(REPO / "assets" / "ukiki.ico"),
        "--runtime-hook", str(REPO / "tools" / "rthook_comtypes.py"),
        # The UI is READ FROM DISK at runtime (server/app.py _UI_INDEX), not
        # imported, so it must be collected preserving the package path.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'ui'}{SEP}sm64_events/ui",
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
        # THE CAPTURE LAYER (core/paths.py::bundled_plugin_file): the wrapper
        # graphics plugin and the renderer it wraps, which the setup screen
        # installs into Project64, plus the 64-bit encoder helper the server
        # spawns. Kept under the package path so the frozen lookup mirrors
        # the source one. tests/test_bundled_natives.py pins all three.
        *(arg for name in ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll")
          for arg in ("--add-data",
                      f"{REPO / 'src' / 'sm64_events' / 'data' / 'plugin' / name}{SEP}sm64_events/data/plugin")),
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
        # EVERY top-level data file again, under its package path. Not every
        # reader goes through core/paths.py: ranks/policy.py opens
        # ranking_policy.json relative to its own module and the pad/oracle
        # display readers use importlib.resources on sm64_events.data. Those
        # resolve to <_MEIPASS>/sm64_events/data/<name> when frozen, so the
        # root-only copies above left 1.10.0-beta.1 crashing at startup
        # (FileNotFoundError in RankingPolicy; round 38). Shipping the folder
        # under the package path makes a module-relative read work frozen
        # with no per-file entry here. The plugin DLLs keep their own entries.
        *(arg for file in sorted(DATA_DIR.iterdir()) if file.is_file()
          for arg in ("--add-data", f"{file}{SEP}sm64_events/data")),
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
    """Embed only the public ingest DSN; an unset DSN makes reports inert."""
    from sm64_events.core.reporting_setup import CONFIG_FILE, read_config_dsn, validate_dsn
    override = os.environ.get("SM64_SENTRY_DSN")
    dsn = read_config_dsn(DATA_DIR / CONFIG_FILE) if override is None else validate_dsn(override)
    work.mkdir(parents=True, exist_ok=True)
    output = work / CONFIG_FILE
    output.write_text(json.dumps({"schema": 1, "dsn": dsn}) + "\n", encoding="utf-8")
    return output


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["app", "bootstrap", "all"],
                    default="all")
    ap.add_argument("--ffmpeg",
                    help="ffmpeg.exe to bundle (default: the one on PATH)")
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

    if args.mode in ("app", "all"):
        ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
        if ffmpeg and not Path(ffmpeg).exists():
            print(f"ffmpeg not found: {ffmpeg}", file=sys.stderr)
            return 2
        if not ffmpeg:
            print("Build refused: ffmpeg.exe is required for a complete application.", file=sys.stderr)
            return 2
        print(f"bundling ffmpeg: {ffmpeg}")
        # The three natives are build outputs, not tracked files: package
        # only ones built from THESE sources, or refuse (tools/build_natives.py).
        sys.path.insert(0, str(REPO / "tools"))
        import build_natives
        try:
            build_natives.ensure(strict=True)
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
        print("\nBuilt:", REPO / "dist" / "SM64Trainer")
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
