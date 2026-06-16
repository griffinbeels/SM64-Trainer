# tools/release.py
"""One-command release: bump -> tag -> build -> SHA-256 -> publish.

    uv run python tools/release.py 1.1.0 [--notes-file NOTES.md] [--dry-run]

Refuses unless the tree is clean, you're on main, `gh` is authed, and the
full test suite passes. Builds the self-contained exe via tools/build_exe.py
(ffmpeg must be on PATH so it gets bundled), writes a SHA-256 the in-app updater
verifies, then `gh release create` with the exe + checksum. GitHub attaches the
source zip/tar.gz to every release automatically.

Pure helpers (bump_*, sha256_file, valid_version) are unit-tested; the git/gh/
build orchestration is exercised by cutting a real release."""
import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION_PY = REPO / "src" / "sm64_events" / "core" / "version.py"
PYPROJECT = REPO / "pyproject.toml"
UV_LOCK = REPO / "uv.lock"
EXE = REPO / "dist" / "sm64_tracker.exe"


def valid_version(v: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", v))


def bump_version_py(text: str, new: str) -> str:
    out, n = re.subn(r'__version__\s*=\s*"[^"]+"',
                     f'__version__ = "{new}"', text)
    if n != 1:
        raise ValueError("could not find __version__ in version.py")
    return out


def bump_pyproject(text: str, new: str) -> str:
    # Targets the FIRST top-level `version = "..."` — assumes [project] (the
    # authoritative version) precedes any [tool.*] version in pyproject.toml.
    out, n = re.subn(r'(?m)^version\s*=\s*"[^"]+"',
                     f'version = "{new}"', text, count=1)
    if n != 1:
        raise ValueError("could not find version in pyproject.toml")
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=REPO, check=True, **kw)


def _capture(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=REPO, check=True,
                          capture_output=True, text=True).stdout.strip()


def _preflight() -> None:
    if _capture(["git", "rev-parse", "--abbrev-ref", "HEAD"]) != "main":
        sys.exit("refusing: not on main")
    if _capture(["git", "status", "--porcelain"]):
        sys.exit("refusing: working tree is dirty")
    try:
        _run(["gh", "auth", "status"], capture_output=True)
    except Exception:
        sys.exit("refusing: `gh` is not authenticated (run `gh auth login`)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="new version, e.g. 1.1.0")
    ap.add_argument("--notes-file", help="markdown notes (default: gh auto-notes)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build + checksum but do not commit/tag/push/publish")
    args = ap.parse_args()
    if not valid_version(args.version):
        sys.exit(f"bad version {args.version!r} (want X.Y.Z)")
    tag = f"v{args.version}"

    _preflight()
    _run(["uv", "run", "pytest", "-q"])

    VERSION_PY.write_text(bump_version_py(VERSION_PY.read_text(), args.version))
    PYPROJECT.write_text(bump_pyproject(PYPROJECT.read_text(), args.version))

    # Build first so a broken build aborts BEFORE any tag/push.
    _run(["uv", "run", "python", "tools/build_exe.py"])
    if not EXE.exists():
        sys.exit("build did not produce dist/sm64_tracker.exe")
    digest = sha256_file(EXE)
    sha_path = EXE.with_name(EXE.name + ".sha256")
    sha_path.write_text(f"{digest}  {EXE.name}\n")
    print("sha256", digest)

    if args.dry_run:
        print("dry-run: built + checksummed, skipping commit/tag/publish")
        return 0

    # uv.lock records the editable package's OWN version, so the bump above
    # regenerates it during the `uv run` build; stage it too or it's left dirty
    # and the NEXT release's clean-tree preflight refuses.
    _run(["git", "add", str(VERSION_PY), str(PYPROJECT), str(UV_LOCK)])
    _run(["git", "commit", "-m", f"release: {tag}"])
    # Annotated (-a) tag, NOT lightweight: `git push --follow-tags` only pushes
    # ANNOTATED tags, so a lightweight `git tag v…` reaches main but never the
    # remote, and `gh release create` then fails "tag … not pushed" (hit live
    # on v1.0.0). Annotated → the one push below carries the tag with it.
    _run(["git", "tag", "-a", tag, "-m", tag])
    _run(["git", "push", "origin", "main", "--follow-tags"])

    notes = (["--notes-file", args.notes_file] if args.notes_file
             else ["--generate-notes"])
    _run(["gh", "release", "create", tag, str(EXE), str(sha_path),
          "--title", tag, *notes])
    print(f"\nReleased {tag}. Users see the update popup on next launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
