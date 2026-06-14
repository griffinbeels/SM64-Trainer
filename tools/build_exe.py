# tools/build_exe.py
"""One-command build: `uv run python tools/build_exe.py` -> dist/sm64_tracker.exe

Bundles Python + all native deps + the UI folder + the Ukiki icon into a
single onefile exe. Pass --ffmpeg PATH to bundle ffmpeg.exe (strongly
recommended for replay quality; without it the exe falls back to the
in-process PyAV encoder)."""
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"
# Native/binary deps whose data files or submodules PyInstaller's auto
# analysis can miss — collect everything for each.
COLLECT = ["av", "windows_capture", "pyaudiowpatch", "pycaw", "comtypes",
           "pymem", "webview", "pystray", "numpy"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", help="path to an ffmpeg.exe to bundle in")
    args = ap.parse_args()

    import PyInstaller.__main__ as pyi

    argv = [
        str(REPO / "gui_entry.py"),
        "--name", "sm64_tracker",
        "--onefile", "--windowed", "--clean", "--noconfirm",
        "--paths", str(REPO / "src"),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
        "--runtime-hook", str(REPO / "tools" / "rthook_comtypes.py"),
        # The UI is READ FROM DISK at runtime (server/app.py _UI_INDEX), not
        # imported, so it must be collected preserving the package path.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'ui'}{SEP}sm64_events/ui",
    ]
    for pkg in COLLECT:
        argv += ["--collect-all", pkg]
    if args.ffmpeg:
        ff = Path(args.ffmpeg)
        if not ff.exists():
            print(f"ffmpeg not found: {ff}", file=sys.stderr)
            return 2
        argv += ["--add-binary", f"{ff}{SEP}."]
    else:
        print("WARNING: building without bundled ffmpeg — replay will use the "
              "in-process encoder. Pass --ffmpeg PATH for best quality.")

    pyi.run(argv)
    print("\nBuilt:", REPO / "dist" / "sm64_tracker.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
