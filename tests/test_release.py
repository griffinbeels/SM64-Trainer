import hashlib
import importlib.util
from pathlib import Path

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
