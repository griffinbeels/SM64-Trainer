# tools/release.py
"""Reusable release preparation, verification, draft upload and publication.

    python tools/release_flow.py ship-beta VERSION DEST --config CONFIG
    python tools/release.py --phase verify --candidate DEST
    python tools/release.py --phase draft --candidate DEST --approval QA --notes-file NOTES
    python tools/release.py --phase publish --candidate DEST --approval QA

Refuses unless the tree is clean, you're on main, `gh` is authed, the local
merge check passes, and the commit's full run on GitHub passed (it waits
for one still running). The verification lane and tools/build_exe.py build
the three natives from this commit and refuse a stale one
(tools/build_natives.py). Builds the onedir app + bootstrap installer via
tools/build_exe.py (ffmpeg must be on PATH so it gets bundled), zips the
onedir tree, emits the per-file update manifest, and publishes SIX assets
the incremental updater + bootstrap consume:

    SM64Trainer-full.zip(.sha256)   the whole app tree (first install/fallback)
    manifest.json(.sha256)          per-file hashes + zip byte offsets
    SM64Trainer.exe(.sha256)        the BOOTSTRAP installer under the
                                    load-bearing name old shipped updaters
                                    can install (their migration vehicle)

Private preparation never tags, pushes, deploys or publishes. Draft and publish
require exact human QA approval, source/verification gates and six downloaded
asset digests. The old version-only --dry-run command remains build-only.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tools"))   # full_run, make_manifest

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
    from sm64_events.core.release_contract import version_key
    try:
        version_key(v)
        return not v.startswith(("v", "V"))
    except ValueError:
        return False


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


def snapshot_version_files() -> dict[Path, bytes]:
    """The files the bump overwrites, as BYTES.

    A dry run has to build the version it claims, so the bump happens before
    the build -- but it must not leave your checkout dirty afterwards. The
    first cut of this restored with `write_text`, which retypes every line
    ending on Windows: uv.lock came back with 1,583 lines changed, dirtier
    than the bump it was undoing (2026-09-19). Bytes in, bytes out.
    """
    return {path: path.read_bytes()
            for path in (VERSION_PY, PYPROJECT, UV_LOCK) if path.is_file()}


def restore_version_files(originals: dict[Path, bytes]) -> None:
    for path, data in originals.items():
        path.write_bytes(data)


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
        if check.get("name") == "merge-check":
            # Same placeholders the harness substitutes (harness/verification.py).
            return [arg.replace("{python}", sys.executable).replace("{project}", str(REPO))
                    for arg in check["command"]]
    raise SystemExit("no merge-check check in .verification.toml")


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


def _verify_or_run_gate(attempts: int = 6, pause: float = 30.0) -> None:
    """Verified, by receipt if one is current and by running the lane if not.

    `unavailable` is not `failed`. The harness takes its verification lock
    non-blocking, so a background checker holding it for a second reads as
    "unavailable" -- and treating that as a red build is how a release dies
    for no reason. Retry it; only a real `failed` stops the release.
    """
    tool = _verify_tool()
    if tool is None:
        _run(integration_command())
        return
    # subprocess.run directly: `_run` hardcodes check=True, and a failed
    # verification must be READ here, not raised as a CalledProcessError whose
    # message says nothing about which test failed.
    command = [sys.executable, str(tool), "full", "--project", str(REPO), "--json"]
    for attempt in range(1, attempts + 1):
        print("+", " ".join(command))
        result = subprocess.run(command, cwd=REPO, capture_output=True,
                                text=True, check=False)
        receipt = json.loads(result.stdout) if result.stdout.strip() else {}
        status = receipt.get("status", "unavailable")
        if status == "passed":
            print(f"verification: passed ({'reused' if receipt.get('reused') else 'fresh'})")
            return
        if status == "failed":
            failed = [c["name"] for c in receipt.get("checks", [])
                      if c.get("status") != "passed"]
            sys.exit(f"refusing: verification failed ({', '.join(failed) or 'unknown check'}); "
                     "read the receipt and fix the tests")
        print(f"verification unavailable ({receipt.get('message') or result.stderr.strip()[:120]}); "
              f"attempt {attempt}/{attempts}")
        if attempt < attempts:
            time.sleep(pause)
    sys.exit("refusing: verification never became available")


def _require_full_run(dry_run: bool = False, gate=None) -> None:
    """The whole suite runs on GitHub, not here: the merge check only covered
    each change's blast radius. So the commit being released must have a green
    full run; one still going is waited for. A dry run reports the verdict and
    carries on, because it publishes nothing and usually stands on a worktree
    commit GitHub has never seen."""
    if gate is None:
        from full_run import GhUnavailable, release_gate
        try:
            allowed, why = release_gate(_capture(["git", "rev-parse", "HEAD"]), wait=not dry_run)
        except GhUnavailable as error:
            allowed, why = False, f"could not read the full run: {error}"
    else:
        allowed, why = gate()
    if allowed:
        print(f"full run: {why}")
    elif dry_run:
        print(f"full run (dry run, not enforced): {why}")
    else:
        sys.exit(f"refusing: {why}")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(cmd, cwd=REPO, check=True, **kw)


def _capture(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=REPO, check=True,
                          capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()


def _package_private_installer(destination, zip_path, manifest_path, version, record, work):
    import shutil
    from sm64_events.bootstrap.beta_payload import append_payload
    from sm64_events.bootstrap.installer import OFFLINE_OFFER
    offline = work / "offline.zip"
    shutil.copyfile(zip_path, offline)
    import zipfile
    with zipfile.ZipFile(offline, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(OFFLINE_OFFER, json.dumps({"schema": 1, "version": version,
                          "manifest": manifest_path.read_text(encoding="utf-8")}, sort_keys=True))
    private_exe = destination / f"SM64Trainer-{version}-private.exe"
    append_payload(destination / BOOTSTRAP_ASSET, offline, private_exe)
    write_sha(private_exe)
    from release_candidate import write_once, digest
    write_once(destination / "private-installer.json", {"schema": 1,
        "candidate_sha256": digest(record), "version": version,
        "installer": private_exe.name, "sha256": digest(private_exe)})


def prepare_candidate(version: str, destination: Path, *, config: Path,
                      ffmpeg: str | None = None, ghosts: Path | None = None,
                      private: bool = False, check_only: bool = False,
                      service_provenance: dict | None = None, promoted_from: str | None = None) -> Path | None:
    """Build pinned artifacts once. Never bump, tag, push, deploy or publish."""
    from sm64_events.core.build_channel import read_metadata
    from sm64_events.core.release_contract import version_key
    from build_beta import check_prerequisites, validate_artifact, SEED_DESCRIPTOR
    from beta_ghosts import review_collection, COLLECTION_NAME, matches
    from make_manifest import build_zip, make_manifest
    from release_candidate import record_candidate, source_revision
    import shutil
    if not valid_version(version):
        raise ValueError("candidate requires an exact SemVer version")
    build = read_metadata(config)
    if (build.installation != "normal"
            or bool(version_key(version).prerelease) != (build.channel == "beta")):
        raise ValueError("candidate version requires matching normal stable/beta identity")
    if build.friend_invitation and not private:
        raise ValueError("public candidate cannot contain a private invitation")
    resolved_ffmpeg = check_prerequisites(ffmpeg)
    approved = review_collection(ghosts) if ghosts else None
    if check_only:
        return None
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("candidate directory must be empty; verify an existing candidate instead of rebuilding it")
    if _capture(["git", "status", "--porcelain"]):
        raise ValueError("candidate requires a clean committed source tree")
    source = source_revision(REPO)
    destination.mkdir(parents=True, exist_ok=True)
    work = destination / ".work"
    _run([sys.executable, str(REPO / "tools" / "build_exe.py"), "--mode", "all",
          "--ffmpeg", resolved_ffmpeg, "--config", str(config.resolve()), "--version", version,
          "--distpath", str(work / "dist"), "--workpath", str(work / "build")])
    app = work / "dist" / "SM64Trainer"
    if approved:
        if not matches(approved.source, approved):
            raise ValueError("approved ghost source changed during the build")
        shutil.copyfile(approved.source, app / COLLECTION_NAME)
        (app / "_internal" / SEED_DESCRIPTOR).write_text(json.dumps({
            "schema": 1, "file": COLLECTION_NAME, "author": "griffman1212",
            "sha256": approved.sha256, "count": approved.count}, sort_keys=True) + "\n", encoding="utf-8")
    validate_artifact(app, build, ghosts=approved)
    zip_path, manifest_path = destination / ZIP_ASSET, destination / MANIFEST_ASSET
    build_zip(app, zip_path)
    manifest_path.write_text(make_manifest(zip_path, version), encoding="utf-8")
    shutil.copyfile(work / "dist" / "SM64TrainerSetup.exe", destination / BOOTSTRAP_ASSET)
    for artifact in (zip_path, manifest_path, destination / BOOTSTRAP_ASSET):
        write_sha(artifact)
    record = record_candidate(destination, version=version, build=build,
        artifacts=release_assets(destination), source_commit=source, lock=UV_LOCK, app=app,
        version_hook=work / "build" / "rthook_release_version.py", private=private,
        service_provenance=service_provenance, promoted_from=promoted_from)
    if private:
        _package_private_installer(destination, zip_path, manifest_path, version, record, work)
    verify_candidate(destination, app=app)
    return record


def verify_candidate(directory: Path, *, app: Path | None = None) -> Path:
    """Recheck immutable artifacts and the actual embedded runtime baseline."""
    from release_candidate import (verify_artifacts, frozen_self_check, record_verification,
                                   digest, read_record)
    record = verify_artifacts(directory, expected_assets=[p.name for p in release_assets(directory)])
    checks = ["six artifact digests", "complete per-file manifest and ZIP ranges",
              "normal identity and public configuration", "native provenance"]
    import tempfile
    import zipfile
    if app is None:
        from sm64_events.bootstrap.beta_payload import checked_members
        with tempfile.TemporaryDirectory(prefix="sm64-candidate-check-") as temporary:
            app = Path(temporary)
            with zipfile.ZipFile(directory / ZIP_ASSET) as archive:
                checked_members(archive)
                archive.extractall(app)
            frozen = frozen_self_check(app, directory, record)
    else:
        frozen = frozen_self_check(app, directory, record)
    # Bind proof to the packaged executable too, not an arbitrary staging tree.
    from sm64_events.core.update_plan import parse_manifest
    manifest = parse_manifest((directory / MANIFEST_ASSET).read_text(encoding="utf-8"))
    expected = next(e.sha256 for e in manifest.files if e.path == "SM64Trainer.exe")
    if frozen["app_sha256"] != expected:
        raise ValueError("frozen runtime proof was produced by a different executable")
    private_receipt = directory / "private-installer.json"
    if read_record(directory)["distribution"] == "private":
        value = json.loads(private_receipt.read_text(encoding="utf-8"))
        name = value.get("installer", "")
        if (Path(name).name != name or value.get("candidate_sha256") != digest(directory / "candidate.json")
                or digest(directory / name) != value.get("sha256")):
            raise ValueError("private installer changed after candidate preparation")
        from sm64_events.bootstrap.beta_payload import verify_payload
        verify_payload(directory / name)
        from sm64_events.bootstrap.installer import OFFLINE_OFFER
        from sm64_events.bootstrap.beta_payload import checked_members
        with zipfile.ZipFile(directory / name) as archive:
            checked_members(archive)
            offer = archive.getinfo(OFFLINE_OFFER)
            if offer.file_size > 16 << 20 or json.loads(archive.read(offer)) != {
                    "schema": 1, "version": record["version"],
                    "manifest": (directory / MANIFEST_ASSET).read_text(encoding="utf-8")}:
                raise ValueError("offline installer offer does not match the verified candidate")
            if {m.filename for m in archive.infolist() if not m.is_dir()} != {e.path for e in manifest.files} | {OFFLINE_OFFER}:
                raise ValueError("offline installer does not contain the complete candidate")
            for entry in manifest.files:
                with archive.open(entry.path) as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != entry.sha256:
                        raise ValueError("offline installer file disagrees with candidate manifest")
        checks.append("checksummed complete offline installer")
    checks.append("frozen runtime version and update baseline")
    return record_verification(directory, record, frozen=frozen, checks=checks)


def publication_preflight(directory: Path) -> dict:
    """Public publication reuses gates and never rebuilds prepared artifacts."""
    from release_candidate import verify_artifacts, digest
    record = verify_artifacts(directory, expected_assets=[p.name for p in release_assets(directory)])
    if record["distribution"] != "public":
        raise ValueError("private QA candidates must never be uploaded to GitHub")
    verification = json.loads((directory / "candidate-verification.json").read_text(encoding="utf-8"))
    if not verification.get("draft_ready") or verification.get("candidate_sha256") != digest(directory / "candidate.json"):
        raise ValueError("candidate has not passed final verification")
    if _capture(["git", "status", "--porcelain"]) or _capture(["git", "rev-parse", "HEAD"]) != record["source_commit"]:
        raise ValueError("publication requires the candidate's clean exact source commit")
    if sha256_file(UV_LOCK) != record["uv_lock_sha256"]:
        raise ValueError("dependency lock changed after candidate preparation")
    if record["channel"] == "stable" and _capture(["git", "rev-parse", "--abbrev-ref", "HEAD"]) != "main":
        raise ValueError("stable publication requires integrated main")
    _run(["gh", "auth", "status"], capture_output=True)
    _verify_or_run_gate()
    _require_full_run()
    return record


def draft_candidate(directory: Path, *, notes_file: Path, approval: Path) -> None:
    """Upload six assets to a draft, then verify actual downloaded draft bytes."""
    from release_candidate import require_approval, write_once, digest
    require_approval(directory, approval)
    record = publication_preflight(directory)
    tag = "v" + record["version"]
    body = compose_release_body(SETUP_HEADER.read_text(encoding="utf-8"), notes_file.read_text(encoding="utf-8"))
    body_file = directory / "release-body.md"
    body_file.write_text(body, encoding="utf-8")
    existing = subprocess.run(["gh", "release", "view", tag, "--json", "isDraft"],
                              cwd=REPO, capture_output=True, text=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if existing.returncode == 0:
        if not json.loads(existing.stdout).get("isDraft"):
            raise ValueError("a published release with this version already exists")
        # No clobber: resumed drafts must contain the same immutable bytes.
    else:
        flags = ["--prerelease"] if record["channel"] == "beta" else []
        _run(["gh", "release", "create", tag, *[str(p) for p in release_assets(directory)],
              "--draft", "--target", record["source_commit"], "--title", tag,
              "--notes-file", str(body_file), *flags])
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sm64-draft-check-") as temporary:
        _run(["gh", "release", "download", tag, "--dir", temporary])
        for asset in release_assets(directory):
            if not (Path(temporary) / asset.name).is_file() or digest(Path(temporary) / asset.name) != digest(asset):
                raise ValueError("draft assets do not match the verified candidate")
    write_once(directory / "draft-upload.json", {"schema": 1, "tag": tag,
               "candidate_sha256": digest(directory / "candidate.json"), "assets_verified": True})


def publish_candidate(directory: Path, *, approval: Path) -> None:
    from release_candidate import require_approval, digest
    require_approval(directory, approval)
    record = publication_preflight(directory)
    receipt = json.loads((directory / "draft-upload.json").read_text(encoding="utf-8"))
    if not receipt.get("assets_verified") or receipt.get("candidate_sha256") != digest(directory / "candidate.json"):
        raise ValueError("draft upload has not passed asset verification")
    # Re-download on this final action: a modified draft after an earlier
    # receipt must not become public. No rebuild or replacement upload occurs.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="sm64-publish-check-") as temporary:
        _run(["gh", "release", "download", "v" + record["version"], "--dir", temporary])
        for asset in release_assets(directory):
            if digest(Path(temporary) / asset.name) != digest(asset):
                raise ValueError("draft asset changed before publication")
    _run(["gh", "release", "edit", "v" + record["version"], "--draft=false",
          "--prerelease=" + ("true" if record["channel"] == "beta" else "false")])


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



def _legacy_dry_run(version: str) -> int:
    """Retain the old build-only command; restore source bytes on failure too."""
    originals = snapshot_version_files()
    try:
        VERSION_PY.write_bytes(bump_version_py(VERSION_PY.read_bytes().decode("utf-8"), version).encode("utf-8"))
        PYPROJECT.write_bytes(bump_pyproject(PYPROJECT.read_bytes().decode("utf-8"), version).encode("utf-8"))
        _run(["uv", "run", "python", "tools/build_exe.py", "--mode", "all", "--version", version])
        if not APP_EXE.is_file() or not BOOTSTRAP_BUILD.is_file():
            raise RuntimeError("build did not produce the complete app and bootstrap")
        import shutil
        from make_manifest import build_zip, make_manifest
        build_zip(APP_DIR, ZIP_PATH)
        MANIFEST_PATH.write_text(make_manifest(ZIP_PATH, version), encoding="utf-8")
        shutil.copyfile(BOOTSTRAP_BUILD, UPLOAD_EXE)
        for artifact in (ZIP_PATH, MANIFEST_PATH, UPLOAD_EXE):
            write_sha(artifact)
        print("dry-run: built and checksummed; no commit, tag, push or release")
        return 0
    finally:
        restore_version_files(originals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", nargs="?", help="new version, e.g. 1.10.0-beta.1")
    ap.add_argument("--phase", choices=("prepare", "verify", "draft", "publish"),
                    help="explicit reusable candidate phase; no rebuild on verify/draft/publish")
    ap.add_argument("--candidate", type=Path)
    ap.add_argument("--config", type=Path)
    ap.add_argument("--ffmpeg")
    ap.add_argument("--ghosts", type=Path)
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--approval", type=Path)
    ap.add_argument("--notes-file", help="markdown notes (default: gh auto-notes)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build + checksum but do not commit/tag/push/publish")
    args = ap.parse_args()
    if args.phase:
        if args.dry_run:
            ap.error("--dry-run is the legacy build-only path; candidate phases are explicit actions")
        if args.candidate is None:
            ap.error("--phase requires --candidate")
        if args.phase == "prepare":
            if args.config is None or args.version is None:
                ap.error("prepare requires version and --config")
            prepare_candidate(args.version, args.candidate, config=args.config,
                              ffmpeg=args.ffmpeg, ghosts=args.ghosts, private=args.private)
        elif args.phase == "verify":
            verify_candidate(args.candidate)
        elif args.phase == "draft":
            if args.approval is None or args.notes_file is None:
                ap.error("draft requires --approval and --notes-file")
            draft_candidate(args.candidate, approval=args.approval, notes_file=Path(args.notes_file))
        else:
            if args.approval is None:
                ap.error("publish requires --approval")
            publish_candidate(args.candidate, approval=args.approval)
        return 0
    if args.version is None:
        ap.error("version or --phase is required")
    if not valid_version(args.version):
        sys.exit(f"bad version {args.version!r} (want X.Y.Z)")
    if not args.dry_run:
        ap.error("use --phase prepare/verify/draft/publish or tools/release_flow.py; direct publication is retired")
    _preflight(True)
    _verify_or_run_gate()
    _require_full_run(True)
    return _legacy_dry_run(args.version)


if __name__ == "__main__":
    sys.exit(main())
