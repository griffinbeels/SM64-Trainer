"""Pinned scanner contracts using isolated Git fixtures and safe synthetic input."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import secret_scan as scanner  # noqa: E402


def synthetic():
    # Generated only at runtime; no credential-shaped value is stored in source.
    return "gh" + "p_" + hashlib.sha256(b"offline scanner self test, never issued").hexdigest()[:36]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    scanner.git(root, "init", "--quiet")
    (root / ".gitignore").write_text(".verification/\nlocal-only/\n", encoding="utf-8")
    (root / ".gitleaks.toml").write_bytes(scanner.POLICY)
    (root / "source.py").write_text("print('public source')\n", encoding="utf-8")
    scanner.git(root, "add", "--", ".gitignore", ".gitleaks.toml", "source.py")
    return root


class Detector:
    def __init__(self, *, status=None, rows=None, mutate=None, version=None):
        self.status, self.rows, self.mutate = status, rows, mutate
        self.version = version or scanner.VERSION.encode()
        self.seen, self.commands = {}, []

    def __call__(self, argv, *, cwd, input=None):
        if Path(argv[0]).stem.casefold() == "git":
            return scanner.run(argv, cwd=cwd, input=input)
        self.commands.append(argv)
        if argv[1] == "version":
            return subprocess.CompletedProcess(argv, 0, self.version, b"")
        source = Path(argv[2])
        self.seen = {path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob("*") if path.is_file()}
        leaks = [{"RuleID": "github-pat", "File": str(source / name), "StartLine": 1,
                  "Match": synthetic(), "Secret": synthetic(), "Fingerprint": synthetic()}
                 for name, data in self.seen.items() if synthetic().encode() in data]
        rows = self.rows if self.rows is not None else leaks
        report = Path(argv[argv.index("--report-path") + 1])
        report.write_text(json.dumps(rows), encoding="utf-8")
        if self.mutate:
            self.mutate()
        status = self.status if self.status is not None else scanner.FINDINGS if rows else 0
        return subprocess.CompletedProcess(argv, status, synthetic().encode(), synthetic().encode())


@pytest.fixture
def inert_tool(monkeypatch):
    monkeypatch.setattr(scanner, "require_tool", lambda _repo: Path("inert-gitleaks"))


def test_default_scope_scans_tracked_and_new_source_but_never_ignored_bytes(repo, inert_tool, monkeypatch):
    ignored = repo / "local-only" / "setup.txt"
    ignored.parent.mkdir()
    ignored.write_text("not a source input", encoding="utf-8")
    (repo / "new.txt").write_text("new public input", encoding="utf-8")
    original = Path.read_bytes
    def checked(path):
        assert path != ignored, "ignored local bytes were opened"
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", checked)
    detector = Detector()
    findings, count, reviewed, _digest = scanner.scan(repo, runner=detector)
    assert findings == [] and count == 4
    assert reviewed == 0
    assert detector.seen["new.txt"] == b"new public input"
    assert not any(name.startswith("local-only/") for name in detector.seen)


def test_staged_scope_reads_index_even_when_working_copy_was_cleaned(repo, inert_tool):
    path = repo / "staged.py"
    path.write_text("token = '" + synthetic() + "'\n", encoding="utf-8")
    scanner.git(repo, "add", "--", "staged.py")
    path.write_text("working copy cleaned\n", encoding="utf-8")
    detector = Detector()
    findings, _count, _reviewed, _digest = scanner.scan(repo, staged=True, runner=detector)
    assert findings == [("github-pat", "staged.py", 1)]
    assert synthetic().encode() in detector.seen["staged.py"]


@pytest.mark.parametrize("staged", [False, True])
def test_tracked_ignored_source_is_still_scanned_in_working_and_index_modes(repo, inert_tool, staged):
    selected = repo / "local-only" / "forced.txt"
    selected.parent.mkdir()
    selected.write_text(synthetic(), encoding="utf-8")
    scanner.git(repo, "add", "--force", "--", "local-only/forced.txt")
    if staged:
        selected.write_text("working copy cleaned", encoding="utf-8")
    findings, _count, _reviewed, _digest = scanner.scan(repo, staged=staged, runner=Detector())
    assert ("github-pat", "local-only/forced.txt", 1) in findings


def test_staged_policy_comments_are_scanned_under_neutral_alias(repo, inert_tool):
    policy = repo / ".gitleaks.toml"
    policy.write_bytes(scanner.POLICY + b"# " + synthetic().encode() + b"\n")
    scanner.git(repo, "add", "--", ".gitleaks.toml")
    policy.write_bytes(scanner.POLICY)
    findings, _count, _reviewed, _digest = scanner.scan(repo, staged=True, runner=Detector())
    assert ("github-pat", ".gitleaks.toml", 1) in findings


@pytest.mark.parametrize("nested", [False, True])
def test_source_ignore_files_are_refused_without_using_them(repo, nested):
    path = repo / "nested" / ".gitleaksignore" if nested else repo / ".gitleaksignore"
    path.parent.mkdir(exist_ok=True)
    path.write_text("not consulted", encoding="utf-8")
    with pytest.raises(scanner.ScanError, match="ignore"):
        scanner.source_snapshot(repo)


@pytest.mark.parametrize("policy", [b"invalid [", b"[extend]\nuseDefault=false\n", scanner.POLICY + b"[allowlist]\npaths=['.*']\n"])
def test_source_policy_cannot_disable_or_replace_controlled_detectors(repo, policy):
    (repo / ".gitleaks.toml").write_bytes(policy)
    with pytest.raises(scanner.ScanError, match="policy"):
        scanner.source_snapshot(repo)


def test_cli_finding_output_contains_only_validated_metadata(repo, inert_tool, capsys):
    (repo / "new.txt").write_text(synthetic(), encoding="utf-8")
    assert scanner.main([], root=repo, runner=Detector()) == 42
    output = capsys.readouterr().out
    assert synthetic() not in output
    first = json.loads(output.splitlines()[0])
    assert first == {"rule": "github-pat", "path": "new.txt", "line": 1}
    assert "Match" not in output and "Fingerprint" not in output


@pytest.mark.parametrize("status", [1, 2, 7, 126])
def test_tool_failure_is_not_clean_or_findings_and_never_echoes_raw_output(repo, inert_tool, capsys, status):
    assert scanner.main([], root=repo, runner=Detector(status=status)) == 1
    assert synthetic() not in capsys.readouterr().out


@pytest.mark.parametrize("row", [
    {"RuleID": "github-pat", "File": "../outside", "StartLine": 1},
    {"RuleID": "github-pat\nraw", "File": "source.py", "StartLine": 1},
    {"RuleID": "github-pat", "File": "source.py", "StartLine": True},
    {"RuleID": "github-pat", "File": "source.py", "StartLine": 0},
])
def test_malformed_report_metadata_fails_closed_without_printing_payload(repo, inert_tool, capsys, row):
    row = {**row, "Secret": synthetic()}
    assert scanner.main([], root=repo, runner=Detector(rows=[row])) == 1
    assert synthetic() not in capsys.readouterr().out


@pytest.mark.parametrize("status,rows", [(0, [{"RuleID": "github-pat", "File": "source.py", "StartLine": 1}]), (42, [])])
def test_detector_exit_and_report_must_agree(repo, inert_tool, status, rows):
    with pytest.raises(scanner.ScanError, match="disagree"):
        scanner.scan(repo, runner=Detector(status=status, rows=rows))


def test_source_change_during_scan_refuses_receipt(repo, inert_tool):
    detector = Detector(mutate=lambda: (repo / "source.py").write_bytes(b"changed source"))
    with pytest.raises(scanner.ScanError, match="changed"):
        scanner.scan(repo, runner=detector)


@pytest.mark.parametrize("mode,stage", [("120000", "0"), ("160000", "0"), ("100644", "1")])
def test_linked_submodule_or_conflicted_index_entries_fail_closed(repo, mode, stage):
    def runner(argv, *, cwd, input=None):
        raw = f"{mode} {'a' * 40} {stage}\tsource.py\0".encode()
        return subprocess.CompletedProcess(argv, 0, raw, b"")
    with pytest.raises(scanner.ScanError, match="nonregular or unresolved"):
        scanner.index_entries(repo, runner)


def test_detector_flags_explicitly_own_configuration_ignore_redaction_and_exit(repo, inert_tool):
    detector = Detector()
    scanner.scan(repo, runner=detector)
    command = detector.commands[-1]
    assert "--redact=100" in command and "--exit-code=42" in command and "--ignore-gitleaks-allow" in command
    assert "--config" in command and "--gitleaks-ignore-path" in command and "--timeout=90" in command
    assert not any(Path(name).name == ".gitleaksignore" for name in detector.seen)


def test_missing_tool_does_not_silently_download(repo, monkeypatch, capsys):
    monkeypatch.setattr(scanner.urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("scan downloaded a tool"))
    assert scanner.main([], root=repo) == 1
    assert synthetic() not in capsys.readouterr().out


def archive_asset(payload=b"inert executable"):
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w") as archive:
        archive.writestr("gitleaks.exe", payload)
        archive.writestr("../not-extracted", b"not extracted")
    raw = packed.getvalue()
    return raw, scanner.Asset("fixture.zip", hashlib.sha256(raw).hexdigest(), "gitleaks.exe")


def test_install_downloads_only_pinned_asset_and_extracts_only_binary(tmp_path, monkeypatch):
    raw, asset = archive_asset()
    monkeypatch.setattr(scanner, "asset_for_host", lambda: asset)
    urls = []
    def opener(request, *, timeout):
        urls.append(request.full_url)
        assert timeout == 60
        return io.BytesIO(raw)
    executable = scanner.install(tmp_path, opener=opener)
    assert executable.read_bytes() == b"inert executable" and urls == [scanner.RELEASE + asset.name]
    assert not (tmp_path / ".verification" / "tools" / "not-extracted").exists()


def test_install_refuses_hash_mismatch_before_extracting(tmp_path, monkeypatch):
    _raw, asset = archive_asset()
    monkeypatch.setattr(scanner, "asset_for_host", lambda: asset)
    with pytest.raises(scanner.ScanError, match="hash"):
        scanner.install(tmp_path, opener=lambda *_args, **_kwargs: io.BytesIO(b"wrong archive"))
    assert not (scanner.tool_directory(tmp_path) / asset.executable).exists()


def test_binary_tampering_is_detected_without_running_it(tmp_path, monkeypatch):
    raw, asset = archive_asset()
    monkeypatch.setattr(scanner, "asset_for_host", lambda: asset)
    executable = scanner.install(tmp_path, opener=lambda *_args, **_kwargs: io.BytesIO(raw))
    executable.write_bytes(b"tampered")
    with pytest.raises(scanner.ScanError, match="differs"):
        scanner.require_tool(tmp_path)


def test_tar_symlink_is_never_extracted(tmp_path):
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as archive:
        member = tarfile.TarInfo("gitleaks")
        member.type, member.linkname = tarfile.SYMTYPE, "../outside"
        archive.addfile(member)
    raw = packed.getvalue()
    path = tmp_path / "fixture.tar.gz"
    path.write_bytes(raw)
    asset = scanner.Asset(path.name, hashlib.sha256(raw).hexdigest(), "gitleaks")
    with pytest.raises(scanner.ScanError, match="member"):
        scanner.archive_binary(path, asset)


def test_wrong_version_is_tool_failure_and_cli_identity_is_offline(repo, inert_tool, capsys):
    assert scanner.main([], root=repo, runner=Detector(version=b"0.0.0")) == 1
    assert scanner.main(["--version"], root=repo) == 0
    assert scanner.VERSION in capsys.readouterr().out


def test_invalid_cli_arguments_do_not_echo_supplied_values(capsys):
    assert scanner.main(["--unsupported", synthetic()]) == 1
    assert synthetic() not in capsys.readouterr().out


def test_real_pinned_detector_finds_safe_runtime_synthetic_and_then_clean(repo, monkeypatch, capsys):
    try:
        executable = scanner.require_tool(ROOT)
    except scanner.ScanError:
        pytest.skip("Pinned detector is not installed; no automatic download")
    monkeypatch.setattr(scanner, "require_tool", lambda _repo: executable)
    path = repo / "local-only" / "probe.txt"
    path.parent.mkdir()
    path.write_text("token = '" + synthetic() + "' # gitleaks:allow\n", encoding="utf-8")
    scanner.git(repo, "add", "--force", "--", "local-only/probe.txt")
    assert scanner.main([], root=repo) == 42
    output = capsys.readouterr().out
    assert synthetic() not in output and '"path": "local-only/probe.txt"' in output
    path.write_text("public harmless text\n", encoding="utf-8")
    assert scanner.main(["--staged"], root=repo) == 42
    assert synthetic() not in capsys.readouterr().out
    scanner.git(repo, "add", "--force", "--", "local-only/probe.txt")
    assert scanner.main(["--staged"], root=repo) == 0
    assert scanner.main([], root=repo) == 0


def review_entry(data, match, *, path="source.py", line=1):
    source_line = data.split(b"\n")[line - 1].removesuffix(b"\r")
    start = source_line.index(match)
    base = 1 if line == 1 else 2
    return {"rule": "generic-api-key", "path": path, "line": line,
            "start_column": start + base, "end_column": start + len(match) + base - 1,
            "match_sha256": hashlib.sha256(match).hexdigest(), "line_sha256": hashlib.sha256(source_line).hexdigest(),
            "reason": "Public UI namespace used only by this isolated policy contract test."}


def write_reviews(repo, entries):
    target = repo / scanner.REVIEW_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"version": 1, "reviews": entries}), encoding="utf-8")
    return target


def reviewed_row(entry):
    return {"RuleID": entry["rule"], "File": entry["path"], "StartLine": entry["line"], "EndLine": entry["line"],
            "StartColumn": entry["start_column"], "EndColumn": entry["end_column"]}


def test_review_matches_exact_expression_and_line_and_reports_its_identity(repo, inert_tool, capsys):
    match = b"public UI namespace"
    data = b"namespace = '" + match + b"'\n"
    (repo / "source.py").write_bytes(data)
    entry = review_entry(data, match)
    policy = write_reviews(repo, [entry])
    assert scanner.main([], root=repo, runner=Detector(rows=[reviewed_row(entry)])) == 0
    output = capsys.readouterr().out
    assert "1 reviewed false positives, 0 findings" in output
    assert hashlib.sha256(policy.read_bytes()).hexdigest() in output
    assert entry["reason"] not in output and match.decode() not in output


@pytest.mark.parametrize("change", ["expression", "same-line-context", "coordinates", "end-line", "rule"])
def test_changed_match_context_coordinates_or_rule_cannot_reuse_review(repo, inert_tool, change):
    match = b"public UI namespace"
    data = b"namespace = '" + match + b"'\n"
    entry = review_entry(data, match)
    write_reviews(repo, [entry])
    row = reviewed_row(entry)
    if change == "expression":
        data = data.replace(match, b"another UI namespace")
    elif change == "same-line-context":
        data = data.rstrip(b"\n") + b"; changed = True\n"
    elif change == "coordinates":
        row["StartColumn"] += 1
    elif change == "end-line":
        row["EndLine"] += 1
    else:
        row["RuleID"] = "github-pat"
    (repo / "source.py").write_bytes(data)
    findings, _count, reviewed, _digest = scanner.scan(repo, runner=Detector(rows=[row]))
    assert findings == [(row["RuleID"], "source.py", 1)] and reviewed == 0


@pytest.mark.parametrize("same_line", [False, True])
def test_new_secret_in_reviewed_file_still_fails(repo, inert_tool, same_line):
    match = b"public UI namespace"
    original = b"namespace = '" + match + b"'\n"
    entry = review_entry(original, match)
    write_reviews(repo, [entry])
    separator = b" " if same_line else b"\n"
    (repo / "source.py").write_bytes(original.rstrip(b"\n") + separator + synthetic().encode() + b"\n")
    rows = [reviewed_row(entry), {"RuleID": "github-pat", "File": "source.py", "StartLine": 1 if same_line else 2}]
    findings, _count, reviewed, _digest = scanner.scan(repo, runner=Detector(rows=rows))
    assert ("github-pat", "source.py", 1 if same_line else 2) in findings
    assert reviewed == (0 if same_line else 1)


def test_review_coordinates_use_source_bytes_and_accept_only_line_ending_normalization(repo, inert_tool):
    match = b"public UI namespace"
    data = "# UTF8 byte coordinates\r\nnamespace = '\U0001f31f ".encode() + match + b"'\r\n"
    (repo / "source.py").write_bytes(data)
    entry = review_entry(data, match, line=2)
    write_reviews(repo, [entry])
    for content in (data, data.replace(b"\r\n", b"\n")):
        (repo / "source.py").write_bytes(content)
        findings, _count, reviewed, _digest = scanner.scan(repo, runner=Detector(rows=[reviewed_row(entry)]))
        assert findings == [] and reviewed == 1


def test_staged_scan_uses_only_indexed_review_policy(repo, inert_tool):
    match = b"public UI namespace"
    data = b"namespace = '" + match + b"'\n"
    (repo / "source.py").write_bytes(data)
    entry = review_entry(data, match)
    write_reviews(repo, [entry])
    scanner.git(repo, "add", "--", "source.py")
    detector = lambda: Detector(rows=[reviewed_row(entry)])
    findings, _count, reviewed, _digest = scanner.scan(repo, staged=True, runner=detector())
    assert findings == [("generic-api-key", "source.py", 1)] and reviewed == 0
    scanner.git(repo, "add", "--", scanner.REVIEW_PATH)
    write_reviews(repo, [])
    findings, _count, reviewed, digest = scanner.scan(repo, staged=True, runner=detector())
    assert findings == [] and reviewed == 1
    assert digest != hashlib.sha256((repo / scanner.REVIEW_PATH).read_bytes()).hexdigest()
    findings, _count, reviewed, _digest = scanner.scan(repo, runner=detector())
    assert findings == [("generic-api-key", "source.py", 1)] and reviewed == 0


@pytest.mark.parametrize("change", ["missing-match-hash", "wildcard", "extra-allowlist", "bad-hash", "boolean-line", "duplicate"])
def test_incomplete_or_overbroad_review_policy_is_refused(repo, change):
    entry = review_entry(b"public UI namespace", b"public UI namespace")
    if change == "missing-match-hash":
        del entry["match_sha256"]
    elif change == "wildcard":
        entry["path"] = "tests/*"
    elif change == "extra-allowlist":
        entry["allowlist"] = True
    elif change == "bad-hash":
        entry["line_sha256"] = "anything"
    elif change == "boolean-line":
        entry["line"] = True
    write_reviews(repo, [entry, entry] if change == "duplicate" else [entry])
    with pytest.raises(scanner.ScanError, match="reviewed-match"):
        scanner.source_snapshot(repo)


def test_duplicate_json_fields_are_not_silently_accepted():
    with pytest.raises(scanner.ScanError, match="duplicate"):
        scanner.reviewed_matches(b'{"version":1,"version":1,"reviews":[]}')


def test_review_policy_change_during_scan_invalidates_receipt(repo, inert_tool):
    write_reviews(repo, [])
    detector = Detector(mutate=lambda: (repo / scanner.REVIEW_PATH).write_bytes(scanner.EMPTY_REVIEWS))
    with pytest.raises(scanner.ScanError, match="changed"):
        scanner.scan(repo, runner=detector)


def test_offline_scan_identity_includes_exact_review_policy_digest(repo, capsys):
    policy = write_reviews(repo, [])
    assert scanner.main(["--version"], root=repo) == 0
    original = capsys.readouterr().out
    assert hashlib.sha256(policy.read_bytes()).hexdigest() in original
    policy.write_bytes(scanner.EMPTY_REVIEWS)
    assert scanner.main(["--version"], root=repo) == 0
    assert capsys.readouterr().out != original
