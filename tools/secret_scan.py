"""Offline public-source scan: 0 clean, 42 findings, 1 unavailable/invalid.

--install alone downloads the official hash-pinned detector. Scans copy only
Git-selected source bytes; --staged reads index blobs. Ignored untracked local
files are never opened. Raw detector output/report fields are never printed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

VERSION = "8.30.1"
FINDINGS = 42
TITLE = "SM64 Trainer public-source secret detection"
POLICY = f'title = "{TITLE}"\n[extend]\nuseDefault = true\n'.encode()
RELEASE = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/"
# https://github.com/gitleaks/gitleaks/releases/expanded_assets/v8.30.1
# Official release asset digests, checked 2026-10-21.
ASSETS = {
    ("windows", "x64"): ("zip", "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e"),
    ("windows", "arm64"): ("zip", "b95f5e4f5c425cedca7ee203d9afd29597e692c4924a12ed42f970537c72cc0f"),
    ("linux", "x64"): ("tar.gz", "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"),
    ("linux", "arm64"): ("tar.gz", "e4a487ee7ccd7d3a7f7ec08657610aa3606637dab924210b3aee62570fb4b080"),
    ("darwin", "x64"): ("tar.gz", "dfe101a4db2255fc85120ac7f3d25e4342c3c20cf749f2c20a18081af1952709"),
    ("darwin", "arm64"): ("tar.gz", "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5"),
}
MAX_DOWNLOAD = 16 * 1024 * 1024
MAX_BINARY = 64 * 1024 * 1024
MAX_REPORT = 16 * 1024 * 1024
REVIEW_PATH = "tools/verification/secret-scan-reviewed.json"
EMPTY_REVIEWS = b'{"version":1,"reviews":[]}\n'


class ScanError(RuntimeError):
    """Only static, value-free adapter messages may cross this boundary."""


@dataclass(frozen=True)
class Asset:
    name: str
    digest: str
    executable: str


def asset_for_host(system=None, machine=None):
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    architecture = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine)
    selected = ASSETS.get((system, architecture))
    if selected is None:
        raise ScanError("unsupported detector platform")
    suffix, digest = selected
    return Asset(f"gitleaks_{VERSION}_{system}_{architecture}.{suffix}", digest,
                 "gitleaks.exe" if system == "windows" else "gitleaks")


def run(argv, *, cwd, input=None):
    try:
        return subprocess.run(argv, cwd=cwd, input=input, capture_output=True, check=False, timeout=120, **quiet_spawn_kwargs())
    except (OSError, subprocess.SubprocessError) as error:
        raise ScanError("required command did not finish") from error


def git(repo, *args, runner=run, input=None, accepted=(0,)):
    binary = shutil.which("git")
    if binary is None:
        raise ScanError("Git is unavailable")
    result = runner([binary, "--no-optional-locks", "-c", "core.fsmonitor=false", *args], cwd=repo, input=input)
    if result.returncode not in accepted or not isinstance(result.stdout, bytes):
        raise ScanError("Git source selection failed")
    return result.stdout


def checked_name(value):
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value) or "\\" in value or ":" in value:
        raise ScanError("invalid source path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ScanError("invalid source path")
    if path.name.casefold() == ".gitleaksignore":
        raise ScanError("uncontrolled source ignore file is present")
    return value


def checked_path(root, name, *, required=True):
    target = root / checked_name(name)
    current = root
    for part in PurePosixPath(name).parts:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ScanError("linked source or tool path is not permitted")
    if not target.resolve().is_relative_to(root.resolve()) or required and not target.is_file():
        raise ScanError("selected source or tool file is unavailable")
    return target


def config_bytes(raw):
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ScanError("source detector policy is invalid") from error
    if parsed != {"title": TITLE, "extend": {"useDefault": True}}:
        raise ScanError("source detector policy differs from the controlled default policy")
    return raw


@dataclass(frozen=True)
class Review:
    rule: str
    path: str
    line: int
    start_column: int
    end_column: int
    match_sha256: str
    line_sha256: str


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScanError("reviewed-match policy has duplicate fields")
        result[key] = value
    return result


def reviewed_matches(raw):
    if len(raw) > 256 * 1024:
        raise ScanError("reviewed-match policy exceeds its bound")
    try:
        parsed = json.loads(raw, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as error:
        raise ScanError("reviewed-match policy is invalid") from error
    if (not isinstance(parsed, dict) or set(parsed) != {"version", "reviews"}
            or type(parsed["version"]) is not int or parsed["version"] != 1
            or not isinstance(parsed["reviews"], list) or len(parsed["reviews"]) > 128):
        raise ScanError("reviewed-match policy is invalid")
    reviews = []
    keys = {"rule", "path", "line", "start_column", "end_column", "match_sha256", "line_sha256", "reason"}
    for entry in parsed["reviews"]:
        if not isinstance(entry, dict) or set(entry) != keys:
            raise ScanError("reviewed-match policy requires exact match and line hashes")
        if not isinstance(entry["rule"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", entry["rule"]):
            raise ScanError("reviewed-match rule is invalid")
        checked_name(entry["path"])
        if any(char in entry["path"] for char in "*?[]"):
            raise ScanError("reviewed-match path must be exact")
        if any(type(entry[key]) is not int or not 1 <= entry[key] <= 2**31 - 1
               for key in ("line", "start_column", "end_column")) or entry["start_column"] > entry["end_column"]:
            raise ScanError("reviewed-match coordinates are invalid")
        if any(not isinstance(entry[key], str) or not re.fullmatch(r"[0-9a-f]{64}", entry[key])
               for key in ("match_sha256", "line_sha256")):
            raise ScanError("reviewed-match hashes are invalid")
        reason = entry["reason"]
        if not isinstance(reason, str) or not 20 <= len(reason) <= 240 or any(ord(char) < 32 for char in reason):
            raise ScanError("reviewed-match justification is invalid")
        review = Review(**{key: value for key, value in entry.items() if key != "reason"})
        if review in reviews:
            raise ScanError("reviewed-match policy contains a duplicate match")
        reviews.append(review)
    return tuple(reviews)


def review_identity(repo):
    path = checked_path(repo, REVIEW_PATH, required=False)
    raw = path.read_bytes() if path.is_file() else EMPTY_REVIEWS
    reviews = reviewed_matches(raw)
    return hashlib.sha256(raw).hexdigest(), len(reviews)


def index_entries(repo, runner=run):
    raw = git(repo, "ls-files", "--stage", "-z", runner=runner)
    entries = {}
    try:
        for row in raw.split(b"\0"):
            if not row:
                continue
            header, path = row.split(b"\t", 1)
            mode, blob, stage = header.decode("ascii").split()
            name = checked_name(path.decode("utf-8"))
            if mode not in {"100644", "100755"} or stage != "0" or not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", blob):
                raise ScanError("source index has a nonregular or unresolved entry")
            entries[name] = blob
    except (UnicodeError, ValueError) as error:
        raise ScanError("source index metadata is invalid") from error
    return entries, raw


@dataclass(frozen=True)
class Snapshot:
    files: dict[str, bytes]
    policy: bytes
    reviews: bytes
    index: bytes
    selection: bytes


def source_snapshot(repo, *, staged=False, runner=run):
    if (repo / ".gitleaksignore").exists() or (repo / ".gitleaksignore").is_symlink():
        raise ScanError("uncontrolled source ignore file is present")
    entries, index = index_entries(repo, runner)
    working_policy = config_bytes(checked_path(repo, ".gitleaks.toml").read_bytes())
    indexed_policy = (config_bytes(git(repo, "cat-file", "blob", entries[".gitleaks.toml"], runner=runner))
                      if ".gitleaks.toml" in entries else working_policy)
    command = ("diff", "--cached", "--no-renames", "--name-only", "--diff-filter=ACM", "-z") if staged else (
        "ls-files", "--others", "--exclude-standard", "-z")
    selected = git(repo, *command, runner=runner)
    try:
        names = {checked_name(part.decode("utf-8")) for part in selected.split(b"\0") if part}
    except UnicodeError as error:
        raise ScanError("source selection metadata is invalid") from error
    if not staged:
        names.update(entries)
    files = {}
    for name in sorted(names):
        if staged:
            if name not in entries:
                raise ScanError("staged source is missing from the index")
            files[name] = git(repo, "cat-file", "blob", entries[name], runner=runner)
        else:
            files[name] = checked_path(repo, name).read_bytes()
    reviews = (git(repo, "cat-file", "blob", entries[REVIEW_PATH], runner=runner)
               if staged and REVIEW_PATH in entries else EMPTY_REVIEWS if staged else files.get(REVIEW_PATH, EMPTY_REVIEWS))
    reviewed_matches(reviews)
    return Snapshot(files, indexed_policy if staged else working_policy, reviews, index, selected)


def tool_directory(repo):
    return repo / ".verification" / "tools" / f"gitleaks-{VERSION}"


def archive_binary(archive, asset):
    with archive.open("rb") as handle:
        raw = handle.read(MAX_DOWNLOAD + 1)
    if len(raw) > MAX_DOWNLOAD or hashlib.sha256(raw).hexdigest() != asset.digest:
        raise ScanError("detector archive hash does not match the pinned release")
    try:
        if asset.name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(raw)) as package:
                info = package.getinfo(asset.executable)
                if info.file_size > MAX_BINARY or stat.S_ISLNK(info.external_attr >> 16):
                    raise ScanError("detector archive member is invalid")
                return package.read(info)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as package:
            info = package.getmember(asset.executable)
            if not info.isfile() or info.size > MAX_BINARY:
                raise ScanError("detector archive member is invalid")
            return package.extractfile(info).read()
    except (KeyError, ValueError, zipfile.BadZipFile, tarfile.TarError) as error:
        raise ScanError("detector archive is invalid") from error


def require_tool(repo):
    asset = asset_for_host()
    directory = tool_directory(repo)
    relative = directory.relative_to(repo)
    try:
        archive = checked_path(repo, (relative / asset.name).as_posix())
        executable = checked_path(repo, (relative / asset.executable).as_posix())
    except ScanError as error:
        raise ScanError("pinned detector is unavailable; run --install") from error
    payload = archive_binary(archive, asset)
    if executable.stat().st_size > MAX_BINARY:
        raise ScanError("detector binary exceeds its bound")
    if hashlib.sha256(executable.read_bytes()).digest() != hashlib.sha256(payload).digest():
        raise ScanError("detector binary differs from the pinned archive; run --install")
    return executable


def install(repo, *, opener=urllib.request.urlopen):
    asset = asset_for_host()
    directory = tool_directory(repo)
    relative = directory.relative_to(repo)
    archive = checked_path(repo, (relative / asset.name).as_posix(), required=False)
    executable = checked_path(repo, (relative / asset.executable).as_posix(), required=False)
    directory.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        request = urllib.request.Request(RELEASE + asset.name, headers={"User-Agent": "SM64-Trainer-source-scan"})
        with opener(request, timeout=60) as response:
            raw = response.read(MAX_DOWNLOAD + 1)
        if len(raw) > MAX_DOWNLOAD or hashlib.sha256(raw).hexdigest() != asset.digest:
            raise ScanError("downloaded detector archive failed its pinned hash")
        archive.write_bytes(raw)
    payload = archive_binary(archive, asset)
    temporary = directory / ("install-" + uuid4().hex)
    try:
        temporary.write_bytes(payload)
        temporary.chmod(0o755)
        os.replace(temporary, executable)
    finally:
        temporary.unlink(missing_ok=True)
    return require_tool(repo)


def is_reviewed(row, path, data, reviews):
    for review in reviews:
        if (row["RuleID"], path, row["StartLine"]) != (review.rule, review.path, review.line):
            continue
        if (type(row.get("EndLine")) is not int or row["EndLine"] != review.line
                or type(row.get("StartColumn")) is not int or row["StartColumn"] != review.start_column
                or type(row.get("EndColumn")) is not int or row["EndColumn"] != review.end_column):
            continue
        lines = data.split(b"\n")
        if review.line > len(lines):
            continue
        line = lines[review.line - 1].removesuffix(b"\r")
        if hashlib.sha256(line).hexdigest() != review.line_sha256:
            continue
        # v8.30.1 detect/location.go counts the preceding newline byte in
        # columns, except on a fragment's first line. Try only those two
        # byte-coordinate forms. Both the exact expression and entire source
        # line must match reviewed hashes; coordinates alone never exempt it.
        for previous_newline in (0, 1):
            start = review.start_column - 1 - previous_newline
            end = review.end_column - previous_newline
            if 0 <= start < end <= len(line) and hashlib.sha256(line[start:end]).hexdigest() == review.match_sha256:
                return True
    return False


def report_findings(report, aliases, source, source_bytes, reviews):
    if not report.is_file() or report.stat().st_size > MAX_REPORT:
        raise ScanError("detector report is unavailable or exceeds its bound")
    try:
        rows = json.loads(report.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ScanError("detector report is invalid") from error
    if rows is None:
        rows = []
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ScanError("detector report is invalid")
    findings, reviewed = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ScanError("detector report metadata is invalid")
        rule, name, line = row.get("RuleID"), row.get("File"), row.get("StartLine")
        if not isinstance(rule, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", rule) or not isinstance(name, str):
            raise ScanError("detector report metadata is invalid")
        name = name.replace("\\", "/")
        prefix = source.as_posix() + "/"
        if name.startswith(prefix):
            name = name[len(prefix):]
        if name not in aliases or type(line) is not int or not 1 <= line <= 2**31 - 1:
            raise ScanError("detector report metadata is invalid")
        public_name = aliases[name]
        if is_reviewed(row, public_name, source_bytes[public_name], reviews):
            reviewed.add((rule, public_name, line, row["StartColumn"], row["EndColumn"]))
        else:
            findings.add((rule, public_name, line))
    return sorted(findings), len(reviewed)


def scan(repo, *, staged=False, runner=run):
    before = source_snapshot(repo, staged=staged, runner=runner)
    executable = require_tool(repo)
    version = runner([str(executable), "version"], cwd=repo)
    if version.returncode != 0 or version.stdout.strip() != VERSION.encode():
        raise ScanError("detector version does not match the pin")
    scratch = repo / ".verification"
    checked_path(repo, ".verification/source-scan", required=False)
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-scan-", dir=scratch) as temporary:
        control = Path(temporary)
        source = control / "source"
        source.mkdir()
        aliases = {}
        for name, data in before.files.items():
            target = source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            aliases[name] = name
        # Upstream excludes gitleaks.toml itself. Scan its source under an
        # additional neutral alias without letting source configure the tool.
        alias = "source-policy-" + uuid4().hex + ".txt"
        (source / alias).write_bytes(before.policy)
        aliases[alias] = ".gitleaks.toml"
        config, ignore, report = control / "policy.toml", control / "empty.ignore", control / "report.json"
        config.write_bytes(POLICY)
        ignore.write_bytes(b"")
        result = runner([str(executable), "dir", str(source), "--config", str(config), "--gitleaks-ignore-path", str(ignore),
            "--ignore-gitleaks-allow", "--redact=100", "--no-banner", "--no-color", "--log-level=error",
            "--exit-code=42", "--timeout=90", "--max-target-megabytes=0", "--report-format=json", "--report-path", str(report)], cwd=control)
        if result.returncode not in {0, FINDINGS}:
            raise ScanError("detector failed; raw output was suppressed")
        findings, reviewed = report_findings(report, aliases, source,
            {**before.files, ".gitleaks.toml": before.policy}, reviewed_matches(before.reviews))
        if bool(findings or reviewed) != (result.returncode == FINDINGS):
            raise ScanError("detector exit and report disagree")
        if source_snapshot(repo, staged=staged, runner=runner) != before:
            raise ScanError("selected source changed during the scan; retry")
    return findings, len(before.files), reviewed, hashlib.sha256(before.reviews).hexdigest()


class Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise ScanError("invalid arguments; use --help")


def main(argv=None, *, root=ROOT, runner=run):
    try:
        parser = Arguments(description=__doc__)
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument("--install", action="store_true", help="download and verify the pinned detector")
        actions.add_argument("--staged", action="store_true", help="scan actual staged index changes")
        actions.add_argument("--version", action="store_true", help="print adapter/detector identity without downloading")
        args = parser.parse_args(argv)
        if args.version:
            digest, reviews = review_identity(root)
            print(f"secret_scan v2; gitleaks {VERSION}; controlled-default-policy v1; reviewed-set sha256:{digest}; {reviews} entries")
            return 0
        if args.install:
            install(root)
            print(f"secret_scan: installed hash-verified gitleaks {VERSION}")
            return 0
        findings, count, reviewed, digest = scan(root, staged=args.staged, runner=runner)
        for rule, path, line in findings:
            print(json.dumps({"rule": rule, "path": path, "line": line}, ensure_ascii=True))
        print(f"secret_scan: {reviewed} reviewed false positives, {len(findings)} findings in {count} selected source files; reviewed-set sha256:{digest}")
        return FINDINGS if findings else 0
    except ScanError as error:
        print("secret_scan: " + str(error))
    except Exception:  # noqa: BLE001 - never echo source, report or raw command output
        print("secret_scan: scan did not finish; private details were suppressed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
