"""The packaged app carries every bundled component's license text.

His ask (2026-10-24, on the license gaps the credits audit found): "sure, how
do we fix this?". `tools/third_party_notices.py` stages `_internal/licenses/`
after PyInstaller and refuses the build when a notice is missing or stale.
These tests drive it on a scratch `_internal` with the real installed
packages; the build runs the same function on the real exe's contents.
"""
import importlib.metadata as metadata
import shutil
import sys
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import third_party_notices as notices  # noqa: E402


def _texts(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_a_built_app_carries_its_own_the_interpreters_and_each_packages_license(tmp_path):
    out = notices.stage(tmp_path, {"certifi", "pystray", "fastapi", "json"}, ffmpeg=None)
    shipped = _texts(out)
    for dist in ("certifi", "pystray", "fastapi"):
        version = metadata.version(dist)
        assert any(p.startswith(f"python/{dist}-{version}/") for p in shipped), (dist, shipped)
    assert f"python/pystray-{metadata.version('pystray')}/COPYING.LGPL" in shipped
    assert "SM64-Trainer/LICENSE" in shipped and "README.md" in shipped
    assert any(p.startswith("python/CPython-") and p.endswith("LICENSE.txt") for p in shipped)
    assert "renderer/Qt/LICENSE.LGPL3" in shipped and "ffmpeg/LICENSE" in shipped
    # The standard library maps to no distribution: the interpreter's license covers it.
    assert not any(p.startswith("python/json") for p in shipped)


def test_a_bundled_package_with_no_license_file_refuses_the_build(tmp_path):
    """proxy_tools (pywebview's dependency) installs no license file; only the
    tracked text in licenses/python/proxy-tools/ lets the build through."""
    assert not notices.license_files(metadata.distribution("proxy_tools"))
    without = tmp_path / "licenses"
    shutil.copytree(ROOT / "licenses", without)
    shutil.rmtree(without / "python" / "proxy-tools")
    with pytest.raises(RuntimeError, match="proxy_tools 0.1.0"):
        notices.stage(tmp_path / "bare", {"proxy_tools"}, ffmpeg=None, licenses=without)
    out = notices.stage(tmp_path / "tracked", {"proxy_tools"}, ffmpeg=None)
    assert (out / "python" / "proxy-tools" / "LICENSE.txt").is_file()


def test_an_ffmpeg_the_notices_do_not_describe_refuses_the_build(tmp_path, monkeypatch):
    described = notices.recorded_version(ROOT / "licenses" / "ffmpeg", "BUILD-README.txt")
    monkeypatch.setattr(notices, "ffmpeg_version", lambda _exe: described)
    notices.check_recorded_builds(Path("ffmpeg.exe"))
    monkeypatch.setattr(notices, "ffmpeg_version", lambda _exe: "2026-01-01-git-0000000000-full_build-www.gyan.dev")
    with pytest.raises(RuntimeError, match="licenses/ffmpeg describes"):
        notices.check_recorded_builds(Path("ffmpeg.exe"))


def test_a_pyav_the_notices_do_not_describe_refuses_the_build(tmp_path):
    stale = tmp_path / "licenses"
    shutil.copytree(ROOT / "licenses", stale)
    readme = stale / "pyav-ffmpeg" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace(
        f"Version: {metadata.version('av')}", "Version: 0.0.1"), encoding="utf-8")
    with pytest.raises(RuntimeError, match="the bundled PyAV is"):
        notices.check_recorded_builds(None, stale)


def _runtime_closure() -> dict[str, metadata.Distribution]:
    """Every distribution the project's runtime dependencies pull in."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    pending = [Requirement(spec) for spec in project["dependencies"]]
    found: dict[str, metadata.Distribution] = {}
    while pending:
        requirement = pending.pop()
        name = notices._canonical(requirement.name)
        if name in found:
            continue
        try:
            dist = metadata.distribution(requirement.name)
        except metadata.PackageNotFoundError:
            continue    # a requirement whose marker excludes this platform
        found[name] = dist
        extras = requirement.extras or {""}
        for spec in dist.requires or []:
            child = Requirement(spec)
            if child.marker is None or any(child.marker.evaluate({"extra": e}) for e in extras):
                pending.append(child)
    return found


def test_every_runtime_dependency_installs_a_license_file_or_has_a_tracked_one():
    """The early warning for the build's refusal: a new dependency without a
    license file turns this red on the push that adds it, not at release."""
    closure = _runtime_closure()
    assert len(closure) >= 30, sorted(closure)
    missing = [name for name, dist in closure.items()
               if not notices.license_files(dist) and not (ROOT / "licenses" / "python" / name).is_dir()]
    assert not missing, (f"{missing} install no license file. Add each text under "
                         "licenses/python/<name>/ and list it in licenses/README.md.")
