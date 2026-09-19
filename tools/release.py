# tools/release.py
"""One-command release: bump -> tag -> build -> zip+manifest -> SHA-256 -> publish.

    uv run python tools/release.py 1.1.0 [--notes-file NOTES.md] [--dry-run]

Refuses unless the tree is clean, you're on main, `gh` is authed, and the
full test suite passes. Builds the onedir app + bootstrap installer via
tools/build_exe.py (ffmpeg must be on PATH so it gets bundled), zips the
onedir tree, emits the per-file update manifest, and publishes SIX assets
the incremental updater + bootstrap consume:

    SM64Trainer-full.zip(.sha256)   the whole app tree (first install/fallback)
    manifest.json(.sha256)          per-file hashes + zip byte offsets
    SM64Trainer.exe(.sha256)        the BOOTSTRAP installer under the
                                    load-bearing name old shipped updaters
                                    can install (their migration vehicle)

Pure helpers (bump_*, sha256_file, valid_version, write_sha, release_assets)
are unit-tested; the git/gh/build orchestration is exercised by cutting a
real release."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.core.update_plan import (BOOTSTRAP_ASSET,  # noqa: E402
                                          MANIFEST_ASSET, PATCH_NOTES_MARKER,
                                          ZIP_ASSET)

SETUP_HEADER = REPO / "docs" / "release_setup_header.md"

VERSION_PY = REPO / "src" / "sm64_events" / "core" / "version.py"
PYPROJECT = REPO / "pyproject.toml"
UV_LOCK = REPO / "uv.lock"
DIST = REPO / "dist"
APP_DIR = DIST / "SM64Trainer"                    # onedir build
APP_EXE = APP_DIR / "SM64Trainer.exe"
BOOTSTRAP_BUILD = DIST / "SM64TrainerSetup.exe"   # bootstrap onefile build
# Asset names come from THE registry (core/update_plan.py) — a rename there
# must flow through here or updater+bootstrap would find nothing.
ZIP_PATH = DIST / ZIP_ASSET
MANIFEST_PATH = DIST / MANIFEST_ASSET
UPLOAD_EXE = DIST / BOOTSTRAP_ASSET     # bootstrap copy under the asset name


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


def write_sha(path: Path) -> Path:
    digest = sha256_file(path)
    side = path.with_name(path.name + ".sha256")
    side.write_text(f"{digest}  {path.name}\n")
    return side


def release_assets(dist: Path) -> list[Path]:
    return [dist / ZIP_ASSET, dist / (ZIP_ASSET + ".sha256"),
            dist / MANIFEST_ASSET, dist / (MANIFEST_ASSET + ".sha256"),
            dist / BOOTSTRAP_ASSET, dist / (BOOTSTRAP_ASSET + ".sha256")]


def compose_release_body(setup_header: str, patch_notes: str) -> str:
    """GitHub release body = first-time-setup header (for new users landing
    on the page) + PATCH_NOTES_MARKER + the patch notes. The in-app popup
    strips through the marker (core/release_feed.strip_body), so recurring
    users see only the patch notes."""
    return (setup_header.rstrip() + "\n\n" + PATCH_NOTES_MARKER + "\n\n"
            + patch_notes.lstrip())


def integration_command() -> list[str]:
    """The gate a merge runs, read from ITS definition, not restated here.

    A release must be judged exactly as a merge is. Two spellings of "run the
    tests" drift, and on 2026-09-18 both wrong ones cost a night: a bare
    serial `pytest -q` took 2h30m and failed 12 timing tests that pass in 43s
    through the gate, and `run_tests.py` with its own default of 16 workers
    failed a browser wait on three runs out of three, while the configured
    lane's 4 workers passed 11010 on the same tree. `.verification.toml` owns
    that number; this reads it.
    """
    import tomllib
    config = tomllib.loads((REPO / ".verification.toml").read_text(encoding="utf-8"))
    for check in config.get("checks", []):
        if check.get("name") == "integration-tests":
            # Same placeholders the harness substitutes (harness/verification.py).
            return [arg.replace("{python}", sys.executable).replace("{project}", str(REPO))
                    for arg in check["command"]]
    raise SystemExit("no integration-tests check in .verification.toml")


def _verify_tool() -> Path | None:
    """The harness's verify.py, which REUSES a receipt whose fingerprint still
    matches. A release minutes after a merge then costs seconds instead of
    re-running the same 40-minute suite against the same bytes (2026-09-18:
    it ran three times for one release). Absent harness: run the lane."""
    named = os.environ.get("SM64_VERIFY_TOOL")
    for candidate in (Path(named) if named else None,
                      Path.home() / ".claude" / "harness" / "tools" / "verify.py"):
        if candidate and candidate.is_file():
            return candidate
    return None


def _verify_or_run_gate() -> None:
    tool = _verify_tool()
    if tool is None:
        _run(integration_command())
        return
    # subprocess.run directly: `_run` hardcodes check=True, and a failed
    # verification must be READ here, not raised as a CalledProcessError whose
    # message says nothing about which test failed.
    command = [sys.executable, str(tool), "full", "--project", str(REPO), "--json"]
    print("+", " ".join(command))
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    receipt = json.loads(result.stdout or "{}") if result.stdout else {}
    if receipt.get("status") != "passed":
        sys.exit(f"refusing: verification is {receipt.get('status', 'unavailable')}; "
                 f"{receipt.get('message') or 'run it and read the failures'}")
    print(f"verification: passed ({'reused' if receipt.get('reused') else 'fresh'})")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=REPO, check=True, **kw)


def _capture(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=REPO, check=True,
                          capture_output=True, text=True).stdout.strip()


def _preflight(dry_run: bool = False) -> None:
    """A DRY RUN may stand anywhere; a real release still may not.

    Until 2026-09-19 this path could only be exercised from a clean main, so
    every bug in it cost a full merge cycle (two 40-minute gate passes) before
    anyone could see whether the fix worked. Three separate release bugs were
    found that way, one at a time, across a day. A dry run commits nothing,
    tags nothing and publishes nothing -- let it run from the worktree where
    the fix was written, and the next bug is found in one pass.
    """
    if not dry_run:
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

    _preflight(args.dry_run)
    _verify_or_run_gate()

    VERSION_PY.write_text(bump_version_py(VERSION_PY.read_text(), args.version))
    PYPROJECT.write_text(bump_pyproject(PYPROJECT.read_text(), args.version))

    # Build first so a broken build aborts BEFORE any tag/push.
    _run(["uv", "run", "python", "tools/build_exe.py", "--mode", "all"])
    if not APP_EXE.exists():
        sys.exit("build did not produce dist/SM64Trainer/SM64Trainer.exe")
    if not BOOTSTRAP_BUILD.exists():
        sys.exit("build did not produce dist/SM64TrainerSetup.exe")
    import shutil as _shutil

    from make_manifest import build_zip, make_manifest
    print("zipping onedir tree…")
    build_zip(APP_DIR, ZIP_PATH)
    MANIFEST_PATH.write_text(make_manifest(ZIP_PATH, args.version))
    # The bootstrap is uploaded under the LOAD-BEARING name SM64Trainer.exe:
    # already-shipped onefile updaters can only install that asset, and it
    # migrates them to the onedir install (spec 2026-07-23).
    _shutil.copy2(BOOTSTRAP_BUILD, UPLOAD_EXE)
    for artifact in (ZIP_PATH, MANIFEST_PATH, UPLOAD_EXE):
        write_sha(artifact)
    print("assets ready:", ", ".join(a.name for a in release_assets(DIST)))

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

    if args.notes_file:
        # Prepend the standing first-time-setup header to the release PAGE;
        # the in-app popup strips through PATCH_NOTES_MARKER so updaters see
        # only the patch notes. Composed into dist/ (gitignored) to keep the
        # tree clean.
        body = compose_release_body(SETUP_HEADER.read_text(encoding="utf-8"),
                                    Path(args.notes_file).read_text(
                                        encoding="utf-8"))
        body_file = DIST / "release_body.md"
        body_file.write_text(body, encoding="utf-8")
        notes = ["--notes-file", str(body_file)]
    else:
        notes = ["--generate-notes"]
    _run(["gh", "release", "create", tag,
          *[str(a) for a in release_assets(DIST)],
          "--title", tag, *notes])
    print(f"\nReleased {tag}. Users see the update popup on next launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
