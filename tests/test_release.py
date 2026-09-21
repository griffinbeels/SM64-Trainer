import hashlib
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parents[1] / "tools" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def test_bump_version_py_rewrites_constant():
    src = '__version__ = "0.1.0"\n'
    out = release.bump_version_py(src, "1.2.3")
    assert '__version__ = "1.2.3"' in out
    assert "0.1.0" not in out


def test_bump_pyproject_rewrites_project_version():
    src = '[project]\nname = "x"\nversion = "0.1.0"\n'
    out = release.bump_pyproject(src, "1.2.3")
    assert 'version = "1.2.3"' in out


def test_sha256_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert release.sha256_file(f) == hashlib.sha256(b"hello").hexdigest()


def test_valid_version_accepts_semver():
    assert release.valid_version("1.2.3") is True
    assert release.valid_version("v1.2.3") is False
    assert release.valid_version("1.2") is False


def test_write_sha_writes_verifiable_line(tmp_path):
    p = tmp_path / "SM64Trainer-full.zip"
    p.write_bytes(b"payload")
    side = release.write_sha(p)
    digest, name = side.read_text().split()
    assert name == "SM64Trainer-full.zip"
    assert digest == hashlib.sha256(b"payload").hexdigest()


def test_release_assets_names_and_order(tmp_path):
    assets = release.release_assets(tmp_path)
    assert [a.name for a in assets] == [
        "SM64Trainer-full.zip", "SM64Trainer-full.zip.sha256",
        "manifest.json", "manifest.json.sha256",
        "SM64Trainer.exe", "SM64Trainer.exe.sha256"]


def test_compose_release_body_prepends_setup_and_marker():
    from sm64_events.core.update_plan import PATCH_NOTES_MARKER
    body = release.compose_release_body(
        "# First time here?\nInstall steps.\n",
        "\n- **New:** a thing\n")
    header_part, notes_part = body.split(PATCH_NOTES_MARKER)
    assert "Install steps." in header_part
    assert notes_part.strip() == "- **New:** a thing"
    # the popup-side strip (updater) recovers exactly the patch notes
    assert body.split(PATCH_NOTES_MARKER, 1)[1].lstrip() == "- **New:** a thing\n"


# --- the setup story is told twice, and both tellings must agree ------------
#
# `docs/release_setup_header.md` is prepended verbatim to every GitHub release
# page (compose_release_body, above); README.md's Install section tells a repo
# visitor the same story. They are deliberately worded differently — the
# release header opens with "Already installed? You don't need anything from
# this page", which would be nonsense in a README — so they cannot be one file
# and cannot be compared as text.
#
# What CAN drift, silently, is the load-bearing part: the emulator version, the
# ROM version, the install location, the asset names. Every one of those is a
# fact a user ACTS on, and a release page carrying a stale one sends people to
# the wrong download or the wrong emulator. Until 2026-07-28 the only thing
# holding them together was a hand-written "keep the two in sync when the flow
# changes" note in the README — the kind of instruction that is followed right
# up until the day it matters.

REPO_ROOT = Path(__file__).resolve().parents[1]

# (fact, why it is load-bearing if it drifts)
SHARED_SETUP_FACTS = [
    ("SM64Trainer.exe", "the installer asset a user is told to download"),
    ("SM64Trainer-full.zip", "the portable alternative"),
    ("Programs\\SM64Trainer", "where the app installs"),
    ("%LOCALAPPDATA%", "where their history and PBs live"),
    ("Project64", "the only supported emulator"),
    ("1.6", "the only supported Project64 version — addresses are 1.6-specific"),
    ("v1.93u", "the only supported Usamune version"),
    ("WebView2", "the Windows 10 prerequisite"),
    ("SmartScreen", "the warning they WILL see, and must be told is expected"),
]


def test_readme_and_release_page_agree_on_every_setup_fact():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    header = (REPO_ROOT / "docs" / "release_setup_header.md").read_text(
        encoding="utf-8")
    missing = [(fact, why, "README.md" if fact not in readme
                else "docs/release_setup_header.md")
               for fact, why in SHARED_SETUP_FACTS
               if fact not in readme or fact not in header]
    assert not missing, (
        "the two setup stories disagree — (fact, why it matters, where it is "
        f"missing): {missing}. These files are worded differently on purpose "
        "and are not compared as text, but a user acts on every fact above; "
        "one going stale on a release page sends people to the wrong download "
        "or the wrong emulator.")


