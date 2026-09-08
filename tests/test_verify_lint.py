"""Exercise real pinned tools and prove failures cannot become clean evidence."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("verify_lint", ROOT / "tools/verify_lint.py")
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    root = tmp_path_factory.mktemp("lint-project")
    shutil.copy(ROOT / "pyproject.toml", root / "pyproject.toml")
    shutil.copy(ROOT / "eslint.config.mjs", root / "eslint.config.mjs")
    shutil.copytree(ROOT / "tools/verification/node_modules",
                    root / "tools/verification/node_modules")
    return root


@pytest.mark.parametrize("suffix,valid,invalid,rule", [
    ("py", "answer = 42\n", "answer = missing_name\n", "F821"),
    ("js", "export const answer = 42;\n", "export const answer = 42 == '42';\n", "eqeqeq"),
    ("py", "answer = 42\n", "def broken(:\n", "invalid-syntax"),
    ("js", "export const answer = 42;\n", "export const answer = ;\n", "parse-error"),
])
def test_real_tools_accept_valid_and_reject_violation(project, suffix, valid, invalid, rule):
    path = project / f"probe.{suffix}"
    path.write_text(valid, encoding="utf-8")
    assert lint.lint([path.name], root=project) == []
    path.write_text(invalid, encoding="utf-8")
    assert rule in {finding["rule"] for finding in lint.lint([path.name], root=project)}


@pytest.mark.parametrize("code,output", [(2, "[]"), (1, "[]"), (0, ""), (0, "{}")])
def test_crash_and_malformed_output_are_unavailable(code, output):
    with pytest.raises(lint.Unavailable):
        lint.parse_result(subprocess.CompletedProcess([], code, output, "failure"))


def test_missing_executable_is_unavailable(monkeypatch):
    monkeypatch.setattr(lint.shutil, "which", lambda _: None)
    with pytest.raises(lint.Unavailable, match="missing executable"):
        lint.run(["uvx"])


def test_eslint_failure_without_diagnostics_is_unavailable(project, monkeypatch):
    monkeypatch.setattr(lint, "run", lambda *args: subprocess.CompletedProcess(
        [], 1, '[{"messages": []}]', ""))
    with pytest.raises(lint.Unavailable, match="without diagnostics"):
        lint.lint(["probe.js"], root=project)


def test_timeout_is_unavailable(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ruff", 60)
    monkeypatch.setattr(lint.subprocess, "run", timeout)
    with pytest.raises(lint.Unavailable):
        lint.run(["python"])


def test_baseline_is_scoped_and_counted(tmp_path):
    path = tmp_path / "a.py"
    path.write_text("before = 1\na = missing\nafter = 2\n", encoding="utf-8")
    finding = {"path": "a.py", "line": 2, "rule": "F821", "message": "undefined"}
    baseline = {"allowances": [lint.signature(finding, tmp_path)]}
    assert lint.new_findings([finding], baseline, tmp_path) == []
    assert lint.new_findings([finding, finding], baseline, tmp_path) == [finding]
    path.write_text("before = 1\na = another_missing\nafter = 2\n", encoding="utf-8")
    assert lint.new_findings([finding], baseline, tmp_path) == [finding]


def test_fix_only_changes_explicit_owned_file(project):
    owned = project / "owned.py"
    other = project / "other.py"
    content = "import os\nanswer = 42\n"
    owned.write_text(content, encoding="utf-8")
    other.write_text(content, encoding="utf-8")
    assert lint.lint([owned.name], fix=True, root=project) == []
    assert "import os" not in owned.read_text(encoding="utf-8")
    assert other.read_text(encoding="utf-8") == content


def test_fix_refuses_implicit_scope():
    with pytest.raises(SystemExit) as error:
        lint.main(["--fix"])
    assert error.value.code == 2


def test_paths_cannot_escape_checkout(tmp_path):
    with pytest.raises(lint.Unavailable, match="escapes checkout"):
        lint.selected_files(["../outside.py"], root=tmp_path)


def test_main_reports_missing_baseline_as_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(lint, "BASELINE", tmp_path / "absent.json")
    assert lint.main(["--files", "tools/verify_lint.py"]) == 2


def test_baseline_records_exact_versions():
    baseline = json.loads(lint.BASELINE.read_text(encoding="utf-8"))
    assert baseline["versions"] == {"ruff": lint.RUFF, "eslint": lint.ESLINT}
