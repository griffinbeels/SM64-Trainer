"""Immutable local release provenance. Contains no provider secrets or invitations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from sm64_events.bootstrap.beta_payload import checked_members  # noqa: E402
from sm64_events.core.build_channel import CONFIG_FILENAME, STABLE_PORT, parse_metadata  # noqa: E402
from sm64_events.core.release_contract import version_key  # noqa: E402
from sm64_events.core.update_plan import parse_manifest, validate_release_manifest  # noqa: E402

RECORD = "candidate.json"
VERIFICATION = "candidate-verification.json"
PUBLIC_FIELDS = ("community_url", "supabase_url", "supabase_publishable_key", "oauth_client_id")
NATIVES = ("sm64_trainer_gfx.dll", "GLideN64_SM64Trainer.dll", "SM64GpuEncoderV1.dll")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_once(path: Path, value: dict) -> None:
    """Existing immutable records can be resumed only with identical bytes."""
    body = canonical(value)
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError("candidate record already exists with different provenance")
        return
    with path.open("xb") as stream:
        stream.write(body)


def source_revision(repo: Path = REPO) -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True,
                            capture_output=True, text=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return result.stdout.strip()


def public_fingerprint(build) -> str:
    return hashlib.sha256(canonical({name: getattr(build, name) for name in PUBLIC_FIELDS})).hexdigest()


def record_candidate(directory: Path, *, version: str, build, artifacts: list[Path],
                     source_commit: str, lock: Path, app: Path, version_hook: Path,
                     private: bool, service_provenance: dict | None = None,
                     promoted_from: str | None = None) -> Path:
    version_key(version)
    if build.installation != "normal" or (version_key(version).prerelease != ()) != (build.channel == "beta"):
        raise ValueError("candidate version and normal build identity disagree")
    if build.friend_invitation and not private:
        raise ValueError("private invitation cannot appear in a public release")
    # Callers may supply explicit public revision/hash identifiers only. A
    # fingerprint cannot certify that the remote service currently runs them.
    provenance = service_provenance or {}
    if (not isinstance(provenance, dict) or set(provenance) - {"worker_commit", "catalog_sha256", "migration_revision"}
            or any(not isinstance(v, str) or len(v) > 128 or not all(c.isalnum() or c in "._-" for c in v)
                   for v in provenance.values())):
        raise ValueError("service provenance must contain bounded public revision identifiers")
    value = {"schema": 1, "version": version, "channel": build.channel,
             "installation": "normal", "distribution": "private" if private else "public",
             "source_commit": source_commit, "uv_lock_sha256": digest(lock),
             "runtime_hook_sha256": digest(version_hook),
             "public_config_sha256": public_fingerprint(build), "service": provenance,
             "native_sha256": {name: digest(app / "_internal" / "sm64_events" / "data" / "plugin" / name)
                               for name in NATIVES},
             "artifacts": {p.name: {"sha256": digest(p), "size": p.stat().st_size} for p in artifacts}}
    if promoted_from:
        if len(promoted_from) != 64 or any(c not in "0123456789abcdef" for c in promoted_from):
            raise ValueError("promotion requires the approved candidate digest")
        value["promoted_from"] = promoted_from
    path = directory / RECORD
    write_once(path, value)
    return path


def read_record(directory: Path) -> dict:
    path = directory / RECORD
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("candidate provenance exceeds its limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise ValueError("candidate provenance is invalid")
    version_key(value.get("version"))
    if value.get("installation") != "normal" or value.get("distribution") not in ("private", "public"):
        raise ValueError("candidate installation or distribution is invalid")
    return value


def _verify_identity(archive, record):
    metadata_info = archive.getinfo("_internal/" + CONFIG_FILENAME)
    if metadata_info.file_size > 8192:
        raise ValueError("packaged build metadata is too large")
    build = parse_metadata(json.loads(archive.read(metadata_info)))
    if (build.installation != "normal" or build.channel != record["channel"]
            or public_fingerprint(build) != record["public_config_sha256"]
            or build.friend_invitation and record["distribution"] != "private"):
        raise ValueError("candidate build identity or public configuration disagrees")
    version_info = json.loads(archive.read("_internal/release-version.json"))
    if version_info != {"schema": 1, "version": record["version"]}:
        raise ValueError("packaged runtime version metadata disagrees")
    for name in NATIVES:
        with archive.open("_internal/sm64_events/data/plugin/" + name) as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != record["native_sha256"][name]:
                raise ValueError("candidate native provenance disagrees")


def _verify_archive(directory, manifest, record):
    with zipfile.ZipFile(directory / "SM64Trainer-full.zip") as archive:
        members = checked_members(archive)
        if {m.filename for m in members if not m.is_dir()} != {e.path for e in manifest.files}:
            raise ValueError("candidate archive and manifest disagree")
        for entry in manifest.files:
            with archive.open(entry.path) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry.sha256:
                    raise ValueError("candidate file digest disagrees with its manifest")
            info = archive.getinfo(entry.path)
            if info.file_size != entry.size or info.compress_size != entry.zip_csize or info.compress_type != entry.zip_method:
                raise ValueError("candidate ZIP metadata disagrees with its manifest")
            # A wrong range would break incremental updates despite a valid ZIP.
            with (directory / "SM64Trainer-full.zip").open("rb") as raw:
                import struct
                raw.seek(info.header_offset + 26)
                name_len, extra_len = struct.unpack("<HH", raw.read(4))
            if entry.zip_offset != info.header_offset + 30 + name_len + extra_len:
                raise ValueError("candidate ZIP byte ranges disagree")
        _verify_identity(archive, record)



def verify_artifacts(directory: Path, *, expected_assets: list[str]) -> dict:
    """Recheck shipped bytes, per-file manifest, identity and native provenance."""
    record = read_record(directory)
    artifacts = record.get("artifacts", {})
    if set(artifacts) != set(expected_assets):
        raise ValueError("candidate does not contain the exact required asset set")
    for name, entry in artifacts.items():
        if Path(name).name != name or not isinstance(entry, dict):
            raise ValueError("candidate artifact path is invalid")
        path = directory / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != entry.get("size")
                or digest(path) != entry.get("sha256")):
            raise ValueError("candidate artifact changed after preparation")
        if not name.endswith(".sha256"):
            words = (directory / (name + ".sha256")).read_text(encoding="utf-8").split()
            if words != [entry["sha256"], name]:
                raise ValueError("candidate checksum sidecar disagrees")
    manifest = parse_manifest((directory / "manifest.json").read_text(encoding="utf-8"))
    validate_release_manifest(manifest, record["version"])
    _verify_archive(directory, manifest, record)
    return record


def frozen_self_check(app: Path, directory: Path, record: dict) -> dict:
    """Probe the actual frozen authority before GUI/server/capture startup."""
    output = directory / "frozen-runtime-check.json"
    output.unlink(missing_ok=True)
    result = subprocess.run([str(app / "SM64Trainer.exe"), "--build-self-check", str(output.resolve())],
                            cwd=app, check=False, stdin=subprocess.DEVNULL, capture_output=True,
                            timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not output.is_file() or output.stat().st_size > 4096:
        raise ValueError("frozen runtime self-check did not complete")
    value = json.loads(output.read_text(encoding="utf-8"))
    expected = {"schema": 1, "version": record["version"], "channel": record["channel"],
                "installation": "normal", "port": STABLE_PORT, "updates_enabled": True}
    if value != expected:
        raise ValueError("frozen runtime identity or update baseline disagrees")
    return {"result": value, "app_sha256": digest(app / "SM64Trainer.exe")}


def record_verification(directory: Path, record: dict, *, frozen: dict, checks: list[str]) -> Path:
    if not checks or any(not isinstance(item, str) or not item for item in checks):
        raise ValueError("candidate verification requires named completed checks")
    path = directory / VERIFICATION
    write_once(path, {"schema": 1, "candidate_sha256": digest(directory / RECORD),
                      "version": record["version"], "frozen_runtime": frozen,
                      "checks": checks, "draft_ready": record["distribution"] == "public"})
    return path


def require_approval(directory: Path, approval: Path) -> str:
    if approval.is_symlink() or approval.stat().st_size > 65536:
        raise ValueError("QA approval record is invalid")
    value = json.loads(approval.read_text(encoding="utf-8"))
    candidate_digest = digest(directory / RECORD)
    if (not isinstance(value, dict) or value.get("candidate_sha256") != candidate_digest
            or value.get("approved") is not True or value.get("manual_qa") is not True
            or not isinstance(value.get("reviewer"), str) or not value["reviewer"].strip()):
        raise ValueError("release requires human QA approval of this exact candidate")
    return candidate_digest