def test_the_setup_fact_guard_can_still_fail():
    """A substring guard passes forever if the list is empty or the strings are
    generic enough that any document satisfies them."""
    assert len(SHARED_SETUP_FACTS) >= 8
    prose = "This app is great. Download it and have fun."
    absent = [fact for fact, _ in SHARED_SETUP_FACTS if fact not in prose]
    assert len(absent) == len(SHARED_SETUP_FACTS), (
        "a 'fact' in the list is generic enough to appear in unrelated prose")


def test_the_release_runs_the_merge_gates_own_command():
    """A release is judged exactly as a merge is, from ONE definition.

    Two spellings of "run the tests" drift, and on 2026-09-18 both wrong ones
    cost a night. A bare serial `pytest -q` took 2h30m and failed 12
    timing-sensitive tests -- A/V tolerances, UI animation frames -- that pass
    in 43 seconds through the gate. `run_tests.py` with its own default of 16
    workers then failed a browser wait on three runs out of three, while the
    configured lane's 4 workers passed 11010 tests on the same tree. So the
    command comes from `.verification.toml`, which owns that number."""
    import tomllib
    from pathlib import Path
    root = Path(release.__file__).resolve().parents[1]
    config = tomllib.loads((root / ".verification.toml").read_text(encoding="utf-8"))
    configured = next(c["command"] for c in config["checks"]
                      if c["name"] == "merge-check")
    assert release.integration_command() == [
        a.replace("{project}", str(root)) for a in configured], (
        "the release must run the integration lane's own command")


def test_the_release_does_not_spell_the_gate_out_itself():
    """Red if anyone re-hardcodes a test command into the release."""
    import inspect
    source = inspect.getsource(release)
    assert '"pytest"' not in source, "a bare pytest is not the gate"
    assert 'tools/run_tests.py' not in source, (
        "name the lane, not the runner -- the worker count lives in "
        ".verification.toml and a second spelling drops it")


def test_the_dry_run_restore_is_byte_exact(tmp_path, monkeypatch):
    """LF stays LF.

    A dry run builds the bumped version, so the bump lands before the build
    and has to be undone afterwards. The first cut restored with
    `write_text`, which retypes every line ending on Windows: uv.lock came
    back with 1,583 lines changed -- dirtier than the bump it was undoing
    (2026-09-19, caught by the dry run this same commit made possible)."""
    lock = tmp_path / "uv.lock"
    lock.write_bytes(b'version = 1\nname = "x"\n')       # LF, as git stores it
    version = tmp_path / "version.py"
    version.write_bytes(b'__version__ = "1.8.2"\n')
    monkeypatch.setattr(release, "UV_LOCK", lock)
    monkeypatch.setattr(release, "VERSION_PY", version)
    monkeypatch.setattr(release, "PYPROJECT", tmp_path / "absent.toml")

    originals = release.snapshot_version_files()
    assert set(originals) == {lock, version}, "an absent file is not snapshotted"
    lock.write_bytes(b"clobbered")
    version.write_text('__version__ = "1.9.0"\n')          # as the bump writes it
    release.restore_version_files(originals)

    assert lock.read_bytes() == b'version = 1\nname = "x"\n'
    assert version.read_bytes() == b'__version__ = "1.8.2"\n'
    assert b"\r\n" not in lock.read_bytes(), "the restore must not retype line endings"


def test_the_dry_run_branch_restores_before_returning():
    """The call has to be ON the dry-run path, not merely defined."""
    import inspect
    source = inspect.getsource(release.main)
    assert source.index("snapshot_version_files()") < source.index("bump_version_py("), (
        "snapshot BEFORE the bump overwrites anything")
    branch = source.index("if args.dry_run:")
    assert "restore_version_files(originals)" in source[branch:branch + 300], (
        "the dry-run branch must put them back before it returns")


def test_a_release_stops_on_a_full_run_that_is_not_green(capsys):
    """The whole suite runs on GitHub; a release is the one thing that waits
    for it. The decision comes from tools/full_run.py."""
    with pytest.raises(SystemExit) as refused:
        release._require_full_run(gate=lambda: (False, "the full run for abc failed"))
    assert "refusing: the full run for abc failed" in str(refused.value)
    release._require_full_run(gate=lambda: (True, "full run passed for abc"))
    assert "full run passed" in capsys.readouterr().out


def test_a_dry_run_reports_the_full_run_without_enforcing_it(capsys):
    """A dry run publishes nothing and usually stands on a commit GitHub has
    never seen, so it says what a real release would decide and carries on."""
    release._require_full_run(dry_run=True, gate=lambda: (False, "no full run"))
    assert "not enforced" in capsys.readouterr().out


def test_the_full_run_gate_comes_before_anything_is_built():
    import inspect
    source = inspect.getsource(release.main)
    assert source.index("_require_full_run(") < source.index('"tools/build_exe.py"')
