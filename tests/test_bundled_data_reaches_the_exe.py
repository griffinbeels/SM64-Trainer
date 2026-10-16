"""Every seed the app reads when frozen must be in the PyInstaller spec.

A `bundled_*()` helper returns None when its file is absent from `_MEIPASS`,
and every caller treats that as "no data" rather than as an error -- so a seed
missing from the build works perfectly from source and silently disappears in
the released exe. That is the whole failure mode: nothing goes red, the feature
just has nothing to show.

Caught for real on 2026-08-05, when sheet_ladders.seed.json was added and the
spec still listed only two seeds.

Caught again on 2026-10-12 (1.10.0-beta.1, the first installer Griffin ran):
ranks/policy.py reads ranking_policy.json RELATIVE TO ITS OWN MODULE, not via a
core/paths.py helper, so the regex below never saw it, the spec never shipped
it, and the frozen app died at startup with FileNotFoundError. The package-path
tests below cover every reader that bypasses core/paths.py: the whole data
folder ships under sm64_events/data, so a module-relative or importlib.resources
read resolves inside the frozen app exactly as it does from source."""
import re
import sys
from pathlib import Path

from sm64_events.core import paths

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / "build_exe.py"
DATA = REPO / "src" / "sm64_events" / "data"
sys.path.insert(0, str(REPO / "tools"))
import build_exe  # noqa: E402


def _packaged():
    """{source file name: destination folders} of every --add-data PyInstaller
    is actually given, asked of the arguments rather than the file's text."""
    argv = build_exe.app_args(None)
    packaged = {}
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "--add-data":
            source, _, destination = value.rpartition(build_exe.SEP)
            packaged.setdefault(Path(source).name, set()).add(destination)
    return packaged


def _bundled_helpers():
    """Every core/paths.py helper that resolves a file out of _MEIPASS."""
    source = Path(paths.__file__).read_text(encoding="utf-8")
    return sorted(set(re.findall(
        r'_MEIPASS[^)]*\)\s*\)\s*/\s*"([^"]+)"', source)))


def test_every_frozen_seed_is_named_in_the_build_spec():
    spec = BUILD.read_text(encoding="utf-8")
    missing = [name for name in _bundled_helpers() if name not in spec]
    assert missing == [], (
        f"these are read from _MEIPASS when frozen but never added to the "
        f"exe, so they vanish silently in a release: {missing}")


def test_the_scan_actually_finds_the_seeds():
    """Without this the test above passes for the wrong reason the moment the
    regex stops matching."""
    found = _bundled_helpers()
    assert "rank_standards.seed.json" in found
    assert "defaults.seed.json" in found
    assert "sheet_ladders.seed.json" in found


def test_every_data_file_ships_under_its_package_path():
    """A reader that resolves relative to its module, or through
    importlib.resources, finds <_MEIPASS>/sm64_events/data/<name> when frozen.
    Every top-level file in the data folder must land there, whatever name it
    has, so a future data file never needs a spec entry to survive packaging."""
    packaged = _packaged()
    files = sorted(path.name for path in DATA.iterdir() if path.is_file())
    assert files, "the data folder is empty; the scan is broken"
    missing = [name for name in files if "sm64_events/data" not in packaged.get(name, set())]
    assert missing == [], (
        f"these data files never land under sm64_events/data in the frozen app, "
        f"so a module-relative read crashes once installed: {missing}")


def test_the_ranking_policy_ships_where_its_reader_looks():
    """The beta.1 crash, pinned exactly: RankingPolicy resolves its shipped
    file relative to ranks/policy.py, and that path must be a packaged one."""
    from sm64_events.ranks import policy
    relative = policy._POLICY_PATH.relative_to(REPO / "src")
    assert policy._POLICY_PATH.is_file(), relative
    assert relative.parent.as_posix() in _packaged().get(relative.name, set()), (
        f"ranks/policy.py reads {relative.as_posix()} but the build never puts "
        f"{relative.name} under {relative.parent.as_posix()}")


def test_the_display_reader_alphabets_are_shipped_files():
    """padread/oracleread open these through importlib.resources on
    sm64_events.data; the names must exist in the folder the build ships."""
    from sm64_events.replay import oracleread, padread
    for name in (padread.REFERENCE, padread.ICON_REFERENCE, oracleread.REFERENCE):
        assert (DATA / name).is_file(), name
        assert "sm64_events/data" in _packaged().get(name, set()), name


def test_each_named_seed_exists_in_the_repo():
    # Only the JSON seeds live under src/sm64_events/data; the same _MEIPASS
    # idiom also resolves a bundled ffmpeg.exe, which comes from elsewhere.
    for name in _bundled_helpers():
        if not name.endswith(".seed.json"):
            continue
        assert (REPO / "src" / "sm64_events" / "data" / name).exists(), name
